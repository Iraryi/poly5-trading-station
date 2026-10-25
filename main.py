from __future__ import annotations
import argparse
import os
from pathlib import Path
import sys

def main():
    parser=argparse.ArgumentParser(description='POLY5 Windows trading station (live defaults disarmed)')
    parser.add_argument('--worker',action='store_true')
    parser.add_argument('--ipc',action='store_true')
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--show',action='store_true')
    parser.add_argument('--data-dir',default='D:/POLYMARKET/data')
    parser.add_argument('--duration',type=int)
    parser.add_argument('--smoke-test',action='store_true')
    parser.add_argument('--check-update',metavar='MANIFEST')
    parser.add_argument('--configure-reference',action='store_true')
    parser.add_argument('--configure-account',action='store_true')
    parser.add_argument('--configure-reference-auto',action='store_true')
    parser.add_argument('--model-probe',metavar='REQUEST',help='Offline artifact compatibility check; no engine or exchange calls')
    parser.add_argument('--vote-probe',metavar='REQUEST',help='Offline late-vote check on a worker thread; no engine or exchange calls')
    args=parser.parse_args()
    # PyInstaller windowed executables set stdio to None. QProcess supplies
    # explicit pipe handles for the worker, which we wrap without a console.
    if args.ipc and os.name=='nt' and (sys.stdin is None or sys.stdout is None):
        import ctypes, msvcrt
        from ctypes import wintypes
        get_handle=ctypes.windll.kernel32.GetStdHandle
        get_handle.argtypes=[wintypes.DWORD];get_handle.restype=wintypes.HANDLE
        if sys.stdin is None:
            sys.stdin=os.fdopen(msvcrt.open_osfhandle(get_handle(-10 & 0xffffffff),os.O_RDONLY),'r',encoding='utf-8')
        if sys.stdout is None:
            sys.stdout=os.fdopen(msvcrt.open_osfhandle(get_handle(-11 & 0xffffffff),0),'w',encoding='utf-8',buffering=1)
        if sys.stderr is None:
            sys.stderr=os.fdopen(msvcrt.open_osfhandle(get_handle(-12 & 0xffffffff),0),'w',encoding='utf-8',buffering=1)
    root=Path(args.data_dir).absolute(); root.mkdir(parents=True,exist_ok=True)
    temp=root/'cache'/'tmp'; temp.mkdir(parents=True,exist_ok=True)
    os.environ['TEMP']=os.environ['TMP']=str(temp)
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stdin,'reconfigure'): sys.stdin.reconfigure(encoding='utf-8')
    if args.configure_account:
        from poly5.account_signer import run_setup
        run_setup(root)
        return
    if args.model_probe:
        from types import SimpleNamespace
        from poly5.model_bridge import ModelBridge,ShadowRuntime,read
        from poly5.common import atomic_json
        request=read(Path(args.model_probe))
        try:
            result=ShadowRuntime(ModelBridge(root)).predict(request['features'],request['now_ms'],SimpleNamespace(**request['market']))
            result['scope']='offline_compatibility_probe'
            result={'passed':True,'result':result}
        except Exception as exc:result={'passed':False,'error':type(exc).__name__+': '+str(exc)[:160]}
        atomic_json(root/'qa'/'model-probe.json',result)
        return
    if args.vote_probe:
        import threading,time
        from types import SimpleNamespace
        from poly5.model_bridge import ModelBridge,VoteRuntime,preload_inference,read
        from poly5.common import atomic_json
        request=read(Path(args.vote_probe));result={'passed':False,'error':'timeout: no result within 30 s on worker thread'}
        began=time.monotonic()
        if request.get('preload',True):preload_inference()
        def run():
            nonlocal result
            try:
                bridge=ModelBridge(root);out=VoteRuntime(bridge).predict(request['release_ids'],request['features'],request['now_ms'],SimpleNamespace(**request['market']))
                result={'passed':True,'result':out}
            except Exception as exc:result={'passed':False,'error':type(exc).__name__+': '+str(exc)[:160]}
        thread=threading.Thread(target=run,daemon=True);thread.start();thread.join(30)
        result['seconds']=round(time.monotonic()-began,2);result['preload']=request.get('preload',True)
        atomic_json(root/'qa'/'vote-probe.json',result)
        os._exit(0)
    if args.configure_reference_auto:
        from poly5.reference_setup import configure_local_identity
        from poly5.common import dumps
        try:result=configure_local_identity(root)
        except Exception as exc:result={'configured':False,'error':str(exc) if isinstance(exc,ValueError) else type(exc).__name__}
        print(dumps(result),flush=True)
        return
    if args.configure_reference:
        from poly5.reference_setup import run_setup
        run_setup(root)
        return
    if args.check_update:
        from poly5.updates import LocalUpdateSource,check_update
        from poly5 import __version__
        from poly5.common import dumps
        try: result=check_update(LocalUpdateSource(Path(args.check_update)),__version__)
        except Exception as e: result={'status':'error','error':str(e)}
        print(dumps(result),flush=True)
        return
    if args.worker:
        from poly5.worker import worker_main
        worker_main(root,args.duration,args.offline,args.ipc)
    else:
        from poly5.desktop import desktop_main
        desktop_main(args)

if __name__=='__main__':
    main()
