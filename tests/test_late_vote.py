import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from poly5.auto_paper import entry_candidate
from poly5.common import D,atomic_json
from poly5.domain import Book,DataError
from poly5.model_bridge import ModelBridge,VoteRuntime,sha
from poly5.storage import EventStore
from poly5.strategy_report import report
from test_auto_updates import ReferenceFixture
from test_station import market

LAST_MINUTE=1250000

def up_book(ask='.60'):
    b=Book('a','c');b.snapshot({'asset_id':'a','market':'c','timestamp':str(LAST_MINUTE),
        'bids':[{'price':str(D(ask)-D('.01')),'size':'100'}],'asks':[{'price':ask,'size':'100'}]},LAST_MINUTE);return b

def vote(p,at=LAST_MINUTE):return {'probability_up':p,'models':{'x':p},'at_ms':at,'market_id':'m'}

class LateVoteRuleTests(unittest.TestCase):
    def candidate(self,p=.75,ask='.60',now=LAST_MINUTE,v=None):
        return entry_candidate(ReferenceFixture().f,{'Up':up_book(ask)},market(),now,'late_vote',v if v is not None else vote(p,now))
    def test_confident_vote_with_edge_buys_the_reference_side(self):
        c=self.candidate();self.assertEqual(c['outcome'],'Up');self.assertGreaterEqual(D(c['estimated_edge_per_share']),D('.03'))
    def test_insufficient_edge_or_confidence_is_rejected(self):
        with self.assertRaises(DataError):self.candidate(p=.62)
        with self.assertRaises(DataError):self.candidate(p=.52,ask='.40')
    def test_longshot_price_is_never_bought(self):
        with self.assertRaises(DataError):self.candidate(p=.9,ask='.30')
    def test_stale_missing_or_foreign_vote_is_rejected(self):
        with self.assertRaises(DataError):self.candidate(v=vote(.75,LAST_MINUTE-5000))
        with self.assertRaises(DataError):entry_candidate(ReferenceFixture().f,{'Up':up_book()},market(),LAST_MINUTE,'late_vote',None)
        with self.assertRaises(DataError):self.candidate(v={**vote(.75),'market_id':'other'})
    def test_only_last_minute_and_original_rule_keeps_fifty_dollar_distance(self):
        with self.assertRaises(DataError):self.candidate(now=1200000)
        with self.assertRaises(DataError):entry_candidate(ReferenceFixture().f,{'Up':up_book()},market(),LAST_MINUTE,'last_minute_50')

class ReportTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.store=EventStore(self.tmp.name)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def trade(self,n,cost,payout,mode='late_vote',settle=True):
        ex={'id':f'auto-{n}','asset_id':f'a{n}','market_id':f'm{n}','slug':f's{n}','outcome':'Up','side':'BUY','shares':'10','total':str(cost)}
        self.store.append('auto_paper.entry',{'entry_mode':mode,'execution':ex,'plan':{'entry_ms':1000+n}},mode='paper')
        if settle:self.store.append('paper.settlement',{'id':f'settle-{n}','asset_id':f'a{n}','market_id':f'm{n}','side':'SETTLE','shares':'10','total':str(payout)},mode='paper')
    def test_closed_trades_drawdown_and_gate(self):
        self.trade(1,5,10);self.trade(2,5,0);self.trade(3,5,0);self.trade(4,5,10);self.trade(5,5,0,settle=False);self.trade(6,5,10,mode='last_minute_50')
        self.store.flush();r=report(self.store,'late_vote',1,1+3600000)
        self.assertEqual((r['trades'],r['open'],r['wins']),(4,1,2))
        self.assertEqual(D(r['pnl']),D(0));self.assertEqual(D(r['max_drawdown']),D(10));self.assertEqual(D(r['lowest']),D(-5))
        self.assertEqual(r['gate'],'COLLECTING');self.assertEqual(D(r['pnl_per_hour']),D(0))
    def test_drawdown_over_ten_fails_gate_and_open_marks_never_count(self):
        for n in range(3):self.trade(n,5,0)
        self.trade(9,5,0,settle=False);self.store.flush()
        r=report(self.store,'late_vote',1,1000000);self.assertEqual(r['gate'],'FAILED_DRAWDOWN');self.assertEqual(D(r['pnl']),D(-15))

class VotePinTests(unittest.TestCase):
    def setUp(self):
        from sklearn.dummy import DummyClassifier
        import joblib
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);self.trainer=root/'trainer'
        (self.trainer/'models/late').mkdir(parents=True)
        atomic_json(self.trainer/'pass2.json',{'tasks':{'late':{'exam_done':True,'exam_started_ms':10_000,'exam_done_ms':20_000}}})
        conn=sqlite3.connect(self.trainer/'training.sqlite');conn.execute('CREATE TABLE trials(id TEXT,body TEXT,created_ms INTEGER)')
        for i,(score,prior) in enumerate([(.03,.2),(.01,.8),(.05,.5),(.02,.6),(.04,.4),(.06,.5)]):
            p=self.trainer/f'models/late/m{i}.joblib'
            y=[1]*int(prior*10)+[0]*(10-int(prior*10))
            joblib.dump({'model':DummyClassifier(strategy='prior').fit([[0]]*10,y),'features':['stage_fraction'],'task':'late'},p)
            body={'id':f'BTC5M_LATE_FIX_C{i}','task':'late','family':'dummy','status':'complete','features':['stage_fraction'],
                  'model_path':str(p),'model_sha256':sha(p),'metrics':{'score':score,'split':'exam','trained_through_ms':1}}
            conn.execute('INSERT INTO trials VALUES(?,?,?)',(body['id'],json.dumps(body),15_000))
        conn.execute('INSERT INTO trials VALUES(?,?,?)',('old',json.dumps({**body,'id':'OLD','metrics':{'score':0,'split':'replay','trained_through_ms':1}}),15_000))
        conn.commit();conn.close()
        self.bridge=ModelBridge(root/'station',self.trainer)
    def tearDown(self):self.tmp.cleanup()
    def test_pins_exam_top_k_once_without_touching_shadow_selection(self):
        first=self.bridge.pin_vote('late',3)
        self.assertEqual([r['model_id'][-2:] for r in first],['C1','C3','C0']);self.assertEqual([r['exam_rank'] for r in first],[1,2,3])
        self.assertEqual([r['release_id'] for r in self.bridge.pin_vote('late',3)],[r['release_id'] for r in first])
        self.assertIsNone(self.bridge.selected())
    def test_vote_is_the_equal_weight_average_and_rejects_early_stage(self):
        ids=[r['release_id'] for r in self.bridge.pin_vote('late',3)];m=SimpleNamespace(id='m')
        out=VoteRuntime(self.bridge).predict(ids,{'stage_fraction':.9},100,m)
        self.assertAlmostEqual(out['probability_up'],(.8+.6+.2)/3);self.assertEqual(out['scope'],'paper_vote')
        with self.assertRaises(ValueError):VoteRuntime(self.bridge).predict(ids,{'stage_fraction':.5},100,m)
    def test_tampered_release_is_rejected(self):
        r=self.bridge.pin_vote('late',3)[0];(self.bridge.root/'releases'/r['release_id']/'model.joblib').write_bytes(b'x')
        with self.assertRaises(ValueError):VoteRuntime(self.bridge).predict([r['release_id']],{'stage_fraction':.9},100,SimpleNamespace(id='m'))

class LiveRuleParameterTests(unittest.TestCase):
    def test_edge_floor_and_distance_are_the_approved_values(self):
        from poly5.adaptive_capital import initialize,evaluate,policy
        from poly5.live_service import ENTRY_DISTANCE_USD
        self.assertEqual(ENTRY_DISTANCE_USD,D(0))
        cap=initialize('40.00',0,policy('0.20','1'))
        _,view=evaluate(cap,D(0),1);self.assertEqual(D(view['min_edge_per_share']),D('.03'))
        _,view=evaluate(cap,D('-10.00'),1);self.assertEqual(D(view['min_edge_per_share']),D('.04'))  # 25% drawdown -> halfway to .05

class PerformanceTests(unittest.TestCase):
    def test_shared_prediction_runs_once_per_release_market_second(self):
        from poly5.model_bridge import shared_prediction
        calls=[]
        f=lambda:calls.append(1) or .7
        self.assertEqual(shared_prediction('r','m',5_000_100,f),.7);self.assertEqual(shared_prediction('r','m',5_000_900,f),.7)
        self.assertEqual(len(calls),1);shared_prediction('r','m',5_001_000,f);self.assertEqual(len(calls),2)
    def test_models_are_forced_single_threaded(self):
        from sklearn.ensemble import ExtraTreesClassifier
        from sklearn.calibration import CalibratedClassifierCV
        from poly5.model_bridge import single_threaded
        m=CalibratedClassifierCV(ExtraTreesClassifier(n_estimators=3,n_jobs=2),cv=2).fit([[0],[1],[0],[1]],[0,1,0,1])
        single_threaded(m)
        self.assertEqual(m.estimator.n_jobs,1);self.assertTrue(all(c.estimator.n_jobs==1 for c in m.calibrated_classifiers_))
    def test_vote_rule_has_no_noise_gate_like_live(self):
        f=ReferenceFixture().f;f.update(distance_sigma=D('.2'),spot_distance_sigma=D('.2'))
        self.assertEqual(entry_candidate(f,{'Up':up_book()},market(),LAST_MINUTE,'late_vote',vote(.75))['outcome'],'Up')


class BookResyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_rest_is_only_a_fallback_after_three_seconds(self):
        import asyncio
        from unittest.mock import AsyncMock
        from poly5 import worker
        engine_cls=next(v for v in vars(worker).values() if isinstance(v,type) and hasattr(v,'rest_books'))
        book=Book('a','c');calls=[]
        async def rest(asset):calls.append(asset);return {'asset_id':'a','market':'c','timestamp':'1','bids':[],'asks':[]}
        stub=SimpleNamespace(stop=asyncio.Event(),book_resync=asyncio.Event(),market=SimpleNamespace(id='m',condition_id='c'),paused=False,
                             books={'a':book},api=SimpleNamespace(book=rest),event=lambda *a,**k:None,apply_book=lambda item:None)
        task=asyncio.create_task(engine_cls.rest_books(stub))
        stub.book_resync.set();await asyncio.sleep(2.2)
        self.assertEqual(calls,[])               # a WS snapshot may still restore the book
        await asyncio.sleep(1.5)
        self.assertEqual(calls,['a'])            # unusable for 3 s -> one REST fallback
        book.initialized=True;await asyncio.sleep(1.2)
        self.assertEqual(calls,['a'])            # restored -> no periodic REST
        stub.stop.set();await asyncio.wait_for(task,3)

if __name__=='__main__':unittest.main()
