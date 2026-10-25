# 2026-10-22 已核对研究起点（Codex 必须复核）

以下为**开始验证用的资料**，不是承诺下单端点都已由本包连通。Codex 必须记录在线实测结果（失败也报告）和文档版本。

## 高优先级官方来源

1. 当前变更记录： https://docs.polymarket.com/changelog/predictions
   - 2026-04-28 CLOB V2；2026-08-14 BTC 5m 60 秒 Chainlink TWAP；2026-09-04 taker delay 150ms；2026-09-15 PolyBolt 上线；2026-10-02 Chainlink 为 price.crypto 默认提供方。
2. 官方 Python 统一 SDK： https://github.com/Polymarket/py-sdk
   - `pip install polymarket-client`，`from polymarket import AsyncPublicClient`；可按稳定版本 pin，核对 SDK 实际函数名。
3. 官方 SDK 的详细市场发现： https://docs.polymarket.com/market-data/discover-markets
   - Gamma `https://gamma-api.polymarket.com/events/slug/<event-slug>`，有市场版本/condition/outcome id；必须验证时间与币种。
4. 官方实时订单簿（market stream）： https://docs.polymarket.com/market-data/realtime-data
   - `book`, `price_change`, `best_bid_ask`, `market_resolved`, `new_market`，和用户侧成交事件分开。
5. 官方 Chainlink TWAP/参考价（PolyBolt）： https://docs.polymarket.com/api-reference/live-data/overview
   - `wss://ws-live-v2.polymarket.com/ws`；需 CLOB API credentials；`price.crypto.twap` + `btcusd` + `window_seconds:60`，注意 `seq` 和 `source`。
6. 当前手续费： https://docs.polymarket.com/trading/fees
   - Crypto 类目前常见 `fee=shares*0.07*p*(1-p)`（exponent 1），但不得硬编码覆盖实际市场参数。
7. Market trading `feeSchedule`： https://docs.polymarket.com/market-data/market-details
   - 读取 `feesEnabled, feeSchedule(rate, exponent, takerOnly, rebateRate)`，不要仅按旧教程估算。
8. 交易与精度： https://docs.polymarket.com/trading/place-orders
   - FOK/FAK/GTC/GTD 语义、`min_order_size`、`tick_size`、2026 当前统一 SDK 下单字段。
9. 成交与用户 WS： https://docs.polymarket.com/trading/realtime-order-updates
10. 地理封锁 API： https://docs.polymarket.com/api-reference/geoblock
    - `GET https://polymarket.com/api/geoblock`，不要用代理或位置伪装规避限制。
11. 官方 Market Data 示例： https://github.com/Polymarket/agent-skills/blob/main/market-data.md
    - 部分旧 SDK 示例可能滞后于统一 SDK，必须核对当前文档。
12. 5 分钟历史市场规则示例： https://polymarket.com/event/btc-updown-5m-1790027100
    - 2026-09 市场 Rules 明确 Chainlink BTC/USD 60 秒 TWAP；网页仅作规则参考，不代表当前市场 API 状态。

## 对抗性实战例子（未经独立审计，不能视作已验证的赚钱策略）

- https://www.reddit.com/r/CryptoTradingBot/comments/1t7g33s/hiii_again/  自述 paper 87% 胜率因“幽灵成交、早退双边费用”失真，研究其漏洞复现。
- https://www.reddit.com/r/polymarket_bets/comments/1sf1t5j/built_a_profitable_polymarket_bot_but_slippage_is/  自述滑点严重损害盈利；要求核实样本、盘口和成交真实性。
- https://www.reddit.com/r/Polymarket/comments/1un85mg/i_spent_7_months_testing_every_strategy_on/  自述 4600 轮研究；注意发表于 2026-08-14 结算改动之前，结论不能无条件迁移。
- https://www.reddit.com/r/Polymarket/comments/1u484fc/what_market_is_good_for_bots/  其他开发者自述 5m BTC 噪声、点差与亏损经验，作为负面案例。

## 审核要点

每个外部例子需记录：是否真实成交/已赎回、统计窗口、结算规则年代、费率、订单种类、是否用买卖盘而非 midpoint、样本外表现、代码是否可运行、是否属于营销帖。任何“日赚几百”的自述没有成交凭据不能作为设计指标。

## v2 Windows 界面与地理验证新增官方资料

- Qt for Python QSystemTrayIcon：https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QSystemTrayIcon.html （真正托盘图标、双击/菜单/通知；通知不保证总显示）
- Qt for Python QDockWidget：https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QDockWidget.html （参考右侧 dock 思路，但用户要求的最终行为是主窗内部可伸缩侧栏，可用 QML 自定义实现）
- Qt for Python 官方打包：https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html （选择构建工具需在真实 Windows 环境验证）
- Polymarket 官方 Geographic Restrictions：https://docs.polymarket.com/api-reference/geoblock （`GET https://polymarket.com/api/geoblock` 会给 `blocked/ip/country/region`，平台区分 full-block 和 close-only）
- CLOB 真实 stream：https://docs.polymarket.com/market-data/realtime-data （同源行情时间戳/盘口，诊断 WS gap）

历史协议和当前可用性均需 Codex 开发时二次校验。VPN 地区检查用于**准确诊断/遵守限制**，不得自动变更代理以绕开政策。

---
## v3 账户及 API 迁移资料（2026-10-22）

旧资料出现 `/positions`, `/value`, `getOpenOrders` 时请先校验是否过期：Data API v1 2026-10-24 退休。Data API v2 使用 `{data, pagination}` + snake_case + cursor；统一 SDK 0.10.0+。

- 官方 v1→v2: https://docs.polymarket.com/migrate/data-api-v1-to-v2
- Data API v2 Overview: https://docs.polymarket.com/api-reference/data-api/overview
- Portfolio Value: https://docs.polymarket.com/api-reference/wallet/get-portfolio-value
- Current Positions: https://docs.polymarket.com/api-reference/wallet/list-positions-for-a-user-or-market
- Unified SDK migration: https://docs.polymarket.com/migrate/clob-sdk-to-unified-sdk
- Wallets/Auth (Deposit/Proxy/Safe): https://docs.polymarket.com/trading/wallets-auth
- Account User WS: https://docs.polymarket.com/api-reference/wss/user
- Authenticated Orders: https://docs.polymarket.com/api-reference/trade/get-user-orders

**不要把这些来源仅列在 Markdown 而不实际核对响应样本、版本与当前限额。**
