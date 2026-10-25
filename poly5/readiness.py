"""Explain connection prerequisites without granting execution capabilities."""
from .common import epoch_ms

def assess(state,model_status,auth_status):
    now=epoch_ms();geo=state.get('geo',{});auth=auth_status or {}
    checks=[('market','市场与行情',bool(state.get('market')) and state.get('clock_valid') is True and bool(state.get('books')) and all(b.get('fresh') for b in state['books'].values())),
        ('geo','官方地区检查',geo.get('blocked') is False and now-geo.get('checked_ms',0)<90000),
        ('account','真实账户认证',auth.get('identity_verified') is True and auth.get('quality')=='OK' and now-auth.get('observed_ms',0)<30000),
        ('orders','订单对账与用户连接',auth.get('orders_reconciled') is True and state.get('user_ws_status','').startswith(('已连接','收到'))),
        ('model','固定模型影子预测',model_status.get('status')=='OK' and now-model_status.get('observed_ms',0)<5000),
        ('execution','独立实盘执行器与本机人工启用',state.get('live',{}).get('armed') is True)]
    live=state.get('live',{})
    return {'live_enabled':live.get('armed') is True,'ready_for_live':all(p for _,_,p in checks),
        'checks':[{'id':k,'name':n,'passed':bool(p)} for k,n,p in checks],
        'note':live.get('message','实盘需在本机核对本金、模型池与资格后单独启用；模型未被证明盈利。')}
