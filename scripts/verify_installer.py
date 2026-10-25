"""Exercise this build only inside an isolated D:\\POLYMARKET test folder."""
from pathlib import Path
import hashlib,json,os,subprocess,time,winreg
from datetime import datetime,timezone

BASE=Path(__file__).resolve().parents[2]
ROOT=BASE/'work/installer-qa-dev3'
APP=ROOT/'installedApp'
RUNTIME=ROOT/'runtime'
SETUP=BASE/'outputs/Poly5_Setup_3.1.0-dev.3_x64.exe'
KEY=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\POLY5'
ROOT.mkdir(parents=True,exist_ok=True)
assert APP.resolve().is_relative_to(ROOT.resolve()) and not APP.exists(), 'Test folder must be new'
ENV=dict(os.environ,TEMP=str(ROOT),TMP=str(ROOT),PATH=r'C:\Windows\System32;C:\Windows')
info=subprocess.STARTUPINFO();info.dwFlags|=subprocess.STARTF_USESHOWWINDOW;info.wShowWindow=0
checks=[]

def check(name,result):
    checks.append({'check':name,'passed':bool(result)})
    print(name,'PASS' if result else 'FAIL',flush=True)
    if not result:raise AssertionError(name)

def registration():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,KEY,0,winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as key:
            return winreg.QueryValueEx(key,'InstallLocation')[0]
    except FileNotFoundError:return None

def run(args,timeout=120):
    return subprocess.run(list(map(str,args)),cwd=ROOT,env=ENV,startupinfo=info,timeout=timeout).returncode

def wait_until(predicate,seconds=45):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if predicate():return True
        time.sleep(.25)
    return False

report={'at_utc':datetime.now(timezone.utc).isoformat(),'installer':str(SETUP),'test_root':str(ROOT),'checks':checks}
try:
    check('no existing installation registration modified',registration() is None)
    report['install_exit_code']=run([SETUP,'/S','/D='+str(APP)])
    check('silent installation exits successfully',report['install_exit_code']==0)
    manifest=json.loads((APP/'安装清单.json').read_text(encoding='utf-8'))
    report['payload_files']=len(manifest['files'])
    check('every installed payload file matches SHA-256',all(hashlib.sha256((APP/f['path']).read_bytes()).hexdigest()==f['sha256'] for f in manifest['files']))
    check('Windows uninstall registration points at tested folder',registration()==str(APP))
    check('launch shortcut exists',(APP/'启动 POLY5.lnk').is_file())
    sentinel=APP/'custom-notes.txt';sentinel.write_text('User content must survive upgrade and uninstall.',encoding='utf-8')
    nested=APP/'user-data/keep.json';nested.parent.mkdir();nested.write_text('{"preserve": true}',encoding='utf-8')
    check('same-version upgrade succeeds',run([SETUP,'/S','/D='+str(APP)])==0)
    check('upgrade preserves user-added files',sentinel.is_file() and nested.is_file())
    child=subprocess.Popen([str(APP/'Poly5.exe'),'--smoke-test','--data-dir',str(RUNTIME)],cwd=ROOT,env=ENV,startupinfo=info)
    check('installed desktop opens',wait_until(lambda:(RUNTIME/'qa/01-overview.png').exists()))
    check('upgrade refuses to overwrite a running program',run([SETUP,'/S','/D='+str(APP)],30)==2)
    child.wait(timeout=75)
    ui=json.loads((RUNTIME/'qa/ui-smoke.json').read_text(encoding='utf-8'))
    check('installed desktop passes all 30 lifecycle checks',ui['passed']==30 and ui['failed']==0)
    check('installed background worker stops cleanly',json.loads((RUNTIME/'qa/shutdown.json').read_text(encoding='utf-8'))['child_stopped'])
    check('uninstaller starts successfully',run([APP/'Uninstall.exe','/S'])==0)
    check('uninstall removes program and registration',wait_until(lambda:not (APP/'Poly5.exe').exists() and registration() is None))
    check('uninstall removes all managed payload files',all(not (APP/f['path']).exists() for f in manifest['files']))
    check('uninstall preserves user-added text',sentinel.read_text(encoding='utf-8')=='User content must survive upgrade and uninstall.')
    check('uninstall preserves nested user data',nested.read_text(encoding='utf-8')=='{"preserve": true}')
    report['sha256']=hashlib.sha256(SETUP.read_bytes()).hexdigest()
finally:
    report['passed']=sum(c['passed'] for c in checks);report['failed']=sum(not c['passed'] for c in checks)
    (ROOT/'installer-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
