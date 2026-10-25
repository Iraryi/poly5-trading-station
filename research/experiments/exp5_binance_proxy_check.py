"""Is Binance 1s a usable proxy for Chainlink? (1) label reproduction, (2) feature agreement, (3) train-on-Binance / test-on-Chainlink."""
import sys,math,sqlite3,json,zipfile,glob
import numpy as np,polars as pl
sys.path.insert(0,'D:/POLYMARKET/Poly5_Trading_Station_v3')
from trainstation.data import design,vector,REF
from sklearn.ensemble import RandomForestClassifier
# Binance 1s klines: open_time(us), close; a candle is known only after its close (open_time+1s).
frames=[]
for z in sorted(glob.glob('D:/POLYMARKET/research/binance-proxy-check/raw/*.zip')):
    with zipfile.ZipFile(z) as f:frames.append(pl.read_csv(f.read(f.namelist()[0]),has_header=False,new_columns=['t','o','h','l','c','v','ct','q','n','tb','tq','i']).select('t','c'))
b=pl.concat(frames).sort('t');known=(b['t'].to_numpy()//1_000_000)+1;close=b['c'].to_numpy()  # seconds when each close becomes known
def series_at(ts):  # last 61 closes known at or before ts (seconds)
    i=np.searchsorted(known,ts,side='right')-1
    if i<60 or ts-known[i]>5:return None
    return close[i-60:i+1]
def twap(ts):
    s=series_at(ts);return None if s is None else float(np.mean(s[1:]))
def feats(start,offset):
    t=start+offset;s=series_at(t);op=twap(start);tw=twap(t)
    if s is None or op is None or tw is None:return None
    sp=s[-1];x={'spot_open_bps':(sp/op-1)*1e4,'twap_open_bps':(tw/op-1)*1e4,'spot_twap_bps':(sp/tw-1)*1e4,
       'return_5s_bps':math.log(s[-1]/s[-6])*1e4,'return_15s_bps':math.log(s[-1]/s[-16])*1e4,'return_60s_bps':math.log(s[-1]/s[0])*1e4,
       'vol_60s_bps':float(np.std(np.diff(np.log(s)),ddof=1)*1e4),'stage_fraction':offset/300}
    return x
ch=pl.read_parquet('D:/POLYMARKET/research/btc5m-v2-extended/reference-features.parquet').filter((pl.col('offset')>=240)&(pl.col('day')>='2026-08-14')&pl.col('label_up').is_not_null())
bad={'btc-updown-5m-1786911600','btc-updown-5m-1787354400'}
pairs=[];agree=[];seen=set()
for r in ch.iter_rows(named=True):
    if r['market_slug'] in bad:continue
    start=r['start_s'];y=float(r['label_up'])
    if start not in seen:
        seen.add(start);o,e=twap(start),twap(start+300)
        if o is not None and e is not None:agree.append(((e>=o)==(y==1),abs(e/o-1)*1e4))
    bx=feats(start,r['offset'])
    if bx is None:continue
    cx={k:float(r[k]) for k in REF};cx['stage_fraction']=r['offset']/300
    pairs.append((r['day'],cx,bx,y))
a=np.array(agree);print(f'(1) Binance 60s-TWAP close>=open reproduces official result: {a[:,0].mean():.3%} of {len(a)} markets')
for lo,hi in [(0,1),(1,3),(3,10),(10,1e9)]:
    m=(a[:,1]>=lo)&(a[:,1]<hi);print(f'    |move| {lo}-{hi} bps: n={m.sum():4d} agreement {a[m,0].mean():.3%}')
for k in ['spot_open_bps','twap_open_bps','return_15s_bps','vol_60s_bps']:
    c=np.array([p[1][k] for p in pairs]);bb=np.array([p[2][k] for p in pairs])
    print(f'(2) {k:15s} corr {np.corrcoef(c,bb)[0,1]:.4f}  median |diff| {np.median(np.abs(c-bb)):.3f}  chainlink median |x| {np.median(np.abs(c)):.3f}')
F=design('late')
tr=[p for p in pairs if p[0]<'2026-08-19'];te=[p for p in pairs if p[0]>='2026-08-19']
c=sqlite3.connect('file:D:/POLYMARKET/trainer/training.sqlite?mode=ro',uri=True)
live=[(json.loads(a_),float(t)) for a_,t in c.execute("select features,target from samples where source='live' and task='late' and target is not null")]
xl=np.array([vector(x,F) for x,_ in live]);yl=np.array([y for _,y in live]);xte=np.array([vector(p[1],F) for p in te]);yte=np.array([p[3] for p in te])
for name,idx in [('train on Chainlink features',1),('train on Binance features',2)]:
    x=np.array([vector(p[idx],F) for p in tr]);y=np.array([p[3] for p in tr]);pt,pl_=[],[]
    for seed in (1,2,3):
        m=RandomForestClassifier(n_estimators=100,max_depth=10,min_samples_leaf=10,max_features=1.0,random_state=seed,n_jobs=4).fit(x,y)
        pt.append(m.predict_proba(xte)[:,1]);pl_.append(m.predict_proba(xl)[:,1])
    p,q=np.mean(pt,0),np.mean(pl_,0)
    print(f'(3) {name:28s} n={len(tr)} | test on Chainlink Aug19-21 brier {np.mean((yte-p)**2):.5f} | Oct live brier {np.mean((yl-q)**2):.5f}')
