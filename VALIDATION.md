# v3 离线验证记录（2026-10-22）

## 确实跑过的项目

- 在当前容器 Python 3.13 环境执行 `python -m unittest discover -s tests -v`：**33 项 / 33 项通过**。既有 17 项：ROI / 买卖深度 / AI log 审计与安全 / schema；新增 16 项：未知账户金额的语义、订单累计成交量幂等、部分成交、撤单/成交优先、账户隔离、实盘通道所有权限标志校验、扩展清单/事件 JSON Schema 等。
- 使用本地 Chromium（通过 Playwright 的 `set_content` 加载本地 HTML）运行 `python ui/test_mock_v3.py`：账户中心导航、金额未知值、订单 tab、扩展模块列表、概览到订单跳转、右侧 AI 收缩展开等 **11 个检查点** 通过，**0 个 JS pageerror**。
- `ui/account_preview_v3.png` 是实际 Chromium UI 样机截图。UI 自带多处 `DEMO / NO LIVE DATA` 说明；原有 `ui/mock_preview.png` 是 **v2 的旧截图**，注意以 `account_preview_v3.png` 看 v3 的账户页。

## 完全没有实测、不可声称完成

- 尚未连接 Polymarket 自己的真实钱包、余额、用户订单、交易历史或 WS，也未提交任何真钱订单。
- 尚未构建真正的 Windows `.exe`、托盘服务、原生 GUI 和安装包，只有浏览器离线 UI 样机供 Codex 对照。
- 尚未验证 SDK 0.10.0+ 在目标机器下的具体版本、交易接口签名与交易资格；Codex 必须联网实测。
- v3 扩展契约与其示例模块只是**接口设计和数据样例**，不是已注册可用的所有市场/策略。
- 本参考 `account_projection.py` 只做纯算法和门槛，不运行任何账户接入、交易、签名、地理检测、远程 AI 或文件写入。

## Codex 开发与验收方法

逐个里程碑报告：代码提交、运行测试、真实 Win x64 截图/视频、脱敏 API JSON 样本、来源时间与 API 版本、完整账户/订单 UI 状态、网络断线/重连/部分成交/买入不可达/地理限制用例。**有阻碍报告阻碍，不制造实盘成功截图。**
