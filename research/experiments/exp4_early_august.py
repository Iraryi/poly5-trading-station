"""Do the extra Aug 8-13 archive days help or hurt? Train with/without, test on Aug 19-21 archive and October live."""
import sys,math,sqlite3,json
import numpy as np,polars as pl
sys.path.insert(0,'D:/POLYMARKET/Poly5_Trading_Station_v3')
from trainstation.data import design,vector,REF
from sklearn.ensemble import RandomForestClassifier
f=pl.read_parquet('D:/POLYMARKET/research/btc5m-v2-extended/reference-features.parquet').filter(pl.col('offset')>=240).filter(pl.col('label_up').is_not_null())
bad={'btc-updown-5m-1786911600','btc-updown-5m-1787354400'}
rows=[]
for r in f.iter_rows(named=True):
    if r['market_slug'] in bad:continue
    x={k:float(r[k]) for k in REF};x['stage_fraction']=r['offset']/300
    rows.append((r['day'],r['market_slug'],x,float(r['label_up'])))
c=sqlite3.connect('file:D:/POLYMARKET/trainer/training.sqlite?mode=ro',uri=True)
live=[(json.loads(a),float(t),s) for a,t,s in c.execute("select features,target,slug from samples where source='live' and task='late' and target is not null")]
feats=design('late')
def XY(rs):return np.array([vector(x,feats) for _,_,x,_ in rs]),np.array([y for *_,y in rs])
test=[r for r in rows if r[0]>='2026-08-19'];xt,yt=XY(test)
xl=np.array([vector(x,feats) for x,_,_ in live]);yl=np.array([y for _,y,_ in live])
print('test archive',len(test),'live',len(live),'live markets',len({s for *_,s in live}))
sets={'Aug14-18 only (current)':[r for r in rows if '2026-08-14'<=r[0]<'2026-08-19'],
      'Aug08-18 (+early days)':[r for r in rows if r[0]<'2026-08-19'],
      'Aug08-18, early days weight .5':[r for r in rows if r[0]<'2026-08-19']}
for name,tr in sets.items():
    x,y=XY(tr);w=np.array([.5 if d<'2026-08-14' else 1. for d,*_ in tr]) if 'weight' in name else None
    ps,pl_=[],[]
    for seed in (1,2,3):
        m=RandomForestClassifier(n_estimators=100,max_depth=10,min_samples_leaf=10,max_features=1.0,random_state=seed,n_jobs=4).fit(x,y,sample_weight=w)
        ps.append(m.predict_proba(xt)[:,1]);pl_.append(m.predict_proba(xl)[:,1])
    p=np.mean(ps,0);q=np.mean(pl_,0)
    print(f'{name:32s} train n={len(tr):5d} | Aug19-21 brier {np.mean((yt-p)**2):.5f} | Oct live brier {np.mean((yl-q)**2):.5f} acc {np.mean((q>=.5)==yl):.3f}')
