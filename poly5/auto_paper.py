"""Reference-distance PAPER strategy; observation horizons never force an exit."""
from pathlib import Path
import json
import uuid
from types import SimpleNamespace
from decimal import ROUND_DOWN
from .common import D, atomic_json
from .domain import DataError, preview_buy_notional
from .reference import ReferenceTape

POLICY = {'id':'baseline-distance-v1', 'min_distance_sigma':'1', 'confirm_seconds':3,
          'default_entry_mode':'last_minute_50','last_minute_distance_usd':'50',
          'contract_notional_usd':'5', 'fees_paid_separately':True,
          'min_edge_per_share':'.005', 'take_profit_net_roi':'.005',
          'shock_sigma_3s':'4', 'shock_line_seconds':'10',
          'expiry_action':'wait_for_confirmed_resolution', 'entry_close_buffer_ms':15000,
          'exit_cooldown_ms':3000, 'max_holding_seconds':None,
          'probability_method':'uncalibrated Brownian TWAP approximation; research only'}

# Conservative PAPER mode backed by the trainer's final exam: the top late models averaged into ONE probability,
# one $5 stake per market. Exam replay (exp19): same drawdown as the single champion, ~35 trades / 12.5 h.
LATE_VOTE = {'id':'late-vote-v1','min_probability':'.55','min_edge_per_share':'.03','min_entry_price':'.35',
             'exit':'hold to settlement; sudden-reversal exit only','stake_usd':'5'}
LATE_MODES = ('last_minute_50','late_vote')


def entry_candidate(f, books, market, now, mode='distance_trend', vote=None):
    late=mode in LATE_MODES
    if late and not market.start*1000+240000<=now<market.end*1000-15000:
        raise DataError('保守模式：等待最后一分钟')
    if mode=='last_minute_50' and abs(f['twap_distance'])<50:
        raise DataError('保守模式：TWAP 距基准不足 $50')
    if mode=='late_vote' and (not vote or vote.get('market_id')!=market.id or not 0<=now-vote['at_ms']<3000):
        raise DataError('等待新鲜的模型投票')
    direction = 1 if f['spot_distance'] > 0 and f['twap_distance'] > 0 else -1 if f['spot_distance'] < 0 and f['twap_distance'] < 0 else 0
    if not direction: raise DataError('现价与 TWAP 尚未位于基准同侧')
    # late_vote mirrors the live rule (no distance floor; the model edge decides), so it skips the noise gate.
    if mode!='late_vote' and min(direction*f['spot_distance_sigma'], direction*f['distance_sigma']) < D(POLICY['min_distance_sigma']):
        raise DataError('离基准线太近，等待距离超过近期噪声')
    if not late and (direction*f['trend_5s'] <= 0 or direction*f['trend_15s'] <= 0):
        raise DataError('等待 5 秒与 15 秒趋势同向')
    if not late and (f['range_15s'] > 4*f['sigma_1s']*D(15).sqrt() or f['max_step_3s'] > 3*f['sigma_1s']):
        raise DataError('近期波动突增，等待平稳后再入场')
    outcome = 'Up' if direction == 1 else 'Down'
    book = books.get(outcome)
    if not book or not book.fresh(now): raise DataError('等待 '+outcome+' 可成交深度')
    limit = min(D('.99'), min(book.asks)+market.tick)
    result = preview_buy_notional(book, market, '5', str(limit))
    fair = f['fair_up_heuristic'] if direction == 1 else 1-f['fair_up_heuristic']
    # Fair terminal probability is not an executable bid. Respect the price
    # grid and reserve half the present spread before estimating a sale.
    exit_price=min(D(1)-market.tick,((fair-(min(book.asks)-max(book.bids))/2)/market.tick).to_integral_value(rounding=ROUND_DOWN)*market.tick)
    exit_price=max(market.tick,exit_price)
    average = result['total']/result['shares']
    if mode=='late_vote':
        p_up=D(str(vote['probability_up']));fair=p_up if direction==1 else 1-p_up
        if fair < D(LATE_VOTE['min_probability']): raise DataError('模型投票对该方向信心不足')
        if average < D(LATE_VOTE['min_entry_price']): raise DataError('冷门价格低于 0.35，不买')
        edge = fair-average
        if edge < D(LATE_VOTE['min_edge_per_share']): raise DataError('含费价格未留下 0.03 的模型优势')
        return {'outcome':outcome,'asset_id':book.asset_id,'limit':str(limit),'estimated_edge_per_share':str(edge),
                'vote_probability':str(fair),'average_price':str(average),'strategy':LATE_VOTE['id']}
    edge = (fair if late else exit_price-market.fee.cost(exit_price,D(1)))-average
    if edge < D(POLICY['min_edge_per_share']):
        raise DataError('当前买价已透支距离优势，等待更合适报价')
    return {'outcome':outcome, 'asset_id':book.asset_id, 'limit':str(limit),
            'estimated_edge_per_share':str(edge), 'fair_heuristic':str(fair),'estimated_exit_bid':str(exit_price)}


def reversal(f, outcome):
    direction = D(1) if outcome == 'Up' else D(-1)
    adverse = -direction*f['move_3s']
    shock = adverse / (f['sigma_1s']*D(3).sqrt())
    cushion = min(direction*f['spot_distance_sigma'], direction*f['distance_sigma'])
    seconds_to_line = max(D(0), direction*f['spot_distance'])/(adverse/3) if adverse > 0 else None
    urgent = cushion <= 1 or (seconds_to_line is not None and seconds_to_line <= D(POLICY['shock_line_seconds']))
    return {'trigger':shock >= D(POLICY['shock_sigma_3s']) and urgent,
            'shock_sigma':shock, 'distance_sigma':cushion, 'seconds_to_line_at_current_speed':seconds_to_line}


class AutoPaper:
    def __init__(self, root, gateway, event, model=None, reference=None, entry_mode=None):
        self.path = Path(root)/'cache/auto-paper.json'
        self.gateway = gateway; self.event = event
        self.reference = reference or ReferenceTape()
        self.entry_mode=entry_mode or POLICY['default_entry_mode']
        self.model = SimpleNamespace(id=POLICY['id'])
        self.saved = {'evaluated':[], 'plan':None, 'pending':None, 'cooldown_until_ms':0}
        if self.path.exists(): self.saved.update(json.loads(self.path.read_text(encoding='utf-8')))
        pending = self.saved['pending']
        if pending and pending['side'] == 'BUY' and pending['intent_id'] in gateway.seen:
            fill = next(o for o in gateway.orders if o['id'] == pending['intent_id'])
            self.saved['plan'] = {'market_id':pending['market_id'], 'asset_id':fill['asset_id'],
                'outcome':fill['outcome'], 'entry_ms':pending['due_ms'], 'target':POLICY['take_profit_net_roi'],
                'baseline_point':pending.get('baseline_point')}
            self.saved['pending'] = None; self.persist()
        pending = self.saved['pending']
        if pending and pending['side'] == 'SELL' and pending.get('reason') in ('timeout','stop_loss'):
            self.saved['pending'] = None; self.persist()
        if pending and pending['side'] == 'BUY' and 'outcome' not in pending:
            self.saved['pending'] = None; self.persist()
        self.status = {'enabled':False, 'status':'等待基准距离策略', 'model_id':POLICY['id'],
                       'policy':POLICY, 'predictions':[], 'live_enabled':False}
        self.market_id = None; self.last_scan = None; self.last_observe = 0
        self.confirm_outcome = None; self.confirm_count = 0

    def persist(self): atomic_json(self.path,self.saved)

    def note(self, kind, payload, market):
        policy=LATE_VOTE['id'] if self.entry_mode=='late_vote' else POLICY['id']
        self.event('auto_paper.'+kind, {'model_id':policy,'policy_id':policy,'entry_mode':self.entry_mode,**payload},
                   mode='paper',account_id='paper-local',market_id=market.id,condition_id=market.condition_id,source='automatic PAPER')

    def liquidation_roi(self,book,pos,market):
        left=pos['shares'];net=D(0)
        for price,size in sorted(book.bids.items(),reverse=True):
            q=min(left,size);net+=price*q-market.fee.cost(price,q);left-=q
            if left==0:break
        return (net-pos['basis'])/pos['basis'] if left==0 else None

    def step(self, market, book, now, enabled, clock_valid=True, books=None, vote=None):
        self.status['enabled'] = bool(enabled);self.status['entry_mode']=self.entry_mode
        if vote is not None:self.status['vote']={'probability_up':vote['probability_up'],'models':len(vote['models']),'at_ms':vote['at_ms']}
        if not market or now is None or not clock_valid:
            self.status['status']='等待市场与服务端时间';return
        books = books or ({'Up':book} if book else {})
        if self.market_id != market.id:
            self.market_id=market.id;self.last_scan=None;self.confirm_count=0;self.confirm_outcome=None
            self.status.pop('reference',None);self.status.pop('liquidation_roi',None)
        plan=self.saved['plan'];pending=self.saved['pending']
        if plan and plan['market_id'] != market.id:
            if self.gateway.positions.get(plan['asset_id'],{}).get('shares',D(0))>0:
                self.status['status']='旧轮到期仓位等待官方结果结算';return
            self.saved['plan']=None;plan=None;self.persist()
        if pending and pending['market_id']!=market.id:
            self.saved['pending']=None;pending=None;self.persist()
        f=None;reference_error=None
        try:
            f=self.reference.features(market,now,plan.get('baseline_point') if plan else None)
            self.status['reference']={k:str(v) for k,v in f.items() if k!='baseline_point'}
        except DataError as exc: reference_error=str(exc)
        if plan:
            outcome=plan.get('outcome','Up');held_book=books.get(outcome)
            pos=self.gateway.positions.get(plan['asset_id'])
            if not pos or pos['shares']<=0:
                self.saved['plan']=None;self.saved['pending']=None;self.persist();return
            if now>=market.end*1000:
                self.status['status']='本轮到期，等待官方结果后模拟结算';return
            if not held_book or held_book.asset_id!=plan['asset_id'] or not held_book.sellable(now):
                self.status['status']='持仓中，等待可卖盘口';return
            roi=self.liquidation_roi(held_book,pos,market)
            self.status['liquidation_roi']=str(roi) if roi is not None else None
            if pending and pending['side']=='SELL' and now>=pending['due_ms']:
                if pending['reason']=='take_profit' and (roi is None or roi<D(POLICY['take_profit_net_roi'])):
                    self.saved['pending']=None;self.persist();self.status['status']='盈利报价已变化，继续管理持仓';return
                try:
                    fill=self.gateway.execute_paper(market,held_book,outcome,'SELL','5',str(market.tick),now,pending['intent_id'])
                    self.note('exit',{'reason':pending['reason'],'execution':fill},market)
                    self.saved['pending']=None
                    if self.gateway.positions[plan['asset_id']]['shares']<=0:
                        self.saved['plan']=None;self.saved['cooldown_until_ms']=now+POLICY['exit_cooldown_ms']
                        self.status.pop('liquidation_roi',None)
                    self.persist();self.status['status']='已自动模拟卖出'
                except DataError as exc:self.status['status']='等待退出：'+str(exc)
                return
            if not pending and now-self.last_observe>=250:
                self.last_observe=now
                risk=reversal(f,outcome) if f else None
                reason='take_profit' if plan.get('entry_mode','distance_trend') not in LATE_MODES and roi is not None and roi>=D(POLICY['take_profit_net_roi']) else 'sudden_reversal' if risk and risk['trigger'] else None
                if reason:
                    self.saved['pending']={'market_id':market.id,'side':'SELL','due_ms':now+300,'reason':reason,'intent_id':'auto-'+uuid.uuid4().hex}
                    self.persist();self.note('exit_trigger',{'reason':reason,'observed_roi':roi,'risk':risk,'reference':f},market)
            self.status['status']='按基准距离管理持仓' if f else '持仓中 · '+str(reference_error)
            return
        if pending and pending['side']=='BUY' and now>=pending['due_ms']:
            self.saved['pending']=None
            if not enabled or self.gateway.paused or not f:
                self.persist();self.status['status']='已取消待执行模拟买入：'+str(reference_error or '暂停');return
            try:
                candidate=entry_candidate(f,books,market,now,self.entry_mode,vote)
                if candidate['outcome']!=pending['outcome']:raise DataError('买入前趋势方向已改变')
                chosen=books[pending['outcome']]
                fill=self.gateway.execute_paper(market,chosen,pending['outcome'],'BUY','5',pending['limit'],now,pending['intent_id'],notional_target=True)
                self.saved['plan']={'market_id':market.id,'asset_id':chosen.asset_id,'outcome':pending['outcome'],
                    'entry_ms':now,'target':POLICY['take_profit_net_roi'],'baseline_point':f['baseline_point'],'entry_mode':self.entry_mode}
                self.note('entry',{'execution':fill,'plan':self.saved['plan'],'reference':f,'candidate':candidate},market)
                self.status['status']='已自动模拟买入'
            except DataError as exc:self.note('entry_rejected',{'reason':str(exc)},market);self.status['status']='模拟买入未成交：'+str(exc)
            self.persist();return
        if not enabled:self.status['status']='自动模拟已暂停';return
        if self.gateway.paused:self.status['status']='新增模拟风险已暂停';return
        if now<self.saved['cooldown_until_ms']:self.status['status']='平仓后短暂冷却';return
        if market.end*1000-now<=POLICY['entry_close_buffer_ms']:
            self.status['status']='临近封盘，停止新增仓位';return
        scan=now//1000
        if scan==self.last_scan:return
        if self.last_scan is not None and scan!=self.last_scan+1:self.confirm_count=0;self.confirm_outcome=None
        self.last_scan=scan
        try:
            if reference_error:raise DataError(reference_error)
            if not market.twap_60_verified or market.fee is None:raise DataError('结算或手续费规则待核验')
            candidate=entry_candidate(f,books,market,now,self.entry_mode,vote)
            if candidate['outcome']==self.confirm_outcome:self.confirm_count+=1
            else:self.confirm_outcome=candidate['outcome'];self.confirm_count=1
            qualified=self.confirm_count>=POLICY['confirm_seconds']
            self.status['candidate']=candidate
            self.note('decision',{'at_ms':now,'reference':f,'candidate':candidate,'signal':qualified,
                'reason':'qualified' if qualified else 'confirming_trend'},market)
            if qualified:
                self.saved['pending']={'market_id':market.id,'side':'BUY','due_ms':now+300,**candidate,
                    'baseline_point':f['baseline_point'],'intent_id':'auto-'+uuid.uuid4().hex}
                self.persist();self.status['status']='趋势与距离确认，等待模拟买入';self.confirm_count=0
            else:self.status['status']='方向与距离确认中'
        except (DataError,ValueError) as exc:
            self.confirm_count=0;self.confirm_outcome=None
            self.status.pop('candidate',None);self.status['status']=str(exc)
            if f:self.note('decision',{'at_ms':now,'reference':f,'signal':False,'reason':str(exc)},market)
