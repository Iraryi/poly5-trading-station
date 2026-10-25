"""Exploratory causal trend / opening-baseline replay. No client or order API.

Not a deployment policy: the user's qualitative stability rule is provisionally
specified below; archived depth covers Up only. Future maxima are diagnostic
upper bounds and never used to choose entries or policy exits.
"""
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import random
import secrets

import numpy as np
import polars as pl

from research_features import ROOT, asof_index, load_series
from research_blind_round import reconstruct, digest
from research_execution import buy, liquidate, policy_exit

POLICY = {
    'id': 'trend-baseline-exploration-v1',
    'baseline': 'latest opening Chainlink TWAP60 observation at/before start; max age 5s; must already be received',
    'direction': 'received Chainlink spot above baseline and positive 5s return => Up; below and negative => Down',
    'stable': '15-second received-spot high-low / minimum <= 0.0005',
    'reference_max_age_s': 5, 'scan_seconds': [5, 268],
    'entry': 'first valid Up signal with a fill per round; no ROI model filter',
    'budget_usd': 5, 'entry_delay_s': 0.3, 'exit_delay_s': 0.3,
    'entry_limit_above_ask': 0.01, 'target_net_roi': 0.003,
    'stop_net_roi': -0.10, 'max_hold_s': 30, 'fee_rate': 0.07,
    'depth_scope': 'Up only; Down signals counted, never given invented fills',
    'sampling': 'two uniformly sampled markets per day from the existing eight-day archive, before reading their price paths',
    'scope': 'exploratory historical replay on a previously studied archive, NOT new model-blind or prospective validation',
}


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def signal_at(now, opening_index, opening_start, spot, twap):
    so, sr, sp = spot
    to, tr, tp = twap
    if opening_index < 0 or opening_start - to[opening_index] > 5e6 or tr[opening_index] > now:
        return {'reason': 'baseline_unavailable'}
    queries = now - np.arange(15, -1, -1) * 1_000_000
    ids = asof_index(sr, queries)
    if np.any(ids < 0) or np.any(queries - so[ids] > 5e6):
        return {'reason': 'reference_stale_or_missing'}
    assert np.all(sr[ids] <= queries)
    prices = [D(str(p)) for p in sp[ids]]
    baseline = D(str(tp[opening_index]))
    span = (max(prices) - min(prices)) / min(prices)
    trend = prices[-1] / prices[-6] - 1
    distance = prices[-1] / baseline - 1
    side = 'Up' if trend > 0 and distance > 0 else 'Down' if trend < 0 and distance < 0 else None
    return {'side': side if span <= D('.0005') else None,
            'reason': 'unstable' if span > D('.0005') else 'aligned' if side else 'not_aligned',
            'baseline': baseline, 'spot': prices[-1], 'return_5s': trend,
            'distance_from_baseline': distance, 'range_15s': span,
            'latest_receive_us': int(max(sr[ids[-1]], tr[opening_index]))}


def run():
    out = ROOT.parent / ('trend-baseline-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    out.mkdir()
    # Read identity columns only before recording sampling/parameters.
    identities = pl.read_parquet(ROOT / 'raw/markets.parquet', columns=['market_slug', 'start_time'])
    days = sorted(p.name.removeprefix('date=') for p in (ROOT / 'raw/l2').iterdir() if p.is_dir())
    seed = secrets.randbits(64)
    rng = random.Random(seed)
    choices = []
    for day in days:
        candidates = sorted(r['market_slug'] for r in identities.iter_rows(named=True)
                            if r['start_time'].date().isoformat() == day)
        assert len(candidates) >= 2
        choices.extend({'market_slug': slug, 'day': day} for slug in rng.sample(candidates, 2))
    manifest = {'policy': POLICY, 'seed': seed, 'selected': choices,
                'script_sha256': digest(Path(__file__)), 'at_utc': datetime.now(timezone.utc).isoformat(),
                'fee_source': 'https://docs.polymarket.com/trading/fees',
                'rule_source': 'https://polymarket.com/event/btc-updown-5m-1789672500',
                'fee_assumption': 'apply currently verified crypto taker schedule to historical books, not historical paid receipts',
                'baseline_limitation': 'reconstructed from archived TWAP60, not an archived official Price to Beat field'}
    write(out / 'preregistered.json', manifest)
    print('LOCKED', out, 'rounds', len(choices), flush=True)
    spot, sq = load_series('chainlink_streams')
    twap, tq = load_series('chainlink_streams_twap60s')
    write(out / 'reference-quality.json', {'spot': sq, 'twap': tq})
    results = []
    for choice in choices:
        slug = choice['market_slug']
        start = int(slug.rsplit('-', 1)[1]) * 1_000_000
        opening_index = asof_index(twap[0], start)
        states, ticks = reconstruct(next((ROOT / 'raw/l2' / ('date=' + choice['day'])).glob('*.parquet')), slug, continuous=True)
        by_time = {s['at_us']: s for s in states}
        counts = Counter()
        chosen = None
        decisions = []
        for offset in range(5, 269):
            now = start + offset * 1_000_000
            signal = signal_at(now, opening_index, start, spot, twap)
            counts[signal['reason']] += 1
            if signal.get('side'):
                counts[signal['side'] + '_signal_seconds'] += 1
            decisions.append({'offset_s': offset, **signal})
            if chosen is not None or signal.get('side') != 'Up':
                continue
            state = by_time[now]
            if not state['valid']:
                counts['entry_book_invalid'] += 1
                continue
            limit = min(D('.99'), D(str(state['ask'])) + D('.01'))
            position = buy(by_time.get(now + 300_000), limit)
            if position is None:
                counts['entry_unfilled'] += 1
                continue
            # This first entry is fixed before any subsequent path is inspected.
            chosen = {'decision_us': now, 'offset_s': offset, 'signal': signal, 'position': position}
        record = {**choice, 'raw_ticks': ticks, 'counts': dict(counts), 'trade': None}
        if chosen:
            now = chosen['decision_us']
            pos = chosen['position']
            exit_result = policy_exit(by_time, pos, D('.003'), D('-.10'), 30, .3, now)
            # Diagnostic only: all 30 potential delayed liquidations, including
            # after the policy has exited. Never count their maxima as PnL.
            opportunities = []
            for hold in range(1, 31):
                value = liquidate(by_time.get(now + hold * 1_000_000 + 600_000), pos)
                opportunities.append({'hold_s': hold, **value,
                                      'gross_profit_before_fees': value['gross'] - pos['cost']} if value else {'hold_s': hold, 'unavailable': True})
            valid = [v for v in opportunities if not v.get('unavailable')]
            record['trade'] = {**chosen, 'policy_exit': exit_result,
                'diagnostic_valid_exit_seconds': len(valid),
                'diagnostic_gross_positive_seconds': sum(v['gross_profit_before_fees'] > 0 for v in valid),
                'diagnostic_net_positive_seconds': sum(v['pnl'] > 0 for v in valid),
                'diagnostic_max_net_roi': max((v['roi'] for v in valid), default=None),
                'opportunities_not_policy_fills': opportunities}
        write(out / (slug + '.json'), {'result': record, 'decisions': decisions})
        results.append(record)
        print(slug, 'signals', counts['Up_signal_seconds'], counts['Down_signal_seconds'],
              'exit', record['trade']['policy_exit'] if chosen else 'no entry', flush=True)
    trades = [r['trade'] for r in results if r['trade']]
    completed = [t for t in trades if t['policy_exit']['reason'] != 'exit_unavailable_loss_bound']
    incomplete = [t for t in trades if t['policy_exit']['reason'] == 'exit_unavailable_loss_bound']
    totals = {
        'rounds_sampled': len(results), 'filled_up_entries': len(trades),
        'completed_round_trips': len(completed), 'unavailable_exits': len(incomplete),
        'completed_net_pnl_usd': sum((t['policy_exit']['pnl'] for t in completed), D(0)),
        'unresolved_entry_cost_usd': sum((t['position']['total'] for t in incomplete), D(0)),
        'completed_wins': sum(t['policy_exit']['pnl'] > 0 for t in completed),
        'completed_losses': sum(t['policy_exit']['pnl'] < 0 for t in completed),
        'entries_with_any_gross_positive_exit_within_30s': sum(t['diagnostic_gross_positive_seconds'] > 0 for t in trades),
        'entries_with_any_net_positive_exit_within_30s': sum(t['diagnostic_net_positive_seconds'] > 0 for t in trades),
        'entries_with_full_30s_valid_exits_and_no_net_positive': sum(t['diagnostic_valid_exit_seconds'] == 30 and t['diagnostic_net_positive_seconds'] == 0 for t in trades),
        'down_signal_seconds_not_simulated': sum(r['counts'].get('Down_signal_seconds', 0) for r in results),
        'real_orders': 0,
    }
    assert digest(Path(__file__)) == manifest['script_sha256']
    write(out / 'result.json', {'manifest': manifest, 'totals': totals, 'rounds': results})
    report = f'''# 基准线与短期趋势同向：探索性回放

采用用户澄清的本轮起始基准线含义。基准暂以开轮前最近的 Chainlink TWAP60 观测重建（最多旧 5 秒），且必须已收到；没有归档页面 Price to Beat 的逐轮原值。短期方向取已收到 Chainlink 现货价格 5 秒变化，现货相对基准同向。平稳暂定义为过去 15 秒高低波幅不超过 0.05%。这些是本次实验假设，不是用户指定参数。

从既有八天数据每天均匀随机取两轮，共 {totals['rounds_sampled']} 轮；参数及样本先写入 preregistered.json，再读取价格路径。该数据集此前已研究，因此不是全新盲测或前向验证。不调用模型，不设置预测 ROI 入场门槛。每轮只做首笔可成交 Up 信号，单笔预算 $5。Down 缺少深度，只记录信号。信号时间严格使用接收时间；开仓、平仓各延迟 300ms，按真实重建深度吃单，采用当前官方 crypto 0.07 费率公式重算，不冒充历史实付费率。

在入场后 1–30 秒逐秒检查净 ROI，净 +0.3% 止盈、-10% 止损、30 秒超时；触发之后再延迟 300ms 卖出，可能与触发时收益不同。

- 模拟买入 {len(trades)} 笔；完成平仓 {len(completed)} 笔，盈利 {totals['completed_wins']} 笔，亏损 {totals['completed_losses']} 笔。
- 完成交易合计模拟净收益 ${totals['completed_net_pnl_usd']}。无法核实平仓 {len(incomplete)} 笔，未解决买入成本 ${totals['unresolved_entry_cost_usd']}，未将其计为已实现损失或零收益。
- 入场后 30 秒的采样中，曾有扣费前正价差的 {totals['entries_with_any_gross_positive_exit_within_30s']} 笔；曾有扣费后正收益的 {totals['entries_with_any_net_positive_exit_within_30s']} 笔。该诊断包含策略退出后的行情，是事后机会统计，不是可预知或实际捕获的收益。
- 其中 {totals['entries_with_full_30s_valid_exits_and_no_net_positive']} 笔 30 个采样卖点全部有效，但没有一个净盈利采样点。逐秒采样不能排除秒内更短机会。
- Down 同向信号秒点 {totals['down_signal_seconds_not_simulated']}，未假造 Down 成交。

该小样本、Up 侧、暂定参数结果不能代表完整双向策略，更不能用于证明长期收益。每笔的基准、信号、实际模拟卖点和事后机会路径保存在逐轮 JSON。当前运行程序仍是盘口 ROI 自动 PAPER，这个实验尚未接入桌面，未修改运行中的模型或风险参数。
'''
    (out / '基准趋势回放报告.md').write_text(report, encoding='utf-8')
    print('COMPLETE', out, json.dumps(totals, default=str), flush=True)


if __name__ == '__main__':
    run()
