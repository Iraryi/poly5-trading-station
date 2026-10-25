"""Real-time historical blind round using the exact shipped AutoPaper controller."""
import json,secrets,time,sys,hashlib
from pathlib import Path
from datetime import datetime,timezone
import polars as pl
from research_blind_round import reconstruct,digest
from research_features import ROOT
SOURCE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SOURCE))
from poly5.auto_paper import AutoPaper,POLICY
from poly5.domain import Market,Book,Fee
from poly5.common import D,dumps
from poly5.storage import EventStore
from poly5.execution import ExecutionGateway

def run():
    out=ROOT.parent/('auto-blind-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));out.mkdir()
    identities=pl.scan_parquet(ROOT/'roi-dataset.parquet').select('market_slug','day','split').unique().collect()
    used=[]
    for p in ROOT.parent.glob('blind-round-*/preregistered.json'):used.append(json.loads(p.read_text(encoding='utf-8'))['market_slug'])
    for p in ROOT.parent.glob('auto-blind-*/preregistered.json'):used.append(json.loads(p.read_text(encoding='utf-8'))['market_slug'])
    candidates=identities.filter((pl.col('split')=='purged')&~pl.col('market_slug').is_in(used)).sort('market_slug')
    row=candidates.row(secrets.randbelow(candidates.height),named=True);slug=row['market_slug'];start=int(slug.rsplit('-',1)[1])
    manifest={'market_slug':slug,'historical_start_utc':datetime.fromtimestamp(start,timezone.utc).isoformat(),
        'selection':'random purged window, excluding the prior blind round','candidate_count':candidates.height,
        'model_sha256':digest(SOURCE/'assets/orderbook-roi-v1.json'),'controller_sha256':digest(SOURCE/'poly5/auto_paper.py'),
        'policy':POLICY,'real_time_seconds':300,'future_labels_read':False,'scope':'historical blind replay, excluded from all fitted/evaluated splits; not prospective live test',
        'limitation':'model was fitted at seconds 60/120; per-second scanning is a new PAPER experiment, not previously validated deployment'}
    (out/'preregistered.json').write_text(dumps(manifest),encoding='utf-8');print('LOCKED',slug,str(out),flush=True)
    states,count=reconstruct(next((ROOT/'raw/l2'/('date='+row['day'])).glob('*.parquet')),slug,continuous=True)
    store=EventStore(out/'runtime');gateway=ExecutionGateway(store);controller=AutoPaper(out/'runtime',gateway,store.append)
    market=Market(slug,'polymarket.crypto.btc.updown.5m',slug,'Blind historical replay','historical-'+slug,{'Up':'historical-up','Down':'historical-down'},start,start+300,True,D(5),D('.01'),Fee(D('.07'),1),'historical','research only',True)
    beginning=time.monotonic();status=[]
    try:
        for state in states:
            offset=state['offset'];delay=beginning+offset-time.monotonic()
            if delay>0:time.sleep(delay)
            book=Book('historical-up',market.condition_id)
            book.bids={D(str(p)):D(str(q)) for p,q in zip(state['bid_prices'],state['bid_sizes'])}
            book.asks={D(str(p)):D(str(q)) for p,q in zip(state['ask_prices'],state['ask_sizes'])}
            book.source_ms=state['last_event_us']//1000;book.received_ms=state['last_receive_us']//1000
            book.initialized=state['valid'];book.quality='OK' if state['valid'] else 'GAP'
            controller.step(market,book,state['at_us']//1000,True)
            if offset in (0,60,120,180,240,300):
                print(offset,controller.status,flush=True);status.append({'offset_s':offset,**controller.status})
        store.flush();decisions=list(store.all('auto_paper.decision'))
        report={'preregistered':manifest,'wall_seconds':time.monotonic()-beginning,'source_rows':count,
            'decisions':decisions,'signals':sum(d['payload']['signal'] for d in decisions),
            'paper':gateway.view(),'final_status':controller.status,'status_samples':status,'real_orders_sent':0,
            'hashes_unchanged':digest(SOURCE/'assets/orderbook-roi-v1.json')==manifest['model_sha256'] and digest(SOURCE/'poly5/auto_paper.py')==manifest['controller_sha256']}
        (out/'result.json').write_text(dumps(report,indent=2),encoding='utf-8')
        (out/'盲测报告.md').write_text(f"# 自动 PAPER 五分钟盲测\n\n窗口：{slug}（UTC {manifest['historical_start_utc']}）。\n\n使用与安装包相同的自动控制器和冻结盘口模型，顺序运行 {report['wall_seconds']:.1f} 秒。窗口来自此前未用于训练、选模和测试评分的隔离集；没有读取未来收益标签。\n\n评估 {len(decisions)} 次，产生 {report['signals']} 个入场信号；模拟执行 {len(gateway.orders)} 笔，完整平仓 {len(gateway.closed_trades)} 次，模拟已实现净收益 ${gateway.realized}。零成交时净 ROI 无有效分母。\n\n真实订单 0。历史回放不等同于前向实盘验证；一次结果不能证明盈利能力。抽样、模型与代码哈希见 preregistered.json，信号和模拟账本见 result.json。\n",encoding='utf-8')
        print('COMPLETE',out,'signals',report['signals'],'orders',len(gateway.orders),flush=True)
    finally:store.close()

if __name__=='__main__':run()
