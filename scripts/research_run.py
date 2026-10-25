"""Run the current offline experiment as pinned daily files finish downloading."""
import json,time,subprocess,sys
from pathlib import Path
from research_features import ROOT

if __name__=='__main__':
    source=Path(__file__).parent;log=ROOT/'pipeline.log';deadline=time.monotonic()+3600
    with log.open('w',encoding='utf-8') as out:
        for day in range(14,22):
            date=f'2026-08-{day:02}';path=ROOT/f'raw/l2/date={date}/part-0.parquet'
            while not path.exists():
                if time.monotonic()>deadline:raise TimeoutError(f'Missing {path}')
                time.sleep(2)
            print('reconstruct',date,flush=True)
            subprocess.run([sys.executable,str(source/'research_books.py'),'--day',date],stdout=out,stderr=subprocess.STDOUT,check=True)
            out.flush()
        for script in ['research_features.py','research_roi_dataset.py','test_research.py','research_train_roi.py']:
            print('run',script,flush=True)
            subprocess.run([sys.executable,str(source/script)],stdout=out,stderr=subprocess.STDOUT,check=True)
            out.flush()
    print('offline experiment finished',flush=True)
