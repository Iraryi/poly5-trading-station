from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid
from PySide6.QtCore import Qt, QTimer, QProcess, QProcessEnvironment, QSize, QPoint, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath, QFont, QIcon, QPixmap, QAction, QKeySequence, QShortcut
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QFrame,QLabel,QVBoxLayout,QHBoxLayout,QGridLayout,
    QPushButton,QToolButton,QComboBox,QLineEdit,QTextEdit,QPlainTextEdit,QStackedWidget,QSplitter,QScrollArea,
    QTableWidget,QTableWidgetItem,QHeaderView,QTabWidget,QCheckBox,QSystemTrayIcon,QMenu,QMessageBox,QSizeGrip,
    QFormLayout,QDialog,QDialogButtonBox,QProgressBar,QAbstractItemView,QFileDialog)
from . import __version__
from .common import Settings, decimal, dumps, loads, masked, wallet_address, utc_now, atomic_json
from .copilot import SecretStore
from .domain import Registry
from .terminal_widgets import brand_icon, menu_icon, pane, DepthLadder, TerminalSplitter, QuoteLabel
from .quick_trade import QuickTradeView, ModeDialog

BG='#0b111a'; PANEL='#111b28'; BORDER='#1d2a39'; INK='#e5edf8'; MUTED='#8191a8'; GREEN='#56deb6'; RED='#f18694'; BLUE='#7faeff'; AMBER='#f4c780'

def money(v):
    if v is None:return '—'
    try: return f'${decimal(str(v)):,.2f}'
    except Exception:return '—'
def num(v,digits=3):
    if v is None:return '—'
    try:return f'{decimal(str(v)):,.{digits}f}'
    except Exception:return '—'
def label(text,style=None):
    w=QLabel(text); w.setTextFormat(Qt.TextFormat.PlainText)
    if style:w.setStyleSheet(style)
    return w
def button(text,callback=None,primary=False):
    w=QPushButton(text); w.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:w.setProperty('primary',True)
    if callback:w.clicked.connect(callback)
    return w
def card(title,subtitle=''):
    frame=QFrame(); frame.setObjectName('card'); lay=QVBoxLayout(frame); lay.setContentsMargins(14,12,14,12); lay.setSpacing(9)
    if title:lay.addWidget(label(title,'font-size:15px;font-weight:600;'))
    if subtitle:
        frame.setToolTip(subtitle)
    return frame,lay
def table(headers):
    w=QTableWidget(0,len(headers));w.setHorizontalHeaderLabels(headers);w.verticalHeader().hide()
    w.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    w.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);w.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    w.setAlternatingRowColors(True);w.setShowGrid(False);return w
def rows_to_table(w,rows):
    old=w.verticalScrollBar().value();w.setRowCount(len(rows))
    for r,row in enumerate(rows):
        for c,value in enumerate(row):
            item=QTableWidgetItem(str(value) if value is not None else '—');item.setToolTip(item.text());w.setItem(r,c,item)
    w.verticalScrollBar().setValue(old)

class Metric(QFrame):
    def __init__(self,title,sub='未连接 · 无可信来源'):
        super().__init__();self.setObjectName('card');layout=QVBoxLayout(self);layout.setContentsMargins(12,9,12,9);layout.setSpacing(5)
        layout.addWidget(label(title,'color:'+MUTED+';font-size:12px;'))
        self.value=label('—',"font-family:Consolas;font-size:22px;font-weight:600;");layout.addWidget(self.value)
        self.sub=label(sub,'color:'+MUTED+';font-size:10px;');self.sub.setWordWrap(False);layout.addWidget(self.sub);self.setToolTip(sub)
    def set(self,value,sub=None):
        self.value.setText(value)
        if sub is not None:self.sub.setText(sub.split('\n')[0][:38]);self.setToolTip(sub)

class PriceChart(QWidget):
    def __init__(self):
        super().__init__();self.points=[];self.market=None;self.setMinimumHeight(130)
    def add(self,state):
        market=state.get('market')
        if not market:return
        if self.market!=market['slug']:self.points=[];self.market=market['slug']
        now=state.get('server_ms')
        if not now:return
        values=[]
        for name in ('Up','Down'):
            b=state.get('quotes',state.get('books',{})).get(name,{})
            values.append(float(b['bid']) if b.get('fresh') and b.get('bid') is not None else None)
        self.points.append(((now/1000-market['start'])/300,*values));self.points=self.points[-400:];self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        area=QRectF(34,12,self.width()-53,self.height()-40)
        p.setFont(QFont('Segoe UI',9));p.setPen(QColor(MUTED))
        for fraction in (0,.25,.5,.75,1):
            y=area.bottom()-fraction*area.height();p.setPen(QColor('#1c2734'));p.drawLine(int(area.left()),int(y),int(area.right()),int(y));p.setPen(QColor(MUTED));p.drawText(0,int(y)+4,f'{fraction:.2f}')
        for second in (0,60,120,180,240,300):
            x=area.left()+second/300*area.width();p.setPen(QColor(BORDER));p.drawLine(int(x),int(area.top()),int(x),int(area.bottom()))
            p.setPen(QColor(MUTED));p.drawText(int(x)-12,int(area.bottom())+22,f'{second//60}:{second%60:02}')
        if not self.points or not any(a is not None or b is not None for _,a,b in self.points):
            p.setPen(QColor(MUTED));p.drawText(area,Qt.AlignmentFlag.AlignCenter,'等待报价');return
        for column,color in ((1,GREEN),(2,'#ee8f68')):
            path=QPainterPath();active=False;lastx=None
            for row in self.points:
                if row[column] is None:active=False;continue
                x=area.left()+max(0,min(1,row[0]))*area.width();y=area.bottom()-row[column]*area.height()
                if not active or (lastx is not None and x-lastx>area.width()*.02):path.moveTo(x,y)
                else:path.lineTo(x,y)
                active=True;lastx=x
            p.setPen(QPen(QColor(color),2));p.drawPath(path)

class TitleBar(QWidget):
    def __init__(self,window):
        super().__init__();self.window=window;self.setFixedHeight(38);lay=QHBoxLayout(self);lay.setContentsMargins(12,0,6,0)
        logo=QLabel();logo.setPixmap(brand_icon().pixmap(25,25));lay.addWidget(logo)
        lay.addWidget(label('POLY5','color:#e0e9f6;font-size:15px;font-weight:800;letter-spacing:1px;'));lay.addStretch()
        self.connection=label('●  正在启动','color:'+MUTED+';');lay.addWidget(self.connection)
        for txt,fn in [('—',window.hide_to_tray),('□',self.maximize),('×',window.close)]:
            b=button(txt,fn);b.setFixedSize(34,30);lay.addWidget(b)
    def maximize(self):self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.window.windowHandle().startSystemMove()
    def mouseDoubleClickEvent(self,event):self.maximize()

class Station(QMainWindow):
    def __init__(self,args):
        super().__init__();self.args=args;self.root=Path(args.data_dir);self.settings=Settings(self.root)
        self.state={};self.preview_id=None;self.proposal_rel=None;self.quitting=False;self.last_worker=0;self.output_buffer=b''
        self.setWindowTitle('POLY5');self.setWindowIcon(self.icon())
        available=QApplication.primaryScreen().availableGeometry();self.resize(min(1500,available.width()-40),min(900,available.height()-40));self.setMinimumSize(1080,680)
        self.setWindowFlags(Qt.WindowType.Window|Qt.WindowType.FramelessWindowHint)
        self.apply_theme()
        base=QWidget();base.setObjectName('rootWidget');self.setCentralWidget(base);outer=QVBoxLayout(base);outer.setContentsMargins(1,1,1,1);outer.setSpacing(0)
        self.title=TitleBar(self);outer.addWidget(self.title)
        self.account_bar=label('  MONITOR    |    未连接账户    |    可用 —','background:#111b28;padding:6px 12px;color:'+MUTED+';font-size:11px;')
        self.mode_stack=QStackedWidget();outer.addWidget(self.mode_stack,1)
        classic=QWidget();classic_layout=QVBoxLayout(classic);classic_layout.setContentsMargins(0,0,0,0);classic_layout.setSpacing(0)
        classic_layout.addWidget(self.account_bar)
        body=QHBoxLayout();body.setSpacing(0);classic_layout.addLayout(body,1);self.mode_stack.addWidget(classic)
        self.quick=QuickTradeView(self.send,self.open_mode_settings,self.quick_live,self.settings);self.mode_stack.addWidget(self.quick)
        nav=QWidget();nav.setObjectName('nav');nav.setFixedWidth(140);nl=QVBoxLayout(nav);nl.setContentsMargins(7,12,7,10);nl.setSpacing(4)
        self.pages=QStackedWidget();self.nav_buttons=[]
        page_names=['总览','交易台','账户中心','持仓与订单','个人数据','事件日志','模型与接入','设置']
        for i,name in enumerate(page_names):
            b=button(f'{name}',lambda checked=False,n=i:self.navigate(n));b.setCheckable(True);b.setObjectName('navButton');nl.addWidget(b);self.nav_buttons.append(b)
        nl.addStretch()
        self.mode=QComboBox();self.mode.addItems(['MONITOR','PAPER']);self.mode.setToolTip('MONITOR：只读监控；PAPER：纸面模拟');self.mode.setCurrentIndex(1 if self.settings.data['mode']=='paper' else 0)
        self.mode.currentIndexChanged.connect(self.mode_changed);nl.addWidget(self.mode)
        self.risk_button=button('暂停模拟',lambda:self.send('stop_risk'));self.risk_button.setStyleSheet('color:'+AMBER+';');nl.addWidget(self.risk_button)
        self.quick_live_button=button('一键开始实盘',self.quick_live,True);self.quick_live_button.setToolTip('预设：终考前 5 名末分钟模型池、单笔 20%、最小 $1（交易所另有 5 份最小下单量）；核验真实现金后只需最后确认一次');nl.addWidget(self.quick_live_button)
        self.live_stop_button=button('停止实盘开仓',lambda:self.send('live_stop_entries'));self.live_stop_button.setStyleSheet('color:'+RED+';');nl.addWidget(self.live_stop_button)
        body.addWidget(nav)
        self.splitter=TerminalSplitter(Qt.Orientation.Horizontal);self.splitter.setHandleWidth(5);self.splitter.setChildrenCollapsible(False);self.splitter.addWidget(self.pages);body.addWidget(self.splitter,1)
        self.build_overview();self.build_trading();self.build_account();self.build_positions();self.build_metrics();self.build_logs();self.build_modules();self.build_settings()
        self.build_copilot();self.splitter.addWidget(self.copilot);self.splitter.setStretchFactor(0,1);self.splitter.setStretchFactor(1,0)
        self.splitter.splitterMoved.connect(self.save_copilot_width)
        self.rail=QWidget();self.rail.setObjectName('dockRail');self.rail.setFixedWidth(34);rail_layout=QVBoxLayout(self.rail);rail_layout.setContentsMargins(2,9,2,0)
        self.edge=button('',self.toggle_copilot);self.edge.setObjectName('dockToggle');self.edge.setIcon(menu_icon());self.edge.setIconSize(QSize(20,20));self.edge.setFixedSize(30,32);self.edge.setToolTip('展开副驾驶');self.edge.setAccessibleName('展开副驾驶')
        rail_layout.addWidget(self.edge);rail_layout.addStretch();body.addWidget(self.rail)
        self.copilot.setVisible(bool(self.settings.data['copilot_open']));self.splitter.setSizes([900,int(self.settings.data['copilot_width'])])
        self.rail.setVisible(not bool(self.settings.data['copilot_open']))
        bottom=QHBoxLayout();bottom.setContentsMargins(12,3,4,3)
        self.status=label('正在启动独立行情进程…','color:'+MUTED+';font-size:10px;');bottom.addWidget(self.status,1)
        self.live_corner=label('实盘未启用','color:'+AMBER+';font-size:10px;');bottom.addWidget(self.live_corner);bottom.addWidget(QSizeGrip(self));outer.addLayout(bottom)
        QShortcut(QKeySequence('Ctrl+Shift+A'),self,activated=self.toggle_copilot)
        QShortcut(QKeySequence('Ctrl+1'),self,activated=lambda:self.navigate(0))
        QShortcut(QKeySequence('Ctrl+R'),self,activated=lambda:self.send('refresh'))
        self.tray=QSystemTrayIcon(self.windowIcon(),self);self.tray.setToolTip('POLY5 · 仅监控 / 实盘锁定')
        menu=QMenu(self)
        for title,fn in [('打开仪表盘',self.restore),('显示 / 隐藏 AI',self.toggle_and_show),('暂停 / 恢复监听',lambda:self.send('pause')),
                         ('重新检测网络',lambda:self.send('refresh')),('导出诊断包',lambda:self.send('export')),('完全退出',self.confirm_exit)]:menu.addAction(title,fn)
        self.tray.setContextMenu(menu);self.tray.activated.connect(self.tray_activated);self.tray.show()
        self.navigate(0);self.apply_ui_mode();self.start_worker()
        self.watchdog=QTimer(self);self.watchdog.timeout.connect(self.watch_worker);self.watchdog.start(2000)

    @staticmethod
    def icon():
        return brand_icon()

    def apply_theme(self):
        self.setStyleSheet(f'''
            QWidget{{background:transparent;color:{INK};font-family:'Microsoft YaHei UI','Segoe UI';font-size:12px;}}
            QMainWindow,QWidget#rootWidget{{background:{BG};}}
            QDialog{{background:{PANEL};}}
            QFrame#card{{background:{PANEL};border:0;border-radius:5px;}}
            QFrame#terminalPane{{background:#101923;border:0;border-radius:4px;}}
            QWidget#paneHeader{{background:#121d29;border:0;border-bottom:1px solid #1d2a37;}}
            QWidget#nav{{background:#0d1520;border:0;}}
            QWidget#copilotDock,QWidget#dockRail{{background:#101923;}}
            QPushButton#dockToggle{{background:transparent;border:0;border-radius:4px;padding:0;}}
            QPushButton#dockToggle:hover{{background:#213244;}}
            QPlainTextEdit#copilotChat{{background:#101923;border:0;border-radius:0;padding:6px;}}
            QPushButton{{background:#1a2637;border:1px solid {BORDER};border-radius:4px;padding:6px 10px;}}
            QPushButton:hover{{background:#24364d;border-color:#45607f;}}
            QPushButton:disabled{{color:#596679;background:#141d2a;}}
            QPushButton[primary='true']{{background:{GREEN};color:#09241c;border:0;font-weight:600;}}
            QPushButton#navButton{{text-align:left;background:transparent;border:0;padding:10px 12px;color:{MUTED};}}
            QPushButton#navButton:checked{{background:#19332f;color:{GREEN};border-left:3px solid {GREEN};}}
            QLineEdit,QTextEdit,QPlainTextEdit,QComboBox{{background:#0c1420;border:1px solid {BORDER};border-radius:6px;padding:9px;selection-background-color:#315a73;}}
            QLineEdit:focus,QTextEdit:focus,QComboBox:focus{{border-color:{GREEN};}}
            QTableWidget{{background:#101923;alternate-background-color:#131e2b;border:0;border-radius:3px;selection-background-color:#284058;}}
            QHeaderView::section{{background:#152130;color:{MUTED};padding:6px;border:0;border-bottom:1px solid {BORDER};}}
            QTabWidget::pane{{border:0;}} QTabBar::tab{{padding:7px 12px;background:#111a28;color:{MUTED};}}
            QTabBar::tab:selected{{color:{GREEN};border-bottom:2px solid {GREEN};}}
            QScrollArea{{border:0;background:transparent;}}QScrollBar:vertical{{background:#101925;width:7px;}}
            QScrollBar::handle:vertical{{background:#344158;min-height:24px;border-radius:3px;}} QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}
            QSplitter::handle{{background:transparent;}}QToolTip{{background:#223149;color:{INK};border:1px solid #49617d;padding:6px;}}
            QProgressBar{{border:0;background:#1e2b3b;border-radius:3px;height:6px;}}QProgressBar::chunk{{background:{GREEN};border-radius:3px;}}
            QMenu{{background:#152132;border:1px solid {BORDER};padding:7px;}}QMenu::item{{padding:8px 28px;}}QMenu::item:selected{{background:#254159;}}
        ''')

    def new_page(self,title,subtitle):
        container=QWidget();layout=QVBoxLayout(container);layout.setContentsMargins(16,14,16,16);layout.setSpacing(12)
        heading=label(title,'font-size:20px;font-weight:650;');heading.setToolTip(subtitle);layout.addWidget(heading)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(container);self.pages.addWidget(scroll)
        return layout

    def build_overview(self):
        page=QWidget();lay=QVBoxLayout(page);lay.setContentsMargins(12,10,12,10);lay.setSpacing(8);self.pages.addWidget(page)
        toolbar=QHBoxLayout();toolbar.addWidget(label('BTC','font-size:23px;font-weight:700;'))
        toolbar.addWidget(label('UP / DOWN','font-size:11px;color:'+MUTED+';'));tag=label(' 5 MIN ','background:#213245;color:#a2c2e7;padding:4px;font-size:10px;');toolbar.addWidget(tag);toolbar.addStretch()
        self.phase=label('等待市场','font-size:10px;color:'+MUTED+';');toolbar.addWidget(self.phase)
        reset=button('重置布局',self.reset_workspace);toolbar.addWidget(reset);lay.addLayout(toolbar)
        row=QHBoxLayout();row.setSpacing(8);self.over_metrics=[]
        for title in ['可用资金','持仓市值','已实现 PnL','本轮倒计时']:
            m=Metric(title,'');m.sub.hide();m.setFixedHeight(65);row.addWidget(m);self.over_metrics.append(m)
        lay.addLayout(row)
        self.workspace_vertical=TerminalSplitter(Qt.Orientation.Vertical);self.workspace_vertical.setChildrenCollapsible(False)
        self.workspace_main=TerminalSplitter(Qt.Orientation.Horizontal);self.workspace_main.setChildrenCollapsible(False)
        left=QWidget();left.setMinimumWidth(280);ll=QVBoxLayout(left);ll.setContentsMargins(0,0,0,0);ll.setSpacing(7)
        self.market_title=label('','font-size:10px;color:'+MUTED+';');self.market_title.setMinimumWidth(0)
        market,ml=pane('最优买价 · BID',self.market_title)
        legend=QHBoxLayout();legend.addWidget(label('● UP','color:'+GREEN+';font-size:10px;'));legend.addWidget(label('● DOWN','color:#ee8f68;font-size:10px;'));legend.addStretch();ml.addLayout(legend)
        self.chart=PriceChart();ml.addWidget(self.chart,1)
        self.progress=QProgressBar();self.progress.setRange(0,300);self.progress.setTextVisible(False);self.progress.setFixedHeight(3);ml.addWidget(self.progress);ll.addWidget(market,1)
        row=QHBoxLayout();row.setSpacing(7);self.quote_labels={}
        for name,color in [('Up',GREEN),('Down','#ee8f68')]:
            box,bl=card('');box.setFixedHeight(77);bl.setSpacing(3)
            top=QHBoxLayout();top.addWidget(label(name.upper(),'color:'+color+';font-size:11px;font-weight:600;'));top.addStretch();top.addWidget(label('BID / ASK','font-size:9px;color:'+MUTED+';'));bl.addLayout(top)
            price=QuoteLabel('— / —');price.setStyleSheet('color:'+color+';');bl.addWidget(price)
            depth=label('—','font-family:Consolas;font-size:9px;color:'+MUTED+';');bl.addWidget(depth)
            self.quote_labels[name]=(price,depth);row.addWidget(box)
        ll.addLayout(row);self.workspace_main.addWidget(left)
        right,rl=pane('盘口深度');right.setMinimumWidth(210)
        controls=QHBoxLayout();self.dom_outcome=QComboBox();self.dom_outcome.addItems(['Up','Down']);self.dom_outcome.currentIndexChanged.connect(self.update_dom)
        controls.addWidget(self.dom_outcome);self.dom_age=label('—','font-family:Consolas;font-size:10px;color:'+MUTED+';');controls.addStretch();controls.addWidget(self.dom_age);rl.addLayout(controls)
        self.ladder=DepthLadder();rl.addWidget(self.ladder,1);rl.addWidget(button('订单票据',lambda:self.navigate(1),True));self.workspace_main.addWidget(right)
        self.workspace_main.setStretchFactor(0,3);self.workspace_main.setStretchFactor(1,1);self.workspace_vertical.addWidget(self.workspace_main)
        lower=TerminalSplitter(Qt.Orientation.Horizontal);lower.setChildrenCollapsible(False);self.workspace_lower=lower
        self.overview_tabs=QTabWidget();self.overview_tabs.setMinimumHeight(130)
        self.overview_orders=table(['时间','方向','买卖','份额','金额','状态']);self.overview_orders.verticalHeader().setDefaultSectionSize(23)
        self.overview_events=table(['时间','事件','详情']);self.overview_events.verticalHeader().setDefaultSectionSize(23)
        self.overview_tabs.addTab(self.overview_orders,'模拟订单');self.overview_tabs.addTab(self.overview_events,'运行事件');lower.addWidget(self.overview_tabs)
        refresh=button('⟳',lambda:self.send('refresh'));refresh.setFixedSize(26,24);refresh.setToolTip('重新检测')
        health,hl=pane('连接 / 风控',refresh);health.setMinimumWidth(210)
        self.geo_label=label('地区  —','font-size:11px;color:#a6b4c7;');self.geo_label.setWordWrap(True);hl.addWidget(self.geo_label)
        self.guard_label=label('TWAP  —\n策略  停用','font-size:11px;color:#a6b4c7;');self.guard_label.setWordWrap(True);hl.addWidget(self.guard_label);hl.addStretch();lower.addWidget(health)
        lower.setStretchFactor(0,3);lower.setStretchFactor(1,1);lower.setSizes([800,240]);self.workspace_vertical.addWidget(lower)
        self.workspace_vertical.setStretchFactor(0,3);self.workspace_vertical.setStretchFactor(1,1);lay.addWidget(self.workspace_vertical,1)
        self.workspace_main.setSizes(self.settings.data.get('workspace_columns',[800,270]));self.workspace_vertical.setSizes(self.settings.data.get('workspace_rows',[430,165]))
        self.workspace_main.splitterMoved.connect(self.save_workspace);self.workspace_vertical.splitterMoved.connect(self.save_workspace)
        self.workspace_main.splitterMoved.connect(lambda *_:lower.setSizes(self.workspace_main.sizes()))
        self.workspace_main.resized.connect(lambda:lower.setSizes(self.workspace_main.sizes()))
        lower.splitterMoved.connect(lambda *_:self.workspace_main.setSizes(lower.sizes()))
        lower.splitterMoved.connect(self.save_workspace)

    def reset_workspace(self):self.workspace_main.setSizes([800,270]);self.workspace_lower.setSizes(self.workspace_main.sizes());self.workspace_vertical.setSizes([430,165]);self.save_workspace()
    def save_workspace(self,*_):
        self.settings.data['workspace_columns']=self.workspace_main.sizes();self.settings.data['workspace_rows']=self.workspace_vertical.sizes();self.settings.save()
    def update_dom(self,*_):
        if not hasattr(self,'ladder'):return
        b=self.state.get('books',{}).get(self.dom_outcome.currentText(),{});self.ladder.set_book(b)
        self.dom_age.setText((str(b.get('age_ms'))+' ms') if b.get('fresh') else '校验中' if b.get('quality') in ('GAP','CROSSED','SUSPECT') else 'STALE' if b else '—')
        self.dom_age.setToolTip('深度状态：'+b.get('quality','UNKNOWN'))

    def build_trading(self):
        lay=self.new_page('交易台','预览与纸面模拟使用可见盘口深度；真实买入、卖出和撤单入口保持锁定')
        row=QHBoxLayout();self.depth_tables={}
        for name in ('Up','Down'):
            box,bl=card(name.upper()+' / 盘口深度','每份 USD · 份额');t=table(['买价','买量','卖价','卖量']);t.setMinimumHeight(280);bl.addWidget(t);self.depth_tables[name]=t;row.addWidget(box)
        lay.addLayout(row)
        ticket,tl=card('订单票据','每笔最大含费预算 $5；最后 15 秒不新增模拟仓位；模拟总敞口上限 $5')
        form=QHBoxLayout();self.outcome=QComboBox();self.outcome.addItems(['Up','Down']);self.side=QComboBox();self.side.addItems(['BUY · 买入','SELL · 卖出模拟持仓'])
        self.budget=QLineEdit('5.00');self.budget.setPlaceholderText('含费最大支出 USD');self.limit=QLineEdit('0.50');self.limit.setPlaceholderText('限价 USD/份')
        for title,w in [('方向',self.outcome),('操作',self.side),('最大支出 USD',self.budget),('限价 USD/份',self.limit)]:
            col=QVBoxLayout();col.addWidget(label(title,'color:'+MUTED+';font-size:10px;'));col.addWidget(w);form.addLayout(col)
        tl.addLayout(form);actions=QHBoxLayout();actions.addWidget(button('预览',self.preview,True))
        self.submit=button('确认纸面模拟',self.paper_submit);self.submit.setEnabled(False);actions.addWidget(self.submit)
        for title in ('真实买入 / 卖出','真实撤单'):
            b=button(title);b.setEnabled(False);b.setToolTip('此版本没有实盘执行器');actions.addWidget(b)
        tl.addLayout(actions);self.preview_text=QPlainTextEdit('等待预览');self.preview_text.setReadOnly(True);self.preview_text.setMaximumHeight(170);tl.addWidget(self.preview_text)
        for w in (self.budget,self.limit):w.textChanged.connect(self.invalidate_preview)
        for w in (self.outcome,self.side):w.currentIndexChanged.connect(self.invalidate_preview)
        lay.addWidget(ticket);lay.addStretch()

    def build_account(self):
        lay=self.new_page('账户中心','公开地址只读绑定 · 现金、可用额度、持仓估值各自保留来源')
        box,bl=card('观察账户','不需要私钥或助记词。只读地址不代表已认证身份。')
        row=QHBoxLayout();self.account_select=QComboBox();self.refresh_account_list();self.account_select.currentIndexChanged.connect(self.switch_account)
        row.addWidget(self.account_select,1);row.addWidget(button('添加公开钱包',self.add_account,True));row.addWidget(button('连接真实账户',self.connect_account));row.addWidget(button('解除绑定',self.remove_account));row.addWidget(button('刷新',lambda:self.send('refresh')));bl.addLayout(row)
        self.account_status=label('未连接 · 尚未读取个人资产');self.account_status.setWordWrap(True);bl.addWidget(self.account_status);lay.addWidget(box)
        grid=QGridLayout();self.account_metrics={}
        for i,(key,title) in enumerate([('cash','现金缓存 · CLOB'),('available','可用买入资金'),('reserved','挂单占用'),('position_value','持仓市值'),('equity','总资产估值'),('realized_today','今日已实现 PnL'),('unrealized','持仓未实现 PnL')]):
            m=Metric(title);grid.addWidget(m,i//3,i%3);self.account_metrics[key]=m
        lay.addLayout(grid)
        note,nl=card('数据口径','持仓市值来自 Data API v2；现金及可用额度尚未接入经过核对的余额来源。')
        note.setVisible(False);lay.addWidget(note);lay.addStretch()

    def build_positions(self):
        lay=self.new_page('持仓与订单','公开持仓、认证订单与纸面模拟分开显示；换轮不会抹去旧仓位')
        self.position_tabs=QTabWidget();self.position_tables={}
        for key,name,heads in [('public','公开持仓',['市场','方向','份额','均价','估值 USD','状态']),('orders','认证订单',['订单','市场','方向','状态','累计成交','剩余']),('paper','模拟持仓',['市场','方向','份额','成本 USD']),('history','模拟执行记录',['时间','方向','买卖','份额','金额 USD','状态'])]:
            t=table(heads);self.position_tables[key]=t;self.position_tabs.addTab(t,name)
        self.position_tabs.setMinimumHeight(420);lay.addWidget(self.position_tabs)
        self.positions_note=label('账户未连接','color:'+MUTED+';');self.positions_note.setWordWrap(True);lay.addWidget(self.positions_note);lay.addStretch()

    def build_metrics(self):
        lay=self.new_page('个人数据','真实收益与模拟收益分开；只在存在有效分母时展示百分比')
        row=QHBoxLayout();self.metric_paper=Metric('模拟已实现净 PnL','paper · 本地完整模拟账本');row.addWidget(self.metric_paper)
        self.metric_rate=Metric('模拟完整平仓盈利率','盈利平仓数 / 完整平仓数');row.addWidget(self.metric_rate)
        row.addWidget(Metric('真实个人胜率','暂无已核实真实成交数据'));self.metric_prediction=Metric('基准距离','按近期波动折算');row.addWidget(self.metric_prediction);lay.addLayout(row)
        box,bl=card('净 ROI')
        self.metrics_detail=QPlainTextEdit();self.metrics_detail.setReadOnly(True);self.metrics_detail.setPlainText('预测净 ROI        未启用\n可卖净 ROI        等待持仓与完整盘口\n已实现净 ROI      —\n自动交易          未启用');bl.addWidget(self.metrics_detail);lay.addWidget(box);lay.addStretch()

    def build_logs(self):
        lay=self.new_page('事件日志','原始输入与状态变化写入 JSONL；SQLite 索引支持恢复、查询及脱敏导出')
        row=QHBoxLayout();self.log_search=QLineEdit();self.log_search.setPlaceholderText('搜索事件名 / 市场 ID / 内容');self.log_search.returnPressed.connect(self.search_logs)
        row.addWidget(self.log_search,1);row.addWidget(button('查询',self.search_logs));row.addWidget(button('回放录制',lambda:self.send('replay')));row.addWidget(button('导出 JSONL + CSV',lambda:self.send('export'),True));lay.addLayout(row)
        self.log_table=table(['UTC 时间','模式','事件','证据 ID']);self.log_table.setMinimumHeight(340);self.log_table.itemSelectionChanged.connect(self.show_log_detail);lay.addWidget(self.log_table)
        self.log_detail=QPlainTextEdit();self.log_detail.setReadOnly(True);self.log_detail.setMaximumHeight(190);lay.addWidget(self.log_detail);self.log_rows=[];lay.addStretch()

    def build_modules(self):
        lay=self.new_page('模型与接入','固定版本 · 影子预测 · 账户与执行准备')
        box,bl=card('训练模型')
        row=QHBoxLayout();row.addWidget(button('读取训练台模型',lambda:self.send('model_catalog')));self.shadow_label=label('尚未选择模型');self.shadow_label.setWordWrap(True);row.addWidget(self.shadow_label,1);bl.addLayout(row)
        self.bridge_table=table(['任务','算法','版本','历史评分 ↓','状态']);self.bridge_table.setMinimumHeight(210);bl.addWidget(self.bridge_table)
        self.bridge_catalog=[]
        row=QHBoxLayout();row.addWidget(button('固定所选版本并开始影子预测',self.pin_model,True));row.addWidget(button('停止影子预测',lambda:self.send('model_disable')));bl.addLayout(row)
        row=QHBoxLayout();self.bridge_releases=QComboBox();row.addWidget(self.bridge_releases,1);row.addWidget(button('恢复归档版本',self.rollback_model));bl.addLayout(row)
        self.shadow_detail=QPlainTextEdit();self.shadow_detail.setReadOnly(True);self.shadow_detail.setMaximumHeight(110);bl.addWidget(self.shadow_detail);lay.addWidget(box)
        box,bl=card('保守策略 · 末分钟模型投票（纸面）','训练台终考前 5 名末分钟模型合成一票、每轮最多一笔 $5；只做纸面模拟，不会下真钱单。')
        row=QHBoxLayout();row.addWidget(button('固定终考前 5 名并开始纸面投票策略',lambda:self.send('late_vote_enable',k=5),True));bl.addLayout(row)
        self.vote_label=label('尚未启用');self.vote_label.setWordWrap(True);bl.addWidget(self.vote_label);lay.addWidget(box)
        box,bl=card('接入准备')
        self.readiness_table=table(['项目','状态']);self.readiness_table.setMinimumHeight(220);bl.addWidget(self.readiness_table)
        bl.addWidget(label('模型与账户不能自行启用实盘；请在下方单独核验与授权。','color:'+MUTED+';font-size:11px;'));lay.addWidget(box)
        box,bl=card('真钱自动交易')
        self.live_status=label('尚未启用');self.live_status.setWordWrap(True);bl.addWidget(self.live_status)
        self.live_detail=QPlainTextEdit();self.live_detail.setReadOnly(True);self.live_detail.setMaximumHeight(180);bl.addWidget(self.live_detail)
        row=QHBoxLayout();row.addWidget(button('邮箱账户授权',self.configure_account));row.addWidget(button('刷新授权',lambda:self.send('reload')))
        row.addWidget(button('一键开始实盘（保守）',self.quick_live,True));row.addWidget(button('自定义配置实盘',self.configure_live));row.addWidget(button('停止新增交易',lambda:self.send('live_stop_entries')))
        row.addWidget(button('下单连通性测试（$0.05）',self.live_probe))
        row.addWidget(button('实盘完全停机',lambda:self.send('live_halt')));bl.addLayout(row);lay.addWidget(box)
        t=table(['模块','版本','状态','所需能力']);t.setMinimumHeight(150)
        rows_to_table(t,[[m.name,m.schema_version,'已启用' if m.enabled else 'Not Implemented / Disabled',', '.join(m.capabilities)] for m in Registry.builtins().modules.values()]);lay.addWidget(t)
        lay.addStretch()

    def build_settings(self):
        lay=self.new_page('设置','配置仅保存在 D:\\POLYMARKET；模型密钥使用当前 Windows 用户的 DPAPI 加密')
        box,bl=card('桌面与账户')
        self.start_tray=QCheckBox('启动时收入系统托盘');self.start_tray.setChecked(self.settings.data['start_in_tray']);bl.addWidget(self.start_tray)
        self.auth_enable=QCheckBox('启用既有 L2 凭据的认证订单只读同步');self.auth_enable.setChecked(self.settings.data.get('auth_read_enabled',False));bl.addWidget(self.auth_enable)
        self.auto_enable=QCheckBox('自动预测并执行纸面模拟（PAPER）');self.auto_enable.setChecked(self.settings.data.get('auto_paper_enabled',True));bl.addWidget(self.auto_enable)
        row=QHBoxLayout();row.addWidget(label('实际所在地区代码（两位字母，实盘地区核验用）'))
        self.region_edit=QLineEdit(str(self.settings.data.get('allowed_country') or ''));self.region_edit.setMaxLength(2);self.region_edit.setFixedWidth(70)
        row.addWidget(self.region_edit);row.addStretch();bl.addLayout(row)
        bl.addWidget(button('配置参考行情',self.configure_reference))
        helptext=label('邮箱账户在本机授权窗口连接，所有者密钥仅保存在 Windows 加密存储；不要在聊天中提供。\n关闭主窗口后继续监听，不设置开机自启。','color:'+MUTED+';font-size:11px;');helptext.setWordWrap(True);bl.addWidget(helptext)
        bl.addWidget(button('保存桌面设置',self.save_general));lay.addWidget(box)
        box,bl=card('界面模式','快捷交易为默认模式；只能在设置中切换。')
        row=QHBoxLayout();self.ui_mode_combo=QComboBox();self.ui_mode_combo.addItem('快捷交易（网页风格，默认）','quick');self.ui_mode_combo.addItem('专业终端','terminal')
        self.ui_mode_combo.setCurrentIndex(0 if self.settings.data.get('ui_mode','quick')=='quick' else 1);row.addWidget(self.ui_mode_combo,1)
        row.addWidget(button('应用界面模式',lambda:self.set_ui_mode(self.ui_mode_combo.currentData())));bl.addLayout(row);lay.addWidget(box)
        box,bl=card('软件更新')
        self.update_status=label('当前版本 '+__version__);self.update_status.setWordWrap(True);bl.addWidget(self.update_status)
        bl.addWidget(button('选择更新清单',self.check_local_update));lay.addWidget(box)
        box,bl=card('AI / Models','支持多个 OpenAI-compatible Provider，当前选择优先，失败时按列表回退。')
        row=QHBoxLayout();self.provider_select=QComboBox();self.refresh_providers();self.provider_select.currentIndexChanged.connect(self.load_provider)
        row.addWidget(self.provider_select,1);row.addWidget(button('新建',self.new_provider));row.addWidget(button('删除',self.delete_provider));bl.addLayout(row)
        form=QFormLayout();self.provider_id=QLineEdit();self.base_url=QLineEdit();self.base_url.setPlaceholderText('https://你的模型服务/v1')
        self.model_name=QLineEdit();self.api_key=QLineEdit();self.api_key.setEchoMode(QLineEdit.EchoMode.Password);self.api_key.setPlaceholderText('留空保留已加密密钥')
        for title,w in [('Provider ID',self.provider_id),('Base URL',self.base_url),('模型名',self.model_name),('API Key',self.api_key)]:form.addRow(title,w)
        bl.addLayout(form)
        self.ai_consent=QCheckBox('允许将脱敏市场快照、地区状态、事件 ID 和我的提问发送给上述模型服务');self.ai_consent.setChecked(self.settings.data['ai'].get('consent',False));bl.addWidget(self.ai_consent)
        row=QHBoxLayout();row.addWidget(button('保存并选择 Provider',self.save_provider,True));row.addWidget(button('测试模型连接',self.test_ai));bl.addLayout(row)
        small=label('每小时最多 15 次、单次超时 25 秒；无配置时副驾驶仍提供本地规则诊断。\n云端模型没有文件系统、源码修改或交易工具。','color:'+MUTED+';font-size:11px;');small.setWordWrap(True);bl.addWidget(small);lay.addWidget(box);lay.addStretch();self.load_provider()

    def check_local_update(self):
        if getattr(self,'update_process',None) and self.update_process.state()!=QProcess.ProcessState.NotRunning:return
        path,_=QFileDialog.getOpenFileName(self,'选择本地更新清单','D:/POLYMARKET/outputs/update-manifest.json','更新清单 (*.json)')
        if not path:return
        self.update_status.setText('正在校验安装包…')
        self.update_process=QProcess(self)
        self.update_process.finished.connect(self.update_check_finished)
        self.update_process.errorOccurred.connect(lambda _:self.update_status.setText('更新校验进程未能启动'))
        args=['--ipc','--check-update',path,'--data-dir',str(self.root)]
        if getattr(sys,'frozen',False):self.update_process.start(sys.executable,args)
        else:self.update_process.start(sys.executable,[str(Path(__file__).resolve().parents[1]/'main.py')]+args)

    def update_check_finished(self):
        try:
            result=loads(bytes(self.update_process.readAllStandardOutput()))
            if result['status']=='error':raise ValueError(result['error'])
            if result['status']=='current':self.update_status.setText('当前已是 '+result['version']);return
            self.update_status.setText('可更新至 '+result['version']+' · 安装包完整性校验通过')
            reply=QMessageBox.question(self,'POLY5 更新','发现 '+result['version']+'。\n'+result['notes']+'\n\n打开安装向导？程序会继续运行；实际覆盖更新前请从托盘完全退出。',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
            if reply==QMessageBox.StandardButton.Yes:
                os.startfile(result['package'])
        except Exception as e:self.update_status.setText('更新检查失败：'+str(e))

    def build_copilot(self):
        self.copilot=QWidget();self.copilot.setObjectName('copilotDock');self.copilot.setMinimumWidth(300);self.copilot.setMaximumWidth(620)
        lay=QVBoxLayout(self.copilot);lay.setContentsMargins(10,9,10,12);lay.setSpacing(8)
        head=QHBoxLayout();head.addWidget(label('副驾驶','font-size:14px;font-weight:600;'),1)
        collapse=button('',self.toggle_copilot);collapse.setObjectName('dockToggle');collapse.setIcon(menu_icon());collapse.setIconSize(QSize(20,20));collapse.setFixedSize(30,32);collapse.setToolTip('收起副驾驶');collapse.setAccessibleName('收起副驾驶');head.addWidget(collapse);lay.addLayout(head)
        self.ai_status=label('本地诊断','color:'+GREEN+';font-size:10px;');self.ai_status.setWordWrap(True);lay.addWidget(self.ai_status)
        self.ai_topic=QComboBox();self.ai_topic.addItems(['问答 / 当前局势','行情解读','网络与地区','运行诊断','Codex 建议']);lay.addWidget(self.ai_topic)
        self.copilot.setToolTip('辅助分析；不具备交易或修改源码权限')
        self.chat=QPlainTextEdit();self.chat.setObjectName('copilotChat');self.chat.setReadOnly(True);self.chat.setPlaceholderText('分析行情、连接状态或运行事件');lay.addWidget(self.chat,1)
        self.question=QPlainTextEdit();self.question.setPlaceholderText('分析当前状态，或输入你的问题…');self.question.setMaximumHeight(85);lay.addWidget(self.question)
        row=QHBoxLayout();self.ai_send=button('分析 / 发送',self.ask_ai,True);row.addWidget(self.ai_send);row.addWidget(button('取消',lambda:self.send('ai_cancel')));lay.addLayout(row)
        row=QHBoxLayout();row.addWidget(button('保存建议',self.save_advice));row.addWidget(button('修订建议',self.revise_advice));lay.addLayout(row)
        self.ai_evidence=label('证据：等待快照','font-size:10px;color:'+MUTED+';');self.ai_evidence.setWordWrap(True);lay.addWidget(self.ai_evidence)
        history=self.root/'settings'/'copilot-history.json'
        if history.exists():
            try:self.chat.setPlainText(loads(history.read_text(encoding='utf-8')).get('text','')[-20000:])
            except Exception:pass

    def navigate(self,index):
        self.pages.setCurrentIndex(index)
        for i,b in enumerate(self.nav_buttons):b.setChecked(i==index)
        if index==5 and hasattr(self,'process'):self.search_logs()

    def start_worker(self):
        self.process=QProcess(self);self.process.setWorkingDirectory(str(Path(__file__).resolve().parents[1]))
        env=QProcessEnvironment.systemEnvironment();env.insert('PYTHONUTF8','1');env.insert('TEMP',str(self.root/'cache'/'tmp'));env.insert('TMP',str(self.root/'cache'/'tmp'));self.process.setProcessEnvironment(env)
        args=['--worker','--ipc','--data-dir',str(self.root)]
        if self.args.offline:args.append('--offline')
        if not getattr(sys,'frozen',False):args.insert(0,str(Path(__file__).resolve().parents[1]/'main.py'))
        self.process.readyReadStandardOutput.connect(self.read_worker);self.process.readyReadStandardError.connect(self.read_error)
        self.process.finished.connect(self.worker_finished);self.process.errorOccurred.connect(lambda _:self.status.setText('后台进程启动/通信失败，请查看诊断日志'))
        self.process.start(sys.executable,args)

    def send(self,action,**kwargs):
        if not hasattr(self,'process') or self.process.state()!=QProcess.ProcessState.Running:return
        cmd={'action':action,'request_id':uuid.uuid4().hex,**kwargs};self.process.write((dumps(cmd)+'\n').encode('utf-8'))

    def read_worker(self):
        self.output_buffer+=bytes(self.process.readAllStandardOutput())
        while b'\n' in self.output_buffer:
            line,self.output_buffer=self.output_buffer.split(b'\n',1)
            try:msg=loads(line)
            except Exception:continue
            if msg.get('type')=='state':self.last_worker=time.monotonic();self.update_state(msg['state'])
            elif msg.get('type')=='error':
                self.status.setText(msg['message']);self.notify_result(msg['action'],msg['message'],True)
            else:self.result(msg)

    def read_error(self):
        data=bytes(self.process.readAllStandardError())
        path=self.root/'log'/'diagnostics';path.mkdir(parents=True,exist_ok=True)
        with (path/'worker-stderr.log').open('ab') as f:f.write(data)
        self.status.setText('后台报告异常；已写入 log/diagnostics/worker-stderr.log')

    def watch_worker(self):
        if self.last_worker and time.monotonic()-self.last_worker>6:
            self.title.connection.setText('●  后台心跳中断');self.title.connection.setStyleSheet('color:'+RED+';')
            self.status.setText('后台快照已超过 6 秒未更新，显示数据已过期');self.invalidate_preview()
            for _,depth in self.quote_labels.values():depth.setText('STALE · 后台快照已过期')
    def worker_finished(self,*args):
        if not self.quitting:
            self.status.setText('后台已停止；重新启动应用以恢复。历史账本保留。');self.title.connection.setText('●  后台已停止');self.invalidate_preview()

    def update_state(self,s):
        previous=(self.state.get('market') or {}).get('id');self.state=s
        if previous!=(s.get('market') or {}).get('id'):self.invalidate_preview()
        market=s.get('market') or {};account=s.get('account') or {};paper=s.get('paper',{});geo=s.get('geo',{})
        online=s.get('ws_status')=='CONNECTED' and any(b.get('fresh') for b in s.get('quotes',{}).values())
        self.title.connection.setText('●  行情接收中' if online else '●  '+s.get('ws_status','UNKNOWN'))
        self.title.connection.setStyleSheet('color:'+(GREEN if online else AMBER)+';font-size:11px;')
        mode='PAPER · 纸面模拟' if s['mode']=='paper' else 'MONITOR · 仅监控'
        live=s.get('live',{})
        if live.get('armed'):mode='LIVE · 真钱自动交易'
        self.mode.setEnabled(not live.get('armed'));self.risk_button.setEnabled(not live.get('armed'))
        self.live_stop_button.setEnabled(bool(live.get('armed') and live.get('entries_enabled')))
        self.live_status.setText(('LIVE · ' if live.get('armed') else '未启用 · ')+live.get('message',''))
        risk=live.get('risk',{});cash=live.get('cash',{})
        dd=risk.get('drawdown');allocation=risk.get('allocation')
        self.live_detail.setPlainText('小时本金基线 '+money(risk.get('baseline'))+'   净资产 '+money(risk.get('equity'))+'\n'+
            '回撤 '+(num(decimal(str(dd))*100,2)+'%' if dd is not None else '—')+'   当前单笔比例 '+(num(decimal(str(allocation))*100,2)+'%' if allocation is not None else '—')+'\n'+
            '含费预算 '+money(risk.get('all_in_budget'))+'   核验可用 '+money(cash.get('available'))+'\n'+
            '已确认成交净损益 '+money(risk.get('realized_pnl'))+'\n'+
            '模型权重 '+dumps((risk.get('pool') or {}).get('weights',{})))
        active=s.get('active_account') or {}
        if live.get('armed'):
            self.account_bar.setText(f"  {mode}     /     {active.get('alias','账户未连接')}     ·     核验可用 {money(cash.get('available'))}     ·     本金基线 {money(risk.get('baseline'))}")
        else:self.account_bar.setText(f"  {mode}     /     {active.get('alias','账户未连接')}     ·     可用 {money(account.get('available'))}     ·     模拟敞口 {money(paper.get('exposure'))}")
        self.over_metrics[0].set(money(account.get('available')),'尚未核对现金与可用额度')
        self.over_metrics[1].set(money(account.get('position_value')),'Data API v2 · '+account.get('quality','未连接'))
        self.over_metrics[2].set('—','未接入已核实真实成交')
        if live.get('armed'):
            self.over_metrics[0].set(money(cash.get('available')),'链上与 CLOB 核验；下单前重新核对')
            self.over_metrics[2].set(money(risk.get('realized_pnl')),'仅本程序已确认成交，扣除按回执计算的费用')
        rem=s.get('remaining');self.over_metrics[3].set(f'{rem//60:02}:{rem%60:02}' if rem is not None else '—',s.get('phase'))
        self.market_title.setText('USD / 份');self.market_title.setToolTip(market.get('slug','')+'\n'+market.get('rules',''))
        self.phase.setText(s.get('phase',''));self.progress.setValue(300-rem if rem is not None else 0);self.chart.add(s)
        for name,(price,depth) in self.quote_labels.items():
            b=s.get('books',{}).get(name,{})
            quote=s.get('quotes',s.get('books',{})).get(name,{})
            price.setText(num(quote.get('bid'))+' / '+num(quote.get('ask')))
            age=quote.get('age_ms');depth.setText(f"{age if age is not None else '—'} ms  ·  {'LIVE' if quote.get('fresh') else 'STALE'}")
            depth.setToolTip(f"买盘 {num(b.get('bid_depth'),1)} 份 / 卖盘 {num(b.get('ask_depth'),1)} 份")
            bids=b.get('bids',[]);asks=b.get('asks',[]);rows=[]
            for i in range(max(len(bids),len(asks))):rows.append([num(bids[i][0]),num(bids[i][1],2)] if i<len(bids) else ['—','—']);rows[-1].extend([num(asks[i][0]),num(asks[i][1],2)] if i<len(asks) else ['—','—'])
            rows_to_table(self.depth_tables[name],rows)
        self.geo_label.setText(f"出口    {geo.get('country','—')}\nGEO    {'受限' if geo.get('blocked') is True else '未限制' if geo.get('blocked') is False else 'UNKNOWN'}\nWS      {s.get('ws_status','—')}")
        self.geo_label.setToolTip(geo.get('checked_at','')+'\n平台观察到的出口；不等于完整交易资格。客户端节点与 WS 出口未核实。')
        auto=s.get('auto_paper',{});self.guard_label.setText('自动 PAPER\n'+auto.get('status','等待模型'))
        self.quick.update_state(s)
        rep=s.get('late_vote_report');self.last_vote_report=rep
        if rep:
            gate={'COLLECTING':'积累中','PASSED':'已达上线门槛（由你本人决定是否启用实盘）','FAILED_DRAWDOWN':'回撤超过 $10，未通过','FAILED_NEGATIVE':'收益区间为负，未通过'}.get(rep['gate'],rep['gate'])
            vote=auto.get('vote') or {}
            ci=(money(rep['pnl_ci'][0])+' ~ '+money(rep['pnl_ci'][1])) if rep.get('pnl_ci') else '—'
            per_hour=money(rep['pnl_per_hour']) if rep.get('pnl_per_hour') else '—'
            text=(f"已运行 {rep['hours']} 小时 · 已结束 {rep['trades']} 笔（赢 {rep['wins']}，持仓 {rep['open']}） · 盈亏 {money(rep['pnl'])} · 每小时 {per_hour}\n"
                  f"最低点 {money(rep['lowest'])} · 最大回撤 {money(rep['max_drawdown'])} · 95% 区间 {ci}\n"
                  f"门槛：{gate}（≥50 笔、区间下限 > 0、回撤 ≤ $10）")
            if vote.get('probability_up') is not None:text+=f"\n当前投票 Up 概率 {float(vote['probability_up']):.3f}（{vote['models']} 个模型）"
            self.vote_label.setText(text)
        if live.get('armed'):self.guard_label.setText('LIVE · '+('允许开仓' if live.get('entries_enabled') else '停止开仓')+'\n'+live.get('message',''))
        self.live_corner.setText('实盘运行中 · 允许开仓' if live.get('armed') and live.get('entries_enabled') else '实盘运行中 · 已停止开仓' if live.get('armed') else '实盘未启用')
        stop_text='停止实盘开仓' if live.get('armed') and live.get('entries_enabled') else '已停止开仓（持仓照常管理）' if live.get('armed') else '实盘未运行'
        if self.live_stop_button.text()!=stop_text:
            self.live_stop_button.setText(stop_text);self.live_stop_button.setEnabled(bool(live.get('armed') and live.get('entries_enabled')))
        self.guard_label.setToolTip(s.get('strategy_reason',''));self.update_dom()
        rows_to_table(self.overview_orders,[[p['at'][11:19],p['outcome'],p['side'],num(p['shares']),money(p['total']),p['status']] for p in reversed(paper.get('orders',[])[-15:])])
        rows_to_table(self.overview_events,[[e['at'][11:19],e['kind'],e['detail']] for e in reversed(s.get('events',[])[-15:])])
        for key,m in self.account_metrics.items():
            m.set(money(account.get(key)),(account.get('source','未连接')+' · '+account.get('quality','UNKNOWN')+'\n'+account.get('observed_at','')) if account.get(key) is not None else '缺少该项目的可信数据源')
        self.account_status.setText(('公开账户 · '+account.get('address','')+' · '+account.get('quality','')+' · '+account.get('observed_at','')) if account else '未连接 / 尚未同步公开账户')
        auth=s.get('account_auth') or {}
        if auth.get('quality')=='OK':
            self.account_status.setText('账户已认证只读 · '+account.get('address','')+' · '+auth.get('observed_at',''))
            if account.get('cash') is not None:self.account_metrics['cash'].set(money(account['cash']),account.get('cash_source','')+'\n'+account.get('cash_observed_at',''))
        elif auth.get('error'):self.account_status.setText(self.account_status.text()+'\n认证：'+auth['error'])
        if s.get('account_error'):self.account_status.setText(self.account_status.text()+'\n同步异常：'+s['account_error'])
        rows_to_table(self.position_tables['public'],[[p.get('title') or p.get('condition_id'),p.get('outcome'),num(p.get('current_size')),num(p.get('avg_price')),money(p.get('current_value')),p.get('status')] for p in account.get('positions',[])])
        rows_to_table(self.position_tables['orders'],[[o['id'][:16],o.get('market','')[:16],o.get('side'),o.get('state'),num(o.get('size_matched')),num(o.get('remaining'))] for o in s.get('auth_orders') or []])
        rows_to_table(self.position_tables['paper'],[[p['slug'],p['outcome'],num(p['shares']),money(p['basis'])] for p in paper.get('positions',{}).values() if decimal(str(p['shares']))>0])
        rows_to_table(self.position_tables['history'],[[p['at'],p['outcome'],p['side'],num(p['shares']),money(p['total']),p['status']] for p in reversed(paper.get('orders',[]))])
        self.positions_note.setText('认证同步：'+s.get('user_ws_status','未授权'))
        shadow=s.get('model_bridge',{});self.shadow_label.setText(shadow.get('message','尚未选择模型'))
        output=shadow.get('output',{});selected=shadow.get('selected',{})
        self.shadow_detail.setPlainText((selected.get('model_id','')+'\n'+output.get('slug','')+'\n'+
            ('Up 概率 '+f"{float(output['probability_up']):.2%}" if output.get('probability_up') is not None else '预测净 ROI '+f"{float(output['predicted_net_roi']):.3%}" if output.get('predicted_net_roi') is not None else shadow.get('message','等待预测'))))
        rows_to_table(self.readiness_table,[[r['name'],'已就绪' if r['passed'] else '待完成'] for r in s.get('readiness',{}).get('checks',[])])
        self.metric_paper.set(money(paper.get('realized_pnl')));denom=paper.get('closed_count',0);wins=paper.get('profitable_count',0)
        self.metric_rate.set(f'{wins/denom:.1%}' if denom else '—',f'{wins} / {denom} · 完整平仓样本')
        predictions=auto.get('predictions',[])
        reference=auto.get('reference',{})
        distance=reference.get('distance_sigma')
        self.metric_prediction.set(f"{float(distance):+.2f} σ" if distance is not None else '—','TWAP 相对基准距离' if distance is not None else '等待参考行情')
        text='自动 PAPER  '+auto.get('status','等待模型')+'\n'
        if reference:
            text+='\n基准 '+num(reference.get('baseline'),2)+'  ·  当前 TWAP '+num(reference.get('twap'),2)
            text+='\nTWAP 距离 '+num(reference.get('twap_distance'),2)+' USD  ·  '+num(distance,2)+' σ'
            text+='\n现价距离 '+num(reference.get('spot_distance'),2)+' USD'
        candidate=auto.get('candidate',{})
        if candidate:text+='\n候选 '+candidate['outcome']+'  ·  含费估计余量 '+num(candidate.get('estimated_edge_per_share'),4)+' / 份'
        text+='\n\n可卖净 ROI  '+(f"{float(auto['liquidation_roi']):.2%}" if auto.get('liquidation_roi') is not None else '—')
        text+='\n模拟已实现净收益  '+money(paper.get('realized_pnl'))+'\n真实下单  未启用'
        self.metrics_detail.setPlainText(text)
        self.ai_evidence.setText('快照 '+s['at'][11:19]+' UTC');self.ai_evidence.setToolTip('证据：'+' · '.join(s.get('evidence_ids',[])[-5:]))
        self.risk_button.setText('恢复模拟' if paper.get('paused') else '暂停模拟')
        lag=s.get('ws_lag_ms');lag_text=f"   ·   延迟 {lag/1000:.1f} 秒" if isinstance(lag,(int,float)) and lag>1000 else ''
        self.status.setText(s.get('storage_error') or f"行情 {s.get('ws_status','—')}{lag_text}   ·   地区 {geo.get('country','—')}   ·   {s['at'][11:19]} UTC")
        self.status.setToolTip(f"后台 PID {int(self.process.processId())} · 账本 {s.get('storage_bytes',0)/1024/1024:.1f} MB · 重连 {s.get('reconnects',0)}")

    def configure_reference(self):
        args=[] if getattr(sys,'frozen',False) else [str(Path(__file__).resolve().parents[1]/'main.py')]
        args+=['--configure-reference','--data-dir',str(self.root)]
        QProcess.startDetached(sys.executable,args)
        self.status.setText('已打开本机参考行情配置向导')

    def configure_account(self):
        args=[] if getattr(sys,'frozen',False) else [str(Path(__file__).resolve().parents[1]/'main.py')]
        QProcess.startDetached(sys.executable,args+['--configure-account','--data-dir',str(self.root)])
        self.status.setText('已打开本机邮箱钱包授权窗口；完成后点击刷新授权')

    def configure_live(self):
        from .model_bridge import ModelBridge,TASK_NAMES
        releases=ModelBridge(self.root).releases()
        releases=[r for r in releases if r['task'] in ('direction','direction_mkt','late','late_mkt')]
        if not releases:QMessageBox.information(self,'先固定模型','先在上方读取模型，逐个固定需要的方向模型版本。ROI 模型不能当方向概率投票。');return
        dialog=QDialog(self);dialog.setWindowTitle('实盘配置 · 第一步');dialog.resize(740,480);layout=QVBoxLayout(dialog)
        text=label('本金按首次核验的全部可用现金。每小时只上调基线，回撤50%熔断。\n回撤越大单笔比例越低；未达到最小金额就等待，不强行买入。');text.setWordWrap(True);layout.addWidget(text)
        form=QFormLayout();allocation=QLineEdit('20');minimum=QLineEdit('1');form.addRow('单笔基础比例（%，含费预算）',allocation);form.addRow('最小合约金额（美元）',minimum);layout.addLayout(form)
        layout.addWidget(label('选择固定模型池：全阶段与末分钟分别成组，不能混入不同预测任务。'))
        scroll=QScrollArea();scroll.setWidgetResizable(True);contents=QWidget();rows=QVBoxLayout(contents);checks=[];seen=set()
        for meta in releases:
            check=QCheckBox(TASK_NAMES[meta['task']]+' · '+meta['model_id']+' · '+meta['release_id'][-8:]);rid=meta['release_id']
            if meta['task'] in ('direction','direction_mkt') and meta['task'] not in seen:check.setChecked(True);seen.add(meta['task'])
            checks.append((check,rid));rows.addWidget(check)
        scroll.setWidget(contents);layout.addWidget(scroll)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons);buttons.rejected.connect(dialog.reject)
        def submit():
            try:
                fraction=decimal(allocation.text())/100
                self.send('live_prepare',release_ids=[rid for check,rid in checks if check.isChecked()],allocation=str(fraction),minimum=minimum.text())
                self.status.setText('正在核验资金钱包、链上现金、授权额度和挂单占用…');dialog.accept()
            except Exception as exc:QMessageBox.warning(dialog,'配置未提交',str(exc))
        buttons.accepted.connect(submit);dialog.exec()

    def apply_ui_mode(self):
        quick=self.settings.data.get('ui_mode','quick')=='quick'
        self.mode_stack.setCurrentWidget(self.quick if quick else self.mode_stack.widget(0))

    def set_ui_mode(self,mode):
        if mode not in ('quick','terminal'):return
        # Reload first so settings the worker saved (vote releases etc.) are never overwritten by a stale copy.
        self.settings=Settings(self.root);self.settings.data['ui_mode']=mode;self.settings.save()
        self.quick.settings=self.settings;self.send('reload');self.apply_ui_mode()
        if hasattr(self,'ui_mode_combo'):self.ui_mode_combo.setCurrentIndex(0 if mode=='quick' else 1)

    def open_mode_settings(self):
        d=ModeDialog(self,self.settings.data.get('ui_mode','quick'))
        if d.exec():self.set_ui_mode(d.mode())

    def live_probe(self):
        text=('用与真实下单相同的签名和凭据，在卖价 ≥ $0.05 的一侧以 $0.01 挂 5 份买单（总价 $0.05，不会主动成交），'
              '交易所接受后立即撤销。\n\n用途：判断交易所是否接受本程序的订单。最坏情况（撤销前恰有人以 $0.01 卖出）花费 $0.05。\n\n需要实盘处于已启用状态。确定发送测试单吗？')
        if QMessageBox.question(self,'下单连通性测试',text)==QMessageBox.StandardButton.Yes:
            self.send('live_probe');self.status.setText('正在发送连通性测试单…')

    def quick_live(self):
        """One click: preset conservative pool and sizing, verified cash preview, then the single final confirmation."""
        ids=Settings(self.root).data.get('vote_release_ids') or []
        if not ids:
            QMessageBox.information(self,'先固定模型池','请先在「模型与接入」页点击「固定终考前 5 名并开始纸面投票策略」。');return
        self.send('live_prepare',release_ids=ids,allocation='0.20',minimum='1')
        self.status.setText('一键实盘：正在核验资金钱包、链上现金、授权额度和挂单占用…')

    def confirm_live(self,preview):
        dialog=QDialog(self);dialog.setWindowTitle('核对并启用真钱交易 · 最后一步');dialog.resize(720,560);layout=QVBoxLayout(dialog)
        rep=getattr(self,'last_vote_report',None) or {}
        gate={'COLLECTING':'尚在积累，未证明盈利','PASSED':'已达门槛','FAILED_DRAWDOWN':'回撤超过 $10，未通过','FAILED_NEGATIVE':'收益为负，未通过'}.get(rep.get('gate'),'无数据')
        rule=label('实盘规则（较高频）：每轮最后 60 秒，现价与 TWAP 位于基准同侧（不限距离）；模型池对该方向信心 ≥ 55%，且比含费价格高出 ≥ 0.03（回撤时两者都提高）；约每 8 笔可能有 1 笔整笔亏损；'
                   '连续 3 秒确认后限价买入；净盈利 ≥ 0.5% 或突发反转时卖出，其余持有到结算。每轮最多一笔，同时只持一个仓位。\n'
                   f"同一模型池的纸面投票策略：已结束 {rep.get('trades',0)} 笔，盈亏 {money(rep.get('pnl'))}，门槛状态：{gate}。",'color:'+AMBER+';')
        rule.setWordWrap(True);layout.addWidget(rule)
        cap=preview['capital'];config=preview['config'];cash=preview['cash']
        text=label('已核验现金 '+money(cash['cash'])+'   可用 '+money(cash['available'])+'\n'+
            '本金基线 '+money(cap['baseline'])+'   50%回撤线 '+money(decimal(str(cap['baseline']))/2)+'\n'+
            '基础单笔 '+num(decimal(str(config['allocation']))*100,2)+'%   最小合约 '+money(config['minimum_notional'])+'\n'+
            '每小时盈利抬高基线，亏损不下调。充值不计入盈利。\n'+
            '模型未被证明盈利；50%停机不能保证跳空或无法卖出时的损失上限。\n'+
            '到期仓位需在官方账户确认并赎回，期间暂停新增交易。\n'+
            '本次固定模型：\n'+'\n'.join(r['model_id'] for r in preview['models']));text.setWordWrap(True);layout.addWidget(text)
        region=str(Settings(self.root).data.get('allowed_country') or '未设置').upper()
        consent=QCheckBox(f'我实际位于设置中填写的地区（{region}）、已达法定年龄、符合当地法律及平台资格，并接受上述资金规则。');layout.addWidget(consent)
        phrase=QLineEdit();phrase.setPlaceholderText('本人输入：启用真钱交易');layout.addWidget(phrase)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons);buttons.rejected.connect(dialog.reject)
        def submit():
            if not consent.isChecked() or phrase.text()!='启用真钱交易':return
            self.send('live_arm',token=preview['token'],attested=True,phrase=phrase.text());dialog.accept()
        buttons.accepted.connect(submit);dialog.exec()

    def result(self,msg):
        action=msg.get('action');r=msg.get('result')
        if action=='live_prepare' and r:self.confirm_live(r)
        elif action in ('live_arm','live_stop_entries','live_halt'):
            # The status bar is rewritten every second by state updates, so confirm safety actions visibly.
            text=(r or {}).get('message','实盘状态已更新');self.status.setText(text)
            QMessageBox.information(self,{'live_arm':'实盘已启用','live_stop_entries':'已停止新增实盘交易','live_halt':'实盘已完全停机'}[action],text)
        elif action=='model_catalog' and r:
            self.bridge_catalog=r['catalog'];rows_to_table(self.bridge_table,[[x['name'],x.get('family'),x['id'],num(x.get('metrics',{}).get('score'),5),x['status']] for x in self.bridge_catalog])
            self.bridge_releases.clear()
            for item in r['releases']:self.bridge_releases.addItem(item['model_id']+' · '+item['release_id'][-8:],item['release_id'])
            self.status.setText(f"读取到 {len(self.bridge_catalog)} 个任务模型；仅在手动固定版本后启用影子预测")
        elif action in ('live_manual_buy','live_manual_sell') and r:self.quick.show_result(action,r)
        elif action=='live_probe' and r:
            if r.get('accepted'):QMessageBox.information(self,'连通性测试：交易所已接受',r.get('message',''))
            else:QMessageBox.warning(self,'连通性测试：交易所拒绝',f"HTTP {r.get('status','—')} · 代码 {r.get('code') or '—'}\n\n交易所说明：{r.get('message') or '（无）'}")
        elif action=='late_vote_enable' and r:
            self.settings=Settings(self.root);self.send('model_catalog')
            self.status.setText('已固定 '+str(len(r['releases']))+' 个终考模型，纸面投票策略已启动（不下真钱单）')
        elif action in ('model_pin','model_rollback','model_disable'):
            self.settings=Settings(self.root);self.send('model_catalog');self.status.setText('模型影子设置已保存')
        elif action=='preview' and r:
            self.preview_id=r['preview_id'];self.submit.setEnabled(self.settings.data['mode']=='paper')
            self.preview_text.setPlainText(f"模拟预览  ·  {r['outcome'].upper()}  {r['side']}\n\n"+(f"份额  {num(r.get('shares'),6)}\n含费支出  {money(r.get('total'))}    手续费  {money(r.get('fee'))}" if r.get('shares') is not None else '按限价与可用买盘模拟卖出持仓。'))
        elif action=='paper_submit':
            self.invalidate_preview();self.preview_text.setPlainText(f"纸面模拟  ·  {r['outcome'].upper()}  {r['side']}\n\n份额  {num(r['shares'],6)}\n含费金额  {money(r['total'])}    手续费  {money(r['fee'])}\n状态  {r['status']}")
        elif action=='logs':
            self.log_rows=r or [];rows_to_table(self.log_table,[[e['observed_at_utc'],e['mode'],e['event_type'],e['event_id']] for e in self.log_rows])
        elif action=='ai' and r:
            self.ai_send.setEnabled(True);self.chat.appendPlainText('\n'+r.get('at','')+' · '+r['provider']+'\n'+r['text'])
            self.ai_status.setText(r['provider']+' · '+r.get('model','规则诊断'))
            atomic_json(self.root/'settings'/'copilot-history.json',{'text':self.chat.toPlainText()[-20000:]})
        elif action=='advice' and r:self.proposal_rel=r['relpath'];self.status.setText('建议已保存：'+r['path']);self.chat.appendPlainText('\n建议已保存：'+r['path'])
        elif action=='replay' and r:self.log_detail.setPlainText(dumps(r,indent=2));self.status.setText('回放完成：'+r['path'])
        elif action=='export' and r:self.status.setText('已导出：'+r['path']);QMessageBox.information(self,'诊断包已导出',r['path']+'\n包含脱敏事件、CSV、完整性哈希。')
    def notify_result(self,action,text,error=False):
        if action in ('live_manual_buy','live_manual_sell'):
            if error:self.quick.show_error(text)
            return
        if action.startswith('live_') and error:QMessageBox.warning(self,'实盘未执行',text)
        if action in ('preview','paper_submit'):self.preview_text.setPlainText('未执行：'+text);self.invalidate_preview()
        elif action=='ai':self.ai_send.setEnabled(True);self.chat.appendPlainText('\n'+text)
    def invalidate_preview(self,*_):self.preview_id=None;self.submit.setEnabled(False)
    def preview(self):self.send('preview',outcome=self.outcome.currentText(),side='BUY' if self.side.currentIndex()==0 else 'SELL',budget=self.budget.text(),limit=self.limit.text())
    def paper_submit(self):
        if self.preview_id:self.send('paper_submit',preview_id=self.preview_id);self.invalidate_preview()
    def mode_changed(self,index):self.settings.data['mode']='paper' if index else 'monitor';self.settings.save();self.send('reload');self.invalidate_preview()
    def search_logs(self):self.send('logs',query=self.log_search.text())
    def show_log_detail(self):
        row=self.log_table.currentRow()
        if 0<=row<len(self.log_rows):self.log_detail.setPlainText(dumps(self.log_rows[row],indent=2))

    def refresh_account_list(self):
        self.account_select.blockSignals(True);self.account_select.clear();self.account_select.addItem('未连接',None)
        for a in self.settings.data['accounts']:self.account_select.addItem(a['alias']+' · '+masked(a['address']),a['id'])
        index=self.account_select.findData(self.settings.data['active_account_id']);self.account_select.setCurrentIndex(max(0,index));self.account_select.blockSignals(False)
    def add_account(self):
        dialog=QDialog(self);dialog.setWindowTitle('绑定公开钱包（只读）');form=QFormLayout(dialog)
        alias=QLineEdit();address=QLineEdit();address.setMinimumWidth(430);kind=QComboBox();kind.addItems(['unknown','deposit','proxy','safe','EOA'])
        form.addRow('别名',alias);form.addRow('公开资金钱包地址',address);form.addRow('钱包类型（不代表认证）',kind)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);form.addRow(buttons);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:addr=wallet_address(address.text().strip())
        except ValueError as e:QMessageBox.warning(self,'地址格式不正确',str(e));return
        aid=uuid.uuid4().hex;self.settings.data['accounts'].append({'id':aid,'alias':alias.text().strip()[:40] or '观察账户','address':addr,'wallet_type':kind.currentText()})
        self.settings.data['active_account_id']=aid;self.settings.save();self.refresh_account_list();self.send('reload')
    def switch_account(self,index):self.settings.data['active_account_id']=self.account_select.currentData();self.settings.save();self.send('reload')
    def remove_account(self):
        aid=self.settings.data['active_account_id']
        if not aid:return
        self.settings.data['accounts']=[a for a in self.settings.data['accounts'] if a['id']!=aid];self.settings.data['active_account_id']=None;self.settings.save();self.refresh_account_list();self.send('reload')

    def connect_account(self):
        self.configure_account()
        return
    def connect_clob_readonly(self):
        from .account_connection import configured,load,save,SIGNATURE_TYPES
        active=self.settings.active_account()
        if not active:QMessageBox.information(self,'连接账户','先添加并选择 Polymarket 的资金钱包公开地址。');return
        dialog=QDialog(self);dialog.setWindowTitle('连接真实账户 · 只读');dialog.resize(660,360);layout=QVBoxLayout(dialog)
        text=label('资金钱包：'+masked(active['address'])+'\n填写 CLOB L2 三项凭据；Relayer / Builder Key 不适用。');text.setWordWrap(True);layout.addWidget(text)
        form=QFormLayout();kind=QComboBox();kind.addItem('请选择已确认的钱包类型',None)
        for name,value in SIGNATURE_TYPES.items():kind.addItem(name,value)
        signer=QLineEdit();form.addRow('钱包类型',kind);form.addRow('签名地址（公开 0x 地址）',signer);fields={}
        for key,title in [('KEY','CLOB API Key'),('SECRET','CLOB Secret'),('PASSPHRASE','CLOB Passphrase')]:
            field=QLineEdit();field.setEchoMode(QLineEdit.EchoMode.Password);field.setPlaceholderText('已配置时，三项留空可保留');fields[key]=field;form.addRow(title,field)
        if configured(self.root,active['id']):
            try:old=load(self.root,active);signer.setText(old['SIGNER']);kind.setCurrentIndex(max(0,kind.findData(old['SIGNATURE_TYPE'])))
            except Exception:pass
        layout.addLayout(form);note=label('不填写私钥、助记词或聊天验证码。保存后核对身份、读取订单和余额缓存；不发送订单。','color:'+MUTED+';');note.setWordWrap(True);layout.addWidget(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons);buttons.rejected.connect(dialog.reject)
        def commit():
            try:
                save(self.root,active,{**{k:w.text().strip() for k,w in fields.items()},'SIGNER':signer.text().strip(),'SIGNATURE_TYPE':kind.currentData()})
                self.settings.data['auth_read_enabled']=True;self.settings.save();self.auth_enable.setChecked(True);self.send('reload');dialog.accept()
            except Exception as exc:QMessageBox.warning(dialog,'尚未保存',str(exc))
        buttons.accepted.connect(commit);dialog.exec()

    def pin_model(self):
        index=self.bridge_table.currentRow()
        if not 0<=index<len(self.bridge_catalog):self.status.setText('先读取训练台模型，再选择一行');return
        row=self.bridge_catalog[index]
        self.send('model_pin',task=row['task'],model_id=row['id'])

    def rollback_model(self):
        if self.bridge_releases.currentData():self.send('model_rollback',release_id=self.bridge_releases.currentData())
    def save_general(self):
        code=self.region_edit.text().strip().upper()
        self.settings.data['allowed_country']=code if len(code)==2 and code.isalpha() else None
        self.settings.data['start_in_tray']=self.start_tray.isChecked();self.settings.data['auth_read_enabled']=self.auth_enable.isChecked();self.settings.data['auto_paper_enabled']=self.auto_enable.isChecked();self.settings.save();self.send('reload');self.status.setText('桌面与账户设置已保存')
    def refresh_providers(self):
        self.provider_select.blockSignals(True);self.provider_select.clear()
        for p in self.settings.data['ai']['providers']:self.provider_select.addItem(p['id'],p['id'])
        index=self.provider_select.findData(self.settings.data['ai'].get('active'));self.provider_select.setCurrentIndex(max(0,index));self.provider_select.blockSignals(False)
    def load_provider(self,*_):
        if not hasattr(self,'provider_id'):return
        p=next((p for p in self.settings.data['ai']['providers'] if p['id']==self.provider_select.currentData()),{})
        self.provider_id.setText(p.get('id',''));self.base_url.setText(p.get('base_url',''));self.model_name.setText(p.get('model',''));self.api_key.clear()
    def new_provider(self):self.provider_id.clear();self.base_url.clear();self.model_name.clear();self.api_key.clear();self.provider_id.setFocus()
    def save_provider(self):
        try:
            name=self.provider_id.text().strip();secrets=SecretStore(self.root);secrets._path(name)
            from urllib.parse import urlparse
            url=self.base_url.text().strip().rstrip('/');parsed=urlparse(url)
            if parsed.scheme not in ('https','http') or not parsed.hostname or not self.model_name.text().strip():raise ValueError('请填写有效 API 地址与模型名')
            if parsed.scheme=='http' and parsed.hostname not in ('localhost','127.0.0.1','::1'):raise ValueError('远程服务必须使用 HTTPS')
            if parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('API 地址不能包含凭据、参数或片段')
            if self.api_key.text():secrets.save(name,self.api_key.text());self.api_key.clear()
            ai=self.settings.data['ai'];ai['providers']=[p for p in ai['providers'] if p['id']!=name]+[{'id':name,'base_url':url,'model':self.model_name.text().strip(),'timeout':25}]
            ai['active']=name;ai['consent']=self.ai_consent.isChecked();ai['enabled']=ai['consent'];self.settings.save();self.refresh_providers();self.send('reload');self.status.setText('Provider 已保存，密钥不写入普通设置与日志')
        except Exception as e:QMessageBox.warning(self,'无法保存',str(e))
    def delete_provider(self):
        name=self.provider_select.currentData()
        if not name:return
        SecretStore(self.root).delete(name);ai=self.settings.data['ai'];ai['providers']=[p for p in ai['providers'] if p['id']!=name];ai['active']=ai['providers'][0]['id'] if ai['providers'] else '';self.settings.save();self.refresh_providers();self.load_provider();self.send('reload')
    def test_ai(self):self.toggle_and_show();self.send('ai',question='连接测试',test=True)
    def ask_ai(self):
        text=self.question.toPlainText().strip() or self.ai_topic.currentText()+'：分析当前状态'
        self.chat.appendPlainText('\n你：'+text);self.question.clear();self.ai_send.setEnabled(False);self.send('ai',question=text)
    def save_advice(self):self.send('advice',text=self.chat.toPlainText()[-18000:])
    def revise_advice(self):
        if not self.proposal_rel:self.status.setText('请先保存一份建议');return
        path=self.root/'ai log'/self.proposal_rel
        dialog=QDialog(self);dialog.setWindowTitle('修订建议（保留历史）');dialog.resize(700,520);lay=QVBoxLayout(dialog);editor=QPlainTextEdit(path.read_text(encoding='utf-8'));lay.addWidget(editor)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);lay.addWidget(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:self.send('advice',text=editor.toPlainText(),relpath=self.proposal_rel)

    def toggle_copilot(self):
        opened=self.copilot.isHidden();self.copilot.setVisible(opened)
        self.rail.setVisible(not opened)
        if opened:self.splitter.setSizes([max(520,self.width()-int(self.settings.data['copilot_width'])-210),int(self.settings.data['copilot_width'])])
        self.settings.data['copilot_open']=opened;self.settings.save()
    def save_copilot_width(self,*_):
        if self.copilot.isVisible():self.settings.data['copilot_width']=max(300,min(620,self.copilot.width()));self.settings.save()
    def toggle_and_show(self):
        self.restore()
        if not self.copilot.isVisible():self.toggle_copilot()
    def restore(self):self.showNormal();self.raise_();self.activateWindow()
    def tray_activated(self,reason):
        if reason==QSystemTrayIcon.ActivationReason.DoubleClick:self.hide_to_tray() if self.isVisible() else self.restore()
    def hide_to_tray(self):
        if QSystemTrayIcon.isSystemTrayAvailable():self.hide()
        else:self.showMinimized()
    def closeEvent(self,event):
        if self.quitting:event.accept()
        elif QSystemTrayIcon.isSystemTrayAvailable():event.ignore();self.hide_to_tray()
        else:event.ignore();self.confirm_exit()
    def changeEvent(self,event):
        from PySide6.QtCore import QEvent
        if event.type()==QEvent.Type.WindowStateChange and self.isMinimized() and QSystemTrayIcon.isSystemTrayAvailable():QTimer.singleShot(0,self.hide)
        super().changeEvent(event)
    def confirm_exit(self):
        if QMessageBox.question(self,'完全退出','停止后台行情采集并刷盘退出？')==QMessageBox.StandardButton.Yes:self.shutdown()
    def shutdown(self):
        if self.quitting:return
        self.quitting=True;self.watchdog.stop();self.send('exit');self.process.closeWriteChannel()
        if not self.process.waitForFinished(15000):self.process.terminate();self.process.waitForFinished(3000)
        self.tray.hide();QApplication.instance().quit()

def desktop_main(args):
    app=QApplication(sys.argv);app.setApplicationName('Poly5');app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont('Microsoft YaHei UI',9))
    name='poly5-'+hashlib.sha256(str(Path(args.data_dir).resolve()).lower().encode()).hexdigest()[:20]
    socket=QLocalSocket();socket.connectToServer(name)
    if socket.waitForConnected(400):socket.write(b'show');socket.flush();socket.waitForBytesWritten(500);return
    server=QLocalServer();server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(name):raise RuntimeError('无法建立本地单实例通道')
    window=Station(args)
    def activate():
        client=server.nextPendingConnection()
        if client:client.disconnectFromServer();client.deleteLater()
        window.restore()
    server.newConnection.connect(activate)
    app.aboutToQuit.connect(window.shutdown)
    if args.show or not window.settings.data['start_in_tray'] or not QSystemTrayIcon.isSystemTrayAvailable():window.show()
    # User preference: tray operation is silent, including first launch.
    if args.smoke_test:
        from .ui_smoke import install_smoke
        install_smoke(window,app)
    app.exec()
