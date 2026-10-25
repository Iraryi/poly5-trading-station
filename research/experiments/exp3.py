"""Robustness: feature/param variants on two snapshots, and value of fitting on more recent data."""
import json,sys,math
import numpy as np
sys.path.insert(0,'D:/POLYMARKET/Poly5_Trading_Station_v3')
from trainstation.modeling import split_rows
from trainstation.data import TASKS
from scipy.stats import norm
from sklearn.ensemble import RandomForestClassifier,HistGradientBoostingClassifier
from sklearn.metrics import log_loss
base=TASKS['late'][1]
def eng(x):
    rem=max(300*(1-x['stage_fraction']),1.0);s=max(x['vol_60s_bps'],.5)*math.sqrt(rem)
    return [x['spot_open_bps']/s,x['twap_open_bps']/s,norm.cdf(x['spot_open_bps']/s),math.sqrt(rem),x['return_15s_bps']/s]
X=lambda part,e:np.array([[r['x'][k] for k in base]+(eng(r['x']) if e else []) for r in part],float)
Y=lambda part:np.array([r['y'] for r in part],float)
def boot(per,n=2000):
    rng=np.random.default_rng(0);bs=[per[rng.integers(0,len(per),len(per))].mean() for _ in range(n)];return np.percentile(bs,[2.5,97.5])
for snap in sys.argv[1:]:
    rows=json.load(open(snap));train,val,audit=split_rows(rows);ya=Y(audit)
    slugs=np.array([r['slug'] for r in audit]);u=np.unique(slugs);idx=[np.where(slugs==s)[0] for s in u]
    print('==',snap[-20:],'train',len(train),'val',len(val),'audit',len(audit))
    out={}
    variants={'F100 d10 mf1 base':(lambda s:RandomForestClassifier(n_estimators=100,max_depth=10,min_samples_leaf=10,max_features=1.0,random_state=s,n_jobs=4),False),
              'F300 d10 mf.5 base':(lambda s:RandomForestClassifier(n_estimators=300,max_depth=10,min_samples_leaf=10,max_features=.5,random_state=s,n_jobs=4),False),
              'F100 d10 mf1 eng':(lambda s:RandomForestClassifier(n_estimators=100,max_depth=10,min_samples_leaf=10,max_features=1.0,random_state=s,n_jobs=4),True),
              'F300 d10 mf.5 eng':(lambda s:RandomForestClassifier(n_estimators=300,max_depth=10,min_samples_leaf=10,max_features=.5,random_state=s,n_jobs=4),True)}
    for name,(mk,e) in variants.items():
        ps=[];lls=[]
        for seed in (1,2,3):
            m=mk(seed).fit(X(train,e),Y(train));p=np.clip(m.predict_proba(X(audit,e))[:,1],1e-4,1-1e-4);ps.append(p)
        p=np.mean(ps,0);out[name]=p
        print(f'  {name:22s} audit brier {np.mean((ya-p)**2):.5f} logloss {log_loss(ya,p):.4f} (3 seeds avg)')
    for name in list(variants)[1:]:
        d=(ya-out['F100 d10 mf1 base'])**2-(ya-out[name])**2;per=np.array([d[i].mean() for i in idx]);ci=boot(per)
        print(f'  gain vs current [{name}]: {per.mean():+.5f} CI[{ci[0]:+.5f},{ci[1]:+.5f}]')
    # Value of recent data: same model trained on train vs train+val, scored on audit.
    for e in (False,True):
        m1=RandomForestClassifier(n_estimators=300,max_depth=10,min_samples_leaf=10,max_features=.5,random_state=1,n_jobs=4)
        p1=m1.fit(X(train,e),Y(train)).predict_proba(X(audit,e))[:,1]
        m2=RandomForestClassifier(n_estimators=300,max_depth=10,min_samples_leaf=10,max_features=.5,random_state=1,n_jobs=4)
        p2=m2.fit(X(train+val,e),Y(train+val)).predict_proba(X(audit,e))[:,1]
        d=(ya-p1)**2-(ya-p2)**2;per=np.array([d[i].mean() for i in idx]);ci=boot(per)
        print(f'  recent-data refit (eng={e}): audit brier train-only {np.mean((ya-p1)**2):.5f} -> train+val {np.mean((ya-p2)**2):.5f}  gain {per.mean():+.5f} CI[{ci[0]:+.5f},{ci[1]:+.5f}]')
    live=[r for r in audit if r['source']=='live'];print('  live samples in audit:',len(live),'of',len(audit),'| live in train/val:',sum(r['source']=='live' for r in train+val))
