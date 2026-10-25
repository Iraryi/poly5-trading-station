"""Independent trainer entry point. No order submission is reachable."""
import argparse
import os
from pathlib import Path
import sys

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',default='D:/POLYMARKET/trainer')
    p.add_argument('--worker',action='store_true');p.add_argument('--fit');p.add_argument('--offline',action='store_true')
    p.add_argument('--autostart',action='store_true');p.add_argument('--smoke-test',action='store_true')
    args=p.parse_args()
    root=Path(args.root).resolve();root.mkdir(parents=True,exist_ok=True)
    temp=root/'cache/tmp';temp.mkdir(parents=True,exist_ok=True)
    os.environ['TEMP']=os.environ['TMP']=str(temp)
    os.environ['OMP_NUM_THREADS']='2';os.environ['OPENBLAS_NUM_THREADS']='2';os.environ['MKL_NUM_THREADS']='2'
    seed=Path(__file__).resolve().parent/'trainstation/seed_data'
    if args.fit:
        from trainstation.modeling import fit_job
        fit_job(args.fit)
    elif args.worker:
        from trainstation.runner import run
        run(root,seed,args.offline)
    else:
        from trainstation.gui import run_gui
        run_gui(args)

if __name__=='__main__':main()
