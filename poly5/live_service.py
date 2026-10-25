"""User-armed BTC5m coordinator. Tests inject an exchange; research never sends."""
import asyncio
import copy
import time
import uuid
from decimal import ROUND_DOWN,ROUND_HALF_UP
from types import SimpleNamespace
from .common import D,decimal,epoch_ms
from .domain import DataError
from .adaptive_capital import initialize,evaluate,policy
from .adaptive_models import initial_pool,register_prediction,blend,hourly_weights
from .live_ledger import LiveLedger
from .live_exchange import LiveExchange,buy_plan,order_digest

# Entry distance of the 60 s TWAP from the opening baseline. 2026-10-24, user-approved twice: $50 -> $30, then
# ("改成出手更多的规则", plan B) no dollar minimum; spot and TWAP must still sit on the same side of the baseline and
# the model edge floor stays 0.03 (adaptive_capital). Out-of-sample exp20 (12.9 h live, exam models, $5 notional):
# $30/0.03 -> 6 trades +$5.78; no minimum/0.03 -> 23 trades (1.8/h), 20 wins, +$5.37, max drawdown $5.18. Not
# statistically confirmed; roughly one full-stake loss in eight trades.
ENTRY_DISTANCE_USD=D(0)

def exchange_text(value):
    """The exchange's own refusal text, made safe to keep: long hex/base64-like runs (keys, signatures, hashes,
    addresses) are elided and the text is truncated. Used only for order refusals, never for arbitrary exceptions."""
    import re
    text=str(value or '')[:400]
    text=re.sub(r'0x[0-9a-fA-F]{16,}','0x…',text)
    text=re.sub(r'[A-Za-z0-9+/=_-]{32,}','…',text)
    return text[:200]

class LiveService:
    def __init__(self,engine,exchange_factory=LiveExchange):
        self.e=engine;self.factory=exchange_factory;self.exchange=None;self.ledger=None;self.account=None
        self.armed=False;self.entries=False;self.prepared=None;self.lock=asyncio.Lock();self.runtimes={}
        self.status={'armed':False,'entries_enabled':False,'message':'实盘未启用','adapter_present':True}
        self.confirm=None;self.last_cash=0;self.last_results=0;self.last_attempt=0
    def commit(self,kind,payload,changes=None,key=None):
        if self.e.store.error:raise DataError('事实账本不可写，禁止实盘写入')
        return self.ledger.commit(kind,payload,key or uuid.uuid4().hex,changes or {})
    async def bind(self):
        active=self.e.settings.active_account()
        if not active:raise DataError('请先选择资金账户')
        if self.account!=active:
            await self.close();self.account=dict(active);self.ledger=LiveLedger(self.e.root,active)
            self.exchange=self.factory(self.e.root,active)
            self.status={'armed':False,'entries_enabled':False,'message':'实盘未启用','adapter_present':True}
        return active
    def region(self):
        """The user's own country/region code (two letters) where they are physically located and the platform allows
        trading. A local setting, never a code default: without it live trading cannot start."""
        code=str(self.e.settings.data.get('allowed_country') or '').strip().upper()
        if len(code)!=2 or not code.isalpha():raise DataError('请先在设置中填写你实际所在、且平台允许交易的国家/地区代码（两位字母）')
        return code
    def gates(self,require_arm=False):
        e=self.e;now=epoch_ms();geo=e.geo
        if e.offline or e.paused or e.stop.is_set():raise DataError('离线、暂停或正在退出')
        if e.store.error:raise DataError('事实账本不可写')
        if not e.api.clock.valid():raise DataError('服务端时间未同步')
        region=self.region()
        if geo.get('blocked') is not False or geo.get('country')!=region or not 0<=now-geo.get('checked_ms',0)<60000:
            raise DataError('等待新鲜官方地区检查，须与设置中的实际所在地一致')
        active=e.settings.active_account()
        if not active or self.account!=active:raise DataError('资金账户已改变，需重新启用')
        if require_arm and not self.armed:raise DataError('实盘未人工启用')
    async def prepare(self,release_ids,allocation,minimum):
        async with self.lock:
            await self.bind();self.gates()
            if self.armed:raise DataError('先停止实盘，再修改授权配置')
            market=self.e.market
            if not market:raise DataError('等待已验证市场')
            releases=[self.e.model_bridge.release(rid) for rid in release_ids]
            pool=initial_pool(releases);config=policy(allocation,minimum)
            existing=self.ledger.get('capital')
            if existing and existing.get('stop_latched'):raise DataError('累计回撤熔断已锁定；不能通过重启或重新启用重置本金')
            old_pool=self.ledger.get('pool')
            if old_pool and old_pool['release_ids']==pool['release_ids']:pool=old_pool
            cash=await self.exchange.cash(next(iter(market.outcomes.values())))
            if decimal(cash['available'])<=0:raise DataError('可核验可下单现金为零；请在官方账户核对资金和交易授权')
            self.prepared={'token':uuid.uuid4().hex,'at_ms':epoch_ms(),'cash':cash,'config':config,'pool':pool,
                'account_id':self.account['id'],'releases':releases}
            capital=existing or initialize(cash['available'],epoch_ms(),config)
            return {'token':self.prepared['token'],'cash':cash,'capital':capital,'config':config,
                'models':[{'model_id':r['model_id'],'release_id':r['release_id'],'task':r['task']} for r in releases],
                'expires_ms':self.prepared['at_ms']+60000,'existing_baseline_retained':bool(existing)}
    async def arm(self,token,attested,phrase):
        async with self.lock:
            p=self.prepared
            if not p or token!=p['token'] or epoch_ms()-p['at_ms']>60000:raise DataError('授权预览已过期，请重新核验')
            if attested is not True or phrase!='启用真钱交易':raise DataError('需要本人确认实际所在地、年龄及资格，并输入启用短语')
            self.gates();self.prepared=None
            for meta in p['releases']:
                if self.e.model_bridge.release(meta['release_id'])!=meta:raise DataError('预览后模型版本发生变化，请重新核验')
            # Refresh cash at the final click; never authorize unseen higher capital.
            cash=await self.exchange.cash(next(iter(self.e.market.outcomes.values())))
            if decimal(cash['available'])!=decimal(p['cash']['available']):raise DataError('现金变化，请重新预览实际本金')
            capital=self.ledger.get('capital') or initialize(cash['available'],self.e.api.clock.now(),p['config'])
            if capital['stop_latched']:raise DataError('累计回撤熔断已锁定')
            capital={**capital,'policy':p['config']}
            self.commit('user.armed',{'capital':capital,'pool':p['pool']['release_ids'],'eligibility':self.region()+'/adult/user-attested'},
                {'capital':capital,'pool':p['pool'],'cash':cash})
            self.armed=True;self.entries=True;self.exchange.armed=True
            self.status.update(armed=True,entries_enabled=True,message='实盘已启用，等待完整核验与信号',cash=cash)
            return self.status
    def pause_entries(self):
        self.entries=False;self.status.update(entries_enabled=False,message='已停止新增实盘交易，继续管理本程序持仓')
        if self.ledger:self.commit('user.entries_stopped',{})
    async def halt(self):
        self.armed=False;self.entries=False
        if self.exchange:self.exchange.armed=False
        self.status.update(armed=False,entries_enabled=False,message='实盘完全停机；已有持仓需本人在官方账户处理')
        if self.ledger:self.commit('user.halted',{})
    def capital_view(self,now):
        capital=self.ledger.get('capital');position=self.ledger.get('position');realized=decimal(self.ledger.get('realized','0'))
        pnl=realized;verified=not bool(self.ledger.get('pending'))
        if position and decimal(position['shares'])>0:
            market=self.e.market;book=self.e.books.get(position['asset_id'])
            if not market or market.id!=position['market_id'] or not book or not book.sellable(now) or market.fee is None:verified=False
            else:
                left=decimal(position['shares']);net=D(0)
                for price,size in sorted(book.bids.items(),reverse=True):
                    q=min(left,size);net+=q*price-market.fee.cost(price,q);left-=q
                    if left==0:break
                if left:verified=False
                else:pnl+=net-decimal(position['basis'])
        updated,view=evaluate(capital,pnl,now,verified=verified)
        if updated!=capital:
            changes={'capital':updated};adjustment=None
            pool=self.ledger.get('pool')
            if view['checkpoint'] and pool:
                adjustment=hourly_weights(pool)
                if adjustment:
                    pool={**pool,'weights':adjustment['weights'],'last_weight_samples':adjustment['common_markets']};changes['pool']=pool
            self.commit('risk.hourly_checkpoint' if view['checkpoint'] else 'risk.stop_triggered',
                {'risk':view,'model_adjustment':adjustment},changes)
        view['realized_pnl']=str(realized);view['position']=position;view['pool']=self.ledger.get('pool')
        self.status['risk']=view
        return view
    async def predict(self,market,now):
        from .model_bridge import ShadowRuntime,reference_features
        pool=self.ledger.get('pool');outputs=[];base=reference_features(self.e.reference,market,now)
        for rid in pool['release_ids']:
            meta=self.e.model_bridge.release(rid);f=dict(base)
            if meta['task'].endswith('_mkt'):
                for outcome,key in [('Up','up_avg_price'),('Down','down_avg_price')]:
                    book=self.e.books.get(market.outcomes[outcome])
                    if not book or not book.fresh(now):raise DataError('模型等待新鲜双侧盘口')
                    from .domain import preview_buy_notional
                    q=preview_buy_notional(book,market,'5',str(D(1)-market.tick));f[key]=float(q['total']/q['shares'])
                up=self.e.books[market.outcomes['Up']];f.update(mkt_bid=float(max(up.bids)),mkt_ask=float(min(up.asks)))
            if rid not in self.runtimes:
                bridge=SimpleNamespace(root=self.e.model_bridge.root,selected=lambda rid=rid:self.e.model_bridge.release(rid))
                self.runtimes[rid]=ShadowRuntime(bridge)
            outputs.append(await asyncio.to_thread(self.runtimes[rid].predict,f,now,market))
        sample=register_prediction(pool,market.condition_id,outputs,now,market.end)
        if sample:
            pool=copy.deepcopy(pool);pool['predictions'][market.condition_id]=sample
            self.commit('model.forward_prediction',{'condition_id':market.condition_id,**sample},{'pool':pool})
        probability,values=blend(pool,outputs)
        self.status['prediction']={'at_ms':now,'market_id':market.id,'probability_up':str(probability),
            'models':{rid:{'probability_up':str(p),'weight':pool['weights'][rid]} for rid,p in values.items()}}
        return probability
    async def results(self,now):
        from .api import CLOB
        pool=self.ledger.get('pool')
        if not pool:return
        for cid,pred in pool['predictions'].items():
            if cid in pool['results'] or now<(pred['end_s']+5)*1000:continue
            raw=await self.e.api.get(CLOB+'/markets/'+cid)
            tokens=raw.get('tokens',[])
            if raw.get('condition_id')!=cid or raw.get('closed') is not True or len(tokens)!=2 or {t.get('outcome') for t in tokens}!={'Up','Down'} or len({str(t.get('token_id')) for t in tokens})!=2:continue
            if any(type(t.get('winner')) is not bool for t in tokens) or sum(t['winner'] for t in tokens)!=1:continue
            up=next((t for t in tokens if t.get('outcome')=='Up'),None)
            if up is None:continue
            pool=copy.deepcopy(pool);pool['results'][cid]={'up':int(up['winner']),'observed_ms':now}
            self.commit('model.forward_result',{'condition_id':cid,**pool['results'][cid]},{'pool':pool})
    async def reconcile(self):
        pending=self.ledger.get('pending')
        if not pending:return
        try:order=await self.exchange.lookup(pending['order_id'])
        except Exception as exc:
            if type(exc).__name__ not in ('UnexpectedResponseError','RequestRejectedError'):raise
            # 2026-10-24: get_order failed for a filled market order while the account trade feed showed the fill
            # (taker order id, CONFIRMED, tx hash). Reconcile from those receipts; with none, use the closed-market rule.
            trades=await self.exchange.trades(pending['condition_id'])
            mine=[t for t in trades if self.names_order(t,pending['order_id'])]
            if mine:
                if any(t.status!='CONFIRMED' or not t.transaction_hash for t in mine):raise DataError('成交仍未全部链上确认，暂停新增风险')
                self.apply_fills(pending,mine,None,'account trades (order lookup unavailable)');return
            await self.close_never_accepted(pending,type(exc).__name__);return
        if str(order.asset_id)!=pending['asset_id'] or order.condition_id!=pending['condition_id'] or order.side!=pending['side'] or order.maker_address.lower()!=self.account['address'].lower():
            raise DataError('订单身份对账失败，实盘暂停新增风险')
        ids=set(order.associate_trades)|set(pending.get('trade_ids',[]))
        if order.size_matched==0 and str(order.status).upper() in ('CANCELED','CANCELLED','REJECTED','EXPIRED','UNMATCHED'):
            self.commit('order.unfilled',{'order_id':order.id,'status':order.status},{'pending':None});return
        if str(order.status).upper() in ('LIVE','DELAYED'):
            # An unexpected resting FOK cannot be allowed to acquire later risk.
            await self.exchange.cancel_owned(order.id)
            raise DataError('订单仍挂起，已申请撤销，等待最终对账')
        if not ids:raise DataError('等待官方成交回执；不将订单接受视为收益')
        trades=await self.exchange.trades(pending['condition_id']);found={t.id:t for t in trades if t.id in ids}
        if set(found)!=ids or any(t.status!='CONFIRMED' or not t.transaction_hash for t in found.values()):
            raise DataError('成交仍未全部链上确认，暂停新增风险')
        self.apply_fills(pending,list(found.values()),order.size_matched,'order + account trades')

    @staticmethod
    def names_order(trade,order_id):
        oid=str(order_id).lower()
        ids=[getattr(trade,'taker_order_id','') or '']+[getattr(m,'order_id','') or '' for m in (getattr(trade,'maker_orders',None) or ())]
        return oid in (str(i).lower() for i in ids)

    def apply_fills(self,pending,trades,size_matched,source):
        """Book confirmed fills. A whole-cent market BUY may receive more shares than planned when the price is
        better, so BUYs are bounded by spend (<= max_cost), SELLs by shares held. Fees use each trade's own reported
        rate (the 14:29Z fill reported 0 bps while the market listed 700)."""
        from .domain import Fee
        quantity=cost=fees=D(0)
        for t in trades:
            if str(t.taker_order_id).lower()!=str(pending['order_id']).lower() or t.trader_side!='TAKER' or t.side!=pending['side'] or str(t.asset_id)!=pending['asset_id'] or t.condition_id!=pending['condition_id']:
                raise DataError('成交回执身份不一致')
            price=decimal(str(t.price));size=decimal(str(t.size))
            if not D(0)<price<D(1) or size<=0:raise DataError('成交金额非法')
            if (t.side=='BUY' and price>decimal(pending['price'])) or (t.side=='SELL' and price<decimal(pending['price'])):raise DataError('成交超出保护限价')
            fee=Fee(decimal(str(t.fee_rate_bps))/10000,pending['fee_exponent'])
            quantity+=size;cost+=size*price;fees+=fee.cost(price,size).quantize(D('.00001'),rounding=ROUND_HALF_UP)
        if size_matched is not None and quantity!=decimal(str(size_matched)):raise DataError('累计成交份额尚未勾稽一致')
        pos=self.ledger.get('position');realized=decimal(self.ledger.get('realized','0'))
        if pending['side']=='BUY':
            if cost+fees>decimal(pending['max_cost']):raise DataError('实际支出超过预留上限，暂停核验')
            if not pending.get('amount') and quantity>decimal(pending['shares']):raise DataError('累计成交份额超过下单份额')
            if pos and decimal(pos['shares'])>0:
                if pos['asset_id']!=pending['asset_id']:raise DataError('存在其他方向的程序仓位，不能叠加')
                pos={**pos,'shares':str(decimal(pos['shares'])+quantity),'basis':str(decimal(pos['basis'])+cost+fees)}
            else:
                pos={k:pending.get(k) for k in ('asset_id','condition_id','market_id','end_s','outcome','baseline_point')}
                pos.update(shares=str(quantity),basis=str(cost+fees))
        else:
            if not pos or quantity>decimal(pos['shares']):raise DataError('卖出数量超出程序持仓')
            released=decimal(pos['basis'])*quantity/decimal(pos['shares']);realized+=cost-fees-released
            pos={**pos,'shares':str(decimal(pos['shares'])-quantity),'basis':str(decimal(pos['basis'])-released)}
            if decimal(pos['shares'])==0:pos=None
        receipt={'order_id':pending['order_id'],'side':pending['side'],'shares':str(quantity),'contract_value':str(cost),
            'fee':str(fees),'fee_source':'confirmed fill price/size + each trade fee_rate_bps, rounded 5dp','source':source,
            'trade_ids':sorted(str(t.id) for t in trades),'transaction_hashes':[t.transaction_hash for t in trades],'realized_pnl':str(realized)}
        self.commit('order.confirmed',receipt,{'position':pos,'realized':str(realized),'pending':None},'fill-'+str(pending['order_id']))
        self.status['last_receipt']=receipt;self.last_cash=0

    async def settle_expired(self,pos):
        """After the market closes: a losing outcome is booked as a realized loss at once; a winning one waits until
        the user redeems in the official account (no auto-redeem here), then the payout is booked."""
        from .api import CLOB
        raw=await self.e.api.get(CLOB+'/markets/'+pos['condition_id'])
        tokens=raw.get('tokens',[]) if isinstance(raw,dict) else []
        if raw.get('condition_id')!=pos['condition_id'] or raw.get('closed') is not True or len(tokens)!=2 or any(type(t.get('winner')) is not bool for t in tokens):
            raise DataError('持仓所在轮次等待官方结算结果')
        mine=next((t for t in tokens if str(t.get('token_id'))==str(pos['asset_id'])),None)
        if mine is None:raise DataError('官方结果与持仓方向不匹配，需人工核对')
        shares=decimal(pos['shares']);basis=decimal(pos['basis']);realized=decimal(self.ledger.get('realized','0'))
        if not mine['winner']:
            self.commit('position.settled_loss',{'market_id':pos['market_id'],'outcome':pos['outcome'],'shares':str(shares),'basis':str(basis),'payout':'0'},
                        {'position':None,'realized':str(realized-basis)})
            return
        if await self.exchange.token_balance(pos['asset_id'])>0:
            raise DataError('上一轮持仓获胜：请在 Polymarket 官方账户点 Redeem 领取，领取后本程序自动入账')
        self.commit('position.redeemed',{'market_id':pos['market_id'],'outcome':pos['outcome'],'shares':str(shares),'basis':str(basis),'payout':str(shares)},
                    {'position':None,'realized':str(realized+shares-basis)})

    def manual_mode(self):
        return self.e.settings.data.get('ui_mode','quick')=='quick'

    async def manual_buy(self,outcome,amount):
        """One-tap BUY clicked by the user in the quick-trade page: whole-cent spend, FOK, protected at best ask
        plus one tick. Requires live to be armed by the user; same signer, audit and reconciliation as every order."""
        async with self.lock:
            self.gates(True);e=self.e;market=e.market;now=e.api.clock.now()
            if self.ledger.get('pending'):raise DataError('上一笔订单仍在核对，请稍候')
            if not market or not market.accepting or market.fee is None or now>=market.end*1000-3000:raise DataError('本轮已封盘或即将封盘')
            if outcome not in market.outcomes:raise DataError('未知方向')
            budget=decimal(str(amount))
            if budget is None or not D(1)<=budget<=D(10000):raise DataError('金额需在 $1–$10,000 之间')
            book=e.books.get(market.outcomes[outcome])
            if not book or not book.asks or not book.fresh(now):raise DataError('该方向暂无新鲜卖单，稍后再试')
            pos=self.ledger.get('position')
            if pos and decimal(pos['shares'])>0 and (pos['market_id']!=market.id or pos['asset_id']!=book.asset_id):
                raise DataError('请先卖出或等待现有持仓结算，再买另一方向/另一轮')
            cash=await self.exchange.cash(book.asset_id)
            if decimal(cash['available'])<budget:raise DataError('可用现金不足')
            plan=buy_plan(book,market,str(budget),'1')
            await self.submit(market,book,outcome,'BUY',plan['price'],plan['shares'],plan['max_cost'],{},plan['amount'],manual=True)
            return {'submitted':True,'outcome':outcome,'spend':str(plan['amount']),'max_price':str(plan['price'])}

    async def manual_sell(self):
        """Cash out: sell the whole program position at the best bid, protected one tick below it, FOK."""
        async with self.lock:
            self.gates(True);e=self.e;market=e.market;now=e.api.clock.now()
            if self.ledger.get('pending'):raise DataError('上一笔订单仍在核对，请稍候')
            pos=self.ledger.get('position')
            if not pos or decimal(pos['shares'])<=0:raise DataError('当前没有可卖持仓')
            if not market or pos['market_id']!=market.id or now>=market.end*1000:raise DataError('持仓所在轮次已结束，等待官方结算')
            book=e.books.get(pos['asset_id'])
            if not book or not book.bids or not book.sellable(now):raise DataError('暂无新鲜买单，稍后再试')
            shares=decimal(pos['shares']).quantize(D('.01'),rounding=ROUND_DOWN)
            if shares<market.min_size:raise DataError('持仓低于交易所最小卖出量，请在官方账户处理')
            price=max(market.tick,max(book.bids)-market.tick)
            await self.submit(market,book,pos['outcome'],'SELL',price,shares,D(0),{},manual=True)
            return {'submitted':True,'shares':str(shares),'min_price':str(price)}
    async def close_never_accepted(self,pending,lookup_error):
        """The exchange has no such order. Only after its market has closed, with no account trade naming the order
        and no tokens of that outcome held, is the UNKNOWN intent closed; otherwise keep waiting and never resend."""
        now=self.e.api.clock.now()
        if now is None or now<(int(pending['end_s'])+60)*1000:raise DataError('交易所查不到该订单；等待该轮封盘后再核验，绝不重发')
        trades=await self.exchange.trades(pending['condition_id'])
        oid=pending['order_id'].lower()
        def names_order(t):
            ids=[getattr(t,'taker_order_id','') or '']+[getattr(m,'order_id','') or '' for m in (getattr(t,'maker_orders',None) or ())]
            return oid in (i.lower() for i in ids)
        if any(names_order(t) for t in trades):
            raise DataError('发现与该订单相关的成交记录，需人工核对，暂停新增风险')
        if await self.exchange.token_balance(pending['asset_id'])!=0:raise DataError('账户持有该方向份额，需人工核对，暂停新增风险')
        self.commit('order.never_accepted',{'order_id':pending['order_id'],'lookup_error':lookup_error,'account_trades_checked':len(trades),
            'token_balance':'0','rule':'封盘后：交易所无此订单、无相关成交、无该方向份额 → 关闭未知意图'},{'pending':None})
        self.status['message']='上一笔订单经核验从未被交易所接受，已解除锁定'

    async def submit(self,market,book,outcome,side,price,shares,max_cost,reference,amount=None,manual=False):
        self.gates(True)
        if self.ledger.get('pending'):raise DataError('已有待对账订单，禁止重复提交')
        now=self.e.api.clock.now()
        if not book.sellable(now) or side=='BUY' and not book.fresh(now):raise DataError('提交前盘口过期')
        if not market.accepting or now>=market.end*1000 or self.e.market.id!=market.id:raise DataError('当前市场已封盘或换轮')
        if side=='BUY' and not manual and not self.entries:raise DataError('新增风险已停止')
        signed=await self.exchange.sign(book.asset_id,side,price,shares,amount)
        # Verify encoded amounts, not only the requested rounded values. A market BUY spends exactly `amount` (whole
        # cents) for a floored share count; its encoded price may exceed the cap only fractionally (< 0.0001).
        offered=D(signed.maker_amount)/1000000;requested=D(signed.taker_amount)/1000000
        if side=='BUY' and (offered!=amount or (offered*100)%1 or offered>max_cost or requested<market.min_size or requested>shares or offered/requested>=price+D('.0001')):
            raise DataError('签名金额精度不符合保护限额')
        if side=='BUY':shares=requested
        if side=='SELL' and (offered!=shares or requested/offered<price):raise DataError('卖出签名数量或价格不符')
        self.gates(True);now=self.e.api.clock.now()
        cutoff=market.end*1000-(3000 if manual else 15000)
        if not book.sellable(now) or side=='BUY' and ((not manual and not self.entries) or not book.fresh(now) or now>=cutoff):raise DataError('签名后行情或权限已变化')
        if price%market.tick:raise DataError('签名后价格梯度已变化')
        if side=='BUY' and not manual:
            pred=self.status.get('prediction',{})
            if pred.get('market_id')!=market.id or not 0<=now-pred.get('at_ms',0)<5000:raise DataError('签名后的模型预测已过期，未发送订单')
            fresh_ref=self.e.reference.features(market,now)
            direction=1 if outcome=='Up' else -1
            if abs(fresh_ref['twap_distance'])<ENTRY_DISTANCE_USD or direction*fresh_ref['spot_distance']<=0 or direction*fresh_ref['twap_distance']<=0:raise DataError('签名后基准方向已改变')
        digest=order_digest(signed)
        pending={'order_id':digest,'asset_id':book.asset_id,'condition_id':market.condition_id,'market_id':market.id,
            'end_s':market.end,'outcome':outcome,'side':side,'price':str(price),'shares':str(shares),'max_cost':str(max_cost),
            'fee_rate':str(market.fee.rate),'fee_exponent':market.fee.exponent,'baseline_point':reference.get('baseline_point'),
            'state':'UNKNOWN','created_ms':now,'trade_ids':[],'amount':str(amount) if amount is not None else None,'manual':manual}
        # Durable UNKNOWN before POST. Crash, timeout, or cancellation never resubmits.
        self.commit('order.intent',pending,{'pending':pending})
        try:response=await self.exchange.send_once(signed)
        except Exception as exc:
            status=getattr(exc,'status',None)
            if type(exc).__name__ in ('RequestRejectedError',) and isinstance(status,int) and 400<=status<500:
                # A 4xx answer is the exchange refusing the order: nothing was created, so the intent is closed.
                # Only machine codes are recorded; SDK message text may contain request details and is never logged.
                restriction=getattr(exc,'restriction',None)
                self.commit('order.rejected',{'order_id':digest,'status':status,'code':getattr(exc,'code',None),
                    'restriction':str(restriction) if restriction is not None else None,'message':exchange_text(exc)},{'pending':None})
                raise DataError(f'交易所拒绝订单（HTTP {status}，代码 {getattr(exc,"code",None)}）；未成交，未重发') from None
            self.commit('order.transport_unknown',{'order_id':digest,'error_type':type(exc).__name__,'status':status if isinstance(status,int) else None})
            raise DataError('送单结果未知；已锁定意图，等待订单对账，绝不重发') from None
        if not response.ok:
            self.commit('order.rejected',{'order_id':digest,'code':response.code,'message':exchange_text(getattr(response,'message',''))},{'pending':None})
            raise DataError('交易所拒绝订单：'+str(response.code))
        if response.order_id.lower()!=digest.lower():raise DataError('交易所订单编号与签名摘要不一致；暂停核验')
        pending={**pending,'state':str(response.status),'trade_ids':list(response.trade_ids)}
        self.commit('order.accepted',{'order_id':digest,'status':response.status,'trade_ids':pending['trade_ids']},{'pending':pending})
        self.status['message']='订单已接受，等待成交与链上确认'
    async def step(self):
        if not self.armed:return
        async with self.lock:
            self.gates(True);e=self.e;now=e.api.clock.now();market=e.market
            if self.ledger.get('pending'):
                await self.reconcile();self.status['pending']=self.ledger.get('pending');return
            self.status['pending']=None
            if self.manual_mode():
                pos=self.ledger.get('position')
                if pos and (not market or pos['market_id']!=market.id or now>=int(pos['end_s'])*1000):await self.settle_expired(pos)
                self.status['message']='手动交易模式：自动策略已停用；你点击的订单照常核对与结算'
                return
            if not market or not market.fee or not market.twap_60_verified:raise DataError('等待已验证结算和手续费规则')
            risk=self.capital_view(now)
            if risk.get('stop_latched'):self.entries=False;self.status['entries_enabled']=False
            if time.monotonic()-self.last_results>30:
                self.last_results=time.monotonic();await self.results(now)
            pos=self.ledger.get('position')
            if pos:
                if pos['market_id']!=market.id or now>=pos['end_s']*1000:
                    await self.settle_expired(pos);return
                book=e.books.get(pos['asset_id'])
                if not book or not book.sellable(now):raise DataError('持仓中，等待可卖盘口')
                f=e.reference.features(market,now,pos.get('baseline_point'))
                from .auto_paper import reversal
                shares=decimal(pos['shares']);left=shares;net=D(0)
                for p,q in sorted(book.bids.items(),reverse=True):
                    q=min(q,left);net+=q*p-market.fee.cost(p,q);left-=q
                    if not left:break
                roi=(net-decimal(pos['basis']))/decimal(pos['basis']) if not left else None
                reason='profit' if roi is not None and roi>=D('.005') else 'sudden_reversal' if reversal(f,pos['outcome'])['trigger'] else 'drawdown_stop' if risk.get('stop_latched') else None
                self.status['message']='持仓中，按净盈利与基准反向风险管理';self.status['liquidation_roi']=str(roi) if roi is not None else None
                if reason and time.monotonic()-self.last_attempt>=5:
                    self.last_attempt=time.monotonic()
                    if await self.exchange.token_balance(book.asset_id)<shares:raise DataError('资金钱包可卖份额低于程序持仓，暂停核验')
                    # Limited one-tick slippage, with full-size FOK; partial loss is not hidden.
                    price=max(market.tick,max(book.bids)-market.tick);size=shares.quantize(D('.01'),rounding=ROUND_DOWN)
                    if size<market.min_size:raise DataError('剩余程序份额低于最小卖出量，需官方账户处理')
                    if reason=='profit' and size*(price-market.fee.cost(price,D(1)))<decimal(pos['basis'])*(size/shares)*D('1.005'):
                        raise DataError('保护限价下净盈利不足，继续持有')
                    await self.submit(market,book,pos['outcome'],'SELL',price,size,D(0),f)
                return
            if not self.entries:raise DataError('已停止新增实盘交易')
            auth=e.account_auth or {}
            if auth.get('account_id')!=self.account['id'] or auth.get('identity_verified') is not True or auth.get('orders_reconciled') is not True or auth.get('quality')!='OK' or not 0<=epoch_ms()-auth.get('observed_ms',0)<30000:
                raise DataError('等待账户身份、订单与余额的新鲜完整对账')
            if not e.user_ws_status.startswith(('已连接','收到')):raise DataError('用户订单连接断流，暂停新增风险')
            if not risk['new_risk_allowed']:raise DataError(risk['reason'])
            if not market.start*1000+240000<=now<market.end*1000-15000:raise DataError('末分钟模式等待入场时间')
            f=e.reference.features(market,now)
            if abs(f['twap_distance'])<ENTRY_DISTANCE_USD:raise DataError(f'末分钟 TWAP 离基准不足 ${ENTRY_DISTANCE_USD}')
            direction=1 if f['spot_distance']>0 and f['twap_distance']>0 else -1 if f['spot_distance']<0 and f['twap_distance']<0 else 0
            if not direction:raise DataError('现价与 TWAP 不在基准同侧')
            probability=await self.predict(market,now);outcome='Up' if direction==1 else 'Down';fair=probability if direction==1 else 1-probability
            if fair<decimal(risk['min_probability']):raise DataError('模型池对当前方向信心不足')
            book=e.books.get(market.outcomes[outcome])
            if not book or not book.fresh(e.api.clock.now()):raise DataError('等待可成交深度')
            # Require three distinct seconds; do not count a tight loop as confirmation.
            scan=now//1000
            if not self.confirm or self.confirm[0:2]!=(market.id,outcome) or scan-self.confirm[2]>2:self.confirm=(market.id,outcome,scan,1)
            elif scan>self.confirm[2]:self.confirm=(market.id,outcome,scan,self.confirm[3]+1)
            if self.confirm[3]<3:raise DataError('趋势与模型确认中')
            if time.monotonic()-self.last_attempt<10:raise DataError('等待本轮订单尝试冷却')
            self.last_attempt=time.monotonic();cash=await self.exchange.cash(book.asset_id)
            self.status['cash']=cash;self.commit('cash.verified',cash,{'cash':cash})
            try:await self.final_checks_and_submit(market,book,outcome,direction,risk,e)
            except DataError as exc:
                # Every stop after cash verification is audited with this program's own reason text.
                self.commit('entry.skipped',{'market_id':market.id,'outcome':outcome,'reason':str(exc)[:200]})
                raise
    async def final_checks_and_submit(self,market,book,outcome,direction,risk,e):
            fresh_market=await asyncio.wait_for(e.api.sdk.get_market(slug=market.slug),12)
            schedule=fresh_market.trading.fee_schedule
            if fresh_market.condition_id!=market.condition_id or schedule is None or schedule.rate!=market.fee.rate or schedule.exponent!=market.fee.exponent:
                raise DataError('送单前市场身份或手续费档变化，停止新增风险')
            if await self.exchange.token_balance(book.asset_id)!=0:raise DataError('该方向已有账户仓位，避免与手工持仓混合')
            cash=self.status['cash']
            if decimal(cash['available'])<decimal(risk['all_in_budget']):raise DataError('当前可用现金低于策略单笔预算，不使用充值扩大授权')
            now=e.api.clock.now()
            if not book.fresh(now):raise DataError('余额核验后盘口过期，等待下一次新鲜报价')
            plan=buy_plan(book,market,risk['all_in_budget'],risk['minimum_notional'])
            probability=await self.predict(market,e.api.clock.now());fair=probability if direction==1 else 1-probability
            if fair<decimal(risk['min_probability']):raise DataError('送单前模型信心已改变')
            if fair-plan['expected_cost_per_share']<decimal(risk['min_edge_per_share']):raise DataError('含费价格未留下模型优势')
            f=e.reference.features(market,now)
            if abs(f['twap_distance'])<ENTRY_DISTANCE_USD or direction*f['spot_distance']<=0 or direction*f['twap_distance']<=0:raise DataError('送单前基准方向已变化')
            await self.submit(market,book,outcome,'BUY',plan['price'],plan['shares'],plan['max_cost'],f,plan['amount'])
    async def probe(self):
        """User-clicked connectivity test: same signer, credentials and audit path as a real order, but a GTC BUY of
        the exchange minimum at $0.01 on a side asking >= $0.05, so it cannot fill (worst case $0.05), then cancelled at
        once. Distinguishes "every order is refused" from "an FOK simply could not fill"."""
        async with self.lock:
            self.gates(True);e=self.e;now=e.api.clock.now();market=e.market
            if self.ledger.get('pending'):raise DataError('已有待对账订单，稍后再测试')
            if self.ledger.get('position'):raise DataError('持有程序仓位时不做测试')
            if not market or not market.accepting or market.fee is None or now>=market.end*1000-20000:raise DataError('等待市场开放（封盘前 20 秒内不测试）')
            choice=next(((o,e.books[a]) for o,a in market.outcomes.items() if a in e.books and e.books[a].initialized and e.books[a].asks and min(e.books[a].asks)>=D('.05')),None)
            if not choice:raise DataError('两侧卖价都低于 $0.05，换下一轮再测试')
            outcome,book=choice;price=D('.01');shares=market.min_size
            signed=await self.exchange.sign_resting(book.asset_id,'BUY',price,shares)
            offered=D(signed.maker_amount)/1000000;requested=D(signed.taker_amount)/1000000
            if requested!=shares or offered>shares*price+D('.0001'):raise DataError('测试单签名金额不符')
            digest=order_digest(signed)
            pending={'order_id':digest,'asset_id':book.asset_id,'condition_id':market.condition_id,'market_id':market.id,
                'end_s':market.end,'outcome':outcome,'side':'BUY','price':str(price),'shares':str(shares),'max_cost':str(offered),
                'fee_rate':str(market.fee.rate),'fee_exponent':market.fee.exponent,'baseline_point':None,'state':'UNKNOWN',
                'created_ms':now,'trade_ids':[],'probe':True}
            self.commit('probe.intent',pending,{'pending':pending})
            try:response=await self.exchange.send_once(signed)
            except Exception as exc:
                status=getattr(exc,'status',None)
                if type(exc).__name__=='RequestRejectedError' and isinstance(status,int) and 400<=status<500:
                    result={'accepted':False,'status':status,'code':getattr(exc,'code',None),'message':exchange_text(exc)}
                    self.commit('probe.rejected',result,{'pending':None});return result
                self.commit('probe.transport_unknown',{'order_id':digest,'error_type':type(exc).__name__})
                raise DataError('测试单送达结果未知；已锁定，将按订单对账处理（若挂单存在会自动撤销），绝不重发') from None
            if not response.ok:
                result={'accepted':False,'code':response.code,'message':exchange_text(getattr(response,'message',''))}
                self.commit('probe.rejected',result,{'pending':None});return result
            self.commit('probe.accepted',{'order_id':response.order_id,'status':str(response.status)})
            try:await self.exchange.cancel_owned(response.order_id)
            except Exception as exc:
                self.commit('probe.cancel_unknown',{'order_id':response.order_id,'error_type':type(exc).__name__})
                return {'accepted':True,'cancelled':False,'message':'交易所已接受测试单；撤单结果未知，订单对账会继续撤销'}
            # The exchange's order view lags the cancel by a moment (2026-10-24: still "live" right after cancelling,
            # CANCELED one second later), so re-check briefly before reporting.
            for attempt in range(6):
                order=await self.exchange.lookup(response.order_id)
                matched=D(str(order.size_matched))
                if matched!=0 or str(order.status).upper() in ('CANCELED','CANCELLED'):break
                await asyncio.sleep(.6)
            if matched==0 and str(order.status).upper() in ('CANCELED','CANCELLED'):
                self.commit('probe.cancelled',{'order_id':response.order_id,'status':str(order.status)},{'pending':None})
                return {'accepted':True,'cancelled':True,'message':'交易所接受了测试单并已撤销：签名、凭据与账户权限正常'}
            return {'accepted':True,'cancelled':False,'matched':str(matched),'message':'测试单状态需对账：'+str(order.status)}

    def prior_attestation(self):
        """Seq/time of the user's last completed manual enable (money preview + location/age attestation + phrase)."""
        row=self.ledger.db.execute("SELECT seq,at_ms FROM audit WHERE kind='user.armed' ORDER BY seq DESC LIMIT 1").fetchone()
        return {'seq':row[0],'at_ms':row[1]} if row else None

    async def auto_arm_manual(self):
        """Quick (manual) mode, user-requested 2026-10-24: re-enable on launch without repeating the confirmation,
        only after the user has completed it at least once. No order is ever sent without a user click in this mode
        (the automatic strategy is off); every live gate (fresh official geoblock for the configured region, account binding) still applies."""
        async with self.lock:
            if self.armed or not self.manual_mode() or self.e.settings.data.get('manual_auto_arm',True) is False:return
            await self.bind();self.gates()
            prior=self.prior_attestation()
            if not prior:raise DataError('首次使用需在本机完成一次真钱交易确认（本金预览、资格勾选与启用短语）')
            capital=self.ledger.get('capital')
            if capital and capital.get('stop_latched'):raise DataError('累计回撤熔断已锁定；需人工处理')
            self.commit('user.auto_armed_manual',{'prior_attestation':prior,'scope':'manual clicks only; automatic strategy off',
                'eligibility':self.region()+'/adult/user-attested (prior)'})
            self.armed=True;self.entries=False;self.exchange.armed=True
            self.status.update(armed=True,entries_enabled=False,message='手动交易已自动启用（沿用此前本人确认）；自动策略关闭')

    async def loop(self):
        last_auto=0
        while not self.e.stop.is_set():
            try:
                if not self.armed and self.manual_mode() and time.monotonic()-last_auto>=10:
                    last_auto=time.monotonic();await self.auto_arm_manual()
                await self.step()
            except asyncio.CancelledError:raise
            except Exception as exc:
                # SDK/network errors may contain authenticated request details. Never log their text.
                self.status['message']=str(exc) if type(exc) in (DataError,ValueError,PermissionError) else '实盘等待核验：'+type(exc).__name__
                self.status['last_checked_ms']=epoch_ms()
            await asyncio.sleep(1)
    async def close(self):
        self.armed=False;self.entries=False
        if self.exchange:await self.exchange.close();self.exchange=None
        if self.ledger:self.ledger.close();self.ledger=None
        self.account=None;self.status.update(armed=False,entries_enabled=False)
