"""Trainer-owned samples and immutable trial history, separate from trading logs."""
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

from .feature_schema import REF,BOOK
TASKS={'late':('最后一分钟 · 结算方向',REF+['stage_fraction'],'classification'),
       'direction':('五分钟 · 结算方向',REF+['stage_fraction'],'classification'),
       'roi5':('5 秒净 ROI',REF+BOOK,'regression'), 'roi15':('15 秒净 ROI',REF+BOOK,'regression')}
# Market-aware tasks: the executable market at the decision moment is an input. The market is the strongest single
# predictor; a model is only useful where it adds information the price lacks (judged by skill_vs_market).
MKT=['mkt_bid','mkt_ask','up_avg_price','down_avg_price']
TASKS['late_mkt']=('最后一分钟 · 盘口感知方向',REF+['stage_fraction']+MKT,'classification')
TASKS['direction_mkt']=('五分钟 · 盘口感知方向',REF+['stage_fraction']+MKT,'classification')
MARKET_TASKS={'late_mkt':'late','direction_mkt':'direction'}
from .dynamic import FEATURES as DYNAMIC_FEATURES
TASKS['dynamic']=('动态择机止盈',DYNAMIC_FEATURES,'regression')
LEGACY_TASKS=('roi5','roi15')
ACTIVE_TASKS=('dynamic','late','direction','late_mkt','direction_mkt')

# Derived from stored features at fit/predict time, so old samples and snapshots need no migration.
# z_* is the distance to the opening baseline in units of expected remaining move (sigma*sqrt(t)),
# the same quantity as d2 in a cash-or-nothing digital option; p_digital is its normal CDF.
DERIVED=['z_spot','z_twap','p_digital','sqrt_remaining_s','z_momentum_15s']
# implied_z inverts the market mid through the same normal model, so z_gap = how far the reference price says the
# market should be from where it is (model-implied vs market-implied distance).
MKT_DERIVED=['mkt_mid','mkt_spread','logit_mid','implied_z','z_gap']

def market_mid(x):
    return (x['mkt_bid']+x['mkt_ask'])/2

def derived(x):
    remaining=max(300*(1-x['stage_fraction']),1.);scale=max(x['vol_60s_bps'],.5)*math.sqrt(remaining)
    z=x['spot_open_bps']/scale
    out={'z_spot':z,'z_twap':x['twap_open_bps']/scale,'p_digital':.5*(1+math.erf(z/math.sqrt(2))),
         'sqrt_remaining_s':math.sqrt(remaining),'z_momentum_15s':x['return_15s_bps']/scale}
    if 'mkt_bid' in x:
        from statistics import NormalDist
        mid=min(max(market_mid(x),.005),.995);implied=NormalDist().inv_cdf(mid)
        out.update(mkt_mid=market_mid(x),mkt_spread=x['mkt_ask']-x['mkt_bid'],logit_mid=math.log(mid/(1-mid)),implied_z=implied,z_gap=z-implied)
    return out

# Paper (simulated) P&L for the user, never a training signal and never realised PnL: buy $5 of the side whose model
# probability beats its executable average entry price (depth-walked, fees included) by EDGE; at most one trade per
# market (the first qualifying decision); a winning share pays $1.
EDGE=.03
# Longshot filter (validated 2026-10-23, exp15: 8-day walk-forward, P&L improved in 7/8 configurations; longshots
# below 0.35 lost 15-46% after fees from 210s on, exp10). Never buy a side priced below this.
MIN_ENTRY_PRICE=.35

def paper_trades(items,min_price=MIN_ENTRY_PRICE):
    """items: (slug, decision_ms, p_up, quotes, label_up). Returns the summary dict shown to the user."""
    traded=set();trades=[];markets=set()
    for slug,decision,p,quotes,label in sorted(items,key=lambda i:(i[0],i[1])):
        if not quotes or label is None:continue
        markets.add(slug)
        if slug in traded:continue
        for side,prob,win in (('Up',p,label==1),('Down',1-p,label==0)):
            q=quotes.get(side)
            if q and q['shares']>0 and prob-q['total']/q['shares']>EDGE and q['total']/q['shares']>=min_price:
                traded.add(slug);trades.append((q['total'],q['shares']*win-q['total']));break
    return summarise([x for _,x in trades],[t for t,_ in trades],len(markets))

def summarise(pnls,stakes,markets):
    # 95% interval by resampling trades (one trade per market, so markets are the independent units).
    import random
    stake=sum(stakes);pnl=sum(pnls);ci=None
    if len(pnls)>=2:
        rng=random.Random(0);sums=sorted(sum(rng.choice(pnls) for _ in pnls) for _ in range(2000));ci=[round(sums[49],2),round(sums[1949],2)]
    return {'markets':markets,'trades':len(pnls),'wins':sum(x>0 for x in pnls),'stake':round(stake,2),'pnl':round(pnl,2),'roi':round(pnl/stake,4) if stake else None,'pnl_ci':ci}

def verdict(paper,minimum=200,refute_minimum=50):
    """Pre-registered confirmation rule for the frozen model's paper P&L. A handful of losing trades has a bootstrap
    interval entirely below zero, so refutation also needs a minimum sample."""
    if not paper or not paper.get('pnl_ci'):return '验证中'
    if paper['trades']>=minimum and paper['pnl_ci'][0]>0:return '已确认盈利'
    if paper['trades']>=refute_minimum and paper['pnl_ci'][1]<0:return '已否定（显著亏损）'
    return '验证中'

def roi_trades(items,threshold=.005):
    """items: (slug, predicted_roi, realised_roi, entry_cost). Enter when predicted net ROI >= threshold."""
    trades=[(cost,cost*y) for slug,pred,y,cost in items if y is not None and pred>=threshold]
    return summarise([x for _,x in trades],[t for t,_ in trades],len({i[0] for i in items}))

def design(task):
    return TASKS[task][1]+DERIVED+(MKT_DERIVED if task in MARKET_TASKS else [])

def vector(x,features):
    extra=derived(x) if any(k in DERIVED or k in MKT_DERIVED for k in features) else {}
    return [x[k] if k in x else extra[k] for k in features]

def live_registry(root):
    """Models that predict live: up to three per task (the deployed champion first)."""
    out=[]
    for path in sorted((Path(root)/'tasks').glob('*/registry.json')):
        if path.parent.name not in LEGACY_TASKS:out+=(read(path,[]) or [])[:3]
    for path in sorted((Path(root)/'tasks').glob('*/frozen.json')):  # pre-registered model keeps predicting
        frozen=read(path)
        if frozen and frozen['id'] not in {m['id'] for m in out}:out.append(frozen)
    return out

def read(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return default

def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

class Data:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'training.sqlite'
        with self.db() as c:c.executescript('''
            CREATE TABLE IF NOT EXISTS samples(id TEXT PRIMARY KEY, task TEXT, slug TEXT, start_s INTEGER,
              decision_ms INTEGER, ready_ms INTEGER, features TEXT, target REAL, source TEXT, aux TEXT);
            CREATE INDEX IF NOT EXISTS task_time ON samples(task,start_s);
            CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,at_ms INTEGER);
            CREATE TABLE IF NOT EXISTS trials(id TEXT PRIMARY KEY,scope TEXT,generation INTEGER, family TEXT,
              params TEXT,score REAL,body TEXT,created_ms INTEGER);
            CREATE TABLE IF NOT EXISTS predictions(model_id TEXT,sample_id TEXT,value REAL,at_ms INTEGER,
              PRIMARY KEY(model_id,sample_id));
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,at_ms INTEGER,kind TEXT,body TEXT);
            CREATE INDEX IF NOT EXISTS trial_created ON trials(created_ms DESC);
        ''')
    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=20);c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA synchronous=FULL')
        try:yield c;c.commit()
        finally:c.close()
    def event(self,kind,body):
        with self.db() as c:c.execute('INSERT INTO events(at_ms,kind,body) VALUES(?,?,?)',(int(time.time()*1000),kind,json.dumps(body,ensure_ascii=False)))
    @staticmethod
    def row(id,task,slug,start,decision,features,target=None,ready=None,source='live',aux=None):
        if task not in TASKS or not all(k in features and math.isfinite(float(features[k])) for k in TASKS[task][1]):raise ValueError('特征不完整或包含非有限值')
        if decision<start*1000 or (ready is not None and ready<decision):raise ValueError('特征/标签时间非法')
        if target is not None and not math.isfinite(float(target)):raise ValueError('标签不是有限数值')
        return (id,task,slug,start,decision,ready,json.dumps(features),target,source,json.dumps(aux or {}))
    def add(self,*args,**kwargs):
        row=self.row(*args,**kwargs)
        with self.db() as c:c.execute('INSERT OR IGNORE INTO samples VALUES(?,?,?,?,?,?,?,?,?,?)',row)
    def label(self,id,target,ready):
        if not math.isfinite(float(target)):raise ValueError('非法标签')
        with self.db() as c:c.execute('UPDATE samples SET target=?,ready_ms=? WHERE id=? AND target IS NULL AND decision_ms<=?',(target,ready,id,ready))
    def snapshot(self,task):
        with self.db() as c:
            rows=c.execute('SELECT id,slug,start_s,decision_ms,ready_ms,features,target,source,aux FROM samples WHERE task=? AND target IS NOT NULL AND ready_ms<=? ORDER BY start_s,decision_ms LIMIT 200000',(task,int(time.time()*1000))).fetchall()
        out=[{'id':r[0],'slug':r[1],'start_s':r[2],'decision_ms':r[3],'ready_ms':r[4],'x':json.loads(r[5]),'y':r[6],'source':r[7],'aux':json.loads(r[8])} for r in rows]
        if task=='dynamic':
            from .dynamic import canonical_episodes
            return canonical_episodes(out)
        return out
    def stats(self,task):
        with self.db() as c:
            total,ready,live=c.execute("SELECT COUNT(*),SUM(target IS NOT NULL),SUM(source='live') FROM samples WHERE task=?",(task,)).fetchone()
        return {'samples':total,'labelled':ready or 0,'live_samples':live or 0}
    def predict(self,model_id,sample_id,value,at_ms):
        with self.db() as c:
            row=c.execute('SELECT decision_ms,target FROM samples WHERE id=?',(sample_id,)).fetchone()
            if row and row[1] is None and row[0]<=at_ms:
                c.execute('INSERT OR IGNORE INTO predictions VALUES(?,?,?,?)',(model_id,sample_id,float(value),at_ms))
    def forward(self,model_id):
        with self.db() as c:rows=c.execute('SELECT p.value,s.target FROM predictions p JOIN samples s ON s.id=p.sample_id WHERE p.model_id=? AND s.target IS NOT NULL AND p.at_ms<s.ready_ms',(model_id,)).fetchall()
        return {'n':len(rows),'mse':sum((p-y)**2 for p,y in rows)/len(rows) if rows else None}
    def deployed_forward(self,task):
        # The champion changes every batch, so per-model forward counts stay small; pool the first prediction any
        # deployed (_FULL) model made for each live sample, strictly before its outcome was known.
        with self.db() as c:rows=c.execute('''SELECT s.id,p.value,s.target FROM predictions p JOIN samples s ON s.id=p.sample_id
          WHERE s.task=? AND s.source='live' AND s.target IS NOT NULL AND p.at_ms<s.ready_ms AND substr(p.model_id,-5)='_FULL'
          ORDER BY s.id,p.at_ms''',(task,)).fetchall()
        first={}
        for id,value,target in rows:first.setdefault(id,(value,target))
        n=len(first)
        return {'n':n,'mse':sum((p-y)**2 for p,y in first.values())/n if n else None,'accuracy':sum((p>=.5)==(y==1) for p,y in first.values())/n if n else None}
    def forward_paper(self,task,model_ids=None,since_ms=0,min_price=MIN_ENTRY_PRICE):
        """Paper P&L of real-time predictions (made before the outcome), per model id or pooled over deployed (_FULL) models."""
        with self.db() as c:rows=c.execute('''SELECT p.model_id,s.id,s.slug,s.decision_ms,p.value,s.target,s.aux,p.at_ms FROM predictions p JOIN samples s ON s.id=p.sample_id
          WHERE s.task=? AND s.source='live' AND s.target IS NOT NULL AND p.at_ms<s.ready_ms AND p.at_ms>=? ORDER BY s.id,p.at_ms''',(task,since_ms)).fetchall()
        groups={}
        for model,sid,slug,decision,value,target,aux,at in rows:
            key='deployed' if model_ids is None else model
            if model_ids is None and not model.endswith('_FULL'):continue
            if model_ids is not None and model not in model_ids:continue
            groups.setdefault(key,{}).setdefault(sid,(slug,decision,value,target,json.loads(aux or '{}')))
        out={}
        for key,samples in groups.items():
            if TASKS[task][2]=='classification':out[key]=paper_trades([(s,d,v,a.get('quotes'),t) for s,d,v,t,a in samples.values()],min_price)
            else:out[key]=roi_trades([(s,v,t,a.get('entry_cost',5)) for s,d,v,t,a in samples.values()])
        return out
    def paired_forward(self,first,second):
        with self.db() as c:rows=c.execute('''SELECT a.value,b.value,s.target FROM predictions a
          JOIN predictions b ON a.sample_id=b.sample_id JOIN samples s ON s.id=a.sample_id
          WHERE a.model_id=? AND b.model_id=? AND s.source='live' AND s.target IS NOT NULL
          AND a.at_ms<s.ready_ms AND b.at_ms<s.ready_ms''',(first,second)).fetchall()
        return {'n':len(rows),'first_mse':sum((a-y)**2 for a,b,y in rows)/len(rows) if rows else None,
                'second_mse':sum((b-y)**2 for a,b,y in rows)/len(rows) if rows else None}

def proxy_manifest(root):
    """Training-only historical backfill (official outcomes + proxy price source) kept as immutable parquet files."""
    return [{'path':str(p),'sha256':digest(p)} for p in sorted((Path(root)/'proxy').glob('*.parquet'))]

def proxy_count(manifest,task):
    if task not in ('late','direction') or not manifest:return 0
    import polars as pl
    return sum(pl.read_parquet(i['path'],columns=['offset','label_up']).filter(pl.col('label_up').is_in([0,1])&(pl.col('offset')>=(240 if task=='late' else 0))).height for i in manifest)

def replay_days(manifest,rows,warmup_days=3,min_markets=200):
    """Walk-forward replay schedule: UTC days (start seconds) of history, each used once as fresh validation data.
    The first warmup days only ever train; a day needs min_markets labelled markets (real or proxy) to be judged."""
    if not manifest:return []
    import polars as pl
    proxy_days=set()
    counts={}
    for item in manifest:
        for start in pl.read_parquet(item['path'],columns=['market_slug','start_s']).unique('market_slug')['start_s'].to_list():
            day=int(start)//86400*86400;proxy_days.add(day);counts.setdefault(day,set()).add(int(start))
    for r in rows:counts.setdefault(int(r['start_s'])//86400*86400,set()).add(int(r['start_s']))
    first=min(proxy_days)+warmup_days*86400
    return sorted(d for d,s in counts.items() if d>=first and len(s)>=min_markets)

def proxy_rows(manifest,task,exclude_slugs=()):
    # Never stored in SQLite or snapshots; the judge's validation/audit never contains these rows.
    if task not in ('late','direction') or not manifest:return []
    import polars as pl
    rows=[];exclude=set(exclude_slugs)
    for item in manifest:
        if digest(item['path'])!=item['sha256']:raise ValueError('代理历史数据文件哈希不匹配')
        frame=pl.read_parquet(item['path']).filter(pl.col('label_up').is_in([0,1]))
        if task=='late':frame=frame.filter(pl.col('offset')>=240)
        for r in frame.iter_rows(named=True):
            if r['market_slug'] in exclude:continue
            x={k:float(r[k]) for k in REF};x['stage_fraction']=r['offset']/300
            rows.append({'id':f"proxy:{task}:{r['market_slug']}:{r['offset']}",'slug':r['market_slug'],'start_s':int(r['start_s']),
                'decision_ms':int(r['decision_us'])//1000,'ready_ms':(int(r['start_s'])+300)*1000,'x':x,'y':float(r['label_up']),'source':'proxy'})
    return rows

def import_seed(data,folder):
    import polars as pl
    folder=Path(folder)
    for filename in ['reference-features.parquet','roi-dataset.parquet','market-features.parquet']:
        path=folder/filename
        if not path.exists():continue  # extra archives (root/archive) may carry only one of the two files
        key=filename+':'+digest(path)
        with data.db() as c:
            if c.execute('SELECT 1 FROM imports WHERE id=?',(key,)).fetchone():continue
        frame=pl.read_parquet(path);batch=[]
        for row in frame.iter_rows(named=True):
            slug=row['market_slug'];start=int(slug.rsplit('-',1)[1]);at=int(row['decision_us'])//1000
            # Known disagreement windows are quarantined, never silently relabelled.
            if slug in ('btc-updown-5m-1786911600','btc-updown-5m-1787354400'):continue
            if filename.startswith('market'):
                # Historical market-aware rows (PMData books at the decision moment); quotes kept for paper P&L.
                f={k:float(row[k]) for k in REF};f['stage_fraction']=row['offset']/300
                f.update(mkt_bid=float(row['mkt_bid']),mkt_ask=float(row['mkt_ask']),up_avg_price=row['up_total']/row['up_shares'],down_avg_price=row['down_total']/row['down_shares'])
                quotes={'Up':{'shares':row['up_shares'],'total':row['up_total']},'Down':{'shares':row['down_shares'],'total':row['down_total']}}
                for task in ['direction_mkt']+(['late_mkt'] if row['offset']>=240 else []):
                    batch.append(data.row(f'archive:{task}:{slug}:{row["offset"]}',task,slug,start,at,f,float(row['label_up']),(start+300)*1000,'archive',{'offset':row['offset'],'quotes':quotes,'quote_basis':'$5 budget incl. fees'}))
            elif filename.startswith('reference'):
                if row.get('label_up') not in (0,1):continue
                f={k:float(row[k]) for k in REF};f['stage_fraction']=row['offset']/300
                for task in ['direction']+(['late'] if row['offset']>=240 else []):
                    batch.append(data.row(f'archive:{task}:{slug}:{row["offset"]}',task,slug,start,at,f,float(row['label_up']),(start+300)*1000,'archive',{'offset':row['offset'],'archive_previously_studied':True}))
            else:
                for task,h in [('roi5',5),('roi15',15)]:
                    if not row.get('entry_filled') or not row.get(f'exit_available_{h}s'):continue
                    batch.append(data.row(f'archive:{task}:{slug}:{row["stage"]}',task,slug,start,at,{k:float(row[k]) for k in REF+BOOK},float(row[f'roi_{h}s']),at+(h+2)*1000,'archive',{'entry_cost':row['entry_cost'],'amount_basis':'legacy $5 inclusive of fees'}))
        with data.db() as c:
            c.executemany('INSERT OR IGNORE INTO samples VALUES(?,?,?,?,?,?,?,?,?,?)',batch)
            c.execute('INSERT OR IGNORE INTO imports VALUES(?,?)',(key,int(time.time()*1000)))
        data.event('seed_import',{'file':filename,'sha256':digest(path)})
