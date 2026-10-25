"""Append-first facts with a rebuildable SQLite index and bounded writer queue.

Facts and raw market ticks are separate streams. Facts (orders, paper trades,
market lifecycle, gaps) are indexed row by row in SQLite, count against
``max_bytes`` and are never dropped silently. Raw WebSocket ticks (``book.raw``)
arrive at hundreds per second near round close: they go to their own segments
under ``log/events/raw``, are counted per segment instead of indexed per row,
are the only thing shed when the writer falls behind (the shed count is itself
recorded as a fact) and roll off oldest-first beyond ``raw_keep_bytes``."""
from __future__ import annotations
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import queue
import sqlite3
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from .common import dumps, loads, redact, utc_now

RAW_KINDS=frozenset({'book.raw'})
SEGMENT_BYTES=8*1024*1024
INSERT_SQL='INSERT OR IGNORE INTO events(event_id,event_type,observed_at_utc,mode,account_id,market_id,body) VALUES(?,?,?,?,?,?,?)'

def connect(path):
    conn=sqlite3.connect(path,timeout=15)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=FULL')
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
    INSERT OR IGNORE INTO schema_migrations VALUES(1);
    CREATE TABLE IF NOT EXISTS events(
      seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
      event_type TEXT NOT NULL, observed_at_utc TEXT NOT NULL, mode TEXT NOT NULL,
      account_id TEXT, market_id TEXT, body TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS events_type ON events(event_type,seq);
    CREATE INDEX IF NOT EXISTS events_account ON events(account_id,mode,seq);
    CREATE TABLE IF NOT EXISTS raw_segments(
      segment TEXT NOT NULL, market_id TEXT NOT NULL, messages INTEGER NOT NULL,
      first_utc TEXT, last_utc TEXT, PRIMARY KEY(segment,market_id));
    CREATE TABLE IF NOT EXISTS recovered_segments(segment TEXT PRIMARY KEY, bytes INTEGER NOT NULL);
    ''')
    return conn

class _Segment:
    """One append stream, rotated at SEGMENT_BYTES; closed parts wait for commit."""
    def __init__(self,store,folder):
        self.store=store;self.folder=folder;self.stream=None;self.path=None;self.size=0;self.part=0

    def write(self,data):
        if self.stream is None or self.size>SEGMENT_BYTES:self.rotate()
        self.stream.write(data);self.size+=len(data)
        return self.path

    def rotate(self,final=False):
        if self.stream:
            self.stream.flush();os.fsync(self.stream.fileno());self.stream.close();self.stream=None
            self.store.closed_segments.append(self.path)
        if final:return
        self.path=self.folder/(self.store.session_started+'-'+self.store.session_id+f'-{self.part:04}.jsonl')
        self.stream=self.path.open('ab');self.size=0;self.part+=1

    def sync(self):
        if self.stream:self.stream.flush();os.fsync(self.stream.fileno())

class EventStore:
    def __init__(self, root, max_bytes=512*1024*1024, raw_keep_bytes=20*1024**3):
        self.root=Path(root); self.log=self.root/'log'; self.log.mkdir(parents=True,exist_ok=True)
        self.event_dir=self.log/'events'; self.event_dir.mkdir(exist_ok=True)
        self.raw_dir=self.event_dir/'raw'; self.raw_dir.mkdir(exist_ok=True)
        self.db_path=self.log/'metrics.sqlite'
        self.max_bytes=max_bytes; self.raw_keep_bytes=raw_keep_bytes; self.error=None
        self.queue=queue.Queue(maxsize=20000)
        # Raw ticks may only use the first 75% of the queue; the rest is kept
        # for facts so a tick burst can never block an order or paper fill.
        self.raw_admit=int(self.queue.maxsize*.75)
        self.raw_dropped=0; self.compress_error=None
        self.files_lock=threading.RLock(); self.counter_lock=threading.Lock()
        self.closed_segments=[]
        self.closed=False
        self.session_id=uuid.uuid4().hex
        self.session_started=utc_now().replace(':','').replace('.','')
        self.bytes_written=0; self.raw_bytes=0
        self.recover()
        self.bytes_written=sum(p.stat().st_size for p in self.event_dir.glob('*') if p.is_file())
        self.raw_bytes=sum(p.stat().st_size for p in self.raw_dir.glob('*') if p.is_file())
        self.compress_queue=queue.Queue()
        self.compressor=threading.Thread(target=self._compressor,daemon=True,name='fact-ledger-compress')
        self.compressor.start()
        self.thread=threading.Thread(target=self._writer,daemon=True,name='fact-ledger')
        self.thread.start()

    @staticmethod
    def _row(event):
        indexed=event
        if event['event_type']=='book.raw':
            # Legacy segments only: raw ticks are no longer indexed per row.
            indexed={k:event.get(k) for k in ('source_ms','outcome_asset_id')}
            indexed['payload']={'raw_preserved_in':'log/events/*.jsonl[.gz]'}
        return (event['event_id'],event['event_type'],event['observed_at_utc'],event['mode'],event.get('account_id'),event.get('market_id'),dumps(indexed))

    @classmethod
    def _insert(cls, conn, event):
        conn.execute(INSERT_SQL,cls._row(event))

    def segment_key(self,path):
        stem=path.name.split('.jsonl')[0]
        return ('raw/' if path.parent==self.raw_dir else '')+stem

    @staticmethod
    def _count_raw(stats,key,event):
        at=event['observed_at_utc'];s=stats.setdefault((key,event.get('market_id') or ''),[0,at,at])
        s[0]+=1;s[1]=min(s[1],at);s[2]=max(s[2],at)

    @staticmethod
    def _store_raw_stats(conn,stats):
        if stats:conn.executemany('INSERT OR REPLACE INTO raw_segments VALUES(?,?,?,?,?)',[(k[0],k[1],*v) for k,v in stats.items()])

    def _compress(self,path):
        with self.files_lock:return self._compress_locked(path)

    def _compress_locked(self,path):
        dest=path.with_suffix('.jsonl.gz');tmp=dest.with_suffix('.gz.tmp')
        with path.open('rb') as source,gzip.open(tmp,'wb',compresslevel=3) as target:
            shutil.copyfileobj(source,target,1024*1024)
        os.replace(tmp,dest)
        saved=path.stat().st_size-dest.stat().st_size
        path.unlink()
        with self.counter_lock:
            if path.parent==self.raw_dir:self.raw_bytes-=saved
            else:self.bytes_written-=saved
        return dest

    def _compress_and_mark(self,path,conn):
        if not path.exists():return
        dest=self._compress(path)
        conn.execute('INSERT OR REPLACE INTO recovered_segments VALUES(?,?)',(self.segment_key(dest),dest.stat().st_size));conn.commit()

    def _compressor(self):
        conn=None
        while True:
            path=self.compress_queue.get()
            try:
                if path is None:return
                conn=conn or connect(self.db_path)
                self._compress_and_mark(path,conn)
                if path.parent==self.raw_dir:self._enforce_raw_retention()
            except Exception as exc:
                # Compression is only an optimisation; the plain segment stays valid.
                self.compress_error=type(exc).__name__
            finally:
                self.compress_queue.task_done()
                if path is None and conn:conn.close()

    def _enforce_raw_retention(self):
        if self.raw_keep_bytes is None:return
        removed=[];freed=0
        with self.files_lock:
            for p in sorted(self.raw_dir.glob('*.jsonl.gz')):
                if self.raw_bytes<=self.raw_keep_bytes:break
                size=p.stat().st_size;p.unlink();removed.append(p.name);freed+=size
                with self.counter_lock:self.raw_bytes-=size
        if removed:
            try:self.append('engine.raw_retention',{'removed_segments':removed,'removed_bytes':freed,'raw_keep_bytes':self.raw_keep_bytes,
                                                    'note':'只轮换最旧的原始盘口分段；事实事件、SQLite 索引和每段消息计数保留'},source='fact-ledger')
            except RuntimeError:pass

    def recover(self):
        # JSONL is authoritative. A crash after append but before DB commit is
        # replayed. Compressed segments are immutable, so each is replayed once.
        conn=connect(self.db_path)
        done=dict(conn.execute('SELECT segment,bytes FROM recovered_segments').fetchall())
        leftovers=[]
        for p in sorted(self.event_dir.glob('*.jsonl*'))+sorted(self.raw_dir.glob('*.jsonl*')):
            if p.suffix=='.tmp':continue
            key=self.segment_key(p)
            if p.suffix=='.gz':
                if done.get(key)==p.stat().st_size:continue
                stream=gzip.open(p,'rb')
            else:
                # Preserve and repair only an interrupted final line; never discard history.
                with p.open('rb+') as f:
                    raw=f.read()
                    if raw and not raw.endswith(b'\n'):
                        split=raw.rfind(b'\n')+1
                        (self.log/('recovered-tail-'+p.name+'.bin')).write_bytes(raw[split:])
                        f.seek(split); f.truncate(); f.flush(); os.fsync(f.fileno())
                stream=p.open('rb'); leftovers.append(p)
            rows=[];stats={}
            with stream:
                for line in stream:
                    event=loads(line)
                    if event['event_type'] in RAW_KINDS:self._count_raw(stats,key,event)
                    else:rows.append(self._row(event))
                    if len(rows)>=5000:conn.executemany(INSERT_SQL,rows);rows.clear()
            if rows:conn.executemany(INSERT_SQL,rows)
            self._store_raw_stats(conn,stats)
            if p.suffix=='.gz':conn.execute('INSERT OR REPLACE INTO recovered_segments VALUES(?,?)',(key,p.stat().st_size))
            conn.commit()
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=2').fetchone():
            # Only the rebuildable index changes; raw append-only facts are
            # preserved byte for byte. Remove repeated envelope text from old
            # per-tick rows before they exhaust the active storage budget.
            conn.execute("""UPDATE events SET body=json_object(
                'schema_version',json_extract(body,'$.schema_version'),
                'event_id',event_id,'event_type',event_type,'observed_at_utc',observed_at_utc,
                'mode',mode,'market_id',market_id,'source_ms',json_extract(body,'$.source_ms'),
                'outcome_asset_id',json_extract(body,'$.outcome_asset_id'),
                'payload',json_object('raw_preserved_in','log/events/*.jsonl[.gz]')) WHERE event_type='book.raw'""")
            conn.execute('INSERT INTO schema_migrations VALUES(2)');conn.commit()
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            conn.execute('VACUUM')
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=3').fetchone():
            conn.execute("""UPDATE events SET body=json_object(
                'source_ms',json_extract(body,'$.source_ms'),
                'outcome_asset_id',json_extract(body,'$.outcome_asset_id'),
                'payload',json_object('raw_preserved_in','log/events/*.jsonl[.gz]')) WHERE event_type='book.raw'""")
            conn.execute('INSERT INTO schema_migrations VALUES(3)');conn.commit()
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)');conn.execute('VACUUM')
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=4').fetchone():
            # Raw ticks leave the per-row index; their bytes stay in JSONL and
            # their per-market counts were rebuilt into raw_segments above.
            conn.execute("DELETE FROM events WHERE event_type='book.raw'")
            conn.execute('INSERT INTO schema_migrations VALUES(4)');conn.commit()
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)');conn.execute('VACUUM')
        # Plain segments left by an earlier session are complete: compress once.
        for p in leftovers:self._compress_and_mark(p,conn)
        conn.close()

    def _envelope(self, kind, payload, *, mode='monitor', account_id=None, market_id=None,
                  condition_id=None, asset_id=None, source='poly5', source_ms=None, event_id=None):
        return {
            'schema_version':'3.1','event_id':event_id or uuid.uuid4().hex,'event_type':kind,
            'observed_at_utc':utc_now(),'source_at_utc':datetime.fromtimestamp(source_ms/1000,timezone.utc).isoformat().replace('+00:00','Z') if source_ms else None,'source_ms':source_ms,
            'monotonic_ns':time.monotonic_ns(),'session_id':self.session_id,'mode':mode,
            'venue_id':'polymarket','account_id':account_id,'market_id':market_id,
            'condition_id':condition_id,'outcome_asset_id':asset_id,'source':source,
            'source_status':'simulated' if mode=='paper' else 'unknown' if source=='poly5' else 'live','freshness_ms':None,'correlation_id':None,
            'quality_flags':[],'payload':redact(payload),
        }

    def append(self, kind, payload, **kwargs):
        if self.closed or self.error:
            raise RuntimeError(self.error or '账本已关闭')
        raw=kind in RAW_KINDS
        if raw and self.queue.qsize()>=self.raw_admit:
            # Shed raw ticks first. In-memory books are unaffected; the writer
            # records the gap as a fact so replay cannot mistake it for quiet.
            self.raw_dropped+=1
            return None
        event=self._envelope(kind,payload,**kwargs)
        try: self.queue.put_nowait(event)
        except queue.Full:
            if raw:
                self.raw_dropped+=1
                return None
            try:self.queue.put(event,timeout=2)
            except queue.Full:
                self.error='事实账本队列已满；停止新增模拟交易'; raise RuntimeError(self.error)
        return event['event_id']

    def _write_batch(self,conn,events,facts,raw,raw_stats):
        ids=list({e['event_id'] for e in events if e['event_type'] not in RAW_KINDS})
        known=set()
        for i in range(0,len(ids),500):
            chunk=ids[i:i+500]
            known.update(r[0] for r in conn.execute('SELECT event_id FROM events WHERE event_id IN (%s)'%','.join('?'*len(chunk)),chunk))
        database_bytes=self.db_path.stat().st_size
        wal=self.db_path.with_name(self.db_path.name+'-wal')
        if wal.exists():database_bytes+=wal.stat().st_size
        rows=[]
        for event in events:
            data=(dumps(event)+'\n').encode('utf-8')
            if event['event_type'] in RAW_KINDS:
                path=raw.write(data)
                with self.counter_lock:self.raw_bytes+=len(data)
                self._count_raw(raw_stats,self.segment_key(path),event)
                continue
            if event['event_id'] in known:continue
            if self.bytes_written+len(data)+database_bytes>self.max_bytes:
                self.error=f'事实日志达到 {self.max_bytes//1024//1024} MB 上限；请导出归档后调整保留策略'
                (self.log/'storage-error.json').write_text(dumps({'event_type':'engine.storage_degraded','at':utc_now(),'reason':self.error}),encoding='utf-8')
                break
            known.add(event['event_id'])
            facts.write(data)
            with self.counter_lock:self.bytes_written+=len(data)
            rows.append(self._row(event))
        raw.sync(); facts.sync()
        if rows:conn.executemany(INSERT_SQL,rows)
        self._store_raw_stats(conn,raw_stats)
        conn.commit()
        current=self.segment_key(raw.path) if raw.path else None
        for k in [k for k in raw_stats if k[0]!=current]:del raw_stats[k]

    def _writer(self):
        conn=connect(self.db_path)
        facts=_Segment(self,self.event_dir); raw=_Segment(self,self.raw_dir)
        raw_stats={}; commits=0; reported=0; last_report=0.
        try:
            while True:
                batch=[self.queue.get()]
                # Coalesce burst arrivals before fsync, instead of syncing each
                # individual quote and starving the producer during active BTC.
                time.sleep(.01)
                while len(batch)<5000:
                    try: batch.append(self.queue.get_nowait())
                    except queue.Empty: break
                stop=any(item is None for item in batch)
                events=[item for item in batch if item is not None]
                dropped=self.raw_dropped
                if dropped>reported and (stop or time.monotonic()-last_report>=1):
                    events.append(self._envelope('book.raw_dropped',{'dropped':dropped-reported,'dropped_total':dropped,
                        'reason':'写入积压，按优先级丢弃原始盘口；内存盘口与事实事件不受影响，回放在此处有缺口'},source='fact-ledger'))
                    reported=dropped;last_report=time.monotonic()
                if events and not self.error:
                    self._write_batch(conn,events,facts,raw,raw_stats)
                    commits+=1
                    if commits%100==0:conn.execute('PRAGMA wal_checkpoint(PASSIVE)')
                # Closed segments are compressed only after their rows committed,
                # so marking them recovered can never skip an unindexed event.
                while self.closed_segments and not stop:self.compress_queue.put(self.closed_segments.pop(0))
                for _ in batch: self.queue.task_done()
                if stop: break
        except Exception as exc:
            self.error=f'账本写入失败：{type(exc).__name__}'
            try:(self.log/'storage-error.json').write_text(dumps({'event_type':'engine.storage_degraded','at':utc_now(),'reason':self.error}),encoding='utf-8')
            except OSError:pass
            while True:
                try: self.queue.get_nowait(); self.queue.task_done()
                except queue.Empty: break
        finally:
            facts.rotate(final=True); raw.rotate(final=True)
            if not self.error:
                for path in self.closed_segments:
                    try:self._compress_and_mark(path,conn)
                    except Exception:pass
            self.closed_segments.clear()
            conn.close()

    def flush(self, timeout=10):
        until=time.monotonic()+timeout
        while self.queue.unfinished_tasks and self.thread.is_alive() and time.monotonic()<until:
            time.sleep(.01)
        if self.queue.unfinished_tasks or self.error:
            raise RuntimeError(self.error or '账本刷盘超时')

    def recent(self, limit=100, query='', mode=None):
        conn=sqlite3.connect(self.db_path,timeout=10)
        sql='SELECT body,event_id,event_type,observed_at_utc,mode,account_id,market_id FROM events WHERE (event_type LIKE ? OR body LIKE ?)'
        args=['%'+query+'%']*2
        if mode: sql+=' AND mode=?'; args.append(mode)
        sql+=' ORDER BY seq DESC LIMIT ?'; args.append(limit)
        rows=[self._decode_index(r) for r in conn.execute(sql,args)]
        conn.close(); return rows

    def all(self, kind=None, mode=None):
        conn=sqlite3.connect(self.db_path,timeout=10)
        sql='SELECT body,event_id,event_type,observed_at_utc,mode,account_id,market_id FROM events WHERE 1=1'; args=[]
        if kind: sql+=' AND event_type=?'; args.append(kind)
        if mode: sql+=' AND mode=?'; args.append(mode)
        for row in conn.execute(sql+' ORDER BY seq',args): yield self._decode_index(row)
        conn.close()

    @staticmethod
    def _decode_index(row):
        body=loads(row[0])
        if row[2]=='book.raw':
            body={**body,'schema_version':'3.1',**dict(zip(
                ('event_id','event_type','observed_at_utc','mode','account_id','market_id'),row[1:]))}
        return body

    def export(self, dest):
        self.flush(); dest=Path(dest); dest.mkdir(parents=True,exist_ok=True)
        events=dest/'events.jsonl'; table=dest/'events.csv'
        count=0
        with events.open('w',encoding='utf-8') as js,table.open('w',encoding='utf-8-sig',newline='') as cf:
            writer=csv.writer(cf); writer.writerow(['event_id','observed_at_utc','event_type','mode','account_id','market_id','payload'])
            seen=set()
            for e in self.raw_events():
                if e['event_id'] in seen:continue
                seen.add(e['event_id'])
                e=redact(e); js.write(dumps(e)+'\n')
                writer.writerow([e[k] for k in ['event_id','observed_at_utc','event_type','mode','account_id','market_id']]+[dumps(e['payload'])]); count+=1
        manifest={'count':count,'exported_at_utc':utc_now(),'sha256':hashlib.sha256(events.read_bytes()).hexdigest(),'redacted':True,'live_execution':False}
        (dest/'manifest.json').write_text(dumps(manifest,indent=2),encoding='utf-8')
        return manifest

    def raw_events(self):
        with self.files_lock:paths=sorted(self.event_dir.glob('*.jsonl*'))+sorted(self.raw_dir.glob('*.jsonl*'))
        for path in paths:
            if path.suffix=='.tmp':continue
            # Hold only one 8 MB segment against rotation, not the whole export.
            with self.files_lock:
                if not path.exists():path=path.with_suffix('.jsonl.gz')
                if not path.exists():continue
                opener=gzip.open if path.suffix=='.gz' else open
                with opener(path,'rt',encoding='utf-8') as stream:
                    for line in stream:
                        try:yield loads(line)
                        except ValueError:
                            if not line.endswith('\n'):break
                            raise

    def close(self):
        if self.closed: return
        self.closed=True
        if self.thread.is_alive():
            self.queue.put(None); self.thread.join(15)
        self.compress_queue.put(None); self.compressor.join(30)
