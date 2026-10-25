"""Diagnostic fixed-policy comparison; these dates are not a new blind test."""
import sys,json
from pathlib import Path
from decimal import Decimal as D
import polars as pl
SOURCE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SOURCE))
from poly5.roi_model import ROIModel,FEATURES
from research_features import ROOT
from research_execution import buy,policy_exit
from research_train_roi import load_states

policies=[{'id':'original','mean':.02,'q20':.01}, {'id':'revised','mean':.005,'q20':-.05},
          {'id':'diagnostic_no_quantile_filter_NOT_ENABLED','mean':.005,'q20':-100}]
OUT=ROOT.parent/'orderbook-roi-v1'
(OUT/'comparison-predeclared.json').write_text(json.dumps(policies),encoding='utf-8')
data=pl.read_parquet(ROOT/'roi-dataset.parquet').filter(pl.col('split').is_in(['validation','test']))
model=ROIModel();rows=data.to_dicts();preds=[model.predict(r) for r in rows]
needed={(r['market_slug'],r['stage']) for r,p in zip(rows,preds) if any(x['mean_roi']>=.005 for x in p)}
states=load_states(needed);reports=[]
for policy in policies:
    for split in ['validation','test']:
        ledger=[];signals=0;unfilled=0;net=D(0)
        for row,pred in zip(rows,preds):
            if row['split']!=split or net<=-10:continue
            options=[p for p in pred if p['mean_roi']>=policy['mean'] and p['q20_roi']>=policy['q20']]
            if not options:continue
            signals+=1;selected=max(options,key=lambda p:p['mean_roi']);at=row['decision_us']
            books=states[(row['market_slug'],row['stage'])];position=buy(books.get(at+300000),D(str(row['limit'])))
            if not position:unfilled+=1;continue
            target=(min(.05,max(.01,selected['q20_roi'])) if policy['id']=='original' else min(.02,max(.003,selected['mean_roi']*.5)))
            outcome=policy_exit(books,position,D(str(target)),D('-.05'),selected['horizon_s'],.3,at);net+=outcome['pnl']
            ledger.append({'slug':row['market_slug'],'stage':row['stage'],'entry_cost':position['total'],**outcome})
        cost=sum((t['entry_cost'] for t in ledger),D(0))
        reports.append({'policy':policy,'split':split,'signals':signals,'unfilled':unfilled,'round_trips':len(ledger),
                        'net_pnl_usd':net,'aggregate_roi':net/cost if cost else None,'ledger':ledger})
result={'scope':'same historic validation/test dates, already used in research; fixed 60/120s opportunities only, not continuous scanning or a fresh blind test',
        'fee_and_depth_included':True,'reports':reports}
(OUT/'policy-comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,default=str))
