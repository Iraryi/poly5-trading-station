"""Build a manifest-based Windows installer without touching user data."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,argparse

SOURCE=Path(__file__).resolve().parents[1]
BASE=SOURCE.parent
sys.path.insert(0,str(SOURCE))
from poly5 import __version__
from poly5.updates import PRODUCT_ID
parser=argparse.ArgumentParser();parser.add_argument('--qa',action='store_true');args_cli=parser.parse_args()
APP=BASE/'outputs'/__version__/'Poly5'
WORK=BASE/'work'/('installer-build-dev4-qa' if args_cli.qa else 'installer-build-dev4')
COMPILER=BASE/'work/installer-tools/nsis-3.13/makensis.exe'
OUTPUT=(WORK/'Poly5_Setup_QA.exe') if args_cli.qa else BASE/'outputs'/f'Poly5_Setup_{__version__}_x64.exe'
WORK.mkdir(parents=True,exist_ok=True)
os.environ['TEMP']=os.environ['TMP']=str(WORK)
assert APP.is_dir() and COMPILER.is_file()

def nsis(value):
    return str(value).replace('$','$$').replace('"','$\\"')

guide=WORK/'installed-guide.txt'
guide.write_text((APP/'使用说明.txt').read_text(encoding='utf-8-sig'),encoding='utf-8-sig')

entries=[]
for path in sorted(APP.rglob('*')):
    if not path.is_file():continue
    rel=path.relative_to(APP)
    origin=guide if str(rel)=='使用说明.txt' else path
    entries.append((rel,origin))
manifest={'product':'POLY5','version':__version__,'files':[{'path':p.as_posix(),'bytes':s.stat().st_size,'sha256':hashlib.sha256(s.read_bytes()).hexdigest()} for p,s in entries]}
manifest_path=WORK/'installed-manifest.json'
manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
entries.append((Path('安装清单.json'),manifest_path))
install=[];uninstall=[];directories=set()
for rel,origin in entries:
    sub='' if rel.parent==Path('.') else '\\'+str(rel.parent)
    install.extend([f'SetOutPath "$INSTDIR{nsis(sub)}"',f'File "/oname={nsis(rel.name)}" "{nsis(origin)}"'])
    uninstall.append(f'Delete "$INSTDIR\\{nsis(rel)}"')
    directories.update(p for p in rel.parents if p!=Path('.'))
for directory in sorted(directories,key=lambda p:(-len(p.parts),str(p))):
    uninstall.append(f'RMDir "$INSTDIR\\{nsis(directory)}"')
(WORK/'install-files.nsh').write_text('\n'.join(install),encoding='utf-8-sig')
(WORK/'uninstall-files.nsh').write_text('\n'.join(uninstall),encoding='utf-8-sig')
args=[str(COMPILER),'/INPUTCHARSET','UTF8',f'/DAPP_SOURCE={APP}',f'/DOUTPUT_FILE={OUTPUT}',f'/DINSTALL_FILES={WORK / "install-files.nsh"}',f'/DUNINSTALL_FILES={WORK / "uninstall-files.nsh"}',f'/DPAYLOAD_KB={sum(s.stat().st_size for _,s in entries)//1024}',str(SOURCE/'scripts/installer.nsi')]
if args_cli.qa:args[-1:-1]=['/DPRODUCT_REG_KEY=Software\\POLY5-QA-dev4','/DUNINSTALL_KEY=Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\POLY5-QA-dev4']
with (WORK/'compiler.log').open('w',encoding='utf-8') as log:
    subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,check=True,cwd=WORK)
print(OUTPUT)
print('bytes',OUTPUT.stat().st_size,'sha256',hashlib.sha256(OUTPUT.read_bytes()).hexdigest())
if not args_cli.qa:
    update={'schema_version':1,'product_id':PRODUCT_ID,'channel':'development','platform':'windows-x64','version':__version__,
            'compatibility':{'settings_schema_min':1,'settings_schema_max':1,'readable_event_schemas':['3.1']},
            'package':{'file':OUTPUT.name,'bytes':OUTPUT.stat().st_size,'sha256':hashlib.sha256(OUTPUT.read_bytes()).hexdigest()},
            'notes':'邮箱钱包本机授权、独立人工启用实盘、每小时高水位本金与回撤减仓；保留账户、模型及审计。','restart_required':True}
    (OUTPUT.parent/'update-manifest.json').write_text(json.dumps(update,ensure_ascii=False,indent=2),encoding='utf-8')
    OUTPUT.with_name(OUTPUT.name+'.sha256').write_text(update['package']['sha256']+'  '+OUTPUT.name+'\n',encoding='utf-8')


