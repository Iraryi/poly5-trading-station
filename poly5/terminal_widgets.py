from __future__ import annotations
from PySide6.QtCore import Qt, QRectF, QPointF, QSize, QByteArray, QBuffer, QIODevice, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QIcon, QFont, QLinearGradient
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSplitter, QSplitterHandle
from pathlib import Path
import struct

UP='#42d6b0';DOWN='#ee8f68';MUTED='#75859b';LINE='#1c2835';PANEL='#101823'

def menu_icon():
    pix=QPixmap(40,40);pix.fill(Qt.GlobalColor.transparent)
    p=QPainter(pix);p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen=QPen(QColor('#9cacbf'),2.5);pen.setCapStyle(Qt.PenCapStyle.RoundCap);p.setPen(pen)
    for y in (12,20,28):p.drawLine(10,y,30,y)
    p.end();return QIcon(pix)

class Divider(QSplitterHandle):
    def __init__(self,orientation,parent):
        super().__init__(orientation,parent);self.hovered=False;self.setMouseTracking(True)
    def enterEvent(self,event):self.hovered=True;self.update();super().enterEvent(event)
    def leaveEvent(self,event):self.hovered=False;self.update();super().leaveEvent(event)
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # One restrained shared separator. The hit target is wider than the line.
        p.setPen(QPen(QColor('#477165' if self.hovered else '#1b2734'),1))
        if self.orientation()==Qt.Orientation.Horizontal:
            x=self.width()/2;p.drawLine(QPointF(x,0),QPointF(x,self.height()))
        else:
            y=self.height()/2;p.drawLine(QPointF(0,y),QPointF(self.width(),y))

class TerminalSplitter(QSplitter):
    resized=Signal()
    def __init__(self,orientation,parent=None):
        super().__init__(orientation,parent);self.setHandleWidth(7)
    def createHandle(self):return Divider(self.orientation(),self)
    def resizeEvent(self,event):super().resizeEvent(event);self.resized.emit()

class QuoteLabel(QLabel):
    """Fit numeric quotes without imposing a large minimum on the dock layout."""
    def __init__(self,text):
        super().__init__(text);self.setMinimumWidth(0);self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(25)
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.TextAntialiasing);font=QFont('Consolas');font.setBold(True);font.setPixelSize(22)
        from PySide6.QtGui import QFontMetrics
        width=QFontMetrics(font).horizontalAdvance(self.text())
        font.setPixelSize(max(11,min(22,int(22*self.width()/max(1,width)))))
        p.setFont(font);p.setPen(self.palette().color(self.foregroundRole()));p.drawText(self.rect(),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,self.text())

def brand_pixmap(size):
    pix=QPixmap(size,size);pix.fill(Qt.GlobalColor.transparent)
    p=QPainter(pix);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.scale(size/64,size/64)
    g=QLinearGradient(0,0,64,64);g.setColorAt(0,QColor('#172b40'));g.setColorAt(1,QColor('#08121e'))
    p.setBrush(g);p.setPen(QPen(QColor('#33516a'),1));p.drawRoundedRect(QRectF(2,2,60,60),14,14)
    path=QPainterPath();path.moveTo(17,47);path.lineTo(17,17);path.lineTo(35,17);path.cubicTo(52,17,52,38,35,38);path.lineTo(26,38);path.lineTo(26,47);path.closeSubpath()
    hole=QPainterPath();hole.addRoundedRect(QRectF(26,25,10,6),2,2)
    path=path.subtracted(hole);p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(UP));p.drawPath(path)
    p.setBrush(QColor('#f0a575'));p.drawRoundedRect(QRectF(34,43,5,8),1,1);p.drawRoundedRect(QRectF(42,38,5,13),1,1)
    p.end();return pix

def brand_icon():
    icon=QIcon()
    for size in (16,24,32,48,64,128,256):icon.addPixmap(brand_pixmap(size))
    return icon

def write_icon(path):
    images=[]
    for size in (16,24,32,48,64,128,256):
        data=QByteArray();buffer=QBuffer(data);buffer.open(QIODevice.OpenModeFlag.WriteOnly);brand_pixmap(size).save(buffer,'PNG');images.append((size,bytes(data)))
    offset=6+16*len(images);header=struct.pack('<HHH',0,1,len(images));directory=b'';body=b''
    for size,data in images:
        directory+=struct.pack('<BBBBHHII',0 if size==256 else size,0 if size==256 else size,0,0,1,32,len(data),offset)
        body+=data;offset+=len(data)
    Path(path).write_bytes(header+directory+body)

def pane(title,extra=None):
    frame=QFrame();frame.setObjectName('terminalPane');layout=QVBoxLayout(frame);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0)
    header=QWidget();header.setObjectName('paneHeader');header.setFixedHeight(32);bar=QHBoxLayout(header);bar.setContentsMargins(12,0,10,0)
    text=QLabel(title);text.setStyleSheet('font-size:11px;font-weight:600;letter-spacing:.5px;color:#b0bdce;');bar.addWidget(text);bar.addStretch()
    if extra:bar.addWidget(extra)
    layout.addWidget(header)
    body=QWidget();content=QVBoxLayout(body);content.setContentsMargins(12,8,12,10);content.setSpacing(6);layout.addWidget(body,1)
    return frame,content

class DepthLadder(QWidget):
    def __init__(self):
        super().__init__();self.book={};self.setMinimumSize(200,175);self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
        self.setToolTip('限价委托簿 · 左侧买量 / 中间价格 / 右侧卖量；色条按当前可见档位数量归一化')
    def set_book(self,book):self.book=book;self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        width=self.width();height=self.height();mid=width/2;pricewidth=64;left=mid-pricewidth/2;right=mid+pricewidth/2
        p.fillRect(QRectF(left,0,pricewidth,height),QColor('#131f2c'))
        p.setFont(QFont('Microsoft YaHei UI',8));p.setPen(QColor(MUTED))
        p.drawText(QRectF(4,0,left-8,22),Qt.AlignmentFlag.AlignCenter,'买量');p.drawText(QRectF(left,0,pricewidth,22),Qt.AlignmentFlag.AlignCenter,'价格')
        p.drawText(QRectF(right+4,0,width-right-8,22),Qt.AlignmentFlag.AlignCenter,'卖量')
        p.setPen(QColor(LINE));p.drawLine(0,24,width,24)
        count=max(3,min(7,int((height-54)/38)));asks=self.book.get('asks',[])[:count];bids=self.book.get('bids',[])[:count]
        values=[float(q) for _,q in asks+bids];maximum=max(values,default=1) or 1
        rowheight=min(24,(height-55)/(count*2));y=28
        p.setFont(QFont('Consolas',10))
        for rows,is_ask in ((list(reversed(asks)),True),(bids,False)):
            padded=[None]*(count-len(rows))+rows if is_ask else rows+[None]*(count-len(rows))
            for row in padded:
                if row:
                    price,q=row;qty=float(q);color=QColor(DOWN if is_ask else UP);fill=QColor(color);fill.setAlpha(42)
                    if is_ask:p.fillRect(QRectF(right,y,(width-right-4)*qty/maximum,rowheight-1),fill)
                    else:p.fillRect(QRectF(left-(left-4)*qty/maximum,y,(left-4)*qty/maximum,rowheight-1),fill)
                    p.setPen(color);p.drawText(QRectF(left,y,pricewidth,rowheight),Qt.AlignmentFlag.AlignCenter,f'{float(price):.3f}')
                    p.setPen(QColor('#adbacd'));rect=QRectF(right+5,y,width-right-10,rowheight) if is_ask else QRectF(4,y,left-10,rowheight)
                    p.drawText(rect,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,f'{qty:,.1f}')
                else:
                    p.setPen(QColor('#3c4a5d'));p.drawText(QRectF(left,y,pricewidth,rowheight),Qt.AlignmentFlag.AlignCenter,'—')
                y+=rowheight
            if is_ask:
                spread=self.book.get('spread');p.fillRect(QRectF(0,y,width,25),QColor('#1c2838'));p.setPen(QColor(MUTED));p.setFont(QFont('Consolas',9))
                p.drawText(QRectF(0,y,width,25),Qt.AlignmentFlag.AlignCenter,'SPREAD  '+(f'{float(spread):.3f}' if spread is not None else '—'))
                p.setFont(QFont('Consolas',10));y+=27
        if not self.book:
            p.setFont(QFont('Microsoft YaHei UI',9));p.setPen(QColor(MUTED));p.drawText(QRectF(0,height-25,width,20),Qt.AlignmentFlag.AlignCenter,'等待盘口')
