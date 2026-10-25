from __future__ import annotations
import asyncio
import ast
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from poly5.common import D, decimal, loads, dumps, Settings, redact, wallet_address
from poly5.domain import Book, Quote, MarketTemplate, Market, Fee, FeatureModule, Registry, DataError, preview_buy
from poly5.storage import EventStore
from poly5.execution import ExecutionGateway
from poly5.api import PublicAccountAdapter, AuthReadAdapter
from poly5.copilot import SecretStore, safe_snapshot
from poly5.replay import replay_files
from ai_log_store import AIAdviceStore

ROOT=Path(__file__).resolve().parents[1]

def market():
    return Market('m','test-template','btc-test','Test','c',{'Up':'a','Down':'b'},1000,1300,True,D('5'),D('.01'),Fee(D('.07'),1),'fixture','fixture',True)
def book(bids=None,asks=None,at=1100000):
    b=Book('a','c');b.snapshot({'asset_id':'a','market':'c','timestamp':str(at),
                              'bids':bids or [{'price':'.39','size':'100'}],
                              'asks':asks or [{'price':'.40','size':'100'}]},at);return b

class DomainTests(unittest.TestCase):
    def test_decimal_never_float(self):
        for value in [float('nan'),.5,'NaN','Infinity']:
            with self.assertRaises(ValueError):decimal(value)
    def test_zero_distinct_from_none(self):self.assertIsNone(decimal(None));self.assertEqual(decimal('0'),D(0))
    def test_json_financial_precision(self):self.assertEqual(loads('{"price":0.1234567890123456789}')['price'],D('0.1234567890123456789'))
    def test_wallet_validation(self):
        self.assertEqual(wallet_address('0x'+'A'*40),'0x'+'a'*40)
        for x in ['abc','0x'+'1'*39,'0x'+'x'*40]:
            with self.assertRaises(ValueError):wallet_address(x)
    def test_budget_includes_fee(self):
        r=preview_buy(book(),market(),'5','.50');self.assertLessEqual(r['total'],D(5));self.assertGreater(r['fee'],0)
        self.assertLess(r['shares'],D('12.5'))
    def test_depth_limits_quantity(self):
        r=preview_buy(book(asks=[{'price':'.4','size':'6'},{'price':'.6','size':'99'}]),market(),'5','.5')
        self.assertEqual(r['shares'],D(6));self.assertGreater(r['unspent'],D(2))
    def test_minimum_order_cannot_raise_budget(self):
        with self.assertRaises(DataError):preview_buy(book(),market(),'1','.5')
    def test_unknown_fee_rejects(self):
        with self.assertRaises(DataError):preview_buy(book(),replace(market(),fee=None),'5','.5')
    def test_tick_size_rejects(self):
        with self.assertRaises(DataError):preview_buy(book(),market(),'5','.505')
    def test_snapshot_sorted_and_delta_deletes(self):
        b=book();b.delta([{'asset_id':'a','side':'BUY','price':'.39','size':'0'},{'asset_id':'a','side':'BUY','price':'.38','size':'7'}],1100001,1100001)
        self.assertEqual(b.view(1100001)['bid'],D('.38'));self.assertNotIn(D('.39'),b.bids)
    def test_late_snapshot_cannot_regress(self):
        b=book();self.assertFalse(b.snapshot({'asset_id':'a','market':'c','timestamp':'100','bids':[],'asks':[]},1100001));self.assertTrue(b.bids)
    def test_partial_delta_before_snapshot_rejected(self):
        b=Book('a','c');self.assertFalse(b.delta([],123,123));self.assertFalse(b.fresh(123))
    def test_stale_and_future_clock_rejected(self):
        b=book();self.assertFalse(b.fresh(1102000));self.assertFalse(b.fresh(1099000));self.assertTrue(b.fresh(1100010))
    def test_book_identity_rejected(self):
        with self.assertRaises(DataError):book().snapshot({'asset_id':'wrong','market':'c','timestamp':'1'},1)
    def test_crossed_book_not_executable(self):
        b=book(bids=[{'price':'.6','size':'10'}]);self.assertEqual(b.quality,'CROSSED');self.assertFalse(b.fresh(1100000))
    def test_delta_inconsistent_best_quote_marks_gap(self):
        bad={'asset_id':'a','side':'BUY','price':'.38','size':'10','best_bid':'.38','best_ask':'.40'}
        b=book();b.delta([bad],1100001,1100001)
        self.assertEqual(b.quality,'SUSPECT');self.assertFalse(b.fresh(1100001));self.assertTrue(b.initialized)
        b.delta([dict(bad,size='11')],1100002,1100002);b.delta([dict(bad,size='12')],1100003,1100003)
        self.assertEqual(b.quality,'GAP');self.assertFalse(b.fresh(1100003));self.assertFalse(b.initialized)
    def test_transient_best_quote_mismatch_heals_without_snapshot(self):
        b=book();best=max(b.bids)
        b.delta([{'asset_id':'a','side':'BUY','price':'.38','size':'10','best_bid':'.38','best_ask':'.40'}],1100001,1100001)
        self.assertEqual(b.quality,'SUSPECT')
        b.delta([{'asset_id':'a','side':'BUY','price':'.38','size':'10','best_bid':str(max(b.bids)),'best_ask':str(min(b.asks))}],1100002,1100002)
        self.assertEqual(b.quality,'OK');self.assertEqual(b.mismatches,0);self.assertTrue(b.fresh(1100002))
    def test_authoritative_quote_independent_of_incomplete_depth(self):
        b=book();q=Quote();q.update('.38','.40',1100001,1100001)
        b.delta([{'asset_id':'a','side':'BUY','price':'.38','size':'10','best_bid':'.38','best_ask':'.40'}],1100001,1100001)
        self.assertFalse(b.fresh(1100001));self.assertTrue(q.view(1100001)['fresh']);self.assertEqual(q.bid,D('.38'))
        self.assertFalse(q.update('.3','.4',1100000,1100002));self.assertFalse(q.view(1103000)['fresh'])
    def test_crossed_or_future_quote_not_fresh(self):
        q=Quote();q.update('.6','.4',1000,1000);self.assertFalse(q.view(1000)['fresh'])
        q.update('.3','.4',2000,1000);self.assertFalse(q.view(1000)['fresh'])
    def test_market_actual_start_not_creation(self):
        event=loads((ROOT/'research/current-event.json').read_text(encoding='utf-8'));clob=loads((ROOT/'research/clob-market.json').read_text(encoding='utf-8'))
        start=int(event['slug'].split('-')[-1]);m=MarketTemplate().parse(event,clob,start+100)
        self.assertEqual(m.start,start);self.assertEqual(m.end-m.start,300);self.assertEqual(m.fee.rate,D('.07'))
    def test_market_identity_and_time_mismatch(self):
        event=loads((ROOT/'research/current-event.json').read_text(encoding='utf-8'));clob=loads((ROOT/'research/clob-market.json').read_text(encoding='utf-8'))
        start=int(event['slug'].split('-')[-1])
        with self.assertRaises(DataError):MarketTemplate().parse(event,clob,start+300)
        clob['condition_id']='wrong'
        with self.assertRaises(DataError):MarketTemplate().parse(event,clob,start+100)
    def test_registry_denies_elevation(self):
        r=Registry.builtins()
        with self.assertRaises(DataError):r.register(FeatureModule('evil','evil',('LIVE_AUTOMATION',),True,True))
        self.assertFalse(r.modules['eth5m'].enabled);self.assertFalse(r.modules['btc15m'].enabled)
    def test_disabled_future_module_cannot_be_enabled(self):
        with self.assertRaises(DataError):Registry().register(FeatureModule('dummy','dummy',(),False,True))

class LedgerTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=EventStore(self.root)
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_1200_events_dedup_restart(self):
        for i in range(1200):self.store.append('test.event',{'n':i},event_id=str(i))
        for _ in range(100):self.store.append('test.event',{'n':0},event_id='0')
        self.store.flush();self.store.close();self.store=EventStore(self.root)
        self.assertEqual(len(list(self.store.all())),1200)
    def test_recovery_after_append_before_sqlite_commit(self):
        self.store.append('test.event',{'n':1});self.store.flush();row=list(self.store.all())[0];self.store.close()
        row['event_id']='uncommitted'
        p=self.root/'log/events/zz-interrupted.jsonl';p.write_text(dumps(row)+'\n{"incomplete":',encoding='utf-8')
        self.store=EventStore(self.root)
        self.assertEqual(len(list(self.store.all())),2);self.assertTrue(list((self.root/'log').glob('recovered-tail*')))
    def test_export_redacts_secret_and_address(self):
        self.store.append('account.test',{'api_key':'never-log-this','wallet':'0x'+'a'*40},account_id='a');self.store.flush()
        result=self.store.export(self.root/'export');content=(self.root/'export/events.jsonl').read_text(encoding='utf-8')
        self.assertEqual(result['count'],1);self.assertNotIn('never-log-this',content);self.assertNotIn('0x'+'a'*40,content)
    def test_interrupted_compression_is_not_parsed_as_json(self):
        self.store.append('test.event',{'n':1});self.store.close()
        (self.root/'log/events/interrupted.jsonl.gz.tmp').write_bytes(b'\x1f\x8bincomplete-compression')
        self.store=EventStore(self.root);self.assertEqual(len(list(self.store.all())),1)
    def test_500_delta_replay_equivalence(self):
        raw={'event_type':'book','asset_id':'a','market':'c','timestamp':'1000','bids':[{'price':'.4','size':'10'}],'asks':[{'price':'.5','size':'10'}]}
        self.store.append('book.raw',raw)
        for i in range(500):self.store.append('book.raw',{'event_type':'price_change','market':'c','timestamp':str(1001+i),'price_changes':[{'asset_id':'a','side':'BUY','price':'.4','size':str(i+1)}]})
        self.store.flush();self.store.close()
        result=replay_files((self.root/'log/events').rglob('*.jsonl*'))
        self.assertEqual(result['events'],501);self.assertEqual(result['books']['a']['bid_depth'],D(500));self.assertEqual(result['parse_errors'],[])
    def test_compact_raw_index_preserves_actual_raw(self):
        self.store.append('book.raw',{'large_raw_field':'keep-this'});self.store.flush()
        self.assertEqual(list(self.store.all()),[])  # raw ticks are counted per segment, not indexed per row
        self.assertIn('keep-this',dumps(list(self.store.raw_events())[0]))
    def test_no_live_network_methods_reachable(self):
        g=ExecutionGateway(self.store)
        with self.assertRaises(PermissionError):g.submit_live({'enabled':True})
        with self.assertRaises(PermissionError):g.cancel_live('x')
        tree=ast.parse((ROOT/'poly5/execution.py').read_text(encoding='utf-8'))
        imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        self.assertFalse(any(x and ('polymarket' in x or 'httpx' in x or 'websocket' in x) for x in imports))
    def test_paper_buy_sell_pnl_and_restart(self):
        g=ExecutionGateway(self.store);m=market();b=book()
        buy=g.execute_paper(m,b,'Up','BUY','5','.5',1100000,'paper-1');self.assertEqual(g.realized,0)
        sellbook=book(bids=[{'price':'.6','size':'100'}],asks=[{'price':'.7','size':'100'}])
        sold=g.execute_paper(m,sellbook,'Up','SELL','5','.5',1100000,'paper-2')
        expected=sold['total']-buy['total'];self.assertEqual(g.realized,expected);self.assertEqual(len(g.closed_trades),1)
        again=ExecutionGateway(self.store);self.assertEqual(again.realized,expected)
        with self.assertRaises(DataError):again.execute_paper(m,b,'Up','BUY','5','.5',1100000,'paper-1')
    def test_paper_partial_sell_keeps_remaining_position(self):
        g=ExecutionGateway(self.store);g.execute_paper(market(),book(),'Up','BUY','5','.5',1100000,'buy')
        b=book(bids=[{'price':'.6','size':'3'}],asks=[{'price':'.7','size':'10'}])
        g.execute_paper(market(),b,'Up','SELL','5','.5',1100000,'sell')
        self.assertGreater(g.positions['a']['shares'],0);self.assertEqual(g.closed_trades,[])
    def test_split_exit_profitability_uses_all_fills(self):
        g=ExecutionGateway(self.store);g.execute_paper(market(),book(),'Up','BUY','5','.5',1100000,'buy')
        g.execute_paper(market(),book(bids=[{'price':'.8','size':'10'}],asks=[{'price':'.9','size':'10'}]),'Up','SELL','5','.5',1100000,'sell1')
        g.execute_paper(market(),book(bids=[{'price':'.1','size':'100'}],asks=[{'price':'.2','size':'100'}]),'Up','SELL','5','.1',1100000,'sell2')
        self.assertEqual(len(g.closed_trades),1);self.assertGreater(g.closed_trades[0]['pnl'],0);self.assertEqual(g.closed_trades[0]['pnl'],g.realized)
    def test_risk_limits_pause_and_stale(self):
        g=ExecutionGateway(self.store);g.paused=True
        with self.assertRaises(DataError):g.execute_paper(market(),book(),'Up','BUY','5','.5',1100000,'1')
        g.paused=False
        with self.assertRaises(DataError):g.execute_paper(market(),book(),'Up','BUY','5','.5',1104000,'2')
        with self.assertRaises(DataError):g.execute_paper(market(),book(at=1290000),'Up','BUY','5','.5',1290000,'3')
    def test_settings_cannot_enable_live(self):
        s=Settings(self.root);s.data['mode']='live';s.data['live_enabled']=True;s.save();s=Settings(self.root)
        self.assertEqual(s.data['mode'],'monitor');self.assertFalse(s.data['live_enabled'])
    def test_dpapi_roundtrip_no_plaintext(self):
        if os.name!='nt':self.skipTest('Windows-only DPAPI')
        secret=SecretStore(self.root);secret.save('test','test-only-not-a-real-key')
        self.assertEqual(secret.get('test'),'test-only-not-a-real-key');self.assertNotIn(b'test-only',secret._path('test').read_bytes())
    def test_advice_revision_does_not_rewrite_facts(self):
        self.store.append('test.fact',{'value':1});self.store.flush();original=[p.read_bytes() for p in (self.root/'log/events').rglob('*') if p.is_file()]
        advice=AIAdviceStore(self.root);advice.create('2026-10-22/a.md','first');advice.revise('2026-10-22/a.md','second')
        self.assertEqual(original,[p.read_bytes() for p in (self.root/'log/events').rglob('*') if p.is_file()]);self.assertTrue(list((self.root/'ai log/versions').glob('*')))
    def test_hardlink_rejected(self):
        advice=AIAdviceStore(self.root);path=advice.create('2026-10-22/a.md','first');os.link(path,self.root/'other.md')
        with self.assertRaises(ValueError):advice.revise('2026-10-22/a.md','bad')
    def test_worker_cannot_double_start(self):
        from poly5.locking import WorkerLock
        with WorkerLock(self.root):
            with self.assertRaises(RuntimeError):WorkerLock(self.root)
        with WorkerLock(self.root):pass
    def test_runtime_event_schema(self):
        import jsonschema
        self.store.append('account.snapshot',{'value':None},account_id='test');self.store.flush()
        schema=json.loads((ROOT/'contracts/runtime_event.schema.json').read_text(encoding='utf-8'))
        jsonschema.validate(list(self.store.all())[0],schema)

class AccountApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_5000_row_cursor_pagination(self):
        class Fake:
            def __init__(self):self.calls=[]
            async def get(self,url,params):
                self.calls.append(dict(params));start=int(params.get('cursor','0'))
                return {'data':[{'proxy_wallet':'x','condition_id':'c','token_id':str(i)} for i in range(start,start+100)],'pagination':{'next_cursor':str(start+100) if start<4900 else None,'has_more':start<4900}}
        api=Fake();result=await PublicAccountAdapter(api).pages('positions',{'user':'x','status':'OPEN','limit':100})
        self.assertEqual(len(result),5000);self.assertEqual(len(api.calls),50);self.assertTrue(all(p['user']=='x' and p['status']=='OPEN' and 'offset' not in p for p in api.calls))
    async def test_cursor_loop_fails_atomic_snapshot(self):
        class Fake:
            async def get(self,*args):return {'data':[],'pagination':{'next_cursor':'same','has_more':True}}
        with self.assertRaises(DataError):await PublicAccountAdapter(Fake()).pages('positions',{'user':'x'})
    async def test_v1_response_rejected(self):
        class Fake:
            async def get(self,*args):return []
        with self.assertRaises(DataError):await PublicAccountAdapter(Fake()).pages('positions',{})
    async def test_value_is_not_cash(self):
        address='0x'+'1'*40
        class Fake:
            async def get(self,url,params):
                if url.endswith('/positions'):return {'data':[],'pagination':{'next_cursor':None,'has_more':False}}
                return {'data':{'proxy_wallet':address,'value':D('123.45')}}
        s=await PublicAccountAdapter(Fake()).snapshot(address)
        self.assertEqual(s['position_value'],D('123.45'));self.assertIsNone(s['cash']);self.assertIsNone(s['available']);self.assertIsNone(s['equity'])
    async def test_auth_has_fixed_get_allowlist(self):
        adapter=AuthReadAdapter(None)
        with self.assertRaises(PermissionError):adapter.headers('/order')
    async def test_ai_snapshot_has_no_account_identifiers(self):
        s=safe_snapshot({'account':{'wallet':'0x'+'1'*40,'secret':'secret-value'},'active_account':{'address':'0x'+'2'*40},'geo':{'ip':'1.2.3.4','country':'XX'}})
        self.assertNotIn('secret-value',dumps(s));self.assertNotIn('0x'+'1'*40,dumps(s));self.assertNotIn('1.2.3.4',dumps(s))

if __name__=='__main__':unittest.main()
