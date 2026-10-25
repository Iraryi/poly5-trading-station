from pathlib import Path
p=Path(__file__).with_name('interactive_mock.html')
s=p.read_text(encoding='utf8')
s=s.replace('Poly5 BTC • Windows UI 参考样机（离线模拟）','Poly5 Trading Station v3 • Windows UI 离线交互样机')
s=s.replace('.demo{position:absolute;', '''
.account-ribbon{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:7px;margin-bottom:12px}
.a-metric{min-width:0;padding:11px 9px;border:1px solid #33465d;border-radius:10px;background:#17253b}
.a-metric b{display:block;font-size:17px;margin-top:7px;color:#e5f2ff}
.a-metric .lbl{font-size:10px}
.a-metric small{display:block;color:#8798b1;margin-top:4px;font-size:10px}
.interrupt-banner{display:flex;justify-content:space-between;align-items:center;gap:12px;border:1px solid #374d65;border-radius:10px;background:#182942;padding:12px 13px;margin-bottom:15px}
.tablist{display:flex;gap:6px;flex-wrap:wrap;margin:9px 0 13px}
.tablist button{background:#1e3049;border:1px solid #435672;color:#cedcf0;padding:7px 12px;border-radius:8px}
.tablist button.active{color:#162033;background:#a4c4ec}
.info-table{border-collapse:collapse;width:100%;font-size:12px;table-layout:fixed}
.info-table th,.info-table td{border-bottom:1px solid #2e3b50;padding:12px 8px;text-align:left;vertical-align:top;overflow-wrap:anywhere}
.info-table th{color:#90a4c1;font-size:11px;font-weight:600}
.empty{border:1px dashed #46607c;border-radius:10px;padding:18px;color:#a9bedb;text-align:center;background:#17253a;font-size:12px}
.mode-pill{background:#3a3449;color:#dcc8fa;border-radius:8px;padding:4px 8px;font-size:10px}
.wide-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.wide-actions button{padding:8px 11px}
button[disabled]{opacity:.44;cursor:not-allowed}
.moduleitem{display:flex;justify-content:space-between;align-items:center;gap:12px;border:1px solid #33455d;padding:13px;border-radius:11px;margin-top:9px;background:#182638}
.demo{position:absolute;''')
s=s.replace('@media(max-width:1180px){.sidebar', '@media(max-width:1180px){.account-ribbon{grid-template-columns:repeat(3,minmax(0,1fr))}.sidebar')
s=s.replace('@media(max-width:780px){.sidebar','@media(max-width:780px){.account-ribbon{grid-template-columns:repeat(2,minmax(0,1fr))}.sidebar')
s=s.replace('<div class="brand"><i>5</i>Poly5 BTC</div>','<div class="brand"><i>5</i>Poly5 Station</div>')
s=s.replace(' <button class="navbtn" data-page="market">◈　市场</button>', ''' <button class="navbtn" data-page="market">◈　交易台</button>
 <button class="navbtn" data-page="account">▣　账户中心</button>
 <button class="navbtn" data-page="orders">▤　持仓订单</button>''')
s=s.replace(' <button class="navbtn" data-page="logs">▤　Log</button>', ' <button class="navbtn" data-page="logs">▤　Log</button>\n <button class="navbtn" data-page="modules">▦　扩展模块</button>')
s=s.replace('<div class="head-title">BTC 五分钟 · Up / Down</div><div class="sub">主窗口与右侧 AI 一体化工作台', '<div class="head-title">Poly5 Trading Station <span class="tag">BTC · 5m</span></div><div class="sub">账户资产 / 交易台 / 模块化策略 / 右侧 AI')
s=s.replace('<span class="pill muted">DEMO / 仅监控</span>', '<span class="pill muted">未绑定账户 · DEMO</span>')
anchor='<div class="alert" id="alerts">演示模式：网络和 Polymarket 均未连接。请不要依据此界面交易。</div>'
add='''<div class="account-ribbon" aria-label="账户摘要（当前未连接）">
    <div class="a-metric"><span class="lbl">现金余额</span><b>—</b><small>未连接账户</small></div>
    <div class="a-metric"><span class="lbl">可用买入资金</span><b>—</b><small>需核实可用额度</small></div>
    <div class="a-metric"><span class="lbl">挂单占用</span><b>—</b><small>等待认证订单</small></div>
    <div class="a-metric"><span class="lbl">持仓市值</span><b>—</b><small>尚未绑定钱包</small></div>
    <div class="a-metric"><span class="lbl">今日净盈亏</span><b>—</b><small>缺少真实账本</small></div>
   </div>
   <div class="interrupt-banner"><div><b>当前买入 / 卖出进度：</b> <span class="warn">暂无账户订单数据</span><div class="sub">信号不是下单，提交不是成交 · 当前实盘锁定</div></div><button class="ghost jump" data-target="orders">查看订单 →</button></div>'''
s=s.replace(anchor, anchor+'\n   '+add)
market_old='<div id="page-market" class="hidden-page"><div class="section-head"><h2>市场深度与时间窗</h2></div><div class="card">盘口原始事件、Up/Down 深度、订单簿时间戳和费用将在接通官方接口后显示。当前是纯离线 UI 样机。</div></div>'
market_new='''<div id="page-market" class="hidden-page">
   <div class="section-head"><div><h2>BTC 5m 交易台</h2><div class="sub">未来也可通过市场模块切换其它品种、时间周期</div></div><span class="mode-pill">当前实盘交易未启用</span></div>
   <div class="row"><section class="card"><div class="card-title">双向盘口与机会计算 <span class="tag">无实时数据</span></div><div class="bidask"><div class="marketbox"><span class="teal">↑ BTC UP</span><div>— / —</div><small class="lbl">bid / ask / 深度待拉取</small></div><div class="marketbox red"><span class="red">↓ BTC DOWN</span><div>— / —</div><small class="lbl">bid / ask / 深度待拉取</small></div></div><p class="footnote">市场行情的中间价不代表可成交价格；完整 ROI 需要真实盘口及手续费。</p></section>
   <section class="card"><div class="card-title">快速交易票据 <span class="tag">先预览，后确认</span></div><div class="mini">选择方向：<b>未选择</b><br>每单预算上限：<b>$5.00（策略研究设置）</b><br>可用金额：<b>—（未核实）</b><br>手续费：<b>—</b><br>模拟/真钱：<b>只读监控</b></div><div class="wide-actions"><button class="action" disabled title="实盘尚未授权、未核实资金">↑ 买入 Up</button><button class="ghost" disabled>↓ 买入 Down</button><button class="ghost" disabled>卖出持仓</button></div><p class="footnote">这三个按钮都只是被禁用的占位样机，不会提交订单。真实程序先做预览、认证与风险校验。</p></section></div>
  </div>'''
if market_old not in s: raise SystemExit('Market section not found')
s=s.replace(market_old,market_new)
insert_point='  <div id="page-stats" class="hidden-page">'
account='''  <div id="page-account" class="hidden-page">
    <div class="section-head"><div><h2>账户中心</h2><div class="sub">同一个钱包的余额、仓位市值与交易权限必须区分</div></div><span class="mode-pill">未绑定 / 仅监控</span></div>
    <div class="alert">离线原型未接入钱包、Polymarket API 或个人数据；所有账户金额均为未知（—），不代表 0。</div>
    <section class="card"><div class="card-title">身份与连接 <span class="tag">读取权限：无</span></div><div class="mini">当前账号：<strong>尚未连接</strong><br>钱包类型：<strong>未知（Deposit / Proxy / Safe）</strong><br>钱包地址：<strong>—</strong><br>CLOB 用户订单 WS：<strong>未连接</strong><br>余额/授权来源：<strong>未校验</strong><br>数据刷新：<strong>—</strong></div><div class="wide-actions"><button class="ghost" id="link-wallet-demo">绑定公开钱包（演示说明）</button><button class="ghost" disabled>认证订单 API</button><button class="ghost" disabled>刷新账户</button></div></section>
    <div class="account-ribbon" style="margin-top:12px;grid-template-columns:repeat(3,minmax(0,1fr))"><div class="a-metric"><span class="lbl">现金/抵押资产</span><b>—</b><small>不是持仓市值</small></div><div class="a-metric"><span class="lbl">可动用购买力</span><b>—</b><small>非账面现金余额</small></div><div class="a-metric"><span class="lbl">未成交订单占用</span><b>—</b><small>需账本对账</small></div><div class="a-metric"><span class="lbl">持仓市值</span><b>—</b><small>Data API /v2/value</small></div><div class="a-metric"><span class="lbl">估计总权益</span><b>—</b><small>必须避免重复计入</small></div><div class="a-metric"><span class="lbl">已实现 / 浮动 PnL</span><b>— / —</b><small>两个口径分别统计</small></div></div>
    <section class="card"><div class="card-title">账户对账与权限 <span class="tag">待连接</span></div><div class="mini">公开持仓与持仓估值：可通过 Data API v2 获取（账户地址绑定后）<br>实时本人委托：需认证的用户订单 REST + WebSocket<br>可用买入余额：需钱包资产及挂单占用等校验<br>真钱下单权限：<b class="warn">禁用，需要明确授权且满足规则</b></div></section>
  </div>
  <div id="page-orders" class="hidden-page">
    <div class="section-head"><div><h2>持仓与订单</h2><div class="sub">覆盖当前 BTC 一局以及跨局遗留仓位、挂单和待领回资产</div></div><span class="mode-pill">尚无真实账户数据</span></div>
    <div class="tablist" role="tablist"><button class="active" data-subtab="active">进行中订单</button><button data-subtab="positions">当前持仓</button><button data-subtab="fills">成交记录</button><button data-subtab="redeem">待领回与历史</button></div>
    <section class="card" data-subpanel="active"><div class="card-title">订单状态时间线 <span class="tag">需认证账户</span></div><table class="info-table"><thead><tr><th>品种/方向</th><th>委托金额</th><th>已成交</th><th>状态</th><th>操作</th></tr></thead></table><div class="empty">暂无可核实的开放订单。<br>账户未绑定，不是已经确认“0 笔订单”。</div><p class="footnote">未来单笔将显示：意图 → 已提交 → 已接受 → 部分成交 → 全部成交 / 撤单。网络超时显示“结果待核对”，不能自动重下。</p></section>
    <section class="card hidden-page" data-subpanel="positions"><div class="card-title">持仓清单 <span class="tag">Data API v2</span></div><div class="empty">尚未绑定公开钱包地址。待显示份额、均价、持仓市值、可实现 ROI、结算状态。</div></section>
    <section class="card hidden-page" data-subpanel="fills"><div class="card-title">逐笔成交 <span class="tag">REST + WS 对账</span></div><div class="empty">暂未读取账户成交记录。未成交单不会记入已实现盈亏。</div></section>
    <section class="card hidden-page" data-subpanel="redeem"><div class="card-title">已结束 / 可领回 / 失败订单</div><div class="empty">尚未完成账户绑定，无法查看已结算持仓或待领回余额。</div></section>
    <div class="wide-actions"><button class="ghost" disabled>取消选中挂单</button><button class="ghost" disabled>全部撤单（需再次确认）</button><button class="ghost jump" data-target="account">前往账户中心 →</button></div>
  </div>
  <div id="page-modules" class="hidden-page">
    <div class="section-head"><div><h2>模块与扩展</h2><div class="sub">当前专注 BTC 5 分钟，其余接口先保留，将来逐步开发</div></div><span class="mode-pill">版本化插件架构</span></div>
    <div class="moduleitem"><div><b>BTC Up / Down · 5m</b><div class="sub">预装目标模板 · 双阶段 ROI / 末段研究策略</div></div><span class="tag">当前开发目标</span></div>
    <div class="moduleitem"><div><b>账户中心与订单投影</b><div class="sub">Polymarket Data API v2 + CLOB 用户订单 WS</div></div><span class="tag">v3 计划</span></div>
    <div class="moduleitem"><div><b>ETH / SOL / XRP · 5m</b><div class="sub">预留不同数据源、时间窗口、结算规则接口</div></div><span class="tag">未来 · 未实现</span></div>
    <div class="moduleitem"><div><b>BTC 15m / 1h / 普通事件市场</b><div class="sub">将来采用独立 MarketTemplate / SettlementRule</div></div><span class="tag">未来 · 未实现</span></div>
    <div class="moduleitem"><div><b>多策略 / 自定义 AI 工具 / 图表与通知</b><div class="sub">策略与执行隔离，AI 没有下单权限</div></div><span class="tag">未来 · 未实现</span></div>
    <p class="footnote">本原型的模块条目仅用于验证界面可扩展，不加载外部代码、不联网，也不具备实盘交易能力。</p>
  </div>
'''
s=s.replace(insert_point, account+insert_point)
s=s.replace('真实程序需要 Settings → AI / Models','此版本新增账户绑定 / API 凭据授权 / 钱包类型 / 市场模板与扩展配置。真实程序需要 Settings → AI / Models')
# handler for summary buttons and tabbed subpanels
needle="document.getElementById('send').addEventListener('click'"
extra="""document.querySelectorAll('.jump').forEach(el=>el.addEventListener('click',()=>{const target=el.dataset.target;document.querySelectorAll('.navbtn').forEach(x=>x.classList.toggle('active',x.dataset.page===target));document.querySelectorAll('[id^=page-]').forEach(x=>x.classList.add('hidden-page'));document.getElementById('page-'+target).classList.remove('hidden-page')}));
document.querySelectorAll('[data-subtab]').forEach(el=>el.addEventListener('click',()=>{document.querySelectorAll('[data-subtab]').forEach(x=>x.classList.toggle('active',x===el));document.querySelectorAll('[data-subpanel]').forEach(x=>x.classList.toggle('hidden-page',x.dataset.subpanel!==el.dataset.subtab))}));
document.getElementById('link-wallet-demo').addEventListener('click',()=>alert('离线 UI 样机不读取钱包或 API。最终 EXE 将允许输入您自己的公开钱包地址，实现只读查询，无需私钥。'));
"""
s=s.replace(needle, extra+needle)
s=s.replace('<span class="demo">MOCK UI / NO LIVE DATA</span>','<span class="demo">v3 UI MOCK / ACCOUNT NOT CONNECTED / NO LIVE DATA</span>')
p.write_text(s,encoding='utf8')
