import base64
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock
import uuid
from poly5.account_signer import connect_owner,signer_info,signer_load,signer_configured
from poly5.account_connection import load
from poly5.common import Settings

# Public, deterministic test vector, never a funded or production identity.
FIXTURE_KEY='0'*63+'1'
FIXTURE_ADDRESS='0x7e5f4552091a69125d5dfcb7b8c2659029395bdf'

class SetupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir='D:/POLYMARKET/work');self.root=Path(self.tmp.name)
        self.account={'id':'main-account','address':FIXTURE_ADDRESS}
        settings=Settings(self.root);settings.data.update(accounts=[self.account],active_account_id=self.account['id']);settings.save()
    def tearDown(self):self.tmp.cleanup()
    def client(self,limit=False):
        async def pages():yield NS(items=(),limit_reached=limit)
        return NS(wallet=FIXTURE_ADDRESS,signer=FIXTURE_ADDRESS,wallet_type='EOA',
            get_balance_allowance=AsyncMock(return_value=NS(balance=12345678,allowances={})),list_open_orders=pages,
            credentials=NS(key=str(uuid.uuid4()),secret=base64.urlsafe_b64encode(b'test-fixture-secret-123456').decode(),passphrase='fixture-pass'),close=AsyncMock())
    async def test_user_key_stays_encrypted_and_connection_never_arms(self):
        client=self.client();factory=AsyncMock(return_value=client)
        result=await connect_owner(self.root,self.account,FIXTURE_KEY,factory)
        self.assertTrue(result['connected']);self.assertFalse(result['live_enabled']);self.assertEqual(result['open_orders'],0)
        self.assertEqual(result['cash_cache'],'12.345678')
        self.assertTrue(signer_configured(self.root,self.account));self.assertTrue(Settings(self.root).data['auth_read_enabled'])
        self.assertEqual(signer_load(self.root,self.account)['private_key'],'0x'+FIXTURE_KEY)
        for p in self.root.rglob('*'):
            if p.is_file():self.assertNotIn(FIXTURE_KEY.encode(),p.read_bytes())
        client.close.assert_awaited_once()
    async def test_wrong_key_wallet_is_rejected_before_client_creation(self):
        factory=AsyncMock()
        with self.assertRaises(ValueError):await connect_owner(self.root,{**self.account,'address':'0x'+'1'*40},FIXTURE_KEY,factory)
        factory.assert_not_called();self.assertFalse(signer_configured(self.root,self.account))
    async def test_incomplete_order_pages_cannot_finish_setup(self):
        client=self.client(True)
        with self.assertRaises(ValueError):await connect_owner(self.root,self.account,FIXTURE_KEY,AsyncMock(return_value=client))
        self.assertFalse(signer_configured(self.root,self.account));client.close.assert_awaited_once()

if __name__=='__main__':unittest.main()
