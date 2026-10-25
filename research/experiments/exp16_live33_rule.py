"""Paper check of the trading-station 3.3 last-minute entry rule (as described in DEVELOPMENT_REPORT_3.3.md):
at 240/270s, the 60s TWAP is >= $50 from the opening baseline and spot is on the same side -> buy that side at the
executable price ($5, depth-walked, fees in), hold to settlement. Model / confirmation filters are not modelled here,
so this measures the raw edge of the trigger itself. Data: PMData books 08-08..21 and live quotes since 10-22."""
import glob,io,json,sqlite3,zipfile
import numpy as np,polars as pl
frames=[]
for z in sorted(glob.glob('D:/POLYMARKET/research/proxy-backfill/raw/binance-1s/BTCUSDT-1s-2026-*.zip')):
    with zipfile.ZipFile(z) as f:
        d=pl.read_csv(io.BytesIO(f.read(f.namelist()[0])),has_header=False,columns=[0,4]);d.columns=['t','c'];frames.append(d)
bn=pl.concat(frames).with_columns((pl.col('t')//1_000_000).alias('s'));price={s:c for s,c in zip(bn['s'].to_list(),bn['c'].to_list())}
def rows_hist():
    f=pl.read_parquet('D:/POLYMARKET/research/market-rows/market-features.parquet').filter(pl.col('offset')>=240)
    for r in f.iter_rows(named=True):
        btc=price.get(r['start_s']);
        if not btc:continue
        yield r['market_slug'],r['offset'],r['spot_open_bps'],r['twap_open_bps'],btc,{'Up':(r['up_shares'],r['up_total']),'Down':(r['down_shares'],r['down_total'])},r['label_up']
def rows_live():
    c=sqlite3.connect('file:D:/POLYMARKET/trainer/training.sqlite?mode=ro',uri=True)
    for slug,start,feat,aux,y in c.execute("select slug,start_s,features,aux,target from samples where task='late' and source='live' and target is not null"):
        x=json.loads(feat);a=json.loads(aux);q=a.get('quotes');btc=float(a['evidence']['baseline']) if a.get('evidence') else price.get(start)
        if not q or not btc:continue
        yield slug,round(x['stage_fraction']*300),x['spot_open_bps'],x['twap_open_bps'],btc,{'Up':(q['Up']['shares'],q['Up']['total']),'Down':(q['Down']['shares'],q['Down']['total'])},y
def run(name,rows,dollars=50):
    taken=set();pnl=[];prices=[]
    for slug,off,spot,twap,btc,q,y in sorted(rows,key=lambda r:(r[0],r[1])):
        if slug in taken:continue
        dist=twap*1e-4*btc
        if abs(dist)<dollars or np.sign(spot)!=np.sign(twap):continue
        side='Up' if twap>0 else 'Down';shares,total=q[side];taken.add(slug)
        pnl.append(shares*((side=='Up')==(y==1))-total);prices.append(total/shares)
    p=np.array(pnl);rng=np.random.default_rng(0);bs=[p[rng.integers(0,len(p),len(p))].sum() for _ in range(4000)] if len(p) else [0]
    losses=int((p<0).sum())
    print(f'{name:28s} ${dollars:>3d}: trades {len(p):4d} wins {int((p>0).sum()):4d} losses {losses:3d} | avg entry {np.mean(prices) if prices else 0:.3f} | pnl {p.sum():+8.2f} roi {p.sum()/max(1,5*len(p)):+.4f} CI[{np.percentile(bs,2.5):+.1f},{np.percentile(bs,97.5):+.1f}] | avg win {p[p>0].mean() if (p>0).any() else 0:+.3f} avg loss {p[p<0].mean() if (p<0).any() else 0:+.3f}')
    return p
for dollars in (30,50,80):
    run('historical books 08-08..21',list(rows_hist()),dollars)
    run('live quotes since 10-22',list(rows_live()),dollars)
p=run('historical books 08-08..21',list(rows_hist()),50)
# risk of the 3.3 sizing: 20% of a $34 bankroll per trade, stop at -50%: probability the stop is hit within N trades (bootstrap)
rng=np.random.default_rng(1);stake_frac=.2;bank0=35.0
for n in (20,50,100):
    hit=0
    for _ in range(4000):
        bank=peak=bank0
        for r in rng.choice(p/5,n):  # per-$ return of a trade
            bank+=max(5,stake_frac*bank)*r if bank>=5 else 0
            peak=max(peak,bank)
            if bank<=peak*.5:hit+=1;break
    print(f'drawdown-stop (50%) reached within {n} trades: {hit/4000:.1%} (bootstrap of historical rule outcomes, 20% sizing, $34 start)')
