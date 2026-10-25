"""Read-only live samples; official labels arrive strictly after predictions."""
import asyncio
from bisect import bisect_right
from collections import deque
from decimal import Decimal as D
import json
import math
from pathlib import Path
import time
from .data import REF,BOOK,TASKS,read,digest,vector,live_registry
from poly5.common import atomic_json,dumps,loads
from poly5.domain import Book,MarketTemplate,DataError,preview_buy_notional
from poly5.api import PublicAPI,MARKET_WS,CLOB
from poly5.reference import ReferenceTape,reference_loop
from .recorder import OrderBookRecorder


def reference_features(tape,market,now):
    if not tape.connected:raise DataError(tape.status)
    spot=[p for p in tape.points['spot'] if p.received_ms<=now]
    twap=[p for p in tape.points['twap'] if p.received_ms<=now]
    if not spot or not twap or max(now-spot[-1].observed_ms,now-twap[-1].observed_ms)>5000:raise DataError('等待新鲜参考行情')
    start=market.start*1000
    opening=[p for p in twap if 0<=start-p.observed_ms<=5000]
    if not opening or now<start+5000:raise DataError('等待可核验的本轮基准')
    base=opening[-1];recv=[p.received_ms for p in spot];history=[]
    for query in range(now-60000,now+1,1000):
        i=bisect_right(recv,query)-1
        if i<0 or query-spot[i].observed_ms>5000:raise DataError('积累 60 秒接收时间历史')
        history.append(float(spot[i].value))
    import numpy as np
    p=history;sp=float(spot[-1].value);tp=float(twap[-1].value);opening=float(base.value)
    f={'spot_open_bps':(sp/opening-1)*1e4,'twap_open_bps':(tp/opening-1)*1e4,'spot_twap_bps':(sp/tp-1)*1e4,
       'return_5s_bps':math.log(p[-1]/p[-6])*1e4,'return_15s_bps':math.log(p[-1]/p[-16])*1e4,
       'return_60s_bps':math.log(p[-1]/p[0])*1e4,'vol_60s_bps':float(np.std(np.diff(np.log(p)),ddof=1)*1e4),
       'stage_fraction':(now-start)/300000}
    evidence={'baseline':str(base.value),'baseline_observed_ms':base.observed_ms,'baseline_received_ms':base.received_ms,
              'spot_observed_ms':spot[-1].observed_ms,'twap_observed_ms':twap[-1].observed_ms,
              'latest_receive_ms':max(spot[-1].received_ms,twap[-1].received_ms),'history_received_price':p}
    assert evidence['latest_receive_ms']<=now
    return f,evidence


class Collector:
    def __init__(self,data,credential_root,stop_event,status):
        self.data=data;self.credential_root=credential_root;self.stop_event=stop_event;self.status=status
        self.api=None;self.tape=ReferenceTape();self.market=None;self.books={};self.history=deque(maxlen=100)
        self.observed=set();self.pending=[];self.models={};self.last_models=0
        self.recorder=OrderBookRecorder(data.root,status)
        from .dynamic_forward import DynamicForward
        self.dynamic=DynamicForward(data);self.last_dynamic_second=None
    def event(self,kind,payload,**kwargs):
        if kind=='reference.connection':self.data.event(kind,payload)
        elif kind=='reference.price':
            try:
                body=payload.get('payload',{});rows=body.get('data',[]) if payload.get('snapshot') else [body]
                kind_=('twap' if str(payload.get('channel','')).endswith('.twap') else 'spot');now=self.api.clock.now()
                for row in rows:self.recorder.on_reference(kind_,int(row['timestamp']),row['full_accuracy_value'],now,bool(payload.get('snapshot')))
            except (KeyError,TypeError,ValueError,AttributeError):pass  # recording must never disturb the feed
    def quotes(self,m,now):
        # Executable $5 entry for each side at the decision moment, walking visible depth with fees (estimate only).
        out={'at_ms':now}
        for side,asset in m.outcomes.items():
            b=self.books.get(asset)
            if not b or not b.fresh(now) or not b.asks:return None
            ask=min(b.asks)
            # Paper only: walk visible depth up to 0.99 (the real average price, slippage included, is what is recorded).
            # The ask+1 tick cap belongs to live order safety and left most late-market decisions without a quote.
            try:p=preview_buy_notional(b,m,'5','.99')
            except DataError:return None
            out[side]={'shares':float(p['shares']),'total':float(p['total']),'best_ask':float(ask),'best_bid':float(max(b.bids)) if b.bids else None}
        return out
    def refresh_models(self):
        # Research registry only. Never loads or edits the trading application's model.
        if time.monotonic()-self.last_models>20:
            self.last_models=time.monotonic();registry=live_registry(self.data.root)
            import joblib
            for m in registry:
                if m['id'] in self.models:continue
                p=Path(m['model_path'])
                if p.is_file() and digest(p)==m['model_sha256']:
                    self.models[m['id']]=(joblib.load(p),m)
            keep={m['id'] for m in registry};self.models={k:v for k,v in self.models.items() if k in keep}
    def predict(self,id,task,f,now):
        self.refresh_models()
        for key,(bundle,meta) in self.models.items():
            if bundle['task']!=task or meta['metrics']['trained_through_ms']>=now:continue
            x=[vector(f,bundle['features'])];model=bundle['model']
            prediction=model.predict_proba(x)[0,1] if TASKS[task][2]=='classification' else model.predict(x)[0]
            self.data.predict(key,id,prediction,now)
    async def stream(self,market):
        import websockets
        while not self.stop_event.is_set() and self.market is market:
            try:
                async with websockets.connect(MARKET_WS,open_timeout=10,ping_interval=20,max_size=4*1024*1024) as ws:
                    await ws.send(dumps({'assets_ids':list(market.outcomes.values()),'type':'market','custom_feature_enabled':True}))
                    async def ping():
                        while True:await asyncio.sleep(10);await ws.send('PING')
                    heartbeat=asyncio.create_task(ping())
                    try:
                        # No REST seeding: a REST snapshot is not sequenced with the stream, and mixing them produced
                        # constant best-bid/ask mismatches (GAP) and reconnect loops. The stream sends a full book on
                        # subscribe and after trades, which (re)initialises each side.
                        async for raw in ws:
                            if raw in ('PING','PONG'):continue
                            message=loads(raw);messages=message if isinstance(message,list) else [message]
                            now=self.api.clock.now()
                            for item in messages:
                                kind=item.get('event_type')
                                if kind=='book' and item.get('asset_id') in self.books:
                                    asset=item['asset_id'];self.books[asset].snapshot(item,now);self.recorder.on_book(asset,self.books[asset],now,market.slug,int(item.get('timestamp') or 0))
                                elif kind=='price_change':
                                    for asset in {v['asset_id'] for v in item['price_changes']}:
                                        if asset in self.books:
                                            self.books[asset].delta(item['price_changes'],int(item['timestamp']),now);self.recorder.on_book(asset,self.books[asset],now,market.slug,int(item['timestamp']))
                                elif kind=='last_trade_price' and item.get('asset_id') in self.books:self.recorder.on_trade(item,now,market.slug)
                            # A GAP leaves that book uninitialised (not fresh) until the next full 'book' message.
                    finally:heartbeat.cancel();await asyncio.gather(heartbeat,return_exceptions=True)
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.status['book']='重连：'+type(exc).__name__
                for b in self.books.values():b.initialized=False
                await asyncio.sleep(3)
    async def labels(self,now):
        with self.data.db() as c:
            rows=c.execute("SELECT id,slug,start_s,aux FROM samples WHERE target IS NULL AND source='live' AND task IN ('late','direction','late_mkt','direction_mkt') AND start_s+300<=?",(now//1000,)).fetchall()
        grouped={}
        for id,slug,start,aux in rows:grouped.setdefault(json.loads(aux)['condition_id'],[]).append((id,slug,start))
        for condition,samples in grouped.items():
            raw=await self.api.get(CLOB+'/markets/'+condition)
            tokens=raw.get('tokens',[])
            if raw.get('condition_id')!=condition or raw.get('closed') is not True or len(tokens)!=2 or any(type(t.get('winner')) is not bool for t in tokens) or sum(t['winner'] for t in tokens)!=1:continue
            winning=next(t['outcome'] for t in tokens if t['winner'])
            if winning not in ('Up','Down'):continue
            for id,slug,start in samples:self.data.label(id,int(winning=='Up'),now)
            self.data.event('official_label',{'condition_id':condition,'winning':winning,'observed_ms':now})
    async def run(self):
        self.api=PublicAPI();stop=asyncio.Event();feed=None;stream=None;backfill_task=None;last_backfill=0;last_clock=last_discovery=last_labels=0
        try:
            await self.api.sync_clock()
            feed=asyncio.create_task(reference_loop(self.credential_root,self.tape,stop,self.api.clock,self.event))
            while not self.stop_event.is_set():
                try:
                    now=self.api.clock.now()
                    if time.monotonic()-last_clock>30:await self.api.sync_clock();last_clock=time.monotonic();now=self.api.clock.now()
                    self.status['reference']=self.tape.status
                    if self.market is None or now>=self.market.end*1000:
                        if time.monotonic()-last_discovery<3:await asyncio.sleep(.25);continue
                        last_discovery=time.monotonic()
                        if stream:stream.cancel();await asyncio.gather(stream,return_exceptions=True)
                        self.market,_,_=await self.api.discover(MarketTemplate(),now//1000)
                        self.books={a:Book(a,self.market.condition_id) for a in self.market.outcomes.values()};self.history.clear();self.recorder.on_market(self.market)
                        stream=asyncio.create_task(self.stream(self.market));self.status['market']=self.market.slug
                    m=self.market;b=self.books.get(m.outcomes['Up']);elapsed=(now-m.start*1000)/1000
                    self.recorder.snapshot(now,m,self.books);self.recorder.maybe_flush()
                    if now//1000!=self.last_dynamic_second:
                        self.last_dynamic_second=now//1000;self.refresh_models()
                        try:df,de=reference_features(self.tape,m,now)
                        except DataError:df=de=None
                        frames={}
                        for side,asset in m.outcomes.items():
                            book=self.books[asset];bs=sorted(book.bids.items(),reverse=True)[:20];az=sorted(book.asks.items())[:20]
                            frames[side]={'available_ms':now,'source_ms':book.source_ms,'received_ms':book.received_ms,
                                'initialized':book.initialized,'quality':book.quality,'bid_prices':[float(p) for p,q in bs],
                                'bid_sizes':[float(q) for p,q in bs],'ask_prices':[float(p) for p,q in az],'ask_sizes':[float(q) for p,q in az]}
                        self.dynamic.tick(m,frames,df,de,[meta for bundle,meta in self.models.values() if meta['task']=='dynamic'],now)
                    if time.monotonic()-last_backfill>300 and (backfill_task is None or backfill_task.done()):
                        if backfill_task is not None:
                            try:backfill_task.result()
                            except Exception as exc:self.status['dynamic_backfill_error']=str(exc)[:160]
                        from .dynamic_dataset import backfill
                        backfill_task=asyncio.create_task(asyncio.to_thread(lambda:asyncio.run(backfill(self.data,self.status,self.stop_event))))
                        last_backfill=time.monotonic()
                    if b and b.fresh(now):self.history.append((now,(max(b.bids)+min(b.asks))/2))
                    for offset in (60,120,210,240,270):
                        key=f'{m.slug}:{offset}'
                        if key in self.observed or elapsed<offset:continue
                        if elapsed>offset+2:self.observed.add(key);continue
                        try:f,evidence=reference_features(self.tape,m,now)
                        except DataError:continue
                        self.observed.add(key)
                        aux={'condition_id':m.condition_id,'outcomes':m.outcomes,'evidence':evidence,'fee':str(m.fee.rate) if m.fee else None,'quotes':self.quotes(m,now)}
                        for task in ['direction']+(['late'] if offset>=240 else []):
                            id=f'live:{task}:{key}';self.data.add(id,task,m.slug,m.start,now,f,aux=aux);self.predict(id,task,f,now)
                        q=aux['quotes']
                        if q:
                            # Market-aware rows use the same executable quotes the paper P&L is settled against.
                            fm={**f,'mkt_bid':q['Up']['best_bid'],'mkt_ask':q['Up']['best_ask'],'up_avg_price':q['Up']['total']/q['Up']['shares'],'down_avg_price':q['Down']['total']/q['Down']['shares']}
                            if fm['mkt_bid'] is not None:
                                for task in ['direction_mkt']+(['late_mkt'] if offset>=240 else []):
                                    id=f'live:{task}:{key}';self.data.add(id,task,m.slug,m.start,now,fm,aux=aux);self.predict(id,task,fm,now)
                    if time.monotonic()-last_labels>15:
                        await self.labels(now);await self.dynamic.resolve(self.api,now);last_labels=time.monotonic()
                    self.status['book']='已连接' if b and b.fresh(now) else '等待新鲜盘口'
                    await asyncio.sleep(.25)
                except asyncio.CancelledError:raise
                except Exception as exc:
                    self.status['error']=type(exc).__name__+': '+str(exc)[:120];self.status['error_ms']=int(time.time()*1000);await asyncio.sleep(3)
        finally:
            stop.set()
            try:self.recorder.maybe_flush(force=True)
            except Exception as exc:self.status['recorder']='盘口记录写入失败：'+type(exc).__name__
            for task in [feed,stream,backfill_task]:
                if task:task.cancel()
            await asyncio.gather(*[t for t in [feed,stream,backfill_task] if t],return_exceptions=True)
            await self.api.close()
