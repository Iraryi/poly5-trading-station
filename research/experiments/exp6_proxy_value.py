"""Does the Binance-proxy backfill improve predictions on REAL Chainlink data? Proxy rows only before the test window."""
import sys,json,sqlite3
import numpy as np,polars as pl
sys.path.insert(0,'D:/POLYMARKET/Poly5_Trading_Station_v3')
from trainstation.data import design,vector,REF
from sklearn.ensemble import RandomForestClassifier,HistGradientBoostingClassifier
F=design('late');months=sys.argv[1].split(',') if len(sys.argv)>1 else None
ch=pl.read_parquet('D:/POLYMARKET/research/btc5m-v2-extended/reference-features.parquet').filter((pl.col('offset')>=240)&(pl.col('day')>='2026-08-14')&pl.col('label_up').is_not_null())
bad={'btc-updown-5m-1786911600','btc-updown-5m-1787354400'}
def rowx(r):x={k:float(r[k]) for k in REF};x['stage_fraction']=r['offset']/300;return x
real=[(r['start_s'],r['market_slug'],rowx(r),float(r['label_up'])) for r in ch.iter_rows(named=True) if r['market_slug'] not in bad]
test_start=min(s for s,_,_,_ in real if s>=1787097600)  # 2026-08-19 00:00 UTC
tr=[r for r in real if r[0]+300<test_start-600];te=[r for r in real if r[0]>=test_start]
px=pl.read_parquet('D:/POLYMARKET/research/proxy-backfill/proxy-features-all.parquet').filter(pl.col('offset')>=240)
px=px.with_columns(pl.from_epoch('start_s').dt.strftime('%Y-%m').alias('month'))
if months:px=px.filter(pl.col('month').is_in(months))
if len(sys.argv)>2:px=px.filter(pl.col('start_s')>=int(sys.argv[2]))
slugs={s for _,s,_,_ in real}
proxy=[(r['start_s'],r['market_slug'],rowx(r),float(r['label_up'])) for r in px.iter_rows(named=True) if r['market_slug'] not in slugs and r['start_s']+300<test_start-600]
c=sqlite3.connect('file:D:/POLYMARKET/trainer/training.sqlite?mode=ro',uri=True)
live=[(json.loads(a),float(t),s) for a,t,s in c.execute("select features,target,slug from samples where source='live' and task='late' and target is not null")]
xl=np.array([vector(x,F) for x,_,_ in live]);yl=np.array([y for _,y,_ in live]);ls=np.array([s for *_,s in live])
xte=np.array([vector(x,F) for _,_,x,_ in te]);yte=np.array([y for *_,y in te]);ts=np.array([s for _,s,_,_ in te])
print(f'real train {len(tr)} | proxy rows {len(proxy)} ({px["month"].unique().sort().to_list()}) | test Aug19-21 {len(te)} | Oct live {len(live)} ({len(set(ls))} markets)')
def boot(d,groups,n=2000):
    u=np.unique(groups);per=np.array([d[groups==g].mean() for g in u]);rng=np.random.default_rng(0)
    b=[per[rng.integers(0,len(per),len(per))].mean() for _ in range(n)];return per.mean(),np.percentile(b,[2.5,97.5])
models={'forest':lambda s:RandomForestClassifier(n_estimators=100,max_depth=10,min_samples_leaf=10,max_features=1.0,random_state=s,n_jobs=8),
        'hist':lambda s:HistGradientBoostingClassifier(max_iter=200,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=40,random_state=s)}
for fam,mk in models.items():
    ref=None
    for w in (0.,.25,.5,1.):
        rows=tr+(proxy if w else []);x=np.array([vector(r[2],F) for r in rows]);y=np.array([r[3] for r in rows]);sw=np.array([1.]*len(tr)+[w]*(len(rows)-len(tr)))
        pt,pq=[],[]
        for seed in (1,2,3):
            m=mk(seed).fit(x,y,sample_weight=sw);pt.append(m.predict_proba(xte)[:,1]);pq.append(m.predict_proba(xl)[:,1])
        p,q=np.mean(pt,0),np.mean(pq,0);et,el=(yte-p)**2,(yl-q)**2
        if ref is None:ref=(et,el);extra=''
        else:
            a,ca=boot(ref[0]-et,ts);b,cb=boot(ref[1]-el,ls);extra=f'  gain Aug19-21 {a:+.5f} [{ca[0]:+.5f},{ca[1]:+.5f}]  gain live {b:+.5f} [{cb[0]:+.5f},{cb[1]:+.5f}]'
        print(f'{fam:6s} proxy_weight {w:<4} train {len(rows):6d} | Aug19-21 brier {et.mean():.5f} | Oct live brier {el.mean():.5f}{extra}')
