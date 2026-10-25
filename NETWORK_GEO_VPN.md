# 网络、VPN 地区和合规状态：一等公民功能

这是用户特别强调的最高优先级之一。**不要将「VPN 软件显示的地区」等同「Polymarket 看到的地区」**，也不要将 `HTTP 200` 等同实时交易可用，更不能将 `blocked=false` 等同法律上一定允许交易。

## 权威来源和准确字段

官方文档：https://docs.polymarket.com/api-reference/geoblock

`GET https://polymarket.com/api/geoblock` 返回 `blocked: bool`, `ip`, `country`, `region`。官方强调这个 endpoint 在 `polymarket.com`，而不是 `clob.polymarket.com`。请求必须从**应用本机、使用与将来交易请求相同的代理/系统出口策略**发出；不得经另一台中继替用户伪造结果。

### 五类地区概念要分开

1. Windows VPN/代理客户端配置的“已选节点名称与标称地区”（如果官方开放配置接口/用户提供；读不到就显示未知），**不作为交易地域授权依据**。
2. Polymarket geoblock 接口响应中的 **实际观察到的出口 IP/country/region 与 blocked**，注明检查时刻和 TTL。
3. 可选独立公共 IP 回显服务看到的出口 IP/地区，用于比对代理分流、DNS 或多出口；不是判定 Polymarket 限制的依据。须声明隐私与对外访问；失败不得代替官方结果。
4. 应用的 `Gamma REST / CLOB REST / CLOB WS / PolyBolt WS / AI API` 各自真实请求路径是否通过 VPN；可能存在 split tunnel / 系统代理与客户端代理不一致。不可宣称所有线路相同，除非逐个验证。
5. 用户真实使用位置/法律资格/平台条款是另一层验证，不可仅依靠 IP 定位而忽略。若存在交易限制或不确定，禁用自动真实交易、继续合规只读研究。

## 检查节奏与网络状态机

- 启动时检测；代理设置变更、网络接口变更、VPN 重连、Polymarket 出口 IP/地区变更或 WS 重连后立即复查；稳定时默认 60 秒缓存并遵守官方限制。地理检测不能因为不断轮询而触发 rate limit。
- 一个独立但低开销的诊断流每 5–15 秒采样：Gamma REST、CLOB `/book` 响应时间、CLOB WS 最近数据年龄、PolyBolt TWAP 最近数据年龄、TLS 握手/DNS 失败、HTTP 403/429/5xx、丢帧/seq gap、断线重连次数与持续秒数；**禁止为了监控延迟高频向交易接口发真实订单**。
- 记录 `dns_ms`, `connect_ms`, `tls_ms`, `first_byte_ms`, `request_ms`（能够可靠采集的字段）、`p50/p95`, `ws_age_ms`, `last_success_utc`, `consecutive_failures`, `proxy_mode`, `observed_exit_country`, `observed_region`, `geoblock_blocked`, `change_reason`, `diagnostic_confidence`。
- HTTP 状态码、timeout、证书错误、JSON 验证失败、服务端限制、序列号缺口、路由变更应分别分类，不要统称「VPN 挂了」。
- 网络状态：`OK` / `DEGRADED` / `DISCONNECTED` / `GEO_BLOCKED` / `GEO_UNKNOWN` / `ROUTE_CHANGED`。前四字样含义要严谨；`ROUTE_CHANGED` 可以并列标记，状态要带上 `checked_at`, `source` 和日志 event_id。
- **出口地区突变**：生成 `network.route_changed` 和 `geo.status_changed` 事件（含旧/新、时间、证据），通知副驾驶和托盘；停止新风险敞口、要求人工确认恢复；只读行情采集可以在合法可访问时继续。避免掉线造成订单状态不明时擅自重复提交。
- API 未响应/状态不可信时 `blocked=unknown`、禁止任何真实新开仓。频繁变动时对 warning 做 debounce，但每一次变化写入 Log。不得通过切节点、换代理、远程机、伪装 IP 来绕过地理限制。

## 可视化与易用性

显示一个专门的「网络与地区」卡片：

- `客户端节点：未知 / 配置所称XX（未验证）`
- `Polymarket 实测出口：国家地区 | IP（屏幕可选择打码） | 最近检测秒数`
- `限制：可观察 / 不可新单 / 地区不明`（严格按官方 response 及当地限制输出，不将 `blocked=false` 译为「已合法」）
- `CLOB REST：延迟p50/p95、最后成功；WS：最近消息年龄、重连次数；Chainlink TWAP：最后有效帧`
- `路由发生变化：旧值→新值；建议检查 VPN 客户端和系统代理`；提供「重新检测」按钮（并发保护/节流）和诊断报告导出。

如系统 VPN 接口不允许读取某个客户端（例如闭源应用），不要通过注入其进程、搜集私人浏览历史或读取明文账号配置实现；只汇报**实测出口**与**用户手工填写节点标称地区**。

## 核心验证用例

- A：VPN 软件标“日本”，官方检测到别的地区，UI 同时显示两个不一致，不自动挑选任何节点。
- B：Polymarket 地区检查返回 blocked=true，任何实时自动下单路径必须关闭；AI 不得建议切到不受限地区绕过。
- C：WebSocket 数据停止但 geoblock 仍正常，必须判为行情 stale、禁入场；不能只看 geo 正常就显示在线。
- D：geoblock 请求失败，状态 `UNKNOWN`，禁止新风险；保留已有日志。
- E：代理切换后 DNS/HTTPS/WS 异步恢复，分别展示延迟与最后成功时刻；日志有事件时间和旧/新状态。
- F：App 代理出口和外部 IP 服务不一致，诊断不能武断认定 Polymarket 出口一定被篡改。
- G：地区限制从允许变为 close-only 或不明时，保留并跟踪已知持仓风险状态，不能错误声称必能平仓。

官方文档包括受限司法辖区的 different `block completely`/`close-only` 类型；必须以**运行时官方事实**为准，不将本文件冻结成永久允许国家白名单。
