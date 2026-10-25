"""Download pinned, public research evidence. No credentials or trading APIs."""
from pathlib import Path
import concurrent.futures, hashlib, json, time
from datetime import datetime, timezone
import httpx

ROOT=Path('D:/POLYMARKET/research/btc5m-v1')
REV='acb8cecf4234677823ddd8ee1275de1db48aa995'
DATASET='mileswangs/polymarket-btc-updown-5m-orderbook-tick-data'

def download(rel):
    target=ROOT/'raw'/rel
    target.parent.mkdir(parents=True,exist_ok=True)
    url=f'https://huggingface.co/datasets/{DATASET}/resolve/{REV}/{rel}'
    if not target.exists():
        for attempt in range(3):
            try:
                with httpx.Client(timeout=60,follow_redirects=True) as c:
                    if rel.startswith('l2/'):
                        probe=c.get(url,headers={'Range':'bytes=0-0'})
                        probe.raise_for_status()
                        assert probe.status_code==206
                        total=int(probe.headers['content-range'].split('/')[-1])
                        chunk=4*1024*1024
                        def part(start):
                            end=min(total-1,start+chunk-1)
                            for retry in range(2):
                                try:
                                    # Isolate proxy connections between concurrent ranges.
                                    with httpx.Client(timeout=30,follow_redirects=True) as segment_client:
                                        response=segment_client.get(url,headers={'Range':f'bytes={start}-{end}'})
                                    response.raise_for_status()
                                    assert response.status_code==206 and response.headers['content-range']==f'bytes {start}-{end}/{total}'
                                    assert len(response.content)==end-start+1
                                    return start,response.content
                                except Exception as error:
                                    print('range failure',rel,start,type(error).__name__,str(error),flush=True)
                                    if retry==1:raise
                            raise RuntimeError('unreachable')
                        partial=target.with_suffix('.partial')
                        resume=(partial.stat().st_size//chunk)*chunk if partial.exists() else 0
                        with partial.open('r+b' if partial.exists() else 'wb') as f, concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                            for start,content in pool.map(part,range(resume,total,chunk)):
                                f.seek(start);f.write(content);f.flush()
                                if start%(8*chunk)==0:print('downloaded',rel,start+len(content),total,flush=True)
                            f.truncate(total)
                    else:
                        with c.stream('GET',url) as r:
                            r.raise_for_status()
                            with target.with_suffix('.partial').open('wb') as f:
                                for block in r.iter_bytes(1024*1024):f.write(block)
                target.with_suffix('.partial').replace(target)
                break
            except Exception:
                if attempt==2:raise
                time.sleep(2**attempt)
    h=hashlib.sha256()
    with target.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    result={'path':rel,'url':url,'bytes':target.stat().st_size,'sha256':h.hexdigest()}
    print(json.dumps(result),flush=True)
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--l2',action='store_true');a=p.parse_args()
    files=['README.md','markets.parquet']
    for day in range(14,22):
        for kind in ['chainlink_streams','chainlink_streams_twap60s']:
            files.append(f'{kind}/date=2026-08-{day:02}/BTCUSD.parquet')
        if a.l2:files.append(f'l2/date=2026-08-{day:02}/part-0.parquet')
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        rows=list(pool.map(download,files))
    manifest={'retrieved_utc':datetime.now(timezone.utc).isoformat(),'dataset_revision':REV,
              'license':'CC-BY-4.0','attribution':'PMData https://pmdata.dev', 'files':rows}
    (ROOT/('download-manifest-l2.json' if a.l2 else 'download-manifest.json')).write_text(json.dumps(manifest,indent=2),encoding='utf-8')
