"""Deterministic observation reports. No profitability claims from quote data."""
import csv
import json
from pathlib import Path
import sqlite3
from .common import atomic_json, dumps, utc_now

def observation_report(db_path,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(db_path,timeout=15)
    counts=dict(conn.execute('SELECT event_type,COUNT(*) FROM events GROUP BY event_type').fetchall())
    # Raw ticks are counted per segment (log/events/raw), not indexed per row.
    raw_total=conn.execute('SELECT COALESCE(SUM(messages),0) FROM raw_segments').fetchone()[0]
    if raw_total:counts['book.raw']=raw_total
    total=conn.execute('SELECT COUNT(*),MIN(observed_at_utc),MAX(observed_at_utc) FROM events').fetchone()
    markets=[]
    for body in conn.execute("SELECT body FROM events WHERE event_type='market.discovered' ORDER BY seq"):
        event=json.loads(body[0]);m=event['payload']['market']
        stats=conn.execute("SELECT COALESCE(SUM(messages),0),MIN(first_utc),MAX(last_utc) FROM raw_segments WHERE market_id=?",(m['id'],)).fetchone()
        markets.append({'slug':m['slug'],'market_id':m['id'],'window_start_utc_seconds':m['start'],'window_end_utc_seconds':m['end'],
                        'first_market_observation':event['observed_at_utc'],'raw_messages':stats[0],
                        'first_book_observation':stats[1],'last_book_observation':stats[2],
                        'condition_id':m['condition_id'],'resolution_source':m['resolution_source'],'fee':m['fee']})
    conn.close()
    result={'created_at_utc':utc_now(),'mode':'monitor','event_count':total[0],'first_observation':total[1],
            'last_observation':total[2],'event_types':counts,'markets':markets,'actual_orders_sent':0,
            'personal_realized_pnl':None,'personal_winrate':None,'doi':None,'doi_definition':'definition_pending',
            'twap':'Not connected: authorized reference feed unavailable',
            'limitations':['不是盈利评估；没有用户真实账户或成交回执','轮次首尾存在发现/换订阅延迟，不声称无缝完整数据','WebSocket 未提供全局连续序号，缺口需结合重连与最优价一致性检测']}
    atomic_json(output/'observation-report.json',result)
    with (output/'rounds.csv').open('w',encoding='utf-8-sig',newline='') as f:
        keys=['slug','market_id','window_start_utc_seconds','window_end_utc_seconds','raw_messages','first_book_observation','last_book_observation']
        w=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore');w.writeheader();w.writerows(markets)
    text='# Poly5 只读观察报告\n\n'
    text+=f"生成时间（UTC）：{result['created_at_utc']}\n\n观测范围：{total[1]} 至 {total[2]}。记录 {total[0]:,} 条去重事件，发现 {len(markets)} 个市场窗口。\n\n"
    text+='真实下单数：0。个人现金、已实现 PnL、真实胜率：未知。DOI：待定义。\n\n'
    for m in markets:text+=f"- {m['slug']}：{m['raw_messages']:,} 条原始盘口消息，{m['first_book_observation']} 至 {m['last_book_observation']}。\n"
    text+='\n本报告只验证数据采集。未连接授权的 Chainlink TWAP、未读取用户账户、未验证策略盈利能力。换轮有发现与订阅延迟；不能将记录条数当作完整行情覆盖率。\n'
    (output/'观察报告.md').write_text(text,encoding='utf-8')
    return result
