"""Rolling walk-forward (test day D = 08-14..08-21, train on markets settled >10 min before D): do order-flow features
and a longshot filter improve market-aware models? Reports skill vs market and paper P&L (edge 3pp over executable
price, one trade per market, $5) with per-market bootstrap CIs, pooled over the 8 test days."""
import sys,math,numpy as np,polars as pl
sys.path.insert(0,'D:/POLYMARKET/Poly5_Trading_Station_v3')
from trainstation.data import REF,derived
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from statistics import NormalDist
f=pl.read_parquet('D:/POLYMARKET/research/market-rows/market-features-flow.parquet')
slog=lambda v:math.copysign(math.log1p(abs(v)),v)
def feats(r,flow):
    x={k:r[k] for k in REF};x['stage_fraction']=r['offset']/300;x.update(derived(x))
    mid=(r['mkt_bid']+r['mkt_ask'])/2;m=min(max(mid,.005),.995);iz=NormalDist().inv_cdf(m)
    v=list(x.values())+[r['mkt_bid'],r['mkt_ask'],mid,r['mkt_ask']-r['mkt_bid'],r['up_total']/r['up_shares'],r['down_total']/r['down_shares'],math.log(m/(1-m)),iz,x['z_spot']-iz]
    if flow:v+=[slog(r['flow_10s']),slog(r['flow_30s']),math.log1p(r['trades_30s']),r['imbalance5'],r['last_trade_gap']]
    return v
def paper(rows,p,minprice):
    taken=set();pnl=[];slugs=[]
    for r,q in sorted(zip(rows,p),key=lambda t:(t[0]['market_slug'],t[0]['offset'])):
        if r['market_slug'] in taken:continue
        for prob,shares,total,win in ((q,r['up_shares'],r['up_total'],r['label_up']==1),(1-q,r['down_shares'],r['down_total'],r['label_up']==0)):
            price=total/shares
            if prob-price>.03 and price>=minprice:taken.add(r['market_slug']);pnl.append(shares*win-total);break
    p_=np.array(pnl);rng=np.random.default_rng(0);bs=[p_[rng.integers(0,len(p_),len(p_))].sum() for _ in range(3000)] if len(p_) else [0]
    return len(p_),p_.sum(),np.percentile(bs,2.5),np.percentile(bs,97.5)
D0=1787443200-86400*4  # 2026-08-14
for group,offsets in (('direction 60/120/210',(60,120,210)),('late 240/270',(240,270))):
    rows=[r for r in f.iter_rows(named=True) if r['offset'] in offsets]
    print(f'\n== {group}')
    for name,mk in (('logit',lambda:make_pipeline(StandardScaler(),LogisticRegression(C=.3,max_iter=4000))),('hist-GB',lambda:HistGradientBoostingClassifier(max_iter=150,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=60,l2_regularization=5,random_state=1))):
        for flow in (False,True):
            test_rows=[];preds=[];sk_m=[];sk_p=[]
            for d in range(8):
                ds=D0+d*86400;tr=[r for r in rows if r['start_s']+300<ds-600];te=[r for r in rows if ds<=r['start_s']<ds+86400]
                if not te:continue
                m=mk().fit(np.array([feats(r,flow) for r in tr]),np.array([r['label_up'] for r in tr]))
                p=m.predict_proba(np.array([feats(r,flow) for r in te]))[:,1];test_rows+=te;preds+=list(p)
            y=np.array([r['label_up'] for r in test_rows]);p=np.array(preds);mid=np.array([(r['mkt_bid']+r['mkt_ask'])/2 for r in test_rows])
            skill=np.mean((y-mid)**2)-np.mean((y-p)**2)
            slugs=np.array([r['market_slug'] for r in test_rows]);u=np.unique(slugs);rng=np.random.default_rng(1)
            idx={s:np.where(slugs==s)[0] for s in u};d_=(y-mid)**2-(y-p)**2;per=np.array([d_[idx[s]].mean() for s in u])
            bs=[per[rng.integers(0,len(per),len(per))].mean() for _ in range(2000)]
            line=f'  {name:7s} {"market+flow" if flow else "market only":12s} skill_vs_market {skill:+.4f} CI[{np.percentile(bs,2.5):+.4f},{np.percentile(bs,97.5):+.4f}]'
            for mp in (0.,.35):
                n,s,lo,hi=paper(test_rows,p,mp);line+=f' | min price {mp:.2f}: {n:4d} trades pnl {s:+7.1f} [{lo:+.0f},{hi:+.0f}]'
            print(line)
