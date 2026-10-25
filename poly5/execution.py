"""Simulation only. No signer, secure SDK client, or exchange mutation methods."""
from __future__ import annotations
import uuid
from .common import D, decimal, utc_now
from .domain import DataError, preview_buy, preview_buy_notional

class ExecutionGateway:
    def __init__(self,store):
        self.store=store; self.paused=False; self.positions={}; self.orders=[]; self.seen=set()
        self.realized=D(0); self.closed_trades=[]
        for event in store.all(mode='paper'):
            if event['event_type'] in ('paper.execution','paper.settlement'):self._apply(event['payload'])

    def submit_live(self,*args,**kwargs):
        raise PermissionError('此构建不包含实盘执行器，无法发送真实订单')

    def cancel_live(self,*args,**kwargs):
        raise PermissionError('此构建不包含真实撤单接口')

    def _apply(self,p):
        if p['id'] in self.seen: return
        self.seen.add(p['id']); self.orders.append(p)
        key=p['asset_id']; pos=self.positions.setdefault(key,{'market_id':p['market_id'],'slug':p['slug'],'outcome':p['outcome'],'shares':D(0),'basis':D(0),'fee':D(0),'cycle_pnl':D(0),'cycle_basis':D(0)})
        if p['side']=='BUY':
            pos['condition_id']=p['condition_id'];pos['window_end_s']=p.get('window_end_s')
            if pos['window_end_s'] is None and p['slug'].startswith('btc-updown-5m-'):
                try:pos['window_end_s']=int(p['slug'].rsplit('-',1)[1])+300
                except ValueError:pass
        q,total,fee=decimal(p['shares']),decimal(p['total']),decimal(p['fee'])
        if p['side']=='BUY':
            if pos['shares']==0:pos['cycle_pnl']=D(0);pos['cycle_basis']=D(0)
            pos['shares']+=q; pos['basis']+=total; pos['fee']+=fee
        else:
            basis=decimal(p['basis_released']); pos['shares']-=q; pos['basis']-=basis
            profit=total-basis; self.realized+=profit
            pos['cycle_pnl']+=profit;pos['cycle_basis']+=basis
            if pos['shares']==0: self.closed_trades.append({'pnl':pos['cycle_pnl'],'basis':pos['cycle_basis']})

    def execute_paper(self,market,book,outcome,side,budget,limit,now_ms,intent_id,notional_target=False):
        if intent_id in self.seen:
            raise DataError('该模拟意图已处理，不重复执行')
        if self.paused and side=='BUY': raise DataError('新增模拟风险已暂停')
        if not market.accepting or not market.start*1000<=now_ms<market.end*1000:
            raise DataError('市场已停止接受订单或窗口已结束')
        if not (book.sellable(now_ms) if side=='SELL' else book.fresh(now_ms)): raise DataError('盘口过期或异常，模拟未成交')
        if market.fee is None: raise DataError('手续费未知')
        if side=='BUY':
            if any(p['shares']>0 and p.get('window_end_s') and now_ms>=p['window_end_s']*1000 for p in self.positions.values()):
                raise DataError('旧轮到期仓位尚未结算，暂停新增风险')
            if now_ms>=market.end*1000-15000: raise DataError('最后 15 秒停止新增模拟风险')
            result=preview_buy_notional(book,market,budget,limit) if notional_target else preview_buy(book,market,budget,limit)
            exposure=sum((p['basis'] for p in self.positions.values()),D(0))
            if exposure+result['total']>D('5.350002'): raise DataError('模拟总敞口超过 $5 合约及手续费上限')
            if self.realized<=D(-10): raise DataError('模拟累计亏损限额已触发')
            shares,total,fee=result['shares'],result['total'],result['fee']
            released=None
        elif side=='SELL':
            pos=self.positions.get(book.asset_id)
            if not pos or pos['shares']<=0: raise DataError('当前方向没有可卖模拟持仓')
            limit=decimal(limit)
            if not D(0)<limit<D(1) or limit%market.tick: raise DataError('卖出限价无效')
            remaining=pos['shares']; shares,total,fee=D(0),D(0),D(0)
            for price,size in sorted(book.bids.items(),reverse=True):
                if price<limit: break
                q=min(size,remaining); f=market.fee.cost(price,q)
                total+=price*q-f; fee+=f; shares+=q; remaining-=q
                if remaining==0: break
            if not shares: raise DataError('没有满足限价的买盘，模拟未成交')
            released=pos['basis']*shares/pos['shares']
        else: raise DataError('未知买卖方向')
        payload={'id':intent_id,'execution_id':'paper-fak-v1','market_id':market.id,'slug':market.slug,
                 'window_start_s':market.start,'window_end_s':market.end,
                 'condition_id':market.condition_id,'template_id':market.template_id,'asset_id':book.asset_id,
                 'outcome':outcome,'side':side,'shares':shares,'total':total,'fee':fee,'basis_released':released,
                 'budget':budget,'limit':limit,'status':'SIMULATED_FAK','at':utc_now(),'source_ms':book.source_ms,
                 'assumptions':'至少 200 ms 延迟后重新获取盘口；仅按可见深度估算，无队列保证；不是实际成交'}
        if side=='BUY':
            payload['contract_cost']=result['cost'];payload['budget_basis']=result.get('budget_basis','total_including_fees')
            if notional_target:payload['execution_id']='paper-notional-fok-v1'
        self.store.append('paper.execution',payload,mode='paper',account_id='paper-local',market_id=market.id,
                          condition_id=market.condition_id,asset_id=book.asset_id,source_ms=book.source_ms,event_id=intent_id)
        self.store.flush(); self._apply(payload)
        return payload

    def settle_paper(self,resolution,asset_id,now_ms,source='CLOB confirmed resolution'):
        """Paper payout after verified identity and explicit 0/1 official outcome.

        No market order, redemption transaction or wallet signing is performed.
        The network caller must supply the confirmed public CLOB response.
        """
        pos=self.positions.get(asset_id)
        if not pos or pos['shares']<=0:return None
        end=pos.get('window_end_s')
        if end is None or now_ms<end*1000:raise DataError('市场尚未到期，不能模拟结算')
        if resolution.get('condition_id')!=pos.get('condition_id') or resolution.get('closed') is not True:
            raise DataError('结算市场身份不匹配或尚未封盘')
        tokens=resolution.get('tokens',[])
        if len(tokens)!=2 or len({str(t.get('token_id')) for t in tokens})!=2 or any(type(t.get('winner')) is not bool for t in tokens) or sum(t['winner'] for t in tokens)!=1:
            raise DataError('结果尚未明确，不推断胜负')
        token=next((t for t in tokens if str(t.get('token_id'))==asset_id and t.get('outcome')==pos['outcome']),None)
        if token is None:raise DataError('结算结果方向与持仓不匹配')
        payout=D(1) if token['winner'] else D(0)
        intent='settle-'+pos['condition_id']+'-'+asset_id
        if intent in self.seen:return None
        payload={'id':intent,'execution_id':'paper-resolution-v1','market_id':pos['market_id'],'slug':pos['slug'],
                 'condition_id':pos['condition_id'],'template_id':'polymarket.crypto.btc.updown.5m','asset_id':asset_id,
                 'outcome':pos['outcome'],'side':'SETTLE','shares':pos['shares'],'total':payout*pos['shares'],
                 'fee':D(0),'basis_released':pos['basis'],'payout_per_share':payout,
                 'budget':None,'limit':None,'status':'SIMULATED_SETTLEMENT','at':utc_now(),'source_ms':now_ms,
                 'assumptions':'按已确认结果模拟兑付；不是链上实际赎回','resolution_source':source,
                 'resolution_evidence':resolution}
        self.store.append('paper.settlement',payload,mode='paper',account_id='paper-local',market_id=pos['market_id'],
                          condition_id=pos['condition_id'],asset_id=asset_id,source=source,event_id=intent)
        self.store.flush();self._apply(payload);return payload

    def view(self):
        return {'positions':self.positions,'orders':self.orders[-100:],'realized_pnl':self.realized,
                'exposure':sum((p['basis'] for p in self.positions.values()),D(0)),
                'closed_count':len(self.closed_trades),'profitable_count':sum(p['pnl']>0 for p in self.closed_trades),
                'paused':self.paused,'doi':None,'doi_status':'definition_pending'}
