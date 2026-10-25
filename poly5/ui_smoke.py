"""Real Qt/Windows lifecycle checks. No fake balances or market injections."""
from pathlib import Path
import hashlib,sys
import time
from PySide6.QtCore import QTimer, QProcess
from PySide6.QtWidgets import QSystemTrayIcon
from .common import atomic_json, utc_now

def install_smoke(window,app):
    out=window.root/'qa';out.mkdir(parents=True,exist_ok=True)
    checks=[];start=time.monotonic();before={}
    def record(name,ok,detail=''):
        checks.append({'check':name,'passed':bool(ok),'detail':detail,'elapsed_seconds':round(time.monotonic()-start,2)})
    def safe(fn):
        def wrapped():
            try:fn()
            except Exception as e:record(fn.__name__,False,type(e).__name__+': '+str(e))
        return wrapped
    def start_check():
        record('Qt tray available',QSystemTrayIcon.isSystemTrayAvailable())
        record('default tray start',not window.isVisible())
        record('real child process running',window.process.state()==QProcess.ProcessState.Running)
        window.restore()
    def overview():
        record('worker delivers snapshots',bool(window.state))
        record('no wallet balance fabricated',window.state.get('account') is None)
        record('live disabled',window.state.get('live_enabled') is False)
        record('copilot initially collapsed',not window.copilot.isVisible())
        record('compact dock uses icon only',window.edge.text()=='' and not window.edge.icon().isNull())
        window.grab().save(str(out/'01-overview.png'))
        window.toggle_copilot();record('copilot inside main window',window.copilot.isVisible() and window.copilot.window()==window)
        window.splitter.setSizes([800,410]);window.save_copilot_width()
        record('copilot width persisted',300<=window.settings.data['copilot_width']<=620)
        window.ask_ai()
    def advice():
        record('local analysis delivered','本地规则诊断' in window.chat.toPlainText())
        record('dock fills right edge without duplicate rail',window.copilot.isVisible() and not window.rail.isVisible() and window.copilot.window()==window)
        record('splitter has one shared divider',window.splitter.handleWidth()<=7)
        window.save_advice();window.grab().save(str(out/'02-copilot.png'))
    def account():
        window.navigate(2);record('account navigation',window.pages.currentIndex()==2)
        record('unknown cash preserved',window.account_metrics['cash'].value.text()=='—')
        window.grab().save(str(out/'03-account.png'))
        if window.proposal_rel:
            window.send('advice',relpath=window.proposal_rel,text='# 修订建议\n\nAI hypothesis · UI 自动测试。仅诊断，不修改事实。')
        record('advice created',bool(window.proposal_rel))
    def navigate():
        for i in range(8):window.navigate(i);record('navigate '+str(i),window.pages.currentIndex()==i)
        window.navigate(0);window.resize(1100,760)
        if not window.copilot.isHidden():window.toggle_copilot()
        QTimer.singleShot(250,small_window)
    def request_models():
        window.navigate(6);window.send('model_catalog')
    def check_models():
        window.navigate(6)
        record('six trainer task contracts discovered',len(window.bridge_catalog)==6)
        record('readiness does not grant live',window.state.get('readiness',{}).get('ready_for_live') is False)
        window.grab().save(str(out/'05-model-connection.png'))
        if window.bridge_catalog:window.bridge_table.selectRow(0);window.pin_model()
    def small_window():
        window.grab().save(str(out/'04-small-window.png'))
        record('collapsed workspace takes available width',window.pages.width()>=window.width()-190)
        before['at']=window.state.get('at');window.close()
        record('close hides window',not window.isVisible())
    def finish():
        record('worker continued in tray',window.state.get('at')!=before.get('at') and window.process.state()==QProcess.ProcessState.Running)
        record('advice revision snapshot exists',bool(list((window.root/'ai log'/'versions').glob('*'))))
        window.restore();record('tray restore',window.isVisible())
        record('unknown real winrate preserved',window.state.get('account') is None)
        record('pinned model kept in local release',bool(list((window.root/'models/releases').glob('*/manifest.json'))))
        record('model opt-in remains shadow only',window.state.get('live_enabled') is False and window.state.get('readiness',{}).get('ready_for_live') is False)
        atomic_json(out/'ui-smoke.json',{'at':utc_now(),'platform':'Windows native Qt','checks':checks,
                    'binary_sha256':hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest() if getattr(sys,'frozen',False) else None,
                    'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),
                    'market_seen':(window.state.get('market') or {}).get('slug')})
        window.shutdown()
        atomic_json(out/'shutdown.json',{'child_stopped':window.process.state()==QProcess.ProcessState.NotRunning,'at':utc_now()})
    for ms,fn in [(3000,start_check),(9000,overview),(14000,advice),(18000,account),(19500,request_models),(21500,check_models),(22000,navigate),(27000,finish)]:
        QTimer.singleShot(ms,safe(fn))
