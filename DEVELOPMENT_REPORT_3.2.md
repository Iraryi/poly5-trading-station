# POLY5 3.2.0-dev.1：模型与真实账户接入准备

2026-10-23，Asia/Shanghai。Windows 原生交易台，支持行情、纸面模拟、账户认证只读、固定模型影子预测。此版本不包含真钱下单、撤单、链上授权或赎回适配器；训练台保持独立运行。

## 模型接入

“模型与接入 → 读取训练台模型”检查 D:\POLYMARKET\trainer\tasks 下六类任务的 best.json，优先取部署重训版。校验完成状态、明确的特征列表、训练截止时间、模型路径和 SHA-256。仅接受训练目录 models 内的本机 joblib 文件，不支持导入未知外部模型。

选择一行并点击“固定所选版本并开始影子预测”，将模型及清单复制到交易台数据目录 models/releases，记录源模型、任务、特征契约、文件哈希和创建时间。模型选择记录到事实账本。训练台随后更换冠军，不会改变这份副本；恢复归档版本和停止影子预测都保留文件。

推理在后台线程运行，单线程 CPU 限额。支持训练台 1.8 的方向、盘口感知方向、5/15 秒 ROI，以及森林、ExtraTrees、梯度提升、线性/逻辑回归和校准包装。特征与训练台保持一致：只使用本机已接收的 Chainlink 现货/TWAP、冻结开局基准、60 秒历史以及所需的实际双侧深度。启动时缺少开局基准则等下一轮，末分钟模型只在最后 60 秒运行。盘口感知模型用足额 $5 合约、费用另计的深度估计；预测值不是成交收益。

每两秒最多预测一次；预测与版本、市场、condition、时间和特征绑定，最多每十秒记录一次影子快照。换轮、数据过期、模型被改动、特征不完整、日志不可写时暂停预测。影子预测不进入任何模拟或真实执行路径。

模型文件来自受信任的本机训练台。joblib 不是不可信文件的安全格式，SHA-256 证明文件一致性，不是发布者签名。后续模型特征契约变化须升级适配器，不盲目加载新文件。

## 真实账户连接准备

账户中心增加本机连接向导：先选择资金钱包，再填写钱包类型、公开签名地址及 CLOB L2 API Key / Secret / Passphrase。凭据以 Windows 用户 DPAPI 加密，按账户 ID 隔离在 data/account-auth；不落入普通设置、AI 提示词或日志。Relayer/Builder 凭据不能代替 CLOB 三项，不要求私钥或助记词。

通过官方统一 SDK 0.13.0 的确定性钱包推导核对 EOA、Proxy、Safe 和 Deposit Wallet 所有者签名地址与钱包类型；无法确定的钱包或会话签名者继续只读公开监控，专用 Session Key 授权连接尚未实现。它不会借用参考行情的独立身份访问用户资金。

认证读取只有 GET /data/orders 和 GET /balance-allowance 两条固定白名单，不含余额缓存更新、授权、创建凭据或交易写操作。完整订单 REST 分页与 User WS 继续对账；切换账户/重新加载配置会终止旧订阅并清除认证健康。失败或数据超过 30 秒则降为异常/过期。已有部分成交按累计数量处理，不按 MATCHED 字样假定整单完成。

现金卡片明确标注为 CLOB 缓存余额，按 SDK 的六位抵押资产最小单位换算，并保留来源时间；可用资金、挂单占用和总资产继续未知，不由缓存余额或持仓市值推算。当前用户资金账户没有通过这次开发自动认证；接入后须由真实授权凭据实测。

## 执行准备与更新

接入页列出市场、地区、账户、订单对账、固定模型、模型验证和独立实盘执行器的状态。地区检查通过不代表完整交易资格。Settings 或模型字段不能启用 Live；实际执行器和人工启用尚未接入，模型盈利验证也不由历史准确率替代。

现有 PAPER 策略和账本与模型影子路径分离；无固定 30 秒持仓限制，每笔自动模拟 $5 合约、手续费另计。原交易账本仍有 512MB 运行总量上限，达到上限会暂停新增风险与影子预测，不能长期无维护运行。

安装器保留数据，支持旧版检测、更新/同版维护、拒绝降级和运行中拒绝覆盖。默认安装 %LOCALAPPDATA%\Programs\POLY5，数据 D:\POLYMARKET\data。更新只针对交易台，不替换 POLY5Trainer。验收记录在 D:\POLYMARKET\outputs\validation\3.2.0-dev.1。

## 核查资料

- https://docs.polymarket.com/changelog/predictions
- https://docs.polymarket.com/trading/wallets-auth
- https://docs.polymarket.com/trading/session-keys
- https://docs.polymarket.com/api-reference/trade/get-user-orders
- https://docs.polymarket.com/api-reference/wss/user
- 本机 SDK 0.13.0：models/clob/account.py（余额最小单位）、_internal/actions/orders/math.py（六位抵押资产精度）、_internal/actions/account.py（余额 GET 参数）、_internal/wallet.py（类型与确定性钱包推导）。

以上用于核对接口，不构成收益证明。实际连通和测试结果以同版本验收文件为准。
