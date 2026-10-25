"""Maker (resting limit order) paper backtest, PMData 2026-08-08..21. At a checkpoint, rest a $5 BUY on the favourite
side, either joining its best bid (behind the visible queue) or improving it by one tick (front of queue, only if the
spread is >= 2 ticks). Fill from the trade tape within W seconds: same outcome with a taker SELL at <= our price, or the
complement outcome with a taker BUY at >= 1 - our price (mint match). At our exact price we need the traded size to
exceed the queue ahead plus our size. Filled positions are held to settlement; makers pay no fee; rebates excluded."""
import glob,io,zipfile
import numpy as np,polars as pl
frames=[]
for z in sorted(glob.glob('D:/POLYMARKET/research/proxy-backfill/raw/binance-1s/BTCUSDT-1s-2026-08-*.zip')):
    with zipfile.ZipFile(z) as f:
        d=pl.read_csv(io.BytesIO(f.read(f.namelist()[0])),has_header=False,columns=[0,4]);d.columns=['t','c'];frames.append(d)
_bn=pl.concat(frames).with_columns((pl.col('t')//1_000_000+1).alias('s'))
CLOSE=dict(zip(_bn['s'].to_list(),_bn['c'].to_list()))
LATENCY_S=1  # a Binance move seen at second s cancels our order from s+1 on
TICK=.01
books={}
for r in ('btc5m-v1','btc5m-v2-extended'):
    for p in glob.glob(f'D:/POLYMARKET/research/{r}/checkpoint-books/*.parquet'):
        for row in pl.read_parquet(p).iter_rows(named=True):
            if row['reason']!='reconstructed_top_mismatch':books[(row['market_slug'],row['at_us'])]=row
labels=dict(pl.read_parquet('D:/POLYMARKET/research/btc5m-v2-extended/reference-features.parquet').filter(pl.col('label_up').is_not_null()).select('market_slug','label_up').unique('market_slug').iter_rows())
tr=pl.concat([pl.read_parquet(p) for p in glob.glob('D:/POLYMARKET/research/btc5m-v2-extended/raw/trades/**/*.parquet',recursive=True)])
tr=tr.filter(pl.col('event_type')=='last_trade_price').with_columns(pl.col('timestamp').dt.epoch('us').alias('ts')).select('market_slug','ts','outcome','price','size','side')
trades={k[0]:g.sort('ts') for k,g in tr.group_by(['market_slug'])}
def book_for(st,name):
    """bids of the outcome we buy: Up bids as recorded; Down bids mirror Up asks."""
    if name=='Up':return list(zip(st['bid_prices'] or [],st['bid_sizes'] or [])),list(zip(st['ask_prices'] or [],st['ask_sizes'] or []))
    return [(round(1-p,6),s) for p,s in zip(st['ask_prices'] or [],st['ask_sizes'] or [])],[(round(1-p,6),s) for p,s in zip(st['bid_prices'] or [],st['bid_sizes'] or [])]
def run(offset,window,mode,lo,hi,cancel_bps=None):
    out=[]
    for (slug,at),st in books.items():
        start=int(slug.rsplit('-',1)[1])
        if at!=(start+offset)*1_000_000 or slug not in labels or slug not in trades:continue
        mid=None
        if st['bid'] is not None and st['ask'] is not None:mid=(st['bid']+st['ask'])/2
        if mid is None:continue
        name='Up' if mid>=.5 else 'Down'
        bids,asks=book_for(st,name)
        if not bids or not asks:continue
        best_bid=max(p for p,_ in bids);best_ask=min(p for p,_ in asks)
        if mode=='improve':
            if best_ask-best_bid<2*TICK-1e-9:continue
            price=round(best_bid+TICK,2);queue=0.
        else:price=best_bid;queue=sum(s for p,s in bids if abs(p-price)<1e-9)
        if not (lo<=price<hi):continue
        shares=5/price;mine='yes' if name=='Up' else 'no';other='no' if mine=='yes' else 'yes'
        g=trades[slug].filter((pl.col('ts')>at)&(pl.col('ts')<=at+window*1_000_000))
        better=g.filter(((pl.col('outcome')==mine)&(pl.col('side')=='SELL')&(pl.col('price')<price-1e-9))|((pl.col('outcome')==other)&(pl.col('side')=='BUY')&(pl.col('price')>1-price+1e-9)))
        same=g.filter(((pl.col('outcome')==mine)&(pl.col('side')=='SELL')&((pl.col('price')-price).abs()<1e-9))|((pl.col('outcome')==other)&(pl.col('side')=='BUY')&((pl.col('price')-(1-price)).abs()<1e-9)))
        # fill time: first better-priced trade, or the trade at our price that clears the queue ahead of us
        times=[]
        if better.height:times.append(better['ts'][0])
        if same.height:
            c=np.cumsum(same['size'].to_numpy());k=np.searchsorted(c,queue+shares,side='right')
            if k<len(c):times.append(same['ts'][int(k)])
        filled=bool(times)
        if filled and cancel_bps is not None:
            # Quote management: cancel once Binance has moved cancel_bps against our side since posting.
            t_fill=min(times)//1_000_000;post=at//1_000_000;base=CLOSE.get(post);sign=1 if name=='Up' else -1
            if base:
                for sec in range(post+1,t_fill-LATENCY_S+1):
                    c_=CLOSE.get(sec)
                    if c_ and sign*np.log(c_/base)*1e4<=-cancel_bps:filled=False;break
        win=(name=='Up')==(labels[slug]==1)
        out.append((filled,price,shares*win-5 if filled else 0.,win,(min(times)-at)/1e6 if times else None,mid))
    return out

import numpy as np
for offset in (120,210,240):
    o=run(offset,30,'join',.55,.97)
    d=np.array([x[4] for x in o if x[0]])
    print(f'offset {offset}: filled {len(d)} | fill delay after posting: median {np.median(d):.2f}s, share within 1s {np.mean(d<=1):.1%}, within 2s {np.mean(d<=2):.1%}')
