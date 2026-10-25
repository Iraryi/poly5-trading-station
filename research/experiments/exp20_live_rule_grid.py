"""Which live entry rule is 'fairly safe' yet trades more often than once per 3.5 h? Out-of-sample only: live late
samples from the final-exam start (10-21 20:35 UTC) on, scored by the 5 pinned exam models (trained before that).
Rule = last minute (offset >= 240, < 285), spot and TWAP same side, |TWAP - baseline| >= D dollars, vote probability
for that side >= .55, vote - executable avg price (fees in) >= E. First qualifying decision per market, hold to
settlement, $5 notional (the paper scale). Reports trades/hour, wins, P&L, lowest point, max drawdown, 95% CI."""
import sys,json,sqlite3,random
sys.path.insert(0,'D:/POLYMARKET/Poly5_Trading_Station_v3')
import numpy as np,joblib
from trainstation.data import vector
T='D:/POLYMARKET/trainer'
db=sqlite3.connect(f'file:{T}/training.sqlite?mode=ro',uri=True)
ex=json.load(open(f'{T}/pass2.json',encoding='utf-8'))['tasks']['late'];start=json.load(open(f'{T}/pass2.json',encoding='utf-8'))['exam_start_s']
settings=json.load(open('D:/POLYMARKET/data/settings/settings.json',encoding='utf-8'))
models=[joblib.load(f'D:/POLYMARKET/data/models/releases/{rid}/model.joblib') for rid in settings['vote_release_ids']]
rows=[]
for slug,s,feat,aux,y in db.execute("select slug,start_s,features,aux,target from samples where task='late' and source='live' and target is not null and start_s>=?",(start,)):
    x=json.loads(feat);a=json.loads(aux);q=a.get('quotes');ev=a.get('evidence') or {}
    off=x['stage_fraction']*300
    if not q or not ev.get('baseline') or not 240<=off<285:continue
    rows.append((slug,s,off,x,float(ev['baseline']),q,int(y)))
X={m_i:None for m_i in range(len(models))}
vote=np.mean([m['model'].predict_proba(np.array([vector(r[3],m['features']) for r in rows],float))[:,1] for m in models],axis=0)
markets=sorted({r[1] for r in rows});hours=len(markets)/12
print(f'out-of-sample live markets {len(markets)} (~{hours:.1f} h), last-minute decision rows {len(rows)}')
def run(D,E):
    taken=set();trades=[]
    for (slug,s,off,x,base,q,y),p in sorted(zip(rows,vote),key=lambda t:(t[0][1],t[0][2])):
        if s in taken:continue
        twap=x['twap_open_bps']*1e-4*base;spot=x['spot_open_bps']*1e-4*base
        if abs(twap)<D or np.sign(twap)!=np.sign(spot) or twap==0:continue
        side='Up' if twap>0 else 'Down';fair=p if side=='Up' else 1-p;qq=q.get(side)
        if not qq or qq['shares']<=0:continue
        avg=qq['total']/qq['shares']
        if fair<.55 or fair-avg<E:continue
        win=(y==1)==(side=='Up');taken.add(s);trades.append((s,avg,qq['shares']*win-qq['total']))
    p=[t[2] for t in trades];eq=low=peak=dd=0
    for v in p:eq+=v;low=min(low,eq);peak=max(peak,eq);dd=max(dd,peak-eq)
    ci='—'
    if len(p)>=2:
        rng=random.Random(0);sums=sorted(sum(rng.choice(p) for _ in p) for _ in range(2000));ci=f'[{sums[49]:+.1f},{sums[1949]:+.1f}]'
    return len(p),sum(v>0 for v in p),sum(p),low,dd,ci,(np.mean([t[1] for t in trades]) if trades else 0)
print(' D($)   E    trades  per-hour  wins  avg-entry   P&L($5)   lowest  max-dd   95% CI')
for D in (50,40,30,20,0):
    for E in (.005,.02,.03,.05):
        n,w,pnl,low,dd,ci,avg=run(D,E)
        print(f'{D:4d}  {E:.3f}  {n:5d}   {n/hours:6.2f}   {w:4d}   {avg:.3f}    {pnl:+7.2f}   {low:+6.2f}  {dd:6.2f}   {ci}')
