"""Archive the already tested dev.5 binary and latest separate research evidence."""
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import shutil
import subprocess
import sys

from package_release import archive, sha, read

SOURCE = Path(__file__).resolve().parents[1]
BASE = SOURCE.parent
OUT = BASE / 'outputs'
VERSION = '3.1.0-dev.5'
APP = OUT / VERSION / 'Poly5'
QA = BASE / 'work/installer-qa-dev4-20261021T192816Z'
RESEARCH = BASE / 'research/trend-baseline-20261021T193423Z'


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def main():
    sys.path.insert(0, str(SOURCE))
    from poly5 import __version__
    from poly5.updates import check_update, LocalUpdateSource
    assert __version__ == VERSION
    validation = OUT / 'validation/dev5'
    validation.mkdir(parents=True, exist_ok=True)
    qa = read(QA / 'installer-validation.json')
    ui = read(QA / 'runtime/qa/ui-smoke.json')
    assert qa['passed'] == 21 and qa['failed'] == 0
    assert ui['passed'] == 30 and ui['failed'] == 0
    payload = read(BASE / 'work/installer-build-dev4/installed-manifest.json')
    assert payload['version'] == VERSION
    qa_payload = read(BASE / 'work/installer-build-dev4-qa/installed-manifest.json')
    assert qa_payload == payload, 'QA and production must contain the same payload'
    for file in payload['files']:
        path = BASE / 'work/installer-build-dev4/installed-guide.txt' if file['path'] == '使用说明.txt' else APP / file['path']
        assert path.stat().st_size == file['bytes'] and sha(path) == file['sha256']
    update = check_update(LocalUpdateSource(OUT / 'update-manifest.json'), '3.1.0-dev.3')
    assert update['status'] == 'update_available' and update['version'] == VERSION
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    cli = subprocess.run([str(APP / 'Poly5.exe'), '--check-update', str(OUT / 'update-manifest.json'),
                          '--ipc', '--data-dir', str(BASE / 'work/update-cli-dev5')],
                         input='', capture_output=True, encoding='utf-8', timeout=30, startupinfo=info)
    assert cli.returncode == 0, cli.stderr
    packaged_check = json.loads(cli.stdout)
    assert packaged_check['status'] == 'current' and packaged_check['sha256'] == update['sha256']
    write(validation / 'update-package-verification.json', {'from_dev3': update, 'packaged_cli': packaged_check,
        'payload_files_verified': len(payload['files']), 'qa_registry_isolated': True})
    shutil.copy2(QA / 'installer-validation.json', validation / 'installer-validation.json')
    shutil.copy2(QA / 'update-page.png', validation / 'update-page.png')
    shutil.copytree(QA / 'runtime/qa', validation / 'desktop', dirs_exist_ok=True)
    shutil.copy2(BASE / 'work/tests-dev5.log', validation / 'unit-tests.log')
    for name in ['preregistered.json', 'result.json', '盲测报告.md']:
        shutil.copy2(BASE / 'research/auto-blind-20261021T192127Z' / name, validation / ('auto-blind-' + name))
    shutil.copytree(RESEARCH, validation / 'trend-baseline', dirs_exist_ok=True)
    comparison = read(RESEARCH / 'exit-comparison/result.json')
    assert len(comparison['trades']) == 14
    for variant in ['original', 'no_price_stop']:
        realized = sum((D(r[variant]['pnl']) for r in comparison['trades']), D(0))
        assert realized == D(comparison['summary'][variant]['net_pnl'])
    for row in read(RESEARCH / 'result.json')['rounds']:
        trade = row['trade']
        if not trade:
            continue
        signal = trade['signal']
        assert signal['side'] == 'Up' and signal['latest_receive_us'] <= trade['decision_us']
        assert D(signal['distance_from_baseline']) > 0 and D(signal['return_5s']) > 0
        assert D(signal['range_15s']) <= D('.0005')
        for value in trade['opportunities_not_policy_fills']:
            if not value.get('unavailable'):
                assert D(value['pnl']) == D(value['gross']) - D(value['exit_fees']) - D(trade['position']['total'])
    write(validation / 'research-ledger-validation.json', {'causal_entry_checks': 14,
          'paired_exit_reproduced': 14, 'all_sampled_liquidations_reconciled': True,
          'scope': 'exploratory Up-only reference/trend strategy; not shipped runtime'})
    snapshot = read(BASE / 'data/cache/snapshot.json')
    write(validation / 'running-paper-snapshot.json', {key: snapshot[key] for key in
          ['version', 'at', 'mode', 'market', 'ws_status', 'paper', 'auto_paper', 'storage_error']})
    instructions = f'''POLY5 {VERSION} Windows 安装包

安装文件：Poly5_Setup_{VERSION}_x64.exe
首次安装默认：%LOCALAPPDATA%\\Programs\\POLY5
已有安装：识别旧版后选择“更新现有安装”，保留旧安装位置、数据与设置。
同版本可修复；拒绝降级。运行中的目标程序不会被安装器强行结束。
需要替换正在使用的程序时，请自行通过托盘“完全退出”后更新。
行情和设置：D:\\POLYMARKET\\data；源码、研究和成品均在 D:\\POLYMARKET。

当前启动快捷方式：D:\\POLYMARKET\\启动 POLY5.lnk
当前免安装目录：D:\\POLYMARKET\\outputs\\{VERSION}\\Poly5
直接运行 EXE 静默进托盘；快捷方式打开主窗口。关闭或最小化窗口后继续运行。
右侧三横线展开副驾驶。未增加系统自启。不要单独移动 EXE，须保留 _internal。

新设置默认自动 PAPER；现有模式保留。自动策略是盘口 ROI 模型，只模拟 Up。
每轮第 5–270 秒逐秒扫描，持仓周期 1–30 秒，含费预算 $5、总敞口 $5。
当前候选没有验证出持续盈利能力。所有买卖和收益均为纸面模拟，真实下单未实现。
用户新澄清的“基准线＋趋势”已经单独回放，尚未接入运行程序；实时授权参考流尚缺。
两套策略成绩不可混用。基准趋势回放和取消价格止损的配对结果见 validation\\dev5\\trend-baseline。
日志上限 512MB，达到上限暂停新增模拟风险；不支持无限期无维护运行。

更新接口：设置页选择本地 update-manifest.json，校验版本、兼容性、大小和 SHA-256。
清单和安装 EXE 须在同一目录。没有远程自动更新服务或发行者数字签名。
卸载保留数据目录和用户添加的文件。

验证：100 项单测（99 通过、1 项环境跳过）；原生桌面 30 项；安装更新维护 21 项。
安装器测试使用隔离注册项；实际发布包清单、全部载荷和 EXE 更新检查另行验证。
研究与桌面截图证据：validation\\dev5。
'''
    (OUT / '安装说明.txt').write_text(instructions, encoding='utf-8-sig')
    (BASE / '使用说明.txt').write_text(instructions, encoding='utf-8-sig')
    shutil.copy2(SOURCE / 'DEVELOPMENT_REPORT_DEV4.md', OUT / '开发报告.md')
    shutil.copy2(RESEARCH / '基准趋势回放报告.md', OUT / '基准趋势回放报告.md')
    shutil.copy2(RESEARCH / 'exit-comparison/止损对照报告.md', OUT / '止损对照报告.md')
    source_zip = OUT / f'Poly5_{VERSION}_Source.zip'
    portable_zip = OUT / f'Poly5_{VERSION}_Windows_x64.zip'
    archive(SOURCE, source_zip)
    archive(APP, portable_zip)
    artifacts = []
    for path in [OUT / f'Poly5_Setup_{VERSION}_x64.exe', source_zip, portable_zip]:
        digest = sha(path)
        path.with_name(path.name + '.sha256').write_text(f'{digest}  {path.name}\n', encoding='utf-8')
        artifacts.append({'name': path.name, 'bytes': path.stat().st_size, 'sha256': digest})
    manifest = {'version': VERSION, 'at_utc': datetime.now(timezone.utc).isoformat(),
        'automatic_paper_default': True, 'runtime_strategy': 'auto-paper-v2 / orderbook-roi-v1 / Up-only',
        'live_execution': False, 'trend_baseline_runtime_implemented': False,
        'installer_default': r'%LOCALAPPDATA%\Programs\POLY5', 'data': str(BASE / 'data'),
        'tests': {'unit_passed': 99, 'unit_skipped': 1, 'desktop_passed': 30, 'installer_passed': 21},
        'exit_comparison_exploratory': comparison['summary'], 'artifacts': artifacts}
    write(OUT / '交付清单.json', manifest)
    print(json.dumps({'version': VERSION, 'artifacts': artifacts, 'validation': str(validation)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
