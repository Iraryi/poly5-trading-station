"""Fitted Q iteration for holding versus exercising a sell opportunity.

References: Ernst et al. (JMLR 2005). Decisions are validated by sequential
policy replay on whole unseen markets; the audit tail never selects a model.
"""
from collections import Counter
import os
from pathlib import Path
import time
from .data import read,digest,vector,summarise
from .dynamic import action,entry_allowed,execute_path,PROFIT_FLOOR,LATENCY_MS,MAX_GAP_MS,VERSION,DATA_REVISION,canonical_episodes

ITERATIONS=4

def split_episodes(rows):
    markets=sorted({r['start_s'] for r in rows})
    if len(markets)<60:raise ValueError('动态任务等待至少 60 个完整盘口市场')
    a=markets[int(len(markets)*.6)];b=markets[int(len(markets)*.8)]
    parts=([r for r in rows if r['start_s']<a-600],
           [r for r in rows if a<=r['start_s']<b-600],
           [r for r in rows if r['start_s']>=b])
    if min(len(p) for p in parts)<20:raise ValueError('动态任务隔离后路径不足')
    for p,q in zip(parts,parts[1:]):
        assert not {r['slug'] for r in p}&{r['slug'] for r in q}
        assert max(r['start_s']+300 for r in p)+600<=min(r['start_s'] for r in q)
    return parts

def fit_continuation(spec,rows):
    import numpy as np
    from .modeling import build,fit_weighted
    rows=[r for r in rows if entry_allowed(r['x'])]
    if not rows:raise ValueError('训练段没有符合因果入场条件的持仓路径')
    features=spec['features'];flat=[];terminal=[];weights=[];slices=[]
    markets=Counter(r['slug'] for r in rows)
    for r in rows:
        path=r['aux']['path'];start=len(flat);flat.extend(path);terminal.extend([r['y']]*len(path))
        weights.extend([1/(len(path)*markets[r['slug']])]*len(path));slices.append((start,len(flat),r))
    X=np.array([vector(p['x'],features) for p in flat],float);y=np.array(terminal,float)
    if not np.isfinite(X).all() or not np.isfinite(y).all():raise ValueError('动态路径含非法数值')
    w=np.array(weights);w*=len(w)/w.sum()
    model=build(spec['family'],spec['params'],spec['seed'],False);fit_weighted(model,X,y,w)
    for _ in range(ITERATIONS):
        prior=np.clip(model.predict(X),-1,10);target=np.array(terminal,float)
        for start,end,row in slices:
            path=row['aux']['path'];n=len(path);values=prior[start:end].copy()
            # At a next state the policy can choose sell versus hold; selling requires an executable future fill.
            j=0
            for i in range(n):
                j=max(j,i+1)
                while j<n and path[j]['at_ms']<path[i]['at_ms']+LATENCY_MS:j+=1
                reason=action(path[i]['x'],prior[start+i])
                if reason=='hold' or j>=n or path[j]['at_ms']-path[i]['at_ms']>MAX_GAP_MS:continue
                nxt=path[j]['x']
                if nxt['can_sell'] and (reason=='reversal' or nxt['sell_roi']>=PROFIT_FLOOR):values[i]=nxt['sell_roi']
            # Q_hold(s) is the value at the NEXT observable state, never an oracle max over the path.
            if n>1:target[start:end-1]=values[1:]
        model=build(spec['family'],spec['params'],spec['seed'],False);fit_weighted(model,X,target,w)
    return model,len(flat)

def evaluate(model,rows,features,mode='model'):
    import numpy as np
    points=[r['x'] for r in rows]+[p['x'] for r in rows for p in r['aux']['path']]
    if not points:return {**summarise([],[],0),'exits':{},'score':1e6,'baseline_comparison':None}
    predictions=np.clip(model.predict(np.array([vector(x,features) for x in points],float)),-1,10)
    if not np.isfinite(predictions).all():raise ValueError('动态候选输出非有限数值')
    cursor=len(rows);pnls=[];stakes=[];exits=Counter();holds=[];by_market={};raw=[]
    for i,row in enumerate(rows):
        n=len(row['aux']['path']);ps=predictions[cursor:cursor+n];cursor+=n
        raw.append((row,float(predictions[i]),ps))
    for row,p,ps in sorted(raw,key=lambda t:(t[0]['decision_ms'],t[0]['aux']['side'])):
        if row['slug'] in by_market or not entry_allowed(row['x']):continue
        if mode=='model' and p<PROFIT_FLOOR:continue
        result=execute_path(row,ps,mode);cost=row['aux']['entry_cost'];by_market[row['slug']]={**result,'cost':cost,'predicted_roi':p}
        stakes.append(cost);pnls.append(result['roi']*cost);holds.append(result['holding_s']);exits[result['reason']]+=1
    rois=np.array([r['roi'] for r in by_market.values()],float)
    # A no-trade candidate cannot win just by avoiding losses. Judge includes downside and uncertainty.
    score=float(-np.mean(rois)+.5*np.sqrt(np.mean(np.minimum(rois,0)**2))+np.std(rois)/np.sqrt(len(rois))) if len(rois)>=5 else 1e6
    return {**summarise(pnls,stakes,len({r['slug'] for r in rows})),
            'exits':dict(exits),'mean_holding_s':float(np.mean(holds)) if holds else None,
            'max_holding_s':max(holds) if holds else None,'score':score,'market_returns':by_market,
            'scope':'独立市场顺序回放，费用/深度/≥300ms 延迟；非实盘；缺失路径剔除并单列'}

def fit_dynamic_job(spec,rows):
    import joblib
    from threadpoolctl import threadpool_limits
    from poly5.common import atomic_json
    rows=canonical_episodes(rows)
    if not rows:raise ValueError('修正封盘边界后没有可训练的完整持仓路径')
    if spec.get('refit_of'):
        with threadpool_limits(limits=2):model,states=fit_continuation(spec,rows)
        metrics={**spec['metrics'],'refit_rows':len(rows),'refit_states':states,'trained_through_ms':max(r['ready_ms'] for r in rows),'forward_n':0}
    else:
        train,val,audit=split_episodes(rows)
        with threadpool_limits(limits=2):
            model,states=fit_continuation(spec,train)
            result=evaluate(model,val,spec['features']);hold=evaluate(model,val,spec['features'],'hold');rule=evaluate(model,val,spec['features'],'rule')
            paper=evaluate(model,audit,spec['features'])
        metrics={'score':result['score'],'baseline_score':hold['score'],'mse':None,'validation_policy':result,
                 'conservative_rule':rule,'hold_to_settlement':hold,'paper_pnl':paper,'train_n':len(train),'train_states':states,
                 'validation_n':len(val),'validation_markets':result['markets'],'audit_n':len(audit),'audit_used_for_selection':False,
                 'trained_through_ms':max(r['ready_ms'] for r in train),'forward_n':0,
                 'split':'whole markets 60/20/20 + 600s embargo; offline verified outcomes',
                 'objective':VERSION,'data_revision':DATA_REVISION,'iterations':ITERATIONS,'exit_timer_s':None,'profit_floor':PROFIT_FLOOR,
                 'limitations':'complete-path replay; censored data reported separately; recorded depth is not a trade receipt'}
    path=Path(spec['model_path']);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp')
    joblib.dump({'model':model,'features':spec['features'],'task':'dynamic','policy_version':VERSION},tmp,compress=3);os.replace(tmp,path)
    atomic_json(Path(spec['result']),{**spec,'metrics':metrics,'model_sha256':digest(path),'status':'complete','completed_ms':int(time.time()*1000)})
