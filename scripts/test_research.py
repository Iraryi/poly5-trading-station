import unittest
from decimal import Decimal as D
from research_execution import buy,liquidate,policy_exit,fee
from research_features import asof_index,split_for
from datetime import datetime,timezone
import numpy as np

def state(bid=.5,ask=.51,depth=100):
    return {'valid':True,'bid_prices':[bid],'bid_sizes':[depth],'ask_prices':[ask],'ask_sizes':[depth]}

class ResearchSafety(unittest.TestCase):
    def test_budget_includes_fee(self):
        p=buy(state(),D('.52'))
        self.assertLessEqual(p['total'],D(5));self.assertGreater(p['fees'],0)
        self.assertLess(p['shares'],D(5)/D('.51'))
    def test_unchanged_price_loses_spread_and_both_fees(self):
        p=buy(state(),D('.52'));v=liquidate(state(),p)
        self.assertLess(v['roi'],0);self.assertGreater(v['exit_fees'],0)
    def test_rising_bid_can_still_have_negative_net_roi(self):
        p=buy(state(bid=.5,ask=.51),D('.52'))
        self.assertLess(liquidate(state(bid=.52,ask=.53),p)['roi'],0)
    def test_exit_sweeps_depth_instead_of_marking_all_at_top(self):
        p=buy(state(),D('.52'));s=state(bid=.6)
        s['bid_prices']=[.6,.4];s['bid_sizes']=[1,100]
        v=liquidate(s,p)
        self.assertLess(v['pnl'],0)
        self.assertEqual(v['gross'],D('.6')+(p['shares']-1)*D('.4'))
    def test_minimum_shares_and_limit(self):
        self.assertIsNone(buy(state(depth=4),D('.52')))
        self.assertIsNone(buy(state(ask=.54),D('.52')))
    def test_depth_shortage_is_not_full_exit(self):
        p=buy(state(),D('.52'));self.assertIsNone(liquidate(state(depth=1),p))
    def test_delayed_exit_can_lose_after_profit_trigger(self):
        p=buy(state(),D('.52'))
        v=policy_exit({1_300_000:state(bid=.6),1_600_000:state(bid=.4)},p,D('.01'),D('-.05'),1,.3,0)
        self.assertEqual(v['reason'],'take_profit');self.assertLess(v['pnl'],0)
    def test_unknown_exit_not_counted_profit(self):
        p=buy(state(),D('.52'));v=policy_exit({},p,D('.01'),D('-.05'),1,.3,0)
        self.assertEqual(v['roi'],D(-1));self.assertEqual(v['reason'],'exit_unavailable_loss_bound')
    def test_asof_excludes_future(self):
        self.assertEqual(asof_index(np.array([10,20,30]),19),0)
        self.assertEqual(asof_index(np.array([10,20,30]),5),-1)
    def test_boundary_purge(self):
        t=int(datetime(2026,8,20,tzinfo=timezone.utc).timestamp())
        self.assertEqual(split_for(t),'purged');self.assertEqual(split_for(t+600),'test')

if __name__=='__main__':unittest.main(verbosity=2)
