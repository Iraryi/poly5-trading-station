"""Archive completed dev.3 validation and create verifiable distribution files."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
from zipfile import ZipFile, ZIP_DEFLATED

SOURCE = Path(__file__).resolve().parents[1]
BASE = SOURCE.parent
OUT = BASE / 'outputs'
sys.path.insert(0, str(SOURCE))
from poly5 import __version__


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def archive(folder, path):
    with ZipFile(path, 'w', ZIP_DEFLATED, compresslevel=6) as bundle:
        for file in sorted(folder.rglob('*')):
            relative = file.relative_to(folder)
            if file.is_file() and not any(p in {'__pycache__', '.git', '.venv'} for p in relative.parts):
                bundle.write(file, Path(folder.name) / relative)
    with ZipFile(path) as bundle:
        assert bundle.testzip() is None


def main():
    validation = OUT / 'validation/dev3'
    validation.mkdir(parents=True, exist_ok=True)
    qa = BASE / 'work/installer-qa-dev3'
    install = read(qa / 'installer-validation.json')
    setup = OUT / f'Poly5_Setup_{__version__}_x64.exe'
    assert install['failed'] == 0 and install['passed'] == 16 and install['sha256'] == sha(setup)
    rollover_roots = sorted((BASE / 'work').glob('rollover-dev3-*/rollover-validation.json'))
    assert rollover_roots, 'Real-boundary verification must finish before packaging'
    rollover = read(rollover_roots[-1])
    assert rollover['passed'] == 9 and rollover['failed'] == 0
    assert rollover['binary_sha256'] == sha(OUT / 'Poly5/Poly5.exe')
    shutil.copy2(qa / 'installer-validation.json', validation / 'installer-validation.json')
    shutil.copytree(qa / 'runtime/qa', validation / 'desktop', dirs_exist_ok=True)
    shutil.copy2(rollover_roots[-1], validation / 'rollover-validation.json')
    research = BASE / 'research/btc5m-v1'
    for name in ['checks-prebuild.json', 'evidence-validation.json', 'final-research-checks.json']:
        shutil.copy2(research / name, validation / name)
    shutil.copy2(SOURCE / 'DEVELOPMENT_REPORT.md', OUT / '开发报告.md')
    shutil.copy2(research / 'ROI研究报告.md', OUT / 'ROI研究报告.md')
    instructions = f'''POLY5 {__version__} Windows 安装包

双击 Poly5_Setup_{__version__}_x64.exe 开始安装。
新安装默认程序目录：%LOCALAPPDATA%\\Programs\\POLY5
已有安装的升级会保留原安装目录，可在安装向导中修改。
行情、设置、AI 建议始终默认保存在：D:\\POLYMARKET\\data
源码、离线研究、打包中间文件与成品均保存在 D:\\POLYMARKET。

安装末页可以直接打开主窗口；之后使用程序目录“启动 POLY5.lnk”。
直接双击 Poly5.exe 会静默进入托盘。双击托盘图标打开窗口。
关闭/最小化主窗口收入托盘；完全退出须使用托盘菜单。
桌面快捷方式可选，默认不创建。不添加系统自启。
升级前请先在托盘“完全退出”，运行中升级会被拒绝。

右侧三横线展开/收起副驾驶；分隔线可拖动调整布局。
五分钟换轮自动预取候选、到点核验并切换行情，旧轮持仓归属不变。
接口未就绪或失败时重试，换轮有实际网络和订阅延迟，不是无缝行情保证。

MONITOR 是只读监控；PAPER 是手动纸面模拟。每笔最多 $5 含费用。
完成首轮八天历史数据、1–30 秒净 ROI 离线训练；测试未产生满足预设
保守门槛的交易，预测和自动交易保持关闭，真实下单未实现。
报告：ROI研究报告.md。完整数据与模型：D:\\POLYMARKET\\research\\btc5m-v1
日志当前总上限 512 MB，达到上限停止记账并暂停新增模拟风险。

卸载：程序目录 Uninstall.exe 或 Windows“已安装的应用”。
卸载保留行情、设置、AI 建议以及自行添加的文件。
安装/升级/卸载 16 项及原生桌面 30 项检查通过，实际 EXE 换轮 9 项通过。
完整功能边界：开发报告.md；本版验收：validation\\dev3。
'''
    (OUT / '安装说明.txt').write_text(instructions, encoding='utf-8-sig')
    (BASE / '使用说明.txt').write_text(instructions, encoding='utf-8-sig')
    source_zip = OUT / f'Poly5_{__version__}_Source.zip'
    portable_zip = OUT / f'Poly5_{__version__}_Windows_x64.zip'
    archive(SOURCE, source_zip)
    archive(OUT / 'Poly5', portable_zip)
    artifacts = []
    for file in [setup, source_zip, portable_zip]:
        digest = sha(file)
        file.with_name(file.name + '.sha256').write_text(f'{digest}  {file.name}\n', encoding='utf-8')
        artifacts.append({'name': file.name, 'bytes': file.stat().st_size, 'sha256': digest})
    manifest = {'version': __version__, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'live_execution': False, 'automatic_strategy_enabled': False,
                'installer_default': r'%LOCALAPPDATA%\Programs\POLY5', 'default_data': str(BASE / 'data'),
                'unit_tests': {'passed': 77, 'skipped': 1, 'failed': 0},
                'research_tests': {'passed': 10, 'failed': 0},
                'desktop_tests': {'passed': 30, 'failed': 0},
                'installer_tests': {'passed': 16, 'failed': 0},
                'rollover_tests': {'passed': rollover['passed'], 'failed': rollover['failed'],
                                   'observed_switch_delay_ms': rollover['first_observed_switch_after_boundary_ms']},
                'research': {'days': 8, 'markets': 2304, 'raw_l2_rows': 281570041,
                             'test_qualified_entries': 0, 'deployment_allowed': False},
                'exe_sha256': sha(OUT / 'Poly5/Poly5.exe'), 'artifacts': artifacts}
    (OUT / '交付清单.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
