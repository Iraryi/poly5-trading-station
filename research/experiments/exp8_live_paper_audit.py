"""Live paper audit: per-model paper P&L vs the judge's validation score, P&L over time, and favourite/longshot split.
Read-only on D:/POLYMARKET/trainer. Paper rule identical to trainstation.data.paper_trades (EDGE 3pp, $5, one trade/market)."""
import sys,json,sqlite3,time,collections
import numpy as np
sys.path.insert(0,'D:/POLYMARKET/Poly5_Trading_Station_v3')
from trainstation.data import EDGE
from scipy.stats import spearmanr
c=sqlite3.connect('file:D:/POLYMARKET/trainer/training.sqlite?mode=ro',uri=True)
T=lambda ms:time.strftime('%m-%d %H:%M',time.localtime(ms/1000))
rows=c.execute("""SELECT p.model_id,s.task,s.slug,s.decision_ms,p.value,s.target,json_extract(s.aux,'$.quotes'),p.at_ms FROM predictions p JOIN samples s ON s.id=p.sample_id
  WHERE s.task IN ('late','direction') AND s.source='live' AND s.target IS NOT NULL AND p.at_ms<s.ready_ms AND json_extract(s.aux,'$.quotes') IS NOT NULL ORDER BY s.slug,s.decision_ms""").fetchall()
trades=collections.defaultdict(list);decisions=collections.Counter()
for model,task,slug,decision,p,y,q,at in rows:
    q=json.loads(q);key=(model,task);decisions[key]+=1
    if any(t[0]==slug for t in trades[key]):continue
    for side,prob,win in (('Up',p,y==1),('Down',1-p,y==0)):
        quote=q.get(side)
        if quote and quote['shares']>0:
            price=quote['total']/quote['shares']
            if prob-price>EDGE:trades[key].append((slug,decision,side,price,prob,quote['shares']*win-quote['total']));break
def ci(x):
    x=np.array(x);rng=np.random.default_rng(0);b=[x[rng.integers(0,len(x),len(x))].sum() for _ in range(4000)]
    return np.percentile(b,2.5),np.percentile(b,97.5)
scores={}
for (body,) in c.execute("SELECT body FROM trials WHERE score IS NOT NULL"):
    b=json.loads(body);scores[b['id']]=b['metrics']['score'];scores[b['id']+'_FULL']=b['metrics']['score']
print('live quoted predictions since',T(min(r[3] for r in rows)),'to',T(max(r[3] for r in rows)))
print('\n== per model (live, predictions made before outcome) ==')
table=[]
for (model,task),t in sorted(trades.items(),key=lambda kv:-len(kv[1])):
    pnl=[x[-1] for x in t];lo,hi=ci(pnl) if len(pnl)>1 else (0,0)
    fav=[x for x in t if x[3]>=.5];shot=[x for x in t if x[3]<.5]
    table.append((model,task,scores.get(model),len(t),sum(pnl)))
    print(f"{task:9s} {model[-36:]:36s} judge {scores.get(model,float('nan')):.4f} | decisions {decisions[(model,task)]:3d} trades {len(t):3d} wins {sum(p>0 for p in pnl):3d} pnl {sum(pnl):+8.2f} CI[{lo:+.1f},{hi:+.1f}]"
          f" | favourites {len(fav)} pnl {sum(x[-1] for x in fav):+.2f} · longshots {len(shot)} pnl {sum(x[-1] for x in shot):+.2f}")
for task in ('late','direction'):
    pts=[(s,p/max(n,1),n) for m,tk,s,n,p in table if tk==task and s is not None and n>=5]
    if len(pts)>=4:print(f'\n{task}: spearman(judge score, pnl per trade) over {len(pts)} models with >=5 trades: {spearmanr([a for a,_,_ in pts],[b for _,b,_ in pts]).correlation:+.2f} (negative = better score earns more)')
print('\n== pooled deployed (_FULL) by hour ==')
for task in ('late','direction'):
    pooled={}
    for (model,tk),t in trades.items():
        if tk!=task or not model.endswith('_FULL'):continue
        for x in t:pooled.setdefault(x[0],x)  # first deployed model to trade each market
    t=sorted(pooled.values(),key=lambda x:x[1]);cum=0;hours=collections.OrderedDict()
    for x in t:hours.setdefault(time.strftime('%H',time.localtime(x[1]/1000)),[]).append(x[-1])
    if not t:continue
    lo,hi=ci([x[-1] for x in t])
    print(f'{task}: {len(t)} trades, pnl {sum(x[-1] for x in t):+.2f} CI[{lo:+.1f},{hi:+.1f}], wins {sum(x[-1]>0 for x in t)}')
    for h,v in hours.items():cum+=sum(v);print(f'   hour {h}: trades {len(v):2d} pnl {sum(v):+7.2f} cumulative {cum:+8.2f}')
    by=collections.defaultdict(list)
    for x in t:by['favourite (price>=.5)' if x[3]>=.5 else 'longshot (price<.5)'].append(x[-1])
    for k,v in by.items():print(f'   {k}: {len(v)} trades, wins {sum(p>0 for p in v)}, pnl {sum(v):+.2f}')
