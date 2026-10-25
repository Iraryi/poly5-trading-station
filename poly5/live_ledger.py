"""Crash-durable account-scoped facts and state; no keys and no network calls."""
import json
import sqlite3
from pathlib import Path
from .common import dumps,loads,epoch_ms
from .account_connection import slot

class LiveLedger:
    def __init__(self,root,account):
        self.path=Path(root)/'live'/slot(account['id'])/'ledger.sqlite';self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path);self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,body TEXT NOT NULL);'
            'CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY,event_key TEXT UNIQUE NOT NULL,at_ms INTEGER NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL);')
        identity={'account_id':account['id'],'wallet':account['address'].lower()}
        previous=self.get('identity')
        if previous and previous!=identity:raise ValueError('实盘账本资金钱包不一致')
        if not previous:self.commit('identity',identity,'wallet-bound',{'identity':identity})
    def get(self,key,default=None):
        row=self.db.execute('SELECT body FROM state WHERE key=?',(key,)).fetchone()
        return loads(row[0]) if row else default
    def commit(self,kind,payload,event_key,changes):
        """A single durable transaction: audit before sending, projection after fills."""
        with self.db:
            inserted=self.db.execute('INSERT OR IGNORE INTO audit(event_key,at_ms,kind,body) VALUES(?,?,?,?)',
                (event_key,epoch_ms(),kind,dumps(payload))).rowcount
            if not inserted:return False
            for key,value in changes.items():
                self.db.execute('INSERT OR REPLACE INTO state(key,body) VALUES(?,?)',(key,dumps(value)))
        return True
    def history(self,limit=100):
        return [{'at_ms':at,'kind':kind,'payload':loads(body)} for at,kind,body in self.db.execute(
            'SELECT at_ms,kind,body FROM audit ORDER BY seq DESC LIMIT ?',(limit,))]
    def close(self):self.db.close()
