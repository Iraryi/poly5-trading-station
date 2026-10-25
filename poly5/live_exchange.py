"""Explicitly armed execution only. SDK writes are confined to this adapter."""
import asyncio
from dataclasses import asdict,replace
from decimal import ROUND_DOWN
from types import SimpleNamespace
from .common import D,epoch_ms
from .domain import DataError
from .sdk_pages import collect

def exchange_address(asset_id):
    from polymarket._internal.environment import PRODUCTION_CONFIG
    from polymarket._internal.actions.orders.context import resolve_order_exchange_address
    return str(resolve_order_exchange_address(PRODUCTION_CONFIG,asset_id=asset_id,neg_risk=False))

def order_digest(order):
    from eth_account.messages import encode_typed_data
    from eth_utils import keccak
    from polymarket._internal.actions.orders.typed_data import build_order_typed_data
    from polymarket._internal.environment import PRODUCTION_CONFIG
    from polymarket._internal.protocol import is_v2_position_id
    fields={k:v for k,v in asdict(order).items() if k not in ('signature','post_only')}
    draft=SimpleNamespace(**fields,chain_id=PRODUCTION_CONFIG.chain_id,exchange_address=exchange_address(str(order.token_id)),
        protocol_version='3' if is_v2_position_id(str(order.token_id)) else '2')
    typed=encode_typed_data(full_message=build_order_typed_data(draft))
    return '0x'+keccak(b'\x19'+typed.version+typed.header+typed.body).hex()

class LiveExchange:
    def __init__(self,root,account,client_factory=None):
        self.root=root;self.account=account;self.factory=client_factory;self.client=None;self.armed=False
    async def read_client(self):
        if self.client is None:
            if self.factory:self.client=await self.factory()
            else:
                from .account_signer import owner_client
                self.client=await owner_client(self.root,self.account)
        return self.client
    async def cash(self,asset_id):
        """Agreeing CLOB cache + Polygon balance/allowance, minus ALL open buys."""
        import httpx
        from polymarket._internal.environment import PRODUCTION_CONFIG as cfg
        c=await self.read_client();balance=await asyncio.wait_for(c.get_balance_allowance(asset_type='COLLATERAL'),12)
        orders=await asyncio.wait_for(collect(c.list_open_orders()),15)
        spender=exchange_address(asset_id);wallet=self.account['address'][2:].rjust(64,'0')
        async with httpx.AsyncClient(timeout=10) as http:
            async def rpc(method,params):
                response=await http.post(cfg.rpc_url,json={'jsonrpc':'2.0','id':1,'method':method,'params':params});response.raise_for_status()
                data=response.json()
                if 'error' in data or 'result' not in data:raise DataError('链上余额核验失败')
                return data['result']
            if int(await rpc('eth_chainId',[]),16)!=cfg.chain_id:raise DataError('RPC 链身份错误')
            block=await rpc('eth_getBlockByNumber',['latest',False]);tag=block['number']
            if abs(epoch_ms()//1000-int(block['timestamp'],16))>120:raise DataError('链上区块过期')
            async def call(data):return int(await rpc('eth_call',[{'to':cfg.collateral_token,'data':data},tag]),16)
            onchain=await call('0x70a08231'+wallet)
            allowance=await call('0xdd62ed3e'+wallet+spender[2:].lower().rjust(64,'0'))
        if onchain!=balance.balance:raise DataError('CLOB 现金缓存与链上余额尚未一致')
        cached={k.lower():v for k,v in balance.allowances.items()}.get(spender.lower())
        if cached is None or cached<0:raise DataError('当前交易所授权额度未知')
        # Upper-bound fee reserve for any existing buy, independent of its category.
        reserved=sum((max(D(0),o.original_size-o.size_matched)*o.price*D('1.25') for o in orders if o.side=='BUY'),D(0))
        available=max(D(0),min(D(onchain),D(allowance),D(cached))/1000000-reserved)
        return {'cash':str(D(onchain)/1000000),'available':str(available),'open_order_reserve':str(reserved),
                'allowance':str(D(min(allowance,cached))/1000000),'observed_ms':epoch_ms(),'quality':'CHAIN_AND_CLOB_VERIFIED'}
    async def token_balance(self,asset):
        from polymarket._internal.protocol import is_v2_position_id
        c=await self.read_client()
        b=await asyncio.wait_for(c.get_balance_allowance(asset_type='CONDITIONAL-V2' if is_v2_position_id(asset) else 'CONDITIONAL',asset_id=asset),12)
        return D(b.balance)/1000000
    async def sign(self,asset,side,price,shares,amount=None):
        if not self.armed:raise PermissionError('实盘尚未由本机人工启用')
        c=await self.read_client()
        if side=='BUY':
            # SDK market order: whole-cent spend, never above max_price on any tick grid, FOK (no resting remainder).
            return await asyncio.wait_for(c.create_market_order(asset_id=asset,side='BUY',amount=amount,max_price=price,order_type='FOK'),15)
        # SELL keeps the exact share quantity and protected limit price; FOK, no resting remainder.
        signed=await asyncio.wait_for(c.create_limit_order(asset_id=asset,price=price,size=shares,side=side),15)
        return replace(signed,order_type='FOK')
    async def sign_resting(self,asset,side,price,shares):
        """GTC limit order for the connectivity test only (rests on the book; the caller cancels it at once)."""
        if not self.armed:raise PermissionError('实盘尚未由本机人工启用')
        c=await self.read_client()
        return await asyncio.wait_for(c.create_limit_order(asset_id=asset,price=price,size=shares,side=side),15)
    async def send_once(self,signed):
        if not self.armed:raise PermissionError('实盘已停机')
        c=await self.read_client()
        # No place_* allowance recovery, automatic approval, or network retry.
        return await asyncio.wait_for(c.post_order(signed),12)
    async def lookup(self,order_id):
        c=await self.read_client();return await asyncio.wait_for(c.get_order(order_id=order_id),12)
    async def trades(self,condition):
        c=await self.read_client();return await asyncio.wait_for(collect(c.list_account_trades(market=condition)),15)
    async def cancel_owned(self,order_id):
        if not self.armed:raise PermissionError('实盘未启用')
        c=await self.read_client();return await asyncio.wait_for(c.cancel_order(order_id=order_id),12)
    async def close(self):
        self.armed=False
        if self.client:await self.client.close();self.client=None

def buy_plan(book,market,budget,minimum):
    """Shares use SDK's 2-decimal size grid; fees get a worst-price envelope."""
    if not book.asks or market.fee is None:raise DataError('缺少可成交深度或手续费')
    limit=min(D(1)-market.tick,min(book.asks)+market.tick)
    fee_max=market.fee.rate*D('.25')**market.fee.exponent
    shares=((D(budget)-D('.00002'))/(limit+fee_max)).quantize(D('.01'),rounding=ROUND_DOWN)
    # Immediate (FOK) BUYs must carry a dollar amount with at most 2 decimals (2026-10-24: the first live order,
    # 8.08 x 0.83 = $6.7064 built as a limit order, was rejected). BUYs are now signed with the SDK market-order
    # builder: whole-cent spend `amount`, protected by max_price; the SDK floors the share count to market precision.
    amount=(shares*limit).quantize(D('.01'),rounding=ROUND_DOWN)
    if shares<market.min_size or amount/limit<market.min_size or amount<D(minimum):raise DataError('缩减预算不足最小合约金额或最小份额')
    if sum((q for p,q in book.asks.items() if p<=limit),D(0))<shares:raise DataError('保护限价内深度不足，不追价')
    left=shares;notional=D(0);level_fee=D(0)
    for p,q in sorted(book.asks.items()):
        if p>limit:break
        q=min(q,left);notional+=p*q;left-=q
        level_fee=max(level_fee,market.fee.cost(p,D(1)))
        if left==0:break
    if notional<D(minimum):raise DataError('按当前逐档深度计算的合约金额不足最小值')
    # max_cost keeps the worst-case fee envelope for the cash reservation. The edge test uses the expected cost: the
    # depth-walked average price plus the highest official fee among the levels actually consumed (2026-10-24, user-
    # approved fix "A": the envelope priced a 0.94 fill like 0.96 and rejected trades the tested rule takes).
    return {'price':limit,'shares':shares,'amount':amount,'max_cost':shares*(limit+fee_max)+D('.00002'),
            'worst_fee_per_share':fee_max,'expected_cost_per_share':notional/shares+level_fee}
