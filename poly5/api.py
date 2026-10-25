from __future__ import annotations
import asyncio
import base64
import hashlib
import hmac
import os
import time
from urllib.parse import urlparse
import httpx
from polymarket import AsyncPublicClient
from .common import D, decimal, epoch_ms, loads, masked, redact, utc_now, wallet_address
from .domain import DataError

GAMMA='https://gamma-api.polymarket.com'
CLOB='https://clob.polymarket.com'
DATA='https://data-api.polymarket.com'
GEO='https://polymarket.com/api/geoblock'
MARKET_WS='wss://ws-subscriptions-clob.polymarket.com/ws/market'
USER_WS='wss://ws-subscriptions-clob.polymarket.com/ws/user'

class APIError(RuntimeError):
    pass

class ServerClock:
    def __init__(self): self.anchor=None; self.monotonic=None; self.checked=0; self.rtt_ms=None; self.skew_ms=None
    def update(self,server_seconds,started):
        elapsed=(time.monotonic()-started)*1000
        # /time has whole-second resolution. Anchor at the midpoint of that
        # second instead of systematically lagging millisecond WS timestamps.
        self.anchor=int(server_seconds)*1000+500+elapsed/2
        self.monotonic=time.monotonic(); self.checked=self.monotonic; self.rtt_ms=round(elapsed)
        self.skew_ms=round(self.anchor-epoch_ms())
    def now(self):
        return int(self.anchor+(time.monotonic()-self.monotonic)*1000) if self.anchor is not None else None
    def valid(self): return self.anchor is not None and time.monotonic()-self.checked<120

class PublicAPI:
    def __init__(self):
        self.http=httpx.AsyncClient(timeout=httpx.Timeout(12,connect=6),follow_redirects=False,
                                    headers={'User-Agent':'Poly5-ReadOnly-Development/3.1'},trust_env=True)
        self.sdk=AsyncPublicClient()
        self.cooldown={}; self.health={}; self.clock=ServerClock()

    async def close(self):
        await self.http.aclose(); await self.sdk.__aexit__(None,None,None)

    async def get(self,url,params=None,headers=None):
        host=urlparse(url).netloc
        if host not in {'clob.polymarket.com','gamma-api.polymarket.com','data-api.polymarket.com','polymarket.com'}:
            raise APIError('非许可数据源')
        if self.cooldown.get(host,0)>time.monotonic():
            raise APIError('RATE_LIMIT_COOLDOWN')
        started=time.monotonic()
        try:
            response=await self.http.get(url,params=params,headers=headers)
            if response.status_code in (429,503):
                raw=response.headers.get('retry-after','15')
                try: delay=max(1,float(raw))
                except ValueError:
                    from email.utils import parsedate_to_datetime
                    try: delay=max(1,parsedate_to_datetime(raw).timestamp()-time.time())
                    except Exception: delay=15
                self.cooldown[host]=time.monotonic()+delay
            if response.status_code!=200: raise APIError(f'HTTP_{response.status_code}')
            result=loads(response.content)
            self.health[host]={'status':'OK','latency_ms':round((time.monotonic()-started)*1000),'checked_at':utc_now(),'last_success_ms':epoch_ms()}
            return result
        except Exception as e:
            kind=str(e) if isinstance(e,APIError) else type(e).__name__
            self.health[host]={'status':kind,'latency_ms':round((time.monotonic()-started)*1000),'checked_at':utc_now()}
            raise APIError(kind) from None

    async def sync_clock(self):
        started=time.monotonic(); raw=await self.get(CLOB+'/time')
        if not isinstance(raw,(str,int)) or not str(raw).isdigit(): raise DataError('服务端时间格式错误')
        self.clock.update(raw,started)

    async def discover(self,template,now):
        slug=template.candidate(now)
        event=await self.get(GAMMA+'/events/slug/'+slug)
        if len(event.get('markets',[]))!=1: raise DataError('市场候选不唯一')
        clob=await self.get(CLOB+'/markets/'+event['markets'][0]['conditionId'])
        market=template.parse(event,clob,now)
        # Verify the installed official unified SDK's identity and decimal fee mapping.
        sdk_market=await asyncio.wait_for(self.sdk.get_market(slug=slug),15)
        if sdk_market.condition_id!=market.condition_id:
            raise DataError('SDK 市场身份校验失败')
        if sdk_market.trading.fee_schedule and market.fee:
            f=sdk_market.trading.fee_schedule
            if f.rate!=market.fee.rate or f.exponent!=market.fee.exponent:
                raise DataError('SDK / Gamma 手续费不一致')
        return market,event,clob

    async def book(self,asset):
        return await self.get(CLOB+'/book',{'token_id':asset})

class PublicAccountAdapter:
    def __init__(self,api): self.api=api
    async def pages(self,path,params):
        rows=[]; seen_cursors=set(); seen_rows=set(); params=dict(params)
        for _ in range(200):
            result=await self.api.get(DATA+'/v2/'+path,params)
            if not isinstance(result,dict) or not isinstance(result.get('data'),list) or not isinstance(result.get('pagination'),dict):
                raise DataError('Data API v2 分页契约不匹配；不使用 v1 回退')
            for row in result['data']:
                if path=='positions': key=(row.get('proxy_wallet'),row.get('condition_id'),row.get('token_id'))
                else:
                    from .common import dumps
                    key=hashlib.sha256(dumps(row,sort_keys=True).encode()).hexdigest()
                if key not in seen_rows: rows.append(row); seen_rows.add(key)
            pagination=result['pagination']; cursor=pagination.get('next_cursor')
            if cursor is None:
                if pagination.get('has_more') is True: raise DataError('分页完整性错误')
                return rows
            if cursor in seen_cursors or not cursor: raise DataError('分页游标循环或无效')
            seen_cursors.add(cursor); params['cursor']=cursor
        raise DataError('分页超过安全上限；快照未完成，不更新账户')

    async def snapshot(self,address):
        address=wallet_address(address)
        positions,value=await asyncio.gather(self.pages('positions',{'user':address,'status':'OPEN','limit':100}),
                                           self.api.get(DATA+'/v2/value',{'user':address}))
        v=value.get('data')
        if not isinstance(v,dict) or 'value' not in v or v.get('proxy_wallet','').lower()!=address:
            raise DataError('持仓估值返回身份不匹配')
        value=decimal(v['value'])
        if value is None or value<0: raise DataError('持仓估值非法')
        for row in positions:
            if row.get('proxy_wallet','').lower()!=address: raise DataError('持仓快照账户不匹配')
            for key in ('current_size','avg_price','current_value'):
                if decimal(row.get(key)) is None: raise DataError('持仓关键字段缺失')
        return {'mode':'watch_only','address':masked(address),'source':'Data API v2 /positions + /value',
                'observed_at':utc_now(),'observed_ms':epoch_ms(),'position_value':value,'positions':redact(positions),
                'cash':None,'available':None,'reserved':None,'equity':None,'realized_today':None,
                'unrealized':sum((decimal(p['unrealized_pnl']) for p in positions),D(0)) if all(p.get('unrealized_pnl') is not None for p in positions) else None,
                'orders':None,'quality':'OK'}

class AuthReadAdapter:
    """Existing L2 credentials only; fixed GET allowlist; never creates credentials."""
    def __init__(self,api,root=None,account_provider=None):
        self.api=api;self.root=root;self.account_provider=account_provider

    def credentials(self):
        if self.root is not None and self.account_provider:
            from .account_connection import configured,load
            active=self.account_provider()
            if active and configured(self.root,active['id']):return load(self.root,active)
        names=('KEY','SECRET','PASSPHRASE','SIGNER','WALLET')
        values={name:os.environ.get('POLY5_CLOB_'+name,'') for name in names}
        if not all(values.values()): raise APIError('AUTH_NOT_CONFIGURED：需要本地环境变量中的 L2 凭据及账户身份')
        wallet_address(values['SIGNER']); wallet_address(values['WALLET'])
        return values

    def headers(self,path):
        if path not in ('/data/orders','/balance-allowance'): raise PermissionError('认证读取路径未授权')
        c=self.credentials()
        if not self.api.clock.valid(): raise APIError('CLOCK_UNKNOWN')
        stamp=str(self.api.clock.now()//1000)
        secret=base64.urlsafe_b64decode(c['SECRET']+'='*((-len(c['SECRET']))%4))
        signature=base64.urlsafe_b64encode(hmac.new(secret,(stamp+'GET'+path).encode(),hashlib.sha256).digest()).decode()
        return {'POLY_ADDRESS':c['SIGNER'],'POLY_API_KEY':c['KEY'],'POLY_PASSPHRASE':c['PASSPHRASE'],
                'POLY_TIMESTAMP':stamp,'POLY_SIGNATURE':signature}

    async def verify_identity(self,address):
        c=self.credentials();address=wallet_address(address)
        if wallet_address(c['WALLET'])!=address:raise DataError('认证资金钱包与当前账户不一致')
        from polymarket._internal.wallet import try_classify_wallet_type,signature_type_for
        from polymarket._internal.environment import PRODUCTION_CONFIG
        actual=try_classify_wallet_type(signer=c['SIGNER'],wallet=address,config=PRODUCTION_CONFIG.wallet_derivation)
        if actual is None:raise DataError('无法确定性核对资金钱包；会话签名者须走专用授权接入，当前保持公开监控')
        if signature_type_for(actual)!=c.get('SIGNATURE_TYPE'):raise DataError('所选钱包类型与 SDK 确定性身份核对不一致')

    async def balance(self,address):
        c=self.credentials()
        if wallet_address(c['WALLET'])!=wallet_address(address):raise DataError('余额读取账户不匹配')
        kind=c.get('SIGNATURE_TYPE')
        if type(kind) is not int or kind not in (0,1,2,3):raise DataError('尚未确认账户钱包类型')
        raw=await self.api.get(CLOB+'/balance-allowance',{'asset_type':'COLLATERAL','signature_type':kind},self.headers('/balance-allowance'))
        from polymarket.models.clob.account import BalanceAllowance
        parsed=BalanceAllowance.parse_response(raw)
        if parsed.balance<0 or any(v<0 for v in parsed.allowances.values()):raise DataError('账户余额或授权额度非法')
        # SDK 0.13 collateral uses six base-unit decimals. Cache is not spendable cash.
        return {'cash':D(parsed.balance)/D(1000000),'allowances':{k:str(v) for k,v in parsed.allowances.items()},
            'available':None,'reserved':None,'equity':None,'source':'CLOB /balance-allowance 缓存 · pUSD / 6 decimals',
            'observed_ms':epoch_ms(),'observed_at':utc_now(),'quality':'OK','identity_verified':True}

    async def orders(self,address,order_id=None):
        c=self.credentials()
        if c['WALLET'].lower()!=wallet_address(address): raise DataError('认证资金钱包与观察账户不一致')
        params={'id':order_id} if order_id else {}; result=[]; seen=set()
        for _ in range(200):
            page=await self.api.get(CLOB+'/data/orders',params,self.headers('/data/orders'))
            if not isinstance(page.get('data'),list): raise DataError('认证订单格式不匹配')
            for row in page['data']:
                if row.get('maker_address','').lower()!=address.lower(): raise DataError('订单资金账户不匹配')
                result.append(row)
            cursor=page.get('next_cursor')
            if not cursor or cursor=='LTE=': return result
            if cursor in seen: raise DataError('认证订单分页循环')
            seen.add(cursor); params['next_cursor']=cursor
        raise DataError('认证订单分页不完整')
