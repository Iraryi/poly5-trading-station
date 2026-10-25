from __future__ import annotations
import json
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

D = Decimal
DEFAULT_ROOT = Path('D:/POLYMARKET')

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

def epoch_ms():
    return time.time_ns() // 1_000_000

def decimal(value):
    if value is None:
        return None
    if isinstance(value, float):
        raise ValueError('Binary floats are prohibited for money')
    try:
        result = D(value)
    except (InvalidOperation, TypeError):
        raise ValueError('Invalid decimal') from None
    if not result.is_finite():
        raise ValueError('Non-finite decimal')
    return result

def dumps(value, **kwargs):
    return json.dumps(value, ensure_ascii=False, default=lambda v: str(v) if isinstance(v, (D, Path)) else vars(v), **kwargs)

def loads(value):
    return json.loads(value, parse_float=D)

def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as stream:
        stream.write(dumps(value, indent=2))
        stream.flush()
        os.fsync(stream.fileno())
    # Windows refuses the replace while another process (UI poller, antivirus,
    # indexer) briefly holds the target open; retry instead of failing.
    for attempt in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)

def masked(address):
    return address[:6] + '…' + address[-4:] if address else '未连接'

def wallet_address(value):
    if not re.fullmatch(r'0x[0-9a-fA-F]{40}', value):
        raise ValueError('请输入有效的 0x 开头、40 位十六进制公开钱包地址')
    return value.lower()

SECRET_KEYS = re.compile(r'(secret|passphrase|private.?key|api.?key|authorization|cookie|mnemonic|signature)', re.I)
ADDRESS = re.compile(r'0x[0-9a-fA-F]{40}(?![0-9a-fA-F])')
def redact(value):
    if isinstance(value, dict):
        return {k: '[REDACTED]' if SECRET_KEYS.search(k) or k in ('owner', 'order_owner', 'trade_owner', 'ip') else redact(v) for k,v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return ADDRESS.sub(lambda m: masked(m.group()), value)
    return value

DEFAULT_SETTINGS = {
    'schema_version': 1, 'mode': 'paper', 'auto_paper_enabled': True, 'auto_paper_strategy':'last_minute_50', 'accounts': [], 'active_account_id': None,
    'start_in_tray': True, 'copilot_open': False, 'copilot_width': 360, 'ui_mode': 'quick',
    'theme': 'dark', 'auth_read_enabled': False,
    'ai': {'enabled': False, 'consent': False, 'providers': [], 'active': '', 'requests_per_hour': 15},
}

class Settings:
    def __init__(self, root):
        self.path = Path(root) / 'settings' / 'settings.json'
        self.data = json.loads(json.dumps(DEFAULT_SETTINGS))
        if self.path.exists():
            try:
                saved=loads(self.path.read_text(encoding='utf-8'))
                if saved.get('schema_version',1)>DEFAULT_SETTINGS['schema_version']:
                    raise RuntimeError('设置来自较新版本，请使用兼容版本；原设置未被修改')
                self.data.update(saved)
            except (ValueError, OSError):
                backup = self.path.with_name('settings.corrupt-' + str(epoch_ms()) + '.json')
                self.path.rename(backup)
        # Configuration cannot enable live, even when edited manually.
        if self.data.get('mode') not in ('monitor', 'paper'):
            self.data['mode'] = 'monitor'
        self.data['live_enabled'] = False

    def save(self):
        self.data['live_enabled'] = False
        atomic_json(self.path, self.data)

    def active_account(self):
        return next((a for a in self.data['accounts'] if a['id'] == self.data['active_account_id']), None)
