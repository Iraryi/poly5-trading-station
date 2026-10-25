from __future__ import annotations
import argparse
import gzip
from pathlib import Path
from .common import loads,atomic_json
from .domain import Book

def replay_files(paths):
    def events():
        for path in sorted(map(Path,paths)):
            if path.suffix=='.tmp':continue
            if not path.exists() and path.suffix=='.jsonl':path=path.with_suffix('.jsonl.gz')
            opener=gzip.open if path.suffix=='.gz' else open
            with opener(path,'rt',encoding='utf-8') as stream:
                for line in stream:
                    try:yield loads(line)
                    except ValueError:
                        if not line.endswith('\n'):break
                        raise
    return replay_events(events())

def replay_events(events):
    books={};seen=set();counts={};markets=set();errors=[];processed=0
    for e in events:
        if e['event_id'] in seen:continue
        seen.add(e['event_id']);processed+=1;counts[e['event_type']]=counts.get(e['event_type'],0)+1
        if e['event_type']=='market.discovered':markets.add(e['payload']['market']['slug'])
        if e['event_type']!='book.raw':continue
        payload=e['payload']
        for raw in payload if isinstance(payload,list) else [payload]:
            try:
                if raw.get('event_type')=='book':
                    asset=raw['asset_id'];book=books.setdefault(asset,Book(asset,raw['market']))
                    book.snapshot(raw,int(raw['timestamp']))
                elif raw.get('event_type')=='price_change':
                    for asset in {c['asset_id'] for c in raw['price_changes']}:
                        if asset in books:books[asset].delta(raw['price_changes'],int(raw['timestamp']),int(raw['timestamp']))
            except Exception as exc:errors.append({'event_id':e['event_id'],'error':str(exc)})
    return {'mode':'replay','events':processed,'counts':counts,'markets':sorted(markets),'parse_errors':errors,
            'books':{k:b.view(b.source_ms) for k,b in books.items()},'live_calls':0,
            'limitations':'重建收到的盘口，不补造网络空洞，不评定盈利优势。'}

def main():
    p=argparse.ArgumentParser();p.add_argument('directory');p.add_argument('--output',required=True);a=p.parse_args()
    result=replay_files(Path(a.directory).rglob('*.jsonl*'));atomic_json(Path(a.output),result)
    print('Replayed',result['events'],'events;',len(result['markets']),'markets;',len(result['parse_errors']),'errors')
if __name__=='__main__':main()
