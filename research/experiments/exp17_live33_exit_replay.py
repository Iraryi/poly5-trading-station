"""Second-by-second replay of the 3.3 last-minute rule on the full order-book recorder (since 2026-10-23 00:25):
from 240s to end-15s, enter when TWAP and spot are on the same side and TWAP is >= $50 from the opening TWAP for 3
consecutive seconds; buy $5 of that side on the next second's book (depth-walked, fee 7%*p*(1-p) per share).
Exit A (3.3): sell at the first later second whose bid-side liquidation nets >= +0.5%, else hold to settlement.
Exit B: hold to settlement. Model filters are not applied (this isolates the trigger and the exit). Paper only."""
import glob,json,sqlite3
import numpy as np,polars as pl
R='D:/POLYMARKET/trainer/orderbook'
books=pl.concat([pl.read_parquet(p) for p in glob.glob(R+'/date=*/books/*.parquet')],how='diagonal_relaxed').filter(pl.col('initialized'))
ref=pl.concat([pl.read_parquet(p) for p in glob.glob(R+'/date=*/reference/*.parquet')],how='diagonal_relaxed').unique(['kind','observed_ms']).sort('observed_ms')
c=sqlite3.connect('file:D:/POLYMARKET/trainer/training.sqlite?mode=ro',uri=True)
labels={s:t for s,t in c.execute("select slug,target from samples where task='direction' and source='live' and target is not null")}
fee=lambda p,q:q*.07*p*(1-p)
def buy(asks,sizes,budget=5.):
    cost=fees=shares=0.
    for p,s in zip(asks,sizes):
        if p>.99:break
        q=min(s,(budget-cost-fees)/(p+.07*p*(1-p)))
        if q<=0:break
        cost+=p*q;fees+=fee(p,q);shares+=q
        if budget-cost-fees<1e-6:break
    return (shares,cost+fees) if shares>=5 and cost+fees>=4.99 else None
def liquidate(bids,sizes,shares):
    left=shares;value=0.
    for p,s in zip(bids,sizes):
        q=min(left,s);value+=p*q-fee(p,q);left-=q
        if left<=1e-9:return value
    return None
tw=ref.filter(pl.col('kind')=='twap');sp=ref.filter(pl.col('kind')=='spot')
tw_t,tw_v=tw['observed_ms'].to_numpy(),tw['value_float'].to_numpy();sp_t,sp_v=sp['observed_ms'].to_numpy(),sp['value_float'].to_numpy()
def at(t,v,ms):
    i=np.searchsorted(t,ms,side='right')-1;return v[i] if i>=0 and ms-t[i]<=5000 else None
res={'A':[],'B':[]};exits=[];checked=0
for (slug,),g in books.group_by(['slug']):
    if slug not in labels:continue
    start=int(slug.rsplit('-',1)[1]);open_twap=at(tw_t,tw_v,start*1000)
    if open_twap is None:continue
    checked+=1
    by={(r['ts_ms']//1000,r['outcome']):r for r in g.iter_rows(named=True)}
    streak=0;side=None;entry=None
    for sec in range(start+240,start+285):
        t=at(tw_t,tw_v,sec*1000);s=at(sp_t,sp_v,sec*1000)
        if t is None or s is None:streak=0;continue
        d=t-open_twap;direction='Up' if d>=50 and s>open_twap else 'Down' if d<=-50 and s<open_twap else None
        streak=streak+1 if direction and direction==side else (1 if direction else 0);side=direction
        if streak>=3:
            row=by.get((sec+1,side))
            if row and (fill:=buy(row['ask_prices'],row['ask_sizes'])):entry=(sec+1,side,fill);break
    if not entry:continue
    sec0,side,(shares,cost)=entry;win=(side=='Up')==(labels[slug]==1);settle=shares*win-cost
    res['B'].append(settle);sold=None
    for sec in range(sec0+1,start+300):
        row=by.get((sec,side))
        if not row:continue
        v=liquidate(row['bid_prices'],row['bid_sizes'],shares)
        if v is not None and v>=cost*1.005:sold=(v-cost,sec-start);break
    res['A'].append(sold[0] if sold else settle);exits.append(sold[1] if sold else None)
print(f'markets replayed {checked}, rule triggered {len(res["B"])}')
for k,name in (('A','3.3 exit: sell at +0.5% net'),('B','hold to settlement')):
    p=np.array(res[k]);rng=np.random.default_rng(0);bs=[p[rng.integers(0,len(p),len(p))].sum() for _ in range(4000)] if len(p) else [0]
    print(f'{name:30s} trades {len(p):3d} wins {int((p>0).sum()):3d} losses {int((p<0).sum()):3d} | pnl {p.sum():+7.2f} per trade {p.mean() if len(p) else 0:+.3f} CI[{np.percentile(bs,2.5):+.1f},{np.percentile(bs,97.5):+.1f}] | avg win {p[p>0].mean() if (p>0).any() else 0:+.3f} avg loss {p[p<0].mean() if (p<0).any() else 0:+.3f}')
sold=[e for e in exits if e is not None];print(f'take-profit hit in {len(sold)}/{len(exits)} trades, median exit at {np.median(sold) if sold else float("nan"):.0f}s')
w=np.array(res['A']);print(f'break-even win rate implied by 3.3 exit: {abs(w[w<0].mean())/(abs(w[w<0].mean())+w[w>0].mean()):.1%}' if (w<0).any() and (w>0).any() else '')
