"""Evolution supervisor: isolated fits, durable checkpoints and automatic collection, for every task in rotation."""
import asyncio
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import threading
import time
import uuid
from .data import Data,TASKS,ACTIVE_TASKS,LEGACY_TASKS,MARKET_TASKS,MIN_ENTRY_PRICE,design,import_seed,read,digest,proxy_manifest,proxy_count,replay_days,verdict
from .modeling import FAMILIES,CALIBRATIONS,propose
from .ai_policy import AI_SLOTS,complete,slots_for,history as ai_history,report as ai_report,breaker as ai_breaker
from poly5.common import atomic_json
from poly5.locking import WorkerLock

def review_with_heartbeat(review,root,config,summary,meta,publish,stopped):
    """A slow AI HTTP request must not make a healthy worker look dead to the GUI."""
    result=[];errors=[]
    def invoke():
        try:result.append(review(root,config,summary,meta))
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=invoke,daemon=True);thread.start()
    while thread.is_alive():
        thread.join(.5)
        if stopped():publish(phase='正在停止 · 等待已有 AI 请求结束并保存')
        else:publish()
    if errors:raise errors[0]
    return result[0]

# Training weight of the proxy backfill relative to real Chainlink rows; 0 means not used. Evolved and judged
# only on real validation data, so the program decides whether the proxy helps.
PROXY_WEIGHTS=[0.,.25,.5,1.]
# Candidates run in isolated processes, each limited to 2 threads; leave most of the machine to the user.
PARALLEL=max(2,min(6,(os.cpu_count() or 4)//5))
POPULATION=8      # candidates per generation
BATCH_TRIALS=64   # candidates per data batch (8 generations) before waiting for new data or the next replay day
CANDIDATE_TIMEOUT_S=600
DEPLOY_TIMEOUT_S=900

def command(*args):
    return [sys.executable,*args] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).resolve().parents[1]/'trainer_main.py'),*args]

def launch(args,log=None):
    kwargs={}
    if os.name=='nt':kwargs['creationflags']=subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log or subprocess.DEVNULL,stderr=log or subprocess.DEVNULL,**kwargs)

def task_file(root,task,name):
    folder=Path(root)/'tasks'/task;folder.mkdir(parents=True,exist_ok=True)
    return folder/name

def migrate_legacy(root):
    # Before 1.4.0 one task's state lived at the root. Copy it into tasks/<task>/ once; originals stay untouched.
    legacy=read(root/'evolution.json')
    if not legacy or not legacy.get('task_scope'):return
    task=legacy['task_scope'].split(':')[0]
    if task not in TASKS or task_file(root,task,'evolution.json').exists():return
    for old,new in [('evolution.json','evolution.json'),('research_best.json','best.json'),('registry.json','registry.json'),('forward_rejected.json','forward_rejected.json')]:
        if (root/old).exists():shutil.copy2(root/old,task_file(root,task,new))

def run(root,seed_folder,offline=False):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with WorkerLock(root):
        data=Data(root);run_id=uuid.uuid4().hex;stop=threading.Event();collector_state={};collector_thread=None
        state={'run_id':run_id,'pid':os.getpid(),'running':True,'phase':'导入历史数据','generation':0,'active':0,'ai_status':'等待阶段总结','tasks':{}}
        def publish(**updates):
            state.update(updates);state['heartbeat_ms']=int(time.time()*1000);state['collector']=dict(collector_state)
            atomic_json(root/'state.json',state)
        def publish_task(task,**updates):
            state['tasks'].setdefault(task,{}).update(updates);publish()
        def stopped():
            request=read(root/'stop.json',{})
            return request.get('run_id')==run_id
        def live_id(champion):
            deploy=champion.get('deploy') or {}
            return deploy['id'] if deploy.get('status')=='complete' and deploy.get('refit_of')==champion['id'] else champion['id']
        def forward_check(task):
            champion=read(task_file(root,task,'best.json'));registry=read(task_file(root,task,'registry.json'),[]) or []
            if task=='dynamic':
                from .dynamic_forward import forward_summary
                paper=forward_summary(data)
                frozen=read(task_file(root,task,'frozen.json'))
                f=({'id':frozen['id'],'since_ms':frozen['frozen_ms'],
                    'paper':forward_summary(data,[frozen['id']]),'verdict':'动态逐笔验证中'} if frozen else None)
                publish_task(task,best=champion,paper=paper,live_pooled={},frozen=f,
                             qualification='动态持仓价值 / 扣费择机止盈；没有固定秒数退出；研究模型不自动接入实盘')
                return
            if not champion:return
            for contender in registry:
                if contender['id'] in (champion['id'],live_id(champion)) or contender['task']!=champion['task']:continue
                paired=data.paired_forward(live_id(champion),contender['id'])
                if paired['n']>=20 and paired['first_mse']>paired['second_mse']*1.15+.0001:
                    rejected=read(task_file(root,task,'forward_rejected.json'),[]) or []
                    if champion['id'] not in rejected:rejected.append(champion['id'])
                    atomic_json(task_file(root,task,'forward_rejected.json'),rejected)
                    atomic_json(task_file(root,task,'best.json'),contender)
                    data.event('automatic_forward_rollback',{'task':task,'from':champion['id'],'to':contender['id'],'paired':paired})
                    break
            champion=read(task_file(root,task,'best.json'));champion['forward']=data.forward(live_id(champion))
            pooled=data.deployed_forward(task) if TASKS[task][2]=='classification' else {}
            frozen=read(task_file(root,task,'frozen.json'))
            if frozen:
                # The original registration keeps its original rule (no longshot filter). The filtered rule is a second,
                # separately pre-registered test that only counts predictions made after it was registered.
                if 'filtered_since_ms' not in frozen:
                    frozen['filtered_since_ms']=int(time.time()*1000);atomic_json(task_file(root,task,'frozen.json'),frozen)
                    data.event('frozen_rule_registered',{'task':task,'id':frozen['id'],'min_price':MIN_ENTRY_PRICE})
                paper=data.forward_paper(task,[frozen['id']],since_ms=frozen['frozen_ms'],min_price=frozen.get('min_price',0.)).get(frozen['id'])
                filtered=data.forward_paper(task,[frozen['id']],since_ms=frozen['filtered_since_ms']).get(frozen['id'])
                frozen={'id':frozen['id'],'since_ms':frozen['frozen_ms'],'paper':paper,'verdict':verdict(paper),
                        'filtered_since_ms':frozen['filtered_since_ms'],'filtered_paper':filtered,'filtered_verdict':verdict(filtered)}
            publish_task(task,best=champion,live_pooled=pooled,paper=data.forward_paper(task).get('deployed'),frozen=frozen)
        def ensure_deploy(checkpoint,task):
            # The selected configuration only saw part of the history; retrain it on all labelled rows before it
            # predicts live. The registry then carries the refit, so forward scores measure it.
            champion=read(task_file(root,task,'best.json'))
            if not champion or champion['task']!=task or not checkpoint.get('snapshot'):return
            deploy=champion.get('deploy') or {}
            if deploy.get('refit_of')!=champion['id']:
                id=champion['id']+'_FULL';folder=root/'models'/task/champion['family']/('G'+str(champion['generation']).zfill(4));folder.mkdir(parents=True,exist_ok=True)
                spec={k:champion[k] for k in ('scope','task','family','params','generation','seed','classification','metrics')}
                spec.update(id=id,parents=[champion['id']],refit_of=champion['id'],features=champion.get('features') or TASKS[task][1],
                    proxy=checkpoint.get('proxy',[]) if (champion.get('data_params') or {}).get('proxy_weight') else [],data_params=champion.get('data_params') or {},
                    snapshot=checkpoint['snapshot'],snapshot_sha256=checkpoint['snapshot_sha256'],
                    model_path=str(folder/(id+'.joblib')),result=str(folder/(id+'.json')),created_ms=int(time.time()*1000))
                path=folder/(id+'.job.json');atomic_json(path,spec)
                publish(phase=TASKS[task][0]+' · 最优模型用全部有标签样本重训（部署版）',active=1)
                log=open(folder/(id+'.log'),'ab');proc=launch(command('--fit',str(path)),log);began=time.monotonic()
                while proc.poll() is None and time.monotonic()-began<DEPLOY_TIMEOUT_S and not stopped():time.sleep(.5);publish()
                if proc.poll() is None:proc.terminate();proc.wait(timeout=10)
                log.close();publish(active=0)
                if stopped():return
                result=read(spec['result'])
                if not result or result.get('status')!='complete':
                    result={**spec,'status':'rejected','error':'部署版重训失败或超时；继续使用选优模型，日志已留档'}
                    atomic_json(spec['result'],result)
                data.event('deploy_refit',{'id':id,'refit_of':champion['id'],'status':result['status'],'rows':(result.get('metrics') or {}).get('refit_rows')})
                champion['deploy']=result;atomic_json(task_file(root,task,'best.json'),champion);deploy=result
            if deploy.get('status')!='complete':return
            registry=read(task_file(root,task,'registry.json'),[]) or []
            if any(r['id']==deploy['id'] for r in registry):return
            registry=[r for r in registry if r['id']!=champion['id']]
            atomic_json(task_file(root,task,'registry.json'),[deploy]+registry)
            freeze(task,deploy)
        def freeze(task,deploy):
            # Pre-registered confirmation: the first deployed market-aware champion is frozen and keeps predicting live,
            # never retrained or replaced; only predictions made after the freeze count towards its verdict.
            path=task_file(root,task,'frozen.json')
            if (task in MARKET_TASKS or task=='dynamic') and not path.exists():
                atomic_json(path,{**deploy,'frozen_ms':int(time.time()*1000),'filtered_since_ms':int(time.time()*1000),'min_price':0.,'rule':'预先登记：冻结后不改动；≥200 笔且 95% 区间下限 > 0 才算确认盈利；≥50 笔且区间上限 < 0 判定否定'})
                data.event('frozen_for_confirmation',{'task':task,'id':deploy['id']})
        def ai_context(selected):
            """Everything the AI sees each review, plus the paired AI-vs-control report and circuit breaker update."""
            now=int(time.time()*1000)
            with data.db() as db:recent=[json.loads(b) for (b,) in db.execute('SELECT body FROM trials WHERE created_ms>?',(now-24*3600000,))]
            for t in recent:t['created_ms']=t.get('created_ms') or 0
            stats=ai_report(recent,now)
            current=read(root/'ai/breaker.json',{}) or {}
            if 'until_ms' in current and 'global' not in current:current={'global':{k:v for k,v in current.items() if k!='tasks'},'tasks':current.get('tasks') or {}}  # pre-probation format
            updated=ai_breaker(recent,current,now)
            if updated!=current:
                atomic_json(root/'ai/breaker.json',updated)
                data.event('ai_circuit_breaker',updated)
            slots={t:slots_for(t,recent,updated,now) for t in selected}
            # Full track record (all AI candidates since v2), refreshed at most every 10 minutes.
            if now-history_cache.get('at',0)>600000:
                with data.db() as db:everything=[json.loads(b) for (b,) in db.execute('SELECT body FROM trials WHERE body LIKE ?',('%"origin"%',))]
                history_cache.update(at=now,table=ai_history(everything))
            table,poor=history_cache['table']
            publish(ai_report={**stats,'breaker':updated,'slots':slots})
            advice=read(root/'ai/latest.json',{}) or {};previous={}
            for t in recent:
                if t.get('origin')=='ai' and t.get('ai_review_ms')==advice.get('at_ms') and t.get('metrics'):
                    previous.setdefault(t['task'],[]).append({'family':t['family'],'params':t['params'],'data_params':t.get('data_params'),'score':round(t['metrics']['score'],5)})
            tasks={}
            for t in selected:
                champion=read(task_file(root,t,'best.json')) or {};m=champion.get('metrics') or {};info=state['tasks'].get(t,{})
                tasks[t]={'label':TASKS[t][0],'classification':TASKS[t][2]=='classification','generation':info.get('generation'),'labelled':(info.get('stats') or {}).get('labelled'),
                    'champion':{'family':champion.get('family'),'params':champion.get('params'),'data_params':champion.get('data_params'),
                                **{k:m.get(k) for k in ('score','baseline_score','skill_vs_market','ece','log_loss','paper_pnl')}} if champion else None,
                    'qualification':info.get('qualification'),'live_paper':info.get('paper'),'frozen':(info.get('frozen') or {}).get('verdict')}
            meta={t:{'classification':TASKS[t][2]=='classification','has_proxy':t in ('late','direction') and bool(proxy_manifest(root))} for t in selected}
            for t in meta:tasks[t]['has_proxy']=meta[t]['has_proxy']
            meta['_avoid']=poor
            return {'tasks':tasks,'ai_vs_control':stats,'previous_proposals':previous,'breaker':updated,'ai_slots_per_task':slots,'ai_track_record':table},meta
        history_cache={};shadow_state={'at':0}
        def shadow_update():
            # Trading-station 3.3 rule: paper shadow ledger from the order-book recorder, refreshed every 10 minutes.
            if time.monotonic()-shadow_state['at']<600:return
            shadow_state['at']=time.monotonic()
            try:
                from .shadow33 import update
                publish(shadow33=update(data))
            except Exception as exc:publish(shadow33={'error':type(exc).__name__+': '+str(exc)[:160]})
        def prepare(task,family,config,rollback_task):
            """Bring one task's batch up to date; returns (checkpoint, markets, trainable)."""
            evolution=task_file(root,task,'evolution.json')
            checkpoint=read(evolution,{'scope':'','generation':0,'trials':0,'base_markets':0})
            rows=data.snapshot(task);markets=len({r['slug'] for r in rows})
            scope_key=task+':'+family;manifest=proxy_manifest(root) if task in ('late','direction') else []
            dataset_changed=checkpoint.get('task_scope')!=scope_key or markets>=checkpoint.get('base_markets',0)+12 or manifest!=checkpoint.get('proxy',[])
            from .dynamic import DATA_REVISION
            if task=='dynamic' and checkpoint.get('data_revision')!=DATA_REVISION:dataset_changed=True
            # Historical fast-forward: while unjudged history days remain, each finished batch moves on to the next
            # day instead of waiting an hour for 12 live markets. Every day is validation data exactly once.
            replay=read(root/'replay.json',{}) or {};schedule=replay_days(manifest,rows) if manifest else []
            pending=[d for d in schedule if d>replay.get(task,0)]
            replay_next=pending[0] if pending and (checkpoint['trials']>=BATCH_TRIALS or dataset_changed or not checkpoint['scope']) else None
            # Second pass + final exam (pass2.json): replay the judged history again with today's stronger search, using
            # each day's winner only as the next day's parent (never deployed); then one exam batch validated only on
            # live markets that no selection has seen, with the incumbent's exact configuration competing too.
            pass2=read(root/'pass2.json',{}) or {};p2=(pass2.get('tasks') or {}).get(task)
            p2_next=None
            if p2 and not p2.get('exam_done') and not replay_next:
                in_progress=(checkpoint.get('split') or {}).get('pass') in (2,'exam') and checkpoint['trials']<BATCH_TRIALS
                if not in_progress:
                    days=[d for d in schedule if d>p2.get('cursor',0) and d+86400<=pass2['exam_start_s']]
                    p2_next=('pass2',days[0]) if days else ('exam',None)
            if markets>=60 and (not checkpoint['scope'] or dataset_changed or replay_next or p2_next):
                identity=[(r['id'],r['y']) for r in rows]
                if task=='dynamic':identity=[DATA_REVISION,identity]
                rows_hash=hashlib.sha256(json.dumps(identity).encode()).hexdigest()
                fingerprint=hashlib.sha256(json.dumps([rows_hash,[m['sha256'] for m in manifest],replay_next]+([list(p2_next)] if p2_next else [])).encode()).hexdigest()
                # Warm start (population-based training style): the new data batch inherits the previous
                # batch's best configurations as parents instead of restarting from random parameters.
                inherited=[]
                for record in [read(task_file(root,task,'best.json')) or {}]+(read(task_file(root,task,'registry.json'),[]) or []):
                    if record.get('task')==task and record.get('family') in FAMILIES and not record.get('refit_of') and record['id'] not in [r['id'] for r in inherited]:
                        inherited.append({'id':record['id'],'family':record['family'],'params':record['params'],'data_params':record.get('data_params') or {}})
                if task=='dynamic' and not inherited:
                    from .dynamic_dataset import freeze_priors
                    for _,meta in freeze_priors(root).values():
                        inherited.append({'id':meta['id'],'family':meta['family'],'params':meta['params'],
                                          'data_params':{},'transferred_from':'legacy fixed-horizon estimator, refit on new objective'})
                if checkpoint.get('scope'):
                    atomic_json(root/'checkpoints'/task/(hashlib.sha256(checkpoint['scope'].encode()).hexdigest()[:16]+'.json'),checkpoint)
                checkpoint={'scope':scope_key+':'+fingerprint[:16],'task_scope':scope_key,'generation':0,'trials':0,'base_markets':markets,'seed_parents':inherited[:2],
                    'proxy':manifest,'proxy_rows':proxy_count(manifest,task)}
                if task=='dynamic':checkpoint['data_revision']=DATA_REVISION
                if p2_next:
                    seeds=[read(task_file(root,task,'pass2_best.json'))]+[read(task_file(root,task,'best.json'))]
                    seeds=[{'id':r['id'],'family':r['family'],'params':r['params'],'data_params':r.get('data_params') or {}} for r in seeds if r and r.get('family') in FAMILIES]
                    if seeds:checkpoint['seed_parents']=seeds[:2]
                    if p2_next[0]=='pass2':
                        day=p2_next[1];days=[d for d in schedule if d+86400<=pass2['exam_start_s']]
                        checkpoint['split']={'mode':'replay','pass':2,'val_start_s':day,'val_end_s':day+86400,
                            'day':datetime.fromtimestamp(day,timezone.utc).strftime('%Y-%m-%d'),'index':days.index(day)+1,'total':len(days)}
                        p2['cursor']=day
                    else:
                        incumbent=read(task_file(root,task,'best.json'))
                        checkpoint['split']={'mode':'exam','pass':'exam','val_start_s':pass2['exam_start_s'],'val_end_s':10**11,
                            'day':'实时 '+datetime.fromtimestamp(pass2['exam_start_s'],timezone.utc).strftime('%m-%d %H:%M')+' UTC 起','index':1,'total':1}
                        if incumbent and incumbent.get('family') in FAMILIES:
                            checkpoint['incumbent']={'id':incumbent['id'],'family':incumbent['family'],'params':incumbent['params'],'data_params':incumbent.get('data_params') or {}}
                        p2['exam_started_ms']=int(time.time()*1000)
                    pass2['tasks'][task]=p2;atomic_json(root/'pass2.json',pass2)
                elif replay_next:
                    checkpoint['split']={'mode':'replay','val_start_s':replay_next,'val_end_s':replay_next+86400,
                        'day':datetime.fromtimestamp(replay_next,timezone.utc).strftime('%Y-%m-%d'),'index':schedule.index(replay_next)+1,'total':len(schedule)}
                    replay[task]=replay_next;atomic_json(root/'replay.json',replay)
                snapshot=root/'snapshots'/(rows_hash+'.json')
                if not snapshot.exists():atomic_json(snapshot,rows)
                checkpoint['snapshot']=str(snapshot);checkpoint['snapshot_sha256']=digest(snapshot)
                atomic_json(evolution,checkpoint)
            split=checkpoint.get('split') or {}
            publish_task(task,generation=checkpoint['generation'],trials=checkpoint['trials'],stats={**data.stats(task),'proxy_rows':checkpoint.get('proxy_rows',0)},
                fresh_markets=max(0,markets-checkpoint.get('base_markets',0)),replay=((('第二轮 ' if split.get('pass')==2 else '终考 ' if split.get('pass')=='exam' else '')+f"{split['day']}（{split['index']}/{split['total']}）") if split else None))
            forward_check(task)
            ensure_deploy(checkpoint,task)
            rollback_requested=rollback_task==task and config['rollback_id']!=checkpoint.get('rollback_applied')
            trainable=markets>=60 and checkpoint.get('snapshot') and (checkpoint['trials']<BATCH_TRIALS or rollback_requested)
            return checkpoint,markets,bool(trainable)
        def generation_step(task,checkpoint,family,config,rollback_task):
            nonlocal children
            evolution=task_file(root,task,'evolution.json')
            if shutil.disk_usage(root).free<2*1024**3:raise RuntimeError('剩余磁盘不足 2GB，已保留进度并停止')
            with data.db() as db:
                prior=[json.loads(r[0]) for r in db.execute('SELECT body FROM trials WHERE scope=? AND score IS NOT NULL ORDER BY score',(checkpoint['scope'],))]
            generation=checkpoint['generation']+1
            parents=prior[:2] or checkpoint.get('seed_parents',[]);jobs=[];rng=random.Random(checkpoint['trials']+71022)
            rollback=config.get('rollback_id')
            if rollback_task==task and rollback!=checkpoint.get('rollback_applied'):
                with data.db() as db:record=db.execute('SELECT body FROM trials WHERE id=?',(rollback,)).fetchone()
                if record:
                    chosen=json.loads(record[0])
                    if digest(chosen['model_path'])==chosen['model_sha256']:
                        atomic_json(task_file(root,task,'best.json'),chosen);data.event('user_rollback',{'task':task,'to':rollback});parents=[chosen]
                checkpoint['rollback_applied']=rollback
            # Slots per generation: 0-3 exploit parents, 4 explore another algorithm, 5-6 AI proposals (program
            # exploit when the AI is off, stale, invalid or circuit-broken), 7 matched control = AI's family with
            # program-chosen parameters. Every candidate records its origin for the paired AI-vs-control comparison.
            advice=read(root/'ai/latest.json',{}) or {};breaker_state=read(root/'ai/breaker.json',{}) or {}
            fresh=int(time.time()*1000)-advice.get('at_ms',0)<12*3600000
            # Slots are earned per task: 0 in cooldown, 1 by default, 2 once AI beat its control significantly here.
            with data.db() as db:recent=[json.loads(b) for (b,) in db.execute('SELECT body FROM trials WHERE created_ms>?',(int(time.time()*1000)-24*3600000,))]
            ai_slots=slots_for(task,recent,breaker_state) if (config.get('ai') or {}).get('enabled') and fresh else 0
            ai_items=[a for a in ((advice.get('proposals') or {}).get(task) or []) if family=='auto' or a['family']==family] if ai_slots else []
            def program_data(parent):
                out={}
                if checkpoint.get('proxy'):
                    # Replay validation days are mostly proxy history, so the proxy cannot be switched off there.
                    choices=PROXY_WEIGHTS[1:] if (checkpoint.get('split') or {}).get('mode')=='replay' else PROXY_WEIGHTS
                    inherited_weight=(parent.get('data_params') or {}).get('proxy_weight') if parent else None
                    out['proxy_weight']=rng.choice(choices) if inherited_weight not in choices or rng.random()<.3 else inherited_weight
                if TASKS[task][2]=='classification':
                    inherited_cal=(parent.get('data_params') or {}).get('calibration') if parent else None
                    out['calibration']=rng.choice(CALIBRATIONS) if inherited_cal not in CALIBRATIONS or rng.random()<.3 else inherited_cal
                return out
            for i in range(POPULATION):
                origin='program';ai_item=None
                if 5<=i<5+ai_slots and ai_items:origin='ai';ai_item=ai_items[(i-5)%len(ai_items)]
                elif i==POPULATION-1:origin='control'
                if family!='auto':chosen_family=family
                elif origin=='ai':chosen_family=ai_item['family']
                elif origin=='control':chosen_family=ai_items[0]['family'] if ai_items else FAMILIES[(generation+i)%len(FAMILIES)]
                elif not parents:chosen_family=FAMILIES[i%len(FAMILIES)]
                elif i==4:chosen_family=FAMILIES[(generation+i)%len(FAMILIES)]
                else:chosen_family=parents[i%len(parents)]['family']
                parent=next((p for p in parents if p['family']==chosen_family),None)
                data_params=program_data(parent)
                if origin=='ai':
                    params=complete(chosen_family,ai_item['params'],rng,propose)
                    if i>5 and len(ai_items)==1:params=propose(chosen_family,rng,params)  # second slot: a variation of the single proposal
                    for key,value in ai_item['data_params'].items():
                        if key in data_params or key=='calibration':data_params[key]=value
                    if (checkpoint.get('split') or {}).get('mode')=='replay' and data_params.get('proxy_weight')==0.:data_params['proxy_weight']=PROXY_WEIGHTS[1]
                elif i==0 and generation==1 and checkpoint.get('incumbent') and family in ('auto',checkpoint['incumbent']['family']):
                    # Final exam: the incumbent's exact configuration competes on the same exam as the challengers.
                    inc=checkpoint['incumbent'];origin='incumbent';chosen_family=inc['family'];params=dict(inc['params']);data_params=dict(inc['data_params'])
                else:params=propose(chosen_family,rng,parent['params'] if parent else None)
                id=f'BTC5M_{task.upper()}_SK{chosen_family.upper()}_G{generation:04}_C{checkpoint["trials"]+i+1:05}_{uuid.uuid4().hex[:6]}'
                folder=root/'models'/task/chosen_family/('G'+str(generation).zfill(4));folder.mkdir(parents=True,exist_ok=True)
                spec={'id':id,'scope':checkpoint['scope'],'task':task,'family':chosen_family,'params':params,
                    'generation':generation,'parents':[parent['id']] if parent else [],'seed':rng.randrange(1,1000000),
                    'snapshot':checkpoint['snapshot'],'snapshot_sha256':checkpoint['snapshot_sha256'],
                    'model_path':str(folder/(id+'.joblib')),'result':str(folder/(id+'.json')),
                    'classification':TASKS[task][2]=='classification','features':design(task),'proxy':checkpoint.get('proxy',[]),'data_params':data_params,'split':checkpoint.get('split'),'origin':origin,
                    'ai_review_ms':advice.get('at_ms') if origin=='ai' else None,'ai_rationale':ai_item['rationale'] if ai_item else None,'created_ms':int(time.time()*1000)}
                path=folder/(id+'.job.json');atomic_json(path,spec);jobs.append((spec,path))
            queue=list(jobs);children=[]
            split=checkpoint.get('split') or {}
            label=TASKS[task][0]+((' · 第二轮历史快进' if split.get('pass')==2 else ' · 终考（只用从未参与选优的实时数据）' if split.get('pass')=='exam' else ' · 历史快进')+f" 验证日 {split['day']}（第 {split['index']}/{split['total']} 天）" if split else ' · 种群训练 / 程序判官评分')
            publish(phase=label,task=task,generation=generation,active=0)
            while (queue or children) and not stopped():
                while queue and len(children)<PARALLEL:
                    spec,path=queue.pop(0);log=open(Path(spec['result']).with_suffix('.log'),'ab')
                    proc=launch(command('--fit',str(path)),log);children.append((proc,spec,log,time.monotonic()))
                for proc,spec,log,began in list(children):
                    if proc.poll() is None and time.monotonic()-began<CANDIDATE_TIMEOUT_S:continue
                    if proc.poll() is None:proc.terminate();proc.wait(timeout=10)
                    log.close();children.remove((proc,spec,log,began));result=read(spec['result'])
                    if result is None:
                        result={**spec,'status':'rejected','error':f'候选训练失败或超过 {CANDIDATE_TIMEOUT_S} 秒；日志已留档','metrics':None}
                        atomic_json(spec['result'],result)
                    score=result['metrics']['score'] if result.get('metrics') else None
                    with data.db() as db:db.execute('INSERT OR IGNORE INTO trials VALUES(?,?,?,?,?,?,?,?)',(spec['id'],spec['scope'],generation,spec['family'],json.dumps(spec['params']),score,json.dumps(result,ensure_ascii=False),int(time.time()*1000)))
                    checkpoint['trials']+=1;atomic_json(evolution,checkpoint)
                    publish(active=len(children));publish_task(task,trials=checkpoint['trials'])
                time.sleep(.2);publish()
            for proc,spec,log,began in children:
                if proc.poll() is None:proc.terminate();proc.wait(timeout=10)
                log.close();atomic_json(Path(spec['result']).with_suffix('.cancelled.json'),{**spec,'status':'cancelled','reason':'用户停止；已完成模型与历史未删除'})
            children=[]
            if stopped():return
            checkpoint['generation']=generation;atomic_json(evolution,checkpoint);publish_task(task,generation=generation)
            with data.db() as db:ranked=[json.loads(r[0]) for r in db.execute('SELECT body FROM trials WHERE scope=? AND score IS NOT NULL ORDER BY score',(checkpoint['scope'],))]
            if ranked:
                # Qualification: beat the constant baseline; market-aware tasks must also beat the market's own price
                # (positive skill_vs_market on validation). The best qualifying candidate, not merely the best score.
                def qualifies(r):
                    m=r['metrics'];return m['score']<m['baseline_score'] and (task not in MARKET_TASKS or (m.get('skill_vs_market') or 0)>0)
                best=next((r for r in ranked if qualifies(r)),ranked[0]);qualified=qualifies(best);all_ranked=ranked;ranked=ranked[:3]
                current=read(task_file(root,task,'best.json'))
                rejected=read(task_file(root,task,'forward_rejected.json'),[]) or []
                split_now=checkpoint.get('split') or {}
                if split_now.get('pass')==2:
                    # Second pass: the day's winner only seeds the next day; the deployed champion is untouched.
                    if qualified:atomic_json(task_file(root,task,'pass2_best.json'),best)
                    publish(phase=TASKS[task][0]+' · 第二轮本代已归档');return
                if split_now.get('pass')=='exam' and checkpoint['trials']>=BATCH_TRIALS:
                    pass2=read(root/'pass2.json',{}) or {};p2=(pass2.get('tasks') or {}).get(task) or {}
                    inc=[(i,r) for i,r in enumerate(all_ranked) if r.get('origin')=='incumbent']
                    p2.update(exam_done=True,exam_done_ms=int(time.time()*1000),exam_best=best['id'],exam_best_origin=best.get('origin'),exam_best_score=best['metrics']['score'],
                              exam_candidates=len(all_ranked),incumbent_rank=inc[0][0]+1 if inc else None,incumbent_score=inc[0][1]['metrics']['score'] if inc else None)
                    p2['_finalize']=True;pass2.setdefault('tasks',{})[task]=p2;atomic_json(root/'pass2.json',pass2)
                if qualified and best['id'] not in rejected and (not current or current['scope']!=best['scope'] or best['metrics']['score']<current['metrics']['score']-.0001):
                    if current:data.event('archive_champion',{'task':task,'id':current['id'],'reason':'新候选历史验证评分更好；原文件保留'})
                    atomic_json(task_file(root,task,'best.json'),best);data.event('promote_research_candidate',{'task':task,'id':best['id'],'score':best['metrics']['score'],'deployment_allowed':False})
                if split_now.get('pass')=='exam' and checkpoint['trials']>=BATCH_TRIALS:
                    pass2=read(root/'pass2.json',{}) or {};p2=(pass2.get('tasks') or {}).get(task) or {}
                    champion=read(task_file(root,task,'best.json')) or {};p2.pop('_finalize',None)
                    p2.update(champion_after_exam=champion.get('id'),champion_after_exam_score=(champion.get('metrics') or {}).get('score'))
                    pass2.setdefault('tasks',{})[task]=p2;atomic_json(root/'pass2.json',pass2);data.event('pass2_exam_done',{'task':task,**p2})
                if task in MARKET_TASKS and not qualified:publish_task(task,qualification='本批没有候选战胜市场价格（skill_vs_market ≤ 0），不产生新冠军')
                elif task in MARKET_TASKS:publish_task(task,qualification=None)
                registry=[]
                for record in ranked[:2]+([current] if current and current['task']==task else ranked[2:3]):
                    if record['id'] not in [r['id'] for r in registry]:registry.append(record)
                atomic_json(task_file(root,task,'registry.json'),registry)
                from .supervisor import review
                summary,meta=ai_context(list(ACTIVE_TASKS) if config.get('task','all')=='all' else [task])
                feedback=review_with_heartbeat(review,root,config,summary,meta,publish,stopped)
                publish(ai_status=feedback['status'],phase=TASKS[task][0]+' · 本代已归档')
        children=[]
        try:
            publish()
            import_seed(data,seed_folder)
            # Extra real archives in the packaged format (e.g. PMData 08-08..08-13 ROI) dropped into root/archive.
            if (root/'archive').is_dir():import_seed(data,root/'archive')
            migrate_legacy(root)
            legacy_path=root/'dynamic/legacy-progress.json'
            if not legacy_path.exists():
                atomic_json(legacy_path,{'archived_ms':int(time.time()*1000),'tasks':{
                    t:{'best':read(task_file(root,t,'best.json')),'evolution':read(task_file(root,t,'evolution.json'))} for t in LEGACY_TASKS},
                    'rule':'历史模型、训练样本及进度全部保留；默认不再训练固定秒数 ROI 任务'})
            if not offline:
                def collect():
                    from .collector import Collector
                    while not stop.is_set():
                        try:asyncio.run(Collector(data,'D:/POLYMARKET/data',stop,collector_state).run())
                        except Exception as exc:
                            collector_state['error']=type(exc).__name__+': '+str(exc)[:120];collector_state['error_ms']=int(time.time()*1000)
                            stop.wait(5)
                collector_thread=threading.Thread(target=collect,daemon=True);collector_thread.start()
            turn=0
            while not stopped():
                config=read(root/'config.json',{'task':'all','family':'auto','library':'sklearn'})
                selected=list(ACTIVE_TASKS) if config.get('task','all')=='all' else [config.get('task')];family=config.get('family','auto')
                if any(t in LEGACY_TASKS for t in selected):
                    selected=['dynamic'];config['task']='dynamic';atomic_json(root/'config.json',config)
                if any(t not in TASKS for t in selected) or family not in FAMILIES+['auto'] or config.get('library','sklearn')!='sklearn':raise ValueError('训练配置不在已实现白名单')
                rollback_task=None
                if config.get('rollback_id'):
                    with data.db() as db:record=db.execute('SELECT body FROM trials WHERE id=?',(config['rollback_id'],)).fetchone()
                    rollback_task=json.loads(record[0])['task'] if record else None
                state['tasks']={t:v for t,v in state['tasks'].items() if t in selected}
                work=None
                # Round-robin: each task with work trains one generation, then the next task gets a turn.
                for k in range(len(selected)):
                    task=selected[(turn+k)%len(selected)]
                    checkpoint,markets,trainable=prepare(task,family,config,rollback_task)
                    if stopped():break
                    if trainable:work=(task,checkpoint);turn=(selected.index(task)+1)%len(selected);break
                if stopped():break
                if work:
                    # Busy replay/pass-2 stretches can last hours; keep the rate-limited shadow ledger current too.
                    generation_step(work[0],work[1],family,config,rollback_task);shadow_update();time.sleep(.2);continue
                waiting=' · '.join(f"{TASKS[t][0].split(' · ')[0]} {state['tasks'].get(t,{}).get('fresh_markets',0)}/12" for t in selected)
                publish(phase='持续采集 · 各任务新标签市场 '+waiting,active=0)
                from .supervisor import review
                shadow_update()
                summary,meta=ai_context(selected)
                feedback=review_with_heartbeat(review,root,config,summary,meta,publish,stopped)
                publish(ai_status=feedback['status'])
                for _ in range(10):
                    if stopped():break
                    time.sleep(1);publish()
        except Exception as exc:
            data.event('runner_error',{'type':type(exc).__name__,'message':str(exc)[:300]})
            publish(error=type(exc).__name__+': '+str(exc)[:300],phase='已保存进度 · 需要检查错误')
        finally:
            stop.set()
            for proc,spec,log,began in children:
                if proc.poll() is None:proc.terminate();proc.wait(timeout=10)
                if not log.closed:log.close()
            if collector_thread:
                collector_thread.join(timeout=18)
                while collector_thread.is_alive():
                    publish(phase='正在停止 · 等待采集与回填完成写盘')
                    collector_thread.join(timeout=1)
            publish(running=False,active=0,phase='已停止 · 模型和数据已保存')
