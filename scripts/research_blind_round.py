"""One preregistered, real-time, model-blind historical round; no order API."""
import hashlib,json,os,secrets,time
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal as D
import joblib,numpy as np,polars as pl
from research_features import ROOT
from research_roi_dataset import FEATURES,HORIZONS
from research_execution import buy,liquidate

OUT=ROOT.parent/('blind-round-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))

def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def reconstruct(raw,slug,continuous=False):
    start=int(slug.rsplit('-',1)[1])*1_000_000
    columns=['local_timestamp','timestamp','event_type','ask_prices','ask_sizes','bid_prices','bid_sizes','best_ask','best_bid','pc_price','pc_size','pc_side']
    df=(pl.scan_parquet(raw).filter(pl.col('market_slug')==slug).select(columns)
        .with_columns(pl.col('local_timestamp').cast(pl.Int64),pl.col('timestamp').cast(pl.Int64))
        .sort(['local_timestamp','timestamp']).collect())
    offsets=set(range(301))
    for stage in (range(5,271) if continuous else (60,120)):
        offsets.add(stage+.3)
        for hold in range(1,31):offsets.update([stage+.3+hold,stage+.6+hold])
    ticks=iter(df.iter_rows());tick=next(ticks,None)
    bids={};asks={};last_event=last_recv=0;known_bid=known_ask=None;states=[]
    for offset in sorted(x for x in offsets if x<=300):
        at=start+round(offset*1e6)
        while tick is not None and tick[0]<=at:
            recv,event,kind,ap,az,bp,bz,ka,kb,price,size,side=tick
            if event>=last_event:
                if kind=='book':
                    bids={p:s for p,s in zip(bp or [],bz or []) if s>0};asks={p:s for p,s in zip(ap or [],az or []) if s>0}
                    known_bid=max(bids,default=None);known_ask=min(asks,default=None)
                    last_event=event;last_recv=recv
                elif kind=='price_change' and price is not None and size is not None and side in ('BUY','SELL'):
                    sidebook=bids if side=='BUY' else asks
                    if size==0:sidebook.pop(price,None)
                    else:sidebook[price]=size
                    known_bid=kb;known_ask=ka;last_event=event;last_recv=recv
            tick=next(ticks,None)
        bid=max(bids,default=None);ask=min(asks,default=None)
        valid=bid is not None and ask is not None and 0<bid<ask<1 and known_bid is not None and known_ask is not None
        valid=bool(valid and abs(bid-known_bid)<1e-8 and abs(ask-known_ask)<1e-8 and 0<=at-last_event<=2_000_000)
        sellable=bool(bid is not None and known_bid is not None and 0<bid<1 and abs(bid-known_bid)<1e-8 and (ask is None or ask>bid) and 0<=at-last_event<=2_000_000)
        states.append({'offset':round(offset,6),'at_us':at,'last_receive_us':last_recv,'last_event_us':last_event,
                       'valid':valid,'sellable':sellable,'bid':bid,'ask':ask,'bid_prices':sorted(bids,reverse=True),
                       'bid_sizes':[bids[p] for p in sorted(bids,reverse=True)],
                       'ask_prices':sorted(asks),'ask_sizes':[asks[p] for p in sorted(asks)]})
    assert all(s['last_receive_us']<=s['at_us'] for s in states)
    return states,df.height

def run():
    OUT.mkdir(parents=True)
    dataset=ROOT/'roi-dataset.parquet'
    # Only identity/split metadata is read before random selection. No ROI labels.
    identities=pl.scan_parquet(dataset).select('market_slug','day','split','stage').collect()
    candidates=(identities.filter(pl.col('split')=='purged').group_by(['market_slug','day']).len().filter(pl.col('len')==2).sort('market_slug'))
    if candidates.is_empty():raise ValueError('No previously excluded complete two-decision window')
    choice=secrets.randbelow(candidates.height);selected=candidates.row(choice,named=True)
    slug=selected['market_slug'];day=selected['day']
    assert not identities.filter((pl.col('market_slug')==slug)&pl.col('split').is_in(['train','validation','test'])).height
    models={h:ROOT/'models'/f'roi-{h}s.joblib' for h in HORIZONS}
    manifest={'created_at_utc':datetime.now(timezone.utc).isoformat(),'selection':'uniform random from purge split, identity metadata only',
              'candidate_count':candidates.height,'selected_index':choice,'market_slug':slug,'day':day,
              'models':{str(h):digest(p) for h,p in models.items()},'policy_sha256':digest(ROOT/'locked-selection.json'),
              'feature_columns':FEATURES,'decisions_seconds':[60,120],'playback_seconds':300,'entry_delay_seconds':.3,
              'mean_threshold':.02,'q20_threshold':.01,'stop_loss':-.05,'budget_usd':5,
              'blind_scope':'excluded from model fitting, validation selection and previous test scoring; historical data, not prospective live blind test'}
    (OUT/'preregistered.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    # Fixed before any selected feature, book or outcome is read.
    print('LOCKED',slug,OUT,flush=True)
    fitted={h:joblib.load(p) for h,p in models.items()}
    features=(pl.scan_parquet(dataset).filter(pl.col('market_slug')==slug)
              .select(['stage','decision_us','limit']+FEATURES).collect())
    rows={r['stage']:r for r in features.to_dicts()}
    raw=next((ROOT/'raw/l2'/f'date={day}').glob('*.parquet'))
    states,raw_count=reconstruct(raw,slug)
    decisions=[];ledger=[];pending=None;position=None;trigger=None;chosen=None;valid_seconds=0
    real_start=time.monotonic()
    with (OUT/'events.jsonl').open('w',encoding='utf-8') as log:
        def emit(kind,payload):
            record={'type':kind,'elapsed_wall_s':time.monotonic()-real_start,'payload':payload}
            log.write(json.dumps(record,ensure_ascii=False,default=str)+'\n');log.flush()
        for state in states:
            offset=state['offset'];remaining=real_start+offset-time.monotonic()
            if remaining>0:time.sleep(remaining)
            # The engine receives exactly one released state, never the future map.
            emit('book',state)
            if offset==int(offset):
                valid_seconds+=int(state['valid'])
                if int(offset)%30==0:print('Replay',int(offset),'/ 300',flush=True)
            if offset in (60,120):
                row=rows[int(offset)];x=np.array([[row[c] for c in FEATURES]])
                predictions=[];options=[]
                for h,m in fitted.items():
                    assert m['features']==FEATURES
                    mean=float(m['mean'].predict(x)[0]);q20=float(m['q20'].predict(x)[0])
                    predictions.append({'horizon_s':h,'mean_roi':mean,'q20_roi':q20})
                    if mean>=.02 and q20>=.01:options.append((mean,h,q20))
                decision={'offset_s':offset,'predictions':predictions,'signal':bool(options and state['valid']),
                          'reason':'qualified' if options and state['valid'] else 'book_invalid' if not state['valid'] else 'conservative_entry_threshold_not_met'}
                decisions.append(decision);emit('decision',decision);print('Decision',decision,flush=True)
                if decision['signal'] and position is None:
                    _,h,q20=max(options);chosen={'stage':int(offset),'horizon':h,'target':D(str(min(.05,max(.01,q20))))}
                    pending=(round(offset+.3,6),D(str(row['limit'])))
            if pending and offset==pending[0]:
                position=buy(state,pending[1]);emit('entry',position or {'unfilled':True});pending=None
            if position and chosen:
                hold=round(offset-chosen['stage']-.3,6)
                if trigger and offset==trigger[0]:
                    value=liquidate(state,position)
                    trade={'entry_cost':position['total'],'entry_fees':position['fees'],'shares':position['shares'],
                           'exit_reason':trigger[1],'exit_offset':offset,**(value or {'pnl':-position['total'],'roi':D(-1),'exit_unavailable_loss_bound':True})}
                    ledger.append(trade);emit('exit',trade);position=None;chosen=None;trigger=None
                elif trigger is None and hold==int(hold) and 1<=hold<=chosen['horizon']:
                    value=liquidate(state,position);reason=None
                    if value and value['roi']>=chosen['target']:reason='take_profit'
                    elif value and value['roi']<=D('-.05'):reason='stop_loss'
                    elif hold==chosen['horizon']:reason='timeout'
                    if reason:trigger=(round(offset+.3,6),reason);emit('exit_trigger',{'offset':offset,'reason':reason})
    assert position is None
    assert all(digest(models[h])==manifest['models'][str(h)] for h in HORIZONS)
    cost=sum((t['entry_cost'] for t in ledger),D(0));profit=sum((t['pnl'] for t in ledger),D(0))
    result={'market_slug':slug,'historical_start_utc':datetime.fromtimestamp(int(slug.rsplit('-',1)[1]),timezone.utc).isoformat(),
            'wall_seconds':time.monotonic()-real_start,'source_tick_rows':raw_count,'reconstructed_states':len(states),
            'valid_integer_second_states':valid_seconds,'integer_second_states':301,'decisions':decisions,'ledger':ledger,
            'signals':sum(d['signal'] for d in decisions),'round_trips':len(ledger),'net_pnl_usd':profit,
            'net_roi':profit/cost if cost else None,'model_hashes_unchanged':True,'future_label_columns_read':False,
            'real_orders_sent':0,'blind_scope':manifest['blind_scope']}
    (OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    report=f'''# 五分钟历史盲测

模型与门槛事先冻结，从此前隔离未使用的窗口随机选择一轮。此轮未参与训练、选模或既有测试评分；这是历史回放，不是未来实时行情的前向盲测。

- 窗口：{slug}，UTC {result['historical_start_utc']}。
- 实际顺序运行：{result['wall_seconds']:.1f} 秒；原始行情 {raw_count:,} 条。
- 采样秒点有效盘口：{valid_seconds}/301。无效盘口禁止买入。
- 在预先固定的第 60、120 秒评估六个持有周期，没有读取未来收益标签。
- 入场信号：{result['signals']}；完成模拟往返：{len(ledger)}。
- 模拟净收益：${profit}；净 ROI：{str(result['net_roi']) if cost else '无成交，无有效分母'}。
- 模型文件哈希未变；未调整阈值，真实订单数为 0。

这一次结果不能证明长期盈利。若未入场，仅说明本轮没有满足既定保守条件；不代表模型准确或实盘安全。详细预测与原因见 result.json，按时释放的盘口与决策见 events.jsonl，抽样与模型锁定凭证见 preregistered.json。
'''
    (OUT/'盲测报告.md').write_text(report,encoding='utf-8');print('COMPLETE',json.dumps(result,default=str),flush=True)

if __name__=='__main__':run()
