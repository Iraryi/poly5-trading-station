"""Can a longshot specialist find underpriced longshots? Rows: each decision where one side's executable price is
below 0.35 (that side = the longshot). Target: did the longshot win. Features: reference features, market prices,
model-vs-market gap, order flow. Rolling walk-forward over test days 08-14..08-21 (train only on markets settled >10 min
before each day). Buy the longshot ($5, executable price incl. fees) when predicted win probability exceeds its price by
EDGE; one trade per market. Compare with buying every longshot and with the market-implied baseline."""
import sys,math,numpy as np,polars as pl
sys.path.insert(0,'D:/POLYMARKET/Poly5_Trading_Station_v3')
from trainstation.data import REF,derived
from statistics import NormalDist
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
f=pl.read_parquet('D:/POLYMARKET/research/market-rows/market-features-flow.parquet')
slog=lambda v:math.copysign(math.log1p(abs(v)),v)
rows=[]
for r in f.iter_rows(named=True):
    up=r['up_total']/r['up_shares'];down=r['down_total']/r['down_shares']
    if min(up,down)>=.35:continue
    side='Up' if up<down else 'Down';sign=1 if side=='Up' else -1;price=min(up,down)
    shares,total=(r['up_shares'],r['up_total']) if side=='Up' else (r['down_shares'],r['down_total'])
    x={k:r[k] for k in REF};x['stage_fraction']=r['offset']/300;d=derived(x);mid=(r['mkt_bid']+r['mkt_ask'])/2;m=min(max(mid,.005),.995)
    # express everything from the longshot's point of view (sign flips Down to look like Up)
    feats=[sign*x['spot_open_bps'],sign*x['twap_open_bps'],sign*x['spot_twap_bps'],sign*x['return_5s_bps'],sign*x['return_15s_bps'],sign*x['return_60s_bps'],x['vol_60s_bps'],
           x['stage_fraction'],sign*d['z_spot'],sign*d['z_twap'],sign*d['z_momentum_15s'],d['sqrt_remaining_s'],price,math.log(price/(1-price)),
           sign*(d['z_spot']-NormalDist().inv_cdf(m)),r['mkt_ask']-r['mkt_bid'],sign*slog(r['flow_10s']),sign*slog(r['flow_30s']),math.log1p(r['trades_30s']),sign*r['imbalance5'],sign*r['last_trade_gap']]
    rows.append({'slug':r['market_slug'],'start':r['start_s'],'offset':r['offset'],'x':feats,'win':int((r['label_up']==1)==(side=='Up')),'price':price,'shares':shares,'total':total})
print('longshot decision rows',len(rows),'| markets',len({r['slug'] for r in rows}),'| longshot win rate %.3f vs avg price %.3f'%(np.mean([r['win'] for r in rows]),np.mean([r['price'] for r in rows])))
D0=1787443200-86400*4
def trade(test,probs,edge):
    taken=set();pnl=[]
    for r,p in sorted(zip(test,probs),key=lambda t:(t[0]['slug'],t[0]['offset'])):
        if r['slug'] in taken or p-r['price']<=edge:continue
        taken.add(r['slug']);pnl.append(r['shares']*r['win']-r['total'])
    p_=np.array(pnl);rng=np.random.default_rng(0);bs=[p_[rng.integers(0,len(p_),len(p_))].sum() for _ in range(3000)] if len(p_) else [0]
    return f'{len(p_):4d} trades wins {int((p_>0).sum()):3d} pnl {p_.sum():+8.1f} roi {p_.sum()/max(1,5*len(p_)):+.3f} CI[{np.percentile(bs,2.5):+.0f},{np.percentile(bs,97.5):+.0f}]'
models={'logit':lambda:make_pipeline(StandardScaler(),LogisticRegression(C=.3,max_iter=4000)),
        'hist-GB':lambda:HistGradientBoostingClassifier(max_iter=150,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=60,l2_regularization=5,random_state=1)}
test_all=[];preds={k:[] for k in models};preds_cal={k:[] for k in models}
for d in range(8):
    ds=D0+d*86400;tr=[r for r in rows if r['start']+300<ds-600];te=[r for r in rows if ds<=r['start']<ds+86400]
    if not te:continue
    X=np.array([r['x'] for r in tr]);Y=np.array([r['win'] for r in tr]);Xt=np.array([r['x'] for r in te]);test_all+=te
    order=np.argsort([r['start'] for r in tr]);k=int(len(tr)*.8)
    for name,mk in models.items():
        preds[name]+=list(mk().fit(X,Y).predict_proba(Xt)[:,1])
        base=mk().fit(X[order[:k]],Y[order[:k]]);cal=CalibratedClassifierCV(FrozenEstimator(base),method='isotonic').fit(X[order[k:]],Y[order[k:]])
        preds_cal[name]+=list(cal.predict_proba(Xt)[:,1])
y=np.array([r['win'] for r in test_all]);price=np.array([r['price'] for r in test_all])
print(f'test rows {len(test_all)} | Brier: market price {np.mean((y-price)**2):.4f}',' '.join(f'| {n} {np.mean((y-np.array(p))**2):.4f} cal {np.mean((y-np.array(preds_cal[n]))**2):.4f}' for n,p in preds.items()))
print(f'  buy every longshot (once per market):        {trade(test_all,[1.]*len(test_all),-1)}')
for edge in (.03,.05,.10):
    for n in models:
        print(f'  edge {edge:.2f} {n:8s} raw: {trade(test_all,preds[n],edge)} | isotonic: {trade(test_all,preds_cal[n],edge)}')
