"""Dynamic optimal stopping research. No exchange writes and no holding deadline."""
from decimal import Decimal as D, ROUND_CEILING, ROUND_DOWN
import math
from .feature_schema import REF, BOOK
from poly5.domain import Fee, DataError

VERSION='dynamic-tp-v1'
DATA_REVISION='boundary-v2'
PROFIT_FLOOR=.005
EXIT_MARGIN=.002
LATENCY_MS=300
MAX_GAP_MS=3500
POSITION=['side_sign','holding_s','entry_price','entry_cost','position_shares',
          'sell_roi','can_sell','peak_roi','drawdown_roi','legacy_up_roi5','legacy_up_roi15']
FEATURES=REF+BOOK+POSITION

def fresh(frame,now,selling=False):
    return (frame.get('initialized') and frame.get('quality') in (('OK','EMPTY') if selling else ('OK',))
            and frame['available_ms']<=now and -500<=now-frame['source_ms']<=1500
            and 0<=now-frame['received_ms']<=1500)

def buy(frame,meta,now,limit=None):
    if not fresh(frame,now):raise DataError('入场盘口过期')
    fee=Fee(D(meta['fee_rate']),int(meta['fee_exponent']))
    tick=D(str(meta['tick']));limit=D(str(limit)) if limit is not None else min(D('.99'),D(str(frame['ask_prices'][0]))+tick)
    cost=fees=shares=D(0)
    for px,size in zip(frame['ask_prices'],frame['ask_sizes']):
        p,q=D(str(px)),D(str(size)).quantize(D('.000001'),rounding=ROUND_DOWN)
        if p>limit:break
        q=min(q,((D(5)-cost)/p).quantize(D('.000001'),rounding=ROUND_CEILING))
        if q<=0:break
        cost+=p*q;shares+=q;fees+=fee.cost(p,q)
        if cost>=5:break
    if cost<5 or shares<D(str(meta.get('min_size',5))):raise DataError('入场深度/最小份额不足')
    return {'shares':str(shares),'total':str(cost+fees),'cost':str(cost),'fee':str(fees),'entry_ms':now,'limit':str(limit)}

def sell_roi(frame,meta,position,now):
    if not fresh(frame,now,True):return None
    fee=Fee(D(meta['fee_rate']),int(meta['fee_exponent']));left=D(position['shares']);value=D(0)
    for px,size in zip(frame['bid_prices'],frame['bid_sizes']):
        p=D(str(px));q=min(left,D(str(size)));value+=p*q-fee.cost(p,q);left-=q
        if not left:break
    return float((value-D(position['total']))/D(position['total'])) if not left else None

def state_features(ref,frame,meta,position,now,side,history,peak=0.,priors=None):
    bids=frame['bid_prices'];asks=frame['ask_prices']
    bid=bids[0] if bids else 0.;ask=asks[0] if asks else 1.;mid=(bid+ask)/2
    bd=sum(frame['bid_sizes'][:5]);ad=sum(frame['ask_sizes'][:5]);sizes=(frame['bid_sizes'] or [0])[0]+(frame['ask_sizes'] or [0])[0]
    past=lambda delay:next((v for at,v in reversed(history) if at<=now-delay and now-delay-at<=2000),mid)
    roi=sell_roi(frame,meta,position,now)
    f={**ref,'mid':mid,'spread':ask-bid,'imbalance5':(bd-ad)/(bd+ad) if bd+ad else 0.,
       'microprice_offset':(ask*(frame['bid_sizes'] or [0])[0]+bid*(frame['ask_sizes'] or [0])[0])/sizes-mid if sizes else 0.,
       'mid_change_1s':mid-past(1000),'mid_change_5s':mid-past(5000),'book_age_s':max(0,now-frame['source_ms'])/1000,
       'side_sign':1. if side=='Up' else -1.,'holding_s':max(0,now-position['entry_ms'])/1000,
       'entry_price':float(D(position['cost'])/D(position['shares'])),'entry_cost':float(position['total']),
       'position_shares':float(position['shares']),'sell_roi':roi if roi is not None else -1.,'can_sell':float(roi is not None),
       'peak_roi':max(peak,roi if roi is not None else -1.),'drawdown_roi':max(peak,roi if roi is not None else -1.)-(roi if roi is not None else -1.),
       'legacy_up_roi5':0.,'legacy_up_roi15':0.}
    if priors and side=='Up':
        from .data import vector
        # Old forecasts describe Up, including on a Down position. Names preserve this distinction.
        for task,(bundle,_) in priors.items():
            f['legacy_up_'+task]=float(bundle['model'].predict([vector(f,bundle['features'])])[0])
    return f

def shock(x):
    """A sharp adverse move AND loss of distance from the baseline; ordinary red PnL is not a stop."""
    sigma=max(x['vol_60s_bps'],.5);side=x['side_sign'];remaining=max(300*(1-x['stage_fraction']),1.)
    return (side*x['return_5s_bps'] < -max(2.,3*sigma*math.sqrt(5))
            and side*x['spot_open_bps'] < 1.5*sigma*math.sqrt(remaining))

def entry_allowed(x):
    s=x['side_sign'];sigma=max(x['vol_60s_bps'],.5)
    return (s*x['spot_open_bps']>0 and s*x['return_15s_bps']>=-.5*sigma*math.sqrt(15)
            and x['entry_price']>=.35 and x['entry_price']<.99)

def action(x,continuation):
    if not math.isfinite(float(continuation)):raise DataError('持仓价值预测不是有限数值')
    if not x['can_sell']:return 'hold'
    if shock(x):return 'reversal'
    if x['sell_roi']>=PROFIT_FLOOR and x['sell_roi']>=continuation+EXIT_MARGIN:return 'take_profit'
    return 'hold'

def execute_path(row,predictions,mode='model'):
    """Sequential causal decisions. Only the latency fill uses a later frame; missed targets keep holding."""
    path=row['aux']['path'];pending=None
    for i,point in enumerate(path):
        x=point['x'];now=point['at_ms']
        # A rounded legacy capture may appear exactly at expiry; it is not an executable market.
        if now>=(row['start_s']+300)*1000:break
        if pending and now>=pending['due_ms']:
            if now-pending['due_ms']<=MAX_GAP_MS and x['can_sell']:
                if pending['reason']=='reversal' or x['sell_roi']>=PROFIT_FLOOR:
                    return {'roi':x['sell_roi'],'reason':pending['reason'],'holding_s':x['holding_s'],'at_ms':now}
            pending=None
        if pending or mode=='hold':continue
        a=action(x,predictions[i]) if mode=='model' else ('reversal' if shock(x) and x['can_sell'] else 'take_profit' if x['can_sell'] and x['sell_roi']>=PROFIT_FLOOR else 'hold')
        if a!='hold':pending={'reason':a,'due_ms':now+LATENCY_MS}
    return {'roi':row['y'],'reason':'official_settlement','holding_s':(row['start_s']*1000+300000-row['aux']['entry_ms'])/1000,'at_ms':row['ready_ms']}

def canonical_episodes(rows):
    """Correct the training view without editing original rows, snapshots or models."""
    out=[]
    for row in rows:
        end=(row['start_s']+300)*1000;aux=row.get('aux') or {}
        if not row['start_s']*1000<=row['decision_ms']<aux.get('entry_ms',end)<end:continue
        path=[p for p in aux.get('path',[]) if aux['entry_ms']<=p['at_ms']<end]
        if len(path)<2 or end-path[-1]['at_ms']>MAX_GAP_MS:continue
        if any(b['at_ms']<=a['at_ms'] or b['at_ms']-a['at_ms']>MAX_GAP_MS for a,b in zip(path,path[1:])):continue
        out.append({**row,'aux':{**aux,'path':path,'data_revision':DATA_REVISION}})
    return out
