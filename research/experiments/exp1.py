"""Offline experiment: compare current judge/model against candidate improvements on the real snapshot."""
import json,sys,math,time
import numpy as np
sys.path.insert(0,'D:/POLYMARKET/Poly5_Trading_Station_v3')
from trainstation.modeling import split_rows,metric
from trainstation.data import TASKS
from scipy.stats import norm
from sklearn.ensemble import RandomForestClassifier,HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import log_loss

snap=sys.argv[1];task=sys.argv[2] if len(sys.argv)>2 else 'late'
rows=[r for r in json.load(open(snap)) ]
train,val,audit=split_rows(rows)
base=TASKS[task][1]
def eng(x):
    rem=max(300*(1-x['stage_fraction']),1.0);sig=max(x['vol_60s_bps'],.5)
    s=sig*math.sqrt(rem)
    return {'z_spot':x['spot_open_bps']/s,'z_twap':x['twap_open_bps']/s,'p_spot':norm.cdf(x['spot_open_bps']/s),
            'sqrt_rem':math.sqrt(rem),'mom_z':x['return_15s_bps']/s}
def X(part,extra):
    return np.array([[r['x'][k] for k in base]+([*eng(r['x']).values()] if extra else []) for r in part],float)
y=lambda p:np.array([r['y'] for r in p],float)
yt,yv,ya=y(train),y(val),y(audit)
print(task,'train',len(train),'val',len(val),'audit',len(audit),'val start',time.strftime('%m-%d',time.gmtime(val[0]['start_s'])),'audit',time.strftime('%m-%d',time.gmtime(audit[0]['start_s'])),time.strftime('%m-%d',time.gmtime(audit[-1]['start_s'])))
names=base+list(eng(train[0]['x']))
res={}
def run(name,model,extra,cols=None):
    xt,xv,xa=X(train,extra),X(val,extra),X(audit,extra)
    if cols is not None:xt,xv,xa=xt[:,cols],xv[:,cols],xa[:,cols]
    model.fit(xt,yt);pv=np.clip(model.predict_proba(xv)[:,1],1e-4,1-1e-4);pa=np.clip(model.predict_proba(xa)[:,1],1e-4,1-1e-4)
    res[name]=(pv,pa)
    mv,ma=metric(yv,pv,True),metric(ya,pa,True)
    print(f"{name:34s} val score {mv['score']:.5f} brier {mv['mse']:.5f} ll {log_loss(yv,pv):.4f} acc {mv['accuracy']:.3f} | audit brier {ma['mse']:.5f} ll {log_loss(ya,pa):.4f} acc {ma['accuracy']:.3f}")
    return model
run('A current-best forest',RandomForestClassifier(n_estimators=100,max_depth=10,min_samples_leaf=10,max_features=1.0,random_state=1,n_jobs=4),False)
run('A2 forest + engineered',RandomForestClassifier(n_estimators=300,max_depth=10,min_samples_leaf=10,max_features=.5,random_state=1,n_jobs=4),True)
zi=names.index('z_spot')
run('B logistic(z_spot only)',LogisticRegression(C=10),True,[zi])
run('B2 logistic(z_spot,z_twap,mom_z)',make_pipeline(StandardScaler(),LogisticRegression(C=1)),True,[zi,names.index('z_twap'),names.index('mom_z')])
run('B3 logistic all+eng',make_pipeline(StandardScaler(),LogisticRegression(C=1,max_iter=2000)),True)
run('C hgb base',HistGradientBoostingClassifier(max_iter=200,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=1,random_state=1),False)
mono=[1 if n in ('spot_open_bps','twap_open_bps','z_spot','z_twap','p_spot') else 0 for n in names]
run('C2 hgb eng + monotonic',HistGradientBoostingClassifier(max_iter=200,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=1,monotonic_cst=mono,random_state=1),True)
run('C3 hgb eng + mono, small',HistGradientBoostingClassifier(max_iter=150,learning_rate=.05,max_leaf_nodes=7,min_samples_leaf=80,l2_regularization=10,monotonic_cst=mono,random_state=1),True)
for a,b in [('B logistic(z_spot only)','C2 hgb eng + monotonic'),('B2 logistic(z_spot,z_twap,mom_z)','C3 hgb eng + mono, small'),('B2 logistic(z_spot,z_twap,mom_z)','A2 forest + engineered')]:
    pv=(res[a][0]+res[b][0])/2;pa=(res[a][1]+res[b][1])/2
    print(f"{'E avg '+a[:2]+'+'+b[:2]:34s} val brier {np.mean((yv-pv)**2):.5f} ll {log_loss(yv,pv):.4f} | audit brier {np.mean((ya-pa)**2):.5f} ll {log_loss(ya,pa):.4f}")
# paired per-market bootstrap: does best-by-val beat current on audit?
def boot(a,b,part=1,yy=ya,n=2000):
    rows_=audit;slugs=np.array([r['slug'] for r in rows_]);u=np.unique(slugs);idx={s:np.where(slugs==s)[0] for s in u}
    da=(yy-res[a][part])**2-(yy-res[b][part])**2;per=np.array([da[idx[s]].mean() for s in u]);rng=np.random.default_rng(0)
    bs=[per[rng.integers(0,len(per),len(per))].mean() for _ in range(n)]
    return per.mean(),np.percentile(bs,[2.5,97.5])
for b in ['B logistic(z_spot only)','C2 hgb eng + monotonic','A2 forest + engineered']:
    m,ci=boot('A current-best forest',b);print(f'audit brier(current) - brier({b}) = {m:+.5f}  95%CI [{ci[0]:+.5f},{ci[1]:+.5f}]')
