# 动态择机止盈训练升级 — 2026-10-23

用户明确要求保留模型和进度，将 ROI 目标改为动态择机止盈并继续训练。本次只升级独立训练台，不改变交易台正在使用的模型、账户授权或实盘风控。

## 实施

- 新增 dynamic 任务、持仓状态特征、四轮 fitted Q iteration 和可复现的逐步策略回放。输出为继续持有的预期净 ROI。止盈、明显反向急跌风险退出及官方到期结算为不同事件；没有持仓秒数上限。
- roi5/roi15 保留原目录、数据库记录、快照和旧进度。默认任务停止优化旧固定目标；四项方向任务继续原进度。新任务继承旧最优模型的算法配置，并冻结旧的 Up ROI 预测器为 Up 持仓的特征；Down 不冒充 Up 预测。新目标不能直接冒用旧目标的评分或权重。
- 公共只读接口逐市场验证手续费、condition/token 身份和明确赢家。历史 recorder 秒时间戳向后保守取整，未来记录增加精确 captured_ms；特征只读取接收时间不晚于决策的参考数据。
- 入场使用事前确定的决策网格及当时的价格保护；下一份可见盘口模拟成交。退出在至少 300ms 后核验真实可见深度与净收益，止盈未达到则持有。Decimal 计算买卖金额及手续费，不把中间价当利润。
- 校验按完整市场隔离，600 秒 embargo；验证段的风险调整策略收益用于选择，审计段仅报告。没有足够交易的候选不能用零收益冒充冠军。旧 ROI 的未来标签均方误差不用于动态模型的自动回滚。
- 动态真实前向纸面策略独立持久化；入场前固定模型，重启恢复，不随冠军改变策略。轨迹记录 continuation、sell ROI、holding、基准距离及买卖原因。异常缺段单列 censored。
- 后台每五分钟增量回填已结束市场；新增足量市场后延续种群训练。每代 8 候选，AI 监督沿用原频率与熔断、档案机制。没有研究模型自动晋升实盘的路径。

## 数据与限制

初次重建：190 个已结束市场中的 140 个具有完整可用持仓路径，共 475 条；初始切分 82/27/28 个市场，隔离市场不参与拟合和评分。1860 个预定侧向入场时点，缺参考 162，未成交/深度不满足 1067，路径缺段 156；最终数量详见 dynamic/backfill-report.json。

这是完整路径上的小样本回放，有剔除不完整路径的选择偏差。16 笔盈利等历史结果不是稳定收益证明。不会把回填的历史模拟冒充实时盲测；真正前向纸面结果来自 dynamic_forward 表。官方结算仅表示模拟 payout，未执行赎回或真实卖单。

升级前备份：D:\POLYMARKET\trainer\archive\before-dynamic-20261023-161027，含 SQLite 一致性备份、配置、进度、各任务 JSON、旧安装 EXE 及哈希清单。既有全部模型文件另保留在原目录；更新前还会记录最终保留情况。

## 可检查的验收文件

- D:\POLYMARKET\work\trainer-dynamic-tests.txt：退出、延迟、费用、数据缺失、重启恢复、旧任务保留等自动测试。
- D:\POLYMARKET\work\dynamic-integration\integration-result.json：真实历史数据的多代种群训练、候选选优、全量拟合、归档与优雅停止。
- D:\POLYMARKET\work\dynamic-packaged-smoke\smoke-result.json：打包 EXE 的界面、训练和停止验收。
- D:\POLYMARKET\trainer\dynamic\backfill-report.json 与 metadata.json：路径数量和逐市场官方证据。
- D:\POLYMARKET\outputs\trainer-dynamic-upgrade-20261023.json：最终安装、进度保留及续训状态。

方法来源：Ernst, Geurts, Wehenkel, 2005, Tree-Based Batch Mode Reinforcement Learning, https://www.jmlr.org/beta/papers/v6/ernst05a.html 。官方费用及字段核对：https://docs.polymarket.com/trading/fees 、https://docs.polymarket.com/market-data/market-details 、https://docs.polymarket.com/changelog/predictions 。
