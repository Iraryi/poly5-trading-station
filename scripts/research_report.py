"""Write a Chinese report from actual experiment outputs, never placeholder scores."""
from pathlib import Path
import json,hashlib
import polars as pl
import pyarrow.parquet as pq
from research_features import ROOT

def percent(x):return '—' if x is None else f'{x:.2%}'

def report():
    result=json.loads((ROOT/'training-results.json').read_text());quality=json.loads((ROOT/'quality.json').read_text())
    roi=json.loads((ROOT/'roi-quality.json').read_text());books=[json.loads(p.read_text()) for p in sorted((ROOT/'books').glob('*.json'))]
    remote={x['path']:x for x in json.loads((ROOT/'remote-file-metadata.json').read_text()) if x['type']=='file'}
    hashes=[];raw_rows=0
    for path in sorted((ROOT/'raw/l2').glob('*/*.parquet')):
        rel=path.relative_to(ROOT/'raw').as_posix();digest=hashlib.file_digest(path.open('rb'),'sha256').hexdigest()
        assert digest==remote[rel]['lfs']['oid'],f'Checksum mismatch {rel}'
        raw_rows+=pq.ParquetFile(path).metadata.num_rows;hashes.append({'path':rel,'sha256':digest,'matches_provider_lfs':True})
    samples=json.loads((ROOT/'official/historical-events.json').read_text());market_labels={r['market_slug']:r['winning_outcome'] for r in pl.read_parquet(ROOT/'raw/markets.parquet').to_dicts()}
    verified=[]
    for sample in samples:
        m=sample['response'][0]['markets'][0];prices=json.loads(m['outcomePrices']);outcomes=json.loads(m['outcomes'])
        winner=outcomes[prices.index('1')];match=('yes' if winner.lower()=='up' else 'no')==market_labels[sample['slug']]
        assert m['umaResolutionStatus']=='resolved' and match
        verified.append({'slug':sample['slug'],'matches':match,'fee':m['feeSchedule']})
    evidence={'raw_l2_rows':raw_rows,'files':hashes,'official_label_checks':verified,'reconstructed_states':sum(x['states'] for x in books),
              'valid_states':sum(x['valid_states'] for x in books),'book_profiles':books}
    (ROOT/'evidence-validation.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    test=result['policy']['test_0.3s'];stress=result['policy']['test_1.0s']
    title='首轮训练与离线验证完成；尚不满足自动交易上线条件。'
    if test['simulated_round_trips']==0:conclusion='测试集没有产生满足预设保守入场条件的交易，因此不能宣称策略盈利，也不能把零回撤理解为交易策略安全。'
    elif test['net_pnl_usd']<=0:conclusion='测试集模拟净收益未转正，不能作为可盈利策略发布。'
    else:conclusion='测试集出现模拟正收益，但仅两个测试日且存在假设成交和延迟限制，不能作为可持续盈利或实盘能力的证明。'
    lines=['# BTC 五分钟市场：1–30 秒净 ROI 研究结果','',title,'',conclusion,'',
           '## 研究目标','',
           '在预期未来短持仓净 ROI 足以覆盖全部成本时入场，按入场时确定的保守目标、亏损阈值或最长持仓退出。终局涨跌不是本轮主训练标签。',
           '', '## 数据与切分','',
           f'- 历史区间：2026-08-14 至 08-21 UTC，{quality["markets"]:,} 个五分钟市场。',
           f'- 原始 L2 共 {raw_rows:,} 条；重建 {evidence["reconstructed_states"]:,} 个抽样盘口状态，其中 {evidence["valid_states"]:,} 个通过内部有效性检查。原始记录数不等于独立训练样本数。',
           f'- 进入建模的数据为 {roi["rows"]:,} 个决策样本、{roi["markets"]:,} 个市场；每轮只观察第 60、120 秒，未寻找最佳入场时点。',
           '- 训练 08-14～08-18、验证 08-19、测试 08-20～08-21；分组按市场、时间顺序，边界隔离 10 分钟。',
           '- 标准化只在训练集拟合；均值候选仅以验证误差选择，测试集不参与选择。',
           f'- 八份 L2 文件均与供应商 LFS SHA-256 一致；独立抽查 {len(verified)} 个官方终局结果，均与数据标签一致。终局标签仅辅助审计，不用于补造短线成交。',
           f'- 参考现货接收延迟中位数 {quality["spot"]["lag_seconds_quantiles"][0]:.3f} 秒，TWAP 为 {quality["twap"]["lag_seconds_quantiles"][0]:.3f} 秒。特征严格按接收时间，不按事后事件时间倒灌。',
           '', '## 测试集预测指标','',
           '以下收益是固定周期、假设吃单成交后的标签统计，不是已实现收益；分位数覆盖率理想约为 20%，不能视作盈利保证。',
           '', '| 计划持有 | 均值模型 | 标签平均净 ROI | 标签正收益比例 | 模型 MSE | 常数基线 MSE | 落在预测 20% 分位数以下 |',
           '|---|---|---:|---:|---:|---:|---:|']
    for m in result['metrics']:
        if m['split']=='test':lines.append(f'| {m["horizon"]} 秒 | {m["model"]} | {percent(m["mean_label_roi"])} | {percent(m["positive_label_rate"])} | {m["mse"]:.6f} | {m["constant_baseline_mse"]:.6f} | {percent(m["q20_observed_below_rate"])} |')
    lines+=['','## 动态止盈回放','',
            '净 ROI 均值至少 2%、预测 20% 分位数至少 1% 才尝试入场；选择均值最高的计划持有周期，止盈在入场时固定为分位数截取到 1%–5%，止损触发线 −5%。触发后仍需经过退出延迟，实际收益可以低于目标。',
            '', '| 场景 | 入场信号 | 未成交 | 模拟往返 | 模拟净收益 USD | 净投入收益率 | 最大回撤 USD | 风险停机 |',
            '|---|---:|---:|---:|---:|---:|---:|---|']
    for name,p in result['policy'].items():
        lines.append(f'| {name} | {p["signals"]} | {p["entry_unfilled"]} | {p["simulated_round_trips"]} | {p["net_pnl_usd"]:.4f} | {percent(p["aggregate_net_roi"])} | {p["max_drawdown_usd"]:.4f} | {p["risk_halted"]} |')
    lines+=['', '基线“不交易”收益为 0。零交易情况下净 ROI、胜率无有效分母，保持空值。两种延迟场景都没有交易，不能据此认定策略抗延迟。亏损达到 $10 或退出不可确认即停止新增风险；不可确认退出按全部买入成本损失的保守界计入，未假冒真实损失回执。',
            '', '## 可执行性假设与未完成项','',
            '- 仅使用真实记录的 Up 侧 L2；没有用互补价假造 Down 深度。',
            '- 每笔 $5 含费用，至少 5 份，按档位消耗深度；费用系数 0.07、指数 1，保守向上取五位小数。',
            '- 止盈按一秒间隔检查，成交延迟场景为 300ms 和 1000ms；实际持有时间含额外退出延迟。这还不是逐笔事件驱动的生产执行器。',
            '- 未验证同一价格档位的未来真实可成交量、交易所拒单、订单对盘口的影响及本机实际时延。历史模拟不能代替真实回执。',
            '- 只有八个旧交易日、两个最终测试日；尚未完成更长期跨行情和独立前向纸面验证，当前授权价格源也未接入。',
            '- `deployment_allowed=false`；模型留在离线研究目录，应用中的预测与自动交易保持关闭。',
            '', '## 复现与证据','',
            '完整代码见项目 scripts/research_*.py；研究目录包含固定实验方案、原数据下载清单、字段质量检查、盘口质量、模型、候选记录、分时指标和逐笔模拟账本。研究环境依赖锁在 requirements-research-lock.txt，交互审计入口 research-audit.ipynb。',
            '', '数据归属：PMData，CC BY 4.0：https://huggingface.co/datasets/mileswangs/polymarket-btc-updown-5m-orderbook-tick-data ，https://pmdata.dev 。',
            '', '官方依据：https://docs.polymarket.com/changelog 、https://docs.polymarket.com/trading/fees 、https://docs.polymarket.com/trading/place-orders 。']
    (ROOT/'ROI研究报告.md').write_text('\n'.join(lines),encoding='utf-8')
    print(title,conclusion,flush=True)

if __name__=='__main__':report()
