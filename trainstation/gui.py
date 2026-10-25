import json
import os
from pathlib import Path
import sys
import time
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QComboBox,QPushButton,QLabel,QLineEdit,QCheckBox,QGroupBox,QTableWidget,QTableWidgetItem,QHeaderView,QProgressBar,QSystemTrayIcon,QMenu,QSpinBox
from PySide6.QtGui import QAction,QIcon
from .data import Data,TASKS,ACTIVE_TASKS,read
from .runner import command,launch,BATCH_TRIALS
from poly5.common import atomic_json
from poly5.reference import reference_secret_store
from . import __version__

class Window(QMainWindow):
    def __init__(self,args):
        super().__init__();self.args=args;self.root=Path(args.root);self.data=Data(self.root)
        self.setWindowTitle(f'POLY5 · 模型训练台 {__version__} · 动态择机止盈');self.resize(1050,740)
        icon=Path(__file__).resolve().parents[1]/'assets/poly5.ico'
        if icon.exists():self.setWindowIcon(QIcon(str(icon)))
        self.setStyleSheet('''QWidget{background:#0d1520;color:#dfe8f4;font:14px "Microsoft YaHei UI";}
            QGroupBox{border:1px solid #263344;border-radius:6px;margin-top:10px;padding:14px;}QGroupBox::title{subcontrol-origin:margin;left:12px;}
            QLineEdit,QComboBox{background:#172232;border:1px solid #304156;border-radius:4px;padding:7px;min-height:20px;}
            QPushButton{background:#1b3447;border:0;border-radius:5px;padding:12px 30px;}QPushButton:disabled{color:#607083;background:#17212f;}
            QHeaderView::section{background:#172232;padding:8px;border:0;}QTableWidget{border:0;gridline-color:#1d2939;}QProgressBar{border:0;background:#1b2738;height:8px;}QProgressBar::chunk{background:#49cfae;}''')
        body=QWidget();self.setCentralWidget(body);layout=QVBoxLayout(body);layout.setContentsMargins(24,20,24,20);layout.setSpacing(14)
        title=QLabel('POLY5  /  模型训练台');title.setStyleSheet('font-size:22px;font-weight:600;');layout.addWidget(title)
        row=QHBoxLayout();self.family=QComboBox();self.family.addItem('混合种群：梯度提升 / 森林 / 线性','auto')
        for name,value in [('梯度提升种群','hist'),('随机森林种群','forest'),('线性模型种群','linear')]:self.family.addItem(name,value)
        self.task=QComboBox();self.task.addItem('全部任务（轮流训练）','all')
        for key in ACTIVE_TASKS:self.task.addItem(TASKS[key][0],key)
        self.library=QComboBox();self.library.addItem('scikit-learn · CPU','sklearn')
        for label,widget in [('模型',self.family),('类型',self.task),('库',self.library)]:row.addWidget(QLabel(label));row.addWidget(widget,1)
        layout.addLayout(row)
        row=QHBoxLayout();self.rollback=QComboBox();self.rollback.addItem('自动延续当前最优版本',None);row.addWidget(QLabel('恢复起点'));row.addWidget(self.rollback,1);layout.addLayout(row)
        ai=QGroupBox('AI 监督');ai.setCheckable(True);ai.setChecked(True);self.ai=ai;form=QHBoxLayout(ai)
        self.url=QLineEdit();self.url.setPlaceholderText('兼容 OpenAI 的 API 地址，含 /v1')
        self.model=QLineEdit();self.model.setPlaceholderText('模型名');self.key=QLineEdit();self.key.setEchoMode(QLineEdit.EchoMode.Password);self.key.setPlaceholderText('API Key：本机加密保存，留空沿用')
        self.interval=QSpinBox();self.interval.setRange(1,1440);self.interval.setValue(60);self.interval.setSuffix(' 分钟 / 次')
        self.interval.setToolTip('请求之间的最短间隔。默认 60 分钟；失败也计数，重启不重置。停止后修改并重新训练生效。')
        form.addWidget(self.url,2);form.addWidget(self.model,1);form.addWidget(self.key,2);form.addWidget(self.interval);layout.addWidget(ai)
        row=QHBoxLayout();self.start=QPushButton('训练');self.start.setStyleSheet('background:#4bd1ae;color:#09291f;font-weight:600;');self.stop=QPushButton('停止')
        self.start.clicked.connect(self.start_training);self.stop.clicked.connect(self.stop_training);row.addWidget(self.start);row.addWidget(self.stop);row.addStretch();self.phase=QLabel('就绪');row.addWidget(self.phase);layout.addLayout(row)
        self.progress=QProgressBar();self.progress.setRange(0,BATCH_TRIALS);layout.addWidget(self.progress)
        self.summary=QLabel('等待启动');self.summary.setWordWrap(True);layout.addWidget(self.summary)
        self.connection=QLabel('');self.connection.setWordWrap(True);layout.addWidget(self.connection)
        self.table=QTableWidget(0,6);self.table.setHorizontalHeaderLabels(['模型 / 类型','代数','程序评分 ↓','候选回测盈亏（样本外，含历史盘口）','状态','版本'])
        self.table.horizontalHeaderItem(3).setToolTip('每个候选在训练和选优都没见过的样本上的模拟回测：盘口感知任务含历史盘口重建（数百笔），其余任务只有实时样本。这不是实时亏损；实时盈亏见上方每个任务一行的“纸面盈亏”。')
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.table.verticalHeader().hide();self.table.setShowGrid(False);layout.addWidget(self.table,1)
        self.footer=QLabel('模型、谱系和失败记录保存在 D:\\POLYMARKET\\trainer。历史评分与纸面盈亏都不是实盘收益。');self.footer.setStyleSheet('color:#8092aa;font-size:12px;');layout.addWidget(self.footer)
        self.tray=QSystemTrayIcon(self.windowIcon(),self);menu=QMenu();menu.addAction('打开训练台',self.showNormal);menu.addAction('退出界面（后台训练继续）',QApplication.quit);self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason:self.showNormal() if reason==QSystemTrayIcon.ActivationReason.DoubleClick else None);self.tray.show()
        config=read(self.root/'config.json',{})
        for widget,key in [(self.family,'family'),(self.task,'task')]:
            i=widget.findData(config.get(key));widget.setCurrentIndex(max(0,i))
        self.url.setText(config.get('ai',{}).get('base_url',''));self.model.setText(config.get('ai',{}).get('model',''));self.ai.setChecked(config.get('ai',{}).get('enabled',True))
        self.interval.setValue(int(config.get('ai',{}).get('interval_minutes',60)))
        self.last_table=0;self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(500)
        self.refresh()
        if args.autostart or args.smoke_test:QTimer.singleShot(500,self.start_training)
        if args.smoke_test:self.qa_begin=time.monotonic();self.qa_stopping=False;self.qa=QTimer(self);self.qa.timeout.connect(self.smoke);self.qa.start(500)
    def start_training(self):
        current=read(self.root/'state.json',{})
        if current.get('running') and int(time.time()*1000)-current.get('heartbeat_ms',0)<15000:return
        if self.key.text().strip():
            reference_secret_store(self.root).save('ai-supervisor',self.key.text().strip());self.key.clear()
        config={'family':self.family.currentData(),'task':self.task.currentData(),'library':self.library.currentData(),
            'rollback_id':self.rollback.currentData(),'ai':{'enabled':self.ai.isChecked(),'base_url':self.url.text().strip(),'model':self.model.text().strip(),'interval_minutes':self.interval.value()}}
        atomic_json(self.root/'config.json',config)
        args=command('--worker','--root',str(self.root))
        if self.args.offline or self.args.smoke_test:args.append('--offline')
        launch(args);self.phase.setText('正在启动');self.start.setEnabled(False)
    def stop_training(self):
        state=read(self.root/'state.json',{})
        if state.get('run_id'):atomic_json(self.root/'stop.json',{'run_id':state['run_id']})
        self.phase.setText('正在停止并保存进度')
    @staticmethod
    def money(paper):
        if not paper or not paper.get('markets'):return '—'
        extra=''
        if paper.get('policy_kind')=='dynamic':
            details=[]
            if paper.get('holding'):details.append(f"持仓 {paper['holding']}")
            if paper.get('pending_entries'):details.append(f"待成交 {paper['pending_entries']}")
            if paper.get('censored'):details.append(f"观测缺段 {paper['censored']}")
            extra=(' · '+' / '.join(details)) if details else ''
            if not paper.get('trades') and (paper.get('entered') or paper.get('open')):
                return '暂无完整平仓结果'+extra
        if not paper.get('trades'):return f"未出手（{paper['markets']} 个市场）"+extra
        ci=paper.get('pnl_ci')
        band=f"，95% 区间 {ci[0]:+.0f}~{ci[1]:+.0f}" if ci else ''
        return f"{paper['pnl']:+.2f} 美元 / {paper['trades']} 笔（胜 {paper['wins']}{band}）"+extra
    def shadow_line(self,s):
        sh=s.get('shadow33') or {}
        if not sh:return ''
        if sh.get('error'):return '\n交易台 3.3 规则影子账本：'+sh['error']
        return '\n交易台 3.3 规则影子账本（逐秒重放，纸面）：'+' · '.join(f"{label} {self.money(sh.get(k))}" for k,label in (('tp05','+0.5% 止盈'),('hold','持有到结算'),('hold80','$80 阈值持有')))
    def refresh(self):
        s=read(self.root/'state.json',{});alive=s.get('running') and int(time.time()*1000)-s.get('heartbeat_ms',0)<20000
        for w in [self.family,self.task,self.library,self.rollback,self.ai]:w.setEnabled(not alive)
        self.start.setEnabled(not alive);self.stop.setEnabled(bool(alive));self.phase.setText(s.get('phase','就绪'))
        tasks=s.get('tasks') or {};current=tasks.get(s.get('task'),{});self.progress.setValue(min(BATCH_TRIALS,current.get('trials',0)))
        lines=[];best_ids=set()
        for key,t in tasks.items():
            best=t.get('best') or {};metrics=best.get('metrics') or {};best_ids.add(best.get('id'))
            stats=t.get('stats') or {};live=t.get('live_pooled') or {}
            score=f"验证 {metrics['score']:.4f}" if metrics else '尚无合格冠军'
            if key.endswith('_mkt'):score+=(f" · 对市场技能 {metrics['skill_vs_market']:+.4f}" if metrics.get('skill_vs_market') is not None else '')+(' · '+t['qualification'] if t.get('qualification') else '')
            accuracy=f" · 实时准确率 {live['accuracy']:.1%}（{live['n']}）" if live.get('n') else ''
            replay=f" · 历史快进 {t['replay']}" if t.get('replay') else ''
            proxy=f"+代理 {stats['proxy_rows']:,}" if stats.get('proxy_rows') else ''
            frozen=t.get('frozen') or {}
            if frozen:
                lines.append(f"    冻结验证·原规则（{time.strftime('%m-%d %H:%M',time.localtime(frozen['since_ms']/1000))} 起）：{frozen['verdict']} · 纸面盈亏 {self.money(frozen.get('paper'))}")
                if frozen.get('filtered_since_ms'):lines.append(f"    冻结验证·冷门过滤（{time.strftime('%m-%d %H:%M',time.localtime(frozen['filtered_since_ms']/1000))} 起）：{frozen['filtered_verdict']} · 纸面盈亏 {self.money(frozen.get('filtered_paper'))}")
            lines.insert(len(lines)-((2 if frozen.get('filtered_since_ms') else 1) if frozen else 0),f"{TASKS[key][0]}：第 {t.get('generation',0)} 代 {t.get('trials',0)}/{BATCH_TRIALS} · 样本 {stats.get('labelled',0):,}{proxy} · {score}{accuracy} · 实时纸面盈亏 {self.money(t.get('paper'))}{replay}")
        self.summary.setText('\n'.join(lines) or '等待启动')
        self.summary.setToolTip('动态任务：逐秒比较可成交净 ROI 与继续持有价值，无固定秒数退出；持仓、待成交与观测缺段分别显示，缺段结果不算完整验证收益。方向任务：概率比含费可成交均价高 3 个百分点且价格≥0.35 才模拟入场。每模型每市场最多一笔；均为纸面模拟。')
        c=s.get('collector',{});recent=c.get('error') and int(time.time()*1000)-c.get('error_ms',0)<600000
        collector_error=('  ·  采集错误（'+time.strftime('%H:%M',time.localtime(c['error_ms']/1000))+'）：'+c['error']) if recent else ''
        self.connection.setText((c.get('reference','只读行情待启动')+'  ·  '+c.get('book','')+collector_error+'\n'+s.get('ai_status','')) if not s.get('error') else s['error'])
        report=s.get('ai_report') or {};o=report.get('overall') or {};brk=report.get('breaker') or {}
        if o.get('pairs'):
            ab=f"AI 对照（近 {report.get('window_hours',24)} 小时 {o['pairs']} 组配对）：AI 名次 {o['ai_mean_rank']:.2f} vs 对照 {o['control_mean_rank']:.2f}（0 最好），优势 {o['mean_advantage']:+.3f}［{o['advantage_ci'][0]:+.3f}, {o['advantage_ci'][1]:+.3f}］，代内第一 AI {o['generation_wins_ai']} / 对照 {o['generation_wins_control']}"
        else:ab='AI 对照：尚无配对结果（AI 提案生效后每代与一个对照候选比较）'
        g=brk.get('global') or {}
        if g.get('until_ms',0)>time.time()*1000:ab+=f" · 全局熔断至 {time.strftime('%H:%M',time.localtime(g['until_ms']/1000))}（{g.get('reason','')}）"
        tripped=[TASKS[t][0] for t,v in (brk.get('tasks') or {}).items() if v.get('until_ms',0)>time.time()*1000 and t in TASKS]
        if tripped:ab+=' · 按任务熔断（AI 名额暂由程序接管）：'+'、'.join(tripped)
        slots=report.get('slots') or {}
        if slots:ab+=' · AI 名额：'+'，'.join(f"{TASKS[t][0].split(' · ')[0]}{'·盘口' if t.endswith('_mkt') else ''} {n}" for t,n in slots.items() if t in TASKS)
        errors=' · '.join(str(c[k]) for k in ('dynamic_backfill_error','dynamic_metadata') if c.get(k))
        self.connection.setText(self.connection.text()+'\n'+ab+(('\n'+c['recorder']) if c.get('recorder') else '')+(('\n'+c['dynamic_backfill']) if c.get('dynamic_backfill') else '')+(('\n动态回填：'+errors) if errors else '')+self.shadow_line(s))
        self.connection.setToolTip(s.get('ai_status',''))
        if time.monotonic()-self.last_table<3:return
        self.last_table=time.monotonic()
        with self.data.db() as db:rows=db.execute('SELECT body FROM trials ORDER BY created_ms DESC LIMIT 100').fetchall()
        self.table.setRowCount(len(rows));choice=self.rollback.currentData();self.rollback.blockSignals(True);self.rollback.clear();self.rollback.addItem('自动延续当前最优版本',None)
        for i,(raw,) in enumerate(rows):
            r=json.loads(raw);m=r.get('metrics');name={'hist':'梯度提升','forest':'随机森林','linear':'线性','extra':'极端随机树','logit':'逻辑回归'}.get(r['family'],r['family'])
            status='研究最优' if r['id'] in best_ids else '已存档' if m else '失败留档'
            mark={'ai':' · AI','control':' · 对照'}.get(r.get('origin'),'')
            values=[TASKS[r['task']][0]+' / '+name+mark,str(r['generation']),f"{m['score']:.5f}" if m else '—',self.money((m or {}).get('paper_pnl')),status,r['id']]
            for col,v in enumerate(values):self.table.setItem(i,col,QTableWidgetItem(v))
            if m:self.rollback.addItem(r['id'],r['id'])
        self.rollback.setCurrentIndex(max(0,self.rollback.findData(choice)));self.rollback.blockSignals(False)
    def closeEvent(self,event):
        if QSystemTrayIcon.isSystemTrayAvailable():event.ignore();self.hide()
        else:event.accept()
    def smoke(self):
        state=read(self.root/'state.json',{})
        if sum(t.get('trials',0) for t in (state.get('tasks') or {}).values())>=12 and not self.qa_stopping:
            self.grab().save(str(self.root/'trainer-running.png'));self.stop_training();self.qa_stopping=True
        if self.qa_stopping and state.get('running') is False:
            self.refresh()
            self.grab().save(str(self.root/'trainer-stopped.png'))
            with self.data.db() as db:n=db.execute('SELECT COUNT(*) FROM trials').fetchone()[0]
            atomic_json(self.root/'smoke-result.json',{'passed':n>=12 and self.start.isEnabled(),'trials':n,'stopped':True,'worker_pid':state['pid']});QApplication.quit()
        if time.monotonic()-self.qa_begin>150:
            self.stop_training();atomic_json(self.root/'smoke-result.json',{'passed':False,'state':state,'reason':'timeout'});QApplication.quit()

def run_gui(args):
    app=QApplication(sys.argv);app.setApplicationName('POLY5 Trainer');app.setQuitOnLastWindowClosed(False)
    window=Window(args);window.show();return app.exec()
