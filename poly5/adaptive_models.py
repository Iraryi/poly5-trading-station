"""Forward-only final-direction comparison within a manually frozen model pool."""
from .common import D,decimal

MIN_MARKETS=20

def initial_pool(releases):
    if not releases or len(releases)>8:raise ValueError('模型池需选择 1–8 个固定方向模型')
    tasks={r['task'] for r in releases}
    if not (tasks<={'direction','direction_mkt'} or tasks<={'late','late_mkt'}):
        raise ValueError('只混合同一阶段的最终方向模型；ROI 模型不能当作概率投票')
    ids=[r['release_id'] for r in releases]
    if len(set(ids))!=len(ids):raise ValueError('不能重复选择同一模型版本')
    return {'release_ids':ids,'weights':{rid:str(D(1)/len(ids)) for rid in ids},
            'predictions':{},'results':{},'last_weight_samples':0,'minimum_markets':MIN_MARKETS}

def register_prediction(saved,condition,outputs,at_ms,end_s):
    if condition in saved['predictions']:return None
    by_id={o['release_id']:o for o in outputs}
    if set(by_id)!=set(saved['release_ids']):raise ValueError('模型共同样本不完整')
    probabilities={rid:str(decimal(str(by_id[rid]['probability_up']))) for rid in saved['release_ids']}
    return {'probabilities':probabilities,'at_ms':at_ms,'end_s':end_s}

def blend(saved,outputs):
    by_id={o['release_id']:o for o in outputs}
    if set(by_id)!=set(saved['release_ids']):raise ValueError('等待所有已批准模型，不能静默丢弃差模型')
    values={rid:decimal(str(by_id[rid]['probability_up'])) for rid in saved['release_ids']}
    if any(not D(0)<=p<=1 for p in values.values()):raise ValueError('模型概率非法')
    return sum((decimal(saved['weights'][rid])*p for rid,p in values.items()),D(0)),values

def hourly_weights(saved):
    """One equal-weight observation per common completed market; Brier loss.

    Weight ratio is bounded to 4:1, with at most 20% interpolation per hourly
    checkpoint. No training champions, generated code, or AI calls are involved.
    """
    rows=[(saved['predictions'][cid],result) for cid,result in saved['results'].items() if cid in saved['predictions']]
    n=len(rows)
    if n<MIN_MARKETS or n<=saved['last_weight_samples']:return None
    rows=rows[-200:];scores={}
    for rid in saved['release_ids']:
        loss=sum(((decimal(pred['probabilities'][rid])-D(result['up']))**2 for pred,result in rows),D(0))/len(rows)
        scores[rid]=max(D(1),min(D(4),D(1)/(D('.25')+loss)))
    total=sum(scores.values(),D(0));weights={}
    for rid in scores:weights[rid]=decimal(saved['weights'][rid])*D('.8')+scores[rid]/total*D('.2')
    norm=sum(weights.values(),D(0));weights={rid:str(w/norm) for rid,w in weights.items()}
    return {'previous':dict(saved['weights']),'weights':weights,'common_markets':n,'rule':'brier-ratio-max4-step20-v1'}
