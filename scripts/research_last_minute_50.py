"""User hypothesis: last minute, >= $50 from opening baseline, hold to resolution."""
from datetime import datetime,timezone
from decimal import Decimal as D
import json
from pathlib import Path
import sys
import numpy as np
import polars as pl
from research_features import ROOT,load_series,asof_index
from research_blind_round import reconstruct,digest
SOURCE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SOURCE))
from poly5.common import dumps
from poly5.domain import Book,Market,Fee,preview_buy_notional,DataError


def run():
    out=ROOT.parent/('last-minute-50-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));out.mkdir()
    manifest={'hypothesis':'last minute: buy side >= $50 away from opening baseline, hold to recorded outcome',
        'scan_offsets_s':list(range(240,286)),'distance_usd':'50','variants':['twap','spot'],
        'baseline':'Chainlink TWAP60 at/before start, <=5s old and received before decision',
        'contract_notional_usd':'5','fees_separate':True,'current_fee_rate':'.07','entry_latency_ms':300,
        'entry_limit':'decision ask + .01, capped at .99; minimum 5 shares; full $5 notional required',
        'sample':'all archived 2026-08-14..21 markets, previously studied data; exploratory, not blind',
        'scope':'both directions for outcome hit rate; Up only for fill/PnL because Down depth absent',
        'script_sha256':digest(Path(__file__)),'real_orders':0}
    (out/'preregistered.json').write_text(dumps(manifest,indent=2),encoding='utf-8')
    print('LOCKED',out,flush=True)
    twap,_=load_series('chainlink_streams_twap60s');spot,_=load_series('chainlink_streams')
    identities=pl.read_parquet(ROOT/'raw/markets.parquet',columns=['market_slug','start_time'])
    candidates=[];eligible_markets=0
    for m in identities.iter_rows(named=True):
        day=m['start_time'].date().isoformat()
        if not '2026-08-14'<=day<='2026-08-21':continue
        eligible_markets+=1;start=int(m['start_time'].timestamp());opening=asof_index(twap[0],start*1000000)
        if opening<0 or start*1000000-twap[0][opening]>5000000:continue
        baseline=D(str(twap[2][opening]))
        for kind,series in [('twap',twap),('spot',spot)]:
            for offset in manifest['scan_offsets_s']:
                now=(start+offset)*1000000;at=asof_index(series[1],now)
                if at<0 or now-series[0][at]>5000000 or twap[1][opening]>now:continue
                delta=D(str(series[2][at]))-baseline
                if abs(delta)>=50:
                    candidates.append({'variant':kind,'slug':m['market_slug'],'day':day,'offset_s':offset,
                        'decision_us':now,'baseline':baseline,'current_reference':D(str(series[2][at])),
                        'distance_usd':delta,'outcome':'Up' if delta>0 else 'Down','latest_receive_us':int(max(twap[1][opening],series[1][at]))})
                    break
    (out/'locked-candidates.json').write_text(dumps(candidates,indent=2),encoding='utf-8')
    print('CANDIDATES',len(candidates),'markets',eligible_markets,'before labels',flush=True)
    # Outcome labels only enter after all first-crossing decisions are frozen.
    labels=pl.read_parquet(ROOT/'raw/markets.parquet',columns=['market_slug','winning_outcome'])
    outcomes={m['market_slug']:m['winning_outcome'] for m in labels.iter_rows(named=True)}
    by_slug={}
    for row in candidates:by_slug.setdefault(row['slug'],[]).append(row)
    filled=[]
    for counter,(slug,rows) in enumerate(sorted(by_slug.items()),1):
        up=[r for r in rows if r['outcome']=='Up']
        states=None
        if up:
            values,_=reconstruct(next((ROOT/'raw/l2'/('date='+rows[0]['day'])).glob('*.parquet')),slug,continuous=True)
            states={s['at_us']:s for s in values}
        for row in rows:
            label=outcomes.get(slug);row['recorded_outcome']=label
            row['correct']=None if label not in ('yes','no') else (row['outcome']=='Up')==(label=='yes')
            assert row['latest_receive_us']<=row['decision_us']
            if row['outcome']=='Down':row['fill_status']='Down depth unavailable';continue
            at=row['decision_us'];decision=states[at];actual=states.get(at+300000)
            if not decision['valid'] or not actual or not actual['valid']:
                row['fill_status']='invalid or stale book';continue
            start=int(slug.rsplit('-',1)[1]);m=Market(slug,'btc5m',slug,'historical','historical-'+slug,{'Up':'historical-up','Down':'historical-down'},start,start+300,True,D(5),D('.01'),Fee(D('.07'),1),'archived Chainlink','research',True)
            b=Book('historical-up',m.condition_id)
            b.bids={D(str(p)):D(str(q)) for p,q in zip(actual['bid_prices'],actual['bid_sizes'])}
            b.asks={D(str(p)):D(str(q)) for p,q in zip(actual['ask_prices'],actual['ask_sizes'])}
            b.source_ms=actual['last_event_us']//1000;b.received_ms=actual['last_receive_us']//1000;b.initialized=True;b.check()
            if not b.fresh((at+300000)//1000):row['fill_status']='stale execution book';continue
            try:buy=preview_buy_notional(b,m,'5',str(min(D('.99'),D(str(decision['ask']))+D('.01'))))
            except DataError as exc:row['fill_status']=str(exc);continue
            row['fill_status']='simulated full $5 notional';row['entry']=buy
            if row['correct'] is not None:
                payout=buy['shares'] if row['correct'] else D(0)
                row['payout']=payout;row['net_pnl']=payout-buy['total'];filled.append(row)
        if counter%50==0:print('PROGRESS',counter,'/',len(by_slug),flush=True)
    summary={}
    for kind in manifest['variants']:
        rows=[r for r in candidates if r['variant']==kind];known=[r for r in rows if r['correct'] is not None]
        fills=[r for r in filled if r['variant']==kind]
        summary[kind]={'signal_markets':len(rows),'known_outcomes':len(known),'direction_wins':sum(r['correct'] for r in known),
            'direction_losses':sum(not r['correct'] for r in known),'up_filled':len(fills),
            'up_fill_wins':sum(r['correct'] for r in fills),'up_fill_losses':sum(not r['correct'] for r in fills),
            'up_net_pnl':sum((r['net_pnl'] for r in fills),D(0)),
            'up_total_cost':sum((r['entry']['total'] for r in fills),D(0)),
            'counterexamples':[r for r in rows if r['correct'] is False][:5]}
    assert digest(Path(__file__))==manifest['script_sha256']
    (out/'result.json').write_text(dumps({'manifest':manifest,'markets':eligible_markets,'summary':summary,'candidates':candidates},indent=2),encoding='utf-8')
    text='# 最后一分钟、偏离基准 $50：探索性检查\n\n使用既有八天全部市场，先锁定首次信号再读取结果。现货与 TWAP 两种口径分别检查，相对本轮起始 TWAP60 重建基准；历史基准不等同于逐轮归档的官方页面字段。\n\n第 240–285 秒扫描；最后 15 秒不新增风险。每次足额买入 $5 合约、手续费另计，按 300ms 后真实深度、最小 5 份和限价判断能否买满。仅 Up 有完整深度，Down 只统计方向结果，不虚构净收益。使用当前费率重新计算历史交易。整轮结束后按档案记录结果模拟兑付，不冒充实际成交或当时结算时延。\n\n'
    for kind,s in summary.items():text+=f"- {kind}：方向 {s['direction_wins']} 对 / {s['direction_losses']} 错，共 {s['known_outcomes']} 个已知结果；Up 足额模拟买入 {s['up_filled']} 笔，净收益 ${s['up_net_pnl']}，含费投入 ${s['up_total_cost']}。\n"
    text+='\n同一窗口可能在两个口径各出现一次，两个实验不可相加当作独立收益。历史小范围的高命中率不能证明未来必赚；该档案此前已经研究，不能称为全新盲测。详细买价、数量、费用、无成交原因和反例见 result.json。\n'
    (out/'最后一分钟50美元检查.md').write_text(text,encoding='utf-8')
    print('COMPLETE',out,dumps(summary),flush=True)


if __name__=='__main__':run()
