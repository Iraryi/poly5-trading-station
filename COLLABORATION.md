# Codex / Claude 共同维护记录

更新日期：2026-10-23。本文件说明已实施的契约、验证入口和工作分界；完成状态以最新 JSON 验收记录及只读诊断为准。

## 文件和进程

| 内容 | 位置 |
| --- | --- |
| 源代码 | D:\POLYMARKET\Poly5_Trading_Station_v3 |
| Python | D:\POLYMARKET\.venv\Scripts\python.exe |
| 训练数据与模型 | D:\POLYMARKET\trainer |
| 训练台安装 | %LOCALAPPDATA%\Programs\POLY5Trainer |
| 交易台安装 | %LOCALAPPDATA%\Programs\POLY5 |
| 交易台数据 | D:\POLYMARKET\data |
| 产物与验收 | D:\POLYMARKET\outputs；outputs\validation\trainer-1.9.1 |
| 本轮临时目录 | D:\POLYMARKET\work；work\trainer-1.9.1-smoke |
| 最新只读诊断 | outputs\diagnostics\trainer-latest.json |

不要依赖文档中的旧 PID；从 state.json 的 run_id/pid/heartbeat 及实际进程路径重新识别。不要把 CLI 工作目录 C 盘当作项目根目录。此目录当前没有 .git；本轮提供统一 diff、源码 ZIP 和逐文件哈希，便于审查及防止互相覆盖。

## 当前训练契约

默认五任务：dynamic、late、direction、late_mkt、direction_mkt。roi5/roi15 文件、样本和进度保留为历史，不再默认优化固定持仓秒数。新 dynamic 模型需要重新拟合；它继承旧配置并用冻结的旧 Up ROI 模型作 Up 持仓特征，Down 对应先验特征为 0。

dynamic 输出是“继续持有的预期净 ROI”，不是方向概率。判断执行性时使用买/卖档位、Decimal 费用和至少 300ms 延迟，止盈失败继续持有。只有明显反向急跌且距离基准的保护不足才风险退出；市场到期是自然终点，不是任意持仓倒计时。

1.9.1 的 DATA_REVISION=boundary-v2：训练视图剔除 at_ms >= (start_s+300)*1000 的执行点；原始数据库行和旧 snapshots 不改写。旧批次 checkpoint 自动复制到 trainer/checkpoints/<task>/，新批次继承旧参数继续训练，所有试验仍留在 trials。对新版本应看当前 scope，不跨不同数据修订直接比较评分。

实时纸面持仓在 training.sqlite.dynamic_forward，模型及策略在入场前确定。status=holding 且 censored=true 仍然是持仓，只表示验证数据不完整；不能叫“未出手”，也不能把其收益算入完整前向验证。official_settlement 是模拟兑付，不能冒充真实卖单或现金赎回。

交易台 ModelBridge 当前只支持 trainer-1.8 的六任务影子契约，尚未支持 dynamic 持仓状态。不要只把 dynamic 加进白名单：它需要独立的持仓特征、模型固定、退出仿真、样本外验证及授权契约。此项列为后续工作，不在本轮自动接通真钱。

## 复现命令

从源代码目录的 PowerShell 执行。设置 PYTHONUTF8=1，TEMP/TMP=D:\POLYMARKET\work。交易台 tests 使用同目录导入，因此 PYTHONPATH 包含源目录及 tests。

```powershell
$env:PYTHONUTF8='1'
$env:TEMP='D:\POLYMARKET\work'; $env:TMP=$env:TEMP
$env:PYTHONPATH='D:\POLYMARKET\Poly5_Trading_Station_v3;D:\POLYMARKET\Poly5_Trading_Station_v3\tests'
& 'D:\POLYMARKET\.venv\Scripts\python.exe' scripts/diagnose_trainer.py --output D:\POLYMARKET\outputs\diagnostics\trainer-latest.json
& 'D:\POLYMARKET\.venv\Scripts\python.exe' -m unittest trainstation.test_revision trainstation.test_dynamic trainstation.test_trainer
& 'D:\POLYMARKET\.venv\Scripts\python.exe' -m unittest discover -s tests -p 'test*.py'
& '.\scripts\build_trainer.ps1'
& 'D:\POLYMARKET\work\installer-tools\nsis-3.13\makensis.exe' /INPUTCHARSET UTF8 /DTRAINER_VERSION=1.9.1 scripts/trainer_installer.nsi
& 'D:\POLYMARKET\.venv\Scripts\python.exe' scripts/trainer_release.py verify "$env:LOCALAPPDATA\Programs\POLY5Trainer"
```

构建脚本从 trainstation.__version__ 读取版本，校验运行时 libssl/libcrypto/libffi 哈希，并生成 build-info.json、manifest.json 及卸载文件清单。安装器版本需与源码/产物版本一致。不要只复制 EXE，必须使用完整发行目录或安装器。

## 记录约定

每次改动在 collaboration/CHANGELOG.jsonl 追加一行独立 JSON，至少包含时间、执行者、版本、变更文件、修复问题、测试、部署状态、备份位置、遗留事项。测试目录与正式数据目录分开；禁止把夹具计入生产结果。不要在交接记录中写密钥、secret、passphrase 或授权材料。

在 collaboration/KNOWN_ISSUES.md 区分“已复现且已修复”“已确认限制”“尚待验证”。本轮原始错误复现、回归测试、安装校验、保留检查位于 outputs/validation/trainer-1.9.1；最终验收 JSON 将记录实际运行结果，不用静态文档代替验收。
