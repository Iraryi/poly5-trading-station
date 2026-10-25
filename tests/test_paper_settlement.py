import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from poly5.common import D
from poly5.domain import DataError
from poly5.execution import ExecutionGateway
from poly5.storage import EventStore
from test_station import market,book


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=EventStore(self.root)
        self.g=ExecutionGateway(self.store);self.m=market();self.b=book()
        self.g.execute_paper(self.m,self.b,'Up','BUY','5','.5',1100000,'buy')
        self.asset=self.b.asset_id
        self.result={'condition_id':self.m.condition_id,'closed':True,'tokens':[
            {'token_id':self.asset,'outcome':'Up','winner':True},
            {'token_id':'other','outcome':'Down','winner':False}]}
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_winner_payout_is_share_count_and_no_sale_fee(self):
        pos=self.g.positions[self.asset];q=pos['shares'];cost=pos['basis']
        r=self.g.settle_paper(self.result,self.asset,1400000)
        self.assertEqual(r['total'],q);self.assertEqual(r['fee'],0)
        self.assertEqual(self.g.realized,q-cost);self.assertEqual(self.g.positions[self.asset]['shares'],0)
        self.assertIsNone(self.g.settle_paper(self.result,self.asset,1400000))
        again=ExecutionGateway(self.store);self.assertEqual(again.realized,self.g.realized);self.assertEqual(len(again.closed_trades),1)
    def test_loser_is_zero_payout_not_an_invented_sell_fill(self):
        self.result['tokens'][0]['winner']=False;self.result['tokens'][1]['winner']=True
        cost=self.g.positions[self.asset]['basis'];r=self.g.settle_paper(self.result,self.asset,1400000)
        self.assertEqual(r['side'],'SETTLE');self.assertEqual(r['total'],0);self.assertEqual(self.g.realized,-cost)
    def test_closed_but_no_confirmed_winner_does_not_assume_loss(self):
        self.result['tokens'][0]['winner']=False
        with self.assertRaises(DataError):self.g.settle_paper(self.result,self.asset,1400000)
        self.assertGreater(self.g.positions[self.asset]['shares'],0);self.assertEqual(self.g.realized,0)
    def test_other_condition_and_early_result_rejected(self):
        with self.assertRaises(DataError):self.g.settle_paper(self.result,self.asset,1200000)
        self.result['condition_id']='wrong'
        with self.assertRaises(DataError):self.g.settle_paper(self.result,self.asset,1400000)
    def test_other_outcome_identity_rejected(self):
        self.result['tokens'][0]['outcome']='Down'
        with self.assertRaises(DataError):self.g.settle_paper(self.result,self.asset,1400000)


class IndexMigrationTests(unittest.TestCase):
    def test_raw_ticks_leave_index_but_keep_bytes_and_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            store=EventStore(temp)
            store.append('book.raw',{'raw':'keep'},market_id='m1')
            store.append('paper.note',{'note':'keep'},mode='paper')
            store.close()
            paths=sorted((Path(temp)/'log/events').rglob('*.gz'))
            before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
            self.assertTrue(list((Path(temp)/'log/events/raw').glob('*.gz')))
            # Simulate a pre-v4 index that still holds a per-row raw tick.
            conn=sqlite3.connect(store.db_path)
            conn.execute("INSERT INTO events(event_id,event_type,observed_at_utc,mode,body) VALUES('legacy','book.raw','2026-01-01T00:00:00Z','monitor',?)",(json.dumps({'redundant':'x'*10000}),))
            conn.execute('DELETE FROM schema_migrations WHERE version=4');conn.commit();conn.close()
            recovered=EventStore(temp)
            try:
                self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
                self.assertEqual(len(list(recovered.all())),1)
                self.assertEqual(list(recovered.all('book.raw')),[])
                self.assertEqual(list(recovered.all('paper.note'))[0]['payload']['note'],'keep')
                conn=sqlite3.connect(store.db_path)
                self.assertEqual(conn.execute("SELECT SUM(messages) FROM raw_segments WHERE market_id='m1'").fetchone()[0],1);conn.close()
                self.assertIn('keep',json.dumps([e['payload'] for e in recovered.raw_events()]))
            finally:recovered.close()

    def test_raw_burst_is_shed_and_recorded_without_halting_facts(self):
        with tempfile.TemporaryDirectory() as temp:
            store=EventStore(temp)
            try:
                store.raw_admit=0
                self.assertIsNone(store.append('book.raw',{'n':1}))
                self.assertIsNotNone(store.append('paper.order',{'n':1},mode='paper'))
                store.flush()
                self.assertIsNone(store.error)
                gap=list(store.all('book.raw_dropped'))
                self.assertEqual(gap[0]['payload']['dropped_total'],1)
            finally:store.close()

    def test_retention_removes_only_oldest_raw_segments(self):
        with tempfile.TemporaryDirectory() as temp:
            store=EventStore(temp,raw_keep_bytes=1)
            try:
                raw=Path(temp)/'log/events/raw'
                for name in ('a-0000','b-0000'):
                    with gzip.open(raw/(name+'.jsonl.gz'),'wb') as f:f.write(b'{}'+bytes([10]))
                store.raw_bytes=sum(p.stat().st_size for p in raw.glob('*'))
                store.append('paper.note',{'note':'keep'},mode='paper')
                store._enforce_raw_retention();store.flush()
                self.assertEqual(list(raw.glob('*.gz')),[])
                self.assertEqual(len(list(store.all('paper.note'))),1)
                self.assertEqual(list(store.all('engine.raw_retention'))[0]['payload']['removed_segments'],['a-0000.jsonl.gz','b-0000.jsonl.gz'])
            finally:store.close()

    def test_compressed_segments_are_replayed_once(self):
        with tempfile.TemporaryDirectory() as temp:
            store=EventStore(temp);store.append('paper.note',{'note':'x'},mode='paper');store.close()
            conn=sqlite3.connect(store.db_path)
            marked=conn.execute('SELECT COUNT(*) FROM recovered_segments').fetchone()[0];conn.close()
            self.assertGreaterEqual(marked,1)
            again=EventStore(temp)
            try:self.assertEqual(len(list(again.all('paper.note'))),1)
            finally:again.close()


if __name__=='__main__':unittest.main()
