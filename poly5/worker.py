from __future__ import annotations
import asyncio
from collections import deque
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import sys
import threading
import time
import uuid
import websockets
from account_projection import OrderProjection, OrderUpdate
from . import __version__
from .api import PublicAPI, PublicAccountAdapter, AuthReadAdapter, MARKET_WS, USER_WS, GEO
from .common import D, Settings, atomic_json, dumps, epoch_ms, loads, redact, utc_now, decimal
from .copilot import Copilot
from .domain import Book, Quote, MarketTemplate, Registry, DataError
from .execution import ExecutionGateway
from .storage import EventStore

class Engine:
    def __init__(self,root,emit=None,offline=False):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.settings=Settings(root); self.store=EventStore(root)
        self.api=PublicAPI(); self.account_adapter=PublicAccountAdapter(self.api); self.auth=AuthReadAdapter(self.api,root,lambda:self.settings.active_account())
        self.account_auth=None
        from .model_bridge import ModelBridge,ShadowRuntime,VoteRuntime
        self.model_bridge=ModelBridge(root);self.shadow_runtime=ShadowRuntime(self.model_bridge)
        self.vote_runtime=VoteRuntime(self.model_bridge);self.vote=None;self.vote_report=None;self.vote_future=None
        self.ws_lag_ms=None;self.ws_lag_peak=0;self.ws_lag_logged=0
        self.recent_markets=deque(maxlen=12);self.round_results={}
        from concurrent.futures import ThreadPoolExecutor
        self.vote_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='late-vote')
        if self.settings.data.get('vote_release_ids'):
            from .model_bridge import preload_inference
            preload_inference()
        self.shadow={'status':'DISABLED','message':'尚未选择影子模型'};self.model_catalog=[]
        self.shadow_book_history=deque(maxlen=100);self.shadow_market=None
        self.gateway=ExecutionGateway(self.store); self.copilot=Copilot(root,self.store)
        self.registry=Registry.builtins(); self.template=MarketTemplate()
        self.market=None; self.books={}; self.quotes={};self.book_resync=asyncio.Event();self.geo={'blocked':None,'country':'UNKNOWN','status':'UNKNOWN'}
        self.account=None; self.account_error=None; self.orders={}; self.order_rows={}
        self.evidence=deque(maxlen=12); self.notes=deque(maxlen=30)
        self.stop=asyncio.Event(); self.commands=asyncio.Queue(); self.emit=emit or (lambda _:None)
        self.offline=offline; self.paused=False; self.ws_status='OFFLINE' if offline else 'CONNECTING'
        self.user_ws_status='未授权'; self.user_ws_connected=False; self.ws_last=0; self.reconnects=0; self.preview_cache={}
        self.task_group=[]; self.market_task=None; self.last_geo=0; self.last_account=0; self.last_auth=0
        self.auth_account=None; self.auth_task=None; self.ai_task=None; self.last_state={}
        self.prefetched=None; self.stale=None
        from .auto_paper import AutoPaper
        from .reference import ReferenceTape
        self.reference=ReferenceTape()
        self.auto_paper=AutoPaper(root,self.gateway,self.event,reference=self.reference,entry_mode=self.settings.data.get('auto_paper_strategy','last_minute_50'))
        from .live_service import LiveService
        self.live=LiveService(self)
        for event in self.store.all('account.order_update'):
            self.merge_order(event['account_id'],event['payload'],event['source'],persist=False)

    def event(self,kind,payload,**kwargs):
        try:
            eid=self.store.append(kind,payload,**kwargs)
            if not kind.startswith('book.raw'):
                self.evidence.append(eid); self.notes.append({'at':utc_now(),'kind':kind,'id':eid,'detail':dumps(redact(payload))[:230]})
            return eid
        except RuntimeError:
            self.gateway.paused=True
            return None

    def state(self):
        now=self.api.clock.now()
        market=asdict(self.market) if self.market else None
        books={name:self.books[asset].view(now or epoch_ms()) for name,asset in (self.market.outcomes.items() if self.market else []) if asset in self.books}
        quotes={name:self.quotes[asset].view(now or epoch_ms()) for name,asset in (self.market.outcomes.items() if self.market else []) if asset in self.quotes}
        elapsed=(now//1000-self.market.start) if self.market and now is not None else None
        phases=['早期研究','过渡 / 管理持仓','末段方向研究','停止新增风险']
        phase=phases[0 if elapsed<180 else 1 if elapsed<210 else 2 if elapsed<285 else 3] if elapsed is not None else '等待市场'
        account=dict(self.account) if self.account else None
        if account:
            account['quality']='STALE' if epoch_ms()-account['observed_ms']>45000 or self.account_error else 'OK'
            auth=self.account_auth
            if auth and auth.get('account_id')==self.settings.data.get('active_account_id') and auth.get('quality')=='OK' and epoch_ms()-auth.get('observed_ms',0)<30000:
                account.update(cash=auth.get('cash'),mode='authenticated_readonly',cash_source=auth.get('source'),cash_observed_at=auth.get('observed_at'))
        active=self.settings.active_account()
        orders=[v for (aid,_),v in self.order_rows.items() if active and aid==active['id']]
        result={'version':__version__,'at':utc_now(),'mode':self.settings.data['mode'],'market':market,'books':books,'quotes':quotes,
                'remaining':max(0,self.market.end-now//1000) if self.market and now else None,'phase':phase,
                'server_ms':now,'clock_valid':self.api.clock.valid(),'clock_skew_ms':self.api.clock.skew_ms,
                'clock_rtt_ms':self.api.clock.rtt_ms,'ws_status':self.ws_status,'user_ws_status':self.user_ws_status,
                'ws_age_ms':epoch_ms()-self.ws_last if self.ws_last else None,'ws_lag_ms':self.ws_lag_ms,'reconnects':self.reconnects,
                'geo':self.geo,'network':self.api.health,'account':account,'account_error':self.account_error,
                'active_account':redact(active),'auth_orders':orders if self.auth_account else None,
                'paper':self.gateway.view(),'modules':[asdict(m) for m in self.registry.modules.values()],
                'twap_status':self.reference.status,
                'strategy_reason':'按基准距离、持续趋势和含费报价评估入场；无固定持仓秒数，净盈利、突发反向风险或封盘前退出。',
                'auto_paper':dict(self.auto_paper.status),'late_vote_report':self.vote_report,
                'storage_error':self.store.error,'storage_bytes':self.store.bytes_written,'storage_raw_bytes':self.store.raw_bytes,'storage_raw_dropped':self.store.raw_dropped,
                'evidence_ids':list(self.evidence),'events':list(self.notes),'paused':self.paused,'live_enabled':self.live.armed,
                'live':dict(self.live.status),
                'prefetched_slug':self.prefetched.get('slug') if self.prefetched else None}
        from .readiness import assess
        auth_view=dict(self.account_auth) if self.account_auth else None
        if auth_view and epoch_ms()-auth_view.get('observed_ms',0)>=30000 and auth_view.get('quality')=='OK':auth_view['quality']='STALE'
        result.update(model_bridge=self.shadow,account_auth=redact(auth_view),readiness=assess(result,self.shadow,auth_view))
        result['quick']=self.quick_view(now,account)
        return result

    def quick_view(self,now,account):
        """Everything the web-style quick-trade page shows, from data this process already verifies."""
        m=self.market
        if not m:return None
        now=now or epoch_ms();tape=self.reference
        base=tape.baselines.get(m.id);series=[];last=None
        for p in list(tape.points['spot']):
            if p.observed_ms<m.start*1000-1000 or p.observed_ms>now:continue
            if p.observed_ms//1000!=last:series.append([p.observed_ms,float(p.value)]);last=p.observed_ms//1000
        sides={}
        for name,asset in m.outcomes.items():
            b=self.books.get(asset);q=self.quotes.get(asset)
            bv=b.view(now) if b else {};qv=q.view(now) if q else {}
            sides[name]={'bid':str(bv.get('bid') if bv.get('bid') is not None else qv.get('bid') or '') or None,
                         'ask':str(bv.get('ask') if bv.get('ask') is not None else qv.get('ask') or '') or None,
                         'bids':[[str(p),str(s)] for p,s in bv.get('bids',[])],'asks':[[str(p),str(s)] for p,s in bv.get('asks',[])],
                         'fresh':bool(bv.get('fresh'))}
        live=self.live;pos=pending=None
        if live.ledger:
            pos=live.ledger.get('position');pending=live.ledger.get('pending')
        rounds=[{'start':r['start'],'slug':r['slug'],'outcome':self.round_results.get(r['slug'])} for r in list(self.recent_markets)[-6:]]
        cash=(account or {}).get('cash')
        return {'slug':m.slug,'market_id':m.id,'start':m.start,'end':m.end,'target':str(base.value) if base else None,
                'price':series[-1][1] if series else None,'series':series[-330:],'sides':sides,'rounds':rounds,
                'cash':str(cash) if cash is not None else None,'armed':live.armed,'position':pos,'pending':pending,
                'attested':bool(live.ledger and live.prior_attestation()),
                'live_message':live.status.get('message'),'last_receipt':live.status.get('last_receipt'),
                'fee_rate':str(m.fee.rate) if m.fee else None,'fee_exponent':m.fee.exponent if m.fee else None,'tick':str(m.tick),
                'min_size':str(m.min_size),'server_ms':now}

    async def round_results_loop(self):
        """Official winners of recently finished rounds (CLOB market resolution), for the result dots."""
        from .api import CLOB
        while not self.stop.is_set():
            now=self.api.clock.now()
            for r in list(self.recent_markets):
                if r['slug'] in self.round_results or now is None or now<(r['end']+5)*1000:continue
                try:
                    raw=await self.api.get(CLOB+'/markets/'+r['condition_id'])
                    tokens=raw.get('tokens',[]) if isinstance(raw,dict) else []
                    if raw.get('closed') is True and len(tokens)==2 and all(type(t.get('winner')) is bool for t in tokens):
                        win=next((t for t in tokens if t['winner']),None)
                        if win:self.round_results[r['slug']]=win.get('outcome')
                except Exception:pass
            await asyncio.sleep(15)

    async def model_shadow_loop(self):
        from .model_bridge import reference_features
        from .domain import preview_buy_notional
        last_prediction=last_log=0
        while not self.stop.is_set():
            meta=None
            try:
                now=self.api.clock.now();m=self.market
                if not self.settings.data.get('model_shadow_enabled'):
                    self.shadow={'status':'DISABLED','message':'尚未启用固定模型影子预测'};await asyncio.sleep(1);continue
                if not m or not self.api.clock.valid():raise DataError('等待已验证市场与服务端时间')
                if self.store.error:raise DataError('事实账本不可写，影子预测已暂停：'+self.store.error)
                meta=await asyncio.to_thread(self.model_bridge.selected)
                if not meta:raise DataError('请在模型接入页选择并固定版本')
                if self.shadow_market!=m.id:
                    self.shadow_book_history.clear();self.shadow_market=m.id
                up=self.books.get(m.outcomes['Up'])
                if up and up.fresh(now):self.shadow_book_history.append((now,(max(up.bids)+min(up.asks))/2))
                if time.monotonic()-last_prediction<2:await asyncio.sleep(.25);continue
                last_prediction=time.monotonic()
                f=reference_features(self.reference,m,now)
                if meta['task'].endswith('_mkt'):
                    prices={}
                    for side,asset in m.outcomes.items():
                        book=self.books.get(asset)
                        if not book or not book.fresh(now):raise DataError('等待两侧新鲜可成交深度')
                        q=preview_buy_notional(book,m,'5','.99');prices[side]=float(q['total']/q['shares'])
                    f.update(mkt_bid=float(max(up.bids)),mkt_ask=float(min(up.asks)),up_avg_price=prices['Up'],down_avg_price=prices['Down'])
                elif meta['task'] in ('roi5','roi15'):
                    if not up or not up.fresh(now):raise DataError('等待新鲜 Up 盘口')
                    past=[]
                    for delay in (1000,5000):
                        values=[mid for at,mid in self.shadow_book_history if at<=now-delay and now-delay-at<=1500]
                        if not values:raise DataError('积累 5 秒盘口历史')
                        past.append(values[-1])
                    bid=max(up.bids);ask=min(up.asks);mid=(bid+ask)/2
                    bs=sorted(up.bids.items(),reverse=True);az=sorted(up.asks.items())
                    bd=sum((q for p,q in bs[:5]),D(0));ad=sum((q for p,q in az[:5]),D(0))
                    f.update(mid=float(mid),spread=float(ask-bid),imbalance5=float((bd-ad)/(bd+ad)),
                        microprice_offset=float((ask*bs[0][1]+bid*az[0][1])/(bs[0][1]+az[0][1])-mid),
                        mid_change_1s=float(mid-past[0]),mid_change_5s=float(mid-past[1]),book_age_s=(now-up.source_ms)/1000)
                output=await asyncio.to_thread(self.shadow_runtime.predict,f,now,m)
                if not self.market or self.market.id!=m.id or not self.settings.data.get('model_shadow_enabled') or output['release_id']!=meta['release_id']:continue
                self.shadow={'status':'OK','message':'影子预测中 · 不驱动下单','selected':meta,'output':output,'observed_ms':epoch_ms()}
                if time.monotonic()-last_log>=10:
                    last_log=time.monotonic();self.event('model.shadow_prediction',output,market_id=m.id,condition_id=m.condition_id,source='pinned trainer model',source_ms=now)
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.shadow={'status':'WAITING','message':str(exc)[:160],'observed_ms':epoch_ms()}
                if meta:self.shadow['selected']=meta
            await asyncio.sleep(.25)

    async def publish(self):
        while not self.stop.is_set():
            state=self.state(); self.last_state=state
            if (self.root/'STOP').exists():
                (self.root/'STOP').unlink();self.stop.set()
            atomic_json(self.root/'cache'/'snapshot.json',state)
            self.emit({'type':'state','state':state})
            fresh=bool(state['books']) and all(b['fresh'] for b in state['books'].values())
            if fresh!=self.stale:
                self.stale=fresh; self.event('book.freshness_changed',{'fresh':fresh})
            await asyncio.sleep(1)

    async def late_vote(self,now):
        """Averaged probability of the pinned exam-top late models; computed off the event loop, last minute only."""
        m=self.market;ids=self.settings.data.get('vote_release_ids') or []
        if not m or now is None or not ids or not m.start*1000+240000<=now<m.end*1000:return None
        if self.vote and self.vote['market_id']==m.id and now-self.vote['at_ms']<1000:return self.vote
        from .model_bridge import reference_features
        if self.vote_future and not self.vote_future.done():
            # A previous prediction is still running: never queue more work or block the paper loop on it.
            self.vote=None;self.auto_paper.status['vote_status']='模型投票计算未返回，暂停新增模拟仓位';return None
        try:
            f=reference_features(self.reference,m,now)
            self.vote_future=asyncio.get_running_loop().run_in_executor(self.vote_executor,self.vote_runtime.predict,ids,f,now,m)
            self.vote=await asyncio.wait_for(asyncio.shield(self.vote_future),3)
            self.auto_paper.status['vote_status']='OK'
        except asyncio.TimeoutError:
            self.vote=None;self.auto_paper.status['vote_status']='模型投票超时，暂停新增模拟仓位'
        except (ValueError,DataError) as exc:
            self.vote=None;self.auto_paper.status['vote_status']=str(exc)[:120]
        return self.vote

    async def automatic_paper_loop(self):
        last_report=0
        while not self.stop.is_set():
            try:
                asset=self.market.outcomes.get('Up') if self.market else None
                # PAPER runs alongside live (user-approved 2026-10-24) so the vote rule keeps collecting evidence; it never sends orders.
                enabled=self.settings.data.get('auto_paper_enabled',False) and self.settings.data['mode']=='paper' and not self.paused
                self.auto_paper.entry_mode=self.settings.data.get('auto_paper_strategy','last_minute_50')
                books={name:self.books.get(asset) for name,asset in self.market.outcomes.items()} if self.market else {}
                now=self.api.clock.now()
                vote=await self.late_vote(now) if self.auto_paper.entry_mode=='late_vote' else None
                # Re-read the clock after awaiting the vote: books keep updating meanwhile, and judging them with the
                # earlier timestamp made every newer update look "received in the future", i.e. never fresh (dev.3-10).
                now=self.api.clock.now()
                self.auto_paper.step(self.market,self.books.get(asset),now,enabled,self.api.clock.valid(),books=books,vote=vote)
                if time.monotonic()-last_report>=30 and self.settings.data.get('late_vote_since_ms'):
                    last_report=time.monotonic()
                    from .strategy_report import report
                    self.vote_report=await asyncio.to_thread(report,self.store,'late_vote',self.settings.data['late_vote_since_ms'],epoch_ms())
            except Exception as e:
                self.auto_paper.status['status']='自动模拟异常：'+str(e)[:100]
                self.gateway.paused=True;self.event('auto_paper.error',{'error':str(e)[:150]})
            await asyncio.sleep(.25)

    async def paper_settlement_loop(self):
        from .api import CLOB
        while not self.stop.is_set():
            now=self.api.clock.now()
            if now is not None and self.api.clock.valid():
                for asset,pos in list(self.gateway.positions.items()):
                    if pos['shares']<=0 or not pos.get('window_end_s') or now<pos['window_end_s']*1000:continue
                    try:
                        resolution=await self.api.get(CLOB+'/markets/'+pos['condition_id'])
                        self.gateway.settle_paper(resolution,asset,now)
                    except Exception as exc:
                        self.auto_paper.status['settlement_status']='等待官方确定结果：'+str(exc)[:80]
            await asyncio.sleep(10)

    async def sync(self):
        last_clock=0
        while not self.stop.is_set():
            try:
                if time.monotonic()-last_clock>30:
                    await self.api.sync_clock(); last_clock=time.monotonic()
                now=self.api.clock.now()//1000
                if not self.paused and (self.market is None or now>=self.market.end):
                    previous_market=self.market
                    market,event,clob=await self.api.discover(self.template,now)
                    if self.market_task:
                        self.market_task.cancel(); await asyncio.gather(self.market_task,return_exceptions=True)
                    self.market=market; self.books={asset:Book(asset,market.condition_id) for asset in market.outcomes.values()};self.quotes={asset:Quote() for asset in market.outcomes.values()}
                    self.event('market.discovered',{'market':asdict(market),'gamma':event,'clob':clob},market_id=market.id,condition_id=market.condition_id,source='Gamma + CLOB + unified SDK')
                    self.preview_cache.clear()
                    self.market_task=asyncio.create_task(self.stream())
                    self.prefetched=None
                    if previous_market:
                        self.recent_markets.append({'start':previous_market.start,'end':previous_market.end,'slug':previous_market.slug,
                                                    'condition_id':previous_market.condition_id,'outcomes':dict(previous_market.outcomes)})
                        from .reporting import observation_report
                        await asyncio.to_thread(self.store.flush)
                        await asyncio.to_thread(observation_report,self.store.db_path,self.root/'log'/'reports'/previous_market.slug)
                if self.market and not self.prefetched and self.market.end-now<=45:
                    from .api import GAMMA
                    self.prefetched=await self.api.get(GAMMA+'/events/slug/'+self.template.candidate(self.market.end))
                    self.event('market.prefetched',{'slug':self.prefetched.get('slug'),'validated_for_trading':False})
            except asyncio.CancelledError: raise
            except Exception as e:
                self.event('network.discovery_error',{'error':str(e)[:200]}); await asyncio.sleep(5)
            await asyncio.sleep(2)

    async def stream(self):
        delay=1
        while not self.stop.is_set():
            if self.paused:
                self.ws_status='PAUSED'; await asyncio.sleep(1); continue
            heartbeat=None
            try:
                self.ws_status='CONNECTING'
                for b in self.books.values(): b.initialized=False
                async with websockets.connect(MARKET_WS,open_timeout=10,ping_interval=20,ping_timeout=20,max_size=4*1024*1024) as ws:
                    await ws.send(dumps({'assets_ids':list(self.books),'type':'market','custom_feature_enabled':True}))
                    self.ws_status='CONNECTED'; self.last_geo=0
                    self.event('network.market_ws_connected',{'round':self.market.slug})
                    heartbeat=asyncio.create_task(self.heartbeat(ws))
                    delay=1
                    async for raw in ws:
                        if self.paused: break
                        self.ws_last=epoch_ms()
                        if raw in ('PONG','PING'):
                            if raw=='PING': await ws.send('PONG')
                            continue
                        payload=loads(raw)
                        self.track_ws_lag(payload)
                        self.event('book.raw',payload,source='CLOB market WS',market_id=self.market.id,condition_id=self.market.condition_id)
                        for item in payload if isinstance(payload,list) else [payload]:
                            self.apply_book(item)
            except asyncio.CancelledError: raise
            except Exception as e:
                self.ws_status='DISCONNECTED'; self.reconnects+=1
                self.event('network.market_ws_gap',{'error':type(e).__name__,'reconnects':self.reconnects})
            finally:
                for b in self.books.values(): b.initialized=False
                if heartbeat:
                    heartbeat.cancel(); await asyncio.gather(heartbeat,return_exceptions=True)
            await asyncio.sleep(delay); delay=min(delay*2,30)

    def track_ws_lag(self,payload):
        """Server-clock delay of each market message; a growing value means this process is falling behind."""
        first=payload[0] if isinstance(payload,list) and payload else payload
        now=self.api.clock.now()
        try:lag=now-int(first['timestamp']) if now is not None and isinstance(first,dict) and first.get('timestamp') else None
        except (TypeError,ValueError):lag=None
        if lag is None:return
        self.ws_lag_ms=lag
        if lag>1000:
            self.ws_lag_peak=max(self.ws_lag_peak,lag)
            if time.monotonic()-self.ws_lag_logged>=10:
                self.ws_lag_logged=time.monotonic()
                self.event('network.market_ws_lag',{'lag_ms':lag,'peak_ms':self.ws_lag_peak,'meaning':'行情消息落后服务端时间；盘口按新鲜度规则视为过期，不据此交易'})
                self.ws_lag_peak=0

    async def heartbeat(self,ws):
        while True: await asyncio.sleep(10); await ws.send('PING')

    def apply_book(self,item):
        now=self.api.clock.now() or epoch_ms(); kind=item.get('event_type')
        if item.get('market') and self.market and item['market']!=self.market.condition_id: return
        if kind=='book':
            book=self.books.get(item.get('asset_id'))
            if book and book.snapshot(item,now):
                self.quotes[book.asset_id].update(max(book.bids,default=None),min(book.asks,default=None),book.source_ms,now)
        elif kind=='price_change':
            changes=item['price_changes']; assets={c['asset_id'] for c in changes}
            for asset in assets:
                if asset in self.books:
                    b=self.books[asset];was_ready=b.initialized;b.delta(changes,int(item['timestamp']),now)
                    if was_ready and b.quality=='GAP':
                        self.book_resync.set();self.event('book.sequence_gap',{'asset_id':asset,'reason':'深度最优价与官方报价不一致，等待完整快照；官方报价独立显示'})
                    last=next(c for c in reversed(changes) if c['asset_id']==asset)
                    if 'best_bid' in last and 'best_ask' in last:self.quotes[asset].update(last['best_bid'],last['best_ask'],int(item['timestamp']),now)
        elif kind=='best_bid_ask' and item.get('asset_id') in self.quotes:
            self.quotes[item['asset_id']].update(item.get('best_bid'),item.get('best_ask'),int(item['timestamp']),now)
        elif kind=='market_resolved':
            if self.market: self.market.accepting=False
            self.event('market.resolved',item,market_id=self.market.id if self.market else None)
        elif kind=='tick_size_change' and self.market:
            tick=decimal(item['new_tick_size'])
            if tick is None or not D(0)<tick<D(1): raise DataError('价格梯度变化非法')
            self.market.tick=tick; self.preview_cache.clear()
            self.event('market.tick_changed',{'tick':tick})

    async def rest_books(self):
        """REST snapshots are only a fallback. The market WS sends full 'book' snapshots for each asset many times a
        minute, while a REST snapshot carries a timestamp slightly newer (median 29 ms, 2026-10-24) than the WS deltas
        that follow it; the book dropped those deltas, diverged, flagged a GAP and resynced again - a loop that peaked
        in the last minute. Replaying WS alone kept 100% of last-minute asset-seconds usable. So: no periodic REST,
        and REST only for an asset whose book has been unusable for 3 s without a WS snapshot restoring it."""
        down={}
        while not self.stop.is_set():
            try:await asyncio.wait_for(self.book_resync.wait(),.5)
            except asyncio.TimeoutError:pass
            self.book_resync.clear()
            if not self.market or self.paused:
                down.clear();continue
            market_id=self.market.id;now=time.monotonic()
            down={k:v for k,v in down.items() if k[0]==market_id}
            for asset,book in self.books.items():
                if book.initialized:down.pop((market_id,asset),None)
                else:down.setdefault((market_id,asset),now)
            assets=[a for (m,a),since in down.items() if m==market_id and a in self.books and now-since>=3]
            if not assets:continue
            for a in assets:down[(market_id,a)]=now  # at most one REST attempt per asset every 3 s
            try:
                results=await asyncio.gather(*(self.api.book(asset) for asset in assets))
                if not self.market or self.market.id!=market_id: continue
                for raw in results:
                    self.event('book.raw',{'event_type':'book',**raw},source='CLOB REST',market_id=market_id,condition_id=self.market.condition_id)
                    self.apply_book({'event_type':'book',**raw})
            except Exception as e:
                self.event('network.book_error',{'error':str(e)[:160]})
            await asyncio.sleep(1)

    async def geocheck(self):
        while not self.stop.is_set():
            if time.monotonic()-self.last_geo>=30:
                self.last_geo=time.monotonic()
                try:
                    raw=await self.api.get(GEO)
                    if type(raw.get('blocked')) is not bool: raise DataError('地区状态格式不明')
                    value={'blocked':raw['blocked'],'country':raw.get('country','UNKNOWN'),'region':raw.get('region',''),
                           'ip_hash':hashlib.sha256(raw.get('ip','').encode()).hexdigest()[:12],
                           'checked_at':utc_now(),'checked_ms':epoch_ms(),'status':'GEO_BLOCKED' if raw['blocked'] else 'OBSERVED',
                           'route':'应用本机；HTTP 使用系统/环境代理配置，未核实 WS 是否同出口'}
                except Exception as e:
                    value={'blocked':None,'country':'UNKNOWN','status':'GEO_UNKNOWN','checked_at':utc_now(),'checked_ms':epoch_ms(),'error':str(e)[:100]}
                if any(value.get(k)!=self.geo.get(k) for k in ('blocked','country','region','ip_hash')):
                    self.event('geo.status_changed',{'previous':self.geo,'current':value},source=GEO)
                self.geo=value
            await asyncio.sleep(1)

    async def account_loop(self):
        while not self.stop.is_set():
            active=self.settings.active_account()
            if active and time.monotonic()-self.last_account>=20:
                self.last_account=time.monotonic(); aid=active['id']
                try:
                    snapshot=await self.account_adapter.snapshot(active['address'])
                    current=self.settings.active_account()
                    if current and current['id']==aid:
                        self.account=snapshot; self.account_error=None
                        self.event('balance.snapshot',snapshot,account_id=aid,source='Data API v2')
                except Exception as e:
                    if self.settings.active_account() and self.settings.active_account()['id']==aid:
                        self.account_error=str(e)[:180]; self.event('account.sync_failed',{'error':self.account_error},account_id=aid)
            await asyncio.sleep(1)

    def merge_order(self,aid,row,source,persist=True):
        key=(aid,row['id']); status=row.get('status','UNKNOWN').upper()
        status={'LIVE':'open','MATCHED':'filled','CANCELED':'canceled','CANCELLED':'canceled','UNMATCHED':'open','DELAYED':'submitted'}.get(status,'unknown')
        if row.get('type')=='CANCELLATION': status='canceled'
        requested=decimal(row['original_size']); filled=decimal(row['size_matched'])
        # MATCHED is not proof of full size. Use cumulative quantities.
        if status=='filled' and filled<requested: status='partial' if filled else 'unknown'
        eid=hashlib.sha256((aid+dumps(redact(row),sort_keys=True)).encode()).hexdigest()
        projection=self.orders.setdefault(key,OrderProjection(aid,row['id']))
        update=OrderUpdate(eid,aid,row['id'],status,filled,requested,'user_ws' if source=='user_ws' else 'rest')
        if projection.apply(update):
            self.order_rows[key]={**redact(row),'state':projection.status,'size_matched':projection.cumulative_filled,
                                  'remaining':projection.unfilled_shares,'observed_at':utc_now()}
            if persist: self.event('account.order_update',row,account_id=aid,source=source,event_id=eid)

    async def authenticated_loop(self):
        while not self.stop.is_set():
            active=self.settings.active_account()
            enabled=active and self.settings.data.get('auth_read_enabled')
            if not enabled:
                self.auth_account=None; self.user_ws_status='未授权'
                self.account_auth=None
                if self.auth_task: self.auth_task.cancel(); await asyncio.gather(self.auth_task,return_exceptions=True); self.auth_task=None
            elif time.monotonic()-self.last_auth>=15:
                self.last_auth=time.monotonic(); aid=active['id']
                try:
                    if self.auth_account!=aid:
                        await self.auth.verify_identity(active['address'])
                    rows=await self.auth.orders(active['address'])
                    balance=await self.auth.balance(active['address'])
                    if self.settings.data['active_account_id']!=aid: continue
                    live_ids={r['id'] for r in rows}
                    for row in rows: self.merge_order(aid,row,'rest')
                    for (account_id,order_id),projection in list(self.orders.items()):
                        if account_id==aid and projection.live_order and order_id not in live_ids:
                            found=await self.auth.orders(active['address'],order_id)
                            if found:
                                for row in found: self.merge_order(aid,row,'rest')
                            else:
                                self.order_rows[(aid,order_id)]['state']='unknown'
                    if self.auth_account!=aid or self.auth_task is None or self.auth_task.done():
                        if self.auth_task: self.auth_task.cancel(); await asyncio.gather(self.auth_task,return_exceptions=True)
                        self.auth_account=aid; self.auth_task=asyncio.create_task(self.user_stream(active))
                    self.event('account.reconcile_done',{'open_count':len(rows)},account_id=aid)
                    self.account_auth={**balance,'account_id':aid,'orders_reconciled':not any(r.get('state')=='unknown' for (a,_),r in self.order_rows.items() if a==aid)}
                    if self.user_ws_connected and self.user_ws_status.startswith('同步失败'):self.user_ws_status='已连接（待事件/对账）'
                except Exception as e:
                    if self.settings.data.get('active_account_id')==aid:
                        self.account_auth={'account_id':aid,'quality':'ERROR','identity_verified':False,'orders_reconciled':False,'error':str(e)[:160]}
                    # account_auth (quality ERROR) already blocks live while REST sync fails. Only describe the user
                    # stream as failed when it is actually down; otherwise a later successful sync could not clear it.
                    if not self.user_ws_connected:self.user_ws_status='同步失败：'+str(e)[:80]
                    self.event('account.auth_sync_failed',{'error':str(e)[:120]},account_id=aid)
            await asyncio.sleep(1)

    async def user_stream(self,active):
        heartbeat=None
        try:
            c=self.auth.credentials()
            async with websockets.connect(USER_WS,open_timeout=10,ping_interval=20,ping_timeout=20) as ws:
                await ws.send(dumps({'type':'user','auth':{'apiKey':c['KEY'],'secret':c['SECRET'],'passphrase':c['PASSPHRASE']}}))
                self.user_ws_status='已连接（待事件/对账）'; self.user_ws_connected=True; heartbeat=asyncio.create_task(self.heartbeat(ws))
                async for raw in ws:
                    if raw=='PONG': continue
                    row=loads(raw)
                    if self.settings.data['active_account_id']!=active['id']: break
                    if row.get('event_type')=='order':
                        if row.get('maker_address','').lower()!=active['address'].lower(): raise DataError('User WS 资金账户不一致')
                        self.merge_order(active['id'],row,'user_ws'); self.user_ws_status='收到订单事件'
                    elif row.get('event_type')=='trade':
                        self.event('account.trade_observed',row,account_id=active['id'],source='user_ws')
        except asyncio.CancelledError: raise
        except Exception as e:
            self.user_ws_status='断流 / 待对账'; self.event('account.user_ws_gap',{'error':type(e).__name__},account_id=active['id'])
        finally:
            self.user_ws_connected=False
            if heartbeat: heartbeat.cancel(); await asyncio.gather(heartbeat,return_exceptions=True)
            self.last_auth=0

    async def handle(self,cmd):
        kind=cmd.get('action'); request=cmd.get('request_id')
        try:
            result=None
            if kind=='exit': self.stop.set()
            elif kind=='live_prepare':result=await self.live.prepare(cmd['release_ids'],cmd['allocation'],cmd['minimum'])
            elif kind=='live_arm':result=await self.live.arm(cmd['token'],cmd.get('attested'),cmd.get('phrase'))
            elif kind=='live_stop_entries':self.live.pause_entries();result=self.live.status
            elif kind=='live_halt':await self.live.halt();result=self.live.status
            elif kind=='live_probe':result=await self.live.probe()
            elif kind=='live_manual_buy':result=await self.live.manual_buy(cmd['outcome'],cmd['amount'])
            elif kind=='live_manual_sell':result=await self.live.manual_sell()
            elif kind=='reload':
                old=self.settings.data.get('active_account_id'); self.settings=Settings(self.root)
                self.account_auth=None;self.auth_account=None
                if self.auth_task:self.auth_task.cancel();await asyncio.gather(self.auth_task,return_exceptions=True);self.auth_task=None
                if old!=self.settings.data.get('active_account_id'):
                    await self.live.close()
                    self.account=None; self.account_error=None; self.auth_account=None
                    self.event('account.switched',{'old':old,'new':self.settings.data.get('active_account_id')})
                self.last_account=self.last_auth=0
            elif kind=='refresh': self.last_geo=self.last_account=self.last_auth=0
            elif kind=='model_catalog':
                self.model_catalog=await asyncio.to_thread(self.model_bridge.catalog)
                result={'catalog':self.model_catalog,'releases':self.model_bridge.releases(),'selected':self.model_bridge.selected()}
            elif kind in ('model_pin','model_rollback'):
                result=await asyncio.to_thread(self.model_bridge.pin,cmd['task'],cmd['model_id']) if kind=='model_pin' else await asyncio.to_thread(self.model_bridge.activate,cmd['release_id'])
                self.settings.data['model_shadow_enabled']=True;self.settings.save()
                self.shadow={'status':'WAITING','message':'已固定版本，等待新鲜特征','selected':result}
                self.event('model.shadow_version_selected',{'release_id':result['release_id'],'model_id':result['model_id'],'scope':'shadow_only'})
            elif kind=='late_vote_enable':
                # PAPER only: pins the final exam's top late models and starts the conservative vote strategy.
                from .model_bridge import preload_inference
                preload_inference()
                releases=await asyncio.to_thread(self.model_bridge.pin_vote,'late',int(cmd.get('k',5)))
                d=self.settings.data
                d.update(mode='paper',auto_paper_enabled=True,auto_paper_strategy='late_vote',vote_release_ids=[r['release_id'] for r in releases])
                d.setdefault('late_vote_since_ms',epoch_ms());self.settings.save()
                result={'releases':[{'release_id':r['release_id'],'model_id':r['model_id'],'exam_rank':r.get('exam_rank'),'score':(r.get('metrics') or {}).get('score')} for r in releases],
                        'since_ms':d['late_vote_since_ms'],'scope':'paper_only'}
                self.event('auto_paper.late_vote_enabled',result,mode='paper',account_id='paper-local')
            elif kind=='model_disable':
                self.settings.data['model_shadow_enabled']=False;self.settings.save();self.shadow={'status':'DISABLED','message':'影子预测已停止，版本保留'}
                self.event('model.shadow_disabled',{})
            elif kind=='pause':
                self.paused=not self.paused
                self.event('engine.monitor_paused',{'paused':self.paused})
            elif kind=='stop_risk':
                self.gateway.paused=not self.gateway.paused
                self.event('execution.paper_risk_paused',{'paused':self.gateway.paused},mode='paper')
            elif kind=='preview':
                if not self.market: raise DataError('尚无已验证市场')
                outcome=cmd['outcome']; book=self.books[self.market.outcomes[outcome]]
                now=self.api.clock.now()
                if not self.api.clock.valid() or not book.fresh(now) or not self.market.accepting: raise DataError('报价过期、时间未知或市场关闭')
                from .domain import preview_buy
                result=preview_buy(book,self.market,cmd['budget'],cmd['limit']) if cmd['side']=='BUY' else {'kind':'estimate_only','note':'确认后按新的可见买盘及限价模拟卖出当前方向持仓；深度不足会部分成交或拒绝'}
                pid='paper-'+uuid.uuid4().hex
                self.preview_cache={pid:{**cmd,'market_id':self.market.id,'created':time.monotonic()}}
                result={**result,'preview_id':pid,'market':self.market.slug,'side':cmd['side'],'outcome':outcome}
            elif kind=='paper_submit':
                if self.live.armed:raise DataError('实盘运行期间暂停手工纸面下单')
                if self.settings.data['mode']!='paper': raise DataError('先切换到纸面模拟模式')
                p=self.preview_cache.pop(cmd['preview_id'],None)
                if not p or time.monotonic()-p['created']>30: raise DataError('预览已过期，请重新预览')
                if not self.market or self.market.id!=p['market_id']: raise DataError('市场已换轮，请重新预览')
                market=self.market; asset=market.outcomes[p['outcome']]
                self.event('order.intent_created',{'intent_id':cmd['preview_id'],'mode':'paper','side':p['side'],'outcome':p['outcome']},mode='paper',market_id=market.id)
                await asyncio.sleep(.2)
                raw=await self.api.book(asset)
                if self.paused or self.market.id!=market.id or not self.api.clock.valid(): raise DataError('监听暂停、换轮或时钟失效，模拟未成交')
                book=Book(asset,market.condition_id); book.snapshot(raw,self.api.clock.now())
                self.event('book.raw',{'event_type':'book',**raw},market_id=market.id,source='paper preflight REST')
                result=self.gateway.execute_paper(market,book,p['outcome'],p['side'],p['budget'],p['limit'],self.api.clock.now(),cmd['preview_id'])
            elif kind=='logs': result=self.store.recent(200,cmd.get('query',''),cmd.get('mode'))
            elif kind=='export':
                dest=self.root.parent/'outputs'/('diagnostics-'+str(epoch_ms()))
                result=await asyncio.to_thread(self.store.export,dest); result['path']=str(dest)
                from .reporting import observation_report
                await asyncio.to_thread(observation_report,self.store.db_path,dest)
            elif kind=='replay':
                from .replay import replay_events
                await asyncio.to_thread(self.store.flush)
                result=await asyncio.to_thread(replay_events,self.store.raw_events())
                path=self.root.parent/'outputs'/('replay-'+str(epoch_ms())+'.json');atomic_json(path,result)
                result={'events':result['events'],'markets':result['markets'],'parse_errors':len(result['parse_errors']),'path':str(path),
                        'note':'离线重建已录制盘口；不是交易执行或盈利回测。'}
            elif kind=='ai':
                if self.ai_task and not self.ai_task.done(): raise DataError('副驾驶正在处理，可取消当前请求')
                async def work():
                    try:
                        answer=await self.copilot.generate(self.settings.data['ai'],self.state(),cmd.get('question','分析当前状态'),cmd.get('test',False))
                        self.emit({'type':'result','action':'ai','request_id':request,'result':answer})
                    except asyncio.CancelledError:
                        self.emit({'type':'error','action':'ai','message':'AI 请求已取消'})
                    except Exception as e: self.emit({'type':'error','action':'ai','message':str(e)[:200]})
                self.ai_task=asyncio.create_task(work()); return
            elif kind=='ai_cancel':
                if self.ai_task: self.ai_task.cancel()
            elif kind=='advice': result=self.copilot.save_advice(cmd['text'],self.state(),cmd.get('relpath'))
            else: raise DataError('未知命令')
            self.emit({'type':'result','action':kind,'request_id':request,'result':result})
        except Exception as e:
            message=(str(e)[:300] if type(e) in (DataError,ValueError,PermissionError) else '实盘操作未完成：'+type(e).__name__) if str(kind).startswith('live_') else str(e)[:300]
            self.event('engine.command_rejected',{'action':kind,'reason':message})
            self.emit({'type':'error','action':kind,'request_id':request,'message':message})

    async def command_loop(self):
        while not self.stop.is_set():
            cmd=await self.commands.get(); await self.handle(cmd)

    async def run(self,duration=None,stdin=False):
        loop=asyncio.get_running_loop()
        if stdin:
            def reader():
                try:
                    for line in sys.stdin:
                        try: cmd=loads(line)
                        except Exception: continue
                        loop.call_soon_threadsafe(self.commands.put_nowait,cmd)
                finally:
                    # Parent crash/pipe closure stops the child cleanly; CLI can run independently.
                    try: loop.call_soon_threadsafe(self.stop.set)
                    except RuntimeError: pass
            threading.Thread(target=reader,daemon=True).start()
        self.event('engine.started',{'version':__version__,'live_adapter_present':True,'live_armed':False,'offline':self.offline})
        coros=[self.publish(),self.command_loop(),self.automatic_paper_loop(),self.model_shadow_loop(),self.live.loop()]
        if not self.offline:
            from .reference import reference_loop
            coros += [self.sync(),self.geocheck(),self.rest_books(),self.account_loop(),self.authenticated_loop(),self.round_results_loop(),
                      reference_loop(self.root,self.reference,self.stop,self.api.clock,self.event),self.paper_settlement_loop()]
        self.task_group=[asyncio.create_task(c) for c in coros]
        try:
            if duration: await asyncio.wait_for(self.stop.wait(),duration)
            else: await self.stop.wait()
        except asyncio.TimeoutError: pass
        finally:
            self.stop.set()
            tasks=self.task_group+[t for t in (self.market_task,self.auth_task,self.ai_task) if t]
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            await self.live.close()
            self.event('engine.stopped',{'reason':'normal_shutdown'})
            await self.api.close(); await asyncio.to_thread(self.store.close)
            atomic_json(self.root/'cache'/'snapshot.json',{**self.state(),'worker_stopped':True})

def worker_main(root,duration=None,offline=False,ipc=False):
    from .locking import WorkerLock
    def emit(message):
        if ipc:
            try: print(dumps(message),flush=True)
            except (BrokenPipeError,OSError): pass
    with WorkerLock(root):
        engine=Engine(root,emit,offline)
        asyncio.run(engine.run(duration,stdin=ipc))
