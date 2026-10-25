本目录保留给 Codex 录制**匿名化、只读**的真实数据；不要在这个包里假装有 live 原始行情。建议按 market slug 分文件保存原始事件、processed snapshots、gap/重连日志与数据来源时间戳。若使用合成样例，必须在文件头显式 `synthetic: true`，不得混入收益测试。
