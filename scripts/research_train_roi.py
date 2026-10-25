"""Train offline net-ROI candidates; freeze validation choices before opening test metrics."""
import json,hashlib
from decimal import Decimal as D
from pathlib import Path
import joblib,numpy as np,polars as pl
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_squared_error,mean_pinball_loss
from research_features import ROOT
from research_roi_dataset import FEATURES,HORIZONS
from research_execution import buy,liquidate,policy_exit

def metric(y,p,q):
    return {'n':len(y),'mean_label_roi':float(np.mean(y)),'positive_label_rate':float(np.mean(y>0)),
            'mse':float(mean_squared_error(y,p)),'q20_pinball':float(mean_pinball_loss(y,q,alpha=.2)),
            'q20_observed_below_rate':float(np.mean(y<q)),'predicted_mean_roi':float(np.mean(p))}

def load_states(required):
    states={}
    if not required:return states
    keys=pl.DataFrame([{'market_slug':s,'stage':t} for s,t in required])
    for path in sorted((ROOT/'books').glob('*.parquet')):
        subset=pl.scan_parquet(path).join(keys.lazy(),on=['market_slug','stage'],how='semi').collect()
        for row in subset.to_dicts():states.setdefault((row['market_slug'],row['stage']),{})[row['at_us']]=row
    return states

def simulate(frame,predictions,states,delay):
    ledger=[];signals=0;no_fill=0;realized=D(0);halted=False
    for index,row in enumerate(frame.to_dicts()):
        if halted:break
        options=[]
        for horizon,(p,q) in predictions.items():
            if q[index]>=.01 and p[index]>=.02:options.append((p[index],horizon,q[index]))
        if not options:continue
        _,horizon,lower=max(options);signals+=1
        book=states[(row['market_slug'],row['stage'])];at=row['decision_us']
        position=buy(book.get(at+round(delay*1e6)),D(str(row['limit'])))
        if position is None:no_fill+=1;continue
        target=D(str(min(.05,max(.01,lower))))
        outcome=policy_exit(book,position,target,D('-.05'),horizon,delay,at)
        realized+=outcome['pnl']
        if realized<=D('-10') or outcome['reason']=='exit_unavailable_loss_bound':halted=True
        ledger.append({'market_slug':row['market_slug'],'day':row['day'],'decision_us':at,'horizon':horizon,
                       'target_roi':str(target),'entry_cost':str(position['total']),'shares':str(position['shares']),
                       'pnl':str(outcome['pnl']),'roi':str(outcome['roi']),'exit_reason':outcome['reason'],
                       'planned_hold_seconds':outcome['elapsed'],'actual_hold_seconds':outcome['elapsed']+delay,
                       'decision_to_exit_seconds':outcome['elapsed']+2*delay})
    profits=np.array([float(x['pnl']) for x in ledger]);cost=sum(float(x['entry_cost']) for x in ledger)
    equity=np.r_[0,np.cumsum(profits)];drawdown=float(np.max(np.maximum.accumulate(equity)-equity))
    summary={'eligible_rows':frame.height,'signals':signals,'entry_unfilled':no_fill,'simulated_round_trips':len(ledger),
             'sum_entry_cost':cost,'net_pnl_usd':float(profits.sum()),'aggregate_net_roi':float(profits.sum()/cost) if cost else None,
             'max_drawdown_usd':drawdown,'win_rate':float(np.mean(profits>0)) if len(profits) else None,
             'unavailable_exits':sum(x['exit_reason']=='exit_unavailable_loss_bound' for x in ledger),
             'day_pnl':{day:sum(float(x['pnl']) for x in ledger if x['day']==day) for day in sorted(frame['day'].unique())},
             'risk_halted':halted,'scope_limit':'historical hypothetical fills; $10 cumulative net-loss cutoff; unverified production latency'}
    return summary,ledger

def train():
    data=pl.read_parquet(ROOT/'roi-dataset.parquet');models_dir=ROOT/'models';models_dir.mkdir(exist_ok=True)
    all_frames={s:data.filter(pl.col('split')==s) for s in ('train','validation','test')}
    frames={s:f.filter(pl.col('entry_filled')) for s,f in all_frames.items()}
    # Never put a market in more than one partition.
    groups={s:set(f['market_slug']) for s,f in all_frames.items()}
    assert all(not groups[a]&groups[b] for a,b in [('train','validation'),('train','test'),('validation','test')])
    X={s:f.select(FEATURES).to_numpy() for s,f in frames.items()}
    Xall={s:f.select(FEATURES).to_numpy() for s,f in all_frames.items()}
    candidates=[];chosen={};validation_predictions={};fitted={}
    for h in HORIZONS:
        yt=frames['train'][f'roi_{h}s'].to_numpy();yv=frames['validation'][f'roi_{h}s'].to_numpy()
        params=dict(max_iter=100,max_leaf_nodes=7,min_samples_leaf=50,learning_rate=.05,l2_regularization=10,
                    early_stopping=False,random_state=507)
        quantile=HistGradientBoostingRegressor(loss='quantile',quantile=.2,**params).fit(X['train'],yt)
        qv=quantile.predict(X['validation']);options=[]
        for name,model in [('ridge',make_pipeline(StandardScaler(),Ridge(alpha=1))),
                           ('hist_gradient',HistGradientBoostingRegressor(loss='squared_error',**params))]:
            model.fit(X['train'],yt);p=model.predict(X['validation']);mse=float(mean_squared_error(yv,p))
            candidates.append({'horizon':h,'model':name,'validation_mse':mse});options.append((mse,name,model,p))
        _,name,model,p=min(options,key=lambda x:x[0]);chosen[h]=name;fitted[h]=(model,quantile)
        validation_predictions[h]=(p,qv)
        joblib.dump({'mean':model,'q20':quantile,'features':FEATURES,'deployment_allowed':False},models_dir/f'roi-{h}s.joblib')
    lock={'selected_by_validation':chosen,'candidates':candidates,'feature_names':FEATURES,
          'entry_rule':'q20 >= 0.01 and predicted mean >= 0.02; highest mean chooses horizon',
          'take_profit':'clip(predicted q20, 0.01, 0.05), fixed at entry','stop_loss':-.05,
          'max_hold_s':30,'delay_s':[.3,1.0],'deployment_allowed':False}
    (ROOT/'locked-selection.json').write_text(json.dumps(lock,indent=2),encoding='utf-8')
    # All choices are now fixed. Only now evaluate the chronological test partition.
    metrics=[];daily_metrics=[];test_predictions={}
    for h,(model,qmodel) in fitted.items():
        p=model.predict(X['test']);q=qmodel.predict(X['test']);test_predictions[h]=(p,q)
        for s,pair in [('validation',validation_predictions[h]),('test',(p,q))]:
            y=frames[s][f'roi_{h}s'].to_numpy();m=metric(y,*pair)
            train_mean=float(frames['train'][f'roi_{h}s'].mean())
            m.update(split=s,horizon=h,model=chosen[h],constant_baseline_mse=float(mean_squared_error(y,np.full(len(y),train_mean))))
            metrics.append(m)
            for day in sorted(frames[s]['day'].unique()):
                mask=frames[s]['day'].to_numpy()==day
                daily=metric(y[mask],pair[0][mask],pair[1][mask])
                daily.update(split=s,day=day,horizon=h,model=chosen[h],
                             constant_baseline_mse=float(mean_squared_error(y[mask],np.full(int(mask.sum()),train_mean))))
                daily_metrics.append(daily)
    all_predictions={s:{h:(m.predict(Xall[s]),q.predict(Xall[s])) for h,(m,q) in fitted.items()} for s in ('validation','test')}
    required=set()
    for s,pred in all_predictions.items():
        active=np.any(np.stack([(q>=.01)&(p>=.02) for p,q in pred.values()]),axis=0)
        required.update((x['market_slug'],x['stage']) for x,yes in zip(all_frames[s].to_dicts(),active) if yes)
    states=load_states(required);policy={};ledgers=[]
    for s in ('validation','test'):
        pred=all_predictions[s]
        for delay in (.3,1.):
            summary,ledger=simulate(all_frames[s],pred,states,delay);policy[f'{s}_{delay}s']=summary
            ledgers.extend(dict(x,split=s,delay=delay) for x in ledger)
    result={'research_only':True,'deployment_allowed':False,'selected_models':chosen,'metrics':metrics,'daily_metrics':daily_metrics,'policy':policy,
            'limitations':['eight historical days, two held-out days','Up-side only','hypothetical fills, not exchange receipts',
                          'return regression conditions on executable entries; policy simulation counts unfilled signals',
                          'receive-time reference data often over one second old','current reference feed authorization not connected',
                          'no queue position, own-order impact, or settlement liquidity modeling','no independent forward paper validation']}
    (ROOT/'training-results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    pl.DataFrame(metrics).write_csv(ROOT/'model-metrics.csv')
    pl.DataFrame(daily_metrics).write_csv(ROOT/'model-metrics-by-day.csv')
    (ROOT/'paper-ledger.json').write_text(json.dumps(ledgers,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__':train()
