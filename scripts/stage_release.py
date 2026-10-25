"""Stage reviewable documentation and notices next to the native application."""
from pathlib import Path
import shutil,sys,os
SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE))
from poly5 import __version__
BASE=SOURCE.parent;APP=Path(os.environ.get('POLY5_DIST_ROOT',str(BASE/'outputs'/__version__)))/'Poly5';RESEARCH=BASE/'research/btc5m-v1'
shutil.copytree(SOURCE/'packaging/licenses',APP/'licenses',dirs_exist_ok=True)
shutil.copy2(SOURCE/'assets/poly5.ico',APP/'Poly5.ico')
shutil.copy2(SOURCE/'DEVELOPMENT_REPORT_3.3.md',APP/'开发报告.md')
(APP/'使用说明.txt').write_text(f'''POLY5 {__version__}

直接启动 Poly5.exe 默认静默进入托盘。托盘图标打开主窗口，完全退出用托盘菜单。
右侧三横线展开/收起副驾驶。面板分隔线可拖动，Ctrl+Shift+A 切换副驾驶。
不要单独移动 Poly5.exe，旁边的 _internal 文件夹必须保留。

安装版默认位置：%LOCALAPPDATA%\\Programs\\POLY5
行情、设置、AI 建议：D:\\POLYMARKET\\data
项目、离线研究与打包产物：D:\\POLYMARKET

模型与接入：读取训练台模型，选择一行，固定版本并开始影子预测。
固定模型不会自动替换。实盘须单独选择固定方向模型池并在本机启用。
账户中心：邮箱账户授权，通过官方 Magic 用资金邮箱导出密钥，仅输入本机密码框。
所有者密钥控制钱包资金，按账户使用 Windows DPAPI 加密；不要发到聊天、日志或截图。
仅 CLOB 缓存不等于可用资金；实盘另核对链上现金、当前交易所额度与全部挂单占用。
模型与接入 → 配置并核验实盘：可调单笔基础比例（初始20%）与最小合约金额（初始5美元）。
预览准确本金和模型版本后，本人确认资格并输入“启用真钱交易”。重启后须重新启用。
每小时只上调本金基线；亏损不下调。回撤按预设曲线减仓，达到50%锁定新增交易。
充值不会增加策略本金；缩减预算低于最低合约金额时等待，不自动加码。
程序仓位达到净盈利目标或发生突发反向风险时尝试限价卖出；没有30秒强制退出。
到期仓位尚无自动赎回：暂停新增风险，须在官方账户确认结果并赎回；需要人工核对台账。
订单 UNKNOWN 时锁定并对账，绝不重发；完全停机会停止管理已有仓位，请本人处理。
MONITOR/PAPER 不授予实盘权限；实盘状态独立明确显示。纸面收益不是真钱收益。
交易台更新不影响独立训练台。没有固定持仓期限；自动模拟每笔 $5 合约，费用另计。
事实日志上限仍为512MB，写入失败停止实盘写入；实盘资金与订单审计另存 data/live。
完整功能边界见开发报告.md。
''',encoding='utf-8-sig')
dest=APP/'研究结果';dest.mkdir(exist_ok=True)
for name in ['ROI研究报告.md','training-results.json','model-metrics.csv','roi-quality.json','checks-prebuild.json']:
    shutil.copy2(RESEARCH/name,dest/name)
shutil.copy2(SOURCE/'research/BTC5M_RESEARCH_README.md',dest/'研究方法.md')
shutil.copy2(BASE/'research/orderbook-roi-v1/validation.json',dest/'盘口模型验证.json')
shutil.copy2(SOURCE/'UPDATES.md',APP/'更新接口说明.md')
for root in sorted((BASE/'research').glob('auto-blind-*')):
    if (root/'盲测报告.md').exists():
        shutil.copy2(root/'盲测报告.md',dest/'自动模拟盲测报告.md')
        shutil.copy2(root/'result.json',dest/'自动模拟盲测结果.json')


for prefix,report in [("distance-paper-*","距离策略回放报告.md"),("last-minute-50-*","最后一分钟50美元检查.md")]:
    roots=sorted((BASE/"research").glob(prefix))
    if roots and (roots[-1]/report).exists():
        shutil.copy2(roots[-1]/report,dest/report)
        shutil.copy2(roots[-1]/"result.json",dest/(report+".json"))
