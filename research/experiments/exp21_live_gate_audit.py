"""Why so few live trades? Second-by-second audit of the live entry gates on the order-book recorder since the user
re-armed (10-23 18:25 UTC): last minute (240-284 s), |TWAP - baseline| >= $30, spot same side, then the signal side's
best ask. Classifies each market by the cheapest qualifying ask and compares the two cost formulas: live's worst-case
envelope (ask + 1 tick + 0.0175 fee) vs the actual fee at that price (rate*p*(1-p)). Outcome from the final TWAP."""
import glob,bisect,polars as pl,datetime as dt
B='D:/POLYMARKET/trainer/orderbook'
since=int(dt.datetime(2026,10,23,18,25,tzinfo=dt.timezone.utc).timestamp())
ref=pl.concat([pl.read_parquet(f) for f in glob.glob(B+'/date=2026-10-2[34]/reference/*.parquet')],how='diagonal_relaxed').sort('observed_ms')
bbo=pl.concat([pl.read_parquet(f) for f in glob.glob(B+'/date=2026-10-2[34]/bbo/*.parquet')],how='diagonal_relaxed').sort('received_ms')
series={k:(g['observed_ms'].to_list(),g['value_float'].to_list()) for k,g in ((k,ref.filter(pl.col('kind')==k)) for k in ('spot','twap'))}
def at(kind,ms):
    t,v=series[kind];i=bisect.bisect_right(t,ms)-1;return v[i] if i>=0 and ms-t[i]<=5000 else None
books={}
for (slug,o),g in bbo.group_by(['slug','outcome']):books[(slug,o)]=(g['received_ms'].to_list(),g['ask'].to_list())
def ask(slug,o,ms):
    t,v=books.get((slug,o),([],[]));i=bisect.bisect_right(t,ms)-1;return v[i] if i>=0 else None
now=int(ref['observed_ms'].max()//1000)
rows=[];cats={'no $30 signal':0,'signal but ask > 0.965':0,'0.94 < ask <= 0.965 (blocked only by worst-case fee)':0,'ask <= 0.94 (passes either formula)':0}
for start in range(since//300*300+300,now-300,300):
    slug=f'btc-updown-5m-{start}';base=at('twap',start*1000)
    if base is None:continue
    best=None
    for off in range(240,285):
        ms=(start+off)*1000;tw=at('twap',ms);sp=at('spot',ms)
        if tw is None or sp is None:continue
        d=tw-base
        if abs(d)<30 or (sp-base)*d<=0:continue
        side='Up' if d>0 else 'Down';a=ask(slug,side,ms)
        if a is None:continue
        if best is None or a<best[1]:best=(side,a,off)
    end=at('twap',(start+300)*1000)
    if best is None:cats['no $30 signal']+=1;continue
    side,a,off=best;won=(end>=base)==(side=='Up') if end is not None else None
    if a>.965:cats['signal but ask > 0.965']+=1
    elif a>.94:cats['0.94 < ask <= 0.965 (blocked only by worst-case fee)']+=1;rows.append((slug,side,a,off,won))
    else:cats['ask <= 0.94 (passes either formula)']+=1;rows.append((slug,side,a,off,won))
n=sum(cats.values());print(f'markets audited {n} (~{n/12:.1f} h since re-arm)')
for k,v in cats.items():print(f'  {k:55s} {v}')
pnl=0
for slug,side,a,off,won in rows:
    fee=.07*a*(1-a);r=(1-a-fee)/(a+fee) if won else -1;pnl+=5*r
    print(f'  {slug} {side:4s} ask {a:.3f} at {off}s  actual cost {a+fee:.3f} worst-case {a+.001+.0175:.3f}  {"WIN" if won else "LOSS" if won is not None else "?"}  {5*r:+.2f}')
print(f'  P&L if all were taken at $5: {pnl:+.2f}')
