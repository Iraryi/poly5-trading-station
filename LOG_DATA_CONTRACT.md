# `log/` 全量个人数据与行情变更日志：契约、口径与验收

> 用户明确要求「个人胜率及各种数据变化，包括 DOI（可能与 ROI 不同）越详细越好，都计入一个叫 `log` 的日志」。**`log/` 是客观事件事实账本；`ai log/` 是可被 AI 修订的意见稿，两者严格分离。**

## 目录与存储

持久化至 `%LOCALAPPDATA%/Poly5BTC/`（不写入 EXE 安装目录，不因升级清空），并在设置中显示实际位置，允许用户选择其他非特权数据目录。

```
%LOCALAPPDATA%/Poly5BTC/
├─ log/                                # 原始与计算事实，AI 无写权限
│  ├─ events/YYYY-MM-DD/*.jsonl        # 追加式事件，每轮或体积轮换、压缩
│  ├─ orderbook/YYYY-MM-DD/*.jsonl.gz  # 深度/快照/增量，带 gap/seq 信息
│  ├─ network/YYYY-MM-DD/*.jsonl       # VPN 出口、连接路径、健康检测变化
│  ├─ trades/YYYY-MM-DD/*.jsonl        # 下单→接受→成交→撤单→结算，对账
│  ├─ ai/YYYY-MM-DD/*.jsonl           # AI 调用成本/响应延迟/错误
│  ├─ ai_file_edits.jsonl              # AI 文件写入审计，不可由 AI 改动
│  ├─ reports/                         # 按轮、每日、周/月可重算报表
│  ├─ metrics.sqlite                   # 供检索的带主键索引与统计视图
│  └─ diagnostics/                      # 自检 / 版本 / 进程退出错误
├─ ai log/                             # 可写可修改的 Codex 建议，不是事实源
│  ├─ INDEX.md
│  ├─ YYYY-MM-DD/YYYYMMDD-HHMMSS_*.md
│  └─ versions/                        # 每次修订的备份
├─ settings/                            # 不能存明文密钥
└─ cache/                               # 可清理，不是持久日志
```

## 事件字段：一切有来源、有时间、有身份、有序列

通用 `envelope`：

- `schema_version`, `event_id`, `event_type`, `observed_at_utc`, `source_at_utc`, `monotonic_ns`, `session_id`, `round_id`, `market_slug`, `condition_id`, `asset_id`, `strategy_version`, `mode`, `source`, `latency_ms`, `freshness_ms`, `payload`, `quality_flags`, `correlation_id`。
- 原始消息保留官方时间戳、协议 sequence/订阅 ID（若提供）及原始字段摘要/引用；进程重启去重，不丢事件。任何缺字段写 null（或 `UNKNOWN`），绝对不写虚构 0。
- `observed_at_utc` 不等于 `source_at_utc`；所有事件时间 ISO8601 `Z`，对齐汇报的交易阶段按**市场窗口起点+秒数**，本地 UI 可显示北京时间/用户时区。
- 事件可以是 `market.*`, `price.*`, `twap.*`, `book.*`, `signal.*`, `order.*`, `fill.*`, `position.*`, `settlement.*`, `metric.*`, `network.*`, `geo.*`, `ai.*`, `config.changed`, `engine.*`；必须有事件字典、稳定枚举、字段类型、变更记录。
- 两次变化之间的**连续过程**通过 ticker 样本保留，异常和状态切换单独写事件；秒级汇总与原始高频簿数据区分层级，避免一秒内几十次 GUI 更新产生海量重复无差异报表。

## 具体指标：除统计值，还要存可重算的分子、分母、适用范围

| 领域 | 必需记录 |
|---|---|
| 行情 | BTC 5m round 起始/结束/阶段/剩余秒数/两侧 Up/Down best_bid, best_ask, spread, mid, last_trade, bid/ask 深度逐档、量、可买/可卖上限、min_order/tick/feeSchedule、源时间/延迟/断流 |
| Chainlink | 同源 60s TWAP 起始基准/滚动值/差值/USD/相对差/标准化 z 与其波动度估计窗口、价格来源/序列号/最终结算差异、预测剩余时间与校准误差 |
| 信号 | 模型版本、策略版本、特征值、候选 Up/Down、入场资格、拟单价/数量/手续费/风险余量、置信区间、预测胜率、拒绝原因及数据完整性；候选但没成交要明确区分 |
| 订单 | **若未来真实允许** order lifecycle request/accepted/partial fill/filled/cancel/error/redeemed；paper 使用完全独立的 ID 与 ledger，不能与真实数据混表暗示成交 |
| 金额 | 买入成本含手续费、卖出净收入、实际/预计费用、滑点、资金占用、分批配对、净 PnL、成本加权收益、风险敞口、最大单次亏损、最大回撤 |
| ROI | 页面参考 ROI、理论结算 ROI、按深度可卖 ROI、已确认已实现净 ROI、跨局 ROI 时间序列；触发阈值 0.35 的判定使用哪一种及未成交原因 |
| DOI | **原样保留字段名 `doi`**，来源/算法版本/单位/值/输入/置信度/是否有效；在用户确认独立定义前标记 `definition_pending`，不得偷偷按 ROI 代入或编造计算规则 |
| 胜率 | 明确 `correct_wins / settled_directional_trades`、`profitable_closed_trades / fully_closed_trades`、`positive_rounds / rounds_with_exposure`、`signals_correct / signals_evaluated`，分别展示；无成交信号不算个人胜率 |
| 行为与样本 | 交易机会数、候选/下单/接受/成交/部分/取消/错过/拒绝、盈利/亏损/盈亏平/无入场、阶段/方向/模型/参数/时段/网络状态分层样本量 |
| 评估 | gross PnL、fee、slippage、net PnL、profit factor、expectancy、回撤、时间暴露、收益不确定区间、正负连胜/连亏、不同券价段绩效、纸面 vs 实际差值 |
| 网络 | VPN 标称节点地区 vs 官方观察出口、IP（可脱敏）、geoblock `blocked/country/region`、代理模式、HTTP/WS 健康/延迟 p50 p95、数据 stale、重连/序列 gap、故障恢复与每次地区变化 |
| AI | provider/model、任务类别、用了哪些脱敏事件/快照、调用次数/token/成本/首字延迟/总耗时/超时、建议文件写入、版本变更、AI 自检问题 |
| 版本/环境 | APP/SDK/结算规则/手续费解释版本、UTC时钟校准偏差、操作系统版本、配置快照哈希、策略代码哈希、run id、状态变更 |

### 三种「胜率」不能合并成一个大数字

- **结果方向命中率**：`最终结算猜对的方向持仓次数 / 最终已结算且入场的方向持仓次数`。若一个 round 有两笔方向单，另提供按独立 round 归并的口径，防止把重复单当作独立成功预测。
- **平仓盈利率**：`净 PnL >0 的已完成交易数 / 已完整平仓的交易数`，只做止盈也可能高胜率低收益；需要记录亏损尾部。
- **净盈利轮次比例**：`扣完费/计入结算后净 PnL >0 的有敞口轮次数 / 有敞口且结果已确定的轮次数`。未决轮次不能错误算作 0 亏损。

保存统计聚合 `numerator`, `denominator`, `definition`, `coverage`, `start_utc`, `end_utc`, `mode`, `strategy_version`, `source_dataset_hash`。同一账号多策略/同轮可能相关，需要清楚标注非独立试验，不能拿高胜率推出策略有优势。`ROI` 聚合按投入资金加权，另显示交易级均值和分位数；不能简单平均所有 ROI 得出资金回报率。

## 示例必须遵守财务口径

- `entry_budget_usd=5` 指**含所有已知买入费用的最大现金风险/预算**；`shares=5` 是完全不同的单位。
- 浮动 ROI 不得当已实现净 ROI；无 bids 深度的仓位显示 `UNFILLABLE`，不能把 UI midpoint 当卖出价格。
- 末段结算基准 60 秒 Chainlink TWAP（以当局 market rules 为准），不可用另一交易所即时 BTC 价格替代结算数据。
- 不同策略版本/回测时期/订单执行环境的 PnL 不能直接混淆，不把 replay/paper 统计叫真实个人收益。

## 日志真实性、完整性、隐私与性能

- 客户端按顺序追加 JSONL，持久化 SQLite (WAL, 外键, 主键唯一)，逐局和进程退出前 flush；异常终止有事件恢复与 ledger 对账，hash/CRC 可选以验证中断损坏；原始 orderbook 录制可以高容量压缩轮换，但不能把 `log/` 偷偷清空。
- 用户必须能从 GUI 搜索/筛选/导出 `log/` JSONL/CSV/SQLite 报表，设置保留期限与磁盘上限，导出时显示体积和隐私提示；采用合理保留策略和备份，不得默认永久无限增长导致磁盘耗尽。
- 隐私：密钥/凭据/cookie/账户敏感信息永不写日志，公开出口 IP 在 GUI 可以打码、落盘可以只保留哈希和地区（可由用户选择细节级别），供 AI 的数据默认去标识化。日志的资料不足必须写 `UNKNOWN`，不能替补猜测。
- 所有数据跨模式明确打 `monitor` / `paper` / `replay` / `live` 标签；模拟值绝不写成 `live`。
- 事件流异步入持久队列，UI 使用采样视图不拖慢 WS；写盘背压/丢弃**不得静默**，先报警并停发模拟交易信号，生成 `log.event_drop` / `engine.storage_degraded`，保留可追溯 gap。
- “全部变化”解释为保留**所有收到的真实输入事件、每次状态或策略变化、可重放的关键数据**，而不是每毫秒复制几十份相同卡片；高频原始市场增量分库存档，用户统计快照不必每 tick 重算和写入。

## 硬验收测试

1. 1000+ 次顺序事件和突然中断重启：event_id 去重、时间顺序可检查、账本一致、无破损丢整个历史。
2. 500 个盘口增量事件回放，日志能复原关键价差与成交深度，时间源/跳号 gap 完整。
3. 1 次未成交纸单、1 次部分成交、1 次盈利平仓、1 次亏损结算：ROI/胜率分子分母正确，未完成订单不算已实现收益。
4. VPN 切换地区 3 次且官方 blocked 状态多次变化：日志完整保留**每一次状态切换**并触发相关诊断。
5. 故意把 `doi` 定义留空：数据显示 `definition_pending`，AI/统计系统不计算虚假 DOI。
6. 模型建议修改 ai log：原始事实日志保持只增不改；每次 ai log 修订在 audit 中可以追溯。
7. 未连接钱包时**仍能运行行情/网络/副驾驶的只读分析**，但个人交易数据和胜率显示「暂无真实数据」而不是伪造。
8. 关闭主窗口入托盘后长时间运行，日志继续增长；调用「完全退出」日志完整刷盘；再次启动能读取历史统计。

---
## v3 账户/买卖/扩展事件

新增事件和字段见 `ACCOUNT_EVENT_CONTRACT.md`；旧 `log/` 事实与 `ai log/` 建议仍绝对隔离。每笔账户余额变化、身份切换、发单请求与成交状态都须可追溯；不要将公开地址的其他账户数据与当前已认证账户数据混合。
