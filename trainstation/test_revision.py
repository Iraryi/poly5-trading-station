"""Regression tests for 1.9.1 boundary, reporting, liveness and handoff tooling."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from .test_dynamic import trajectory,book,x
from .dynamic import execute_path,canonical_episodes,DATA_REVISION,action
from .dynamic_dataset import build_episodes,backfill
from .dynamic_forward import DynamicForward,forward_summary
from .data import Data
from .gui import Window
from .runner import review_with_heartbeat
from poly5.domain import DataError
from poly5.domain import Fee
from decimal import Decimal as D

class RevisionTests(unittest.TestCase):
    def test_quote_at_expiry_cannot_fill_pending_profit_exit(self):
        row=trajectory([(239,.03),(240,.04)],terminal=-1.)
        result=execute_path(row,[.01,.01])
        self.assertEqual(result['reason'],'official_settlement');self.assertEqual(result['roi'],-1.)
    def test_quote_after_expiry_does_not_create_a_new_exit(self):
        row=trajectory([(238,-.02),(241,.5)],terminal=-1.)
        self.assertEqual(execute_path(row,[.01,.01])['reason'],'official_settlement')
    def test_training_view_trims_boundary_without_mutating_original(self):
        row=trajectory([(237,.01),(238,.02),(239,.03),(240,.5)])
        row['decision_ms']=59000;before=copy.deepcopy(row)
        corrected=canonical_episodes([row])
        self.assertEqual(row,before);self.assertEqual(corrected[0]['aux']['path'][-1]['at_ms'],299000)
        self.assertEqual(corrected[0]['aux']['data_revision'],DATA_REVISION)
        self.assertEqual(canonical_episodes(corrected),corrected)
    def test_no_long_gap_is_reintroduced_by_projection(self):
        row=trajectory([(235,.02),(239,.03)]);row['decision_ms']=59000
        self.assertEqual(canonical_episodes([row]),[])
    def test_live_and_historical_collector_never_create_post_expiry_state(self):
        meta={'slug':'s','start_s':0,'end_s':300,'fee_rate':'.07','fee_exponent':1,'tick':'.01','min_size':5,
              'winner':'Up','condition_id':'c','up_token':'u','down_token':'d','verified_ms':310000}
        frames=[book(t) for t in range(270000,301000,1000)]
        rows,c=build_episodes(meta,frames,lambda now:({**x(),'stage_fraction':now/300000},{}))
        self.assertTrue(rows);self.assertTrue(all(p['at_ms']<300000 for r in rows for p in r['aux']['path']))
        self.assertEqual(c['outside_market_frames'],1)
    def test_open_censored_position_is_not_labelled_as_no_trade(self):
        with tempfile.TemporaryDirectory() as tmp,patch('trainstation.dynamic_forward.freeze_priors',return_value={}):
            d=Data(Path(tmp));f=DynamicForward(d)
            f.save(('m_FULL','s'),{'status':'holding','censored':True,'model':{'id':'m_FULL'},'market':{'slug':'s'},'created_ms':1,
                                  'position':{'total':'5.1','shares':'10'},'trace':[]})
            paper=forward_summary(d);label=Window.money(paper)
            self.assertEqual(paper['holding'],1);self.assertEqual(paper['censored_open'],1);self.assertEqual(paper['censored'],1)
            self.assertEqual(paper['trades'],0);self.assertNotIn('未出手',label)
            self.assertIn('持仓 1',label);self.assertIn('观测缺段 1',label)
    def test_pending_entry_is_distinct_from_filled_position(self):
        paper={'markets':1,'trades':0,'policy_kind':'dynamic','open':1,'pending_entries':1,'entered':0}
        self.assertIn('待成交 1',Window.money(paper));self.assertNotIn('未出手',Window.money(paper))
    def test_slow_supervisor_keeps_heartbeat_and_stop_status(self):
        publications=[]
        def review(*_):time.sleep(.6);return {'status':'done'}
        result=review_with_heartbeat(review,None,None,None,None,lambda **kw:publications.append(kw),lambda:True)
        self.assertEqual(result['status'],'done');self.assertGreaterEqual(len(publications),2)
        self.assertTrue(all('等待已有 AI 请求' in p['phase'] for p in publications))
    def test_supervisor_exception_is_not_silently_swallowed(self):
        def review(*_):raise ValueError('fixture')
        with self.assertRaisesRegex(ValueError,'fixture'):
            review_with_heartbeat(review,None,None,None,None,lambda **_:None,lambda:False)
    def test_stopped_backfill_does_not_create_files_or_network_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Data(Path(tmp));stop=threading.Event();stop.set()
            with patch('poly5.api.PublicAPI') as api:
                result=asyncio.run(backfill(d,stop_event=stop));api.assert_not_called()
            self.assertTrue(result['stopped']);self.assertFalse((Path(tmp)/'dynamic').exists())
    def test_nonfinite_predictions_cannot_select_exit(self):
        for value in (float('nan'),float('inf'),float('-inf')):
            with self.assertRaises(DataError):action(x(.02),value)
    def test_one_broken_model_does_not_abort_market_collection_or_erase_position(self):
        with tempfile.TemporaryDirectory() as tmp,patch('trainstation.dynamic_forward.freeze_priors',return_value={}):
            data=Data(Path(tmp));f=DynamicForward(data);meta={'id':'m','model_path':'fixture'}
            s={'status':'holding','model':meta,'market':{'slug':'s'},'last_ms':64000,'peak':0.,'censored':False,'side':'Up',
               'position':{'total':'5.1','cost':'5','shares':'10','entry_ms':61000},'trace':[]}
            f.states['m','s']=s
            market=SimpleNamespace(slug='s',condition_id='c',start=0,end=300,outcomes={'Up':'u','Down':'d'},
                tick=D('.01'),min_size=D(5),fee=Fee(D('.07'),1))
            with patch.object(f,'predict',side_effect=DataError('fixture bad model')):
                f.tick(market,{'Up':book(65000),'Down':book(65000)},x(),{},[],65000)
            self.assertEqual(s['status'],'holding');self.assertIn('position',s);self.assertTrue(s['censored'])
            self.assertIn('fixture bad model',s['model_error'])
    def test_manifest_detects_changed_payload(self):
        from scripts.trainer_release import verify,sha
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'fixture.bin';p.write_bytes(b'original')
            (root/'manifest.json').write_text(json.dumps({'version':'test','files':{'fixture.bin':{'sha256':sha(p)}}}),encoding='utf-8')
            self.assertTrue(verify(root)['passed']);p.write_bytes(b'changed');self.assertFalse(verify(root)['passed'])
    def test_diagnostic_does_not_modify_database_or_config(self):
        from scripts.diagnose_trainer import collect
        from scripts.trainer_release import sha
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);Data(root);p=root/'config.json';p.write_text('{"ai":{"secret":"fixture-not-for-report","interval_minutes":30}}',encoding='utf-8')
            before=(sha(root/'training.sqlite'),sha(p));report=collect(root)
            self.assertEqual(before,(sha(root/'training.sqlite'),sha(p)))
            self.assertNotIn('fixture-not-for-report',json.dumps(report));self.assertEqual(report['ai']['interval_minutes'],30)

if __name__=='__main__':unittest.main()
