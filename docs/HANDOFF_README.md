# Poly5 Trading Station v3 — Windows 账户/交易中心 + AI 副驾驶 + 可扩展平台

**2026-10-22 开发更新：已实现 3.1.0-dev.5 原生 Windows 开发版。** 成品位于 `D:\POLYMARKET\outputs\3.1.0-dev.5\Poly5\Poly5.exe`，启动快捷方式在 `D:\POLYMARKET`。已提供自动 PAPER 和识别旧版本的安装更新入口；当前自动策略是 Up 单侧盘口 ROI 模型，用户澄清的“基准线＋趋势”策略及止损对照仅完成离线研究，尚未接入运行程序。当前能力与验证边界以 `DEVELOPMENT_REPORT_DEV4.md` 和 `D:\POLYMARKET\outputs\validation\dev5` 为准。下文保留原始规格交接内容，不能视为已实现功能清单。

> **Codex 开发封包，不是可运行 EXE，不是真实 Polymarket 账户余额或自动交易软件。** 本版包含 v1/v2 的全部策略、界面、网络/geo、AI 以及日志要求，在此基础上新增**账户资产/实时买卖状态/钱包连接/持仓订单/未来模块扩展接口**。

## 第一步：交给 Codex

解压整个 ZIP 到 Codex 可访问的工作目录，先把下方指令交给它：

> 阅读 `CODEX_V3_ACCOUNT_PLATFORM.md`、`AGENTS.md` 和本文，之后依次阅读 `ACCOUNT_AND_TRADING_UI.md`、`API_ACCOUNT_V2_ADAPTER.md`、`EXTENSION_ARCHITECTURE.md`、`ACCOUNT_EVENT_CONTRACT.md` 以及全部 v2 的开发规格，按 v3 的阶段划分继续真实实现。必须制作可运行的 Windows x64 EXE、托盘、右侧可缩 AI、BTC5m 实时行情、账户只读中心与认证后订单状态同步；账户、策略、市场、模块未来可扩展。不得以 HTML 样机代替 exe，也不得假造个人余额、订单、盈亏。**所有真钱下单默认禁用**。首先核对 2026-10-24 退役的旧 API 与 Data API v2，按实际官方接口和用户合法授权执行。以实测和截图证明每个阶段交付；留有阻碍则准确说明。

## v3 新增内容

- `CODEX_V3_ACCOUNT_PLATFORM.md`：**最高优先级的开发总工单**，兼容 v2 开发线，并列出账户/交易/模块各验收阶段。
- `ACCOUNT_AND_TRADING_UI.md`：用户真的能看到现金、可买余额、挂单冻结、持仓、盈亏、当前买入/卖出状态的 Windows GUI；有清楚的交易预览与受限按钮。
- `API_ACCOUNT_V2_ADAPTER.md`：**官方 Data API v1 2026-10-24 停用**的兼容指南；地址只读/认证订单/钱包余额区别、User WebSocket、钱包类型、多种凭据。
- `EXTENSION_ARCHITECTURE.md`：市场/策略/账户/订单执行/插件/AI 工具的版本化接口；BTC5m 是第一种可用模板，其它功能作为可验证占位、不假装已经实现。
- `ACCOUNT_EVENT_CONTRACT.md`：账户、余额、持仓、挂单、已买入/部分成交等状态事件的日志契约，继续使用 v2 的 `log/` 不另开混账。
- `contracts/extension_manifest.schema.json`、`contracts/account_event.schema.json`、示例：离线 JSON 契约。
- `account_projection.py`：**不联网、不签名、不下单**的金额未知值、持仓/余额估值、订单事件幂等与权限门槛参考算法。
- `ui/interactive_mock.html`：新增**账户中心、持仓订单、快速交易票据（禁用）、扩展模块**的可操作导航；全部金额显示 `—` 而非伪造数据。
- `ui/account_preview_v3.png`：实际离线渲染的账户界面预览。

## v2 继承内容

- Windows `.exe` + 默认托盘后台 + 主窗口右侧可伸缩的 AI 副驾驶 + DSH 多 provider 与本地 API 配置。
- 官方 geo 查询辨认 VPN 出口和交易限制，不绕过地区规则。
- BTC 5min 双阶段策略、ROI 与 DOI 分列、Chainlink 结算/盘口/费用核对、$5 单笔预算研究；直播交易锁定。
- `log/` 不可被 AI 改动的真实事件账本 + `ai log/` 能创建/修订 Codex 意见的 AI 工作区。

## 已实测什么（不能夸大）

在本封包制作环境里执行 `python -m unittest discover -s tests -v`：**33 项离线单元测试通过**，包括既有 17 项和新增 16 项。离线 Chromium (Playwright) 的 `ui/test_mock_v3.py` 测了**11 个界面操作步骤**：账户导航、未知余额、订单分页标签、模块页、跳转、AI 右栏收缩等；无 JS 报错。

真实账户余额未读、订单 REST/WS 未连、扩展接口未实现真正服务、Windows 原生窗口/托盘/EXE 未构建。因此**不能说成品已经完成**。

## 安全与隐私

- 不要求用户把私钥/助记词发给 AI 或 Codex 聊天，不记录任何 L2 secret / API 密钥。只读钱包公开地址和交易签名权限分开。
- 缺现金/订单数据时：`—` 或 `UNKNOWN`，不擅自写为 0、无持仓、无挂单或交易成功。
- AI 只有脱敏观察/诊断/编写 `ai log/` 意见权；没有交易签名器或自动调高风控阈值的权限。
- 地区 `blocked` / `UNKNOWN` 或 WS stale 时不许新增真钱订单；不得使用 VPN 绕过官方限制。

## 官方资料与过时 API 提醒

- [Data API v1 → v2 迁移（2026-10-24 退休）](https://docs.polymarket.com/migrate/data-api-v1-to-v2)
- [Data API v2](https://docs.polymarket.com/api-reference/data-api/overview)
- [官方统一 SDK 迁移](https://docs.polymarket.com/migrate/clob-sdk-to-unified-sdk)
- [钱包类型与认证](https://docs.polymarket.com/trading/wallets-auth)
- [实时用户订单 WS](https://docs.polymarket.com/api-reference/wss/user)

本文信息核对于 2026-10-22，Codex 实现时仍须联网校验最新状态。
