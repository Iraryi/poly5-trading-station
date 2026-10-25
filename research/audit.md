# 接口与设计取证（2026-10-22）

读取时间与原始文档 SHA-256 见 `network-audit.json`。下列 confirmed 表示本次开发实际核对，不表示未来接口不会变化。

| 项目 | 本次证据 | 状态 |
| --- | --- | --- |
| 统一 Python SDK | 本地 `polymarket-client==0.13.0`；调用 AsyncPublicClient.get_market，交叉核对 condition_id 与 fee_schedule | confirmed |
| Data API v2 | `/v2/status` 实际 200；官方迁移文档采用 snake_case、cursor pagination；本地测试覆盖 5,000 行分页 | confirmed（用户账户未提供，未做个人账户联网验收） |
| v1 迁移时间 | 官方文档写明 2026-10-24 退役，应用不回退 v1 | confirmed |
| BTC 五分钟窗口 | Gamma eventStartTime/endDate 与 crypto 配置核对 300 秒；创建 startDate 不当作开局时间；CLOB token / condition 身份核对 | confirmed |
| 费用与最小份额 | 本次 BTC5m 返回 rate 0.07、exponent 1、min_order_size 5；代码逐市场读取，不硬编码此费用 | confirmed |
| 价格梯度 | CLOB 当前 tick 与 WS tick_size_change；不能用 Gamma 旧值覆盖 | confirmed |
| 官方最优报价与深度 | WS price_change / best_bid_ask 独立维护最优报价；完整深度出现最优价不一致时标 GAP 并重新拉快照，模拟成交要求深度可信 | confirmed |
| 结算参考 | 市场规则指定 Chainlink 60s TWAP；未拿其他交易所现价替代 | confirmed 规则；实时 TWAP 未连接 |
| 地区 | 官方 geoblock 返回出口 <地区> / blocked=false；原始 IP 已从保存样本去掉 | confirmed 单次观测；不代表完整资格，未核实 WS 与 HTTP 同出口 |
| 认证订单 | 实装固定 GET /data/orders 的 L2 HMAC 只读适配器；按已安装官方 SDK 验证签名格式 | unverified 实际凭据；无用户凭据，不宣称账户订单已连通 |
| 盈利案例与模型 | 没有策略盈利证据，自动策略关闭 | unverified；未完成要求中的社区案例、walk-forward 与多日评估 |

官方来源：

- [SDK](https://github.com/Polymarket/py-sdk)
- [Data API 迁移](https://docs.polymarket.com/migrate/data-api-v1-to-v2)
- [更新日志](https://docs.polymarket.com/changelog/predictions)
- [市场发现](https://docs.polymarket.com/market-data/discover-markets)
- [市场详情与费用](https://docs.polymarket.com/market-data/market-details)
- [Market WebSocket](https://docs.polymarket.com/api-reference/wss/market)
- [User WebSocket](https://docs.polymarket.com/api-reference/wss/user)
- [地区检查](https://docs.polymarket.com/api-reference/geoblock)

布局参考已浏览的官方产品资料：[Quantower 工作区](https://www.quantower.com/interface-features)、[Quantower DOM 栏位](https://help.quantower.com/quantower/trading-panels/dom-trader/dom-trader-columns)、[TradingView Desktop](https://www.tradingview.com/desktop/)、[IBKR Mosaic](https://www.interactivebrokers.com/en/?f=/en/general/education/pdfnotes/WN-TWSMosaic.php)。借鉴多面板、固定盘口栏与紧凑工作区的组织方式；POLY5 图标和界面由本项目自行绘制。
