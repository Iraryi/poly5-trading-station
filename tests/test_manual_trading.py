import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock
from poly5.common import D
from poly5.domain import Book, DataError, Fee
from poly5.live_ledger import LiveLedger
from poly5.live_service import LiveService


def service(tmp, now=1_000_000, mode='quick'):
    a={'id':'named','address':'0x'+'1'*40}
    up=Book('up','c');up.snapshot({'asset_id':'up','market':'c','timestamp':str(now),'bids':[{'price':'.59','size':'100'}],'asks':[{'price':'.60','size':'100'}]},now)
    market=NS(id='m',condition_id='c',outcomes={'Up':'up','Down':'down'},accepting=True,fee=Fee(D('.07'),1),start=now//1000-100,end=now//1000+100,
              min_size=D(5),tick=D('.01'))
    resolution={'condition_id':'c','closed':True,'tokens':[{'token_id':'up','winner':False},{'token_id':'down','winner':True}]}
    e=NS(store=NS(error=None),api=NS(clock=NS(now=lambda:now),get=AsyncMock(return_value=resolution)),market=market,books={'up':up},
         settings=NS(data={'ui_mode':mode,'allowed_country':'XX'}))
    s=LiveService(e);s.account=a;s.ledger=LiveLedger(tmp,a);s.gates=lambda *args,**kw:None
    s.exchange=NS(cash=AsyncMock(return_value={'available':'27.00'}),token_balance=AsyncMock(return_value=D(0)))
    return s


class SettlementTests(unittest.IsolatedAsyncioTestCase):
    def position(self,s,asset):
        pos={'asset_id':asset,'condition_id':'c','market_id':'old','end_s':1,'outcome':'Up' if asset=='up' else 'Down','baseline_point':None,'shares':'16.9','basis':'6.6'}
        s.ledger.commit('seed',{},'seed',{'position':pos});return pos
    async def test_losing_position_is_booked_as_realized_loss(self):
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            s=service(tmp);await s.settle_expired(self.position(s,'up'))
            self.assertIsNone(s.ledger.get('position'));self.assertEqual(D(s.ledger.get('realized')),D('-6.6'));s.ledger.close()
    async def test_winning_position_waits_for_redeem_then_books_payout(self):
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            s=service(tmp);pos=self.position(s,'down');s.exchange.token_balance.return_value=D('16.9')
            with self.assertRaises(DataError):await s.settle_expired(pos)
            self.assertIsNotNone(s.ledger.get('position'))
            s.exchange.token_balance.return_value=D(0);await s.settle_expired(pos)
            self.assertIsNone(s.ledger.get('position'));self.assertEqual(D(s.ledger.get('realized')),D('10.3'));s.ledger.close()
    async def test_unresolved_market_keeps_waiting(self):
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            s=service(tmp);s.e.api.get.return_value={'condition_id':'c','closed':False,'tokens':[]}
            with self.assertRaises(DataError):await s.settle_expired(self.position(s,'up'))
            self.assertIsNotNone(s.ledger.get('position'));s.ledger.close()


class ManualOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_tap_buy_submits_manual_whole_cent_order(self):
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            s=service(tmp);s.submit=AsyncMock()
            r=await s.manual_buy('Up','5')
            args,kw=s.submit.call_args
            self.assertTrue(kw.get('manual'));self.assertEqual(args[3],'BUY');self.assertEqual((args[8]*100)%1,0);self.assertLessEqual(args[8],D(5))
            self.assertEqual(r['outcome'],'Up');s.ledger.close()
    async def test_buy_refused_when_cash_short_or_other_side_held(self):
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            s=service(tmp);s.submit=AsyncMock();s.exchange.cash.return_value={'available':'3'}
            with self.assertRaises(DataError):await s.manual_buy('Up','5')
            s.exchange.cash.return_value={'available':'27'}
            s.ledger.commit('seed',{},'seed',{'position':{'asset_id':'down','condition_id':'c','market_id':'m','end_s':9,'outcome':'Down','shares':'5','basis':'2'}})
            with self.assertRaises(DataError):await s.manual_buy('Up','5')
            s.submit.assert_not_awaited();s.ledger.close()
    async def test_cash_out_sells_whole_position_one_tick_below_bid(self):
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            s=service(tmp);s.submit=AsyncMock()
            s.ledger.commit('seed',{},'seed',{'position':{'asset_id':'up','condition_id':'c','market_id':'m','end_s':9,'outcome':'Up','shares':'8.0722','basis':'5'}})
            r=await s.manual_sell();args,kw=s.submit.call_args
            self.assertEqual((args[3],args[4],args[5]),('SELL',D('.58'),D('8.07')));self.assertTrue(kw.get('manual'));s.ledger.close()


class AutoArmTests(unittest.IsolatedAsyncioTestCase):
    def armed_service(self,tmp,mode='quick'):
        s=service(tmp,mode=mode);s.bind=AsyncMock();s.exchange.armed=False;return s
    async def test_requires_prior_user_confirmation(self):
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            s=self.armed_service(tmp)
            with self.assertRaises(DataError):await s.auto_arm_manual()
            self.assertFalse(s.armed)
            s.ledger.commit('user.armed',{'eligibility':'XX/adult/user-attested'},'armed-1',{'capital':{'stop_latched':False}})
            await s.auto_arm_manual()
            self.assertTrue(s.armed);self.assertFalse(s.entries);self.assertTrue(s.exchange.armed)
            kinds=[h['kind'] if isinstance(h,dict) else h[1] for h in s.ledger.history(5)]
            self.assertIn('user.auto_armed_manual',kinds);s.ledger.close()
    async def test_never_auto_arms_terminal_mode_or_latched_stop(self):
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            s=self.armed_service(tmp,mode='terminal')
            s.ledger.commit('user.armed',{},'armed-1',{'capital':{'stop_latched':False}})
            await s.auto_arm_manual();self.assertFalse(s.armed);s.ledger.close()
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            s=self.armed_service(tmp)
            s.ledger.commit('user.armed',{},'armed-1',{'capital':{'stop_latched':True}})
            with self.assertRaises(DataError):await s.auto_arm_manual()
            self.assertFalse(s.armed);s.ledger.close()


class RegionSettingTests(unittest.TestCase):
    def test_live_gate_requires_a_configured_region_and_never_defaults(self):
        s=LiveService(NS(settings=NS(data={})))
        with self.assertRaises(DataError):s.region()
        s.e.settings.data['allowed_country']='x1'
        with self.assertRaises(DataError):s.region()
        s.e.settings.data['allowed_country']='xx';self.assertEqual(s.region(),'XX')

if __name__=='__main__':unittest.main()
