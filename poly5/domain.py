from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_CEILING
from typing import Protocol
import json
from .common import D, decimal

class DataError(ValueError):
    pass

def timestamp(value):
    return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())

@dataclass(frozen=True)
class MarketTemplate:
    id: str = 'polymarket.crypto.btc.updown.5m'
    instrument: str = 'btc'
    duration: int = 300
    slug_prefix: str = 'btc-updown-5m-'

    def candidate(self, server_seconds):
        return self.slug_prefix + str(int(server_seconds) // self.duration * self.duration)

    def parse(self, event, clob, now):
        markets = event.get('markets', [])
        if len(markets) != 1:
            raise DataError('市场候选不唯一')
        m = markets[0]
        start = timestamp(m.get('eventStartTime') or event.get('startTime', ''))
        end = timestamp(m['endDate'])
        if end-start != self.duration or event.get('slug') != self.candidate(start):
            raise DataError('市场时间窗与模板不匹配')
        if not start <= now < end:
            raise DataError('市场不属于当前窗口')
        cfg = m.get('cryptoMarketConfig') or {}
        if cfg.get('asset') != self.instrument or cfg.get('duration') != '5m':
            raise DataError('市场标的不匹配')
        names = m['outcomes']; ids = m.get('clobTokenIds') or m.get('positionIds')
        names = json.loads(names) if isinstance(names, str) else names
        ids = json.loads(ids) if isinstance(ids, str) else ids
        if len(ids) != 2 or len(set(ids)) != 2 or set(names) != {'Up', 'Down'}:
            raise DataError('结果或 token ID 无效')
        condition = m['conditionId']
        if clob.get('condition_id') != condition or {str(x['token_id']) for x in clob['tokens']} != set(ids):
            raise DataError('Gamma / CLOB 市场身份不一致')
        if {str(x['token_id']): x['outcome'] for x in clob['tokens']} != dict(zip(ids,names)):
            raise DataError('Gamma / CLOB 方向映射不一致')
        schedule = m.get('feeSchedule')
        fee = None
        if m.get('feesEnabled') is False:
            fee = Fee(D('0'), 1)
        elif schedule:
            fee = Fee(decimal(schedule['rate']), int(schedule['exponent'])) if decimal(schedule['exponent']) == int(schedule['exponent']) and schedule.get('takerOnly') is True else None
        return Market(str(m['id']), self.id, event['slug'], m['question'], condition,
                      dict(zip(names, ids)), start, end,
                      m.get('acceptingOrders') is True and clob.get('accepting_orders') is True and m.get('closed') is False and clob.get('closed') is False,
                      decimal(str(clob['minimum_order_size'])), decimal(str(clob['minimum_tick_size'])), fee,
                      m.get('resolutionSource','UNKNOWN'), m.get('description',''),
                      cfg.get('twapEnabled') is True and cfg.get('twapLookbackSeconds') == 60)

@dataclass(frozen=True)
class Fee:
    rate: D
    exponent: int
    def __post_init__(self):
        if self.rate is None or not D(0) <= self.rate <= D(1) or self.exponent not in (1,2):
            raise DataError('手续费规则未验证')
    def cost(self, p, q):
        return q * self.rate * (p * (1-p)) ** self.exponent

@dataclass
class Market:
    id: str
    template_id: str
    slug: str
    title: str
    condition_id: str
    outcomes: dict
    start: int
    end: int
    accepting: bool
    min_size: D
    tick: D
    fee: Fee | None
    resolution_source: str
    rules: str
    twap_60_verified: bool
    venue_id: str = 'polymarket'

@dataclass
class Quote:
    """Authoritative top-of-book prices, separate from reconstructed depth."""
    bid: object = None
    ask: object = None
    source_ms: int = 0
    received_ms: int = 0

    def update(self,bid,ask,source_ms,received_ms):
        if int(source_ms)<self.source_ms:return False
        bid,ask=decimal(bid),decimal(ask)
        if bid is not None and not D(0)<=bid<D(1):raise DataError('买一价非法')
        if ask is not None and not D(0)<ask<=D(1):raise DataError('卖一价非法')
        self.bid=bid if bid else None;self.ask=ask if ask!=D(1) else None
        self.source_ms=int(source_ms);self.received_ms=received_ms;return True

    def view(self,now_ms):
        quality='EMPTY' if self.bid is None or self.ask is None else 'CROSSED' if self.bid>=self.ask else 'OK'
        fresh=quality=='OK' and -500<=now_ms-self.source_ms<=1500 and 0<=now_ms-self.received_ms<=1500
        return {'bid':self.bid,'ask':self.ask,'source_ms':self.source_ms,'age_ms':max(0,now_ms-self.source_ms),
                'clock_delta_ms':now_ms-self.source_ms,'fresh':fresh,'quality':quality}

@dataclass
class Book:
    asset_id: str
    condition_id: str
    bids: dict = field(default_factory=dict)
    asks: dict = field(default_factory=dict)
    source_ms: int = 0
    received_ms: int = 0
    initialized: bool = False
    quality: str = 'UNKNOWN'
    mismatches: int = 0

    def snapshot(self, payload, received_ms):
        if payload.get('asset_id') != self.asset_id or payload.get('market') != self.condition_id:
            raise DataError('盘口身份错误')
        source_ms = int(payload['timestamp'])
        if source_ms < self.source_ms:
            return False
        levels = {}
        for side in ('bids', 'asks'):
            levels[side] = {}
            for row in payload[side]:
                p,q = decimal(row['price']), decimal(row['size'])
                if not D(0)<p<D(1) or q<0:
                    raise DataError('盘口数值非法')
                if q:
                    levels[side][p] = q
        self.bids,self.asks = levels['bids'], levels['asks']
        self.source_ms, self.received_ms, self.initialized = source_ms, received_ms, True
        self.mismatches = 0
        self.check()
        return True

    def delta(self, changes, source_ms, received_ms):
        if not self.initialized or source_ms < self.source_ms:
            return False
        bids, asks = dict(self.bids), dict(self.asks)
        for change in changes:
            if change['asset_id'] != self.asset_id:
                continue
            p,q = decimal(change['price']), decimal(change['size'])
            if not D(0)<p<D(1) or q<0 or change['side'] not in ('BUY','SELL'):
                raise DataError('盘口增量非法')
            side = bids if change['side']=='BUY' else asks
            if q: side[p] = q
            else: side.pop(p, None)
        self.bids,self.asks = bids,asks
        self.source_ms,self.received_ms = source_ms,received_ms
        self.check()
        for change in reversed(changes):
            if change.get('asset_id')!=self.asset_id:continue
            bid=decimal(change.get('best_bid'));ask=decimal(change.get('best_ask'))
            actual_bid=max(self.bids) if self.bids else D(0)
            actual_ask=min(self.asks) if self.asks else D(1)
            if (bid is not None and bid!=actual_bid) or (ask is not None and ask!=actual_ask):
                # Several messages can share one timestamp and a mismatch usually
                # heals on the next one (82% next message, 91% within 3 in replay
                # of 32k live deltas). Never tradable while suspect; only a
                # persistent mismatch discards the book and forces a snapshot.
                self.mismatches+=1
                if self.mismatches>=3:self.quality='GAP';self.initialized=False
                else:self.quality='SUSPECT'
            else:self.mismatches=0
            break
        return True

    def check(self):
        self.quality = 'EMPTY' if not self.bids or not self.asks else 'CROSSED' if max(self.bids)>=min(self.asks) else 'OK'

    def fresh(self, now_ms, age=1500):
        return self.initialized and self.quality=='OK' and -500<=now_ms-self.source_ms<=age and 0<=now_ms-self.received_ms<=age

    def sellable(self, now_ms, age=1500):
        # An empty ask side near resolution must not hide a real, fresh bid.
        return self.initialized and self.quality in ('OK','EMPTY') and bool(self.bids) and -500<=now_ms-self.source_ms<=age and 0<=now_ms-self.received_ms<=age

    def view(self, now_ms, limit=8):
        bid=max(self.bids) if self.bids else None
        ask=min(self.asks) if self.asks else None
        return {'asset_id': self.asset_id, 'bid':bid,'ask':ask,'spread':ask-bid if bid is not None and ask is not None else None,
                'bids':sorted(self.bids.items(),reverse=True)[:limit], 'asks':sorted(self.asks.items())[:limit],
                'bid_depth':sum(self.bids.values(),D(0)), 'ask_depth':sum(self.asks.values(),D(0)),
                'source_ms':self.source_ms,'age_ms':max(0,now_ms-self.source_ms) if self.initialized else None,
                'clock_delta_ms':now_ms-self.source_ms if self.initialized else None,
                'fresh':self.fresh(now_ms),'quality':self.quality}

def preview_buy_notional(book,market,notional,limit):
    target,limit=decimal(notional),decimal(limit)
    if target!=D(5):raise DataError('自动模拟合约金额固定为 $5，不自动加码')
    if limit is None or not D(0)<limit<D(1) or limit%market.tick:raise DataError('限价不符合价格梯度')
    if market.fee is None:raise DataError('手续费未知')
    cost=fees=shares=D(0);fills=[];unit=D('.000001')
    for price,size in sorted(book.asks.items()):
        if price>limit or cost>=target:break
        needed=((target-cost)/price).quantize(unit,rounding=ROUND_CEILING)
        q=min(size.quantize(unit,rounding=ROUND_DOWN),needed)
        if q<=0:continue
        f=market.fee.cost(price,q);cost+=price*q;fees+=f;shares+=q
        fills.append({'price':price,'shares':q,'fee':f})
    if cost<target or shares<market.min_size:
        raise DataError('可见深度不足以买满 $5 合约，不接受小额部分买入')
    return {'shares':shares,'cost':cost,'fee':fees,'total':cost+fees,'unspent':D(0),'fills':fills,
            'win_roi':(shares-cost-fees)/(cost+fees),'kind':'estimate_only','budget_basis':'contract_notional_plus_fees'}


def preview_buy(book, market, budget, limit):
    budget,limit = decimal(budget),decimal(limit)
    if budget is None or not D(0)<budget<=D(5):
        raise DataError('每笔模拟最大支出必须大于 0 且不超过 $5，包含费用')
    if limit is None or not D(0)<limit<D(1) or limit % market.tick != 0:
        raise DataError('限价不符合该市场价格梯度')
    if market.fee is None:
        raise DataError('手续费未知，无法估算')
    remaining,shares,cost,fees = budget,D(0),D(0),D(0)
    fills=[]
    for price,size in sorted(book.asks.items()):
        if price>limit: break
        per_share=price+market.fee.cost(price,D(1))
        q=min(size,remaining/per_share).quantize(D('0.000001'), rounding=ROUND_DOWN)
        if q<=0: continue
        fee=market.fee.cost(price,q)
        fills.append({'price':price,'shares':q,'fee':fee})
        cost+=price*q; fees+=fee; shares+=q; remaining-=price*q+fee
    if shares < market.min_size:
        raise DataError('可见深度或含费预算不足以满足最小份额；不自动增加预算')
    return {'shares':shares,'cost':cost,'fee':fees,'total':cost+fees,'unspent':remaining,'fills':fills,
            'win_roi':(shares-cost-fees)/(cost+fees), 'kind':'estimate_only'}

class MarketProvider(Protocol):
    async def discover(self, template, now): ...
class AccountAdapter(Protocol):
    async def snapshot(self, address): ...
class StrategyPlugin(Protocol):
    def explain_skip(self, snapshot): ...
class ExecutionAdapter(Protocol):
    def preview(self, intent): ...
class AIProvider(Protocol):
    async def generate(self, snapshot, question): ...

@dataclass(frozen=True)
class FeatureModule:
    id: str
    name: str
    capabilities: tuple
    implemented: bool = False
    enabled: bool = False
    schema_version: str = '3.0'

class Registry:
    ALLOWED = frozenset({'PUBLIC_MARKET_READ','PUBLIC_ACCOUNT_READ','SIMULATION','AI_DIAGNOSE','GEO_CHECK','AUTHENTICATED_ORDERS_READ'})
    def __init__(self): self.modules={}
    def register(self, module):
        if module.id in self.modules: raise DataError('重复模块 ID')
        if not set(module.capabilities)<=self.ALLOWED: raise DataError('模块要求未获授权的能力')
        if module.enabled and not module.implemented: raise DataError('未实现的模块不能启用')
        self.modules[module.id]=module
    @classmethod
    def builtins(cls):
        r=cls()
        for m in (
            FeatureModule('btc5m','Bitcoin · 5 分钟市场',('PUBLIC_MARKET_READ',),True,True),
            FeatureModule('account','公开账户 / Data API v2',('PUBLIC_ACCOUNT_READ',),True,True),
            FeatureModule('paper','手动深度模拟 / FAK',('SIMULATION',),True,True),
            FeatureModule('auto-paper','自动净 ROI 研究 / PAPER',('PUBLIC_MARKET_READ','SIMULATION'),True,True),
            FeatureModule('two-stage','双阶段策略研究（暂无验证优势）',('SIMULATION',),False,False),
            FeatureModule('eth5m','Ethereum · 5 分钟（接口样例）',('PUBLIC_MARKET_READ',)),
            FeatureModule('btc15m','Bitcoin · 15 分钟（接口样例）',('PUBLIC_MARKET_READ',)),
        ): r.register(m)
        return r
