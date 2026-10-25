import base64
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
from poly5.common import atomic_json,epoch_ms,D
from poly5.model_bridge import ModelBridge,ShadowRuntime,sha,reference_features
from poly5.account_connection import save,load,configured
from poly5.api import AuthReadAdapter
from poly5.readiness import assess

class ModelPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.trainer=self.root/'trainer'
        self.bridge=ModelBridge(self.root/'station',self.trainer)
        from sklearn.dummy import DummyClassifier
        import joblib
        p=self.trainer/'models/direction/fixture.joblib';p.parent.mkdir(parents=True)
        model=DummyClassifier(strategy='prior').fit([[0],[1]],[0,1])
        joblib.dump({'model':model,'features':['stage_fraction'],'task':'direction'},p)
        self.meta={'id':'BTC5M_DIRECTION_FIXTURE_G0001_C00001','task':'direction','family':'linear','status':'complete',
            'features':['stage_fraction'],'model_path':str(p),'model_sha256':sha(p),'metrics':{'trained_through_ms':1}}
        atomic_json(self.trainer/'tasks/direction/best.json',self.meta)
    def tearDown(self):self.tmp.cleanup()
    def test_pin_is_independent_of_trainer_and_old_releases_rollback(self):
        first=self.bridge.pin('direction',self.meta['id']);second=self.bridge.pin('direction',self.meta['id'])
        Path(self.meta['model_path']).write_bytes(b'trainer replaced its original')
        self.assertEqual(self.bridge.selected()['release_id'],second['release_id'])
        self.bridge.activate(first['release_id']);self.assertEqual(len(self.bridge.releases()),2)
        self.assertEqual(self.bridge.selected()['release_id'],first['release_id'])
    def test_tampered_model_is_rejected_before_joblib_load(self):
        meta=self.bridge.pin('direction',self.meta['id'])
        (self.bridge.root/'releases'/meta['release_id']/'model.joblib').write_bytes(b'tampered')
        with patch('joblib.load') as loader:
            with self.assertRaises(ValueError):ShadowRuntime(self.bridge).predict({'stage_fraction':.5},100,SimpleNamespace())
            loader.assert_not_called()
    def test_replaced_manifest_and_path_traversal_are_rejected(self):
        meta=self.bridge.pin('direction',self.meta['id']);p=self.bridge.root/'releases'/meta['release_id']/'manifest.json'
        changed=json.loads(p.read_text());changed['features']=['unknown'];atomic_json(p,changed)
        with self.assertRaises(ValueError):self.bridge.selected()
        with self.assertRaises(ValueError):self.bridge.activate('../credentials')
    def test_model_change_between_catalog_and_pin_is_rejected(self):
        with self.assertRaises(ValueError):self.bridge.pin('direction','STALE_VERSION')
        self.assertFalse((self.bridge.root/'selected.json').exists())
    def test_shadow_inference_has_no_execution_capability(self):
        self.bridge.pin('direction',self.meta['id']);m=SimpleNamespace(id='m',condition_id='c',slug='s')
        out=ShadowRuntime(self.bridge).predict({'stage_fraction':.5},100,m)
        self.assertEqual(out['probability_up'],.5);self.assertFalse(out['live_approved']);self.assertEqual(out['scope'],'shadow_only')
    def test_future_trained_model_and_nan_features_are_rejected(self):
        self.bridge.pin('direction',self.meta['id']);runtime=ShadowRuntime(self.bridge)
        with self.assertRaises(ValueError):runtime.predict({'stage_fraction':.5},1,SimpleNamespace())
        with self.assertRaises(ValueError):runtime.predict({'stage_fraction':float('nan')},100,SimpleNamespace())
    def test_live_gate_cannot_be_granted_by_model_or_config(self):
        state={'live_enabled':True,'geo':{'blocked':False,'checked_ms':epoch_ms()},'clock_valid':True,'market':{},'books':{}}
        out=assess(state,{'status':'OK','observed_ms':epoch_ms(),'live_approved':True},{'identity_verified':True,'quality':'OK','observed_ms':epoch_ms()})
        self.assertFalse(out['ready_for_live']);self.assertFalse(out['live_enabled'])
    def test_received_time_features_match_trainer_contract(self):
        from poly5.reference import ReferenceTape,Point
        from trainstation.collector import reference_features as original
        tape=ReferenceTape();tape.connected=True;now=2000000;m=SimpleNamespace(start=1800,id='m')
        for at in range(1799000,now+1,1000):
            tape.points['spot'].append(Point(at,at,D('100000')+D(at-1799000)/100000))
            tape.points['twap'].append(Point(at,at,D('100000')+D(at-1799000)/120000))
        a=reference_features(tape,m,now);b,_=original(tape,m,now)
        for k in a:self.assertAlmostEqual(a[k],b[k],places=9)
        self.assertIn(m.id,tape.baselines)
        with self.assertRaises(ValueError):reference_features(tape,m,m.start*1000+1000)

class AccountPreparationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.account={'id':uuid.uuid4().hex,'address':'0x'+'1'*40}
        self.values={'KEY':str(uuid.uuid4()),'SECRET':base64.urlsafe_b64encode(b'fixture-test-only-secret-123').decode(),'PASSPHRASE':'fixture-passphrase',
            'SIGNER':self.account['address'],'SIGNATURE_TYPE':0}
    def tearDown(self):self.tmp.cleanup()
    async def test_credentials_are_encrypted_and_account_scoped(self):
        save(self.root,self.account,self.values);self.assertTrue(configured(self.root,self.account['id']))
        self.assertEqual(load(self.root,self.account)['KEY'],self.values['KEY'])
        files=list((self.root/'account-auth').rglob('*.bin'));self.assertEqual(len(files),1)
        self.assertNotIn(self.values['SECRET'].encode(),files[0].read_bytes())
        with self.assertRaises(ValueError):load(self.root,{**self.account,'address':'0x'+'2'*40})
        save(self.root,self.account,{**self.values,'KEY':'','SECRET':'','PASSPHRASE':''})
        self.assertEqual(load(self.root,self.account)['KEY'],self.values['KEY'])
    async def test_incomplete_credentials_and_changed_signer_cannot_reuse_old_auth(self):
        with self.assertRaises(ValueError):save(self.root,self.account,{**self.values,'SECRET':''})
        save(self.root,self.account,self.values)
        with self.assertRaises(ValueError):save(self.root,self.account,{**self.values,'KEY':'','SECRET':'','PASSPHRASE':'','SIGNER':'0x'+'2'*40})
    async def test_wrong_wallet_type_is_rejected_before_private_http(self):
        adapter=AuthReadAdapter(None)
        with patch.object(adapter,'credentials',return_value={**self.values,'WALLET':self.account['address'],'SIGNATURE_TYPE':3}):
            with self.assertRaises(ValueError):await adapter.verify_identity(self.account['address'])
    async def test_balance_cache_never_becomes_spendable_cash(self):
        class API:
            clock=SimpleNamespace(valid=lambda:True,now=lambda:2000000)
            async def get(self,url,params,headers):
                assert url.endswith('/balance-allowance') and params['signature_type']==0
                return {'balance':'12345678','allowances':{'0x'+'3'*40:'999999999'}}
        adapter=AuthReadAdapter(API())
        with patch.object(adapter,'credentials',return_value={**self.values,'WALLET':self.account['address']}):
            result=await adapter.balance(self.account['address'])
        self.assertEqual(result['cash'],D('12.345678'));self.assertIsNone(result['available']);self.assertIsNone(result['equity'])
        for path in ['/order','/balance-allowance/update','/auth/api-key']:
            with self.assertRaises(PermissionError):adapter.headers(path)

if __name__=='__main__':unittest.main()
