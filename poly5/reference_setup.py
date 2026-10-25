"""Loopback-only wallet signing/import wizard. Never receives a private key."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import threading
import time
import webbrowser

import httpx
from .common import atomic_json, wallet_address
from .reference import validate_credentials, save_credentials, POLYBOLT, subscription_specs, reference_secret_store

HTML = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>POLY5 参考行情配置</title>
<style>body{background:#0b111a;color:#e5edf8;font:15px system-ui;max-width:640px;margin:60px auto;padding:24px}section{background:#111b28;padding:28px;border-radius:12px}h1{font-size:24px}p{line-height:1.8;color:#a6b4c7}button{background:#56deb6;color:#08251c;border:0;padding:12px 20px;border-radius:6px;font-size:15px;cursor:pointer}input{display:block;width:95%;background:#0b111a;color:#e5edf8;border:1px solid #314153;padding:12px;margin:12px 0;border-radius:5px}summary{cursor:pointer;margin-top:28px}#status{white-space:pre-wrap;line-height:1.8;color:#7faeff}button:disabled{opacity:.5}</style>
<section><h1>连接官方参考行情</h1><p>使用 Polymarket 钱包连接并签署身份确认消息，创建或读取 CLOB API 凭据。本程序将其用于 Chainlink 现货与 TWAP 行情读取，并在当前 Windows 用户下加密保存。</p><p>请在钱包中核对：签名域 ClobAuthDomain、身份确认消息。此流程不要求输入钱包私钥，也不请求转账或代币授权。</p><button id="wallet">连接钱包并配置</button><p id="status">等待连接。</p><details><summary>我已有 API Key、Secret 和 Passphrase</summary><input id="key" autocomplete="off" placeholder="API Key"><input id="secret" type="password" autocomplete="new-password" placeholder="API Secret"><input id="passphrase" type="password" autocomplete="new-password" placeholder="API Passphrase"><button id="import">加密保存并验证</button></details></section>
<script nonce="NONCE">const token='TOKEN'; const status=document.getElementById('status');
async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Poly5-Token':token},body:JSON.stringify(body)});const j=await r.json();if(!r.ok)throw Error(j.error||'配置失败');return j;}
async function busy(fn){for(const b of document.querySelectorAll('button'))b.disabled=true;try{await fn();}catch(e){status.textContent=e.message;}finally{for(const b of document.querySelectorAll('button'))b.disabled=false;}}
document.getElementById('wallet').onclick=()=>busy(async()=>{const provider=window.ethereum;if(!provider)throw Error('此浏览器未检测到钱包扩展。请用你安装钱包的浏览器打开此本机网址，或展开下方入口填写已有 API 凭据。');status.textContent='请在钱包中连接你的账户。';const accounts=await provider.request({method:'eth_requestAccounts'});if(!accounts.length)throw Error('未选择钱包账户');const challenge=await post('/challenge',{address:accounts[0]});status.textContent='请在钱包中核对并签署身份确认消息。';const signature=await provider.request({method:'eth_signTypedData_v4',params:[accounts[0],JSON.stringify(challenge.typed_data)]});status.textContent='正在向官方接口取得凭据并测试参考行情…';const done=await post('/complete',{challenge:challenge.id,signature});status.textContent=done.message;});
document.getElementById('import').onclick=()=>busy(async()=>{status.textContent='正在验证官方连接…';const done=await post('/import',{KEY:document.getElementById('key').value,SECRET:document.getElementById('secret').value,PASSPHRASE:document.getElementById('passphrase').value});for(const id of ['key','secret','passphrase'])document.getElementById(id).value='';status.textContent=done.message;});
</script></html>'''


async def probe(values):
    import websockets
    async with websockets.connect(POLYBOLT,open_timeout=10,ping_interval=20,max_size=2*1024*1024) as ws:
        await ws.send(json.dumps({'op':'auth','auth':{'apiKey':values['KEY'],'secret':values['SECRET'],'passphrase':values['PASSPHRASE']}}))
        reply=json.loads(await asyncio.wait_for(ws.recv(),10))
        if reply.get('op')!='authed':raise ValueError('官方 API 身份验证未通过，请核对凭据')
        await ws.send(json.dumps({'op':'subscribe','subscriptions':subscription_specs()}))
        seen=set()
        until=time.monotonic()+12
        while time.monotonic()<until:
            reply=json.loads(await asyncio.wait_for(ws.recv(),max(.1,until-time.monotonic())))
            if reply.get('op')=='error':return False
            if reply.get('channel') in ('price.crypto','price.crypto.twap') and reply.get('payload',{}).get('source')=='chainlink':
                seen.add(reply['channel'])
                if len(seen)==2:return True
        return False


class SetupServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,root):
        self.root=Path(root);self.token=secrets.token_urlsafe(32);self.nonce=secrets.token_urlsafe(24)
        self.challenges={};self.lock=threading.Lock()
        super().__init__(('127.0.0.1',0),SetupHandler)
        self.origin='http://127.0.0.1:'+str(self.server_port)
        self.url=self.origin+'/'+self.token

    def store(self,values):
        values=validate_credentials(values)
        try:connected=asyncio.run(probe(values))
        except ValueError:raise
        except Exception:raise ValueError('无法完成官方连接测试，请确认网络后重试') from None
        save_credentials(self.root,values)
        atomic_json(self.root/'cache/reference-setup-status.json',{'configured':True,'reference_verified':connected,'at_ms':int(time.time()*1000)})
        return {'message':'已加密保存，官方 Chainlink 现货和 TWAP 连接通过。程序将自动读取配置。' if connected else 'API 身份已通过并加密保存，但官方参考行情订阅尚未就绪。请检查账户数据权限。'}


class SetupHandler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def valid_host(self):return self.headers.get('Host')=='127.0.0.1:'+str(self.server.server_port)
    def reply(self,status,body,html=False):
        raw=body.encode() if html else json.dumps(body,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'nonce-"+self.server.nonce+"'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        if not self.valid_host() or self.path!='/'+self.server.token:return self.reply(404,{'error':'无效本机入口'})
        self.reply(200,HTML.replace('TOKEN',self.server.token).replace('NONCE',self.server.nonce),True)
    def do_POST(self):
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=8192:return self.reply(400,{'error':'请求大小非法'})
            self.connection.settimeout(10)
            raw=self.rfile.read(length)
        except Exception:return self.reply(400,{'error':'请求读取失败'})
        if not self.valid_host() or self.headers.get('Origin')!=self.server.origin or not secrets.compare_digest(self.headers.get('X-Poly5-Token',''),self.server.token):
            return self.reply(403,{'error':'仅接受当前本机配置页面的请求'})
        try:
            body=json.loads(raw)
            if self.path=='/import':result=self.server.store(body)
            elif self.path=='/challenge':
                address=wallet_address(body.get('address',''))
                with httpx.Client(timeout=12) as client:
                    response=client.get('https://clob.polymarket.com/time');response.raise_for_status();stamp=int(response.json())
                # Use the installed official SDK's exact EIP-712 message schema.
                from polymarket._internal.l1_auth import build_api_key_auth_typed_data
                typed=build_api_key_auth_typed_data(address=address,chain_id=137,timestamp=stamp,nonce=0)
                ident=secrets.token_urlsafe(24)
                with self.server.lock:self.server.challenges[ident]={'typed':typed,'created':time.monotonic()}
                result={'id':ident,'typed_data':typed}
            elif self.path=='/complete':
                with self.server.lock:challenge=self.server.challenges.pop(body.get('challenge',''),None)
                if not challenge or time.monotonic()-challenge['created']>300:raise ValueError('签名请求已过期，请重新连接')
                signature=body.get('signature','')
                if not re.fullmatch('0x[0-9a-fA-F]{130}',signature):raise ValueError('钱包签名格式错误')
                from eth_account import Account
                from eth_account.messages import encode_typed_data
                typed=challenge['typed'];address=typed['message']['address']
                recovered=Account.recover_message(encode_typed_data(full_message=typed),signature=signature)
                if recovered.lower()!=address.lower():raise ValueError('签名账户与连接账户不一致')
                headers={'POLY_ADDRESS':address,'POLY_NONCE':'0','POLY_SIGNATURE':signature,'POLY_TIMESTAMP':typed['message']['timestamp']}
                with httpx.Client(timeout=12) as client:
                    response=client.post('https://clob.polymarket.com/auth/api-key',headers=headers)
                    if response.status_code==400:response=client.get('https://clob.polymarket.com/auth/derive-api-key',headers=headers)
                    if response.status_code!=200:raise ValueError('官方凭据接口未接受身份签名（HTTP '+str(response.status_code)+'）')
                    api=response.json()
                result=self.server.store({'KEY':api.get('apiKey'),'SECRET':api.get('secret'),'PASSPHRASE':api.get('passphrase')})
            else:return self.reply(404,{'error':'未知配置操作'})
            self.reply(200,result)
        except ValueError as exc:self.reply(400,{'error':str(exc)[:200]})
        except Exception:self.reply(400,{'error':'配置未完成，请确认官方接口连接后重试'})


def run_setup(root,open_browser=True):
    server=SetupServer(root)
    atomic_json(Path(root)/'cache/reference-setup-session.json',{'url':server.url,'expires_ms':int(time.time()*1000)+1800000})
    timer=threading.Timer(1800,server.shutdown);timer.daemon=True;timer.start()
    if open_browser:webbrowser.open(server.url)
    try:server.serve_forever(poll_interval=.25)
    finally:timer.cancel();server.server_close()


def configure_local_identity(root):
    """Explicit setup command: independent signer for reference authentication.

    This identity is never installed into the execution gateway or associated
    with a user's funding wallet. API key creation is the only write endpoint.
    """
    from eth_account import Account
    from polymarket._internal.l1_auth import sign_api_key_auth
    from polymarket._internal.actions.auth import build_l1_auth_headers
    store=reference_secret_store(root)
    if store._path('reference-signer').exists():
        account=Account.from_key(store.get('reference-signer'))
    else:
        account=Account.create()
        store.save('reference-signer',account.key.hex())
    with httpx.Client(timeout=15) as client:
        response=client.get('https://clob.polymarket.com/time');response.raise_for_status();stamp=int(response.json())
        signature=sign_api_key_auth(account,chain_id=137,timestamp=stamp,nonce=0)
        headers=build_l1_auth_headers(signature)
        response=client.get('https://clob.polymarket.com/auth/derive-api-key',headers=headers)
        if response.status_code in (400,404):
            response=client.post('https://clob.polymarket.com/auth/api-key',headers=headers)
        if response.status_code!=200:
            raise ValueError('官方认证接口未接受独立行情身份（HTTP '+str(response.status_code)+'）')
        payload=response.json()
    values=validate_credentials({'KEY':payload.get('apiKey'),'SECRET':payload.get('secret'),'PASSPHRASE':payload.get('passphrase')})
    # Preserve the credentials even if subscription entitlement is not yet ready.
    save_credentials(root,values)
    try:verified=asyncio.run(probe(values));reason='verified' if verified else 'subscription_not_ready'
    except Exception:verified=False;reason='reference_connection_not_verified'
    result={'configured':True,'reference_verified':verified,'authentication_identity':'separate_local_signer',
            'signer_address':account.address,'funding_wallet_connected':False,'reason':reason,'at_ms':int(time.time()*1000)}
    atomic_json(Path(root)/'cache/reference-setup-status.json',result)
    return result
