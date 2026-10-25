"""Fixed-entry exit comparison on the already inspected trend replay; not blind."""
from decimal import Decimal as D
from datetime import datetime, timezone
from pathlib import Path
import json
import sys

from research_features import ROOT
from research_blind_round import reconstruct, digest
from research_execution import liquidate, policy_exit


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def run(folder):
    original = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
    out = folder / 'exit-comparison'
    out.mkdir(exist_ok=False)
    manifest = {'at_utc': datetime.now(timezone.utc).isoformat(),
        'source_sha256': digest(folder / 'result.json'), 'script_sha256': digest(Path(__file__)),
        'scope': 'paired comparison on previously inspected entries; not new blind validation',
        'fixed': 'same 14 entries, sizes, fees, received books, +0.3% target, 30s timeout, 300ms exit delay',
        'variants': ['original -10% stop', 'no price stop; still exit after 30s'],
        'diagnostic': 'initial liquidation separates spread/entry slippage from subsequent market motion',
        'real_orders': 0, 'running_app_modified': False}
    write(out / 'preregistered.json', manifest)
    rows = []
    for row in original['rounds']:
        trade = row['trade']
        if not trade:
            continue
        slug = row['market_slug']
        now = trade['decision_us']
        pos = {k: D(v) for k, v in trade['position'].items()}
        states, _ = reconstruct(next((ROOT / 'raw/l2' / ('date=' + row['day'])).glob('*.parquet')), slug, continuous=True)
        by_time = {s['at_us']: s for s in states}
        entry_mark = liquidate(by_time.get(now + 300_000), pos)
        old = policy_exit(by_time, pos, D('.003'), D('-.10'), 30, .3, now)
        assert old['pnl'] == D(trade['policy_exit']['pnl']) and old['reason'] == trade['policy_exit']['reason']
        # A -200% threshold is unreachable with a fully paid, nonnegative token;
        # this disables only the price stop while leaving the time exit intact.
        new = policy_exit(by_time, pos, D('.003'), D('-2'), 30, .3, now)
        rows.append({'slug': slug, 'entry_offset_s': trade['offset_s'],
            'entry_cost': pos['total'], 'entry_fees': pos['fees'],
            'initial_liquidation': entry_mark,
            'initial_gross_bid_ask_loss': entry_mark['gross'] - pos['cost'] if entry_mark else None,
            'initial_round_trip_fee': pos['fees'] + entry_mark['exit_fees'] if entry_mark else None,
            'original': old, 'no_price_stop': new,
            'stopped_but_later_sampled_net_positive': old['reason'] == 'stop_loss' and trade['diagnostic_net_positive_seconds'] > 0,
            'original_exit_vs_initial_net_change': old['pnl'] - entry_mark['pnl'] if entry_mark else None})
    summary = {}
    for name in ['original', 'no_price_stop']:
        completed = [r[name] for r in rows if r[name]['reason'] != 'exit_unavailable_loss_bound']
        summary[name] = {'completed': len(completed), 'unavailable': len(rows) - len(completed),
            'wins': sum(r['pnl'] > 0 for r in completed), 'losses': sum(r['pnl'] < 0 for r in completed),
            'net_pnl': sum((r['pnl'] for r in completed), D(0)),
            'take_profit': sum(r['reason'] == 'take_profit' for r in completed),
            'stop_loss': sum(r['reason'] == 'stop_loss' for r in completed),
            'timeout': sum(r['reason'] == 'timeout' for r in completed)}
    summary['stopouts_later_closed_profitably_without_price_stop'] = sum(r['original']['reason'] == 'stop_loss' and r['no_price_stop']['reason'] != 'exit_unavailable_loss_bound' and r['no_price_stop']['pnl'] > 0 for r in rows)
    summary['stopouts_with_better_outcome_without_price_stop'] = sum(r['original']['reason'] == 'stop_loss' and r['no_price_stop']['pnl'] > r['original']['pnl'] for r in rows)
    summary['stopouts_with_worse_outcome_without_price_stop'] = sum(r['original']['reason'] == 'stop_loss' and r['no_price_stop']['pnl'] < r['original']['pnl'] for r in rows)
    summary['initial_net_liquidation_below_stop'] = sum(r['initial_liquidation'] is not None and r['initial_liquidation']['roi'] <= D('-.10') for r in rows)
    summary['initial_liquidation_unavailable'] = sum(r['initial_liquidation'] is None for r in rows)
    assert digest(Path(__file__)) == manifest['script_sha256']
    write(out / 'result.json', {'manifest': manifest, 'summary': summary, 'trades': rows})
    (out / '止损对照报告.md').write_text(f'''# 固定入场，只对照价格止损

这是已经查看过的同一组历史样本的配对诊断，不是新盲测。保留原 14 笔 Up 模拟买入、含费 $5 预算、净 +0.3% 止盈、最多 30 秒持有和 300ms 卖出延迟，仅取消价格止损。未改动运行中的桌面程序。

| 规则 | 完成平仓 | 盈利笔数 | 亏损笔数 | 模拟净收益 |
|---|---:|---:|---:|---:|
| 原 -10% 价格止损 | {summary['original']['completed']} | {summary['original']['wins']} | {summary['original']['losses']} | ${summary['original']['net_pnl']} |
| 无价格止损，仍有 30 秒超时 | {summary['no_price_stop']['completed']} | {summary['no_price_stop']['wins']} | {summary['no_price_stop']['losses']} | ${summary['no_price_stop']['net_pnl']} |

原先止损的交易中，取消价格止损后 {summary['stopouts_later_closed_profitably_without_price_stop']} 笔转为实际模拟平仓盈利；{summary['stopouts_with_better_outcome_without_price_stop']} 笔结果更好，{summary['stopouts_with_worse_outcome_without_price_stop']} 笔更差。有 {summary['initial_net_liquidation_below_stop']} 笔刚买入时按同一盘口立即卖出的净估值已低于 -10%；这部分估值损失无需后续价格下跌，来自价差、深度、买卖费用。

不能以这组小样本证明所有止损都该关闭，或趋势策略一定亏损/盈利；秒级行情采样可能错过更短机会，Down 深度缺失，基准为历史 TWAP60 重建值。止盈触发后的 300ms 也可能改变成交结果。完整逐笔费用、入场瞬时可卖价值、两种退出原因和收益见 result.json。
''', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str), flush=True)
    print('COMPLETE', out, flush=True)


if __name__ == '__main__':
    run(Path(sys.argv[1]))
