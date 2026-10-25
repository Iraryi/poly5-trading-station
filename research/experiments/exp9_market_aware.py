"""Does a market-aware model beat the market's own price? Train 08-08..08-17, test 08-18..08-21 (time split, 10-min embargo).
Compares: market mid alone, reference-only model, market-aware model (+calibration). Paper P&L with per-market bootstrap CI."""
import sys,math,numpy as np,polars as pl
sys.path.insert(0,'D:/POLYMARKET/Poly5_Trading_Station_v3')
from trainstation.data import REF,derived
from sklearn.ensemble import RandomForestClassifier,HistGradientBoostingClassifier,ExtraTreesClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from scipy.stats import norm
f=pl.read_parquet('D:/POLYMARKET/research/market-rows/market-features.parquet')
cut=1787443200-86400*3  # 2026-08-18 00:00 UTC
def feats(r,market):
    x={k:r[k] for k in REF};x['stage_fraction']=r['offset']/300;x.update(derived(x));v=list(x.values())
    if market:
        mid=(r['mkt_bid']+r['mkt_ask'])/2;iz=norm.ppf(min(max(mid,.01),.99))
        v+=[r['mkt_bid'],r['mkt_ask'],mid,r['mkt_ask']-r['mkt_bid'],r['up_total']/r['up_shares'],r['down_total']/r['down_shares'],math.log(mid/(1-mid)),iz,x['z_spot']-iz]
    return v
def paper(rows,p,edge=.03):
    taken={};pnl=[]
    for r,q in sorted(zip(rows,p),key=lambda t:(t[0]['market_slug'],t[0]['offset'])):
        if r['market_slug'] in taken:continue
        for side,prob,shares,total,win in (('Up',q,r['up_shares'],r['up_total'],r['label_up']==1),('Down',1-q,r['down_shares'],r['down_total'],r['label_up']==0)):
            if prob-total/shares>edge:taken[r['market_slug']]=1;pnl.append(shares*win-total);break
    pnl=np.array(pnl);rng=np.random.default_rng(0);b=[pnl[rng.integers(0,len(pnl),len(pnl))].sum() for _ in range(4000)] if len(pnl) else [0]
    return f'{len(pnl):4d} trades wins {int((pnl>0).sum()):4d} pnl {pnl.sum():+8.1f} CI[{np.percentile(b,2.5):+.1f},{np.percentile(b,97.5):+.1f}] roi {pnl.sum()/max(1,5*len(pnl)):+.3f}'
for group,offsets in (('direction 60/120/210',(60,120,210)),('late 240/270',(240,270))):
    rows=[r for r in f.iter_rows(named=True) if r['offset'] in offsets]
    tr=[r for r in rows if r['start_s']+300<cut-600];te=[r for r in rows if r['start_s']>=cut]
    y=np.array([r['label_up'] for r in tr]);yt=np.array([r['label_up'] for r in te])
    mid=np.array([(r['mkt_bid']+r['mkt_ask'])/2 for r in te]);mb=np.mean((yt-mid)**2)
    print(f'\n== {group}: train {len(tr)} rows, test {len(te)} rows ({len({r["market_slug"] for r in te})} markets) | market-mid Brier {mb:.4f}')
    print(f'  {"market mid as probability":38s} skill_vs_market +0.0000 | paper {paper(te,mid)}')
    for name,mk,market,cal in [('reference-only forest',lambda:RandomForestClassifier(300,max_depth=7,min_samples_leaf=30,n_jobs=8,random_state=1),False,None),
                               ('market-aware forest',lambda:RandomForestClassifier(300,max_depth=7,min_samples_leaf=30,n_jobs=8,random_state=1),True,None),
                               ('market-aware forest + isotonic',lambda:RandomForestClassifier(300,max_depth=7,min_samples_leaf=30,n_jobs=8,random_state=1),True,'isotonic'),
                               ('market-aware extra-trees + sigmoid',lambda:ExtraTreesClassifier(300,max_depth=8,min_samples_leaf=30,n_jobs=8,random_state=1),True,'sigmoid'),
                               ('market-aware hist-GB',lambda:HistGradientBoostingClassifier(max_iter=200,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=60,random_state=1),True,None),
                               ('market-aware logistic',lambda:LogisticRegression(C=.3,max_iter=3000),True,None)]:
        X=np.array([feats(r,market) for r in tr]);Xt=np.array([feats(r,market) for r in te])
        if cal:  # time-ordered: fit on first 80% of train, calibrate on the last 20%
            k=int(len(X)*.8);order=np.argsort([r['start_s'] for r in tr]);a,b=order[:k],order[k:]
            m=CalibratedClassifierCV(FrozenEstimator(mk().fit(X[a],y[a])),method=cal).fit(X[b],y[b])
        else:m=mk().fit(X,y)
        p=np.clip(m.predict_proba(Xt)[:,1],1e-4,1-1e-4);br=np.mean((yt-p)**2)
        print(f'  {name:38s} skill_vs_market {mb-br:+.4f} | paper {paper(te,p)}')
