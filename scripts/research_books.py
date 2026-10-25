"""Reconstruct sparse receive-time L2 states for the 1-30 second ROI experiment."""
from pathlib import Path
import json, time
import polars as pl
from research_features import ROOT

STAGES=(60,120)

def targets(stage):
    values={stage-5,stage-1,stage,stage+.3,stage+1}
    for delay in (.3,1.):
        for h in range(1,31):values.update((stage+delay+h,stage+2*delay+h))
    return sorted(round(x*1e6) for x in values)

def checkpoint_targets(stage):
    # Coordinated-strategy backtest: decision moment and the moment an order sent then would execute (+0.3s).
    return [round(stage*1e6),round((stage+.3)*1e6)]

def extract(path,stages=STAGES,target_fn=None,folder='books'):
    target_fn=target_fn or targets
    day=path.parent.name.split('=')[1];out=ROOT/folder/f'{day}.parquet';out.parent.mkdir(exist_ok=True)
    if out.exists():return
    started=time.monotonic()
    raw=pl.scan_parquet(path).with_columns(pl.col('local_timestamp').cast(pl.Int64).alias('recv'),pl.col('timestamp').cast(pl.Int64).alias('event_us'))
    columns=['market_slug','recv','event_us','event_type','ask_prices','ask_sizes','bid_prices','bid_sizes','best_ask','best_bid','pc_price','pc_size','pc_side']
    snapshots=raw.filter(pl.col('event_type')=='book').select(columns).collect()
    windows=[]
    for key,b in snapshots.partition_by('market_slug',as_dict=True).items():
        slug=key[0];start=int(slug.rsplit('-',1)[1])*1_000_000;b=b.sort('recv')
        for stage in stages:
            initial=b.filter(pl.col('recv')<=start+(stage-5)*1_000_000)
            if initial.is_empty():continue
            first=int(initial['recv'][-1])
            # Snapshot before the requested interval plus every intervening update.
            windows.append({'market_slug':slug,'stage':stage,'lo':first,'hi':start+(stage+32)*1_000_000})
    w=pl.DataFrame(windows)
    data=(raw.select(columns).join(w.lazy(),on='market_slug').filter(
        (pl.col('recv')>=pl.col('lo'))&(pl.col('recv')<=pl.col('hi'))&pl.col('event_type').is_in(['book','price_change']))
        .sort(['market_slug','stage','recv','event_us']).collect(engine='streaming'))
    print(day,'selected rows',data.height,'snapshots',snapshots.height,flush=True)
    states=[];mismatch=0;empty=0;late=0
    # Tuples avoid a Python dict per tick; retain all depth levels at sampled times.
    cols=['recv','event_us','event_type','ask_prices','ask_sizes','bid_prices','bid_sizes','best_ask','best_bid','pc_price','pc_size','pc_side']
    for key,group in data.partition_by(['market_slug','stage'],as_dict=True).items():
        slug,stage=key;start=int(slug.rsplit('-',1)[1])*1_000_000
        bids={};asks={};known_bid=None;known_ask=None;last_event=0;last_recv=0;last_exchange=0
        ticks=iter(group.select(cols).iter_rows());tick=next(ticks,None)
        for offset_us in target_fn(stage):
            at=start+offset_us
            while tick is not None and tick[0]<=at:
                recv,evt,typ,ap,az,bp,bz,ba,bb,price,size,side=tick
                if evt<last_exchange:
                    late+=1;tick=next(ticks,None);continue
                if typ=='book':
                    bids={p:s for p,s in zip(bp or [],bz or []) if s>0};asks={p:s for p,s in zip(ap or [],az or []) if s>0}
                    known_bid=max(bids,default=None);known_ask=min(asks,default=None)
                elif price is not None and size is not None and side in ('BUY','SELL'):
                    book=bids if side=='BUY' else asks
                    if size==0:book.pop(price,None)
                    else:book[price]=size
                    known_bid=bb;known_ask=ba
                last_event=evt;last_exchange=evt;last_recv=recv;tick=next(ticks,None)
            bid=max(bids,default=None);ask=min(asks,default=None)
            valid=bid is not None and ask is not None and 0<bid<ask<1
            match=valid and known_bid is not None and known_ask is not None and abs(bid-known_bid)<1e-8 and abs(ask-known_ask)<1e-8
            reason=''
            if not valid:reason='invalid_or_empty_book';empty+=1
            elif not match:reason='reconstructed_top_mismatch';mismatch+=1
            elif at-last_event>2_000_000 or last_event>at:reason='stale_book'
            levels_b=sorted(bids.items(),reverse=True);levels_a=sorted(asks.items())
            states.append({'market_slug':slug,'stage':stage,'at_us':at,'last_receive_us':last_recv,'last_event_us':last_event,
                           'valid':not reason,'reason':reason,'bid':bid,'ask':ask,
                           'bid_prices':[p for p,s in levels_b],'bid_sizes':[s for p,s in levels_b],
                           'ask_prices':[p for p,s in levels_a],'ask_sizes':[s for p,s in levels_a]})
    pl.DataFrame(states).write_parquet(out)
    profile={'day':day,'raw_selected_rows':data.height,'snapshot_rows':snapshots.height,'states':len(states),
             'mismatch_states':mismatch,'empty_states':empty,'late_rows_ignored':late,'seconds':time.monotonic()-started,
             'valid_states':sum(x['valid'] for x in states)}
    out.with_suffix('.json').write_text(json.dumps(profile,indent=2),encoding='utf-8');print(profile,flush=True)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--day');p.add_argument('--checkpoints',action='store_true');a=p.parse_args()
    for path in sorted((ROOT/'raw/l2').glob(f'date={a.day or "*"}/*.parquet')):
        if a.checkpoints:extract(path,(60,120,210,240,270),checkpoint_targets,'checkpoint-books')
        else:extract(path)
