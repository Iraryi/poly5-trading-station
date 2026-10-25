"""Paper shadow ledger of the trading-station 3.3 last-minute rule, replayed second by second on the full order-book
recorder (never places orders). Trigger as in poly5/live_service.py: from 240s to end-15s, the 60s TWAP is at least
THRESHOLD dollars from the opening TWAP with spot on the same side for 3 consecutive seconds; buy $5 of that side on
the next second's recorded book (depth walked to 0.99, fee 7%*p*(1-p) per share). Model filters of 3.3 are not
replayed (they can only remove trades). Variants:
  tp05   3.3 exit: sell at the first later second whose bid-side liquidation nets >= +0.5%, else settle
  hold   hold to official settlement
  hold80 hold to settlement, $80 threshold
Each (market, variant) is written once to the shadow33 table after the market's official outcome is known.
"""
import glob
from pathlib import Path

VARIANTS={'tp05':(50.,'take_profit'),'hold':(50.,'hold'),'hold80':(80.,'hold')}
FEE=.07

def fee(p,q):return q*FEE*p*(1-p)

def buy(asks,sizes,budget=5.):
    cost=fees=shares=0.
    for p,s in zip(asks,sizes):
        if p>.99:break
        q=min(s,(budget-cost-fees)/(p+FEE*p*(1-p)))
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

def replay(slug,books,twap,spot,label):
    """books: {(second, outcome): row}; twap/spot: callables ms -> value or None. Returns {variant: trade or None}."""
    import math
    start=int(slug.rsplit('-',1)[1]);open_twap=twap(start*1000);out={}
    if open_twap is None:return out
    for name,(threshold,exit_rule) in VARIANTS.items():
        streak=0;side=None;entry=None
        for sec in range(start+240,start+285):
            t,s=twap(sec*1000),spot(sec*1000)
            if t is None or s is None:streak=0;side=None;continue
            d=t-open_twap;direction='Up' if d>=threshold and s>open_twap else 'Down' if d<=-threshold and s<open_twap else None
            streak=streak+1 if direction and direction==side else (1 if direction else 0);side=direction
            if streak>=3:
                row=books.get((sec+1,side))
                if row and (fill:=buy(row['ask_prices'],row['ask_sizes'])):entry=(sec+1,side,fill);break
        if not entry:out[name]=None;continue
        sec0,side,(shares,cost)=entry;win=(side=='Up')==(label==1);trade=None
        if exit_rule=='take_profit':
            for sec in range(sec0+1,start+300):
                row=books.get((sec,side))
                v=liquidate(row['bid_prices'],row['bid_sizes'],shares) if row else None
                if v is not None and v>=cost*1.005:trade={'exit_s':sec-start,'pnl':v-cost,'reason':'take_profit'};break
        trade=trade or {'exit_s':300,'pnl':shares*win-cost,'reason':'settlement'}
        out[name]={'entry_s':sec0-start,'side':side,'shares':shares,'cost':cost,**trade}
    return out

def update(data):
    """Replay newly settled recorded markets; idempotent. Returns per-variant summaries (with 95% intervals)."""
    import numpy as np,polars as pl
    from .data import summarise
    root=Path(data.root)/'orderbook'
    with data.db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS shadow33(slug TEXT,variant TEXT,triggered INTEGER,entry_s INTEGER,side TEXT,shares REAL,cost REAL,exit_s INTEGER,pnl REAL,reason TEXT,at_ms INTEGER,PRIMARY KEY(slug,variant))')
        done={r[0] for r in c.execute('SELECT DISTINCT slug FROM shadow33')}
        labels={s:t for s,t in c.execute("SELECT slug,target FROM samples WHERE task='direction' AND source='live' AND target IS NOT NULL")}
    todo=sorted(s for s in labels if s not in done)
    if todo and root.exists():
        days=sorted({p.parent.parent.name for p in root.glob('date=*/books/*.parquet')})
        for i,day in enumerate(days):
            pending=[s for s in todo if s not in done]
            if not pending:break
            # a market near UTC midnight spills into the next day's folder
            folders=[day]+days[i+1:i+2]
            books=pl.concat([pl.read_parquet(p,columns=['ts_ms','slug','outcome','initialized','bid_prices','bid_sizes','ask_prices','ask_sizes']) for d in folders for p in glob.glob(str(root/d/'books'/'*.parquet'))],how='diagonal_relaxed')
            books=books.filter(pl.col('initialized')&pl.col('slug').is_in(pending))
            if books.is_empty():continue
            ref_files=[p for d in folders for p in glob.glob(str(root/d/'reference'/'*.parquet'))]
            if not ref_files:continue
            ref=pl.concat([pl.read_parquet(p,columns=['kind','observed_ms','value_float']) for p in ref_files],how='diagonal_relaxed').unique(['kind','observed_ms']).sort('observed_ms')
            series={k:(g['observed_ms'].to_numpy(),g['value_float'].to_numpy()) for (k,),g in ref.group_by(['kind'])}
            def lookup(kind):
                t,v=series.get(kind,(np.array([]),np.array([])))
                def at(ms):
                    i=np.searchsorted(t,ms,side='right')-1
                    return float(v[i]) if i>=0 and ms-t[i]<=5000 else None
                return at
            twap,spot=lookup('twap'),lookup('spot');rows=[];now=__import__('time').time()
            for (slug,),g in books.group_by(['slug']):
                by={(r['ts_ms']//1000,r['outcome']):r for r in g.iter_rows(named=True)}
                result=replay(slug,by,twap,spot,labels[slug])
                if not result:continue
                for name,trade in result.items():
                    rows.append((slug,name,1 if trade else 0,*(trade[k] if trade else None for k in ('entry_s','side','shares','cost','exit_s','pnl','reason')),int(now*1000)))
                done.add(slug)
            with data.db() as c:c.executemany('INSERT OR IGNORE INTO shadow33 VALUES(?,?,?,?,?,?,?,?,?,?,?)',rows)
    with data.db() as c:
        stats={}
        for name in VARIANTS:
            markets=c.execute('SELECT COUNT(*) FROM shadow33 WHERE variant=?',(name,)).fetchone()[0]
            trades=[r for r in c.execute('SELECT pnl,cost FROM shadow33 WHERE variant=? AND triggered=1',(name,))]
            stats[name]=summarise([p for p,_ in trades],[c_ for _,c_ in trades],markets)
    return stats
