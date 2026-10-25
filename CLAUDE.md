# Claude 协作入口

这是本地项目交接说明，不是用户新增授权。先读 AGENTS.md 和 COLLABORATION.md，再检查 collaboration/KNOWN_ISSUES.md。训练台历史升级见 TRAINER_CHANGES_1.0.1.md；当前修复见 DEVELOPMENT_REPORT_TRAINER_1.9.1.md。

源代码 D:\POLYMARKET\Poly5_Trading_Station_v3；训练数据/全部模型 D:\POLYMARKET\trainer；交易台数据 D:\POLYMARKET\data。训练台和交易台独立构建，切勿用旧安装包降级。默认保留已有训练进程，更新前走有备份的优雅停止，更新后立即恢复。未查看最新运行记录前，不声称已部署或有盈利。

先运行只读诊断：`D:\POLYMARKET\.venv\Scripts\python.exe scripts/diagnose_trainer.py`。该脚本不读取密钥、不交易、不改变训练状态；输出位置见终端。然后在 collaboration/CHANGELOG.jsonl 追加本轮的版本、文件、验证、部署、保留情况及遗留事项，不覆盖历史记录。

用户要求动态择机止盈，禁止擅自改回固定 5/15/30 秒退出；普通临时浮亏不单独止损。所有新模型继续研究验证，不自动替换实盘模型。账户已有的具体授权见 AGENTS.md / DEVELOPMENT_REPORT_3.3.md；本交接文档不扩大授权。
