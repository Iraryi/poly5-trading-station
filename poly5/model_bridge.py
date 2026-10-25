"""Pin trainer-owned artifacts into immutable local shadow releases. Never execute orders."""
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import uuid
import threading
from .common import atomic_json,epoch_ms

# Live and PAPER both ask the same pinned models every second of the last minute. One prediction per release, market
# and second is shared, and models run single-threaded: duplicated multi-threaded inference starved the market
# WebSocket loop (2026-10-24: messages fell 6.8 s behind during a last minute, so books went stale exactly then).
_PREDICTIONS={};_PREDICTIONS_LOCK=threading.Lock()

def single_threaded(model):
    """Set n_jobs=1 on the estimator and every nested estimator (calibrators, pipelines)."""
    seen=set();stack=[model]
    while stack:
        o=stack.pop()
        if id(o) in seen:continue
        seen.add(id(o))
        if hasattr(o,'n_jobs'):
            try:o.n_jobs=1
            except AttributeError:pass
        for name in ('estimator','base_estimator','calibrated_classifiers_','steps','named_steps'):
            v=getattr(o,name,None)
            if v is None:continue
            items=v.values() if isinstance(v,dict) else v if isinstance(v,(list,tuple)) else [v]
            for x in items:stack.append(x[1] if isinstance(x,tuple) and len(x)==2 else x)
    return model

def shared_prediction(release_id,market_id,now_ms,compute):
    key=(release_id,market_id,now_ms//1000)
    with _PREDICTIONS_LOCK:
        if key in _PREDICTIONS:return _PREDICTIONS[key]
    value=compute()
    with _PREDICTIONS_LOCK:
        if len(_PREDICTIONS)>256:_PREDICTIONS.clear()
        _PREDICTIONS[key]=value
    return value

TASK_NAMES={'late':'末分钟方向','direction':'全阶段方向','roi5':'5 秒净 ROI','roi15':'15 秒净 ROI',
            'late_mkt':'末分钟盘口方向','direction_mkt':'全阶段盘口方向'}

def read(path,default=None):
    try:
        if Path(path).stat().st_size>2*1024*1024:return default
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return default

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

class ModelBridge:
    def __init__(self,root,trainer_root='D:/POLYMARKET/trainer'):
        self.root=Path(root)/'models';self.root.mkdir(parents=True,exist_ok=True)
        self.trainer=Path(trainer_root).resolve()

    def _artifact(self,meta):
        path=Path(meta['model_path']).resolve()
        if not path.is_relative_to((self.trainer/'models').resolve()) or path.suffix!='.joblib' or not path.is_file():
            raise ValueError('模型文件必须来自本机训练目录 models')
        if path.stat().st_size>256*1024*1024 or sha(path)!=meta.get('model_sha256'):
            raise ValueError('模型文件哈希或大小不合格')
        if meta.get('status')!='complete' or meta.get('task') not in TASK_NAMES:
            raise ValueError('模型尚未完成或任务不兼容')
        if not re.fullmatch(r'[A-Za-z0-9_]{1,180}',meta.get('id','')):raise ValueError('模型版本名非法')
        if not meta.get('features') or not all(isinstance(k,str) for k in meta['features']):
            raise ValueError('模型缺少明确特征契约；请使用新版训练模型')
        return path

    def catalog(self):
        result=[]
        for task,title in TASK_NAMES.items():
            parent=read(self.trainer/'tasks'/task/'best.json')
            if not parent:continue
            meta=parent.get('deploy') or parent
            if not isinstance(meta,dict):continue
            frozen=read(self.trainer/'tasks'/task/'frozen.json',{})
            try:self._artifact(meta);status='可用于影子预测'
            except (ValueError,OSError,KeyError):status='文件校验失败 / 契约不兼容'
            result.append({'task':task,'name':title,'id':meta.get('id'),'family':meta.get('family'),
                'generation':meta.get('generation'),'status':status,'metrics':parent.get('metrics',{}),
                'refit_rows':meta.get('metrics',{}).get('refit_rows'),'frozen_id':frozen.get('id')})
        return result

    def pin(self,task,expected_id):
        if task not in TASK_NAMES:raise ValueError('未知模型任务')
        parent=read(self.trainer/'tasks'/task/'best.json') or {}
        meta=parent.get('deploy') or parent
        if meta.get('id')!=expected_id:raise ValueError('训练台候选已更新，请刷新列表后重新选择')
        return self._pin_meta(task,meta,parent.get('metrics',{}))

    def vote_candidates(self,task='late',k=5):
        """Top-k candidates of the trainer's last final exam: scored only on live markets no selection had seen."""
        import sqlite3
        if task!='late':raise ValueError('投票池目前只支持末分钟方向模型')
        exam=((read(self.trainer/'pass2.json') or {}).get('tasks') or {}).get(task) or {}
        if not exam.get('exam_done'):raise ValueError('训练台尚未完成该任务的终考')
        conn=sqlite3.connect('file:'+(self.trainer/'training.sqlite').as_posix()+'?mode=ro',uri=True,timeout=10)
        try:rows=[json.loads(b) for (b,) in conn.execute('SELECT body FROM trials WHERE created_ms BETWEEN ? AND ?',(exam['exam_started_ms']-60000,exam['exam_done_ms']+60000))]
        finally:conn.close()
        rows=[r for r in rows if r.get('task')==task and r.get('status')=='complete' and (r.get('metrics') or {}).get('split')=='exam']
        if len(rows)<k:raise ValueError('终考候选不足')
        return sorted(rows,key=lambda r:r['metrics']['score'])[:k]

    def pin_vote(self,task='late',k=5):
        """Pin the exam's top-k models as fixed releases without changing the single-model shadow selection."""
        existing={r.get('model_id'):r for r in self.releases() if r.get('origin')=='final_exam_top'}
        return [existing.get(meta['id']) or self._pin_meta(task,meta,meta['metrics'],activate=False,extra={'origin':'final_exam_top','exam_rank':rank})
                for rank,meta in enumerate(self.vote_candidates(task,k),1)]

    def _pin_meta(self,task,meta,metrics,activate=True,extra=None):
        path=self._artifact(meta)
        release_id=meta['id']+'_'+uuid.uuid4().hex[:8]
        folder=self.root/'releases'/release_id;folder.mkdir(parents=True,exist_ok=False)
        shutil.copy2(path,folder/'model.joblib')
        if sha(folder/'model.joblib')!=meta['model_sha256']:raise ValueError('复制期间模型发生变化，未激活')
        manifest={'schema_version':1,'release_id':release_id,'model_id':meta['id'],'task':task,
            'family':meta['family'],'features':meta['features'],'model_sha256':meta['model_sha256'],
            'trained_through_ms':meta['metrics']['trained_through_ms'],'metrics':metrics,
            'feature_contract':'trainer-1.8','created_ms':epoch_ms(),'scope':'shadow_only','live_approved':False,**(extra or {})}
        atomic_json(folder/'manifest.json',manifest)
        if activate:self.activate(release_id)
        return manifest

    def releases(self):
        rows=[read(p) for p in (self.root/'releases').glob('*/manifest.json')]
        return sorted([r for r in rows if r],key=lambda r:r.get('created_ms',0),reverse=True)

    def activate(self,release_id):
        if not re.fullmatch(r'[A-Za-z0-9_]{1,200}',release_id or ''):raise ValueError('版本名非法')
        folder=(self.root/'releases'/release_id).resolve()
        if not folder.is_relative_to((self.root/'releases').resolve()):raise ValueError('版本目录越界')
        manifest=read(folder/'manifest.json')
        if not manifest or manifest.get('schema_version')!=1 or manifest.get('scope')!='shadow_only' or manifest.get('live_approved') is not False:
            raise ValueError('模型发布契约不兼容')
        if manifest.get('release_id')!=release_id or sha(folder/'model.joblib')!=manifest['model_sha256']:
            raise ValueError('版本文件校验失败')
        atomic_json(self.root/'selected.json',{'release_id':release_id,'manifest_sha256':sha(folder/'manifest.json')})
        return manifest

    def selected(self):
        pointer=read(self.root/'selected.json')
        if not pointer:return None
        rid=pointer.get('release_id','')
        if not re.fullmatch(r'[A-Za-z0-9_]{1,200}',rid):raise ValueError('模型指针非法')
        folder=self.root/'releases'/rid
        if sha(folder/'manifest.json')!=pointer.get('manifest_sha256'):raise ValueError('模型清单已改变，需重新选择')
        manifest=read(folder/'manifest.json')
        if manifest.get('schema_version')!=1 or manifest.get('feature_contract')!='trainer-1.8' or manifest.get('scope')!='shadow_only':
            raise ValueError('模型特征或发布契约不支持')
        if sha(folder/'model.joblib')!=manifest['model_sha256']:raise ValueError('固定模型文件已改变，已停止预测')
        return manifest

    def release(self,release_id):
        if not re.fullmatch(r'[A-Za-z0-9_]{1,200}',release_id or ''):raise ValueError('版本名非法')
        folder=self.root/'releases'/release_id;meta=read(folder/'manifest.json')
        if not meta or meta.get('release_id')!=release_id or meta.get('schema_version')!=1 or meta.get('feature_contract')!='trainer-1.8' or meta.get('scope')!='shadow_only':
            raise ValueError('固定版本契约不兼容')
        if sha(folder/'model.joblib')!=meta.get('model_sha256'):raise ValueError('固定模型校验失败')
        return meta

class ShadowRuntime:
    def __init__(self,bridge):self.bridge=bridge;self.loaded=None;self.bundle=None
    def predict(self,features,now_ms,market):
        # Only a manually pinned copy can reach joblib. Hash validation precedes deserialization.
        meta=self.bridge.selected()
        if not meta:raise ValueError('尚未选择固定模型')
        task=meta['task'];stage=float(features['stage_fraction'])
        if not 0<=stage<1:raise ValueError('当前市场已结束或尚未开始')
        if task.startswith('late') and stage<.8:raise ValueError('末分钟模型等待最后 60 秒')
        if meta['trained_through_ms']>=now_ms:raise ValueError('模型训练截止时间不早于本次预测')
        if self.loaded!=meta['release_id']:
            import joblib
            # Explicit imports also make the packaged inference runtime complete.
            from sklearn import ensemble,linear_model,calibration,pipeline,preprocessing
            self.bundle=joblib.load(self.bridge.root/'releases'/meta['release_id']/'model.joblib');single_threaded(self.bundle['model'])
            if self.bundle['task']!=task or self.bundle['features']!=meta['features']:
                self.bundle=None;raise ValueError('模型内容与清单不一致')
            self.loaded=meta['release_id']
        from trainstation.data import vector
        values=vector(features,meta['features'])
        if not all(math.isfinite(float(x)) for x in values):raise ValueError('模型特征存在缺失或非法值')
        from threadpoolctl import threadpool_limits
        with threadpool_limits(limits=1):
            model=self.bundle['model'];classification=task not in ('roi5','roi15')
            compute=lambda:float(model.predict_proba([values])[0,1] if classification else model.predict([values])[0])
            value=shared_prediction(meta['release_id'],getattr(market,'id',None),now_ms,compute) if getattr(market,'id',None) else compute()
        if not math.isfinite(value) or (classification and not 0<=value<=1):raise ValueError('预测结果非法')
        return {'model_id':meta['model_id'],'release_id':meta['release_id'],'task':task,'scope':'shadow_only',
            'market_id':market.id,'condition_id':market.condition_id,'slug':market.slug,'at_ms':now_ms,
            'probability_up':value if classification else None,'predicted_net_roi':None if classification else value,
            'feature_count':len(values),'features':features,'live_approved':False}

def preload_inference():
    """Import the inference stack on the calling (main) thread. In the frozen build, first-time imports of these
    libraries inside a worker thread hung indefinitely (3.3.0-dev.3); after this, threads only run predictions."""
    import joblib,threadpoolctl
    from sklearn import ensemble,linear_model,calibration,pipeline,preprocessing,tree,dummy
    from trainstation.data import vector
    with threadpoolctl.threadpool_limits(limits=1):pass
    return True

class VoteRuntime:
    """Equal-weight average of several pinned late-direction releases (one probability, one stake).

    Each release is hash-verified before its first load and again whenever the market changes."""
    def __init__(self,bridge):self.bridge=bridge;self.bundles={};self.verified={}
    def predict(self,release_ids,features,now_ms,market):
        if not release_ids:raise ValueError('尚未固定投票模型')
        stage=float(features['stage_fraction'])
        if not .8<=stage<1:raise ValueError('末分钟投票等待最后 60 秒')
        values={}
        from trainstation.data import vector
        from threadpoolctl import threadpool_limits
        for rid in release_ids:
            if self.verified.get(rid)!=market.id:
                meta=self.bridge.release(rid)
                if meta['task']!='late':raise ValueError('投票池只接受末分钟方向模型')
                if meta['trained_through_ms']>=now_ms:raise ValueError('模型训练截止时间不早于本次预测')
                if rid not in self.bundles:
                    import joblib
                    from sklearn import ensemble,linear_model,calibration,pipeline,preprocessing
                    bundle=joblib.load(self.bridge.root/'releases'/rid/'model.joblib');single_threaded(bundle['model'])
                    if bundle['task']!='late' or bundle['features']!=meta['features']:raise ValueError('模型内容与清单不一致')
                    self.bundles[rid]=bundle
                self.verified[rid]=market.id
            bundle=self.bundles[rid];row=vector(features,bundle['features'])
            if not all(math.isfinite(float(x)) for x in row):raise ValueError('模型特征存在缺失或非法值')
            def compute(model=bundle['model'],row=row):
                with threadpool_limits(limits=1):return float(model.predict_proba([row])[0,1])
            value=shared_prediction(rid,market.id,now_ms,compute)
            if not (math.isfinite(value) and 0<=value<=1):raise ValueError('预测结果非法')
            values[rid]=value
        return {'probability_up':sum(values.values())/len(values),'models':values,'at_ms':now_ms,'market_id':market.id,'scope':'paper_vote'}

def reference_features(tape,market,now):
    # Same received-time feature contract as trainer 1.8; no current price is substituted for a missing opening.
    from bisect import bisect_right
    import statistics
    if not tape.connected:raise ValueError('等待参考行情连接')
    spot=[p for p in tape.points['spot'] if p.received_ms<=now];twap=[p for p in tape.points['twap'] if p.received_ms<=now]
    if not spot or not twap or min(now-spot[-1].observed_ms,now-twap[-1].observed_ms)<-500 or max(now-spot[-1].observed_ms,now-twap[-1].observed_ms)>5000:
        raise ValueError('参考行情过期')
    if now<market.start*1000+5000:raise ValueError('等待开局基准冻结')
    baseline=tape.baselines.get(market.id)
    if baseline is None:
        opening=[p for p in twap if 0<=market.start*1000-p.observed_ms<=5000]
        if not opening:raise ValueError('本轮基准缺失，等待下一轮')
        baseline=opening[-1];tape.baselines[market.id]=baseline
    received=[p.received_ms for p in spot];history=[]
    for query in range(now-60000,now+1,1000):
        i=bisect_right(received,query)-1
        if i<0 or query-spot[i].observed_ms>5000:raise ValueError('积累 60 秒参考历史')
        history.append(float(spot[i].value))
    sp=float(spot[-1].value);tp=float(twap[-1].value);base=float(baseline.value)
    steps=[math.log(b/a) for a,b in zip(history,history[1:])]
    return {'spot_open_bps':(sp/base-1)*1e4,'twap_open_bps':(tp/base-1)*1e4,'spot_twap_bps':(sp/tp-1)*1e4,
        'return_5s_bps':math.log(history[-1]/history[-6])*1e4,'return_15s_bps':math.log(history[-1]/history[-16])*1e4,
        'return_60s_bps':math.log(history[-1]/history[0])*1e4,'vol_60s_bps':statistics.stdev(steps)*1e4,
        'stage_fraction':(now-market.start*1000)/300000}

