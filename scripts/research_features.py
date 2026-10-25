"""Causal BTC5m features, independent of desktop and order APIs."""
from pathlib import Path
from datetime import datetime, timezone
import json
import numpy as np
import polars as pl

# POLY5_RESEARCH_ROOT lets research_books/research_roi_dataset run on another archive (e.g. btc5m-v2-extended).
ROOT=Path(__import__('os').environ.get('POLY5_RESEARCH_ROOT','D:/POLYMARKET/research/btc5m-v1'))
OFFSETS=(60,120,210,240,270)
REFERENCE_FEATURES=['spot_open_bps','twap_open_bps','spot_twap_bps','return_5s_bps',
                    'return_15s_bps','return_60s_bps','vol_60s_bps']

def asof_index(times,query):
    """Never reads the next/future row. Caller must reject -1 and stale values."""
    return np.searchsorted(times,query,side='right')-1

def split_for(start_s):
    date=datetime.fromtimestamp(start_s,timezone.utc).strftime('%Y-%m-%d')
    # Purge 10 minutes at each split boundary, including all labels in that gap.
    for boundary in ('2026-08-19','2026-08-20'):
        stamp=int(datetime.fromisoformat(boundary).replace(tzinfo=timezone.utc).timestamp())
        if stamp-600<=start_s<stamp+600:return 'purged'
    if date<'2026-08-19':return 'train'
    if date<'2026-08-20':return 'validation'
    return 'test'

def load_series(kind,root=None):
    d=pl.read_parquet(str((root or ROOT)/f'raw/{kind}/*/*.parquet')).select(
        pl.col('observationsTimestamp').cast(pl.Int64).alias('obs'),
        pl.col('receiveMicrosecondTimestamp').cast(pl.Int64).alias('recv'),
        pl.col('price').cast(pl.Float64).truediv(1e18).alias('price'))
    profile={'rows':d.height,'duplicate_observation_rows':d.height-d['obs'].n_unique(),
             'conflicting_observations':d.group_by('obs').agg(pl.col('price').n_unique()).filter(pl.col('price')>1).height,
             'future_observations':d.filter(pl.col('obs')>pl.col('recv')).height,
             'nonpositive_prices':d.filter(pl.col('price')<=0).height}
    assert profile['conflicting_observations']==0 and profile['nonpositive_prices']==0
    d=d.filter(pl.col('obs')<=pl.col('recv')).sort('recv').unique('obs',keep='first',maintain_order=True)
    # An older observation delivered late must not replace a fresher known value.
    obs=d['obs'].to_numpy();keep=np.r_[True,obs[1:]>np.maximum.accumulate(obs)[:-1]]
    d=d.filter(pl.Series(keep));obs=d['obs'].to_numpy();recv=d['recv'].to_numpy();price=d['price'].to_numpy()
    profile.update(rows_after_causal_dedup=d.height,lag_seconds_quantiles=np.quantile((recv-obs)/1e6,[.5,.95,.99,1]).tolist(),
                   observation_gaps_over_5s=int(np.sum(np.diff(obs)>5e6)))
    return (obs,recv,price),profile

def build(root=ROOT,since=datetime(2026,8,14,tzinfo=timezone.utc)):
    # Defaults reproduce the original btc5m-v1 archive; btc5m-v2-extended passes since=2026-08-08.
    ROOT=Path(root)
    (so,sr,sp),sq=load_series('chainlink_streams',ROOT)
    (to,tr,tp),tq=load_series('chainlink_streams_twap60s',ROOT)
    markets=pl.read_parquet(ROOT/'raw/markets.parquet').filter(pl.col('start_time')>=since)
    assert markets['market_slug'].n_unique()==markets.height
    rows=[];rejected=[];audit=[]
    for m in markets.iter_rows(named=True):
        start=int(m['start_time'].timestamp());end=int(m['end_time'].timestamp())
        assert end-start==300
        oi=asof_index(to,start*1_000_000);ei=asof_index(to,end*1_000_000)
        match=None
        if oi>=0 and ei>=0 and (start*1e6-to[oi])<=5e6 and (end*1e6-to[ei])<=5e6:
            match=('yes' if tp[ei]>=tp[oi] else 'no')==m['winning_outcome'] if m['winning_outcome'] else None
        audit.append({'market_slug':m['market_slug'],'day':m['start_time'].date().isoformat(),
                      'winning_outcome':m['winning_outcome'],'twap_matches_recorded':match})
        for offset in OFFSETS:
            decision=(start+offset)*1_000_000;reason=None
            si=asof_index(sr,decision);ti=asof_index(tr,decision)
            past=asof_index(sr,decision-np.arange(60,-1,-1)*1_000_000)
            if oi<0 or tr[oi]>decision or start*1e6-to[oi]>5e6:reason='opening_twap_unavailable'
            elif si<0 or ti<0 or decision-so[si]>5e6 or decision-to[ti]>5e6:reason='reference_stale'
            elif np.any(past<0) or np.any(decision-np.arange(60,-1,-1)*1e6-so[past]>5e6):reason='history_gap'
            if reason:
                rejected.append({'market_slug':m['market_slug'],'offset':offset,'reason':reason});continue
            prices=sp[past];opening=tp[oi]
            row={'market_slug':m['market_slug'],'start_s':start,'decision_us':decision,'offset':offset,
                 'split':split_for(start),'day':m['start_time'].date().isoformat(),'label_up':int(m['winning_outcome']=='yes') if m['winning_outcome'] in ('yes','no') else None,
                 'spot_open_bps':(sp[si]/opening-1)*1e4,'twap_open_bps':(tp[ti]/opening-1)*1e4,
                 'spot_twap_bps':(sp[si]/tp[ti]-1)*1e4,
                 'return_5s_bps':np.log(prices[-1]/prices[-6])*1e4,
                 'return_15s_bps':np.log(prices[-1]/prices[-16])*1e4,
                 'return_60s_bps':np.log(prices[-1]/prices[0])*1e4,
                 'vol_60s_bps':np.std(np.diff(np.log(prices)),ddof=1)*1e4,
                 'latest_feature_receive_us':int(max(sr[si],tr[ti],tr[oi])),
                 'spot_age_s':(decision-so[si])/1e6,'twap_age_s':(decision-to[ti])/1e6}
            assert row['latest_feature_receive_us']<=decision
            rows.append(row)
    pl.DataFrame(rows).write_parquet(ROOT/'reference-features.parquet')
    pl.DataFrame(rejected).write_csv(ROOT/'feature-rejections.csv')
    pl.DataFrame(audit).write_csv(ROOT/'label-audit.csv')
    quality={'scope':since.date().isoformat()+' through 2026-08-21 UTC, one row per market and fixed decision offset',
             'markets':markets.height,'missing_labels':markets['winning_outcome'].null_count(),
             'twap_matches':sum(x['twap_matches_recorded'] is True for x in audit),
             'twap_disagreements':[x for x in audit if x['twap_matches_recorded'] is False],
             'accepted_feature_rows':len(rows),'rejected_feature_rows':len(rejected),'spot':sq,'twap':tq,
             'split_counts':pl.DataFrame(rows).group_by('split').len().to_dicts()}
    (ROOT/'quality.json').write_text(json.dumps(quality,indent=2),encoding='utf-8')
    print(json.dumps(quality,indent=2))

if __name__=='__main__':
    import argparse
    a=argparse.ArgumentParser();a.add_argument('--root',default=str(ROOT));a.add_argument('--since',default='2026-08-14');args=a.parse_args()
    build(args.root,datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc))
