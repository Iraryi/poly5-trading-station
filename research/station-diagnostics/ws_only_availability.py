"""Last-minute book availability if recovery relies on the WebSocket's own 'book' snapshots (no REST injection):
replay raw WS messages; 3 consecutive best-price mismatches -> book unusable until the next WS 'book' message for
that asset. Reports the share of last-minute seconds (240-284 s) in which each traded asset's book was usable."""
import gzip,json,glob,os,collections,sys
from decimal import Decimal as D
files=sorted(glob.glob('D:/POLYMARKET/data/log/events/raw/*.jsonl.gz'),key=os.path.getmtime)[-int(sys.argv[1]):]
books={};bad={};mis={};usable=collections.Counter();total=collections.Counter();last_book=collections.Counter()
def best(b):return (max(b['b']) if b['b'] else D(0),min(b['a']) if b['a'] else D(1))
for f in files:
    try:stream=list(gzip.open(f,'rb'))
    except (OSError,EOFError):continue
    for line in stream:
        e=json.loads(line)
        if e['source']!='CLOB market WS':continue
        p=e['payload']
        for x in (p if isinstance(p,list) else [p]):
            k=x.get('event_type');ts=int(x.get('timestamp') or 0)
            if k=='book':
                a=x['asset_id'];books[a]={'b':{D(r['price']):D(r['size']) for r in x['bids'] if D(r['size'])},'a':{D(r['price']):D(r['size']) for r in x['asks'] if D(r['size'])}}
                bad[a]=False;mis[a]=0;last_book[a]+=1
            elif k=='price_change':
                for a in {c['asset_id'] for c in x['price_changes']}:
                    if a not in books or bad.get(a):continue
                    b=books[a]
                    for c in x['price_changes']:
                        if c['asset_id']!=a:continue
                        side=b['b'] if c['side']=='BUY' else b['a'];pr,q=D(c['price']),D(c['size'])
                        if q:side[pr]=q
                        else:side.pop(pr,None)
                    lc=[c for c in x['price_changes'] if c['asset_id']==a][-1]
                    if 'best_bid' in lc and best(b)!=(D(lc['best_bid']),D(lc['best_ask'])):
                        mis[a]=mis.get(a,0)+1
                        if mis[a]>=3:bad[a]=True
                    else:mis[a]=0
            if ts:
                sec=ts//1000;off=sec%300
                if 240<=off<285:
                    for a in books:
                        key=(a,sec)
                        if key not in total:total[key]=1;usable[key]=0 if bad.get(a) else 1
print('asset-seconds in last minute',len(total),'usable %.1f%%'%(100*sum(usable.values())/max(1,len(total))))
print('WS book snapshots per asset (top):',[v for a,v in last_book.most_common(6)])
