"""Walk-forward paper backtest of single-model and coordinated (direction entry + live re-scoring + early exit) strategies
on reconstructed PMData order books. Research only: hypothetical depth consumption with the official fee formula, never
orders or exchange receipts. Usage: research_coordinated_backtest.py [--first 2026-08-11] [--last 2026-08-21]
"""
import argparse,json,math,sys
from datetime import datetime,timezone
from decimal import Decimal as D
from pathlib import Path
import numpy as np
import polars as pl
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research_execution import buy,liquidate
from trainstation.data import REF,design,vector
from sklearn.ensemble import RandomForestClassifier

RESEARCH=Path('D:/POLYMARKET/research')
EDGE=.03;LIMIT=D('.99');TAKE_PROFIT=D('1.5');RISK_P=.95
CHECKPOINTS=(60,120,210,240,270)
QUARANTINE={'btc-updown-5m-1786911600','btc-updown-5m-1787354400'}

def books():
    frames=[pl.read_parquet(p) for r in ('btc5m-v1','btc5m-v2-extended') for p in sorted((RESEARCH/r/'checkpoint-books').glob('*.parquet'))]
    out={}
    for r in pl.concat(frames,how='vertical_relaxed').iter_rows(named=True):
        # Unchanged-but-connected books are usable; only reconstructions that disagree with the exchange's top are not.
        if r['reason']=='reconstructed_top_mismatch':continue
        out[(r['market_slug'],r['at_us'])]=r
    return out

def side_state(state,side):
    """Executable view for one outcome. Polymarket's Down book mirrors Up: Down ask = 1-Up bid, Down bid = 1-Up ask."""
    if state is None:return None
    if side=='Up':ap,az,bp,bz=state['ask_prices'],state['ask_sizes'],state['bid_prices'],state['bid_sizes']
    else:ap,az,bp,bz=[round(1-p,6) for p in state['bid_prices']],state['bid_sizes'],[round(1-p,6) for p in state['ask_prices']],state['ask_sizes']
    return {'valid':True,'ask_prices':list(ap or []),'ask_sizes':list(az or []),'bid_prices':list(bp or []),'bid_sizes':list(bz or [])}

def rows_x(r):
    x={k:float(r[k]) for k in REF};x['stage_fraction']=r['offset']/300;return x

def load_features():
    ch=pl.read_parquet(RESEARCH/'btc5m-v2-extended/reference-features.parquet').filter(pl.col('label_up').is_not_null()&~pl.col('market_slug').is_in(list(QUARANTINE)))
    px=pl.read_parquet(RESEARCH/'proxy-backfill/export/binance1s_twap_regime_20260808_20260908.parquet').filter(~pl.col('market_slug').is_in(ch['market_slug'].unique().to_list()))
    real=[(r['market_slug'],int(r['start_s']),int(r['offset']),rows_x(r),float(r['label_up']),1.) for r in ch.iter_rows(named=True)]
    proxy=[(r['market_slug'],int(r['start_s']),int(r['offset']),rows_x(r),float(r['label_up']),.5) for r in px.iter_rows(named=True)]
    return real,proxy

def params(task,root=Path('D:/POLYMARKET/trainer')):
    best=json.loads((root/'tasks'/task/'best.json').read_text(encoding='utf-8')) if (root/'tasks'/task/'best.json').exists() else {}
    p=dict(best.get('params') or {}) if best.get('family')=='forest' else {}
    return {'n_estimators':100,'max_depth':7,'min_samples_leaf':30,'max_features':1.,**p}

def fit(rows,task,cutoff,seed):
    use=[r for r in rows if r[1]+300<cutoff-600 and (task=='direction' or r[2]>=240)]
    F=design(task);x=np.array([vector(r[3],F) for r in use]);y=np.array([r[4] for r in use]);w=np.array([r[5] for r in use])
    model=RandomForestClassifier(**params(task),random_state=seed,n_jobs=8).fit(x,y,sample_weight=w)
    return model,F,len(use)

def entry(book,slug,start,offset,p_up):
    """Best side by edge over its executable average price at the decision moment; executed 0.3s later."""
    decision=book.get((slug,(start+offset)*1_000_000));later=book.get((slug,round((start+offset+.3)*1e6)))
    best=None
    for side,prob in (('Up',p_up),('Down',1-p_up)):
        quote=buy(side_state(decision,side),LIMIT)
        if quote and prob-float(quote['total']/quote['shares'])>EDGE and (best is None or prob-float(quote['total']/quote['shares'])>best[1]):best=(side,prob-float(quote['total']/quote['shares']))
    if not best:return None
    fill=buy(side_state(later,best[0]),LIMIT)
    return dict(fill,side=best[0],offset=offset) if fill else None

def settle(position,label_up):
    win=(position['side']=='Up')==(label_up==1)
    return float(position['shares']*(1 if win else 0)-position['total'])

def mid_of(state):
    if not state:return None
    bid=state['bid'] if state['bid'] is not None else (max(state['bid_prices']) if state['bid_prices'] else None)
    ask=state['ask'] if state['ask'] is not None else (min(state['ask_prices']) if state['ask_prices'] else None)
    if bid is None and ask is None:return None
    return ((bid if bid is not None else ask-.01)+(ask if ask is not None else bid+.01))/2

def logit(p):
    p=min(max(p,1e-3),1-1e-3);return math.log(p/(1-p))

def simulate(book,markets,prob,trades,day,labels):
    for slug,start in markets:
        probs=prob.get(slug,{});label=labels[slug]
        if not probs:continue
        for o in (240,270):
            if o in probs and (pos:=entry(book,slug,start,o,probs[o])):trades['A_late_only'].append((slug,day,settle(pos,label),'settle'));break
        pos=None
        for o in (60,120,210):
            if o in probs and (pos:=entry(book,slug,start,o,probs[o])):break
        if not pos:continue
        trades['B_direction_only'].append((slug,day,settle(pos,label),'settle'))
        for name in ('C_coordinated_ev_exit','D_coordinated_take_profit'):
            result=None
            for o in CHECKPOINTS:
                if o<=pos['offset'] or o not in probs:continue
                p_side=probs[o] if pos['side']=='Up' else 1-probs[o]
                now=liquidate(side_state(book.get((slug,(start+o)*1_000_000)),pos['side']),pos)
                if now is None:continue
                value_now=float(now['gross']-now['exit_fees']);hold=p_side*float(pos['shares'])
                go=value_now>hold if name.startswith('C') else (now['pnl']>=TAKE_PROFIT and p_side<RISK_P)
                if go:
                    done=liquidate(side_state(book.get((slug,round((start+o+.3)*1e6))),pos['side']),pos)
                    if done is not None:result=(float(done['pnl']),f'exit@{o}');break
            trades[name].append((slug,day,*(result or (settle(pos,label),'settle'))))

def summary(trades,days):
    out={}
    for name,t in trades.items():
        pnl=np.array([v for _,_,v,_ in t]);rng=np.random.default_rng(0)
        boot=[pnl[rng.integers(0,len(pnl),len(pnl))].sum() for _ in range(2000)] if len(pnl) else [0]
        out[name]={'trades':len(t),'wins':int((pnl>0).sum()),'pnl':round(float(pnl.sum()),2),'roi':round(float(pnl.sum())/(5*len(t)),4) if len(t) else None,
            'pnl_95ci':[round(float(np.percentile(boot,2.5)),2),round(float(np.percentile(boot,97.5)),2)],'early_exits':sum(1 for *_,how in t if how!='settle'),
            'by_day':{str(d):round(float(sum(v for _,dd,v,_ in t if dd==d)),2) for d in days}}
    return out

def run(first,last):
    book=books();real,proxy=load_features();rows=real+proxy
    feats={(s,o):(x,y) for s,st,o,x,y,_ in real};labels={s:y for s,st,o,x,y,_ in real}
    days=pl.date_range(datetime.fromisoformat(first),datetime.fromisoformat(last),'1d',eager=True)
    kinds=('A_late_only','B_direction_only','C_coordinated_ev_exit','D_coordinated_take_profit')
    raw={k:[] for k in kinds};aware={k:[] for k in kinds};history=[];log=[]
    from sklearn.linear_model import LogisticRegression
    for day in days:
        d0=int(datetime(day.year,day.month,day.day,tzinfo=timezone.utc).timestamp())
        md,Fd,nd=fit(rows,'direction',d0,1);ml,Fl,nl=fit(rows,'late',d0,1)
        markets=sorted({(s,st) for s,st,o,x,y,_ in real if d0<=st<d0+86400})
        model_p={};today=[]
        for slug,start in markets:
            for o in CHECKPOINTS:
                if (slug,o) not in feats:continue
                x,label=feats[(slug,o)];p=float((ml if o>=240 else md).predict_proba([vector(x,Fl if o>=240 else Fd)])[0,1])
                model_p.setdefault(slug,{})[o]=p
                m=mid_of(book.get((slug,(start+o)*1_000_000)))
                if m is not None:today.append((slug,o,p,m,label))
        simulate(book,markets,model_p,raw,day,labels)
        # Market-aware coordinator: logistic regression on [logit(model p), logit(market mid)] per checkpoint group,
        # fitted only on earlier days' out-of-sample predictions. Needs two earlier days.
        coordinated=None
        if len({d for d,*_ in history})>=2:
            aware_p={}
            for group in ((60,120,210),(240,270)):
                past=[h for h in history if h[2] in group]
                X=np.array([[logit(h[3]),logit(h[4])] for h in past]);Y=np.array([h[5] for h in past])
                lr=LogisticRegression(C=1.).fit(X,Y)
                for slug,o,p,m,label in today:
                    if o in group:aware_p.setdefault(slug,{})[o]=float(lr.predict_proba([[logit(p),logit(m)]])[0,1])
            simulate(book,markets,aware_p,aware,day,labels);coordinated=len(aware_p)
        history+=[(day,slug,o,p,m,label) for slug,o,p,m,label in today]
        log.append({'day':str(day),'markets':len(markets),'direction_train_rows':nd,'late_train_rows':nl,'market_aware_markets':coordinated})
        print(log[-1],flush=True)
    report={'scope':'paper only: walk-forward models (trained strictly before each day), reconstructed PMData books, official fee formula 7%*p*(1-p), $5 per trade, entry edge > 3pp over executable average price, execution at decision+0.3s; not exchange fills',
            'days':log,'model_probability':summary(raw,days),'market_aware_probability':summary(aware,days)}
    out=RESEARCH/'coordinated-backtest';out.mkdir(exist_ok=True)
    (out/'report.json').write_text(json.dumps(report,indent=1,ensure_ascii=False),encoding='utf-8')
    pl.DataFrame([{'probability':kind,'strategy':k,'slug':s,'day':str(d),'pnl':v,'exit':how} for kind,src in (('model',raw),('market_aware',aware)) for k,t in src.items() for s,d,v,how in t]).write_csv(out/'trades.csv')
    for kind in ('model_probability','market_aware_probability'):
        print('==',kind)
        for name,v in report[kind].items():print(' ',name,{k:v[k] for k in ('trades','wins','pnl','roi','pnl_95ci','early_exits')})

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--first',default='2026-08-11');a.add_argument('--last',default='2026-08-21');args=a.parse_args()
    run(args.first,args.last)
