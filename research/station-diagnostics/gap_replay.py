"""Replay captured raw ticks: how often does the per-message best-price check flag a gap, does the
mismatch heal by itself within the next messages, and does a never-reset book agree with the next
full snapshot (ground truth)?"""
import gzip,json,sys,glob
from decimal import Decimal as D
files=sorted(glob.glob('D:/POLYMARKET/data/log/events/raw/*.jsonl.gz'))[-int(sys.argv[1]):]
books={}
flags=0;healed={1:0,3:0,10:0};pending={};msgs=0;snap_cmp=[0,0];top_cmp=[0,0]
def best(b):return (max(b['b']) if b['b'] else D(0),min(b['a']) if b['a'] else D(1))
for f in files:
    for line in gzip.open(f):
        e=json.loads(line);items=e['payload'] if isinstance(e['payload'],list) else [e['payload']]
        for x in items:
            k=x.get('event_type')
            if k=='book':
                a=x['asset_id'];snap={'b':{D(r['price']):D(r['size']) for r in x['bids'] if D(r['size'])},'a':{D(r['price']):D(r['size']) for r in x['asks'] if D(r['size'])}}
                if a in books:
                    snap_cmp[0]+=1;snap_cmp[1]+=books[a]['b']==snap['b'] and books[a]['a']==snap['a']
                    top_cmp[0]+=1;top_cmp[1]+=best(books[a])==best(snap)
                books[a]=snap;pending.pop(a,None)
            elif k=='price_change':
                msgs+=1
                for a in {c['asset_id'] for c in x['price_changes']}:
                    if a not in books:continue
                    b=books[a]
                    for c in x['price_changes']:
                        if c['asset_id']!=a:continue
                        side=b['b'] if c['side']=='BUY' else b['a'];p,q=D(c['price']),D(c['size'])
                        if q:side[p]=q
                        else:side.pop(p,None)
                    last=[c for c in x['price_changes'] if c['asset_id']==a][-1]
                    ok=best(b)==(D(last['best_bid']),D(last['best_ask']))
                    if a in pending:
                        pending[a]+=1
                        if ok:
                            for h in healed:
                                if pending[a]<=h:healed[h]+=1
                            del pending[a]
                    elif not ok:flags+=1;pending[a]=0
print('files',len(files),'price_change msgs',msgs,'mismatch flags',flags,'(%.2f%%)'%(100*flags/max(1,msgs)))
print('healed within next 1/3/10 msgs:',{h:'%d (%.0f%%)'%(v,100*v/max(1,flags)) for h,v in healed.items()})
print('never-reset book vs next full snapshot: identical %d/%d, same top-of-book %d/%d'%(snap_cmp[1],snap_cmp[0],top_cmp[1],top_cmp[0]))
