# 从 BTC 5m 到未来多市场多策略的真实可扩展架构

原则：**现在让 BTC 五分钟端到端可用；给未来的功能留干净的连接点，不在今天虚构“全部已支持”。** 拒绝把每一个页面、策略、行情源和账户地址写死进 `main.py` 或 UI 按钮。

## 层级与责任

```text
┌──────────── Windows EXE / GUI ──────────────┐
│ 总览 │ 交易台 │ 账户 │ 持仓/订单 │ 数据 │ 扩展 │ 设置 │ 右侧AI副驾驶 │
└────────────── 只读 UI View Models ───────────────┘
              ▲ 独立异步事件/查询（UI不堵WS）
       ┌──────┴────────── application bus ───────────┐
       │账户服务│市场发现│时间窗│持仓投影│统计│诊断│风控│
       └──┬─────┬─────────┬───────────┬───────────────┘
          │     │         │           └── ExecutionGateway → 单独受限的实盘适配器
          │     │         └── 数据层 EventStore + SQLite + JSONL + versioned metrics
          │     └──────── MarketProvider / SettlementRule / PriceFeed / BookFeed
          └────────────── AccountAdapter / OrderFeed / WalletBalanceProvider
                          ↑
                  策略注册表 StrategyPlugin（当前 BTC_5M_TWO_STAGE）
```

## 插件 / 模块的契约（先内部注册，不搞任意远程 Python 执行）

- `MarketProvider`: `discover(template, now)`, `subscribe(market_id)`, `fetch_snapshot`, `fetch_rules`; 负责编码市场的语义 ID/outcomes/时间窗，不传钱包密钥。
- `MarketTemplate`: `instrument`, `resolution`, `category`, `market_type`, `timezone`, `cutoff`, `settlement_rule_id`, `price_source`；兼容任意数量 outcome，BTC Up/Down 是特例。
- `SettlementRule`: `resolve_reference`, `outcome_condition`, `estimate_uncertainty`; 某个市场的 60s TWAP **不得**成为通用引擎假设。
- `AccountAdapter`: `get_identity`, `get_positions`, `get_cash_balance`, `get_portfolio_value`, `get_account_events`, `get_orders`；返回带 source/timestamp/quality/units 的统一 DTO。未来账户/平台可扩展。
- `OrderFeed`: user WS + REST 对账，统一输出 `OrderStateEvent`；**不可**直接在策略中实现“已成交”的判定。
- `StrategyPlugin`: `on_snapshot`, `propose_intents`, `explain_skip`, `required_capabilities`, `risk_profile_id`；策略只输出 `OrderIntent`，不得持签名权限、不得跳过风控。
- `ExecutionAdapter`: `preview(intent)`, `submit(approved_intent)`, `cancel(order_id)`, `reconcile(account_id)`。所有真实提交穿过唯一 `ExecutionGateway` 进行 live gate、risk、geo、idempotency 校验；没有“插件可直接调官方下单函数”的后门。
- `FeatureModule`: 声明 UI panel、routes、required capabilities、dependencies、start/stop 生命周期和日志契约；无法初始化时显示 disabled/why。
- `AIProvider`: 与执行引擎隔离，只可读**脱敏只读快照**和写 `ai log`，不能读密钥或注入交易命令。

## 能力枚举/功能旗标

`PUBLIC_MARKET_READ`, `PUBLIC_ACCOUNT_READ`, `AUTHENTICATED_ORDERS_READ`, `WALLET_BALANCE_READ`, `SIMULATION`, `LIVE_MANUAL_ORDER`, `LIVE_AUTOMATION`, `CANCEL_ORDERS`, `REDEEM_POSITIONS`, `PORTFOLIO_ANALYTICS`, `AI_DIAGNOSE`, `AI_SUGGEST_EDIT`, `GEO_CHECK`。

能力遵循默认拒绝：模块自称支持 `LIVE_AUTOMATION` 不代表它可用，还必须与**应用权限 + 用户显式选择 + account/geo 状态 + 交易所可交易 + 风控**求交集；禁用 AI 自动升级或安装不受信插件。

## 市场发现不是固定“下一个 slug”

1. 注册 market template `polymarket.crypto.btc.updown.5m`。每次轮换从 Gamma/官方允许的接口发现最接近当前窗口的候选 market。
2. 验证 market open/closed、start/end、asset IDs/outcomes、orderbook tick/fee、结算规则与参考价；有缺失就保持只读。
3. 预取下一轮对象，但不转移上一轮未结清订单或持仓。全局账户页继续跟踪旧轮次订单与可领回仓位。
4. 新市场模板（ETH 5m、BTC 15m、非加密事件）提供自己的 `match / parse / resolution / settlement / volatility_model`；其它组件不判断“是否 BTC”。

## 模块列表（现阶段与未来）

| 模块 | 当前交付期望 | 扩展路径 |
|---|---|---|
| BTC Up/Down 5min | **预装、实际开发重点** | 使用真实结算规格、双阶段研究策略 |
| ETH/SOL/XRP 等 5min | 接口与占位模块，`Not Implemented` | 新模板+对应数据源+结算/市场验证后再启用 |
| BTC 15min/1h | 只留模板样例 | 独立行情/策略/截止时间和 backtest 参数，不改 BTC5m |
| 非加密事件市场 | 不启用 | 通用 outcome 模型、跨天结算、不同手续费和期限 |
| 多策略 | 先有 `BTC_5M_TWO_STAGE` + dummy 策略 | 策略热替换仅限 paper；live 策略更换须人工审核及重启 |
| Portfolio 资产雷达 | 账户中心、历史净值图接口 | 多市场/多钱包只读聚合，资金来源分离 |
| 高级风控 | 当前止损/预算/WS stale/geo | 不依赖策略的账户/品种/市场组聚合敞口 |
| 批量导出与回放 | 保留已有 `log/` 与 recorder | 对比策略、参数回测、样本外评估 |
| 扩展型 AI 工具 | 原右侧副驾驶，统一工具路由 | 只读工具注册和 `ai log` 提案，绝无交易权限 |
| 通知和日历 | 事件 API 预留 | 不默认启用远程推送含敏感数据 |

## 数据库建议（至少逻辑上独立）

`accounts (account_id, venue_id, wallet_type, wallet_address, signer_ref, auth_capability, mode)`
`markets (market_id, template_id, venue_id, outcome_set, rules_version, start_utc, end_utc)`
`balances (account_id, asset_type, chain_id, source, observed_at, total, available, reserved, stale)`
`positions (account_id, market_id, outcome_id, confirmed_shares, weighted_cost, current_value, status, source)`
`order_intents (intent_id, account_id, strategy_id, market_id, proposed_budget, approved_budget, status)`
`orders (account_id, venue_id, order_id, intent_id, status, latest_source_time, quantity, matched_quantity)`
`fills (account_id, venue_id, trade_id, order_id, price, shares, fees, status)`
`settlements`, `events`, `plugin_registry`, `schema_migrations`, `metric_definitions`。

数据库实际设计应有唯一键、索引与可恢复重建的投影。`log/events` 为事实不可变源，数据库 materialized views 可重新生成。时间统一 UTC、金额 Decimal 的字符串或整数最小单位（绝不混 float）。

## 模块版控/生命周期与安全

内部可信 `manifest.json`：`id, version, contract_version, module_type, entrypoint, capabilities, dependencies, supported_venues, enabled_by_default=false`。示例文件 `contracts/extension_manifest.schema.json` 与 `contracts/sample_extension_btc_5m.json`。

外部插件未来再做**签名白名单、版本 pin、权限清单、隔离执行**；v3 只实现仓库内受审查的模块注册。安装/卸载不得删除账户历史。插件崩溃只能影响自身；AI 插件不准携带 `LIVE_*` capabilities。

## 验收反例（必须测试）

- 增加 `sample.eth.5m` 模块不修改 `BTC5mMarket` 或 `ExecutionGateway` 代码、仍自动出现在模块列表且显示 disabled。
- 新增非 Up/Down outcome，市场展示/仓位表依赖动态 outcome name，不因缺少 Down 报错。
- 关闭用户 WS 时 account mode 自动降级为 `UNKNOWN_ORDER_STATE`，只读的市场 WS 仍继续显示行情。
- 未经批准的策略申请 `LIVE_AUTOMATION` 一律被拒，并在 `log/` 留下拒绝事件。
- 切换市场、模型、账户后，已有订单和余额不串号，任何模块都不能拿错 account_id。
