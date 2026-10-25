"""Reproducible trainer payload manifests and installation verification (no credentials)."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE))
from trainstation import __version__

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def verify(folder):
    folder=Path(folder).resolve()
    try:manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8-sig'))
    except (OSError,ValueError) as exc:return {'passed':False,'error':type(exc).__name__,'folder':str(folder)}
    files=manifest.get('files')
    if isinstance(files,list):files={item['path']:item for item in files}  # 1.0 legacy format
    if not isinstance(files,dict):return {'passed':False,'error':'invalid manifest file map','folder':str(folder)}
    missing=[];changed=[]
    for relative,expected in files.items():
        p=(folder/relative).resolve()
        if not p.is_relative_to(folder):return {'passed':False,'error':'manifest path outside installation','folder':str(folder)}
        if not p.is_file():missing.append(relative)
        elif sha(p)!=expected['sha256']:changed.append(relative)
    return {'passed':not missing and not changed,'version':manifest['version'],'checked_files':len(files),
            'missing':missing,'changed':changed,'folder':str(folder)}

def stage(folder):
    folder=Path(folder).resolve()
    if not (folder/'POLY5Trainer.exe').is_file():raise ValueError('训练台 EXE 不存在')
    source_files=[SOURCE/'trainer_main.py',*sorted((SOURCE/'trainstation').glob('*.py')),
                  SOURCE/'scripts/build_trainer.ps1',SOURCE/'scripts/trainer_installer.nsi',Path(__file__)]
    info={'version':__version__,'source_sha256':{p.relative_to(SOURCE).as_posix():sha(p) for p in source_files},
          'python':sys.version,'exit_objective':'dynamic net profit; no fixed holding deadline'}
    if sys.platform=='win32':
        runtime=Path(sys._base_executable).parent/'DLLs';dlls={}
        for name in ('libssl-3-x64.dll','libcrypto-3-x64.dll','libffi-8.dll'):
            reference=runtime/name;packed=folder/'_internal'/name
            if not reference.is_file() or not packed.is_file() or sha(reference)!=sha(packed):
                raise ValueError('打包 DLL 与 Python 运行时不一致：'+name)
            dlls[name]=sha(packed)
        info['verified_runtime_dlls']=dlls
    (folder/'build-info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    files={p.relative_to(folder).as_posix():{'sha256':sha(p),'bytes':p.stat().st_size}
           for p in sorted(folder.rglob('*')) if p.is_file() and p.name!='manifest.json'}
    (folder/'manifest.json').write_text(json.dumps({'version':__version__,'files':files},ensure_ascii=False,indent=2),encoding='utf-8')
    # NSIS never recursively removes unknown files or training data.
    lines=['; Generated only from the current trainer payload.']
    for p in sorted(folder.rglob('*')):
        if p.is_file():lines.append('Delete "$INSTDIR\\'+str(p.relative_to(folder))+'"')
    for p in sorted((p for p in folder.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
        lines.append('RMDir "$INSTDIR\\'+str(p.relative_to(folder))+'"')
    target=SOURCE.parent/'work/trainer-uninstall.nsh';target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text('\n'.join(lines)+'\n',encoding='utf-8-sig')
    return verify(folder)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['stage','verify']);parser.add_argument('folder');a=parser.parse_args()
    result=(stage if a.action=='stage' else verify)(a.folder)
    print(json.dumps(result,ensure_ascii=False));raise SystemExit(0 if result['passed'] else 1)
