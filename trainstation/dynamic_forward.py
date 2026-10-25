"""Durable, genuinely forward paper positions for the dynamic policy; never orders."""
from collections import deque
from decimal import Decimal as D
import json
import math
from pathlib import Path
import time
from .data import digest,vector,summarise
from .dynamic import (VERSION,LATENCY_MS,MAX_GAP_MS,PROFIT_FLOOR,buy,state_features,
                      entry_allowed,action,sell_roi)
from .dynamic_dataset import freeze_priors
from poly5.domain import DataError

class DynamicForward:
    def __init__(self,data):
        self.data=data;self.states={};self.finished=set();self.bundles={};self.histories={};self.priors=freeze_priors(data.root)
        with data.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS dynamic_forward(model_id TEXT,slug TEXT,body TEXT,PRIMARY KEY(model_id,slug))')
            for mid,slug,body in db.execute('SELECT model_id,slug,body FROM dynamic_forward'):
                s=json.loads(body)
                if s['status'] in ('closed','no_entry','censored'):self.finished.add((mid,slug))
                else:self.states[mid,slug]=s
    def save(self,key,s):
        with self.data.db() as db:db.execute('INSERT OR REPLACE INTO dynamic_forward VALUES(?,?,?)',(*key,json.dumps(s)))
        if s['status'] in ('closed','no_entry','censored'):self.finished.add(key)
    def bundle(self,meta):
        mid=meta['id']
        if mid not in self.bundles:
            import joblib
            if digest(meta['model_path'])!=meta['model_sha256']:raise DataError('动态模型哈希错误')
            self.bundles[mid]=joblib.load(meta['model_path'])
        return self.bundles[mid]
    def predict(self,meta,f):
        b=self.bundle(meta);value=float(b['model'].predict([vector(f,b['features'])])[0])
        if not math.isfinite(value):raise DataError('动态模型输出非有限数值，未产生买卖决定')
        return value
    def tick(self,market,frames,ref,evidence,models,now):
        if market.fee is None:return
        meta={'slug':market.slug,'condition_id':market.condition_id,'up_token':market.outcomes['Up'],'down_token':market.outcomes['Down'],
              'start_s':market.start,'end_s':market.end,'tick':str(market.tick),'min_size':str(market.min_size),
              'fee_rate':str(market.fee.rate),'fee_exponent':market.fee.exponent}
        for model in models:
            key=(model['id'],market.slug)
            # Freeze the model identity before the opening decision; updates cannot change a position's policy.
            if key not in self.states and key not in self.finished and model['metrics']['trained_through_ms']<now:
                self.states[key]={'status':'watching','model':model,'market':meta,'attempted':[],'created_ms':now,
                                  'policy':VERSION,'last_ms':now,'trace':[],'peak':0.,'censored':False}
        for key,s in list(self.states.items()):
            if key[1]!=market.slug or s['status'] in ('closed','no_entry','censored'):continue
            status=s['status'];elapsed=(now-market.start*1000)/1000
            if status=='watching':
                for offset in (60,120,210,240,270):
                    if offset in s['attempted'] or not offset<=elapsed<=offset+1.5:continue
                    s['attempted'].append(offset)
                    choices=[]
                    if ref is not None:
                        for side,frame in frames.items():
                            try:
                                p=buy(frame,meta,now);history=self.histories.get((market.slug,side),[])
                                f=state_features(ref,frame,meta,p,now,side,history,priors=self.priors)
                                if entry_allowed(f):choices.append((self.predict(s['model'],f),side,p,f))
                            except DataError:continue
                    if choices:
                        pred,side,p,f=max(choices,key=lambda c:c[0])
                        if pred>=PROFIT_FLOOR:
                            s.update(status='entry_pending',decision_ms=now,entry_due_ms=now+LATENCY_MS,side=side,
                                     limit=p['limit'],entry_features=f,entry_prediction=pred,reference_evidence=evidence)
                    break
            elif status=='entry_pending':
                if now>=s['entry_due_ms']:
                    try:
                        if now-s['entry_due_ms']>MAX_GAP_MS or ref is None:raise DataError('入场延迟/参考行情不可用')
                        s['position']=buy(frames[s['side']],meta,now,s['limit']);s.update(status='holding',last_ms=now)
                        s['trace'].append({'at_ms':now,'action':'paper_entry','position':s['position']})
                    except DataError:s.update(status='watching',unfilled=s.get('unfilled',0)+1)
            elif status in ('holding','exit_pending'):
                if now-s['last_ms']>MAX_GAP_MS or ref is None:s['censored']=True
                s['last_ms']=now
                if ref is not None:
                    frame=frames[s['side']];f=state_features(ref,frame,meta,s['position'],now,s['side'],
                        self.histories.get((market.slug,s['side']),[]),s['peak'],self.priors)
                    s['peak']=f['peak_roi']
                    try:prediction=self.predict(s['model'],f)
                    except (DataError,OSError,ValueError) as exc:
                        s['censored']=True;s['model_error']=type(exc).__name__+': '+str(exc)[:120]
                        s['trace'].append({'at_ms':now,'action':'model_unavailable_keep_position','error':s['model_error']})
                        self.save(key,s);continue
                    if status=='exit_pending' and now>=s['exit_due_ms']:
                        roi=sell_roi(frame,meta,s['position'],now)
                        if roi is not None and now-s['exit_due_ms']<=MAX_GAP_MS and (s['exit_reason']=='reversal' or roi>=PROFIT_FLOOR):
                            s.update(status='closed' if not s['censored'] else 'censored',roi=roi,exit_ms=now,holding_s=f['holding_s'],exit_features=f)
                            s['trace'].append({'at_ms':now,'action':'paper_exit','reason':s['exit_reason'],'net_roi':roi})
                        else:
                            s['status']='holding';s['trace'].append({'at_ms':now,'action':'missed_exit_keep_holding'})
                    elif status=='holding':
                        decision=action(f,prediction)
                        if decision!='hold':s.update(status='exit_pending',exit_due_ms=now+LATENCY_MS,exit_reason=decision)
                    # Keep an inspectable causal decision trace; bounded by the natural 5-minute market only.
                    s['trace'].append({'at_ms':now,'action':s['status'],'continuation_roi':prediction,
                                       'sell_roi':f['sell_roi'] if f['can_sell'] else None,'holding_s':f['holding_s'],
                                       'side_distance_bps':f['side_sign']*f['spot_open_bps']})
            self.save(key,s)
        for side,f in frames.items():
            if f['bid_prices'] and f['ask_prices']:
                self.histories.setdefault((market.slug,side),deque(maxlen=20)).append((now,(f['bid_prices'][0]+f['ask_prices'][0])/2))
        # Active market history only; persisted paper states retain their own market identities.
        self.histories={k:v for k,v in self.histories.items() if k[0]==market.slug}
        self.states={k:s for k,s in self.states.items() if k not in self.finished or k[1]==market.slug}
        keep={s['model']['id'] for s in self.states.values()}|{m['id'] for m in models}
        self.bundles={k:v for k,v in self.bundles.items() if k in keep}
    async def resolve(self,api,now):
        from poly5.api import CLOB
        groups={}
        for key,s in self.states.items():
            if s['market']['end_s']*1000>now or s['status'] in ('closed','censored','no_entry'):continue
            groups.setdefault(s['market']['condition_id'],[]).append((key,s))
        for cid,items in groups.items():
            raw=await api.get(CLOB+'/markets/'+cid);tokens=raw.get('tokens',[])
            if raw.get('condition_id')!=cid or raw.get('closed') is not True or len(tokens)!=2 or any(type(t.get('winner')) is not bool for t in tokens) or sum(t['winner'] for t in tokens)!=1:continue
            winning=next(t for t in tokens if t['winner'])
            for key,s in items:
                mapping={s['market']['up_token']:'Up',s['market']['down_token']:'Down'}
                if {str(t['token_id']):t['outcome'] for t in tokens}!=mapping:continue
                if 'position' not in s:s['status']='no_entry'
                else:
                    p=s['position'];roi=float((D(p['shares'])*int(s['side']==winning['outcome'])-D(p['total']))/D(p['total']))
                    # A crash/restart or missing final segment cannot become an invented clean hold-to-expiry.
                    if s['market']['end_s']*1000-s['last_ms']>MAX_GAP_MS:s['censored']=True
                    s.update(status='censored' if s['censored'] else 'closed',roi=roi,exit_ms=now,exit_reason='official_settlement',
                             holding_s=(s['market']['end_s']*1000-p['entry_ms'])/1000,official_winner=winning['outcome'])
                self.save(key,s)

def forward_summary(data,model_ids=None):
    with data.db() as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='dynamic_forward'").fetchone()
        # Do not materialise hours of per-second traces just to render a P&L label.
        rows=[json.loads(b) for (b,) in db.execute("SELECT json_remove(body,'$.trace','$.model.metrics','$.model.deploy') FROM dynamic_forward")] if exists else []
    if model_ids is not None:rows=[r for r in rows if r['model']['id'] in model_ids]
    else:
        # The first deployed model registered per market is the pooled forward cohort; never double-count model variants.
        first={}
        for r in sorted(rows,key=lambda r:r['created_ms']):
            if r['model']['id'].endswith('_FULL'):first.setdefault(r['market']['slug'],r)
        rows=list(first.values())
    closed=[r for r in rows if r['status']=='closed' and 'position' in r];exits={}
    for r in closed:exits[r['exit_reason']]=exits.get(r['exit_reason'],0)+1
    stakes=[float(r['position']['total']) for r in closed]
    return {**summarise([r['roi']*s for r,s in zip(closed,stakes)],stakes,len({r['market']['slug'] for r in rows})),
            'policy_kind':'dynamic',
            'open':sum(r['status'] in ('holding','exit_pending','entry_pending') for r in rows),
            'entered':sum('position' in r for r in rows),
            'pending_entries':sum(r['status']=='entry_pending' for r in rows),
            'holding':sum(r['status'] in ('holding','exit_pending') for r in rows),
            'censored':sum(bool(r.get('censored')) or r['status']=='censored' for r in rows),
            'censored_open':sum(bool(r.get('censored')) and r['status'] in ('holding','exit_pending') for r in rows),
            'no_entry':sum(r['status']=='no_entry' for r in rows),'exits':exits,
            'settlements':sum(r.get('exit_reason')=='official_settlement' for r in closed),
            'sold':sum(r.get('exit_reason')!='official_settlement' for r in closed),
            'scope':'预先固定模型、实时逐秒纸面持仓；从未下单；无固定秒数退出'}
