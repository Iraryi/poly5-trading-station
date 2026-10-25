"""Build depth-aware, fee-inclusive ROI labels and retain all rejected attempts."""
import json
from decimal import Decimal as D
import polars as pl
import numpy as np
from research_features import ROOT,REFERENCE_FEATURES
from research_execution import buy,liquidate

HORIZONS=(1,3,5,10,15,30)
FEATURES=REFERENCE_FEATURES+['stage_fraction','mid','spread','imbalance5','microprice_offset',
                           'mid_change_1s','mid_change_5s','book_age_s']

def build():
    refs=pl.read_parquet(ROOT/'reference-features.parquet').filter(pl.col('offset').is_in([60,120]))
    mapping={(x['market_slug'],x['offset']):x for x in refs.to_dicts()}
    rows=[];rejects=[];bounds=0
    for path in sorted((ROOT/'books').glob('*.parquet')):
        books=pl.read_parquet(path)
        for key,group in books.partition_by(['market_slug','stage'],as_dict=True).items():
            slug,stage=key;ref=mapping.get(key)
            if ref is None:rejects.append({'market_slug':slug,'stage':stage,'reason':'reference_gap'});continue
            states={x['at_us']:x for x in group.to_dicts()};at=ref['decision_us'];s=states.get(at)
            prev1=states.get(at-1_000_000);prev5=states.get(at-5_000_000)
            if not s or not prev1 or not prev5 or not all(x['valid'] for x in [s,prev1,prev5]):
                rejects.append({'market_slug':slug,'stage':stage,'reason':'decision_book_invalid'});continue
            assert s['last_receive_us']<=at and prev1['last_receive_us']<=at-1e6 and prev5['last_receive_us']<=at-5e6
            bp=s['bid_prices'];bs=s['bid_sizes'];ap=s['ask_prices'];az=s['ask_sizes']
            bid_depth=sum(bs[:5]);ask_depth=sum(az[:5]);mid=(s['bid']+s['ask'])/2
            row={k:ref[k] for k in ['market_slug','day','split','decision_us']+REFERENCE_FEATURES}
            row.update(stage=stage,stage_fraction=stage/300,mid=mid,spread=s['ask']-s['bid'],
                       imbalance5=(bid_depth-ask_depth)/(bid_depth+ask_depth),
                       microprice_offset=(ap[0]*bs[0]+bp[0]*az[0])/(bs[0]+az[0])-mid,
                       mid_change_1s=mid-(prev1['bid']+prev1['ask'])/2,
                       mid_change_5s=mid-(prev5['bid']+prev5['ask'])/2,book_age_s=(at-s['last_event_us'])/1e6)
            limit=min(D('.99'),D(str(s['ask']))+D('.01'))
            position=buy(states.get(at+300_000),limit)
            if position is None:
                rejects.append({'market_slug':slug,'stage':stage,'reason':'entry_rejected_after_latency'})
                row.update(entry_filled=False,entry_cost=None,entry_shares=None,limit=float(limit))
                for horizon in HORIZONS:
                    row[f'roi_{horizon}s']=None;row[f'exit_available_{horizon}s']=False
                rows.append(row);continue
            row['entry_filled']=True
            row.update(entry_cost=float(position['total']),entry_shares=float(position['shares']),limit=float(limit))
            for horizon in HORIZONS:
                value=liquidate(states.get(at+round((horizon+.6)*1e6)),position)
                row[f'roi_{horizon}s']=float(value['roi']) if value else -1.
                row[f'exit_available_{horizon}s']=value is not None
                if value is None:bounds+=1
            rows.append(row)
    d=pl.DataFrame(rows).sort(['decision_us','market_slug'])
    assert d.select(pl.struct(['market_slug','stage']).n_unique()).item()==d.height
    assert np.isfinite(d.select(FEATURES).to_numpy()).all()
    d.write_parquet(ROOT/'roi-dataset.parquet');pl.DataFrame(rejects).write_csv(ROOT/'roi-rejections.csv')
    report={'rows':d.height,'markets':d['market_slug'].n_unique(),'rejected_attempts':len(rejects),'unavailable_exit_labels_at_loss_bound':bounds,
            'splits':d.group_by('split').agg(pl.len().alias('rows'),pl.col('market_slug').n_unique().alias('markets')).to_dicts(),
            'features':FEATURES,'horizons':HORIZONS,'truth':'hypothetical depth consumption, not confirmed exchange fills'}
    (ROOT/'roi-quality.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(report)

if __name__=='__main__':build()
