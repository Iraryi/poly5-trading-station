"""Programmatic judge and bounded evolutionary mutations. No exchange calls."""
import json
import math
import os
from pathlib import Path
import random
import time
from .data import TASKS,MARKET_TASKS,design,vector,market_mid,digest,read,proxy_rows,paper_trades,roi_trades

FAMILIES=['hist','forest','linear','extra','logit']
# Probability calibration (Platt 1999 sigmoid; Zadrozny & Elkan 2002 isotonic; Niculescu-Mizil & Caruana 2005):
# forests are under-confident at the extremes, which turned into longshot buying in the paper backtest.
CALIBRATIONS=['none','sigmoid','isotonic']

def propose(family,rng,parent=None):
    grids={'hist':{'max_iter':[60,100,140],'max_leaf_nodes':[5,7,15],'min_samples_leaf':[30,60,100],'learning_rate':[.025,.05,.08],'l2_regularization':[1.,10.,30.]},
           'forest':{'n_estimators':[60,100],'max_depth':[4,7,10],'min_samples_leaf':[10,30,60],'max_features':[.6,1.],'max_samples':[.3,.6,None]},
           'linear':{'alpha':[.0001,.001,.01,.1]},
           'extra':{'n_estimators':[100,200],'max_depth':[6,8,12],'min_samples_leaf':[10,30,60],'max_features':[.6,1.]},
           'logit':{'C':[.03,.1,.3,1.,3.]}}
    params=dict(parent or {k:rng.choice(v) for k,v in grids[family].items()})
    for key,values in grids[family].items():params.setdefault(key,rng.choice(values))  # parents from older grids
    for key in rng.sample(list(grids[family]),min(2,len(grids[family]))):params[key]=rng.choice(grids[family][key])
    return params

def split_rows(rows):
    markets=sorted({r['start_s'] for r in rows})
    if len(markets)<60:raise ValueError('至少等待 60 个有标签的市场窗口')
    a=markets[int(len(markets)*.60)];b=markets[int(len(markets)*.80)]
    train=[r for r in rows if r['start_s']<a-600]
    val=[r for r in rows if a<=r['start_s']<b-600]
    audit=[r for r in rows if r['start_s']>=b]
    if min(len(train),len(val),len(audit))<20:raise ValueError('时间隔离后样本不足')
    assert not {r['slug'] for r in train}&{r['slug'] for r in val}
    assert max(r['ready_ms'] for r in train)<min(r['decision_ms'] for r in val)
    return train,val,audit

def metric(y,p,classification):
    import numpy as np
    errors=(y-p)**2
    chunks=[float(x.mean()) for x in np.array_split(errors,3) if len(x)]
    result={'mse':float(errors.mean()),'score':float(np.mean(chunks)+.1*np.std(chunks)),'n':len(y)}
    if classification:
        q=np.clip(p,1e-4,1-1e-4)
        result['accuracy']=float(((p>=.5)==y).mean());result['log_loss']=float(-np.mean(y*np.log(q)+(1-y)*np.log(1-q)))
        # Expected calibration error (10 equal-width bins): later model coordination needs trustworthy probabilities.
        bins=np.minimum((p*10).astype(int),9)
        result['ece']=float(sum(abs(p[bins==b].mean()-y[bins==b].mean())*(bins==b).mean() for b in range(10) if (bins==b).any()))
    return result

def build(family,params,seed,classification):
    from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor,RandomForestClassifier,RandomForestRegressor
    from sklearn.linear_model import SGDClassifier,SGDRegressor
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    p=dict(params)
    if family=='hist':return (HistGradientBoostingClassifier if classification else HistGradientBoostingRegressor)(**p,random_state=seed,early_stopping=False)
    if family=='forest':return (RandomForestClassifier if classification else RandomForestRegressor)(**p,random_state=seed,n_jobs=2)
    if family=='extra':
        from sklearn.ensemble import ExtraTreesClassifier,ExtraTreesRegressor
        return (ExtraTreesClassifier if classification else ExtraTreesRegressor)(**p,random_state=seed,n_jobs=2)
    if family=='logit':
        from sklearn.linear_model import LogisticRegression,Ridge
        return make_pipeline(StandardScaler(),LogisticRegression(C=p['C'],max_iter=3000) if classification else Ridge(alpha=1/p['C']))
    return make_pipeline(StandardScaler(),(SGDClassifier(loss='log_loss',max_iter=1000,tol=.0001,random_state=seed,**p) if classification else SGDRegressor(max_iter=1000,tol=.0001,random_state=seed,**p)))

def fit_weighted(model,x,y,w):
    if w is None:return model.fit(x,y)
    if hasattr(model,'steps'):return model.fit(x,y,**{model.steps[-1][0]+'__sample_weight':w})
    return model.fit(x,y,sample_weight=w)

def train_model(spec,rows,X,y,weights,classification):
    """Fit the candidate on time-ordered rows; with calibration, the most recent 20% calibrates a frozen base fit."""
    rows=sorted(rows,key=lambda r:(r['start_s'],r['decision_ms']))
    method=(spec.get('data_params') or {}).get('calibration','none') if classification else 'none'
    model=build(spec['family'],spec['params'],spec['seed'],classification)
    if method=='none':return fit_weighted(model,X(rows),y(rows),weights(rows))
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.frozen import FrozenEstimator
    k=int(len(rows)*.8);base,held=rows[:k],rows[k:]
    fit_weighted(model,X(base),y(base),weights(base))
    if len(set(y(held)))<2:return model
    return CalibratedClassifierCV(FrozenEstimator(model),method=method).fit(X(held),y(held))

def fit_job(spec_path):
    # Imports stay in the isolated candidate process; Stop can terminate it.
    import joblib
    import numpy as np
    from threadpoolctl import threadpool_limits
    from poly5.common import atomic_json
    spec=read(spec_path)
    if digest(spec['snapshot'])!=spec['snapshot_sha256']:raise ValueError('训练数据快照哈希不匹配')
    task=spec['task'];classification=TASKS[task][2]=='classification';features=spec.get('features') or TASKS[task][1]
    rows=read(spec['snapshot'])
    if task=='dynamic':
        from .dynamic_fit import fit_dynamic_job
        fit_dynamic_job(spec,rows)
        return
    X=lambda part:np.array([vector(r['x'],features) for r in part],dtype=float)
    y=lambda part:np.array([r['y'] for r in part],dtype=float)
    # Proxy backfill (official outcomes, Binance 1s prices) is training-only; its weight is an evolved data parameter.
    weight=float((spec.get('data_params') or {}).get('proxy_weight',0))
    proxy=proxy_rows(spec.get('proxy') or [],task,{r['slug'] for r in rows}) if weight>0 else []
    weights=lambda part:np.array([weight if r.get('source')=='proxy' else 1. for r in part]) if proxy else None
    if spec.get('refit_of'):
        # Deployment refit: the selected configuration is retrained on every labelled row, so the most
        # recent markets (always in the audit tail during selection) are learned from. Selection metrics
        # stay those of the held-out evaluation; forward scoring only uses samples after trained_through_ms.
        everything=rows+proxy;xa,ya=X(everything),y(everything)
        if not (np.isfinite(xa).all() and np.isfinite(ya).all()):raise ValueError('训练数据含非有限值')
        with threadpool_limits(limits=2):model=train_model(spec,everything,X,y,weights,classification)
        m={**spec['metrics'],'refit_rows':len(rows),'refit_proxy_rows':len(proxy),'trained_through_ms':max(r['ready_ms'] for r in everything),'forward_n':0}
        path=Path(spec['model_path']);path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp');joblib.dump({'model':model,'features':features,'task':task},temp,compress=3);os.replace(temp,path)
        atomic_json(Path(spec['result']),{**spec,'metrics':m,'model_sha256':digest(path),'status':'complete','completed_ms':int(time.time()*1000)})
        return
    split=spec.get('split') or {}
    if split.get('mode')=='replay':
        # Historical walk-forward: validation is one never-judged history day; training only sees markets that
        # settled at least 10 minutes before it. Later real rows are reported as audit, never used for selection.
        start,end=split['val_start_s'],split['val_end_s'];pool=rows+proxy
        train=[r for r in pool if r['start_s']+300<start-600];val=[r for r in pool if start<=r['start_s']<end]
        audit=[r for r in rows if r['start_s']>=end]
        if len(train)<200 or len(val)<100:raise ValueError('历史快进窗口样本不足')
    elif split.get('mode')=='exam':
        # Final exam after the second pass: validate only on real (live) markets from val_start on, which no selection
        # has used; train on everything that settled at least 10 minutes earlier (proxy history never validates).
        start=split['val_start_s'];pool=rows+proxy
        train=[r for r in pool if r['start_s']+300<start-600];val=[r for r in rows if r['start_s']>=start]
        audit=list(val)  # paper P&L shown on the exam rows; display only
        if len(train)<200 or len(val)<100:raise ValueError('终考样本不足')
    else:
        train,val,audit=split_rows(rows)
        cutoff=min(r['start_s'] for r in val)-600
        train=train+[r for r in proxy if r['start_s']+300<cutoff]
    xt,xv,yt,yv=X(train),X(val),y(train),y(val)
    if not all(np.isfinite(a).all() for a in [xt,xv,yt,yv]):raise ValueError('训练数据含非有限值')
    if classification and len(np.unique(yt))!=2:raise ValueError('训练集缺少一种结果')
    with threadpool_limits(limits=2):
        model=train_model(spec,train,X,y,weights,classification)
        pred=model.predict_proba(xv)[:,1] if classification else model.predict(xv)
    if not np.isfinite(pred).all():raise ValueError('候选输出非有限数值')
    m=metric(yv,pred,classification);baseline=metric(yv,np.full(len(yv),yt.mean()),classification)
    # Audit labels are excluded from fitness, mutations, and promotion.
    m.update(split=split.get('mode') or 'time_60_20_20',baseline_score=baseline['score'],train_n=len(train),proxy_train_n=sum(r.get('source')=='proxy' for r in train),audit_n=len(audit),
             audit_used_for_selection=False,trained_through_ms=max(r['ready_ms'] for r in train),
             validation_start_ms=min(r['decision_ms'] for r in val),forward_n=0)
    if task in MARKET_TASKS:
        # Skill vs the market (Brier of the market mid as a probability minus the model's): the qualification bar.
        mv=np.array([market_mid(r['x']) for r in val]);m['market_brier']=float(np.mean((yv-mv)**2));m['skill_vs_market']=m['market_brier']-m['mse']
    # Paper P&L for the user (never used for selection): held-out rows after the training cutoff with executable quotes
    # (historical book reconstructions and live captures); ROI tasks use live entries.
    later=[r for r in audit if (r.get('aux') or {}).get('quotes')] if classification else [r for r in audit if r.get('source')=='live']
    if later:
        pa=model.predict_proba(X(later))[:,1] if classification else model.predict(X(later))
        m['paper_pnl']=(paper_trades([(r['slug'],r['decision_ms'],float(v),r['aux'].get('quotes'),r['y']) for r,v in zip(later,pa)]) if classification
                        else roi_trades([(r['slug'],float(v),r['y'],r['aux'].get('entry_cost',5)) for r,v in zip(later,pa)]))
        m['paper_pnl']['scope']='训练与选优都未见过的样本（历史盘口重建 + 实时），模拟成交，非实盘'
    if not classification:
        active=pred>=.005
        costs=np.array([r['aux'].get('entry_cost',5) for r in val],float)
        m['historical_signal_count']=int(active.sum());m['historical_simulated_net_pnl']=float(np.sum(yv[active]*costs[active]))
        m['pnl_scope']='fixed-horizon historical labels, legacy/recorded fee basis; not trading receipts'
    path=Path(spec['model_path']);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');joblib.dump({'model':model,'features':features,'task':task},temp,compress=3);os.replace(temp,path)
    result={**spec,'metrics':m,'model_sha256':digest(path),'status':'complete','completed_ms':int(time.time()*1000)}
    atomic_json(Path(spec['result']),result)
