"""Offline depth/fee arithmetic. Never imports a client or sends an order."""
from decimal import Decimal as D, ROUND_DOWN, ROUND_CEILING

FEE_RATE=D('.07')
BUDGET=D('5')
MIN_SHARES=D('5')

def fee(price,shares):return shares*FEE_RATE*price*(1-price)

def charged_fee(price,shares):
    # Official fee precision is five decimals. Round upward as a conservative
    # assumption because archived per-fill receipt rounding is not available.
    return fee(price,shares).quantize(D('.00001'),rounding=ROUND_CEILING)

def buy(state,limit,budget=BUDGET):
    if not state or not state['valid']:return None
    remaining=budget;cost=D(0);fees=D(0);shares=D(0)
    for raw_p,raw_s in zip(state['ask_prices'],state['ask_sizes']):
        p=D(str(raw_p));s=D(str(raw_s))
        if p>limit:break
        q=min(s,remaining/(p+fee(p,D(1)))).quantize(D('.000001'),rounding=ROUND_DOWN)
        if q<=0:continue
        while q>0 and p*q+charged_fee(p,q)>remaining:q-=D('.000001')
        if q<=0:continue
        value=p*q;f=charged_fee(p,q);cost+=value;fees+=f;shares+=q;remaining-=value+f
    if shares<MIN_SHARES:return None
    assert cost+fees<=budget
    return {'shares':shares,'cost':cost,'fees':fees,'total':cost+fees,'limit':limit}

def liquidate(state,position):
    if not state or not state['valid']:return None
    remaining=position['shares'];gross=D(0);fees=D(0)
    for raw_p,raw_s in zip(state['bid_prices'],state['bid_sizes']):
        p=D(str(raw_p));q=min(remaining,D(str(raw_s)))
        gross+=p*q;fees+=charged_fee(p,q);remaining-=q
        if remaining<=0:break
    if remaining>0:return None
    net=gross-fees-position['total']
    return {'pnl':net,'roi':net/position['total'],'gross':gross,'exit_fees':fees}

def policy_exit(states,position,target,stop,horizon,delay,decision_us):
    """Trigger on received book then execute after latency; never at future maximum."""
    for elapsed in range(1,horizon+1):
        observed=states.get(decision_us+round((delay+elapsed)*1e6))
        value=liquidate(observed,position)
        reason=None
        if value is not None and value['roi']>=target:reason='take_profit'
        elif value is not None and value['roi']<=stop:reason='stop_loss'
        elif elapsed==horizon:reason='timeout'
        if reason:
            actual=states.get(decision_us+round((2*delay+elapsed)*1e6))
            result=liquidate(actual,position)
            # Failed exit remains a risk, conservatively charged full entry cost.
            if result is None:return {'pnl':-position['total'],'roi':D(-1),'reason':'exit_unavailable_loss_bound','elapsed':elapsed}
            return dict(result,reason=reason,elapsed=elapsed)
    raise AssertionError('No exit evaluated')
