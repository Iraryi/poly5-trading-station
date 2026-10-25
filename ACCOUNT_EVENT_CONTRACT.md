# v3 账户与交易数据新事件（接续原 LOG_DATA_CONTRACT.md）

## 强制原则

v2 原行情、ROI/DOI、网络/VPN 与 AI 事件**全部保留**；v3 为多账户/多市场补充统一事件 envelope / payload。事实事件仍写 `log/`，AI 仅编辑 `ai log/`；不是新建第二套不兼容账本。

所有账户/订单/余额事件须有：`schema_version`, `event_id`, `event_type`, `observed_at_utc`, `source_at_utc`, `mode`, `venue_id`, `account_id`, `market_id|null`, `condition_id|null`, `outcome_asset_id|null`, `source`, `source_status`, `freshness_ms|null`, `correlation_id`, `payload`, `quality_flags`；交易事件再带 `intent_id`, `order_id`, `trade_id`（没有填 null）。所有 id 有来源且允许真实协议额外字段。

新事件名建议：
- `account.bind_requested`, `account.bound_readonly`, `account.authenticated`, `account.disconnected`, `account.switched`, `account.capabilities_changed`, `account.reconcile_started`, `account.reconcile_done`, `account.reconcile_mismatch`
- `balance.snapshot`, `balance.changed`, `balance.stale`, `allowance.changed`, `capital.reserved`, `capital.released`, `capital.unknown`
- `position.opened`, `position.changed`, `position.closed`, `position.redeemable`, `position.redeemed`
- `order.intent_created`, `order.preflight_rejected`, `order.submission_started`, `order.submission_unknown`, `order.accepted`, `order.open`, `order.partial_fill`, `order.filled`, `order.cancel_requested`, `order.canceled`, `order.cancel_failed`, `order.rejected`, `order.reconciled`
- `fill.confirmed`, `fill.reversed`（后者如有官方支持，否则不可编造）
- `plugin.registered`, `plugin.disabled`, `plugin.capability_denied`, `schema.migrated`, `execution.mode_changed`

## 余额快照 payload 最小字段

`wallet_type, wallet_address_masked, asset_type, chain_id, balance_total, balance_available, balance_reserved, allowance, portfolio_position_value, total_estimated_equity, realized_pnl_today, unrealized_pnl, currency, units, valuation_at_utc, balance_source, estimate_method, reconciliation_status`。

各金额类型必须能表示 `null` / unknown，**不能用 Python None 在显示层擅自变成 0**；金额序列化十进制字符串如 `"5.00"`。总权益如数据时刻冲突或会重复计入挂单预留，显示为 unknown 而非粗略相加。敏感地址仅展示最后几位。

## 订单状态事件 payload

`market_template_id, outcome_name, side, order_type, requested_max_spend, requested_shares, quoted_price, average_fill_price, cumulative_filled_shares, remaining_shares, total_fees, order_state, risk_gate_results, parent_intent_id, source_order_id, source_event_id, server_updated_at_utc, rest_ws_reconciled, user_initiated, strategy_version`。

- `order_intent`/`submitted` ≠ 真实成交；`status=UNKNOWN` 不能当 canceled，也不能再次发单。
- `order_id`+`trade_id` 在账户作用域内具唯一性；重复事件幂等。若 WS 晚于 REST 到达，不能倒退累计已成交份数。成交确认包含费用才能进入已实现盈亏；尚无费用记录则 PnL 估计标示不完整。
- 手动交易与策略自动交易共享底层状态机，另用 `trigger_source` 区分；监控/paper/live/replay 账户分簿。
- 历史持仓为 0 不等于“从未买过”，已结算、已关闭和已领回各自有独立生命周期。

## 每次余额变化/账户状态都要可追溯

保留 `old_value`, `new_value`, `delta`, `difference_reason`, `source`, `reconciled`, `at`；单独标注充值/提现/链上手续费/交易盈亏/重估/资金预留/成交确认。只有凭两份可对账快照才发布变化原因，不明原因记录 `UNKNOWN` 并提示。

## 看板图表

支持余额曲线（有来源）、持仓市值、净资产估值、实时敞口、挂单数量、入场累计成本、成交概率/滑点、手续费累积、真实利润、ROI 平均/分布/累计、净胜率分母、退出原因与 network stale 时间对比。账户、多策略、不同市场产品按面板筛选，不把不同属性合并到一个“胜率”。

## 数据隐私

账号地址可用于公开 Data API 读取，但发送给 AI 时默认脱敏；不记录 token、私钥、签名器密钥、L2 secret / passphrase、session key、浏览器 cookie。内部加密密钥库和普通 log 文件权限分离。日志导出默认隐藏全地址，可由用户本机确认显式展开。
