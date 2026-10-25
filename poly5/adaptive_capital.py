"""Persistent hourly high-water capital policy. Money never uses binary floats."""
from decimal import ROUND_DOWN
from .common import D,decimal

HOUR_MS=3600000
POLICY_VERSION='hourly-highwater-drawdown-v1'

def policy(allocation='0.10',minimum='5'):
    allocation,minimum=decimal(allocation),decimal(minimum)
    if not D('.001')<=allocation<=D(1):raise ValueError('单笔基础比例范围为 0.1%–100%')
    if not D(0)<minimum<=D(100000):raise ValueError('最小合约金额须大于零')
    return {'version':POLICY_VERSION,'allocation':str(allocation),'minimum_notional':str(minimum),
            'stop_drawdown':'0.50','checkpoint_interval_ms':HOUR_MS,'sizing_exponent':2}

def initialize(cash,now_ms,config=None):
    cash=decimal(cash)
    if cash is None or cash<=0:raise ValueError('可核验可用现金不足')
    return {'schema':1,'initial_capital':str(cash),'baseline':str(cash),'started_ms':now_ms,
            'next_checkpoint_ms':now_ms+HOUR_MS,'last_checkpoint_ms':None,'stop_latched':False,
            'policy':config or policy(),'checkpoints':0}

def evaluate(saved,strategy_pnl,now_ms,*,verified=True):
    """Only bot fill PnL + current liquidation marks; account deposits are not inputs.

    Caller durably commits returned state before any exchange write. Missing marks
    never become zero or a profitable hourly checkpoint. A triggered stop latches.
    """
    state={**saved};config=state['policy'];base=decimal(state['baseline'])
    result={'verified':False,'baseline':str(base),'equity':None,'drawdown':None,'new_risk_allowed':False,
            'checkpoint':None,'reason':'等待完整的成交与可变现估值','policy_version':POLICY_VERSION}
    if not verified or strategy_pnl is None:return state,result
    equity=decimal(state['initial_capital'])+decimal(strategy_pnl)
    if now_ms>=state['next_checkpoint_ms']:
        old=base;base=max(base,equity)
        result['checkpoint']={'at_ms':now_ms,'previous_baseline':str(old),'baseline':str(base),'equity':str(equity)}
        state.update(baseline=str(base),last_checkpoint_ms=now_ms,checkpoints=state['checkpoints']+1,
                     next_checkpoint_ms=state['next_checkpoint_ms']+((now_ms-state['next_checkpoint_ms'])//HOUR_MS+1)*HOUR_MS)
    drawdown=max(D(0),(base-equity)/base);stop=decimal(config['stop_drawdown'])
    if drawdown>=stop:state['stop_latched']=True
    multiplier=max(D(0),1-drawdown/stop)**config['sizing_exponent']
    allocation=decimal(config['allocation'])*multiplier
    # Never increase intrahour sizing merely because equity rose. All-in cap
    # includes fees; actual order notional is derived after reserving fees.
    budget=(min(base,max(D(0),equity))*allocation).quantize(D('.000001'),rounding=ROUND_DOWN)
    allowed=not state['stop_latched'] and budget>=decimal(config['minimum_notional'])
    result.update(verified=True,baseline=str(base),equity=str(equity),drawdown=str(drawdown),
                  allocation=str(allocation),size_multiplier=str(multiplier),all_in_budget=str(budget),
                  new_risk_allowed=allowed,stop_latched=state['stop_latched'],
                  minimum_notional=config['minimum_notional'],next_checkpoint_ms=state['next_checkpoint_ms'],
                  # Predeclared parameter schedules, not generated code or hot-edited policy.
                  min_probability=str(D('.55')+D('.15')*min(D(1),drawdown/stop)),
                  min_edge_per_share=str(D('.03')+D('.02')*min(D(1),drawdown/stop)),  # 0.03 floor: never buy ~0.97 for pennies (exp20)
                  reason='50% 回撤熔断，需人工处理' if state['stop_latched'] else '缩减后的预算低于最小合约金额，等待' if not allowed else '可按回撤后比例评估入场')
    return state,result

