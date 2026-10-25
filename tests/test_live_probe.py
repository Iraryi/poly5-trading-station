from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch
from poly5.common import D
from poly5.domain import Book, Fee
from poly5.live_ledger import LiveLedger
from poly5.live_service import LiveService, exchange_text


class RequestRejectedError(Exception):
    """Stands in for the SDK's 4xx refusal (status, code, message)."""
    def __init__(self, message, status, code=None):
        super().__init__(message);self.status=status;self.code=code;self.restriction=None


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    def service(self, tmp):
        a={'id':'named','address':'0x'+'1'*40}
        up=Book('up','c');up.snapshot({'asset_id':'up','market':'c','timestamp':'1000','bids':[{'price':'.6','size':'10'}],'asks':[{'price':'.61','size':'10'}]},1000)
        market=NS(id='m',condition_id='c',outcomes={'Up':'up'},accepting=True,fee=Fee(D('.07'),1),end=10_000,min_size=D(5))
        s=LiveService(NS(store=NS(error=None),api=NS(clock=NS(now=lambda:1_000)),market=market,books={'up':up}))
        s.account=a;s.ledger=LiveLedger(tmp,a);s.gates=lambda *args,**kw:None
        s.exchange=NS(sign_resting=AsyncMock(return_value=NS(maker_amount=50_000,taker_amount=5_000_000)),
                      send_once=AsyncMock(return_value=NS(ok=True,order_id='0xorder',status='live')),
                      cancel_owned=AsyncMock(),lookup=AsyncMock(return_value=NS(size_matched=0,status='CANCELED')))
        return s

    async def test_accepted_probe_is_cancelled_and_unlocked(self):
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp, patch('poly5.live_service.order_digest',return_value='0xorder'):
            s=self.service(tmp);r=await s.probe()
            self.assertTrue(r['accepted']);self.assertTrue(r['cancelled']);self.assertIsNone(s.ledger.get('pending'))
            args=s.exchange.sign_resting.call_args.args;self.assertEqual((args[1],args[2],args[3]),('BUY',D('.01'),D(5)))
            s.exchange.cancel_owned.assert_awaited_once_with('0xorder');s.ledger.close()

    async def test_refused_probe_keeps_reason_without_secrets(self):
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp, patch('poly5.live_service.order_digest',return_value='0xorder'):
            s=self.service(tmp)
            s.exchange.send_once.side_effect=RequestRejectedError('invalid signature for 0x'+'a'*40+' key '+'Q'*40,400,'INVALID_ORDER')
            r=await s.probe()
            self.assertFalse(r['accepted']);self.assertEqual(r['status'],400);self.assertIn('invalid signature',r['message'])
            self.assertNotIn('a'*40,r['message']);self.assertNotIn('Q'*40,r['message']);self.assertIsNone(s.ledger.get('pending'))
            s.exchange.cancel_owned.assert_not_awaited();s.ledger.close()

    async def test_probe_refuses_when_a_side_cannot_rest_safely(self):
        with tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work') as tmp:
            s=self.service(tmp);s.e.books['up'].asks={D('.02'):D(10)}
            with self.assertRaises(Exception):await s.probe()
            s.exchange.sign_resting.assert_not_awaited();s.ledger.close()

    def test_exchange_text_truncates_and_elides(self):
        t=exchange_text('order couldn\'t be fully filled. FOK orders are fully filled or killed '+'x'*500)
        self.assertTrue(t.startswith('order couldn'));self.assertLessEqual(len(t),200)


if __name__=='__main__':unittest.main()
