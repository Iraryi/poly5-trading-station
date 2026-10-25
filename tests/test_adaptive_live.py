"""Pure policy / injected exchange tests. No production client or order API."""
import asyncio
import copy
from dataclasses import dataclass
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock
from poly5.common import D,Settings,epoch_ms
from poly5.adaptive_capital import initialize,evaluate,policy,HOUR_MS
from poly5.adaptive_models import initial_pool,register_prediction,blend,hourly_weights
from poly5.live_ledger import LiveLedger
from poly5.live_exchange import LiveExchange,buy_plan
from poly5.live_service import LiveService
from poly5.domain import Book,Fee,DataError
from poly5.account_connection import slot,save,load

class CapitalTests(unittest.TestCase):
    def test_profit_waits_an_hour_losses_never_lower_baseline(self):
        state=initialize('100',100)
        state,r=evaluate(state,'20',HOUR_MS-1);self.assertEqual(r['baseline'],'100')
        state,r=evaluate(state,'20',HOUR_MS+100);self.assertEqual(r['baseline'],'120')
        state,r=evaluate(state,'-10',2*HOUR_MS+100);self.assertEqual(r['baseline'],'120');self.assertEqual(r['equity'],'90')
        self.assertEqual(r['drawdown'],'0.25');self.assertEqual(r['allocation'],'0.0250')
    def test_size_shrinks_and_never_forces_minimum(self):
        state=initialize('100',0)
        for pnl,fraction in [('0','.10'),('-10','.064'),('-25','.025')]:
            _,r=evaluate(state,pnl,1);self.assertEqual(D(r['allocation']),D(fraction))
        self.assertFalse(r['new_risk_allowed']);self.assertLess(D(r['all_in_budget']),D(5))
    def test_fifty_percent_latches_across_recovery_and_restart(self):
        state,r=evaluate(initialize('100',0),'-50',1);self.assertTrue(r['stop_latched'])
        state,r=evaluate(state,'100',HOUR_MS);self.assertFalse(r['new_risk_allowed']);self.assertTrue(state['stop_latched'])
    def test_missing_marks_do_not_checkpoint_or_become_zero(self):
        initial=initialize('100',0);state,r=evaluate(initial,None,HOUR_MS,verified=False)
        self.assertEqual(state,initial);self.assertIsNone(r['equity']);self.assertFalse(r['new_risk_allowed'])
        state,r=evaluate(state,'30',HOUR_MS+100);self.assertEqual(state['baseline'],'130')
    def test_hourly_profit_increase_is_only_equity_input(self):
        # No wallet cash / deposits argument exists. Fees and realized PnL come from bot ledger.
        state=initialize('100',0);state,r=evaluate(state,'0',HOUR_MS)
        self.assertEqual(state['baseline'],'100')
    def test_invalid_policy_and_float_money_rejected(self):
        for value in ('0','1.1','NaN'):
            with self.assertRaises(ValueError):policy(value)
        with self.assertRaises(ValueError):initialize(100.0,0)
    def test_long_disconnect_does_not_invent_intermediate_checkpoints(self):
        state,r=evaluate(initialize('100',0),'5',10*HOUR_MS+1)
        self.assertEqual(state['checkpoints'],1);self.assertEqual(state['next_checkpoint_ms'],11*HOUR_MS)

class WeightTests(unittest.TestCase):
    def pool(self):return initial_pool([{'task':'direction','release_id':'a'},{'task':'direction_mkt','release_id':'b'}])
    def test_different_targets_and_duplicate_versions_rejected(self):
        for tasks in (('direction','roi5'),('direction','late')):
            with self.assertRaises(ValueError):initial_pool([{'task':tasks[0],'release_id':'a'},{'task':tasks[1],'release_id':'b'}])
        with self.assertRaises(ValueError):initial_pool([{'task':'direction','release_id':'a'}]*2)
    def test_common_samples_only_and_no_hindsight_overwrite(self):
        p=self.pool();outputs=[{'release_id':'a','probability_up':.7},{'release_id':'b','probability_up':.6}]
        sample=register_prediction(p,'market',outputs,1,300);p['predictions']['market']=sample
        self.assertIsNone(register_prediction(p,'market',outputs,2,300))
        with self.assertRaises(ValueError):blend(p,outputs[:1])
        prob,_=blend(p,outputs);self.assertEqual(prob,D('.65'))
    def test_twenty_resolutions_required_step_and_ratio_bounded(self):
        p=self.pool()
        for i in range(19):p['predictions'][str(i)]={'probabilities':{'a':'1','b':'0'}};p['results'][str(i)]={'up':1}
        self.assertIsNone(hourly_weights(p))
        p['predictions']['19']={'probabilities':{'a':'1','b':'0'}};p['results']['19']={'up':1}
        r=hourly_weights(p);self.assertEqual(D(r['weights']['a']),D('.56'))
        self.assertLessEqual(D(r['weights']['a'])/D(r['weights']['b']),4)
        p.update(weights=r['weights'],last_weight_samples=r['common_markets']);self.assertIsNone(hourly_weights(p))

class LedgerTests(unittest.TestCase):
    def test_idempotent_transaction_and_restart_preserves_peak(self):
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            account={'id':'main-account','address':'0x'+'1'*40};ledger=LiveLedger(tmp,account)
            state=initialize('100',0);state,_=evaluate(state,'20',HOUR_MS)
            self.assertTrue(ledger.commit('checkpoint',state,'one',{'capital':state}))
            self.assertFalse(ledger.commit('checkpoint',{},'one',{'capital':initialize('1',0)}));ledger.close()
            ledger=LiveLedger(tmp,account);self.assertEqual(ledger.get('capital')['baseline'],'120');self.assertEqual(len(ledger.history()),2);ledger.close()
    def test_named_account_slot_safe_and_uuid_compatible(self):
        self.assertLessEqual(len(slot('main-account')),40)
        self.assertEqual(slot('a'*32),'clob-'+'a'*32)
        with self.assertRaises(ValueError):slot('../wallet')

class LiveAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_pages_are_flattened_and_truncation_blocks_cash(self):
        from poly5.sdk_pages import collect
        async def pages():
            yield NS(items=(),limit_reached=False)
            yield NS(items=('one','two'),limit_reached=False)
        self.assertEqual(await collect(pages()),['one','two'])
        async def truncated():yield NS(items=('one',),limit_reached=True)
        with self.assertRaises(DataError):await collect(truncated())
    async def test_disabled_execution_cannot_even_create_secure_client(self):
        factory=AsyncMock();x=LiveExchange(None,{},factory)
        with self.assertRaises(PermissionError):await x.send_once(None)
        with self.assertRaises(PermissionError):await x.sign('asset','BUY',D('.5'),D(10))
        with self.assertRaises(PermissionError):await x.cancel_owned('x')
        factory.assert_not_called()
    async def test_transport_timeout_is_never_retried(self):
        client=NS(post_order=AsyncMock(side_effect=asyncio.TimeoutError))
        x=LiveExchange(None,{},AsyncMock(return_value=client));x.armed=True
        with self.assertRaises(asyncio.TimeoutError):await x.send_once(NS())
        self.assertEqual(client.post_order.await_count,1)
    async def test_research_settings_and_model_cannot_arm(self):
        fake=NS(root=None);service=LiveService(fake)
        self.assertFalse(service.armed);await service.step();self.assertIsNone(service.exchange)
    async def test_full_cash_authorization_uses_shown_amount_and_old_baseline(self):
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            settings=Settings(tmp);a={'id':'named','address':'0x'+'1'*40};settings.data.update(accounts=[a],active_account_id='named',allowed_country='XX')
            ex=NS(armed=False,cash=AsyncMock(return_value={'available':'100','cash':'100'}),close=AsyncMock())
            e=NS(root=Path(tmp),settings=settings,store=NS(error=None),offline=False,paused=False,stop=asyncio.Event(),
                api=NS(clock=NS(valid=lambda:True,now=epoch_ms)),geo={'blocked':False,'country':'XX','checked_ms':epoch_ms()},
                market=NS(outcomes={'Up':'a'}),model_bridge=NS(release=lambda rid:{'release_id':rid,'model_id':rid,'task':'direction'}))
            s=LiveService(e,lambda *args:ex);p=await s.prepare(['model'],'0.10','5')
            with self.assertRaises(DataError):await s.arm(p['token'],True,'wrong')
            await s.arm(p['token'],True,'启用真钱交易');self.assertTrue(s.armed)
            state=s.ledger.get('capital');state,_=evaluate(state,'20',state['next_checkpoint_ms'])
            s.commit('checkpoint',state,{'capital':state});await s.halt()
            p=await s.prepare(['model'],'0.05','5');self.assertEqual(p['capital']['baseline'],'120')
            ex.cash.return_value={'available':'101','cash':'101'}
            with self.assertRaises(DataError):await s.arm(p['token'],True,'启用真钱交易')
            self.assertFalse(s.armed);await s.close()
    async def test_confirmed_trade_only_no_acceptance_profit_and_partial_idempotency(self):
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            a={'id':'named','address':'0x'+'1'*40};s=LiveService(NS(store=NS(error=None)))
            s.account=a;s.ledger=LiveLedger(tmp,a)
            pending={'order_id':'order','asset_id':'asset','condition_id':'condition','side':'BUY','price':'.5','shares':'10','max_cost':'5.18',
                'market_id':'market','end_s':300,'outcome':'Up','baseline_point':None,'fee_rate':'.07','fee_exponent':1,'trade_ids':['trade']}
            s.ledger.commit('intent',pending,'intent',{'pending':pending})
            order=NS(id='order',asset_id='asset',condition_id='condition',side='BUY',maker_address=a['address'],
                associate_trades=['trade'],size_matched=D(10),status='MATCHED')
            trade=NS(id='trade',status='MATCHED',transaction_hash='0xtransaction',taker_order_id='order',trader_side='TAKER',side='BUY',
                asset_id='asset',condition_id='condition',fee_rate_bps=D(700),price=D('.5'),size=D(10))
            s.exchange=NS(lookup=AsyncMock(return_value=order),trades=AsyncMock(return_value=[trade]))
            with self.assertRaises(DataError):await s.reconcile()
            self.assertIsNone(s.ledger.get('position'));self.assertEqual(s.ledger.get('realized','0'),'0')
            trade.status='CONFIRMED';await s.reconcile()
            self.assertEqual(s.ledger.get('position')['basis'],'5.17500');self.assertIsNone(s.ledger.get('pending'))
            await s.reconcile();self.assertEqual(s.ledger.get('position')['shares'],'10');s.ledger.close()

class PlansTests(unittest.TestCase):
    def test_fee_envelope_and_market_minimum(self):
        book=Book('a','c');book.asks={D('.5'):D(1000)};market=NS(tick=D('.01'),min_size=D(5),fee=Fee(D('.07'),1))
        plan=buy_plan(book,market,'10','5');self.assertLessEqual(plan['max_cost'],10)
        # Edge uses the expected cost (average fill + actual fee of consumed levels), never above the reservation envelope.
        self.assertLess(plan['expected_cost_per_share'],plan['max_cost']/plan['shares']);self.assertGreater(plan['expected_cost_per_share'],min(book.asks))
        self.assertEqual(plan['shares']%D('.01'),0)
        with self.assertRaises(DataError):buy_plan(book,market,'5','5')
    def test_high_price_edge_uses_actual_fee_not_midpoint_envelope(self):
        book=Book('a','c');book.asks={D('.94'):D(1000)};market=NS(tick=D('.001'),min_size=D(5),fee=Fee(D('.07'),1))
        plan=buy_plan(book,market,'6.85','1')
        self.assertEqual(plan['expected_cost_per_share'],D('.94')+D('.07')*D('.94')*D('.06'))  # ~0.9439, not ~0.9585
        self.assertGreaterEqual(D('1')-plan['expected_cost_per_share'],D('.03'))


class UnexpectedResponseError(Exception):
    """Stands in for the SDK error raised when the exchange has no such order."""

class OrderPrecisionAndUnknownTests(unittest.IsolatedAsyncioTestCase):
    def test_fok_buy_spend_is_whole_cents(self):
        for tick,ask in ((D('.01'),D('.82')),(D('.001'),D('.941'))):
            market=NS(tick=tick,min_size=D(5),fee=Fee(D('.07'),1));book=Book('a','c');book.asks={ask:D(1000)}
            plan=buy_plan(book,market,'6.847966','1')
            self.assertEqual((plan['amount']*100)%1,0);self.assertLessEqual(plan['amount'],plan['shares']*plan['price'])
            self.assertGreaterEqual(plan['amount']/plan['price'],5);self.assertLessEqual(plan['amount'],plan['max_cost'])
    def service(self,tmp,now_ms):
        a={'id':'named','address':'0x'+'1'*40}
        s=LiveService(NS(store=NS(error=None),api=NS(clock=NS(now=lambda:now_ms))))
        s.account=a;s.ledger=LiveLedger(tmp,a)
        pending={'order_id':'0xABC','asset_id':'asset','condition_id':'condition','side':'BUY','price':'.83','shares':'8.08','max_cost':'6.84',
            'market_id':'market','end_s':300,'outcome':'Down','baseline_point':None,'fee_rate':'.07','fee_exponent':1,'trade_ids':[]}
        s.ledger.commit('intent',pending,'intent',{'pending':pending})
        s.exchange=NS(lookup=AsyncMock(side_effect=UnexpectedResponseError()),trades=AsyncMock(return_value=[]),token_balance=AsyncMock(return_value=D(0)))
        return s
    async def test_never_accepted_order_closes_only_after_market_close_without_fill_or_tokens(self):
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            s=self.service(tmp,330_000)
            with self.assertRaises(DataError):await s.reconcile()
            self.assertIsNotNone(s.ledger.get('pending'));s.ledger.close()
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            s=self.service(tmp,361_000);await s.reconcile()
            self.assertIsNone(s.ledger.get('pending'));s.ledger.close()
    async def test_fill_found_by_receipt_is_booked_even_above_planned_shares(self):
        # The 2026-10-24 14:29Z case: get_order failed, the account trade feed had the CONFIRMED taker fill, a
        # whole-cent market BUY received more shares than planned (better price) and reported 0 bps fee.
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            s=self.service(tmp,330_000)
            pending=dict(s.ledger.get('pending'),amount='6.70',max_cost='6.84');s.ledger.commit('intent2',pending,'intent2',{'pending':pending})
            fill=NS(id='t1',taker_order_id='0xabc',maker_orders=(),trader_side='TAKER',side='BUY',asset_id='asset',condition_id='condition',
                    price=D('.39'),size=D('16.9'),fee_rate_bps=D(0),status='CONFIRMED',transaction_hash='0xtx')
            s.exchange.trades.return_value=[fill];await s.reconcile()
            pos=s.ledger.get('position');self.assertIsNone(s.ledger.get('pending'))
            self.assertEqual(D(pos['shares']),D('16.9'));self.assertEqual(D(pos['basis']),D('6.591'));s.ledger.close()
    async def test_unconfirmed_fill_or_tokens_keep_the_lock(self):
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            s=self.service(tmp,361_000)
            s.exchange.trades.return_value=[NS(id='t1',taker_order_id='0xabc',maker_orders=(),status='MATCHED',transaction_hash=None)]
            with self.assertRaises(DataError):await s.reconcile()
            self.assertIsNotNone(s.ledger.get('pending'));s.ledger.close()
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            s=self.service(tmp,361_000);s.exchange.token_balance.return_value=D('8.08')
            with self.assertRaises(DataError):await s.reconcile()
            self.assertIsNotNone(s.ledger.get('pending'));s.ledger.close()

if __name__=='__main__':unittest.main()
