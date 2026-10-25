"""AI-proposed candidates with hard guardrails, a matched program control and a circuit breaker.

The AI only ever fills AI_SLOTS of each generation with whitelisted, clamped hyper-parameters. The program judge alone
decides promotion. Each generation also gets one control candidate (same family as the AI's first proposal, parameters
chosen by the program), so AI vs no-AI is measured as a paired comparison within the same generation and data.
"""
import json
import math
import random
import time

from .modeling import FAMILIES,CALIBRATIONS

AI_SLOTS=2
# Hard limits: deliberately wider than the program's own grids so the AI can explore, but every value is clamped here.
BOUNDS={
    'forest':{'n_estimators':('int',20,300),'max_depth':('int',2,20),'min_samples_leaf':('int',1,200),'max_features':('float',.1,1.),'max_samples':('float_or_none',.1,1.)},
    'extra':{'n_estimators':('int',20,300),'max_depth':('int',2,20),'min_samples_leaf':('int',1,200),'max_features':('float',.1,1.)},
    'hist':{'max_iter':('int',20,500),'max_leaf_nodes':('int',3,63),'min_samples_leaf':('int',5,300),'learning_rate':('float',.005,.3),'l2_regularization':('float',0.,100.)},
    'linear':{'alpha':('float',1e-6,1.)},
    'logit':{'C':('float',1e-4,100.)},
}
PROXY_WEIGHTS=[0.,.25,.5,1.]
BREAKER_MIN_PAIRS=30
BREAKER_HOURS=6

def _number(value):
    if isinstance(value,bool):raise ValueError('布尔值不是数值')
    x=float(value)
    if not math.isfinite(x):raise ValueError('非有限数值')
    return x

def sanitize(task,proposal,classification,has_proxy):
    """Return (clean proposal or None, notes). Unknown keys are dropped, numbers clamped, families whitelisted."""
    notes=[]
    if not isinstance(proposal,dict):return None,['提案不是对象']
    family=proposal.get('family')
    if family not in FAMILIES:return None,[f'算法 {family!r} 不在白名单']
    raw=proposal.get('params') if isinstance(proposal.get('params'),dict) else {}
    params={}
    for key,(kind,lo,hi) in BOUNDS[family].items():
        if key not in raw:continue
        value=raw[key]
        try:
            if kind=='float_or_none' and value is None:params[key]=None;continue
            x=_number(value)
            clamped=min(max(x,lo),hi)
            if clamped!=x:notes.append(f'{key}={value} 越界，已夹到 {clamped}')
            params[key]=int(round(clamped)) if kind=='int' else clamped
        except (TypeError,ValueError) as exc:notes.append(f'{key} 无效（{exc}），已忽略')
    for key in raw:
        if key not in BOUNDS[family]:notes.append(f'未知参数 {key} 已丢弃')
    data_params={}
    if classification:
        cal=proposal.get('calibration','none')
        if cal in CALIBRATIONS:data_params['calibration']=cal
        else:notes.append(f'校准方式 {cal!r} 无效，按 none');data_params['calibration']='none'
    if has_proxy and 'proxy_weight' in proposal:
        try:
            w=_number(proposal['proxy_weight']);data_params['proxy_weight']=min(PROXY_WEIGHTS,key=lambda v:abs(v-w))
        except (TypeError,ValueError):notes.append('proxy_weight 无效，已忽略')
    rationale=str(proposal.get('rationale',''))[:400]
    return {'family':family,'params':params,'data_params':data_params,'rationale':rationale},notes

def complete(family,params,rng,propose):
    """Fill parameters the AI left out with the program's own choices, so every candidate is fully specified."""
    base=propose(family,rng,None)
    return {**base,**params}

def generation_ranks(trials):
    """trials: dict bodies with origin. Per (scope, generation): relative rank (0 best .. 1 worst) of each origin's best."""
    groups={}
    for t in trials:
        if t.get('metrics') and t.get('origin'):groups.setdefault((t['scope'],t['generation'],t['task']),[]).append(t)
    out=[]
    for (scope,generation,task),g in groups.items():
        if len(g)<3:continue
        ordered=sorted(g,key=lambda t:t['metrics']['score']);rank={t['id']:i/(len(g)-1) for i,t in enumerate(ordered)}
        best={}
        for t in g:best[t['origin']]=min(best.get(t['origin'],1.),rank[t['id']])
        out.append({'task':task,'scope':scope,'generation':generation,'ranks':best,'winner':ordered[0]['origin']})
    return out

def report(trials,now_ms=None,window_hours=24):
    """Paired AI-vs-control statistics shown to the user and fed back to the AI."""
    now_ms=now_ms or int(time.time()*1000)
    recent=[t for t in trials if now_ms-t.get('created_ms',0)<window_hours*3600000]
    gens=generation_ranks(recent);pairs=[g for g in gens if 'ai' in g['ranks'] and 'control' in g['ranks']]
    def stats(rows):
        if not rows:return {'pairs':0}
        diffs=[g['ranks']['control']-g['ranks']['ai'] for g in rows]  # >0: AI ranked better than its control
        wins=sum(d>0 for d in diffs);ties=sum(d==0 for d in diffs);rng=random.Random(0)
        boot=sorted(sum(rng.choice(diffs) for _ in diffs)/len(diffs) for _ in range(2000))
        return {'pairs':len(rows),'ai_wins':wins,'ties':ties,'ai_mean_rank':round(sum(g['ranks']['ai'] for g in rows)/len(rows),3),
                'control_mean_rank':round(sum(g['ranks']['control'] for g in rows)/len(rows),3),
                'mean_advantage':round(sum(diffs)/len(diffs),3),'advantage_ci':[round(boot[49],3),round(boot[1949],3)],
                'generation_wins_ai':sum(g['winner']=='ai' for g in rows),'generation_wins_control':sum(g['winner']=='control' for g in rows)}
    tasks={}
    for g in pairs:tasks.setdefault(g['task'],[]).append(g)
    return {'window_hours':window_hours,'overall':stats(pairs),'tasks':{k:stats(v) for k,v in tasks.items()}}

TASK_MIN_PAIRS=16

def _trip(row,minimum,scope,now_ms):
    if row.get('pairs',0)>=minimum and row['advantage_ci'][1]<0:
        until=now_ms+BREAKER_HOURS*3600000
        # Probation: after the cooldown only evidence produced after it counts, so stale pairs cannot re-trip forever.
        return {'until_ms':until,'tripped_ms':now_ms,'since_ms':until,
                'reason':f"{scope}：AI 候选在 {row['pairs']} 组配对中显著劣于对照（平均优势 {row['mean_advantage']}，区间上限 {row['advantage_ci'][1]}）"}

def breaker(trials,state,now_ms=None,window_hours=24):
    """Per-task circuit breaker with probation; the global breaker only trips when at least two tasks are significantly
    worse at the same time (one bad task no longer locks the AI out of every task). State:
    {'global': {...}, 'tasks': {task: {until_ms, tripped_ms, since_ms, reason}}} - entries persist after expiry so their
    since_ms keeps old evidence out."""
    now_ms=now_ms or int(time.time()*1000)
    old_tasks=dict(state.get('tasks') or {});tasks={};bad=[]
    for task in {t.get('task') for t in trials if t.get('task')}|set(old_tasks):
        entry=old_tasks.get(task) or {}
        if entry.get('until_ms',0)>now_ms:tasks[task]=entry;bad.append(task);continue
        since=entry.get('since_ms',0)
        row=report([t for t in trials if t.get('task')==task and t.get('created_ms',0)>=since],now_ms,window_hours)['overall']
        tripped=_trip(row,TASK_MIN_PAIRS,task,now_ms)
        if tripped:tasks[task]=tripped;bad.append(task)
        elif entry:tasks[task]={k:entry[k] for k in ('since_ms','tripped_ms','reason') if k in entry}  # keep probation marker
    glob_=dict(state.get('global') or {})
    if glob_.get('until_ms',0)<=now_ms:
        fresh=[t for t in bad if (tasks[t].get('tripped_ms')==now_ms)]
        if len(set(bad))>=2 and fresh:
            until=now_ms+BREAKER_HOURS*3600000
            glob_={'until_ms':until,'tripped_ms':now_ms,'since_ms':until,'reason':'至少两个任务同时显著劣于对照：'+'、'.join(sorted(set(bad)))}
        elif glob_:glob_={k:glob_[k] for k in ('since_ms','tripped_ms','reason') if k in glob_}
    out={}
    if glob_:out['global']=glob_
    if tasks:out['tasks']=tasks
    return out

def ai_allowed(state,task,now_ms=None):
    now_ms=now_ms or int(time.time()*1000)
    return (state.get('global') or {}).get('until_ms',0)<=now_ms and ((state.get('tasks') or {}).get(task) or {}).get('until_ms',0)<=now_ms

def slots_for(task,trials,state,now_ms=None,window_hours=24):
    """Slots are earned: 0 while the task is in cooldown, 2 when AI has been significantly better than its control on
    this task (since its last probation start), otherwise 1."""
    now_ms=now_ms or int(time.time()*1000)
    if not ai_allowed(state,task,now_ms):return 0
    since=((state.get('tasks') or {}).get(task) or {}).get('since_ms',0)
    row=report([t for t in trials if t.get('task')==task and t.get('created_ms',0)>=since],now_ms,window_hours)['overall']
    return AI_SLOTS if row.get('pairs',0)>=TASK_MIN_PAIRS and row['advantage_ci'][0]>0 else 1

def _signature(task,family,params,calibration):
    rounded=tuple(sorted((k,(round(math.log10(v),1) if isinstance(v,(int,float)) and v>0 else v)) for k,v in (params or {}).items()))
    return (task,family,calibration or 'none',rounded)

def history(trials):
    """The AI's full track record: per (task, family, calibration) how its candidates ranked within their generation."""
    groups={}
    for t in trials:
        if t.get('metrics') and t.get('origin'):groups.setdefault((t['scope'],t['generation']),[]).append(t)
    rows={};seen={}
    for g in groups.values():
        if len(g)<3:continue
        ordered=sorted(g,key=lambda t:t['metrics']['score']);rank={t['id']:i/(len(g)-1) for i,t in enumerate(ordered)}
        for t in g:
            if t.get('origin')!='ai':continue
            cal=(t.get('data_params') or {}).get('calibration')
            key=(t['task'],t['family'],cal or 'none');r=rows.setdefault(key,{'task':t['task'],'family':t['family'],'calibration':cal or 'none','n':0,'rank_sum':0.,'best_score':None})
            r['n']+=1;r['rank_sum']+=rank[t['id']];r['best_score']=t['metrics']['score'] if r['best_score'] is None else min(r['best_score'],t['metrics']['score'])
            sig=_signature(t['task'],t['family'],t['params'],cal);s_=seen.setdefault(sig,[0,0.]);s_[0]+=1;s_[1]+=rank[t['id']]
    table=[{**{k:v for k,v in r.items() if k!='rank_sum'},'mean_rank':round(r['rank_sum']/r['n'],3)} for r in rows.values()]
    poor={sig for sig,(n,total) in seen.items() if n>=2 and total/n>.6}
    return sorted(table,key=lambda r:(r['task'],r['mean_rank'])),poor

def parse(content,tasks,classification,has_proxy,avoid=frozenset()):
    """Model reply -> sanitized proposals per task plus notes; raises ValueError on unusable replies. Exact repeats of
    configurations that already ranked poorly (avoid) are rejected."""
    data=json.loads(content)
    if not isinstance(data,dict):raise ValueError('回复不是 JSON 对象')
    raw=data.get('proposals') if isinstance(data.get('proposals'),dict) else {}
    proposals={};notes={}
    for task in tasks:
        items=raw.get(task)
        items=items if isinstance(items,list) else [items] if isinstance(items,dict) else []
        clean=[]
        for item in items[:AI_SLOTS]:
            p,n=sanitize(task,item,classification(task),has_proxy(task))
            if p and _signature(task,p['family'],p['params'],p['data_params'].get('calibration')) in avoid:
                n=n+[f"{p['family']} 的这组参数此前多次排名靠后，重复提案已拒绝"];p=None
            if n:notes.setdefault(task,[]).extend(n)
            if p:clean.append(p)
        if clean:proposals[task]=clean
    if len(raw)>len(tasks):notes.setdefault('_',[]).append('忽略了未知任务的提案')
    return {'proposals':proposals,'notes':notes,'concern':str(data.get('concern',''))[:1000],'suggestion':str(data.get('suggestion',''))[:2000]}
