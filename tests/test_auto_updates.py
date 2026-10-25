import json,hashlib,tempfile,unittest
from pathlib import Path
from dataclasses import replace
from poly5.common import D,Settings
from poly5.domain import Market,Book,Fee
from poly5.execution import ExecutionGateway
from poly5.storage import EventStore
from poly5.auto_paper import AutoPaper
from poly5.updates import PRODUCT_ID,LocalUpdateSource,check_update,version_key

class PositiveModel:
    id='synthetic-policy-test'
    def predict(self,features):return [{'horizon_s':3,'mean_roi':.04,'q20_roi':.02}]
class NegativeModel(PositiveModel):
    def predict(self,features):return [{'horizon_s':3,'mean_roi':.04,'q20_roi':-.1}]

class ReferenceFixture:
    def __init__(self):
        self.f={'baseline':D(100),'spot':D(110),'twap':D(110),'spot_distance':D(10),'twap_distance':D(10),
            'sigma_1s':D(1),'distance_sigma':D(4),'spot_distance_sigma':D(4),'trend_5s':D(1),'trend_15s':D(2),
            'move_3s':D('.5'),'range_15s':D(2),'max_step_3s':D('.5'),'fair_up_heuristic':D('.9'),
            'baseline_point':{'value':'100','observed_ms':1000000,'received_ms':1000000,'window_start_ms':1000000}}
    def features(self,*args):return self.f

class AutoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=EventStore(self.root)
        self.gateway=ExecutionGateway(self.store);self.ref=ReferenceFixture();self.c=AutoPaper(self.root,self.gateway,self.store.append,reference=self.ref,entry_mode='distance_trend')
        self.market=Market('m','t','test','test','c',{'Up':'a','Down':'b'},1000,1300,True,D(5),D('.01'),Fee(D('.07'),1),'fixture','fixture',True)
    def tearDown(self):self.store.close();self.temp.cleanup()
    def step(self,seconds,bid='.49',ask='.50',enabled=True,stale=False):
        now=1000000+round(seconds*1000);b=Book('a','c')
        b.snapshot({'asset_id':'a','market':'c','timestamp':str(now-5000 if stale else now),
                    'bids':[{'price':bid,'size':'100'}],'asks':[{'price':ask,'size':'100'}]},now)
        self.c.step(self.market,b,now,enabled)
    def warm(self):
        for s in range(3,6):self.step(s)
    def test_automatic_entry_then_delayed_profit_exit_without_manual_calls(self):
        self.warm();self.assertEqual(len(self.gateway.orders),0)
        self.step(5.3);self.assertEqual(len(self.gateway.orders),1)
        self.step(6.3,'.56','.57');self.assertEqual(len(self.gateway.orders),1)
        self.step(6.6,'.56','.57');self.assertEqual(len(self.gateway.orders),2)
        self.assertGreater(self.gateway.realized,D(0));self.assertEqual(self.gateway.orders[-1]['side'],'SELL')
    def test_vanished_profit_quote_does_not_turn_profit_exit_into_loss(self):
        self.warm();self.step(5.3);self.step(6.3,'.56','.57');self.step(6.6,'.40','.41')
        self.assertEqual(self.gateway.realized,D(0));self.assertEqual(len(self.gateway.orders),1)
    def test_sudden_reversal_near_baseline_auto_exits(self):
        self.warm();self.step(5.3)
        self.ref.f.update(move_3s=D(-8),spot_distance=D(1),twap_distance=D(1),distance_sigma=D('.3'),spot_distance_sigma=D('.3'))
        self.step(6.3,'.4','.41');self.step(6.6,'.4','.41')
        self.assertEqual(len(self.gateway.orders),2);self.assertLess(self.gateway.realized,D(0))
    def test_quote_loss_alone_does_not_trigger_stop(self):
        self.warm();self.step(5.3);self.step(6.3,'.4','.41');self.step(6.6,'.4','.41')
        self.assertEqual(len(self.gateway.orders),1)
    def test_position_can_remain_open_beyond_thirty_seconds(self):
        self.warm();self.step(5.3)
        for s in [6.3,7.3,8.3,36.3,120.3]:self.step(s,'.49','.50')
        self.assertEqual(len(self.gateway.orders),1)
    def test_expiry_waits_for_official_result_instead_of_forced_sale(self):
        self.warm();self.step(5.3);self.step(298);self.step(298.3)
        self.assertEqual(len(self.gateway.orders),1)
        self.step(300);self.assertIn('等待官方结果',self.c.status['status'])
    def test_expensive_quote_rejects_entry(self):
        self.ref.f['fair_up_heuristic']=D('.51');self.warm();self.step(5.3);self.assertFalse(self.gateway.orders)
    def test_pending_buy_respects_pause(self):
        self.warm();self.gateway.paused=True;self.step(5.3);self.assertFalse(self.gateway.orders)
    def test_stale_book_never_fills(self):
        self.warm();self.step(5.3,stale=True);self.assertFalse(self.gateway.orders)
    def test_one_sided_book_is_not_fresh_and_never_crashes_auto(self):
        b=Book('a','c');now=1005000
        b.snapshot({'asset_id':'a','market':'c','timestamp':str(now),'bids':[],'asks':[{'price':'.5','size':'10'}]},now)
        self.assertFalse(b.fresh(now));self.assertEqual(b.quality,'EMPTY')
        self.c.step(self.market,b,now,True)
        self.assertFalse(self.gateway.orders)
    def test_restart_does_not_repeat_decision(self):
        self.warm();self.step(5.3)
        self.c=AutoPaper(self.root,self.gateway,self.store.append,reference=self.ref);self.step(5.8)
        self.assertEqual(len(self.gateway.orders),1)
    def test_old_position_is_not_moved_into_new_round(self):
        self.warm();self.step(5.3);old=dict(self.gateway.positions['a'])
        self.market=replace(self.market,id='next',start=1300,end=1600,outcomes={'Up':'new','Down':'other'})
        self.step(300);self.assertEqual(self.gateway.positions['a'],old);self.assertIn('等待官方结果',self.c.status['status']);self.assertEqual(len(self.gateway.orders),1)
    def test_disabled_does_not_enter(self):
        for s in range(0,7):self.step(s,enabled=False)
        self.assertFalse(self.gateway.orders)
    def test_far_aligned_reference_can_enter_without_roi_regression_gate(self):
        self.warm();self.step(5.3);self.assertEqual(len(self.gateway.orders),1)
        order=self.gateway.orders[0]
        self.assertGreaterEqual(D(order['contract_cost']),D(5));self.assertGreater(D(order['total']),D(5))
        self.assertEqual(order['budget_basis'],'contract_notional_plus_fees')
    def test_too_close_to_baseline_rejects_entry(self):
        self.ref.f['distance_sigma']=D('.1')
        self.warm();self.step(5.3);self.assertFalse(self.gateway.orders)
    def test_near_certain_but_untradeable_profit_quote_rejects_entry(self):
        self.ref.f['fair_up_heuristic']=D('.999')
        for s in [3,4,5,5.3]:self.step(s,'.98','.99')
        self.assertFalse(self.gateway.orders)
    def test_empty_ask_side_does_not_block_sale_into_existing_bid(self):
        self.warm();self.step(5.3)
        for seconds in [6.3,6.6]:
            now=1000000+round(seconds*1000);b=Book('a','c')
            b.snapshot({'asset_id':'a','market':'c','timestamp':str(now),'bids':[{'price':'.60','size':'100'}],'asks':[]},now)
            self.c.step(self.market,b,now,True)
        self.assertEqual(len(self.gateway.orders),2);self.assertGreater(self.gateway.realized,D(0))

class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.exe=self.root/'setup.exe';self.exe.write_bytes(b'fixture installer')
        self.manifest={'schema_version':1,'product_id':PRODUCT_ID,'platform':'windows-x64','channel':'development','version':'3.1.0-dev.4',
            'compatibility':{'settings_schema_min':1,'settings_schema_max':1,'readable_event_schemas':['3.1']},
            'package':{'file':'setup.exe','bytes':self.exe.stat().st_size,'sha256':hashlib.sha256(self.exe.read_bytes()).hexdigest()}}
        self.path=self.root/'update.json'
    def tearDown(self):self.temp.cleanup()
    def check(self,current='3.1.0-dev.3'):
        self.path.write_text(json.dumps(self.manifest),encoding='utf-8')
        return check_update(LocalUpdateSource(self.path),current)
    def test_update_detected_and_never_auto_installs(self):
        r=self.check();self.assertEqual(r['status'],'update_available');self.assertFalse(r['auto_install'])
    def test_numeric_version_order(self):
        self.assertGreater(version_key('3.1.0-dev.10'),version_key('3.1.0-dev.9'))
        self.assertGreater(version_key('3.1.0'),version_key('3.1.0-dev.99'))
    def test_downgrade_rejected(self):
        with self.assertRaises(ValueError):self.check('3.2.0')
    def test_corruption_rejected(self):
        self.exe.write_bytes(b'changed installer')
        with self.assertRaises(ValueError):self.check()
    def test_path_escape_rejected(self):
        self.manifest['package']['file']='../setup.exe'
        with self.assertRaises(ValueError):self.check()
    def test_wrong_product_rejected(self):
        self.manifest['product_id']='other'
        with self.assertRaises(ValueError):self.check()
    def test_future_schema_rejected(self):
        self.manifest['compatibility']['settings_schema_min']=2
        with self.assertRaises(ValueError):self.check()
    def test_future_settings_remain_intact(self):
        path=self.root/'settings/settings.json';path.parent.mkdir();content='{"schema_version":2,"note":"keep"}';path.write_text(content)
        with self.assertRaises(RuntimeError):Settings(self.root)
        self.assertEqual(path.read_text(),content)
    def test_unknown_user_settings_preserved(self):
        settings=Settings(self.root);settings.data['user_extension']={'keep':42};settings.save()
        again=Settings(self.root);again.save();self.assertEqual(again.data['user_extension'],{'keep':42})

if __name__=='__main__':unittest.main()

