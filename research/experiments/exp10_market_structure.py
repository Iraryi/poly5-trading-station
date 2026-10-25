"""Market-structure study on PMData 2026-08-08..21 (TWAP-settlement regime from 08-08).
A) Calibration of the executable price: by checkpoint and price band, realised win rate vs average entry price (fees in).
B) Lead-lag: does Binance BTC lead the Polymarket Up mid? Correlation of 1s mid changes with Binance returns at lags."""
import glob,io,zipfile
import numpy as np,polars as pl
rows=pl.read_parquet('D:/POLYMARKET/research/market-rows/market-features.parquet')
print('== A) buying a side at its executable average price (incl. fees), $5 per trade; one row = one decision point')
out=[]
for r in rows.iter_rows(named=True):
    for side,price,win in (('Up',r['up_total']/r['up_shares'],r['label_up']==1),('Down',r['down_total']/r['down_shares'],r['label_up']==0)):
        out.append((r['offset'],price,win,r['market_slug'],r['start_s']))
a=pl.DataFrame(out,schema=['offset','price','win','slug','start'],orient='row')
bands=[0,.1,.2,.35,.5,.65,.8,.9,.95,1.01]
a=a.with_columns(pl.col('price').cut(bands[1:-1],labels=[f'{bands[i]:.2f}-{bands[i+1]:.2f}' for i in range(len(bands)-1)]).alias('band'))
def ci(g):
    w=g['win'].to_numpy().astype(float);p=g['price'].to_numpy();ev=w/p-1  # return per $ spent
    rng=np.random.default_rng(0);slugs=g['slug'].to_numpy();u=np.unique(slugs)
    per={s:ev[slugs==s] for s in u};b=[np.mean(np.concatenate([per[s] for s in rng.choice(u,len(u))])) for _ in range(400)]
    return ev.mean(),np.percentile(b,2.5),np.percentile(b,97.5)
tab=[]
for (off,band),g in a.group_by(['offset','band']):
    if g.height<80:continue
    m,lo,hi=ci(g);tab.append((off,band,g.height,g['price'].mean(),g['win'].mean(),m,lo,hi))
for t in sorted(tab,key=lambda t:(t[0],t[1])):
    flag='  <== significant' if t[6]>0 or t[7]<0 else ''
    print(f'  offset {t[0]:3d} price {t[1]:9s} n={t[2]:5d} avg price {t[3]:.3f} win rate {t[4]:.3f} | return/$ {t[5]:+.3f} CI[{t[6]:+.3f},{t[7]:+.3f}]{flag}')
# B) lead-lag on the ROI book grids (dense states 55-152s after open) vs Binance 1s closes
frames=[]
for z in sorted(glob.glob('D:/POLYMARKET/research/proxy-backfill/raw/binance-1s/BTCUSDT-1s-2026-08-*.zip')):
    with zipfile.ZipFile(z) as f:
        d=pl.read_csv(io.BytesIO(f.read(f.namelist()[0])),has_header=False,columns=[0,4]);d.columns=['t','c'];frames.append(d)
bn=pl.concat(frames).with_columns((pl.col('t')//1_000_000+1).alias('s')).select('s','c')  # close known at open+1s
close=dict(zip(bn['s'].to_list(),bn['c'].to_list()))
books=pl.concat([pl.read_parquet(p,columns=['market_slug','at_us','valid','bid','ask']) for r in ('btc5m-v1','btc5m-v2-extended') for p in glob.glob(f'D:/POLYMARKET/research/{r}/books/*.parquet')])
books=books.filter(pl.col('valid')&((pl.col('at_us')%1_000_000)==0)).with_columns(((pl.col('bid')+pl.col('ask'))/2).alias('mid'),(pl.col('at_us')//1_000_000).alias('s')).sort('market_slug','s')
xs={L:[] for L in range(-3,6)};ys=[]
for (slug,),g in books.group_by(['market_slug']):
    s=g['s'].to_numpy();m=g['mid'].to_numpy()
    for i in range(1,len(s)):
        if s[i]-s[i-1]!=1:continue
        dm=m[i]-m[i-1];ok=True;rets={}
        for L in xs:  # Binance return over the second ending L seconds BEFORE the mid change (L>0: Binance earlier)
            a_,b_=close.get(s[i]-L),close.get(s[i]-L-1)
            if a_ is None or b_ is None:ok=False;break
            rets[L]=np.log(a_/b_)*1e4
        if not ok:continue
        ys.append(dm)
        for L in xs:xs[L].append(rets[L])
ys=np.array(ys);print(f'\n== B) lead-lag: {len(ys)} one-second Up-mid changes; corr(mid change at t, Binance 1s return ending at t-L)')
for L in sorted(xs):print(f'  L={L:+d}s corr {np.corrcoef(ys,np.array(xs[L]))[0,1]:+.3f}')
big=np.abs(np.array(xs[1]))>3
print(f'  after a >3 bps Binance second (n={big.sum()}): mean |mid move| next second {np.abs(ys[big]).mean():.4f} vs otherwise {np.abs(ys[~big]).mean():.4f}')
