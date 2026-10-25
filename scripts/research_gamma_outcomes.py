"""Official BTC 5m outcomes straight from the Polymarket Gamma API (series btc-up-or-down-5m, id 10684).

Enumerates btc-updown-5m-<start_ts> slugs on the 300s grid and queries /markets in batches. Only closed markets with
umaResolutionStatus=resolved and outcomePrices exactly 1/0 are kept. Output columns match the Sigrex CSV subset used
by research_proxy_features.py: slug, condition_id, start_ts, label_up, winning_outcome.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse,csv,json,time
import httpx

GAMMA='https://gamma-api.polymarket.com/markets'

def ts(day):return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())

def query(client,slugs):
    for attempt in range(5):
        try:
            r=client.get(GAMMA,params=[('slug',s) for s in slugs]+[('closed','true'),('limit',str(len(slugs)))]);r.raise_for_status();return r.json()
        except Exception:
            if attempt==4:raise
            time.sleep(2**attempt)

def outcome(m):
    """('Up'|'Down', label) only for an unambiguous official resolution, else (reason, None)."""
    if not m.get('closed'):return 'not_closed',None
    if m.get('umaResolutionStatus')!='resolved':return 'uma_'+str(m.get('umaResolutionStatus')),None
    names,prices=json.loads(m['outcomes']),json.loads(m['outcomePrices'])
    if names!=['Up','Down']:return 'outcomes_'+'/'.join(names),None
    if prices==['1','0']:return 'Up',1
    if prices==['0','1']:return 'Down',0
    return 'prices_'+'/'.join(prices),None

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--start',required=True);p.add_argument('--end',required=True,help='exclusive UTC date');p.add_argument('--out',required=True)
    a=p.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    now=int(time.time());starts=[t for t in range(ts(a.start),ts(a.end),300) if t+300<now]
    kept,skipped,found=[],{},set()
    with httpx.Client(timeout=60) as c:
        for i in range(0,len(starts),50):
            for m in query(c,[f'btc-updown-5m-{t}' for t in starts[i:i+50]]):
                if m['slug'] in found:continue
                found.add(m['slug']);name,label=outcome(m)
                if label is None:skipped[name]=skipped.get(name,0)+1;continue
                kept.append({'slug':m['slug'],'condition_id':m['conditionId'],'start_ts':int(m['slug'].rsplit('-',1)[1]),'label_up':label,'winning_outcome':name})
    kept.sort(key=lambda r:r['start_ts'])
    with open(out/'gamma-outcomes.csv','w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['slug','condition_id','start_ts','label_up','winning_outcome']);w.writeheader();w.writerows(kept)
    manifest={'source':GAMMA,'series':'btc-up-or-down-5m (id 10684)','observed_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'range_utc':[a.start,a.end],'grid_slugs':len(starts),'found':len(found),'missing':len(starts)-len(found),'kept':len(kept),'skipped':skipped,
              'rule':'closed and umaResolutionStatus=resolved and outcomePrices exactly [1,0] or [0,1] with outcomes [Up,Down]'}
    (out/'gamma-manifest.json').write_text(json.dumps(manifest,indent=1),encoding='utf-8');print(manifest)
