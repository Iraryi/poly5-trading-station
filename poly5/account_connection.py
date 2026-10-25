"""Local, account-scoped CLOB authentication. No wallet private keys or mutations."""
import json
import hashlib
import re
from pathlib import Path
from .common import atomic_json, wallet_address
from .copilot import SecretStore
from .reference import validate_credentials

SIGNATURE_TYPES={'Deposit Wallet':3,'Proxy Wallet':1,'Safe Wallet':2,'EOA':0}

def vault(root):
    return SecretStore(Path(root)/'account-auth')

def slot(account_id):
    if not isinstance(account_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',account_id):
        raise ValueError('账户 ID 无效')
    # Preserve UUID-backed credentials and support existing named watch accounts.
    suffix=account_id if re.fullmatch(r'[a-fA-F0-9]{32}',account_id) else hashlib.sha256(account_id.encode()).hexdigest()[:32]
    return 'clob-'+suffix

def configured(root,account_id):
    return vault(root)._path(slot(account_id)).is_file()

def load(root,account):
    data=json.loads(vault(root).get(slot(account['id'])))
    if wallet_address(data['WALLET'])!=wallet_address(account['address']):
        raise ValueError('已保存凭据属于另一个资金钱包')
    return data

def save(root,account,values):
    previous=load(root,account) if configured(root,account['id']) else None
    signer=wallet_address(values.get('SIGNER',''))
    kind=values.get('SIGNATURE_TYPE')
    if type(kind) is not int or kind not in SIGNATURE_TYPES.values():raise ValueError('请选择已确认的钱包类型')
    supplied=[bool(values.get(k,'').strip()) for k in ('KEY','SECRET','PASSPHRASE')]
    if any(supplied) and not all(supplied):raise ValueError('更新认证时请同时填写 CLOB API Key、Secret、Passphrase')
    if not any(supplied):
        if not previous or previous['SIGNER']!=signer or previous['SIGNATURE_TYPE']!=kind:
            raise ValueError('首次连接或变更身份，需要完整 CLOB L2 三项凭据')
        credentials={k:previous[k] for k in ('KEY','SECRET','PASSPHRASE')}
    else:credentials=validate_credentials(values)
    data={**credentials,'SIGNER':signer,'WALLET':wallet_address(account['address']),'SIGNATURE_TYPE':kind}
    if kind==0 and signer!=data['WALLET']:raise ValueError('EOA 的签名地址必须等于资金钱包')
    vault(root).save(slot(account['id']),json.dumps(data))
    return {'configured':True,'signature_type':kind,'signer':signer}

