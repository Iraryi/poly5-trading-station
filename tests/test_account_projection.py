import sys
import unittest
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from account_projection import (
    AccountDataError, AccountSnapshot, OrderProjection, OrderUpdate,
    can_submit_live, amount,
)

class AccountTests(unittest.TestCase):
    def sample(self, **overrides):
        data=dict(account_id='acctA', mode='monitor', cash=None, available_to_buy=None,
                  reserved=None, position_value=D('5'), observed_at_utc='2026-10-22T00:00:00Z',
                  cash_verified=False, available_verified=False, position_value_verified=True,
                  comparable_sources=False)
        data.update(overrides)
        return AccountSnapshot(**data)

    def test_missing_cash_not_zero(self):
        s=self.sample()
        self.assertIsNone(s.displayed_cash)
        self.assertIsNone(s.displayed_available)
        self.assertIsNone(s.estimated_equity)
        self.assertEqual(s.displayed_position_value,D('5'))

    def test_unverified_amount_stays_unknown(self):
        s=self.sample(cash=D('80'), available_to_buy=D('70'))
        self.assertIsNone(s.displayed_cash)
        self.assertIsNone(s.displayed_available)

    def test_equity_only_if_explicitly_comparable(self):
        s=self.sample(cash=D('10'),cash_verified=True,comparable_sources=True)
        self.assertEqual(s.estimated_equity,D('15'))
        self.assertIsNone(self.sample(cash=D('10'),cash_verified=True).estimated_equity)

    def test_reserved_not_counted_twice(self):
        s=self.sample(cash=D('10'),cash_verified=True,reserved=D('4'),comparable_sources=True)
        self.assertEqual(s.estimated_equity,D('15'))

    def test_float_and_negative_rejected(self):
        with self.assertRaises(AccountDataError): amount(5.0)
        with self.assertRaises(AccountDataError): amount('-0.01')
        with self.assertRaises(AccountDataError): amount('NaN')
        self.assertIsNone(amount(None))

    def event(self, event='e1', status='open', filled='0', account='acctA', total='10', source='user_ws'):
        return OrderUpdate(event, account,'ord01',status,D(filled),D(total),source)

    def test_partial_fill_and_dedup(self):
        p=OrderProjection('acctA','ord01')
        self.assertTrue(p.apply(self.event()))
        self.assertTrue(p.apply(self.event('e2','partial','4')))
        self.assertFalse(p.apply(self.event('e2','partial','4')))
        self.assertEqual(p.cumulative_filled,D('4'))
        self.assertEqual(p.unfilled_shares,D('6'))
        self.assertTrue(p.live_order)

    def test_stale_open_cannot_erase_fill(self):
        p=OrderProjection('acctA','ord01')
        p.apply(self.event('e1','partial','7'))
        p.apply(self.event('e2','open','0',source='rest'))
        self.assertEqual(p.status,'partial')
        self.assertEqual(p.cumulative_filled,D('7'))

    def test_cancel_remaining_doesnt_erase_fill(self):
        p=OrderProjection('acctA','ord01')
        p.apply(self.event('e1','partial','4'))
        p.apply(self.event('e2','canceled','4'))
        self.assertEqual(p.status,'canceled')
        self.assertEqual(p.cumulative_filled,D('4'))
        self.assertEqual(p.unfilled_shares,D('6'))
        self.assertFalse(p.live_order)
        p.apply(self.event('e3','open','0'))
        self.assertEqual(p.status,'canceled')

    def test_confirmed_full_fill_trumps_cancel(self):
        p=OrderProjection('acctA','ord01')
        p.apply(self.event('e1','canceled','6'))
        p.apply(self.event('e2','filled','10'))
        self.assertEqual(p.status,'filled')

    def test_cross_account_denied(self):
        p=OrderProjection('acctA','ord01')
        with self.assertRaises(AccountDataError):
            p.apply(self.event(account='acctB'))

    def test_inconsistent_order_denied(self):
        p=OrderProjection('acctA','ord01')
        p.apply(self.event())
        with self.assertRaises(AccountDataError):
            p.apply(self.event(event='e2',total='11'))

    def test_real_gate_requires_everything(self):
        settings=dict(user_explicitly_enabled=True,geo_allowed=True,authenticated=True,
                      balances_verified=True,order_state_known=True,market_fresh=True,
                      risk_checks_pass=True,adapter_verified=True)
        self.assertTrue(can_submit_live(**settings))
        for field in settings:
            bad=dict(settings)
            bad[field]=False
            self.assertFalse(can_submit_live(**bad), field)

if __name__=='__main__':unittest.main()
