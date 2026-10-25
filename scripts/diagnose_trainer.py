"""Read-only support snapshot for Codex / Claude. Does not read keys or send requests."""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

SOURCE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SOURCE))
from trainstation import __version__

def read(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return default

def collect(root):
    root=Path(root).resolve();now=int(time.time()*1000);state=read(root/'state.json',{}) or {}
    heartbeat=state.get('heartbeat_ms');age=now-heartbeat if isinstance(heartbeat,(int,float)) else None
    out={'observed_utc':datetime.now(timezone.utc).isoformat(),'source_version':__version__,'data_root':str(root),
         'worker':{k:state.get(k) for k in ('running','pid','phase','error','run_id')},'heartbeat_age_ms':age,
         'healthy_heartbeat':bool(state.get('running') and age is not None and -5000<=age<20000),
         'collector':{k:v for k,v in (state.get('collector') or {}).items() if k in ('reference','market','book','error','error_ms','dynamic_backfill','dynamic_backfill_error','recorder')},
         'tasks':{},'issues':[]}
    config=read(root/'config.json',{}) or {}
    out['ai']={'enabled':bool((config.get('ai') or {}).get('enabled')),'interval_minutes':(config.get('ai') or {}).get('interval_minutes'),'status':state.get('ai_status')}
    for task,info in (state.get('tasks') or {}).items():
        best=info.get('best') or {};deploy=best.get('deploy') or {}
        out['tasks'][task]={'generation':info.get('generation'),'trials_in_batch':info.get('trials'),'stats':info.get('stats'),
                            'best_id':best.get('id'),'deployed_id':deploy.get('id'),'paper':info.get('paper')}
    path=root/'training.sqlite'
    if path.is_file():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3)) as db:
            db.execute('PRAGMA query_only=ON')
            out['samples']={t:n for t,n in db.execute('SELECT task,COUNT(*) FROM samples GROUP BY task')}
            out['trial_count']=db.execute('SELECT COUNT(*) FROM trials').fetchone()[0]
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='dynamic_forward'").fetchone():
                out['dynamic_forward']=[{'status':s,'has_gap':bool(gap),'count':n} for s,gap,n in db.execute("SELECT json_extract(body,'$.status'),COALESCE(json_extract(body,'$.censored'),0),COUNT(*) FROM dynamic_forward GROUP BY 1,2")]
    else:out['issues'].append('训练数据库不存在')
    if state.get('running') and not out['healthy_heartbeat']:out['issues'].append('后台声称运行，但心跳过期')
    if state.get('error'):out['issues'].append('后台报告错误')
    if (state.get('collector') or {}).get('dynamic_backfill_error'):out['issues'].append('动态路径回填失败')
    installed=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'Programs/POLY5Trainer'
    if (installed/'manifest.json').is_file():
        from scripts.trainer_release import verify
        out['installed_payload']=verify(installed)
        if not out['installed_payload']['passed']:out['issues'].append('已安装文件与发行清单不一致')
    out['limitations']=['只读诊断，不创建订单、不读取凭据、不修改训练或实盘配置。','安装检查对应 manifest；源码版本与运行版本应结合 build-info 和 EXE 哈希核对。']
    return out

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',default='D:/POLYMARKET/trainer');p.add_argument('--output');args=p.parse_args()
    result=collect(args.root)
    output=Path(args.output) if args.output else Path('D:/POLYMARKET/outputs/diagnostics')/('trainer-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(output),'healthy_heartbeat':result['healthy_heartbeat'],'issues':result['issues']},ensure_ascii=False))
