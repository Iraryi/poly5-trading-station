"""PAPER performance of one automatic strategy from the fact ledger, with a pre-registered go-live gate.

Only closed trades count (simulated sell or confirmed-resolution settlement); quotes and open marks never become
P&L. The gate is fixed before any data: it can say the strategy qualifies for the user's own manual live decision,
never enable anything itself."""
import random
from .common import D, decimal

GATE = {'min_trades':50,'ci_low_above':'0','max_drawdown_usd':'10',
        'rule':'预先登记：≥50 笔已结束模拟交易，95% 区间下限 > 0，且最大回撤 ≤ $10，才提示可由本人决定是否上实盘'}

def trades_for(store, entry_mode, since_ms=0):
    entries={}
    for e in store.all('auto_paper.entry', mode='paper'):
        p=e['payload']
        if p.get('entry_mode')!=entry_mode:continue
        ex=p.get('execution') or {}
        at=(p.get('plan') or {}).get('entry_ms') or 0
        if at<since_ms or not ex.get('asset_id'):continue
        entries[(ex['asset_id'],ex['market_id'])]={'at_ms':at,'slug':ex.get('slug'),'outcome':ex.get('outcome'),
                                                   'basis':decimal(str(ex['total'])),'proceeds':D(0),'left':decimal(str(ex['shares']))}
    for kind in ('paper.execution','paper.settlement'):
        for e in store.all(kind, mode='paper'):
            p=e['payload'];key=(p.get('asset_id'),p.get('market_id'))
            if key not in entries or p.get('side') not in ('SELL','SETTLE'):continue
            t=entries[key];t['proceeds']+=decimal(str(p['total']));t['left']-=decimal(str(p['shares']))
    closed=[{**t,'pnl':t['proceeds']-t['basis']} for t in entries.values() if t['left']<=0]
    return sorted(closed,key=lambda t:t['at_ms']),sum(1 for t in entries.values() if t['left']>0)

def report(store, entry_mode, since_ms, now_ms):
    closed,open_count=trades_for(store,entry_mode,since_ms)
    pnls=[t['pnl'] for t in closed];equity=low=peak=drawdown=D(0)
    for x in pnls:
        equity+=x;low=min(low,equity);peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
    ci=None
    if len(pnls)>=2:
        rng=random.Random(0);sums=sorted(sum(rng.choice(pnls) for _ in pnls) for _ in range(2000));ci=[str(sums[49]),str(sums[1949])]
    hours=max(D(0),D(now_ms-since_ms)/D(3600000)) if since_ms else D(0)
    if drawdown>decimal(GATE['max_drawdown_usd']):gate='FAILED_DRAWDOWN'
    elif len(pnls)>=GATE['min_trades'] and ci and decimal(ci[1])<0:gate='FAILED_NEGATIVE'
    elif len(pnls)>=GATE['min_trades'] and ci and decimal(ci[0])>decimal(GATE['ci_low_above']):gate='PASSED'
    else:gate='COLLECTING'
    return {'entry_mode':entry_mode,'since_ms':since_ms,'hours':str(hours.quantize(D('.01'))),'trades':len(pnls),'open':open_count,
            'wins':sum(x>0 for x in pnls),'stake':str(sum((t['basis'] for t in closed),D(0))),'pnl':str(equity),
            'pnl_per_hour':str((equity/hours).quantize(D('.0001'))) if hours>=D('.25') else None,
            'lowest':str(low),'max_drawdown':str(drawdown),'pnl_ci':ci,'gate':gate,'gate_rule':GATE['rule'],
            'scope':'模拟成交与官方结果模拟兑付；不是实盘收益','recent':[{'slug':t['slug'],'outcome':t['outcome'],'pnl':str(t['pnl'])} for t in closed[-5:]]}
