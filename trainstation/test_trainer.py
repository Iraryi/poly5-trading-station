import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch,MagicMock
from .data import Data,REF,TASKS,import_seed
from .modeling import propose,split_rows,metric
from .supervisor import review
from poly5.reference import reference_secret_store

class TrainerTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.data=Data(self.root)
    def tearDown(self):self.temp.cleanup()
    def f(self):return {k:0. for k in TASKS['late'][1]}
    def test_future_label_and_nonfinite_feature_rejected(self):
        with self.assertRaises(ValueError):self.data.add('x','late','s',1,1000,self.f(),1,999)
        f=self.f();f[REF[0]]=float('nan')
        with self.assertRaises(ValueError):self.data.add('x','late','s',1,1000,f)
    def test_prediction_must_precede_unseen_label(self):
        self.data.add('a','late','s',1,1000,self.f());self.data.predict('m','a',.9,1100)
        self.data.label('a',1,2000);self.data.predict('late_model','a',1,2100)
        self.assertEqual(self.data.forward('m')['n'],1);self.assertEqual(self.data.forward('late_model')['n'],0)
    def test_forward_comparison_uses_same_samples(self):
        for i in range(25):
            id=str(i);self.data.add(id,'late','s',1,1000,self.f());self.data.predict('bad',id,.1,1100);self.data.predict('good',id,.9,1100);self.data.label(id,1,2000)
        pair=self.data.paired_forward('bad','good');self.assertEqual(pair['n'],25);self.assertGreater(pair['first_mse'],pair['second_mse'])
    def test_grouped_time_split_with_embargo(self):
        rows=[{'slug':str(i),'start_s':i*300,'decision_ms':(i*300+240)*1000,'ready_ms':(i*300+300)*1000} for i in range(100) for _ in range(2)]
        a,b,c=split_rows(rows)
        self.assertFalse({r['slug'] for r in a}&{r['slug'] for r in b})
        self.assertLess(max(r['ready_ms'] for r in a),min(r['decision_ms'] for r in b))
    def test_mutation_is_bounded_and_parent_preserved(self):
        parent=propose('hist',random.Random(1));before=dict(parent)
        for i in range(50):
            child=propose('hist',random.Random(i),parent);self.assertIn(child['max_leaf_nodes'],[5,7,15])
        self.assertEqual(parent,before)
    def test_ai_quota_persists_and_no_code_is_executed(self):
        reference_secret_store(self.root).save('ai-supervisor','fixture-key')
        config={'ai':{'enabled':True,'base_url':'https://fixture.invalid/v1','model':'test'}}
        meta={'late':{'classification':True,'has_proxy':True}}
        reply='{"proposals":{"late":[{"family":"hist","params":{"max_iter":120},"calibration":"isotonic"}]},"suggestion":"more validation"}'
        response=MagicMock();response.status_code=200;response.json.return_value={'choices':[{'message':{'content':reply}}]}
        client=MagicMock();client.__enter__.return_value.post.return_value=response
        first=lambda r:r['proposals']['late'][0]['family']
        with patch('httpx.Client',return_value=client),patch('trainstation.supervisor.time.time',return_value=1000000) as clock:
            self.assertEqual(first(review(self.root,config,{},meta)),'hist');review(self.root,config,{},meta)
            self.assertEqual(client.__enter__.return_value.post.call_count,1)
            clock.return_value=1000000+3599;review(self.root,config,{},meta)
            self.assertEqual(client.__enter__.return_value.post.call_count,1)
            clock.return_value=1000000+3600
            self.assertEqual(first(review(self.root,config,{},meta)),'hist')
            self.assertEqual(client.__enter__.return_value.post.call_count,2)
            config['ai']['interval_minutes']=30
            clock.return_value=1000000+3600+1799;review(self.root,config,{},meta)
            self.assertEqual(client.__enter__.return_value.post.call_count,2)
            clock.return_value=1000000+3600+1800
            self.assertEqual(first(review(self.root,config,{},meta)),'hist')
            self.assertEqual(client.__enter__.return_value.post.call_count,3)
        self.assertTrue((self.root/'ai/quota.json').exists())
        body=client.__enter__.return_value.post.call_args.kwargs['json']
        self.assertGreaterEqual(body['max_tokens'],4000);self.assertEqual(body['response_format'],{'type':'json_object'})
    def test_ai_guardrails_clamp_drop_and_reject(self):
        from .ai_policy import sanitize,parse
        clean,notes=sanitize('late',{'family':'forest','params':{'n_estimators':5000,'max_depth':'7','min_samples_leaf':float('nan'),'evil':'rm -rf','max_samples':None},
                                     'calibration':'magic','proxy_weight':.4,'rationale':'x'*900},True,True)
        self.assertEqual(clean['params'],{'n_estimators':300,'max_depth':7,'max_samples':None})   # clamped, coerced, NaN dropped
        self.assertEqual(clean['data_params'],{'calibration':'none','proxy_weight':.5});self.assertEqual(len(clean['rationale']),400)
        self.assertTrue(any('evil' in n for n in notes) and any('越界' in n for n in notes))
        self.assertIsNone(sanitize('late',{'family':'neural_net','params':{}},True,True)[0])
        self.assertEqual(sanitize('roi5',{'family':'logit','params':{'C':1},'calibration':'isotonic','proxy_weight':1},False,False)[0]['data_params'],{})
        out=parse('{"proposals":{"late":[{"family":"logit","params":{"C":0.5}},{"family":"forest"},{"family":"hist"}],"unknown_task":[{"family":"hist"}]}}',
                  ['late'],lambda t:True,lambda t:False)
        self.assertEqual(len(out['proposals']['late']),2)                                          # at most AI_SLOTS
        with self.assertRaises(ValueError):parse('[1,2,3]',['late'],lambda t:True,lambda t:False)
        with self.assertRaises(ValueError):parse('not json',['late'],lambda t:True,lambda t:False)
    def test_ai_vs_control_report_and_circuit_breaker(self):
        from .ai_policy import report,breaker,ai_allowed,slots_for,history,parse,BREAKER_HOURS
        def gen(task,i,ai,control,at=1000):
            return [{'id':f'{task}{i}{o}','scope':task,'generation':i,'task':task,'origin':o,'family':'forest','params':{'n_estimators':50},
                     'data_params':{},'metrics':{'score':v},'created_ms':at} for o,v in (('ai',ai),('control',control),('program',.5))]
        bad=lambda task,n,at=1000:[t for i in range(n) for t in gen(task,i,.9,.1,at)]
        good=lambda task,n,at=1000:[t for i in range(n) for t in gen(task,i,.1,.9,at)]
        stats=report(bad('late',20),now_ms=2000);self.assertLess(stats['overall']['mean_advantage'],0)
        # one significantly bad task: only that task cools down, no global lockout
        state=breaker(bad('roi15',20)+good('roi5',20),{},now_ms=2000)
        self.assertNotIn('global',state);self.assertFalse(ai_allowed(state,'roi15',2000));self.assertTrue(ai_allowed(state,'roi5',2000))
        # two bad tasks at once: global breaker
        both=breaker(bad('roi15',20)+bad('late_mkt',20),{},now_ms=2000);self.assertIn('until_ms',both['global'])
        # probation: after the cooldown the same stale pairs cannot re-trip
        later=2000+BREAKER_HOURS*3600000+1
        again=breaker(bad('roi15',20),state,now_ms=later);self.assertTrue(ai_allowed(again,'roi15',later));self.assertIn('since_ms',again['tasks']['roi15'])
        fresh_bad=bad('roi15',20,at=later)
        self.assertFalse(ai_allowed(breaker(bad('roi15',20)+fresh_bad,again,now_ms=later+1),'roi15',later+1))   # new evidence does
        # earned slots: 2 when significantly better, 1 by default, 0 in cooldown
        self.assertEqual(slots_for('roi5',good('roi5',20),{},2000),2);self.assertEqual(slots_for('late',good('late',5),{},2000),1)
        self.assertEqual(slots_for('roi15',bad('roi15',20),state,2000),0)
        # track record and rejection of resubmitted poor configurations
        table,poor=history(bad('late',4))
        self.assertEqual(table[0]['mean_rank'],1.0)
        out=parse('{"proposals":{"late":[{"family":"forest","params":{"n_estimators":50}},{"family":"forest","params":{"n_estimators":200}}]}}',
                  ['late'],lambda t:True,lambda t:False,poor)
        self.assertEqual([p['params']['n_estimators'] for p in out['proposals']['late']],[200]);self.assertTrue(out['notes']['late'])
    def test_derived_features_match_digital_probability_and_keep_old_models_working(self):
        from .data import vector,design,DERIVED
        x=self.f();x.update(spot_open_bps=10.,twap_open_bps=5.,vol_60s_bps=1.,stage_fraction=.8,return_15s_bps=2.)
        features=design('late');v=dict(zip(features,vector(x,features)))
        self.assertAlmostEqual(v['z_spot'],10/(1*60**.5));self.assertAlmostEqual(v['sqrt_remaining_s'],60**.5)
        self.assertAlmostEqual(v['p_digital'],.5*(1+__import__('math').erf(v['z_spot']/2**.5)))
        self.assertEqual(vector(x,TASKS['late'][1]),[x[k] for k in TASKS['late'][1]])
        x['vol_60s_bps']=0.;x['stage_fraction']=1.
        self.assertTrue(all(__import__('math').isfinite(value) for value in vector(x,features)))
        self.assertTrue(set(DERIVED)<=set(design('roi5')))
    def test_deployment_refit_learns_from_every_labelled_row(self):
        from .modeling import fit_job
        from poly5.common import atomic_json
        import joblib
        rng=random.Random(3);rows=[]
        for i in range(80):
            for offset in (240,270):
                f=self.f();f['spot_open_bps']=rng.uniform(-20,20);f['vol_60s_bps']=1.;f['stage_fraction']=offset/300
                rows.append({'id':f'{i}:{offset}','slug':f's{i}','start_s':i*300,'decision_ms':(i*300+offset)*1000,'ready_ms':(i*300+300)*1000,'x':f,'y':float(f['spot_open_bps']>0),'source':'archive','aux':{}})
        snapshot=self.root/'snap.json';atomic_json(snapshot,rows)
        from .data import digest,design
        base={'task':'late','family':'forest','params':{'n_estimators':20,'max_depth':4,'min_samples_leaf':5,'max_features':1.},'seed':1,
              'snapshot':str(snapshot),'snapshot_sha256':digest(snapshot),'features':design('late'),'classification':True}
        atomic_json(self.root/'a.job.json',{**base,'id':'a','model_path':str(self.root/'a.joblib'),'result':str(self.root/'a.json')});fit_job(str(self.root/'a.job.json'))
        selected=json.loads((self.root/'a.json').read_text(encoding='utf-8'))
        self.assertLess(selected['metrics']['trained_through_ms'],max(r['ready_ms'] for r in rows));self.assertIn('log_loss',selected['metrics']);self.assertTrue(0<=selected['metrics']['ece']<=1)
        atomic_json(self.root/'b.job.json',{**base,'id':'a_FULL','refit_of':'a','metrics':selected['metrics'],'model_path':str(self.root/'b.joblib'),'result':str(self.root/'b.json')});fit_job(str(self.root/'b.job.json'))
        refit=json.loads((self.root/'b.json').read_text(encoding='utf-8'))
        self.assertEqual(refit['metrics']['refit_rows'],len(rows));self.assertEqual(refit['metrics']['trained_through_ms'],max(r['ready_ms'] for r in rows))
        self.assertEqual(refit['metrics']['score'],selected['metrics']['score']);self.assertEqual(joblib.load(self.root/'b.joblib')['features'],design('late'))
    def test_proxy_backfill_is_training_only_and_never_after_validation_start(self):
        import polars as pl
        from .modeling import fit_job
        from .data import digest,design,proxy_manifest
        from poly5.common import atomic_json
        rng=random.Random(5);rows=[];proxy=[]
        for i in range(80):
            for offset in (240,270):
                f=self.f();f['spot_open_bps']=rng.uniform(-20,20);f['vol_60s_bps']=1.;f['stage_fraction']=offset/300
                rows.append({'id':f'{i}:{offset}','slug':f's{i}','start_s':100000+i*300,'decision_ms':(100000+i*300+offset)*1000,'ready_ms':(100000+i*300+300)*1000,'x':f,'y':float(f['spot_open_bps']>0),'source':'archive','aux':{}})
        for i in range(-200,200):  # proxy markets both before and inside/after the validation window
            v=rng.uniform(-20,20);proxy.append({'market_slug':f'p{i}','start_s':100000+i*300,'decision_us':(100000+i*300+240)*1_000_000,'offset':240,'label_up':int(v>0),
                **{k:0. for k in REF},'spot_open_bps':v,'vol_60s_bps':1.})
        (self.root/'proxy').mkdir();pl.DataFrame(proxy).write_parquet(self.root/'proxy/p.parquet')
        snapshot=self.root/'snap.json';atomic_json(snapshot,rows);manifest=proxy_manifest(self.root)
        base={'task':'late','family':'forest','params':{'n_estimators':10,'max_depth':4,'min_samples_leaf':5,'max_features':1.},'seed':1,'snapshot':str(snapshot),
              'snapshot_sha256':digest(snapshot),'features':design('late'),'classification':True,'proxy':manifest}
        def run(name,**extra):
            atomic_json(self.root/(name+'.job.json'),{**base,**extra,'id':name,'model_path':str(self.root/(name+'.joblib')),'result':str(self.root/(name+'.json'))})
            fit_job(str(self.root/(name+'.job.json')));return json.loads((self.root/(name+'.json')).read_text(encoding='utf-8'))['metrics']
        off=run('off',data_params={'proxy_weight':0.});on=run('on',data_params={'proxy_weight':.5})
        self.assertEqual(off['proxy_train_n'],0);self.assertGreater(on['proxy_train_n'],0);self.assertLess(on['proxy_train_n'],400)
        self.assertEqual(on['n'],off['n']);self.assertLess(on['trained_through_ms'],on['validation_start_ms'])
        full=run('full',data_params={'proxy_weight':.5},refit_of='on',metrics=on)
        self.assertEqual(full['refit_proxy_rows'],400);self.assertEqual(full['refit_rows'],len(rows))
        manifest[0]['sha256']='0'*64;atomic_json(self.root/'bad.job.json',{**base,'proxy':manifest,'data_params':{'proxy_weight':1.},'id':'bad','model_path':str(self.root/'bad.joblib'),'result':str(self.root/'bad.json')})
        with self.assertRaises(ValueError):fit_job(str(self.root/'bad.job.json'))
    def test_replay_judges_each_history_day_once_without_lookahead(self):
        import polars as pl
        from .modeling import fit_job
        from .data import digest,design,proxy_manifest,replay_days
        from poly5.common import atomic_json
        rng=random.Random(9);proxy=[];day0=1786147200
        for i in range(288*6):  # six full days of proxy history
            v=rng.uniform(-20,20);start=day0+i*300
            proxy.append({'market_slug':f'p{i}','start_s':start,'decision_us':(start+240)*1_000_000,'offset':240,'label_up':int(v>0),**{k:0. for k in REF},'spot_open_bps':v,'vol_60s_bps':1.})
        proxy=[p for p in proxy if not (day0+4*86400<=p['start_s']<day0+4*86400+86400*.5)]  # day 5 has only half a day
        (self.root/'proxy').mkdir();pl.DataFrame(proxy).write_parquet(self.root/'proxy/p.parquet');manifest=proxy_manifest(self.root)
        days=replay_days(manifest,[])
        self.assertEqual(days,[day0+3*86400,day0+5*86400])  # 3 warm-up days never judged; the thin day is skipped
        snapshot=self.root/'snap.json';atomic_json(snapshot,[])
        spec={'task':'late','family':'forest','params':{'n_estimators':10,'max_depth':4,'min_samples_leaf':5,'max_features':1.},'seed':1,'snapshot':str(snapshot),
              'snapshot_sha256':digest(snapshot),'features':design('late'),'classification':True,'proxy':manifest,'data_params':{'proxy_weight':.5},
              'split':{'mode':'replay','val_start_s':days[0],'val_end_s':days[0]+86400},'id':'r','model_path':str(self.root/'r.joblib'),'result':str(self.root/'r.json')}
        atomic_json(self.root/'r.job.json',spec);fit_job(str(self.root/'r.job.json'))
        m=json.loads((self.root/'r.json').read_text(encoding='utf-8'))['metrics']
        self.assertEqual(m['split'],'replay');self.assertEqual(m['n'],288)
        self.assertLess(m['trained_through_ms'],(days[0]-600)*1000);self.assertGreaterEqual(m['validation_start_ms'],days[0]*1000)
    def test_paper_pnl_needs_edge_over_executable_price_and_trades_once_per_market(self):
        from .data import paper_trades,roi_trades
        q=lambda up,down:{'Up':{'shares':5/up,'total':5.},'Down':{'shares':5/down,'total':5.}}
        result=paper_trades([('m1',1,.90,q(.80,.22),1),('m1',2,.95,q(.80,.22),1),   # buy Up once (edge .10), wins
                             ('m2',1,.10,q(.85,.20),1),                              # buy Down (edge .70), loses
                             ('m3',1,.82,q(.80,.22),1),                              # edge .02 < .03: no trade
                             ('m4',1,.90,None,1)],min_price=0.)                      # no executable quote: not counted
        self.assertEqual((result['markets'],result['trades'],result['wins']),(3,2,1))
        self.assertAlmostEqual(result['pnl'],(5/.8-5)-5,places=2);self.assertEqual(result['stake'],10.)
        filtered=paper_trades([('m1',1,.90,q(.80,.22),1),('m2',1,.10,q(.85,.20),1)])        # default: longshot filter on
        self.assertEqual(filtered['trades'],1)                                              # the 0.20 Down longshot is skipped
        r=roi_trades([('a',.01,.2,5.),('b',.001,.5,5.),('c',.02,-.1,5.)])
        self.assertEqual((r['trades'],r['pnl']),(2,.5))
    def test_legacy_single_task_state_is_copied_into_task_folder(self):
        from .runner import migrate_legacy
        from poly5.common import atomic_json
        atomic_json(self.root/'evolution.json',{'task_scope':'late:auto','scope':'x','trials':48})
        atomic_json(self.root/'research_best.json',{'id':'champ'});atomic_json(self.root/'registry.json',[{'id':'champ'}])
        migrate_legacy(self.root)
        self.assertEqual(json.loads((self.root/'tasks/late/best.json').read_text(encoding='utf-8'))['id'],'champ')
        self.assertTrue((self.root/'tasks/late/evolution.json').exists() and (self.root/'research_best.json').exists())
        atomic_json(self.root/'tasks/late/best.json',{'id':'newer'});migrate_legacy(self.root)  # never overwrites later state
        self.assertEqual(json.loads((self.root/'tasks/late/best.json').read_text(encoding='utf-8'))['id'],'newer')
    def test_extra_archive_folder_imports_roi_only_once(self):
        import polars as pl
        from .data import BOOK
        folder=self.root/'archive';folder.mkdir()
        row={'market_slug':'btc-updown-5m-1786147200','decision_us':(1786147200+60)*1_000_000,'stage':60,'entry_filled':True,'exit_available_5s':True,'exit_available_15s':False,
             'roi_5s':.02,'roi_15s':None,'entry_cost':5.,**{k:0. for k in REF+BOOK}}
        pl.DataFrame([row]).write_parquet(folder/'roi-dataset.parquet')
        import_seed(self.data,folder);import_seed(self.data,folder)
        self.assertEqual(self.data.stats('roi5')['labelled'],1);self.assertEqual(self.data.stats('roi15')['samples'],0)
    def test_market_features_and_archive_import(self):
        import polars as pl,math
        from .data import design,vector,MKT_DERIVED
        x=self.f();x.update(spot_open_bps=10.,vol_60s_bps=1.,stage_fraction=.8,mkt_bid=.79,mkt_ask=.81,up_avg_price=.82,down_avg_price=.22)
        v=dict(zip(design('late_mkt'),vector(x,design('late_mkt'))))
        self.assertTrue(set(MKT_DERIVED)<=set(v));self.assertAlmostEqual(v['mkt_mid'],.80)
        self.assertAlmostEqual(v['z_gap'],v['z_spot']-v['implied_z']);self.assertAlmostEqual(v['logit_mid'],math.log(.8/.2))
        folder=self.root/'archive';folder.mkdir()
        row={'market_slug':'btc-updown-5m-1786147200','start_s':1786147200,'decision_us':(1786147200+240)*1_000_000,'offset':240,'label_up':1,**{k:0. for k in REF},
             'mkt_bid':.6,'mkt_ask':.62,'up_shares':8.,'up_total':5.,'down_shares':12.,'down_total':5.}
        pl.DataFrame([row]).write_parquet(folder/'market-features.parquet');import_seed(self.data,folder)
        self.assertEqual(self.data.stats('late_mkt')['labelled'],1);self.assertEqual(self.data.stats('direction_mkt')['labelled'],1)
        aux=self.data.snapshot('late_mkt')[0]['aux'];self.assertEqual(aux['quotes']['Up']['total'],5.)
    def test_market_task_reports_skill_ci_and_new_families_calibrate(self):
        from .modeling import fit_job
        from .data import digest,design
        from poly5.common import atomic_json
        rng=random.Random(4);rows=[]
        for i in range(120):
            for offset in (240,270):
                v=rng.uniform(-20,20);mid=min(.95,max(.05,.5+v/45));f=self.f();f.update(spot_open_bps=v,vol_60s_bps=1.,stage_fraction=offset/300,mkt_bid=mid-.01,mkt_ask=mid+.01,up_avg_price=mid+.02,down_avg_price=1-mid+.02)
                quotes={'Up':{'shares':5/(mid+.02),'total':5.},'Down':{'shares':5/(1.02-mid),'total':5.}}
                rows.append({'id':f'{i}:{offset}','slug':f's{i}','start_s':i*300,'decision_ms':(i*300+offset)*1000,'ready_ms':(i*300+300)*1000,'x':f,'y':float(rng.random()<mid),'source':'archive','aux':{'quotes':quotes}})
        snapshot=self.root/'snap.json';atomic_json(snapshot,rows)
        for family,params,cal in [('extra',{'n_estimators':20,'max_depth':4,'min_samples_leaf':10,'max_features':1.},'isotonic'),('logit',{'C':1.},'sigmoid'),('forest',{'n_estimators':20,'max_depth':4,'min_samples_leaf':10,'max_features':1.,'max_samples':None},'none')]:
            spec={'task':'late_mkt','family':family,'params':params,'seed':1,'snapshot':str(snapshot),'snapshot_sha256':digest(snapshot),'features':design('late_mkt'),'classification':True,
                  'data_params':{'calibration':cal},'id':family,'model_path':str(self.root/(family+'.joblib')),'result':str(self.root/(family+'.json'))}
            atomic_json(self.root/(family+'.job.json'),spec);fit_job(str(self.root/(family+'.job.json')))
            m=json.loads((self.root/(family+'.json')).read_text(encoding='utf-8'))['metrics']
            self.assertIn('skill_vs_market',m);self.assertAlmostEqual(m['skill_vs_market'],m['market_brier']-m['mse'])
            self.assertGreater(m['paper_pnl']['markets'],0);self.assertIn('pnl_ci',m['paper_pnl'])
    def test_frozen_verdict_needs_enough_trades_and_a_positive_interval(self):
        from .data import verdict,summarise
        self.assertEqual(verdict(summarise([1.]*50,[5.]*50,50)),'验证中')            # positive but too few trades
        self.assertEqual(verdict(summarise([1.,-.5]*150,[5.]*300,300)),'已确认盈利')
        self.assertEqual(verdict(summarise([-1.,.2]*30,[5.]*60,60)),'已否定（显著亏损）')
        self.assertEqual(verdict(summarise([-5.2,-5.3],[5.]*2,2)),'验证中')                   # two losses refute nothing
    def test_order_book_recorder_writes_readable_tables(self):
        import polars as pl
        from decimal import Decimal as D
        from types import SimpleNamespace
        from .recorder import OrderBookRecorder
        market=SimpleNamespace(slug='btc-updown-5m-1',condition_id='0xc',outcomes={'Up':'u','Down':'d'},start=0,end=300,tick=D('.01'))
        book=lambda bids,asks:SimpleNamespace(bids={D(str(k)):D(str(v)) for k,v in bids.items()},asks={D(str(k)):D(str(v)) for k,v in asks.items()},initialized=True,quality='OK',source_ms=1,received_ms=2)
        books={'u':book({.49:10,.48:5},{.51:7}),'d':book({.49:7},{.51:10,.52:3})}
        r=OrderBookRecorder(self.root,{});r.on_market(market)
        r.snapshot(1786665600000,market,books);r.snapshot(1786665600400,market,books)          # same second: recorded once
        r.on_book('u',books['u'],1786665600500,market.slug,1786665600490);r.on_book('u',books['u'],1786665600600,market.slug,1)  # unchanged: once
        r.on_trade({'asset_id':'d','price':'0.51','size':'12','side':'BUY','timestamp':'1786665600700'},1786665600705,market.slug)
        r.on_reference('twap',1786665600000,'63000.123456789',1786665601200)
        r.maybe_flush(force=True)
        day=self.root/'orderbook'/'date=2026-08-14'
        books_=pl.read_parquet(day/'books'/'*.parquet');self.assertEqual(books_.height,2)
        up=books_.filter(pl.col('outcome')=='Up').row(0,named=True);self.assertEqual(up['bid_prices'],[.49,.48]);self.assertEqual(up['ask_sizes'],[7.])
        self.assertEqual(pl.read_parquet(day/'bbo'/'*.parquet').height,1)
        t=pl.read_parquet(day/'trades'/'*.parquet').row(0,named=True);self.assertEqual((t['outcome'],t['price'],t['side']),('Down',.51,'BUY'))
        self.assertEqual(pl.read_parquet(day/'reference'/'*.parquet')['value'][0],'63000.123456789')
        self.assertIn('盘口记录',r.status['recorder'])
    def test_shadow33_replays_trigger_and_both_exits(self):
        from .shadow33 import replay
        start=1786665600;slug=f'btc-updown-5m-{start}'
        def book(bid,ask):return {'bid_prices':[bid,bid-.01],'bid_sizes':[500.,500.],'ask_prices':[ask,ask+.01],'ask_sizes':[500.,500.]}
        def run(twap_move,bid_path,label):
            twap=lambda ms:63000.+(twap_move if ms>=(start+240)*1000 else 0.)
            spot=lambda ms:63000.+(twap_move*1.2 if ms>=(start+240)*1000 else 0.)
            books={}
            for sec in range(start+240,start+300):
                b=bid_path(sec-start);books[(sec,'Up')]=book(b,b+.01);books[(sec,'Down')]=book(.98-b,.99-b)
            return replay(slug,books,twap,spot,label)
        win=run(60.,lambda t:.92 if t<250 else .97,1)                                              # +$60: triggers; bid rises -> take profit
        self.assertEqual(win['tp05']['reason'],'take_profit');self.assertEqual(win['hold']['reason'],'settlement')
        self.assertGreater(win['hold']['pnl'],win['tp05']['pnl']);self.assertGreater(win['tp05']['pnl'],0);self.assertIsNone(win['hold80'])   # $80 threshold not reached
        loss=run(60.,lambda t:.92 if t<250 else .40,0)                                             # reversal: never sellable at +0.5%
        self.assertEqual(loss['tp05']['reason'],'settlement');self.assertLess(loss['tp05']['pnl'],-4.9)
        self.assertEqual(run(30.,lambda t:.9,1),{'tp05':None,'hold':None,'hold80':None})          # below $50: no trade
    def test_final_exam_validates_only_on_unseen_real_rows(self):
        import polars as pl
        from .modeling import fit_job
        from .data import digest,design,proxy_manifest
        from poly5.common import atomic_json
        rng=random.Random(11);rows=[];proxy=[];exam=1786147200+86400*2
        for i in range(288*3):
            start=1786147200+i*300;v=rng.uniform(-20,20)
            f=self.f();f.update(spot_open_bps=v,vol_60s_bps=1.,stage_fraction=.8)
            if i%2:rows.append({'id':f'r{i}','slug':f'r{i}','start_s':start,'decision_ms':(start+240)*1000,'ready_ms':(start+300)*1000,'x':f,'y':float(v>0),'source':'live' if start>=exam else 'archive','aux':{}})
            else:proxy.append({'market_slug':f'p{i}','start_s':start,'decision_us':(start+240)*1_000_000,'offset':240,'label_up':int(v>0),**{k:0. for k in REF},'spot_open_bps':v,'vol_60s_bps':1.})
        (self.root/'proxy').mkdir();pl.DataFrame(proxy).write_parquet(self.root/'proxy/p.parquet')
        snapshot=self.root/'snap.json';atomic_json(snapshot,rows)
        spec={'task':'late','family':'forest','params':{'n_estimators':10,'max_depth':4,'min_samples_leaf':5,'max_features':1.},'seed':1,'snapshot':str(snapshot),'snapshot_sha256':digest(snapshot),
              'features':design('late'),'classification':True,'proxy':proxy_manifest(self.root),'data_params':{'proxy_weight':.5},
              'split':{'mode':'exam','pass':'exam','val_start_s':exam,'val_end_s':10**11},'id':'e','model_path':str(self.root/'e.joblib'),'result':str(self.root/'e.json')}
        atomic_json(self.root/'e.job.json',spec);fit_job(str(self.root/'e.job.json'))
        m=json.loads((self.root/'e.json').read_text(encoding='utf-8'))['metrics']
        self.assertEqual(m['split'],'exam');self.assertEqual(m['n'],sum(1 for r in rows if r['start_s']>=exam))   # real rows only, no proxy
        self.assertLess(m['trained_through_ms'],(exam-600)*1000);self.assertGreater(m['proxy_train_n'],0)
    def test_every_packaged_module_compiles(self):
        # PyInstaller silently drops modules with syntax errors (e.g. the GUI, which no other test imports).
        import py_compile
        base=Path(__file__).resolve().parents[1]
        for path in [*(base/'trainstation').glob('*.py'),*(base/'poly5').glob('*.py'),base/'trainer_main.py']:
            py_compile.compile(str(path),cfile=str(self.root/'x.pyc'),doraise=True)
    def test_state_write_survives_transient_windows_lock(self):
        from poly5.common import atomic_json
        import os
        real=os.replace;calls=[]
        def flaky(a,b):
            calls.append(1)
            if len(calls)<3:raise PermissionError(5,'拒绝访问')
            real(a,b)
        with patch('poly5.common.os.replace',side_effect=flaky):atomic_json(self.root/'state.json',{'ok':True})
        self.assertEqual(json.loads((self.root/'state.json').read_text(encoding='utf-8')),{'ok':True});self.assertEqual(len(calls),3)
    def test_ai_empty_reasoning_reply_is_recorded_and_shown_while_waiting(self):
        reference_secret_store(self.root).save('ai-supervisor','fixture-key')
        config={'ai':{'enabled':True,'base_url':'https://fixture.invalid/v1','model':'test','interval_minutes':30}}
        response=MagicMock();response.status_code=200;response.json.return_value={'choices':[{'message':{'content':'','reasoning_content':'...'},'finish_reason':'length'}]}
        client=MagicMock();client.__enter__.return_value.post.return_value=response
        with patch('httpx.Client',return_value=client),patch('trainstation.supervisor.time.time',return_value=1000000):
            self.assertIn('finish_reason=length',review(self.root,config,{})['status'])
            waiting=review(self.root,config,{})['status']
        self.assertIn('失败',waiting);self.assertIn('finish_reason=length',waiting)
        calls=(self.root/'ai/calls.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(calls),1);self.assertFalse(json.loads(calls[0])['ok'])

if __name__=='__main__':unittest.main()
