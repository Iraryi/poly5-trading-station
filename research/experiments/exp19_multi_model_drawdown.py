"""Exam-window equity curves: each late/direction exam candidate traded alone ($5/trade, the trainer's paper rule),
and 'several models at once' portfolios. Reports final P&L, lowest cumulative point and max drawdown, all in time
order of market start. Read-only: loads saved candidate models; never trains, never trades."""
import sys,json,sqlite3,random,statistics as st
sys.path.insert(0,'D:/POLYMARKET/Poly5_Trading_Station_v3')
import numpy as np,joblib
from trainstation.data import vector,EDGE,MIN_ENTRY_PRICE
T='D:/POLYMARKET/trainer'
db=sqlite3.connect(f'file:{T}/training.sqlite?mode=ro',uri=True)
p2=json.load(open(f'{T}/pass2.json',encoding='utf-8'))

def trades_for(rows,probs):
    """Trainer paper rule, but keep each trade: (market start, slug, side, stake, pnl)."""
    out={};seen=set()
    for r,p in sorted(zip(rows,probs),key=lambda t:(t[0]['slug'],t[0]['decision_ms'])):
        if r['slug'] in seen:continue
        q_all=r['aux']['quotes']
        for side,prob,win in (('Up',p,r['y']==1),('Down',1-p,r['y']==0)):
            q=q_all.get(side)
            if q and q['shares']>0 and prob-q['total']/q['shares']>EDGE and q['total']/q['shares']>=MIN_ENTRY_PRICE:
                seen.add(r['slug']);out[r['slug']]=(r['start_s'],side,q['total'],q['shares']*win-q['total']);break
    return out

def curve(pnl_by_market,starts):
    eq=0;low=0;peak=0;dd=0
    for s in sorted(starts):
        eq+=pnl_by_market.get(s,0);low=min(low,eq);peak=max(peak,eq);dd=max(dd,peak-eq)
    return eq,low,dd

for task in ('late','direction'):
    ex=p2['tasks'][task]
    trials=[json.loads(b) for (b,) in db.execute('select body from trials where created_ms between ? and ?',(ex['exam_started_ms']-60000,ex['exam_done_ms']+60000))]
    trials=[t for t in trials if t.get('task')==task and (t.get('metrics') or {}).get('split')=='exam']
    snap=json.load(open(trials[0]['snapshot'],encoding='utf-8'))
    start=trials[0]['split']['val_start_s']
    rows=[r for r in snap if r['start_s']>=start and (r.get('aux') or {}).get('quotes')]
    starts=sorted({r['start_s'] for r in rows})
    per={}
    for t in trials:
        m=joblib.load(t['model_path']);X=np.array([vector(r['x'],m['features']) for r in rows],float)
        per[t['id']]=(t['metrics']['score'],trades_for(rows,m['model'].predict_proba(X)[:,1]),m['model'].predict_proba(X)[:,1])
    ranked=sorted(per,key=lambda k:per[k][0])
    def bymkt(tr):
        d={}
        for slug,(s,side,stake,pnl) in tr.items():d[s]=d.get(s,0)+pnl
        return d
    print(f'\n===== {task}: exam window {len(starts)} markets (~{len(starts)/12:.1f} h), {len(trials)} candidates, $5/trade =====')
    rows_out=[]
    for k in ranked:
        sc,tr,_=per[k];f,low,dd=curve(bymkt(tr),starts);rows_out.append((sc,len(tr),f,low,dd))
    lows=[r[3] for r in rows_out];dds=[r[4] for r in rows_out];fins=[r[2] for r in rows_out]
    print('single models: final P&L median %+.1f [min %+.1f, max %+.1f] | lowest point median %+.1f, worst %+.1f | max drawdown median %.1f, worst %.1f'%(
        st.median(fins),min(fins),max(fins),st.median(lows),min(lows),st.median(dds),max(dds)))
    for name,i in (('#1 (champion)',0),('median-ranked',len(ranked)//2),('last (old champion?)',len(ranked)-1)):
        sc,n,f,low,dd=rows_out[i];print(f'  {name:22s} score {sc:.4f} trades {n:3d} final {f:+7.1f} lowest {low:+6.1f} max drawdown {dd:5.1f}')
    # Portfolios: each model trades its own $5 independently (stakes add up), vs one averaged-probability ensemble.
    def portfolio(ids):
        d={}
        for k in ids:
            for s,v in bymkt(per[k][1]).items():d[s]=d.get(s,0)+v
        n=sum(len(per[k][1]) for k in ids);return n,curve(d,starts)
    def ensemble(ids):
        p=np.mean([per[k][2] for k in ids],axis=0);tr=trades_for(rows,p);return len(tr),curve(bymkt(tr),starts)
    print('  several models at once (each its own $5; capital at risk per market up to k x $5):')
    for k in (3,5,10):
        n,(f,low,dd)=portfolio(ranked[:k]);print(f'    top-{k:<2d} together: trades {n:4d} final {f:+7.1f} lowest {low:+6.1f} max drawdown {dd:5.1f}  (per $5 slot: final {f/k:+.1f}, drawdown {dd/k:.1f})')
    n,(f,low,dd)=portfolio(ranked);print(f'    all {len(ranked)} together: trades {n:4d} final {f:+7.1f} lowest {low:+6.1f} max drawdown {dd:5.1f}  (per $5 slot: final {f/len(ranked):+.1f}, drawdown {dd/len(ranked):.1f})')
    for k in (3,5,10):
        n,(f,low,dd)=ensemble(ranked[:k]);print(f'    top-{k:<2d} averaged into ONE vote ($5/trade): trades {n:3d} final {f:+7.1f} lowest {low:+6.1f} max drawdown {dd:5.1f}')
    # How similar are they? share of the champion's trades that the median model also takes (same market, same side).
    a=per[ranked[0]][1];sims=[]
    for k in ranked[1:]:
        b=per[k][1];sims.append(sum(1 for s,v in a.items() if s in b and b[s][1]==v[1])/max(1,len(a)))
    print('  overlap: other candidates take the same market+side as the champion in median %.0f%% of its trades'%(100*st.median(sims)))
