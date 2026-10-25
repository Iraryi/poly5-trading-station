"""Upgrade QA with isolated registry keys; never touches a user's POLY5 instance."""
from pathlib import Path
import ctypes,hashlib,json,os,shutil,subprocess,time,winreg
from ctypes import wintypes
from datetime import datetime,timezone
BASE=Path(__file__).resolve().parents[2]
ROOT=BASE/'work'/('installer-qa-dev4-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));ROOT.mkdir()
APP=ROOT/'installedApp';RUNTIME=ROOT/'runtime'
SETUP=BASE/'work/installer-build-dev4-qa/Poly5_Setup_QA.exe'
REG=r'Software\POLY5-QA-dev4';KEY=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\POLY5-QA-dev4'
REAL=r'Software\POLY5'
ENV=dict(os.environ,TEMP=str(ROOT),TMP=str(ROOT),PATH=r'C:\Windows\System32;C:\Windows')
info=subprocess.STARTUPINFO();info.dwFlags|=subprocess.STARTF_USESHOWWINDOW;info.wShowWindow=0
checks=[]
def check(name,ok):
    checks.append({'check':name,'passed':bool(ok)});print(name,'PASS' if ok else 'FAIL',flush=True)
    if not ok:raise AssertionError(name)
def reg_value(key,value):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key,0,winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as h:return winreg.QueryValueEx(h,value)[0]
    except FileNotFoundError:return None
def run(args,timeout=120):return subprocess.run([str(x) for x in args],cwd=ROOT,env=ENV,startupinfo=info,timeout=timeout).returncode
def wait(predicate,seconds=35):
    stop=time.monotonic()+seconds
    while time.monotonic()<stop:
        if predicate():return True
        time.sleep(.2)
    return False
def installation(version,build=''):
    (APP/'installation.ini').write_text('[POLY5]\nProductId=POLY5-748D8316-6D5E-42CF-90DA-4F84E77A6F20\nInstallDir='+str(APP)+'\nVersion='+version+'\nBuildVersion='+build+'\n',encoding='utf-16')
def window(pid):
    handles=[];cb=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    def visit(hwnd,param):
        found=wintypes.DWORD();ctypes.windll.user32.GetWindowThreadProcessId(hwnd,ctypes.byref(found))
        if found.value==pid:handles.append(hwnd)
        return True
    ctypes.windll.user32.EnumWindows(cb(visit),0)
    return next((h for h in handles if ctypes.windll.user32.GetDlgItem(h,1)),None)
def controls(hwnd):
    result=[];cb=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    def visit(child,param):
        buffer=ctypes.create_unicode_buffer(2048);ctypes.windll.user32.GetWindowTextW(child,buffer,2048)
        result.append((child,buffer.value));return True
    ctypes.windll.user32.EnumChildWindows(hwnd,cb(visit),0);return result
def click(handle):ctypes.windll.user32.SendMessageW(handle,0xF5,0,0)

report={'root':str(ROOT),'registry_isolated':True,'checks':checks,'at':datetime.now(timezone.utc).isoformat()}
real_before=reg_value(REAL,'InstallDir')
try:
    check('QA registration absent',reg_value(REG,'InstallDir') is None)
    shutil.copytree(BASE/'outputs/Poly5',APP)
    installation('3.1.0-dev.3')
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,REG,0,winreg.KEY_WRITE|winreg.KEY_WOW64_64KEY) as h:winreg.SetValueEx(h,'InstallDir',0,winreg.REG_SZ,str(APP))
    sentinel=APP/'user-notes.txt';sentinel.write_text('keep-user-data',encoding='utf-8')
    nested=APP/'private-data/settings.json';nested.parent.mkdir();nested.write_text('{"keep":42}',encoding='utf-8')
    wizard=subprocess.Popen([str(SETUP)],env=ENV,startupinfo=info,cwd=ROOT)
    check('upgrade wizard opens',wait(lambda:window(wizard.pid)))
    hwnd=window(wizard.pid);click(ctypes.windll.user32.GetDlgItem(hwnd,1))
    check('old-version page offers explicit update',wait(lambda:any('更新现有安装' in text for h,text in controls(hwnd))))
    texts=[text for h,text in controls(hwnd)]
    (ROOT/'maintenance-controls.json').write_text(json.dumps(texts,ensure_ascii=False,indent=2),encoding='utf-8')
    check('old and new versions shown',any('3.1.0-dev.3' in t for t in texts) and any('3.1.0-dev.6' in t for t in texts))
    from PySide6.QtWidgets import QApplication
    qt=QApplication.instance() or QApplication([])
    ctypes.windll.user32.ShowWindow(hwnd,5);time.sleep(.3)
    qt.primaryScreen().grabWindow(hwnd).save(str(ROOT/'update-page.png'))
    keep=next(h for h,t in controls(hwnd) if '保持当前版本，退出安装' in t);click(keep);click(ctypes.windll.user32.GetDlgItem(hwnd,1));wizard.wait(timeout=15)
    check('keep-current option exits without overwriting', '3.1.0-dev.3' in (APP/'installation.ini').read_text(encoding='utf-16'))
    check('dev.3 to dev.4 upgrade succeeds',run([SETUP,'/S'])==0)
    manifest=json.loads((APP/'安装清单.json').read_text(encoding='utf-8'))
    check('installed files match hashes',all(hashlib.sha256((APP/f['path']).read_bytes()).hexdigest()==f['sha256'] for f in manifest['files']))
    check('registration retains existing directory',reg_value(REG,'InstallDir')==str(APP))
    check('upgrade preserves user files',sentinel.read_text()=='keep-user-data' and nested.read_text()=='{"keep":42}')
    check('same version repair works',run([SETUP,'/S'])==0)
    installation('3.2.0','3.2.0.65535')
    before=hashlib.sha256((APP/'Poly5.exe').read_bytes()).hexdigest()
    check('downgrade refused',run([SETUP,'/S'])==3)
    check('downgrade leaves executable untouched',hashlib.sha256((APP/'Poly5.exe').read_bytes()).hexdigest()==before)
    installation('3.1.0-dev.6','3.1.0.6')
    child=subprocess.Popen([str(APP/'Poly5.exe'),'--smoke-test','--data-dir',str(RUNTIME)],cwd=ROOT,env=ENV,startupinfo=info)
    check('installed desktop opens',wait(lambda:(RUNTIME/'qa/01-overview.png').exists()))
    check('running process protected',run([SETUP,'/S'],30)==2 and child.poll() is None)
    child.wait(timeout=75)
    ui=json.loads((RUNTIME/'qa/ui-smoke.json').read_text(encoding='utf-8'))
    check('native desktop lifecycle 30 checks',ui['passed']==30 and ui['failed']==0)
    check('test worker exits cleanly',json.loads((RUNTIME/'qa/shutdown.json').read_text(encoding='utf-8'))['child_stopped'])
    check('uninstaller starts',run([APP/'Uninstall.exe','/S'])==0)
    check('managed files and QA registration removed',wait(lambda:not (APP/'Poly5.exe').exists() and reg_value(REG,'InstallDir') is None))
    check('all managed payload removed',all(not (APP/f['path']).exists() for f in manifest['files']))
    check('user-added nested files retained',sentinel.is_file() and nested.is_file())
    check('real installation registration unchanged',reg_value(REAL,'InstallDir')==real_before)
finally:
    report['passed']=sum(c['passed'] for c in checks);report['failed']=sum(not c['passed'] for c in checks)
    (ROOT/'installer-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('REPORT',ROOT,flush=True)

