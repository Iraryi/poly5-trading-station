"""User-entered owner key stays in this Windows account's encrypted local vault."""
import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from .account_connection import vault,slot,load as load_credentials
from .common import Settings,masked,wallet_address,utc_now,D

def owner_slot(account_id):return 'owner-'+slot(account_id)[5:]

def sealed_write(root,name,payload):
    store=vault(root);path=store._path(name);tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('wb') as f:f.write(store._crypt(json.dumps(payload).encode()));f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:tmp.unlink(missing_ok=True)

def signer_info(private_key,account):
    from eth_account import Account
    from polymarket._internal.wallet import try_classify_wallet_type,signature_type_for
    from polymarket._internal.environment import PRODUCTION_CONFIG
    key=private_key.strip().removeprefix('0x')
    if not re.fullmatch('[0-9a-fA-F]{64}',key):raise ValueError('密钥应为 64 位十六进制；请只粘贴到本机窗口')
    try:owner=Account.from_key(bytes.fromhex(key))
    except Exception:raise ValueError('密钥格式无效') from None
    wallet=wallet_address(account['address'])
    kind=try_classify_wallet_type(signer=owner.address,wallet=wallet,config=PRODUCTION_CONFIG.wallet_derivation)
    # This flow never creates a new wallet or enables the Deposit auto-deployment path.
    if kind not in ('POLY_PROXY','GNOSIS_SAFE','EOA'):
        raise ValueError('此密钥不能确定性控制当前资金钱包；请核对导出时使用的邮箱')
    return {'private_key':'0x'+key,'signer':owner.address.lower(),'wallet':wallet,'wallet_type':kind,'signature_type':signature_type_for(kind)}

def signer_configured(root,account):return vault(root)._path(owner_slot(account['id'])).is_file()

def signer_load(root,account):
    if not signer_configured(root,account):raise ValueError('尚未完成本机邮箱账户签名授权')
    raw=json.loads(vault(root).get(owner_slot(account['id'])))
    info=signer_info(raw['private_key'],account)
    if raw['wallet']!=info['wallet'] or raw['signer']!=info['signer']:raise ValueError('签名身份与当前账户不一致')
    return info

async def connect_owner(root,account,private_key,factory=None):
    info=signer_info(private_key,account)
    if factory is None:
        from polymarket import AsyncSecureClient
        factory=AsyncSecureClient.create
    client=None
    try:
        client=await asyncio.wait_for(factory(private_key=info['private_key'],wallet=info['wallet']),40)
        if str(client.wallet).lower()!=info['wallet'] or str(client.signer).lower()!=info['signer'] or client.wallet_type!=info['wallet_type']:
            raise ValueError('官方客户端返回的钱包身份不一致')
        balance=await asyncio.wait_for(client.get_balance_allowance(asset_type='COLLATERAL'),15)
        from .sdk_pages import collect
        orders=await asyncio.wait_for(collect(client.list_open_orders()),20)
        credentials=client.credentials
        raw={'KEY':credentials.key,'SECRET':credentials.secret,'PASSPHRASE':credentials.passphrase,
            'SIGNER':info['signer'],'WALLET':info['wallet'],'SIGNATURE_TYPE':info['signature_type']}
        from .reference import validate_credentials
        validate_credentials(raw)
        if balance.balance<0:raise ValueError('官方余额响应无效')
        # No key material crosses the UI/worker IPC pipe or enters ordinary settings.
        sealed_write(root,owner_slot(account['id']),info)
        sealed_write(root,slot(account['id']),raw)
        settings=Settings(root);active=settings.active_account()
        if active and active['id']==account['id'] and active['address'].lower()==info['wallet']:
            settings.data['auth_read_enabled']=True;settings.save()
        return {'connected':True,'wallet':masked(info['wallet']),'signer':masked(info['signer']),
            'wallet_type':info['wallet_type'],'cash_cache':str(D(balance.balance)/D(1000000)),'open_orders':len(orders),
            'at':utc_now(),'live_enabled':False}
    finally:
        if client is not None:await client.close()

async def owner_client(root,account):
    info=signer_load(root,account);c=load_credentials(root,account)
    if c['SIGNER'].lower()!=info['signer'] or c['SIGNATURE_TYPE']!=info['signature_type']:raise ValueError('CLOB 凭据与签名者不匹配')
    from polymarket import AsyncSecureClient
    from polymarket.models.clob.api_key import ApiKeyCreds
    return await asyncio.wait_for(AsyncSecureClient.create(private_key=info['private_key'],wallet=info['wallet'],
        credentials=ApiKeyCreds(apiKey=c['KEY'],secret=c['SECRET'],passphrase=c['PASSPHRASE'])),40)

def run_setup(root):
    from PySide6.QtCore import Qt,QObject,QThread,Signal,QUrl
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QApplication,QDialog,QVBoxLayout,QLabel,QLineEdit,QPushButton,QCheckBox
    import sys
    app=QApplication(sys.argv);account=Settings(root).active_account()
    dialog=QDialog();dialog.setWindowTitle('POLY5 · 邮箱账户授权');dialog.resize(680,460)
    dialog.setStyleSheet('QWidget{background:#0d1520;color:#e5edf8;font:15px "Microsoft YaHei UI";}QLineEdit{background:#172232;padding:12px;border:1px solid #304156;}QPushButton{background:#245a50;padding:12px;border:0;border-radius:5px;}')
    layout=QVBoxLayout(dialog)
    title=QLabel('连接现有 Polymarket 邮箱钱包');title.setStyleSheet('font-size:23px;font-weight:600;');layout.addWidget(title)
    info=QLabel('资金钱包：'+(masked(account['address']) if account else '请先在交易台选择公开资金钱包'))
    layout.addWidget(info)
    guide=QLabel('1. 在官方 Magic 页面用持有资金的同一邮箱登录并导出密钥。\n2. 仅粘贴到下方本机加密输入框。\n3. 校验钱包、创建 CLOB 凭据并读取余额；此步骤不会下单。');guide.setWordWrap(True);layout.addWidget(guide)
    official=QPushButton('打开官方 Magic 导出页面');official.clicked.connect(lambda:QDesktopServices.openUrl(QUrl('https://reveal.magic.link/polymarket')));layout.addWidget(official)
    field=QLineEdit();field.setEchoMode(QLineEdit.EchoMode.Password);field.setPlaceholderText('仅在本机输入密钥，不发到聊天、截图或日志');layout.addWidget(field)
    consent=QCheckBox('允许此本机应用加密保存钱包签名密钥；密钥控制钱包资金。');layout.addWidget(consent)
    save=QPushButton('加密连接并验证账户');save.setEnabled(bool(account));layout.addWidget(save)
    status=QLabel('等待你本人操作。交易启用和资金限额在下一步单独确认。');status.setWordWrap(True);layout.addWidget(status)
    class Task(QThread):
        done=Signal(object)
        def __init__(self,key):super().__init__();self.key=key
        def run(self):
            try:result=asyncio.run(connect_owner(root,account,self.key))
            except Exception as exc:
                result={'connected':False,'error':'认证连接未完成：'+type(exc).__name__+'；请核对网络和资金邮箱后重试'}
            finally:self.key=None
            self.done.emit(result)
    tasks=[]
    def complete(result):
        save.setEnabled(True)
        if result.get('connected'):status.setText('已认证：'+result['wallet']+'\nCLOB 现金缓存 '+result['cash_cache']+' pUSD；挂单 '+str(result['open_orders'])+'\n可以关闭本窗口。实盘尚未启用。')
        else:status.setText(result.get('error','认证未完成'))
    def submit():
        if not consent.isChecked():status.setText('请先勾选本机保存授权。');return
        key=field.text().strip()
        try:signer_info(key,account)
        except ValueError as exc:status.setText(str(exc));field.clear();return
        if app.clipboard().text().strip()==key:app.clipboard().clear()
        field.clear();save.setEnabled(False);status.setText('正在校验官方账户与现金缓存，请稍候…')
        task=Task(key);tasks.append(task);task.done.connect(complete);task.start()
    save.clicked.connect(submit)
    def close_event(event):
        if any(t.isRunning() for t in tasks):event.ignore();status.setText('正在完成本机认证，请等结果返回后关闭。')
        else:event.accept()
    dialog.closeEvent=close_event;dialog.show();app.exec()
