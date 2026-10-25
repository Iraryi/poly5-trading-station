import base64
from dataclasses import replace
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from poly5.common import D, loads
from poly5.domain import DataError, Book
from poly5.reference import ReferenceTape, credentials, save_credentials, subscription_specs
from poly5.reference_setup import SetupServer
from poly5.auto_paper import entry_candidate, reversal
from test_auto_updates import ReferenceFixture
from test_station import market


class ReferenceTests(unittest.TestCase):
    def tape(self):
        tape=ReferenceTape();tape.connected=True
        for second in range(940,1081):
            tape.add('spot',second*1000,second*1000,str(D(100)+D(second-1000)*D('.01')))
            tape.add('twap',second*1000,second*1000,str(D(100)+D(second-1000)*D('.005')))
        return tape
    def test_distance_has_fixed_opening_baseline_and_no_future_price(self):
        tape=self.tape();m=replace(market(),id='m',start=1000,end=1300)
        a=tape.features(m,1080000)
        self.assertEqual(a['baseline'],D(100));self.assertGreater(a['distance_sigma'],0)
        tape.add('spot',1085000,1085000,'999');tape.add('twap',1085000,1085000,'999')
        self.assertEqual(a,tape.features(m,1080000))
    def test_stale_reference_rejected(self):
        with self.assertRaises(DataError):self.tape().features(replace(market(),start=1000,end=1300),1090000)
    def test_missing_baseline_never_uses_current_price_as_open(self):
        with self.assertRaises(DataError):self.tape().features(replace(market(),start=900,end=1200),1080000)
    def test_provider_and_sequence_verified(self):
        t=ReferenceTape();f={'v':1,'channel':'price.crypto','seq':1,'payload':{'symbol':'btcusd','source':'chainlink','timestamp':1000,'full_accuracy_value':'100'}}
        self.assertTrue(t.consume(f,1000))
        with self.assertRaises(DataError):t.consume({**f,'seq':3},2000)
        with self.assertRaises(DataError):ReferenceTape().consume({**f,'payload':{**f['payload'],'source':'pyth'}},1000)
    def test_twap_wire_requires_sixty_second_filter(self):
        self.assertEqual(subscription_specs()[1]['filter'],{'symbol':'btcusd','window_seconds':60})
    def test_far_cushion_survives_a_reverse_move(self):
        f=ReferenceFixture().f
        f.update(move_3s=D(-8),spot_distance=D(100),distance_sigma=D(40),spot_distance_sigma=D(40))
        self.assertFalse(reversal(f,'Up')['trigger'])
    def test_sudden_reversal_threatening_baseline(self):
        f=ReferenceFixture().f;f.update(move_3s=D(-8),spot_distance=D(1),distance_sigma=D('.3'),spot_distance_sigma=D('.3'))
        self.assertTrue(reversal(f,'Up')['trigger'])
    def test_down_candidate_uses_down_book_not_synthetic_up_complement(self):
        f=ReferenceFixture().f
        for k in ['spot_distance','twap_distance','distance_sigma','spot_distance_sigma','trend_5s','trend_15s']:f[k]=-f[k]
        f['fair_up_heuristic']=D('.1');b=Book('down','c')
        b.snapshot({'asset_id':'down','market':'c','timestamp':'1100000','bids':[{'price':'.49','size':'100'}],'asks':[{'price':'.5','size':'100'}]},1100000)
        c=entry_candidate(f,{'Down':b},market(),1100000)
        self.assertEqual(c['asset_id'],'down');self.assertEqual(c['outcome'],'Down')


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.server=SetupServer(self.root);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.client=httpx.Client(base_url=self.server.origin,trust_env=False)
    def tearDown(self):
        self.client.close();self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def test_page_requires_session_path_and_has_no_external_script(self):
        self.assertEqual(self.client.get('/').status_code,404)
        r=self.client.get('/'+self.server.token)
        self.assertEqual(r.status_code,200);self.assertNotIn('<script src=',r.text)
        self.assertEqual(r.headers['cache-control'],'no-store')
    def test_cross_origin_or_missing_csrf_is_rejected(self):
        self.assertEqual(self.client.post('/import',json={}).status_code,403)
        self.assertEqual(self.client.post('/import',json={},headers={'Origin':'https://evil.invalid','X-Poly5-Token':self.server.token}).status_code,403)
    def test_encrypted_import_does_not_need_signer_private_key(self):
        values={'KEY':'00000000-0000-4000-8000-000000000001','SECRET':base64.urlsafe_b64encode(b'a'*32).decode(),'PASSPHRASE':'fixture'}
        async def verified(_):return True
        with patch('poly5.reference_setup.probe',verified):
            r=self.client.post('/import',json=values,headers={'Origin':self.server.origin,'X-Poly5-Token':self.server.token})
        self.assertEqual(r.status_code,200)
        binary=(self.root/'credentials/settings/secrets/reference.bin').read_bytes()
        self.assertNotIn(values['SECRET'].encode(),binary)
        with patch.dict('os.environ',{},clear=True):self.assertEqual(credentials(self.root),values)
        self.assertFalse((self.root/'credentials/settings/secrets/reference-signer.bin').exists())
        self.assertFalse((self.root/'settings/secrets/reference.bin').exists())


if __name__=='__main__':unittest.main()
