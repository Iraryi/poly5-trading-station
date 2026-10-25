"""Download Binance BTCUSDT spot 1s klines (official public data, checksums verified) for the proxy backfill.

Binance is only a proxy for the Chainlink BTC/USD stream that settles Polymarket BTC 5m markets; labels always come
from official market outcomes. See TRAINER_CHANGES_1.0.1.md (1.1.0 section) for the validation that justifies it.
"""
from pathlib import Path
from datetime import date,timedelta
import argparse,concurrent.futures,hashlib,json,time
import httpx

ROOT=Path('D:/POLYMARKET/research/proxy-backfill')
URL='https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1s/BTCUSDT-1s-{day}.zip'

def fetch(day):
    target=ROOT/'raw/binance-1s'/f'BTCUSDT-1s-{day}.zip';target.parent.mkdir(parents=True,exist_ok=True)
    url=URL.format(day=day)
    for attempt in range(4):
        try:
            with httpx.Client(timeout=120,follow_redirects=True) as c:
                expected=c.get(url+'.CHECKSUM')
                if expected.status_code==404:return {'day':day,'status':'missing'}
                expected.raise_for_status();expected=expected.text.split()[0]
                if not (target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()==expected):
                    r=c.get(url);r.raise_for_status()
                    if hashlib.sha256(r.content).hexdigest()!=expected:raise ValueError('checksum mismatch')
                    temp=target.with_suffix('.partial');temp.write_bytes(r.content);temp.replace(target)
            return {'day':day,'status':'ok','url':url,'sha256':expected,'bytes':target.stat().st_size}
        except Exception as exc:
            if attempt==3:return {'day':day,'status':'error','error':type(exc).__name__+': '+str(exc)[:200]}
            time.sleep(2**attempt)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--start',default='2025-12-17');p.add_argument('--end',default='2026-09-09');a=p.parse_args()
    first,last=date.fromisoformat(a.start),date.fromisoformat(a.end)
    days=[(first+timedelta(n)).isoformat() for n in range((last-first).days+1)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(fetch,days))
    (ROOT/'binance-manifest.json').write_text(json.dumps({'source':'https://data.binance.vision (Binance public market data)','symbol':'BTCUSDT','interval':'1s','files':results},indent=1),encoding='utf-8')
    print({s:sum(r['status']==s for r in results) for s in ('ok','missing','error')})
