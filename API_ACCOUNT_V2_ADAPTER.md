# 2026-10-22 官方账户/API 兼容性笔记 — Codex 联网实测前不得假设接口不变

## 最新的迁移重点

Polymarket 官方写明：**Data API v1 2026-10-24 退休**；新代码直接用 v2；官方统一 Python SDK `polymarket-client` 和 TypeScript `@polymarket/client` 对 Data API v2 的支持从 **0.10.0** 起。`GET /v2/*` 返回 `{data: ..., pagination: ...}`（分页时），row 用 snake_case，且分页是 cursor，不是 offset。Codex 需要首先检查运行日的 changelog 与实际返回 JSON。

官方资料：
- https://docs.polymarket.com/migrate/data-api-v1-to-v2
- https://docs.polymarket.com/api-reference/data-api/overview
- https://docs.polymarket.com/api-reference/wallet/get-portfolio-value
- https://docs.polymarket.com/api-reference/wallet/list-positions-for-a-user-or-market
- https://docs.polymarket.com/migrate/clob-sdk-to-unified-sdk
- https://docs.polymarket.com/trading/wallets-auth
- https://docs.polymarket.com/api-reference/wss/user
- https://docs.polymarket.com/api-reference/trade/get-user-orders

## 建议的数据适配路径

| 功能 | 原始来源（需 Codex 实测） | 使用原则 |
|---|---|---|
| 市场名单 / 最新五分钟轮 | Gamma 事件/市场 + 当前 CLOB 确认 | `MarketTemplate` 提供解析，不能只猜 slug |
| 公开持仓 | `GET https://data-api.polymarket.com/v2/positions?user=0x...&status=OPEN` | 读 `data` + `pagination.next_cursor`，核对资产与 condition、完整拉取后原子更新快照 |
| 待领/关闭仓位 | 同上 `status=REDEEMABLE/CLOSED/...` | 不把 CLOSED 当开放持仓；领回可能是独立动作 |
| 持仓估值 | `GET /v2/value?user=0x...` | **只有组合持仓市值**，不是可用现金，也不是资金全貌 |
| 交易历史 | `GET /v2/trades?user=...`、`GET /v2/activity?user=...` | 游标分页与去重，不能把交易费用/充值提现算成交易利润 |
| 历史 PnL | `GET /v2/user-pnl?user=...` | 与逐笔 ledger 对账，不能假设与本地口径相同 |
| 数据新鲜度 | `GET /v2/status` | Data API 有可能滞后于 CLOB；为每字段保留来源时间 |
| 账户当前订单 | 官方 SDK `SecureClient.listOpenOrders()` / authenticated CLOB 订单 REST | 需要合规授权的 API 凭据，且要拉全页、重连对账 |
| 订单成交更新 | `wss://ws-subscriptions-clob.polymarket.com/ws/user` | 认证 user WS 事件与订单 REST 协同，校验时间与去重 |
| 钱包余额/allowance | 官方 SDK / 合规的链上余额查询 / 认证的 CLOB balance-allowance（依钱包类型） | 真正「可用下单现金」需要单独核对；不能以 `/v2/value` 代替 |
| 手动/自动订单 | 官方统一 SDK 的 `SecureClient` 相应方法（**默认禁用 live**） | 所有写请求通过单独的风控/权限 `ExecutionGateway`，AI 无调用权 |

## 钱包身份

官方钱包类型：2026-05-04 起新创建的账户使用 **Deposit Wallet**，旧用户可能是 **Proxy Wallet**（Google/Magic Link）或 **Safe Wallet**（外部 signer）。钱包资产地址、认证签名账户、资金托管地址可能不是同一个。先读官方文档确定 `account_wallet` / `signer` / `wallet_type` / `chain_id`。地址关联不足时停止绑定，不能把别人的钱包仓位拿来当自己的余额；只读 watch-only 可以明确标注为公开地址而非已认证身份。

## 明确记录不确定性

- `/v2/value` 是 mark-to-market 的持仓价值，含 unresolved combo at cost；官方接口**没有据此推出“可买余额”**。如果现金可用额度不能从权威来源校验，显示 `N/A (未完成余额同步)` 并禁用真钱新增订单。
- 订单 REST 可能与 user WS 到达先后相反；`order_id`/`trade_id`、cumulative matched、provider event id、event source time 必须协调；实际安全合并策略由真实字段确认，不得假设 WS 保证严格有序。
- 订单类型和 minimum order、market convention、订单审核、费用、签名、资金/链上结算语义要按实际市场校验。
- SDK 的 L2 API 凭据、wallet signer、builder 凭据和给 AI provider 的 key **严格隔离**；不允许将交易签名资料暴露给 AI、日志或远程诊断服务器。
- 不因地理位置显示 allow 就假定可以合法实盘，仍需符合平台条款及用户资格；blocked/UNKNOWN 禁止新交易。

## 实际代码迁移的坑

`/positions`（v1）响应为数组与 camelCase 字段，**v2 不一样**；`/v2/positions` 回应 `{data: [...], pagination: {...}}` 且是 `current_size`、`avg_price`、`condition_id` 等 snake_case。

官方统一 SDK 示例 `SecureClient.listOpenOrders({market,assetId})` 迭代分页；`SecureClient.listAccountTrades()` 代替旧 SDK `getTrades()`；下市价买单 `maxSpend` 是包含费用的最大支出限制，不能遗漏造成预算超额。

## Codex 必须输出的接口核查报告

对每个 API 记：SDK/package version、请求路径/方法、账户模式、auth method（**绝不打印凭据**）、HTTP 与返回 schema hash、字段映射、限流/Retry-After、数据新鲜度、断线恢复、测试日期、关键用例截图和脱敏 JSON 样本。不能仅凭文档就声称已经连接用户账户。
