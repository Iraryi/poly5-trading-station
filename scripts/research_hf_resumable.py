"""Resumable, checksum-verified download of a pinned Hugging Face dataset file (Range requests; survives dropped
connections). Usage: research_hf_resumable.py <dataset> <revision> <path-in-repo> <sha256> <target>"""
import hashlib,os,sys,time
from pathlib import Path
import httpx

def fetch(dataset,revision,path,sha256,target):
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True);partial=Path(str(target)+'.partial')
    url=f'https://huggingface.co/datasets/{dataset}/resolve/{revision}/{path}'
    for attempt in range(200):
        if target.exists():break
        have=partial.stat().st_size if partial.exists() else 0
        try:
            with httpx.Client(timeout=120,follow_redirects=True) as c,c.stream('GET',url,headers={'Range':f'bytes={have}-'} if have else {}) as r:
                if r.status_code==416:partial.replace(target);break
                r.raise_for_status()
                if have and r.status_code!=206:have=0
                with partial.open('ab' if have else 'wb') as f:
                    for block in r.iter_bytes(4<<20):f.write(block)
            partial.replace(target)
        except Exception as exc:
            print('retry',attempt,type(exc).__name__,partial.stat().st_size if partial.exists() else 0,flush=True);time.sleep(min(60,2+attempt))
    h=hashlib.sha256()
    with target.open('rb') as f:
        for block in iter(lambda:f.read(8<<20),b''):h.update(block)
    if h.hexdigest()!=sha256:target.rename(str(target)+'.badsha');raise SystemExit('checksum mismatch: '+path)
    print('ok',path,target.stat().st_size,flush=True)

if __name__=='__main__':fetch(*sys.argv[1:6])
