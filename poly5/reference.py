"""Causal Chainlink reference history and authenticated, read-only PolyBolt feed."""
from collections import deque
from dataclasses import dataclass
from decimal import Decimal as D
import asyncio
import json
import math
import os
from pathlib import Path
import statistics

from .common import atomic_json, dumps, loads, epoch_ms, decimal
from .domain import DataError

POLYBOLT = 'wss://ws-live-v2.polymarket.com/ws'
CHANNELS = ('price.crypto', 'price.crypto.twap')

def reference_secret_store(root):
    from .copilot import SecretStore
    # Separate namespace: an AI provider named "reference" must never decrypt
    # and send CLOB credentials as its own provider key.
    return SecretStore(Path(root)/'credentials')

def subscription_specs():
    return [{'channel':'price.crypto','filter':{'symbol':'btcusd','provider':'chainlink'}},
            {'channel':'price.crypto.twap','filter':{'symbol':'btcusd','window_seconds':60}}]


def credentials(root):
    values = {k: os.environ.get('POLY5_CLOB_' + k, '') for k in ('KEY', 'SECRET', 'PASSPHRASE')}
    if all(values.values()):
        return values
    store = reference_secret_store(root)
    if store._path('reference').exists():
        return validate_credentials(json.loads(store.get('reference')))
    return None


def validate_credentials(values):
    import base64
    import uuid
    if not isinstance(values, dict):
        raise ValueError('API 凭据格式不完整')
    values = {k: str(values.get(k, '')).strip() for k in ('KEY', 'SECRET', 'PASSPHRASE')}
    try:
        uuid.UUID(values['KEY'])
        decoded = base64.b64decode(values['SECRET'] + '=' * (-len(values['SECRET']) % 4), altchars=b'-_', validate=True)
        if len(decoded) < 16 or not 1 <= len(values['PASSPHRASE']) <= 512:
            raise ValueError()
    except Exception:
        raise ValueError('API Key、Secret 或 Passphrase 格式不正确') from None
    return values


def save_credentials(root, values):
    store = reference_secret_store(root)
    raw = store._crypt(json.dumps(validate_credentials(values)).encode())
    path = store._path('reference')
    temp = path.with_suffix('.tmp')
    with temp.open('wb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())
    os.replace(temp, path)


@dataclass(frozen=True)
class Point:
    observed_ms: int
    received_ms: int
    value: D


class ReferenceTape:
    def __init__(self):
        self.points = {k: deque(maxlen=1800) for k in ('spot', 'twap')}
        self.baselines = {}
        self.connected = False
        self.status = '等待配置参考行情'
        self.sequences = {}

    def add(self, kind, observed_ms, received_ms, value):
        p = Point(int(observed_ms), int(received_ms), decimal(value))
        if p.value is None or p.value <= 0 or p.observed_ms > p.received_ms + 500:
            raise DataError('参考价格或时间非法')
        rows = self.points[kind]
        if rows and p.observed_ms <= rows[-1].observed_ms:
            return False
        rows.append(p)
        return True

    def consume(self, frame, received_ms):
        channel = frame.get('channel')
        if channel not in CHANNELS or frame.get('v') != 1:
            return False
        seq = frame.get('seq')
        if type(seq) is not int:
            raise DataError('参考行情缺少序列号')
        previous = self.sequences.get(channel)
        if previous is not None and seq != previous + 1:
            raise DataError('参考行情序列中断，需要重连快照')
        payload = frame.get('payload', {})
        if payload.get('source') != 'chainlink' or payload.get('symbol') != 'btcusd':
            raise DataError('参考行情来源不匹配 Chainlink BTC/USD')
        kind = 'twap' if channel.endswith('.twap') else 'spot'
        if kind == 'twap' and payload.get('window_seconds') != 60:
            raise DataError('TWAP 窗口不是 60 秒')
        rows = payload.get('data', []) if frame.get('snapshot') else [payload]
        parsed = []
        for row in rows:
            value = row.get('full_accuracy_value')
            if not isinstance(value, str):
                raise DataError('参考行情缺少精确十进制价格')
            parsed.append((int(row['timestamp']), value))
        for at, value in sorted(parsed):
            self.add(kind, at, received_ms, value)
        self.sequences[channel] = seq
        return True

    def features(self, market, now, baseline_override=None):
        if not self.connected:
            raise DataError(self.status)
        available = {k: [p for p in values if p.received_ms <= now and p.observed_ms <= now]
                     for k, values in self.points.items()}
        if any(not v or now - v[-1].observed_ms > 5000 for v in available.values()):
            raise DataError('参考行情过期，等待新鲜数据')
        start = market.start * 1000
        if market.id not in self.baselines:
            if now-start<5000:
                raise DataError('等待开轮参考价格到达，确认起始基准')
            opening = [p for p in available['twap'] if 0 <= start - p.observed_ms <= 5000]
            if opening:
                self.baselines[market.id] = opening[-1]
            elif baseline_override and baseline_override.get('window_start_ms') == start:
                self.baselines[market.id] = Point(int(baseline_override['observed_ms']), int(baseline_override['received_ms']), D(baseline_override['value']))
            else:
                raise DataError('本轮起始基准尚未取得，等待下一轮')
        baseline = self.baselines[market.id]
        if baseline.received_ms > now:
            raise DataError('基准尚未收到')
        history = []
        rows = available['spot']
        cursor = len(rows) - 1
        for query in range(now, now - 61000, -1000):
            while cursor >= 0 and rows[cursor].observed_ms > query:
                cursor -= 1
            if cursor < 0 or query - rows[cursor].observed_ms > 5000:
                raise DataError('正在积累 60 秒参考价格历史')
            history.append(rows[cursor].value)
        history.reverse()
        # Exclude the latest 3 seconds when estimating shock noise, so the
        # reversal itself cannot inflate the threshold used to detect it.
        changes = [b-a for a, b in zip(history[:-4], history[1:-3])]
        mean = sum(changes, D(0)) / len(changes)
        variance = sum(((x-mean)**2 for x in changes), D(0)) / (len(changes)-1)
        sigma = max(variance.sqrt(), history[-1] * D('.000005'))
        spot = available['spot'][-1].value
        twap = available['twap'][-1].value
        short_noise = sigma * D(5).sqrt()
        remaining = max(D('.001'), D(market.end * 1000 - now)/1000)
        # A Brownian averaging approximation, explicitly a heuristic rather
        # than a trained/calibrated probability. No ROI regression gate.
        if remaining >= 60:
            mean_final = spot
            uncertainty = sigma * (remaining-D(40)).sqrt()
        else:
            known = history[-60:]
            count = max(0, 60-int(remaining))
            mean_final = (sum(known[-count:], D(0)) + spot * remaining) / (D(count)+remaining) if count else spot
            uncertainty = sigma * (remaining**3 / D(10800)).sqrt()
        z = float((mean_final-baseline.value) / max(uncertainty, D('.000001')))
        fair_up = D(str(min(.999, max(.001, .5*(1+math.erf(z/math.sqrt(2)))))))
        return {'baseline': baseline.value, 'baseline_point': {'value': str(baseline.value),
                'observed_ms': baseline.observed_ms, 'received_ms': baseline.received_ms, 'window_start_ms': start},
                'spot': spot, 'twap': twap, 'spot_distance': spot-baseline.value,
                'twap_distance': twap-baseline.value, 'sigma_1s': sigma,
                'distance_sigma': (twap-baseline.value)/short_noise,
                'spot_distance_sigma': (spot-baseline.value)/short_noise,
                'trend_5s': history[-1]-history[-6], 'trend_15s': history[-1]-history[-16],
                'move_3s': history[-1]-history[-4],
                'range_15s': max(history[-16:])-min(history[-16:]),
                'max_step_3s': max(abs(b-a) for a,b in zip(history[-4:-1],history[-3:])),
                'fair_up_heuristic': fair_up, 'latest_receive_ms': max(available['spot'][-1].received_ms, available['twap'][-1].received_ms),
                'spot_age_ms': now-available['spot'][-1].observed_ms, 'twap_age_ms': now-available['twap'][-1].observed_ms}


async def reference_loop(root, tape, stop, clock, event):
    import websockets
    while not stop.is_set():
        try:
            auth = credentials(root)
            if not auth:
                tape.status = '未配置官方参考行情，打开“配置参考行情”完成授权'
                await asyncio.sleep(2); continue
            if not clock.valid():
                tape.status = '等待服务端时间校验'; await asyncio.sleep(2); continue
            tape.status = '正在连接官方参考行情'
            async with websockets.connect(POLYBOLT, open_timeout=10, ping_interval=20, max_size=2*1024*1024) as ws:
                await ws.send(dumps({'op':'auth', 'auth':{'apiKey':auth['KEY'], 'secret':auth['SECRET'], 'passphrase':auth['PASSPHRASE']}}))
                response = loads(await asyncio.wait_for(ws.recv(), 10))
                if response.get('op') != 'authed':
                    raise DataError('参考行情授权未通过，请检查本地配置')
                tape.sequences.clear()
                await ws.send(dumps({'op':'subscribe','subscriptions':subscription_specs()}))
                seen = set()
                async for raw in ws:
                    frame = loads(raw)
                    if frame.get('op') == 'error':
                        raise DataError('参考行情订阅被拒绝')
                    if tape.consume(frame, clock.now()):
                        seen.add(frame['channel'])
                        tape.connected = len(seen) == 2
                        tape.status = 'Chainlink 现货 / 60 秒 TWAP 已连接'
                        event('reference.price', frame, source='PolyBolt / Chainlink', source_ms=frame.get('ts'))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Never include server responses or credentials in errors/logs.
            tape.status = str(exc) if isinstance(exc, DataError) else '参考行情连接中断：'+type(exc).__name__
            event('reference.connection', {'status':tape.status}, source='PolyBolt')
        finally:
            tape.connected = False
        await asyncio.sleep(5)
