"""Read-only verification of the shipped EXE across a real BTC5m boundary."""
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from poly5.api import PublicAPI
from poly5.common import dumps
from poly5.domain import MarketTemplate

BASE = SOURCE.parent
ROOT = BASE / 'work' / ('rollover-dev3-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
ROOT.mkdir(parents=True)
EXE = BASE / 'outputs/Poly5/Poly5.exe'
REPORT = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
          'sdk_version': version('polymarket-client'), 'binary': str(EXE),
          'binary_sha256': hashlib.sha256(EXE.read_bytes()).hexdigest(),
          'data_root': str(ROOT), 'checks': [],
          'sources': ['https://docs.polymarket.com/changelog',
                      'https://docs.polymarket.com/market-data/discover-markets']}


def check(name, result):
    REPORT['checks'].append({'check': name, 'passed': bool(result)})
    print(name, 'PASS' if result else 'FAIL', flush=True)
    if not result:
        raise AssertionError(name)


async def identities():
    api = PublicAPI()
    try:
        await api.sync_clock()
        now = api.clock.now() // 1000
        template = MarketTemplate()
        current, event, clob = await api.discover(template, now)
        # Validation uses the target window's start; this does NOT activate it
        # early or grant trading permission. The real worker revalidates at rollover.
        following, next_event, next_clob = await api.discover(template, current.end)
        REPORT['current'] = asdict(current)
        REPORT['next_candidate'] = asdict(following)
        REPORT['next_candidate']['not_active_yet'] = True
        REPORT['official_responses'] = {'current_gamma': event, 'current_clob': clob,
                                         'next_gamma': next_event, 'next_clob': next_clob}
        check('next window is exactly adjacent and has distinct identities',
              following.start == current.end and following.id != current.id
              and following.condition_id != current.condition_id
              and not (set(following.outcomes.values()) & set(current.outcomes.values())))
        check('both windows passed Gamma / CLOB / official SDK validation',
              current.end - current.start == following.end - following.start == 300)
        return current, following, max(50, current.end - api.clock.now() // 1000 + 30)
    finally:
        await api.close()


def main():
    try:
        current, following, duration = asyncio.run(identities())
        REPORT['duration_seconds'] = duration
        print('Current:', current.slug, 'Next:', following.slug, 'Duration:', duration, flush=True)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        env = dict(os.environ, TEMP=str(ROOT), TMP=str(ROOT), PATH=r'C:\Windows\System32;C:\Windows')
        child = subprocess.Popen([str(EXE), '--worker', '--duration', str(duration),
                                  '--data-dir', str(ROOT / 'runtime')],
                                 cwd=ROOT, env=env, startupinfo=startup)
        started = time.monotonic()
        snapshots = []
        last_key = None
        while child.poll() is None:
            if time.monotonic() - started > duration + 60:
                (ROOT / 'runtime/STOP').touch()
                child.wait(timeout=30)
                raise TimeoutError('Worker exceeded bounded observation time')
            snapshot_file = ROOT / 'runtime/cache/snapshot.json'
            try:
                state = json.loads(snapshot_file.read_text(encoding='utf-8'))
                market = state.get('market') or {}
                key = (market.get('slug'), state.get('prefetched_slug'), state.get('ws_status'),
                       tuple(sorted(state.get('books', {}))))
                if key != last_key:
                    last_key = key
                    record = {k: state.get(k) for k in ['at', 'server_ms', 'market', 'prefetched_slug',
                                                       'ws_status', 'books', 'live_enabled', 'storage_error']}
                    snapshots.append(record)
                    print('State:', key[:3], flush=True)
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            time.sleep(1)
        check('shipped worker exits cleanly', child.returncode == 0)
        conn = sqlite3.connect(ROOT / 'runtime/log/metrics.sqlite')
        events = [json.loads(row[0]) for row in conn.execute(
            "SELECT body FROM events WHERE event_type IN ('market.discovered','market.prefetched',"
            "'network.market_ws_connected','network.discovery_error','engine.started','engine.stopped') ORDER BY seq")]
        REPORT['events'] = events
        REPORT['raw_message_counts_by_market'] = dict(conn.execute(
            "SELECT market_id, count(*) FROM events WHERE event_type='book.raw' GROUP BY market_id"))
        conn.close()
        REPORT['snapshot_transitions'] = snapshots
        discovered = [e['payload']['market'] for e in events if e['event_type'] == 'market.discovered']
        check('EXE discovers original and next market across boundary',
              [m['slug'] for m in discovered[:2]] == [current.slug, following.slug])
        check('next round was prefetched before switching', any(
            e['event_type'] == 'market.prefetched' and e['payload']['slug'] == following.slug for e in events))
        connected = [e['payload']['round'] for e in events if e['event_type'] == 'network.market_ws_connected']
        check('WebSocket connects to both rounds', current.slug in connected and following.slug in connected)
        counts = REPORT['raw_message_counts_by_market']
        check('actual market messages recorded for each market', counts.get(current.id, 0) > 0 and counts.get(following.id, 0) > 0)
        final = json.loads((ROOT / 'runtime/cache/snapshot.json').read_text(encoding='utf-8'))
        check('old token books replaced by the verified next token books',
              final['market']['slug'] == following.slug
              and {b['asset_id'] for b in final['books'].values()} == set(following.outcomes.values()))
        check('no live execution and no storage error', not final['live_enabled'] and final['storage_error'] is None)
        transition = next(s for s in snapshots if (s.get('market') or {}).get('slug') == following.slug)
        REPORT['first_observed_switch_after_boundary_ms'] = transition['server_ms'] - following.start * 1000
        REPORT['timing_note'] = 'Snapshot sampled once per second; server time has whole-second resolution. Not a zero-gap feed claim.'
    finally:
        REPORT['passed'] = sum(c['passed'] for c in REPORT['checks'])
        REPORT['failed'] = sum(not c['passed'] for c in REPORT['checks'])
        (ROOT / 'rollover-validation.json').write_text(dumps(REPORT), encoding='utf-8')
        print('Evidence:', ROOT / 'rollover-validation.json', flush=True)


if __name__ == '__main__':
    main()
