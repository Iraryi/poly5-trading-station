"""exp6 extended with the Gamma-labelled proxy (2026-09-09..10-21). Does the new, more recent proxy help on October live?
A: test = Oct live Chainlink samples from training.sqlite; train = all real Chainlink (Aug 14-21) + proxy ending before live start-600s.
B (secondary, proxy-on-proxy): test = new proxy 10-14 00:00 .. live start; train = real + proxy ending before test start-600s.
Grouped (per-market) bootstrap, 2000 draws, 95% CI; gain = baseline brier - candidate brier (positive = candidate better)."""
import sys,json,sqlite3
import numpy as np,polars as pl
sys.path.insert(0,'D:/POLYMARKET/Poly5_Trading_Station_v3')
from trainstation.data import design,vector,REF
from sklearn.ensemble import RandomForestClassifier,HistGradientBoostingClassifier
F=design('late');P='D:/POLYMARKET/research/proxy-backfill/export/'
def rowx(r):x={k:float(r[k]) for k in REF};x['stage_fraction']=r['offset']/300;return x
ch=pl.read_parquet('D:/POLYMARKET/research/btc5m-v2-extended/reference-features.parquet').filter((pl.col('offset')>=240)&(pl.col('day')>='2026-08-14')&pl.col('label_up').is_not_null())
bad={'btc-updown-5m-1786911600','btc-updown-5m-1787354400'}
real=[(r['start_s'],r['market_slug'],vector(rowx(r),F),float(r['label_up'])) for r in ch.iter_rows(named=True) if r['market_slug'] not in bad]
def load(name):return [(r['start_s'],r['market_slug'],vector(rowx(r),F),float(r['label_up'])) for r in pl.read_parquet(P+name).filter(pl.col('offset')>=240).iter_rows(named=True)]
old=load('binance1s_twap_regime_20260808_20260908.parquet');new=load('binance1s_twap_regime_20260909_20261021.parquet')
c=sqlite3.connect('file:D:/POLYMARKET/trainer/training.sqlite?mode=ro',uri=True)
live=[(s,json.loads(a),float(t),g) for s,a,t,g in c.execute("select start_s,features,target,slug from samples where source='live' and task='late' and target is not null")]
live_start=min(r[0] for r in live)
xl=np.array([vector(r[1],F) for r in live]);yl=np.array([r[2] for r in live]);gl=np.array([r[3] for r in live])
models={'forest':lambda s:RandomForestClassifier(n_estimators=100,max_depth=10,min_samples_leaf=10,max_features=1.0,random_state=s,n_jobs=8),
        'hist':lambda s:HistGradientBoostingClassifier(max_iter=200,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=40,random_state=s)}
def boot(d,groups,n=2000):
    u,inv=np.unique(groups,return_inverse=True);per=np.bincount(inv,d)/np.bincount(inv);rng=np.random.default_rng(0)
    b=per[rng.integers(0,len(per),(n,len(per)))].mean(1);return per.mean(),np.percentile(b,[2.5,97.5])
def predict(fam,proxy,w,xte,cut):
    proxy=[r for r in proxy if r[0]+300<cut-600];rows=real+proxy
    x=np.array([r[2] for r in rows]);y=np.array([r[3] for r in rows]);sw=np.r_[np.ones(len(real)),np.full(len(proxy),w)]
    return np.mean([models[fam](s).fit(x,y,sample_weight=sw).predict_proba(xte)[:,1] for s in (1,2,3)],0),len(proxy)
def run(title,xte,yte,groups,cut,extra_test_filter=None):
    print(f'\n== {title}: {len(yte)} rows, {len(set(groups))} markets ==')
    sets={'old':old,'new':new,'old+new':old+new}
    for fam in models:
        base,_=predict(fam,[],0,xte,cut);eb=(yte-base)**2;res={}
        print(f'{fam:6s} real only                     brier {eb.mean():.5f}')
        for name,proxy in sets.items():
            for w in (.25,.5,1.):
                p,n=predict(fam,proxy,w,xte,cut);e=(yte-p)**2;res[name,w]=e;g,ci=boot(eb-e,groups)
                vs=''
                if name=='old+new':g2,ci2=boot(res['old',w]-e,groups);vs=f'  | vs old {g2:+.5f} [{ci2[0]:+.5f},{ci2[1]:+.5f}]'
                print(f'{fam:6s} {name:7s} w={w:<4} proxy {n:6d} brier {e.mean():.5f}  vs real {g:+.5f} [{ci[0]:+.5f},{ci[1]:+.5f}]{vs}')
print(f'real {len(real)} | old proxy {len(old)} | new proxy {len(new)} | live {len(live)} rows from {live_start}')
run('A. October live (real Chainlink)',xl,yl,gl,live_start)
test_start=1792022400  # 2026-10-14 00:00 UTC
te=[r for r in new if test_start<=r[0] and r[0]+300<live_start-600]
run('B. proxy holdout 10-14..live start (Binance features, official labels)',np.array([r[2] for r in te]),np.array([r[3] for r in te]),np.array([r[1] for r in te]),test_start)
