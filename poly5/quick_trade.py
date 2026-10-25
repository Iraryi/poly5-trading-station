"""Web-style quick-trade page (default UI mode): one BTC 5-minute market, live price vs target, countdown and 1-Tap
buttons. Every click becomes a live_manual_* command handled by LiveService (armed by the user, audited, reconciled);
this widget never talks to the exchange itself."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D, InvalidOperation
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath, QFont, QBrush
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QSizePolicy,
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox, QComboBox)

WHITE='#ffffff'; PAGE='#ffffff'; INK='#1f2328'; GRAY='#77808d'; LINE='#e8eaee'; SOFT='#f4f5f7'
GREEN='#2a9d4b'; GREEN_DARK='#1f7f3a'; RED='#e5484d'; ORANGE='#f5a524'; BTC_ORANGE='#f7931a'

def et_label(epoch_s):
    """US Eastern wall time without tz databases (frozen builds may lack tzdata): DST from the second Sunday of March
    02:00 local to the first Sunday of November 02:00 local."""
    utc=datetime.fromtimestamp(epoch_s,timezone.utc);y=utc.year
    def sunday(month,n):
        d=datetime(y,month,1,tzinfo=timezone.utc);return d+timedelta(days=(6-d.weekday())%7+7*(n-1))
    dst=sunday(3,2).replace(hour=7)<=utc<sunday(11,1).replace(hour=6)
    local=utc+timedelta(hours=-4 if dst else -5)
    return local

def clock_text(local):
    h=local.hour;return ('上午 ' if h<12 else '下午 ')+f'{(h%12) or 12}:{local.minute:02}'

MONTHS=['一月','二月','三月','四月','五月','六月','七月','八月','九月','十月','十一月','十二月']

def dec(v):
    try:return D(str(v)) if v not in (None,'') else None
    except (InvalidOperation,ValueError):return None

def usd(v,digits=2):
    return '—' if v is None else f'${v:,.{digits}f}'

def lbl(text,style=''):
    w=QLabel(text);w.setStyleSheet(style);return w


class BtcIcon(QWidget):
    def __init__(self,size=86):
        super().__init__();self.setFixedSize(size,size);self.s=size
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(BTC_ORANGE));p.setPen(Qt.PenStyle.NoPen);p.drawRoundedRect(QRectF(0,0,self.s,self.s),self.s*.12,self.s*.12)
        p.setPen(QColor(WHITE));f=QFont('Segoe UI',int(self.s*.42));f.setBold(True);f.setItalic(True);p.setFont(f)
        p.drawText(QRectF(0,0,self.s,self.s),Qt.AlignmentFlag.AlignCenter,'₿')


class PriceVsTarget(QWidget):
    """Rolling 90-second BTC price line with the round's target as a dashed line (or an edge pill when off-scale)."""
    WINDOW_MS=90_000
    def __init__(self):
        super().__init__();self.series=[];self.target=None;self.setMinimumHeight(300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
    def set(self,series,target):
        self.series=series or [];self.target=target;self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor(WHITE))
        area=QRectF(4,30,self.width()-96,self.height()-62)
        if not self.series:
            p.setPen(QColor(GRAY));p.drawText(area,Qt.AlignmentFlag.AlignCenter,'等待 Chainlink 价格');return
        end=self.series[-1][0];start=end-self.WINDOW_MS
        pts=[(t,v) for t,v in self.series if t>=start] or self.series[-1:]
        lo=min(v for _,v in pts);hi=max(v for _,v in pts)
        if hi-lo<40:mid=(hi+lo)/2;lo,hi=mid-20,mid+20
        pad=(hi-lo)*.12;lo-=pad;hi+=pad
        y=lambda v:area.bottom()-(v-lo)/(hi-lo)*area.height()
        x=lambda t:area.left()+(t-start)/self.WINDOW_MS*area.width()
        f=QFont('Segoe UI',9);p.setFont(f)
        step=next(s for s in (5,10,25,50,100,250,500,1000) if (hi-lo)/s<=6)
        v=(int(lo/step)+1)*step
        while v<hi:
            yy=y(v);p.setPen(QPen(QColor(LINE),1));p.drawLine(QPointF(area.left(),yy),QPointF(area.right(),yy))
            p.setPen(QColor(GRAY));p.drawText(QPointF(area.right()+10,yy+4),f'${v:,.0f}');v+=step
        for k in range(15,91,15):
            t=start+k*1000;xx=x(t)
            local=datetime.fromtimestamp(t/1000);txt=local.strftime('%I:%M:%S %p').lstrip('0')
            p.setPen(QColor(GRAY));p.drawText(QRectF(xx-50,area.bottom()+8,100,18),Qt.AlignmentFlag.AlignCenter,txt)
        if self.target is not None:
            tv=float(self.target)
            if lo<=tv<=hi:
                pen=QPen(QColor('#b6bcc6'),1.4);pen.setStyle(Qt.PenStyle.DashLine);p.setPen(pen)
                p.drawLine(QPointF(area.left(),y(tv)),QPointF(area.right(),y(tv)));pill_y=y(tv)-11;arrow=''
            else:
                pill_y=area.top()-26 if tv>hi else area.bottom()-22;arrow=' ⌃' if tv>hi else ' ⌄'
            rect=QRectF(area.right()-104,pill_y,104,22);p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#8b929c'));p.drawRoundedRect(rect,6,6)
            p.setPen(QColor(WHITE));fb=QFont('Segoe UI',9);fb.setBold(True);p.setFont(fb);p.drawText(rect,Qt.AlignmentFlag.AlignCenter,'Target'+arrow)
        path=QPainterPath()
        for i,(t,v) in enumerate(pts):
            (path.moveTo if i==0 else path.lineTo)(QPointF(x(t),y(v)))
        fill=QPainterPath(path);fill.lineTo(QPointF(x(pts[-1][0]),area.bottom()));fill.lineTo(QPointF(x(pts[0][0]),area.bottom()));fill.closeSubpath()
        p.fillPath(fill,QBrush(QColor(245,165,36,22)))
        p.setPen(QPen(QColor(ORANGE),2.2));p.setBrush(Qt.BrushStyle.NoBrush);p.drawPath(path)
        cx,cy=x(pts[-1][0]),y(pts[-1][1]);p.setBrush(QColor(ORANGE));p.setPen(QPen(QColor(WHITE),2));p.drawEllipse(QPointF(cx,cy),5,5)


class Pill(QPushButton):
    def __init__(self,text,kind='light'):
        super().__init__(text);self.setCursor(Qt.CursorShape.PointingHandCursor);self.kind(kind)
    def kind(self,kind):
        styles={'light':f'background:{SOFT};color:{INK};border:0;border-radius:22px;padding:10px 18px;font-size:17px;font-weight:600;',
                'dark':f'background:#1f2328;color:{WHITE};border:0;border-radius:22px;padding:10px 18px;font-size:17px;font-weight:700;'}
        self.setStyleSheet('QPushButton{'+styles[kind]+'}')


class AmountButton(QPushButton):
    """Website-style amount tile: large amount, small green 'win' line underneath."""
    def __init__(self):
        super().__init__();lay=QVBoxLayout(self);lay.setContentsMargins(4,10,4,10);lay.setSpacing(2)
        self.big=lbl('',f'font-size:26px;font-weight:800;color:{INK};background:transparent;border:0;')
        self.small=lbl('',f'font-size:15px;color:#4b5563;background:transparent;border:0;')
        for w in (self.big,self.small):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter);w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents);lay.addWidget(w)
    def show_amount(self,amount,win):
        self.big.setText(f'${amount:,.0f}')
        self.small.setText(f"win <span style='color:{GREEN};font-weight:700'>{usd(win) if win is not None else '—'}</span>")


class QuickTradeView(QWidget):
    def __init__(self,send,open_settings,arm_live,settings):
        super().__init__();self.send=send;self.open_settings=open_settings;self.arm_live=arm_live;self.settings=settings
        self.side='Up';self.tab='buy';self.state=None;self.book_open=False
        # Browser-style light page: a plain QWidget only paints its stylesheet background with WA_StyledBackground,
        # otherwise the terminal's dark root shows through between the cards.
        self.setObjectName('quickRoot');self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        self.setStyleSheet(f'QWidget#quickRoot{{background:{PAGE};}} QLabel{{background:transparent;color:{INK};}}'
                           f'QDialog{{background:{WHITE};}} QLineEdit,QComboBox{{background:{WHITE};color:{INK};border:1px solid {LINE};border-radius:8px;padding:8px;}}')
        root=QVBoxLayout(self);root.setContentsMargins(0,0,0,0);root.setSpacing(0)
        root.addWidget(self.build_topbar())
        body=QHBoxLayout();body.setContentsMargins(36,22,36,22);body.setSpacing(36);root.addLayout(body,1)
        body.addLayout(self.build_left(),3);body.addWidget(self.build_trade_card(),0,Qt.AlignmentFlag.AlignTop)

    # ---------- layout ----------
    def build_topbar(self):
        bar=QFrame();bar.setStyleSheet(f'QFrame{{background:{WHITE};border-bottom:1px solid {LINE};}}');bar.setFixedHeight(72)
        h=QHBoxLayout(bar);h.setContentsMargins(36,0,28,0)
        h.addWidget(lbl('POLY5',f'font-size:26px;font-weight:800;color:{INK};'));h.addSpacing(10)
        h.addWidget(lbl('BTC 5分钟 · 快捷交易',f'font-size:15px;color:{GRAY};'));h.addStretch()
        self.portfolio=self.top_value(h,'资产组合');self.cash_top=self.top_value(h,'现金')
        gear=QPushButton('⚙  设置');gear.setCursor(Qt.CursorShape.PointingHandCursor)
        gear.setStyleSheet(f'QPushButton{{background:{SOFT};color:{INK};border:0;border-radius:10px;padding:10px 16px;font-size:15px;}}QPushButton:hover{{background:#e9ebef;}}')
        gear.clicked.connect(self.open_settings);h.addSpacing(16);h.addWidget(gear)
        return bar
    def top_value(self,h,title):
        box=QVBoxLayout();box.setSpacing(0);box.addWidget(lbl(title,f'font-size:13px;color:{INK};'),0,Qt.AlignmentFlag.AlignHCenter)
        v=lbl('—',f'font-size:19px;font-weight:700;color:{GREEN};');box.addWidget(v,0,Qt.AlignmentFlag.AlignHCenter);h.addLayout(box);h.addSpacing(18)
        return v
    def build_left(self):
        col=QVBoxLayout();col.setSpacing(14)
        head=QHBoxLayout();head.setSpacing(20);head.addWidget(BtcIcon(86))
        t=QVBoxLayout();t.setSpacing(4);t.addWidget(lbl('BTC 5分钟上涨或下跌',f'font-size:30px;font-weight:800;'))
        self.when=lbl('—',f'font-size:19px;color:{GRAY};');t.addWidget(self.when);head.addLayout(t);head.addStretch();col.addLayout(head)
        prices=QHBoxLayout();prices.setSpacing(26)
        tb=QVBoxLayout();tb.addWidget(lbl('目标价格',f'font-size:15px;color:{GRAY};font-weight:600;'))
        self.target=lbl('—',f'font-size:34px;font-weight:700;color:#6b7280;');tb.addWidget(self.target);prices.addLayout(tb)
        div=QFrame();div.setFixedWidth(1);div.setStyleSheet(f'background:{LINE};');prices.addWidget(div)
        cb=QVBoxLayout();row=QHBoxLayout();row.addWidget(lbl('当前价格',f'font-size:15px;color:{ORANGE};font-weight:700;'))
        self.delta=lbl('',f'font-size:15px;font-weight:700;');row.addSpacing(30);row.addWidget(self.delta);row.addStretch();cb.addLayout(row)
        self.current=lbl('—',f'font-size:34px;font-weight:700;color:{ORANGE};');cb.addWidget(self.current);prices.addLayout(cb);prices.addStretch()
        cd=QGridLayout();cd.setHorizontalSpacing(22);cd.setVerticalSpacing(0)
        self.mm=lbl('--',f'font-size:38px;font-weight:600;color:{RED};');self.ss=lbl('--',f'font-size:38px;font-weight:600;color:{RED};')
        cd.addWidget(self.mm,0,0,Qt.AlignmentFlag.AlignHCenter);cd.addWidget(self.ss,0,1,Qt.AlignmentFlag.AlignHCenter)
        cd.addWidget(lbl('分钟',f'font-size:13px;color:{GRAY};'),1,0,Qt.AlignmentFlag.AlignHCenter);cd.addWidget(lbl('秒',f'font-size:13px;color:{GRAY};'),1,1,Qt.AlignmentFlag.AlignHCenter)
        prices.addLayout(cd);col.addLayout(prices)
        self.chart=PriceVsTarget();col.addWidget(self.chart,1)
        pills=QHBoxLayout();pills.setSpacing(12)
        past=QFrame();past.setStyleSheet(f'QFrame{{background:{SOFT};border-radius:22px;}}');pl=QHBoxLayout(past);pl.setContentsMargins(16,6,10,6);pl.setSpacing(8)
        pl.addWidget(lbl('过去',f'font-size:17px;font-weight:600;'));self.dots=[]
        for _ in range(3):
            d=lbl('',f'font-size:14px;');d.setFixedSize(28,28);d.setAlignment(Qt.AlignmentFlag.AlignCenter);pl.addWidget(d);self.dots.append(d)
        pills.addWidget(past);self.round_pills=[]
        for k in range(4):
            b=Pill('—','dark' if k==0 else 'light');b.setEnabled(k==0);pills.addWidget(b);self.round_pills.append(b)
        pills.addStretch();col.addLayout(pills)
        ob=QFrame();ob.setStyleSheet(f'QFrame{{background:{WHITE};border:1px solid {LINE};border-radius:14px;}}')
        ol=QVBoxLayout(ob);ol.setContentsMargins(22,14,22,14)
        hdr=QHBoxLayout();hdr.addWidget(lbl('订单簿',f'font-size:19px;font-weight:700;border:0;'));hdr.addStretch()
        self.book_toggle=QPushButton('展开 ⌄');self.book_toggle.setStyleSheet(f'QPushButton{{background:transparent;border:0;color:{GRAY};font-size:15px;}}')
        self.book_toggle.clicked.connect(self.toggle_book);hdr.addWidget(self.book_toggle);ol.addLayout(hdr)
        self.book_body=QWidget();self.book_grid=QGridLayout(self.book_body);self.book_grid.setVerticalSpacing(2);ol.addWidget(self.book_body);self.book_body.hide()
        col.addWidget(ob)
        return col
    def build_trade_card(self):
        card=QFrame();card.setFixedWidth(470)
        card.setStyleSheet(f'QFrame#tradeCard{{background:{WHITE};border:1px solid {LINE};border-radius:18px;}}');card.setObjectName('tradeCard')
        v=QVBoxLayout(card);v.setContentsMargins(24,22,24,22);v.setSpacing(16)
        h=QHBoxLayout();h.setSpacing(14);h.addWidget(BtcIcon(64))
        t=QVBoxLayout();t.setSpacing(2);t.addWidget(lbl('BTC 5分钟上涨或下跌',f'font-size:17px;color:#4b5563;'))
        self.card_side=lbl('Up',f'font-size:21px;font-weight:700;color:{GREEN};');t.addWidget(self.card_side);h.addLayout(t);h.addStretch();v.addLayout(h)
        tabs=QHBoxLayout();self.buy_tab=QPushButton('买入');self.sell_tab=QPushButton('卖出')
        for b,name in ((self.buy_tab,'buy'),(self.sell_tab,'sell')):
            b.setCursor(Qt.CursorShape.PointingHandCursor);b.clicked.connect(lambda _=False,n=name:self.set_tab(n));tabs.addWidget(b)
        tabs.addStretch();tabs.addWidget(lbl('1-Tap',f'font-size:16px;color:{INK};'));v.addLayout(tabs)
        sides=QHBoxLayout();sides.setSpacing(14);self.up_btn=QPushButton('Up —');self.down_btn=QPushButton('Down —')
        for b,name in ((self.up_btn,'Up'),(self.down_btn,'Down')):
            b.setMinimumHeight(64);b.setCursor(Qt.CursorShape.PointingHandCursor);b.clicked.connect(lambda _=False,n=name:self.set_side(n));sides.addWidget(b)
        v.addLayout(sides)
        oh=QHBoxLayout();ot=QVBoxLayout();ot.setSpacing(2);ot.addWidget(lbl('One-tap buy',f'font-size:19px;font-weight:600;'))
        self.cash_card=lbl('— 现金',f'font-size:15px;color:#4b5563;');ot.addWidget(self.cash_card);oh.addLayout(ot);oh.addStretch()
        edit=QPushButton('✎ Edit');edit.setStyleSheet(f'QPushButton{{background:transparent;border:0;color:#4b5563;font-size:15px;}}');edit.clicked.connect(self.edit_amounts)
        oh.addWidget(edit,0,Qt.AlignmentFlag.AlignBottom);v.addLayout(oh)
        amounts=QHBoxLayout();amounts.setSpacing(12);self.amount_btns=[]
        for i in range(3):
            b=AmountButton();b.setMinimumHeight(98);b.setCursor(Qt.CursorShape.PointingHandCursor);b.clicked.connect(lambda _=False,k=i:self.tap(k))
            b.setStyleSheet(f'QPushButton{{background:{WHITE};border:1px solid {LINE};border-radius:16px;font-size:24px;font-weight:700;color:{INK};}}'
                            f'QPushButton:hover{{border-color:#c9ced6;background:#fafbfc;}}QPushButton:disabled{{color:#b4bac3;}}')
            amounts.addWidget(b);self.amount_btns.append(b)
        v.addLayout(amounts)
        self.position=QFrame();self.position.setStyleSheet(f'QFrame{{background:{SOFT};border-radius:16px;}}')
        pl=QHBoxLayout(self.position);pl.setContentsMargins(16,12,12,12)
        self.pos_text=lbl('',f'font-size:15px;');pl.addWidget(self.pos_text,1)
        self.cash_out=QPushButton('Cash out');self.cash_out.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cash_out.setStyleSheet(f'QPushButton{{background:#1f2328;color:{WHITE};border:0;border-radius:14px;padding:12px 20px;font-size:17px;font-weight:700;}}QPushButton:disabled{{background:#9aa1ab;}}')
        self.cash_out.clicked.connect(self.cash_out_clicked);pl.addWidget(self.cash_out);v.addWidget(self.position);self.position.hide()
        self.arm=QPushButton('首次启用真钱交易（本机核对本金与资格）');self.arm.setCursor(Qt.CursorShape.PointingHandCursor)
        self.arm.setStyleSheet(f'QPushButton{{background:{GREEN};color:{WHITE};border:0;border-radius:12px;padding:12px;font-size:16px;font-weight:700;}}')
        self.arm.clicked.connect(self.arm_live);v.addWidget(self.arm)
        self.note=lbl('',f'font-size:14px;color:{GRAY};');self.note.setWordWrap(True);v.addWidget(self.note)
        self.set_side('Up');self.set_tab('buy')
        return card

    # ---------- interaction ----------
    def amounts(self):
        raw=self.settings.data.get('quick_amounts') or ['5','1','100']
        return [dec(a) or D(1) for a in raw][:3]
    def set_side(self,side):
        self.side=side;self.card_side.setText(side);self.card_side.setStyleSheet(f'font-size:21px;font-weight:700;color:{GREEN if side=="Up" else RED};')
        self.refresh_side_buttons();self.refresh_amounts()
    def set_tab(self,tab):
        self.tab=tab
        for b,name in ((self.buy_tab,'buy'),(self.sell_tab,'sell')):
            on=name==tab;b.setStyleSheet(f'QPushButton{{background:transparent;border:0;border-bottom:{3 if on else 0}px solid {INK};padding:6px 4px;font-size:19px;font-weight:{700 if on else 500};color:{INK if on else GRAY};}}')
        for b in self.amount_btns:b.setVisible(tab=='buy')
        self.refresh_position()
    def refresh_side_buttons(self):
        q=(self.state or {}).get('sides',{})
        for b,name,color in ((self.up_btn,'Up',GREEN),(self.down_btn,'Down',RED)):
            ask=dec(q.get(name,{}).get('ask'));cents='—' if ask is None else f'{ask*100:.0f}¢' if ask*100%1==0 else f'{ask*100:.1f}¢'
            b.setText(f'{name} {cents}');on=name==self.side
            b.setStyleSheet(f'QPushButton{{background:{color if on else WHITE};color:{WHITE if on else "#9aa1ab"};border:{0 if on else 1}px solid {LINE};'
                            f'border-radius:12px;font-size:22px;font-weight:600;}}')
    def win(self,amount):
        q=(self.state or {}).get('sides',{}).get(self.side,{});ask=dec(q.get('ask'))
        if ask is None or not D(0)<ask<D(1):return None
        rate=dec((self.state or {}).get('fee_rate')) or D(0);exp=(self.state or {}).get('fee_exponent') or 1
        fee=rate*(ask*(1-ask))**exp
        return amount/(ask+fee)
    def refresh_amounts(self):
        armed=bool((self.state or {}).get('armed'))
        for b,a in zip(self.amount_btns,self.amounts()):
            b.show_amount(a,self.win(a))
            b.setEnabled(armed and not (self.state or {}).get('pending'))
        self.arm.setVisible(not armed and not (self.state or {}).get('attested'))
    def refresh_position(self):
        s=self.state or {};pos=s.get('position')
        if not pos or (dec(pos.get('shares')) or 0)<=0:self.position.hide();return
        shares=dec(pos['shares']);basis=dec(pos['basis']) or D(0)
        bid=dec(s.get('sides',{}).get(pos.get('outcome'),{}).get('bid')) if pos.get('market_id')==s.get('market_id') else None
        value=shares*bid if bid is not None else None;pnl=value-basis if value is not None else None
        arrow='▲' if pos.get('outcome')=='Up' else '▼'
        pnl_text='' if pnl is None else f"  <span style='color:{GREEN if pnl>=0 else RED}'>{'+' if pnl>=0 else '-'}US${abs(pnl):.2f}</span>"
        self.pos_text.setText(f"<span style='color:{GREEN if arrow=='▲' else RED};font-size:18px'>{arrow}</span>  "
                              f"<span style='color:{GRAY};font-size:12px'>{shares:.2f} SHARES · {pos.get('outcome')}</span><br>"
                              f"<b style='font-size:19px'>{usd(value) if value is not None else '等待结算'}</b>{pnl_text}")
        self.cash_out.setEnabled(bool(s.get('armed')) and value is not None and not s.get('pending'));self.position.show()
    def tap(self,k):
        amount=self.amounts()[k]
        self.note.setText(f'正在买入 {self.side} ${amount:,.0f}…（全部成交或取消）')
        self.send('live_manual_buy',outcome=self.side,amount=str(amount))
    def cash_out_clicked(self):
        self.note.setText('正在卖出全部持仓…（全部成交或取消）');self.send('live_manual_sell')
    def edit_amounts(self):
        d=QDialog(self);d.setWindowTitle('One-tap 金额');f=QFormLayout(d);edits=[]
        for i,a in enumerate(self.amounts()):
            e=QLineEdit(str(a));f.addRow(f'按钮 {i+1}（美元）',e);edits.append(e)
        bb=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);f.addRow(bb)
        bb.rejected.connect(d.reject)
        def ok():
            vals=[dec(e.text()) for e in edits]
            if any(v is None or not D(1)<=v<=D(10000) for v in vals):self.note.setText('金额需在 $1–$10,000 之间');return
            self.settings.data['quick_amounts']=[str(v) for v in vals];self.settings.save();self.refresh_amounts();d.accept()
        bb.accepted.connect(ok);d.exec()
    def toggle_book(self):
        self.book_open=not self.book_open;self.book_body.setVisible(self.book_open);self.book_toggle.setText('收起 ⌃' if self.book_open else '展开 ⌄');self.refresh_book()
    def refresh_book(self):
        if not self.book_open:return
        while self.book_grid.count():
            w=self.book_grid.takeAt(0).widget()
            if w:w.deleteLater()
        q=(self.state or {}).get('sides',{}).get(self.side,{})
        self.book_grid.addWidget(lbl(f'交易 {self.side}',f'font-size:15px;font-weight:700;border:0;'),0,0)
        for j,h in enumerate(('价格','份额','总额')):self.book_grid.addWidget(lbl(h,f'color:{GRAY};border:0;'),1,j)
        row=2
        for p,sz in reversed(q.get('asks',[])[:6]):
            pr,s=dec(p),dec(sz);cells=(f'{pr*100:.1f}¢',f'{s:,.2f}',usd(pr*s))
            for j,c in enumerate(cells):self.book_grid.addWidget(lbl(c,f'color:{RED if j==0 else INK};border:0;'),row,j)
            row+=1
        self.book_grid.addWidget(lbl('—— 价差 ——',f'color:{GRAY};border:0;'),row,0,1,3);row+=1
        for p,sz in q.get('bids',[])[:6]:
            pr,s=dec(p),dec(sz);cells=(f'{pr*100:.1f}¢',f'{s:,.2f}',usd(pr*s))
            for j,c in enumerate(cells):self.book_grid.addWidget(lbl(c,f'color:{GREEN if j==0 else INK};border:0;'),row,j)
            row+=1

    # ---------- results from the worker ----------
    def show_result(self,action,r):
        if action=='live_manual_buy':self.note.setText(f"已提交：买入 {r.get('outcome')} 花费 ${r.get('spend')}（最高 {r.get('max_price')}）· 等待成交回执")
        elif action=='live_manual_sell':self.note.setText(f"已提交：卖出 {r.get('shares')} 份（最低 {r.get('min_price')}）· 等待成交回执")
    def show_error(self,text):
        self.note.setText('未执行：'+text)

    def update_state(self,full):
        s=full.get('quick') or {};self.state=s
        if not s:return
        start,end,now=s['start'],s['end'],s.get('server_ms') or 0
        a,b=et_label(start),et_label(end);self.when.setText(f'{MONTHS[a.month-1]} {a.day}, {clock_text(a)}-{clock_text(b)} ET')
        target=dec(s.get('target'));price=s.get('price')
        self.target.setText(usd(target) if target is not None else '—')
        self.current.setText(usd(price) if price is not None else '—')
        if target is not None and price is not None:
            diff=D(str(price))-target;self.delta.setText(f"{'▲' if diff>=0 else '▼'}${abs(diff):,.0f}")
            self.delta.setStyleSheet(f'font-size:15px;font-weight:700;color:{GREEN if diff>=0 else RED};')
        left=max(0,end-now//1000);self.mm.setText(f'{left//60:02}');self.ss.setText(f'{left%60:02}')
        self.chart.set(s.get('series'),target)
        rounds=s.get('rounds') or []
        for d,r in zip(self.dots,(rounds[-3:]+[None]*3)[:3]):
            o=(r or {}).get('outcome')
            d.setText('▲' if o=='Up' else '▼' if o=='Down' else '·')
            d.setStyleSheet(f"border-radius:14px;color:{WHITE};font-size:13px;background:{GREEN if o=='Up' else RED if o=='Down' else '#c9ced6'};")
        for k,pill in enumerate(self.round_pills):
            t=et_label(start+300*k);pill.setText(('● ' if k==0 else '')+clock_text(t))
        cash=dec(s.get('cash'));pos=s.get('position') or {}
        bid=dec(s.get('sides',{}).get(pos.get('outcome'),{}).get('bid')) if pos and pos.get('market_id')==s.get('market_id') else None
        value=(dec(pos.get('shares')) or D(0))*bid if pos and bid is not None else D(0)
        self.cash_top.setText(usd(cash));self.portfolio.setText(usd(cash+value) if cash is not None else '—')
        self.cash_card.setText(f'{usd(cash)} 现金')
        if s.get('pending'):self.note.setText('订单核对中…（以链上确认的成交回执为准）')
        elif not s.get('armed'):self.note.setText(s.get('live_message') or '正在自动启用手动交易…')
        self.refresh_side_buttons();self.refresh_amounts();self.refresh_position();self.refresh_book()


class ModeDialog(QDialog):
    """The only place the UI mode can change (also mirrored in the terminal's Settings page)."""
    def __init__(self,parent,current):
        super().__init__(parent);self.setWindowTitle('设置 · 界面模式');f=QFormLayout(self)
        self.combo=QComboBox();self.combo.addItem('快捷交易（网页风格，默认）','quick');self.combo.addItem('专业终端','terminal')
        self.combo.setCurrentIndex(0 if current=='quick' else 1);f.addRow('界面模式',self.combo)
        f.addRow(QLabel('专业终端包含账户、模型、日志、AI 副驾驶与全部设置。'))
        bb=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);f.addRow(bb)
        bb.accepted.connect(self.accept);bb.rejected.connect(self.reject)
    def mode(self):return self.combo.currentData()
