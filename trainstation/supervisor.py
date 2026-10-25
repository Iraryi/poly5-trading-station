"""Optional low-frequency AI supervision. Suggestions are data, never executed code."""
import json
import math
from pathlib import Path
import time
from urllib.parse import urlparse
from .data import read
from .modeling import FAMILIES,CALIBRATIONS
from .ai_policy import BOUNDS,AI_SLOTS,parse
from poly5.common import atomic_json
from poly5.reference import reference_secret_store

# Reasoning models (e.g. deepseek-flash) spend output tokens on reasoning before the answer;
# a small cap leaves the visible content empty.
MAX_TOKENS=16000  # proposals for six tasks plus reasoning tokens; 4000 truncated the JSON

def clock(ms):return time.strftime('%H:%M',time.localtime(ms/1000))

def record(root,entry):
    # Every attempt, success or failure, is kept so a failed review is never silently lost.
    folder=Path(root)/'ai';folder.mkdir(parents=True,exist_ok=True)
    with (folder/'calls.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
    atomic_json(folder/'last.json',entry)

INSTRUCTION='''You supervise an automated research trainer for Polymarket BTC 5-minute up/down markets (research and paper
simulation only; never recommend trades, never ask for secrets, never output code). The program judge, not you, decides
which models are promoted. For every task you may propose up to {slots} fully specified candidate configurations; they
fill {slots} of the 8 candidates in each generation. One more candidate per generation is a matched CONTROL: same family
as your first proposal but with parameters chosen by the program, so your value is measured directly (see
summary.ai_vs_control: mean_advantage > 0 means your candidates ranked better than their controls). If you are
significantly worse than the control, your slots are automatically handed back to the program for 6 hours.
Families and hard parameter bounds (values outside are clamped): {bounds}. calibration is one of {cals} (classification
only). proxy_weight in [0, 1] applies only to tasks that report has_proxy. Market-aware tasks (*_mkt) only qualify if
they beat the market price (skill_vs_market > 0). Paper P&L figures come with 95% intervals; do not chase noise.
Your full track record is in summary.ai_track_record (mean within-generation rank per task/family/calibration; 0 is
best) and the latest round in summary.previous_proposals. Learn from it: favour regions that ranked well, move away from
regions that ranked poorly, and do not resubmit configurations that already ranked poorly (they are rejected). Your slots
per task are earned (summary.ai_slots_per_task: 0 = cooldown after being significantly worse than the control, 1 = default,
2 = earned by being significantly better); propose for every task anyway.
Return JSON only: {{"proposals": {{"<task>": [{{"family": "...", "params": {{...}}, "calibration": "...", "proxy_weight": 0.5,
"rationale": "at most 25 words"}}]}}, "concern": "at most 60 words", "suggestion": "at most 80 words"}}.
Keep the reply compact: at most {slots} proposals per task, no extra keys.'''

def review(root,config,summary,tasks_meta=None):
    """tasks_meta: {task: {'classification': bool, 'has_proxy': bool}} for validating proposals."""
    tasks_meta=tasks_meta or {}
    ai=config.get('ai',{})
    if not ai.get('enabled'):return {'status':'AI 未启用，程序判官运行中'}
    vault=reference_secret_store(root)
    if not vault._path('ai-supervisor').exists():return {'status':'AI 待填写本机 API 配置'}
    path=Path(root)/'ai'/'quota.json';quota=read(path,{'last_attempt_ms':0,'attempts':[]})
    now=int(time.time()*1000);recent=[v for v in quota['attempts'] if now-v<86400000]
    interval_minutes=max(1,min(1440,int(ai.get('interval_minutes',60))))
    interval_ms=interval_minutes*60000
    daily_limit=math.ceil(1440/interval_minutes)
    if now-quota['last_attempt_ms']<interval_ms or len(recent)>=daily_limit:
        last=read(Path(root)/'ai'/'last.json',{})
        due=max(quota['last_attempt_ms']+interval_ms,(min(recent)+86400000) if len(recent)>=daily_limit else 0)
        outcome=('上次 '+clock(last['at_ms'])+(f" 成功：{last.get('proposal_count',0)} 个提案" if last.get('ok') else ' 失败：'+last.get('error',''))) if last.get('at_ms') else '尚无调用记录'
        return {'status':f'AI 监督 · {interval_minutes} 分钟间隔 · {outcome} · 下次约 {clock(due)}'}
    endpoint=ai.get('base_url','').rstrip('/');url=urlparse(endpoint)
    if url.scheme!='https' and not (url.scheme=='http' and url.hostname in ('127.0.0.1','localhost')):
        return {'status':'AI 地址须为 HTTPS 或本机服务'}
    atomic_json(path,{'last_attempt_ms':now,'attempts':recent+[now]})
    try:
        import httpx
        key=vault.get('ai-supervisor')
        prompt={'instruction':INSTRUCTION.format(slots=AI_SLOTS,bounds=json.dumps(BOUNDS),cals=CALIBRATIONS),'summary':summary}
        body={'model':ai['model'],'messages':[{'role':'user','content':json.dumps(prompt,ensure_ascii=False)}],
            'temperature':.2,'max_tokens':MAX_TOKENS,'response_format':{'type':'json_object'}}
        with httpx.Client(timeout=120) as client:
            r=client.post(endpoint+'/chat/completions',headers={'Authorization':'Bearer '+key},json=body)
            if r.status_code==400:
                # Some compatible servers reject response_format; retry once without it.
                body.pop('response_format');r=client.post(endpoint+'/chat/completions',headers={'Authorization':'Bearer '+key},json=body)
        if r.status_code!=200:raise ValueError('HTTP '+str(r.status_code)+' '+r.text[:200])
        choice=r.json()['choices'][0];content=(choice['message'].get('content') or '')[:30000].strip()
        if not content:raise ValueError('模型未返回正文（finish_reason='+str(choice.get('finish_reason'))+'）')
        if choice.get('finish_reason')=='length':raise ValueError('模型输出被截断（finish_reason=length），本次不采用')
        if content.startswith('```'):content=content.split('\n',1)[1].rsplit('```',1)[0]
        avoid=tasks_meta.get('_avoid') or frozenset();names=[t for t in tasks_meta if not t.startswith('_')]
        parsed=parse(content,names,lambda t:tasks_meta[t]['classification'],lambda t:tasks_meta[t]['has_proxy'],avoid)
        count=sum(len(v) for v in parsed['proposals'].values())
        if not count:raise ValueError('没有通过白名单的提案'+('：'+'；'.join(n for v in parsed['notes'].values() for n in v)[:200] if parsed['notes'] else ''))
        result={'status':f'AI 监督已完成 · {clock(now)} · {count} 个提案 / {len(parsed["proposals"])} 个任务','at_ms':now,**parsed,'raw':content[:30000]}
        atomic_json(Path(root)/'ai'/('review-'+str(now)+'.json'),result)
        atomic_json(Path(root)/'ai'/'latest.json',result)
        record(root,{'at_ms':now,'ok':True,'proposal_count':count,'notes':parsed['notes'],'summary':summary})
        return result
    except Exception as exc:
        error=(str(exc) if isinstance(exc,ValueError) else type(exc).__name__+': '+str(exc))[:300]
        record(root,{'at_ms':now,'ok':False,'error':error,'summary':summary})
        return {'status':'AI 监督暂不可用：'+error}
