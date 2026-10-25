"""Behavioural tests for dynamic exit, causal replay and preserved training contracts."""
from decimal import Decimal as D
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from .data import ACTIVE_TASKS,TASKS,Data,design
from .dynamic import action,execute_path,PROFIT_FLOOR,buy,sell_roi
from .dynamic_dataset import build_episodes
from .dynamic_fit import split_episodes
from .dynamic_forward import DynamicForward
from poly5.domain import DataError,Fee

def x(roi=0.,holding=0.,distance=10.,ret5=0.,can_sell=1.):
    f={k:0. for k in TASKS['dynamic'][1]}
    f.update(sell_roi=roi,can_sell=can_sell,holding_s=holding,spot_open_bps=distance,
             vol_60s_bps=.5,stage_fraction=.5,side_sign=1.,return_5s_bps=ret5,entry_price=.5,entry_cost=5.1,position_shares=10.)
    return f

def trajectory(points,terminal=.2):
    return {'slug':'s','start_s':0,'decision_ms':60000,'ready_ms':310000,'y':terminal,'x':x(),
            'aux':{'entry_ms':60000,'entry_cost':5.1,'side':'Up',
                   'path':[{'at_ms':60000+int(sec*1000),'x':x(roi,sec)} for sec,roi in points]}}

def book(now,bid=.5,ask=.51,depth=100.):
    return {'available_ms':now,'source_ms':now,'received_ms':now,'initialized':True,'quality':'OK','outcome':'Up',
            'bid_prices':[bid],'bid_sizes':[depth],'ask_prices':[ask],'ask_sizes':[depth]}

class DynamicTests(unittest.TestCase):
    def test_no_exit_timer_at_five_fifteen_thirty_or_ninety_seconds(self):
        for t in (5,15,30,90,200):self.assertEqual(action(x(-.03,t),.05),'hold')
    def test_profitable_opportunity_sells_when_continuation_is_lower(self):
        self.assertEqual(action(x(.025,47),.01),'take_profit')
        self.assertEqual(action(x(.025,47),.04),'hold')
    def test_temporary_negative_pnl_is_not_stop_loss(self):
        self.assertEqual(action(x(-.20,40,distance=50,ret5=-1),-.5),'hold')
    def test_only_sharp_reversal_with_lost_distance_allows_risk_exit(self):
        self.assertEqual(action(x(-.15,50,distance=0,ret5=-9),.1),'reversal')
        self.assertEqual(action(x(-.15,50,distance=80,ret5=-9),.1),'hold')
        self.assertEqual(action(x(-.15,50,distance=0,ret5=-9,can_sell=0),.1),'hold')
    def test_latency_rechecks_profit_and_does_not_invent_fill(self):
        row=trajectory([(0,.03),(1,-.02),(2,.04),(3,.035)])
        result=execute_path(row,[.01]*4)
        self.assertEqual(result['at_ms'],63000);self.assertEqual(result['roi'],.035)
    def test_unexecutable_profit_does_not_force_loss_or_fixed_deadline(self):
        row=trajectory([(0,.03),(1,-.02),(16,-.02),(40,-.01)],terminal=.12)
        result=execute_path(row,[.01]*4)
        self.assertEqual(result['reason'],'official_settlement');self.assertEqual(result['roi'],.12)
    def test_sell_walks_full_depth_and_deducts_both_fees(self):
        meta={'fee_rate':'.07','fee_exponent':1,'tick':'.01','min_size':'5'}
        f=book(1000,bid=.5,ask=.5);p=buy(f,meta,1000)
        self.assertGreater(D(p['total']),D(5));self.assertLess(sell_roi(f,meta,p,1000),0)
        f['bid_sizes']=[.1];self.assertIsNone(sell_roi(f,meta,p,1000))
        with self.assertRaises(DataError):buy(book(1000,depth=.1),meta,1000)
    def test_stale_quote_is_not_an_executable_return(self):
        meta={'fee_rate':'.07','fee_exponent':1,'tick':'.01','min_size':'5'}
        f=book(1000);p=buy(f,meta,1000)
        self.assertIsNone(sell_roi(f,meta,p,3000))
    def test_whole_market_split_with_embargo_and_audit_not_shared(self):
        rows=[{'slug':str(i),'start_s':i*300} for i in range(100) for _ in range(2)]
        a,b,c=split_episodes(rows)
        self.assertFalse({r['slug'] for r in a}&{r['slug'] for r in b})
        self.assertGreaterEqual(min(r['start_s'] for r in b)-max(r['start_s']+300 for r in a),600)
    def test_old_tasks_stay_readable_but_are_not_default_training_targets(self):
        self.assertIn('roi5',TASKS);self.assertIn('roi15',TASKS)
        self.assertNotIn('roi5',ACTIVE_TASKS);self.assertNotIn('roi15',ACTIVE_TASKS)
        self.assertIn('dynamic',ACTIVE_TASKS);self.assertIn('holding_s',design('dynamic'))
    def test_missing_path_is_censored_not_scored_as_hold(self):
        m={'slug':'s','start_s':0,'end_s':300,'fee_rate':'.07','fee_exponent':1,'tick':'.01','min_size':5,
           'winner':'Up','condition_id':'c','up_token':'u','down_token':'d','verified_ms':310000}
        frames=[book(t) for t in (60000,61000,70000,299000)]
        rows,counts=build_episodes(m,frames,lambda now:({**x(),'stage_fraction':now/300000},{}))
        self.assertFalse(rows);self.assertGreater(counts.get('censored_gap',0),0)
    def test_forward_open_position_survives_restart_and_records_model_identity(self):
        with tempfile.TemporaryDirectory() as tmp,patch('trainstation.dynamic_forward.freeze_priors',return_value={}):
            d=Data(Path(tmp));f=DynamicForward(d)
            state={'status':'holding','model':{'id':'model-A'},'market':{'slug':'s'},'position':{'shares':'10','total':'5.1'},'trace':[]}
            f.save(('model-A','s'),state)
            g=DynamicForward(d)
            self.assertEqual(g.states['model-A','s']['position']['shares'],'10')
            self.assertEqual(g.states['model-A','s']['model']['id'],'model-A')
    def test_closed_forward_position_is_not_reentered_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp,patch('trainstation.dynamic_forward.freeze_priors',return_value={}):
            d=Data(Path(tmp));f=DynamicForward(d)
            f.save(('m','s'),{'status':'closed','model':{'id':'m'},'market':{'slug':'s'},'trace':[]})
            g=DynamicForward(d)
            self.assertIn(('m','s'),g.finished);self.assertNotIn(('m','s'),g.states)

if __name__=='__main__':unittest.main()
