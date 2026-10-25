"""Replay the exact reference-distance controller through entire five-minute rounds."""
import json
from datetime import datetime,timezone
from pathlib import Path
import sys
from decimal import Decimal as D
import numpy as np
from research_features import ROOT,load_series
from research_blind_round import reconstruct,digest
SOURCE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SOURCE))
from poly5.auto_paper import AutoPaper,POLICY
from poly5.reference import ReferenceTape
from poly5.storage import EventStore
from poly5.domain import Book,Market,Fee
from poly5.execution import ExecutionGateway
from poly5.common import dumps


def run():
    identities=json.loads((ROOT.parent/'trend-baseline-20261021T193423Z/preregistered.json').read_text(encoding='utf-8'))['selected']
    out=ROOT.parent/('distance-paper-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));out.mkdir()
    manifest={'policy':POLICY,'selected':identities,'scope':'exploratory replay of previously inspected 16 rounds, not a fresh blind test',
        'controller_sha256':digest(SOURCE/'poly5/auto_paper.py'),'reference_sha256':digest(SOURCE/'poly5/reference.py'),
        'reconstruction_sha256':digest(SOURCE/'scripts/research_blind_round.py'),
        'entry_limit':'one filled entry per round; no invented Down depth',
        'max_holding_seconds':None,'entry_and_exit_delay_ms':300,'real_orders':0,
        'settlement':'PMData recorded outcome read only after sequential round replay; not live confirmation or actual redemption timing',
        'baseline':'last available TWAP60 observation at/before start, within 5s; confirm after first 5s',
        'book_sampling':'archived receiver-time full-depth reconstruction at .0/.3/.6 seconds',
        'fee_assumption':'current verified 0.07 crypto taker schedule applied to historical books; not historical receipts'}
    (out/'preregistered.json').write_text(dumps(manifest,indent=2),encoding='utf-8');print('LOCKED',out,flush=True)
    series={k:load_series(kind)[0] for k,kind in [('spot','chainlink_streams'),('twap','chainlink_streams_twap60s')]}
    rows=[]
    for identity in identities:
        slug=identity['market_slug'];start=int(slug.rsplit('-',1)[1]);root=out/slug
        tape=ReferenceTape();tape.connected=True;tape.status='historical Chainlink replay'
        pointers={kind:int(np.searchsorted(s[1],(start-75)*1000000,side='left')) for kind,s in series.items()}
        store=EventStore(root);gateway=ExecutionGateway(store);c=AutoPaper(root,gateway,store.append,reference=tape,entry_mode='distance_trend')
        m=Market(slug,'polymarket.crypto.btc.updown.5m',slug,'historical replay','historical-'+slug,{'Up':'historical-up','Down':'historical-down'},start,start+300,True,D(5),D('.01'),Fee(D('.07'),1),'archived Chainlink','research',True)
        states,ticks=reconstruct(next((ROOT/'raw/l2'/('date='+identity['day'])).glob('*.parquet')),slug,continuous=True)
        entered=False;entry_ms=None;exit_ms=None
        try:
            for state in states:
                now_us=state['at_us'];now=now_us//1000
                for kind,(obs,recv,price) in series.items():
                    at=pointers[kind]
                    while at<len(recv) and recv[at]<=now_us:
                        tape.add(kind,int(obs[at])//1000,(int(recv[at])+999)//1000,str(price[at]));at+=1
                    pointers[kind]=at
                b=Book('historical-up',m.condition_id)
                b.bids={D(str(p)):D(str(q)) for p,q in zip(state['bid_prices'],state['bid_sizes'])}
                b.asks={D(str(p)):D(str(q)) for p,q in zip(state['ask_prices'],state['ask_sizes'])}
                b.source_ms=state['last_event_us']//1000;b.received_ms=state['last_receive_us']//1000
                b.initialized=state['valid'] or state['sellable'];b.quality='OK' if state['valid'] else 'EMPTY' if state['sellable'] else 'GAP'
                before=len(gateway.orders)
                c.step(m,b,now,not entered,True,books={'Up':b})
                if len(gateway.orders)>before:
                    if gateway.orders[-1]['side']=='BUY':entered=True;entry_ms=now
                    elif gateway.positions[b.asset_id]['shares']==0:exit_ms=now
            store.flush()
            events=list(store.all('auto_paper.exit'))
            decisions=list(store.all('auto_paper.decision'))
            settled=False
            if any(p['shares']>0 for p in gateway.positions.values()):
                import polars as pl
                # Only now, after the round has finished, release the label.
                label=pl.scan_parquet(ROOT/'raw/markets.parquet').filter(pl.col('market_slug')==slug).select('winning_outcome').collect().item()
                if label in ('yes','no'):
                    evidence={'condition_id':m.condition_id,'closed':True,'tokens':[
                        {'token_id':'historical-up','outcome':'Up','winner':label=='yes'},
                        {'token_id':'historical-down','outcome':'Down','winner':label=='no'}]}
                    settled=bool(gateway.settle_paper(evidence,'historical-up',(start+300)*1000,source='PMData archived recorded outcome; historical simulation'))
                    if settled:exit_ms=(start+300)*1000
            row={**identity,'ticks':ticks,'entered':entered,'closed':len(gateway.closed_trades),
                'holding_seconds':(exit_ms-entry_ms)/1000 if exit_ms else None,'net_realized':gateway.realized,
                'unresolved_cost':gateway.view()['exposure'],'exit_reason':'settlement' if settled else events[-1]['payload']['reason'] if events else None,
                'candidate_reasons':{},'status':c.status,'orders':gateway.orders}
            for d in decisions:
                reason=d['payload']['reason'];row['candidate_reasons'][reason]=row['candidate_reasons'].get(reason,0)+1
            rows.append(row);print(slug,'closed',row['closed'],'pnl',gateway.realized,'hold',row['holding_seconds'],row['exit_reason'],flush=True)
        finally:store.close()
    assert digest(SOURCE/'poly5/auto_paper.py')==manifest['controller_sha256']
    assert digest(SOURCE/'poly5/reference.py')==manifest['reference_sha256']
    summary={'rounds':len(rows),'entries':sum(r['entered'] for r in rows),'closed':sum(r['closed'] for r in rows),
        'wins':sum(r['closed'] and r['net_realized']>0 for r in rows),'losses':sum(r['closed'] and r['net_realized']<0 for r in rows),
        'net_realized':sum((r['net_realized'] for r in rows),D(0)),'unresolved_cost':sum((r['unresolved_cost'] for r in rows),D(0)),
        'holdings_over_30s':sum(r['holding_seconds'] is not None and r['holding_seconds']>30 for r in rows),
        'exit_reasons':{reason:sum(r['exit_reason']==reason for r in rows) for reason in ['take_profit','sudden_reversal','settlement']},
        'real_orders':0}
    (out/'result.json').write_text(dumps({'manifest':manifest,'summary':summary,'rounds':rows},indent=2),encoding='utf-8')
    (out/'距离策略回放报告.md').write_text(f'''# 基准距离策略整轮回放

使用与桌面相同的 AutoPaper 控制器和 ReferenceTape，通过历史接收时间依次释放 Chainlink 现货、TWAP60 和重建 Up 深度。完整回放此前已查看的同 16 个五分钟窗口；这不是新盲测。每轮最多首次模拟买入一次，Down 缺深度不假造交易。当前官方费用假设、300ms 双边延迟、参数和代码哈希事前记录。基准由起始 TWAP 历史观测重建，非归档页面 Price to Beat 原字段。

入场：现价和 TWAP 位于基准同侧；相对过去 60 秒波动有距离余量；5/15 秒趋势同向；连续 3 秒确认；避免近期突变和费用后已过贵的报价。估价是未经校准的 Brownian/TWAP 近似，不能称为已经训练好的获利概率。

退出：净可卖收益至少 +0.5%；或 3 秒反向变化至少 4 倍历史噪声且逼近基准。没有 30 秒或其他固定持仓时长，不在封盘前强制卖出；不按普通浮亏比例止损。止盈执行前重新核查盈利报价。到期仍持有的仓位只在整轮顺序回放结束后读取 PMData 记录的结果并模拟兑付，不是当时已实时确认的结算或链上赎回。

- 窗口 {summary['rounds']}，模拟买入 {summary['entries']}，完成平仓 {summary['closed']}。
- 盈利 {summary['wins']}，亏损 {summary['losses']}，模拟已实现净收益 ${summary['net_realized']}。
- 未解决持仓成本 ${summary['unresolved_cost']}，不计为零或已实现损失。
- 持仓超过 30 秒后退出 {summary['holdings_over_30s']} 笔；退出原因 {summary['exit_reasons']}。

这是有限、已查看样本的探索性结果，不能推出未来盈利。真实订单为 0；双向收益、估价校准和更广泛前向表现仍需验证。逐笔账本与每秒拒绝原因见逐轮目录及 result.json。
''',encoding='utf-8')
    print('COMPLETE',out,dumps(summary),flush=True)


if __name__=='__main__':run()
