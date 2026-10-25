"""Build holding trajectories from recorded depth and causally received reference points.

Only public GETs are used. Incomplete paths are explicitly censored, never silently
filled through a feed outage. Existing rows, checkpoints and model artifacts are immutable.
"""
import asyncio
from bisect import bisect_right
from collections import deque,Counter
from decimal import Decimal as D
import json
from pathlib import Path
import shutil
import time
import threading
from types import SimpleNamespace
from .data import read,digest,vector
from .dynamic import VERSION,MAX_GAP_MS,LATENCY_MS,buy,state_features
from poly5.common import atomic_json
from poly5.domain import DataError

_PRIOR_LOCK=threading.RLock()

async def verified_metadata(api,market):
    from poly5.api import GAMMA,CLOB
    event=await api.get(GAMMA+'/events/slug/'+market['slug'])
    items=event.get('markets',[])
    if event.get('slug')!=market['slug'] or len(items)!=1:raise DataError('历史市场身份不唯一')
    m=items[0];cid=market['condition_id']
    if m.get('conditionId')!=cid:raise DataError('历史市场身份不匹配')
    raw=await api.get(CLOB+'/markets/'+cid);tokens=raw.get('tokens',[])
    mapping={market['up_token']:'Up',market['down_token']:'Down'}
    if raw.get('condition_id')!=cid or {str(t['token_id']):t['outcome'] for t in tokens}!=mapping:raise DataError('结算 token 身份错误')
    if raw.get('closed') is not True or len(tokens)!=2 or any(type(t.get('winner')) is not bool for t in tokens) or sum(t['winner'] for t in tokens)!=1:raise DataError('结算尚未确认')
    schedule=m.get('feeSchedule') or {}
    if m.get('feesEnabled') is False:rate='0';exponent=1
    elif schedule.get('takerOnly') is True and schedule.get('exponent') in (1,2):rate=str(schedule['rate']);exponent=int(schedule['exponent'])
    else:raise DataError('历史手续费未核验')
    from poly5.domain import Fee
    Fee(D(rate),exponent)
    return {**market,'fee_rate':rate,'fee_exponent':exponent,'min_size':str(raw['minimum_order_size']),
            'winner':next(t['outcome'] for t in tokens if t['winner']),'verified_ms':int(time.time()*1000),
            'evidence_urls':[GAMMA+'/events/slug/'+market['slug'],CLOB+'/markets/'+cid]}

def freeze_priors(root):
    # Collector startup and the population supervisor can arrive here together.
    with _PRIOR_LOCK:return _freeze_priors(root)

def _freeze_priors(root):
    """Retain pretrained estimators as causal input features, never reinterpret their old targets."""
    root=Path(root);folder=root/'dynamic/priors';folder.mkdir(parents=True,exist_ok=True)
    manifest=read(folder/'manifest.json')
    if manifest is None:
        manifest={}
        for task in ('roi5','roi15'):
            m=read(root/f'tasks/{task}/best.json')
            if not m or digest(m['model_path'])!=m['model_sha256']:continue
            dest=folder/(task+'.joblib');shutil.copy2(m['model_path'],dest)
            manifest[task]={'id':m['id'],'path':str(dest),'sha256':digest(dest),'trained_through_ms':m['metrics']['trained_through_ms'],
                            'family':m['family'],'params':m['params'],'original_target':task,'transfer':'frozen forecast feature and parent hyperparameters'}
        atomic_json(folder/'manifest.json',manifest)
    import joblib
    return {task:(joblib.load(m['path']),m) for task,m in manifest.items() if digest(m['path'])==m['sha256']}

def add_priors(rows,priors):
    import numpy as np
    for task,(bundle,meta) in priors.items():
        points=[]
        for row in rows:
            if row['decision_ms']<=meta['trained_through_ms'] or row['aux']['side']!='Up':continue
            points.append(row['x']);points.extend(p['x'] for p in row['aux']['path'])
        if not points:continue
        values=bundle['model'].predict(np.array([vector(x,bundle['features']) for x in points]))
        for x,v in zip(points,values):x['legacy_up_'+task]=float(v)

def build_episodes(meta,frames,reference_at,source='recorded'):
    """Same builder in historical backfill and live recording; all reference inputs are as-of reception."""
    histories={s:deque(maxlen=20) for s in ('Up','Down')};tracks=[];episodes=[];counts=Counter();attempted=set()
    for frame in sorted(frames,key=lambda f:(f['available_ms'],f['outcome'])):
        now=frame['available_ms'];side=frame['outcome'];history=histories[side]
        if not meta['start_s']*1000<=now<meta['end_s']*1000:
            counts['outside_market_frames']+=1;continue
        try:ref,evidence=reference_at(now)
        except (DataError,ValueError,IndexError):ref=None;evidence=None
        # Progress every existing position without resampling or selecting its profitable path.
        for tr in tracks:
            if tr['side']!=side or tr.get('censored'):continue
            if now-tr['last_ms']>MAX_GAP_MS or ref is None:
                tr['censored']=True;counts['censored_gap']+=1;continue
            if tr.get('position') is None:
                if now<tr['decision_ms']+LATENCY_MS:continue
                try:tr['position']=buy(frame,meta,now,tr['limit'])
                except DataError:tr['censored']=True;counts['unfilled_entry']+=1;continue
            pos=tr['position'];f=state_features(ref,frame,meta,pos,now,side,history,tr['peak'])
            tr['peak']=f['peak_roi'];tr['path'].append({'at_ms':now,'x':f});tr['last_ms']=now
        # Predeclared decision grid, not a fixed exit horizon. No retrospective choice of entry time.
        for offset in (60,120,210,240,270):
            key=(offset,side);decision=meta['start_s']*1000+offset*1000
            if key in attempted or not decision<=now<=decision+1500:continue
            attempted.add(key);counts['attempted_entries']+=1
            if ref is None:counts['missing_entry_reference']+=1;continue
            try:
                prospective=buy(frame,meta,now);x=state_features(ref,frame,meta,prospective,now,side,history)
            except DataError:counts['unfilled_entry']+=1;continue
            tracks.append({'side':side,'decision_ms':now,'x':x,'last_ms':now,'position':None,'path':[],
                           'peak':0.,'limit':prospective['limit'],'evidence':evidence})
        if frame['bid_prices'] and frame['ask_prices']:history.append((now,(frame['bid_prices'][0]+frame['ask_prices'][0])/2))
    for tr in tracks:
        if tr.get('censored') or not tr['position'] or len(tr['path'])<2:continue
        if meta['end_s']*1000-tr['last_ms']>MAX_GAP_MS:counts['censored_tail']+=1;continue
        pos=tr['position'];win=tr['side']==meta['winner'];y=float((D(pos['shares'])*int(win)-D(pos['total']))/D(pos['total']))
        aux={'version':VERSION,'path':tr['path'],'side':tr['side'],'condition_id':meta['condition_id'],
             'asset_id':meta['up_token'] if tr['side']=='Up' else meta['down_token'],
             'entry_ms':pos['entry_ms'],'entry_cost':float(pos['total']),'entry_receipt':pos,'evidence':tr['evidence'],
             'fee_rate':meta['fee_rate'],'fee_exponent':meta['fee_exponent'],'official_winner':meta['winner'],
             'outcome_verified_ms':meta['verified_ms'],'label_scope':'official terminal outcome, not an executable sell',
             'latency_ms':LATENCY_MS,'source':source}
        episodes.append({'id':f'{VERSION}:{meta["slug"]}:{tr["decision_ms"]}:{tr["side"]}',
                         'slug':meta['slug'],'start_s':meta['start_s'],'decision_ms':tr['decision_ms'],
                         'ready_ms':meta['verified_ms'],'x':tr['x'],'y':y,'source':source,'aux':aux})
    counts['complete_episodes']=len(episodes)
    return episodes,dict(counts)

class RecordedReference:
    def __init__(self,rows,meta):
        from poly5.reference import ReferenceTape
        self.rows=sorted(rows,key=lambda r:(r['received_ms'],r['observed_ms']));self.i=0;self.meta=meta
        self.tape=ReferenceTape();self.tape.connected=True;self.cached={}
    def __call__(self,now):
        from .collector import reference_features
        if now in self.cached:return self.cached[now]
        while self.i<len(self.rows) and self.rows[self.i]['received_ms']<=now:
            r=self.rows[self.i];self.i+=1
            self.tape.add(r['kind'],r['observed_ms'],r['received_ms'],r['value'])
        result=reference_features(self.tape,SimpleNamespace(start=self.meta['start_s']),now)
        self.cached[now]=result
        return result

async def backfill(data,status=None,stop_event=None):
    """Incremental once-per-closed-market import; suitable for a low-frequency collector background task."""
    import polars as pl
    from poly5.api import PublicAPI
    stopped=lambda:stop_event is not None and stop_event.is_set()
    if stopped():return {'stopped':True}
    root=data.root;folder=root/'dynamic';folder.mkdir(parents=True,exist_ok=True)
    files=sorted((root/'orderbook').glob('date=*/markets/*.parquet'))
    if not files:return {'markets':0,'episodes':0}
    markets=pl.concat([pl.read_parquet(f) for f in files],how='diagonal_relaxed').unique('slug').sort('start_s').to_dicts()
    cache=read(folder/'metadata.json',{}) or {};done=read(folder/'imported.json',{}) or {};api=PublicAPI();sem=asyncio.Semaphore(3)
    candidates=[m for m in markets if m['end_s']<int(time.time())-120 and m['slug'] not in done]
    async def fetch(m):
        if m['slug'] in cache:return
        async with sem:
            if stopped():return
            try:cache[m['slug']]=await verified_metadata(api,m)
            except Exception as e:
                if status is not None:status['dynamic_metadata']=type(e).__name__+': '+str(e)[:90]
    try:await asyncio.gather(*(fetch(m) for m in candidates))
    finally:await api.close()
    atomic_json(folder/'metadata.json',cache)
    # Only decode completed, unimported markets. Avoid loading high-volume raw BBO/trades.
    todo={m['slug'] for m in candidates if m['slug'] in cache}
    if not todo:return {'markets':len(done),'episodes':sum(v['complete_episodes'] for v in done.values())}
    bookfiles=sorted((root/'orderbook').glob('date=*/books/*.parquet'));reffiles=sorted((root/'orderbook').glob('date=*/reference/*.parquet'))
    books=pl.concat([pl.read_parquet(f) for f in bookfiles],how='diagonal_relaxed').filter(pl.col('slug').is_in(list(todo)))
    references=pl.concat([pl.read_parquet(f) for f in reffiles],how='diagonal_relaxed')
    priors=freeze_priors(root)
    for slug in sorted(todo,key=lambda s:cache[s]['start_s']):
        if stopped():break
        m=cache[slug];frames=books.filter(pl.col('slug')==slug).to_dicts()
        for f in frames:
            # Legacy recorder rounded the capture timestamp down. Expose the snapshot only AFTER that second.
            f['available_ms']=int(f.get('captured_ms') or f['ts_ms']+1000)
        refs=references.filter((pl.col('received_ms')>=(m['start_s']-1800)*1000)&(pl.col('received_ms')<=m['end_s']*1000)).to_dicts()
        rows,counts=build_episodes(m,frames,RecordedReference(refs,m));add_priors(rows,priors)
        with data.db() as db:
            db.executemany('INSERT OR IGNORE INTO samples VALUES(?,?,?,?,?,?,?,?,?,?)',[
                data.row(r['id'],'dynamic',slug,r['start_s'],r['decision_ms'],r['x'],r['y'],r['ready_ms'],r['source'],r['aux']) for r in rows])
        done[slug]=counts;atomic_json(folder/'imported.json',done)
        if status is not None:status['dynamic_backfill']=f'动态路径：{len(done)} 个市场 / {sum(v["complete_episodes"] for v in done.values())} 条'
    report={'version':VERSION,'markets':len(done),'episodes':sum(v['complete_episodes'] for v in done.values()),
            'counts':dict(sum((Counter(v) for v in done.values()),Counter())),'observed_ms':int(time.time()*1000),
            'fee_evidence':'metadata.json: individually verified Gamma feeSchedule and CLOB token winners',
            'missing_paths':'censored, not scored as zero PnL or interpolated',
            'holding_rule':'no 5s/15s/30s deadline; take profit / sudden adverse reversal / official settlement'}
    atomic_json(folder/'backfill-report.json',report);return report

if __name__=='__main__':
    from .data import Data
    print(json.dumps(asyncio.run(backfill(Data('D:/POLYMARKET/trainer'))),ensure_ascii=False))
