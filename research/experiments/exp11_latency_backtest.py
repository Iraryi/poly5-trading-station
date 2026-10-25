"""Latency strategy backtest (paper): when the Binance 1s close moves more than X bps in the direction of a side,
buy $5 of that side on the reconstructed PMData book 0.3s or 1.0s later (depth walked, official fee formula), hold to
settlement. Window: the dense book grids 56-151s after open (ROI book extracts), 2026-08-08..21. One trade per market."""
import glob,io,zipfile,sys
from decimal import Decimal as D
import numpy as np,polars as pl
sys.path.insert(0,'D:/POLYMARKET/Poly5_Trading_Station_v3/scripts')
from research_execution import buy
frames=[]
for z in sorted(glob.glob('D:/POLYMARKET/research/proxy-backfill/raw/binance-1s/BTCUSDT-1s-2026-08-*.zip')):
    with zipfile.ZipFile(z) as f:
        d=pl.read_csv(io.BytesIO(f.read(f.namelist()[0])),has_header=False,columns=[0,4]);d.columns=['t','c'];frames.append(d)
bn=pl.concat(frames).with_columns((pl.col('t')//1_000_000+1).alias('s'))
close=dict(zip(bn['s'].to_list(),bn['c'].to_list()))
labels=dict(pl.read_parquet('D:/POLYMARKET/research/btc5m-v2-extended/reference-features.parquet').filter(pl.col('label_up').is_not_null()).select('market_slug','label_up').unique('market_slug').iter_rows())
books=pl.concat([pl.read_parquet(p) for r in ('btc5m-v1','btc5m-v2-extended') for p in glob.glob(f'D:/POLYMARKET/research/{r}/books/*.parquet')],how='vertical_relaxed')
books=books.filter(pl.col('reason')!='reconstructed_top_mismatch')
state={(r['market_slug'],r['at_us']):r for r in books.iter_rows(named=True)}
def side(st,name):
    if st is None or not st['ask_prices'] or not st['bid_prices']:return None
    if name=='Up':return {'valid':True,'ask_prices':list(st['ask_prices']),'ask_sizes':list(st['ask_sizes'])}
    return {'valid':True,'ask_prices':[round(1-p,6) for p in st['bid_prices']],'ask_sizes':list(st['bid_sizes'])}
seconds={}
for slug,at in state:
    if at%1_000_000==0:seconds.setdefault(slug,set()).add(at//1_000_000)
print('markets with dense books',len(seconds))
for delay in (.3,1.):
    for thr in (2,3,5,8):
        pnl=[];prices=[]
        for slug,secs in seconds.items():
            if slug not in labels:continue
            for s in sorted(secs):
                a,b=close.get(s),close.get(s-1)
                if a is None or b is None:continue
                r=np.log(a/b)*1e4
                if abs(r)<thr:continue
                name='Up' if r>0 else 'Down';fill=buy(side(state.get((slug,round((s+delay)*1e6))),name),D('.99'))
                if not fill:continue
                win=(name=='Up')==(labels[slug]==1);pnl.append(float(fill['shares']*win-fill['total']));prices.append(float(fill['total']/fill['shares']));break
        p=np.array(pnl);rng=np.random.default_rng(0);bs=[p[rng.integers(0,len(p),len(p))].sum() for _ in range(2000)] if len(p) else [0]
        print(f'delay {delay:.1f}s threshold {thr} bps: trades {len(p):4d} wins {int((p>0).sum()):4d} avg price {np.mean(prices) if prices else 0:.3f} pnl {p.sum():+8.1f} roi {p.sum()/max(1,5*len(p)):+.3f} CI[{np.percentile(bs,2.5):+.1f},{np.percentile(bs,97.5):+.1f}]')
