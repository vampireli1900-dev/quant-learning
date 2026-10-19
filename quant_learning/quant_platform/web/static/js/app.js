/* ==========================================================================
   QuantLab Web Terminal —— 前端逻辑
   K 线用 TradingView 官方开源库 Lightweight Charts（本地已 vendor，离线可用）
   ========================================================================== */

const TV = {
  bg: '#1e222d', grid: '#242938', border: '#2a2e39',
  text: '#9ca3b0', up: '#f23645', down: '#089981',   // 中国习惯：涨红跌绿
  accent: '#2962ff',
};

const state = {
  symbol: 'SPY',
  strategy: 'ADX趋势过滤',
  duration: '5 Y',
  view: 'chart',
  ohlc: null,
  signals: null,
  overlays: { ma: true, bb: false, sig: true, vol: true },
  charts: {},
};

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

/* ---------------- 工具 ---------------- */
function fmt(v, d = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return Number(v).toFixed(d);
}
function pct(v, d = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return (Number(v) * 100).toFixed(d) + '%';
}
function cls(v) { return Number(v) > 0 ? 'up' : (Number(v) < 0 ? 'down' : ''); }
function colorOf(v) { return Number(v) > 0 ? TV.up : (Number(v) < 0 ? TV.down : TV.text); }

let barTimer = null;
function loading(on) {
  const b = $('#loadingBar');
  if (on) {
    b.classList.add('on'); b.style.width = '35%';
    clearTimeout(barTimer);
    barTimer = setTimeout(() => { b.style.width = '70%'; }, 220);
  } else {
    b.style.width = '100%';
    barTimer = setTimeout(() => { b.classList.remove('on'); b.style.width = '0'; }, 320);
  }
}
let toastTimer = null;
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2400);
}

async function api(path) {
  loading(true);
  try {
    const r = await fetch(path);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  } catch (e) {
    toast('请求失败：' + e.message);
    throw e;
  } finally { loading(false); }
}

/* 数字滚动动画 */
function countUp(el, target, d = 2, suffix = '') {
  const start = parseFloat(String(el.textContent).replace(/[^\d.-]/g, '')) || 0;
  const t0 = performance.now(), dur = 620;
  function step(now) {
    const p = Math.min(1, (now - t0) / dur);
    const e = 1 - Math.pow(1 - p, 3);
    el.textContent = (start + (target - start) * e).toFixed(d) + suffix;
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

/* ---------------- 初始化 ---------------- */
async function boot() {
  const meta = await api('/api/bootstrap');
  state.meta = meta;

  // 标的 chips
  const chips = $('#symbolChips');
  chips.innerHTML = meta.symbols.map(s =>
    `<button class="chip${s === state.symbol ? ' active' : ''}" data-sym="${s}">${s}</button>`).join('');
  chips.querySelectorAll('.chip').forEach(c => c.onclick = () => setSymbol(c.dataset.sym));
  $('#symbolInput').addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.target.value.trim()) setSymbol(e.target.value.trim().toUpperCase());
  });

  // 策略下拉
  const sel = $('#strategySel');
  sel.innerHTML = meta.strategy_names.map(n =>
    `<option value="${n}"${n === state.strategy ? ' selected' : ''}>${n}</option>`).join('');
  sel.onchange = () => { state.strategy = sel.value; loadSignals(); syncStrategyList(); };
  $('#durationSel').onchange = (e) => { state.duration = e.target.value; reloadAll(); };

  // 左侧策略列表（带类别）
  const list = $('#strategyList');
  list.innerHTML = meta.strategies.map(s =>
    `<div class="strat-item${s['策略'] === state.strategy ? ' active' : ''}" data-name="${s['策略']}">
       <span>${s['策略']}</span><span class="strat-cat">${s['类别']}</span></div>`).join('');
  list.querySelectorAll('.strat-item').forEach(it => it.onclick = () => {
    state.strategy = it.dataset.name; sel.value = it.dataset.name;
    loadSignals(); syncStrategyList();
  });

  // 净值页标的
  const eqs = $('#eqSymbolSel');
  eqs.innerHTML = meta.symbols.map(s =>
    `<option value="${s}"${s === state.symbol ? ' selected' : ''}>${s}</option>`).join('');
  eqs.onchange = () => loadEquityCurves(eqs.value);

  bindNav(); bindOverlays();
  await reloadAll();
  loadRanking(); loadRiskViews(); loadWatchlist(); loadAccount(); loadPaper();

  $('#boot').classList.add('hide');
  setTimeout(() => { $('#boot').remove(); }, 600);
  $('#app').hidden = false;
}

function syncStrategyList() {
  $$('.strat-item').forEach(it => it.classList.toggle('active', it.dataset.name === state.strategy));
}

function bindNav() {
  $$('.nav-item').forEach(b => b.onclick = () => {
    $$('.nav-item').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    state.view = b.dataset.view;
    $$('.view').forEach(v => v.classList.remove('active'));
    $('#view-' + state.view).classList.add('active');
    if (state.view === 'equity') loadEquityCurves($('#eqSymbolSel').value);
    requestAnimationFrame(() => resizeCharts());
  });
}

function bindOverlays() {
  $$('#overlayToggle .sw').forEach(b => b.onclick = () => {
    b.classList.toggle('active');
    state.overlays[b.dataset.ov] = b.classList.contains('active');
    renderPriceChart();
  });
  $('#watchRefresh').onclick = () => loadWatchlist(true);
  $('#eqRefresh').onclick = () => loadEquityCurves($('#eqSymbolSel').value);
  $('#rankMetric').onchange = () => loadRanking();
  window.addEventListener('resize', () => resizeCharts());
}

function setSymbol(s) {
  state.symbol = s;
  $$('.chip').forEach(c => c.classList.toggle('active', c.dataset.sym === s));
  $('#symbolInput').value = s;
  $('#eqSymbolSel').value = s;
  reloadAll();
}

async function reloadAll() {
  await Promise.all([loadOHLC(), loadSignals()]);
  loadEquityCurves(state.symbol);
}

/* ---------------- K 线 ---------------- */
async function loadOHLC() {
  const d = await api(`/api/ohlc?symbol=${encodeURIComponent(state.symbol)}&duration=${encodeURIComponent(state.duration)}`);
  state.ohlc = d;
  $('#chartTitle').textContent = `${d.symbol} · ${state.strategy}`;
  $('#chartTag').textContent = `日线 ${d.count} 根 · ${d.start} ~ ${d.end}`;

  const chg = d.prev_close ? (d.last / d.prev_close - 1) : 0;
  $('#ticker').innerHTML =
    `<span class="t-sym">${d.symbol}</span>
     <span class="t-px">${fmt(d.last)}</span>
     <span class="t-chg ${cls(chg)}">${chg >= 0 ? '+' : ''}${pct(chg)}</span>`;
  renderPriceChart();
}

function baseChartOpts() {
  return {
    layout: {
      background: { color: TV.bg }, textColor: TV.text,
      fontFamily: '"Microsoft YaHei","Segoe UI",sans-serif', fontSize: 11,
    },
    grid: { vertLines: { color: TV.grid }, horzLines: { color: TV.grid } },
    rightPriceScale: { borderColor: TV.border, scaleMargins: { top: 0.12, bottom: 0.08 } },
    timeScale: { borderColor: TV.border, timeVisible: false, rightOffset: 4 },
    crosshair: {
      mode: 0,
      vertLine: { color: '#4a5163', width: 1, style: 3, labelBackgroundColor: TV.accent },
      horzLine: { color: '#4a5163', width: 1, style: 3, labelBackgroundColor: TV.accent },
    },
    handleScroll: { mouseWheel: true, pressedMouseMove: true },
    handleScale: { mouseWheel: true, pinch: true },
  };
}

function renderPriceChart() {
  const d = state.ohlc; if (!d) return;
  const el = $('#priceChart'), vel = $('#volChart');

  if (!state.charts.price) {
    state.charts.price = LightweightCharts.createChart(el, {
      ...baseChartOpts(),
      width: el.clientWidth, height: el.clientHeight,
      crosshair: { ...baseChartOpts().crosshair },
    });
    state.charts.vol = LightweightCharts.createChart(vel, {
      ...baseChartOpts(),
      width: vel.clientWidth, height: vel.clientHeight,
      rightPriceScale: { visible: false },
      timeScale: { borderColor: TV.border, visible: false },
      handleScroll: false, handleScale: false,
    });
  }
  const c = state.charts.price, vc = state.charts.vol;
  // 切换标的 / 开关叠加层时，删掉旧 series 再重建（比 diff 简单可靠）
  ['seriesCandle', 'seriesMa20', 'seriesMa60', 'seriesBbU', 'seriesBbL'].forEach(k => {
    if (state.charts[k]) { try { c.removeSeries(state.charts[k]); } catch (e) {} state.charts[k] = null; }
  });
  if (state.charts.volSeries) { try { vc.removeSeries(state.charts.volSeries); } catch (e) {} }

  state.charts.seriesCandle = c.addCandlestickSeries({
    upColor: TV.up, downColor: TV.down,
    borderUpColor: TV.up, borderDownColor: TV.down,
    wickUpColor: TV.up, wickDownColor: TV.down,
    priceLineColor: TV.accent, priceLineStyle: 2, priceLineWidth: 1,
  });
  state.charts.seriesCandle.setData(d.bars);

  if (state.overlays.ma) {
    state.charts.seriesMa20 = c.addLineSeries({ color: '#e0b64a', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    state.charts.seriesMa20.setData(zip(d.times, d.ma20));
    state.charts.seriesMa60 = c.addLineSeries({ color: '#7b61ff', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    state.charts.seriesMa60.setData(zip(d.times, d.ma60));
  }
  if (state.overlays.bb) {
    state.charts.seriesBbU = c.addLineSeries({ color: 'rgba(41,98,255,.55)', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    state.charts.seriesBbU.setData(zip(d.times, d.bb_upper));
    state.charts.seriesBbL = c.addLineSeries({ color: 'rgba(41,98,255,.55)', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    state.charts.seriesBbL.setData(zip(d.times, d.bb_lower));
  }
  c.timeScale().fitContent();

  // 成交量
  if (state.overlays.vol) {
    vel.style.display = '';
    state.charts.volSeries = vc.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: '' });
    state.charts.volSeries.setData(d.volume);
    vc.timeScale().fitContent();
    // 双图联动
    try {
      c.timeScale().subscribeVisibleLogicalRangeChange(r => { if (r) vc.timeScale().setVisibleLogicalRange(r); });
      vc.timeScale().subscribeVisibleLogicalRangeChange(r => { if (r) c.timeScale().setVisibleLogicalRange(r); });
    } catch (e) {}
  } else {
    vel.style.display = 'none';
  }

  if (state.overlays.sig && state.signals && state.signals.symbol === d.symbol) {
    try { state.charts.seriesCandle.setMarkers(state.signals.markers); } catch (e) {}
  }
}

function zip(times, vals) {
  const out = [];
  for (let i = 0; i < times.length; i++) {
    const v = vals[i];
    if (v !== null && v !== undefined && !Number.isNaN(v)) out.push({ time: times[i], value: v });
  }
  return out;
}

function resizeCharts() {
  const el = $('#priceChart'), vel = $('#volChart'), eql = $('#equityChart');
  if (state.charts.price && el.clientWidth) {
    state.charts.price.applyOptions({ width: el.clientWidth, height: el.clientHeight });
    state.charts.vol.applyOptions({ width: vel.clientWidth, height: vel.clientHeight });
  }
  if (state.charts.equity && eql.clientWidth) {
    state.charts.equity.applyOptions({ width: eql.clientWidth, height: eql.clientHeight });
  }
}

/* ---------------- 信号 + 指标 ---------------- */
async function loadSignals() {
  const d = await api(`/api/signals?symbol=${encodeURIComponent(state.symbol)}` +
    `&strategy=${encodeURIComponent(state.strategy)}&duration=${encodeURIComponent(state.duration)}`);
  state.signals = d;
  $('#chartTitle').textContent = `${d.symbol} · ${d.strategy}`;
  $('#btParams').textContent = Object.entries(d.params || {}).map(([k, v]) => `${k}=${v}`).join('  ');

  if (state.overlays.sig && state.charts.seriesCandle) {
    try { state.charts.seriesCandle.setMarkers(d.markers); } catch (e) {}
  }

  const m = d.metrics || {}, b = d.benchmark_metrics || {};
  const cards = [
    { k: '年化收益率', v: m['年化收益率'], d: 2, s: '%', sub: `基准 ${pct(b['年化收益率'], 1)}`, c: cls(m['年化收益率']) },
    { k: '夏普比率', v: m['夏普比率'], d: 2, s: '', sub: `基准 ${fmt(b['夏普比率'])}`, c: cls(m['夏普比率']) },
    { k: '最大回撤', v: m['最大回撤'], d: 2, s: '%', sub: `基准 ${pct(b['最大回撤'], 1)}`, c: cls(m['最大回撤']) },
    { k: '卡玛比率', v: m['卡玛比率'], d: 2, s: '', sub: '年化收益 / 最大回撤', c: cls(m['卡玛比率']) },
  ];
  const wrap = $('#metricCards');
  wrap.innerHTML = cards.map((c, i) =>
    `<div class="card"><div class="k">${c.k}</div>
      <div class="v ${c.c}" data-num="${i}">0</div>
      <div class="s">${c.sub}</div></div>`).join('');
  cards.forEach((c, i) => {
    const el = wrap.querySelector(`[data-num="${i}"]`);
    const raw = (c.k.includes('率') && c.k !== '夏普比率' && c.k !== '卡玛比率') ? (c.v || 0) * 100 : (c.v || 0);
    countUp(el, raw, c.d, c.s);
  });

  const keys = ['总收益率', '年化波动率', '索提诺比率', 'Omega', '月度胜率', '交易次数',
    '胜率', '盈亏比', '年化换手', '持仓占比', 'Beta', '年化Alpha', '信息比率',
    'VaR95(日)', 'CVaR95(日)', '回撤持续期', '平均持仓天数'];
  const sg = d.significance || {};
  const extra = { 'PSR(显著概率)': sg['PSR(>0)'], 'DSR(去膨胀)': sg['DSR'], '样本数': sg['样本数'] };
  $('#metricGrid').innerHTML =
    keys.filter(k => m[k] !== undefined && m[k] !== null).map(k => {
      const isPct = k.includes('率') || k.includes('占比') || k.includes('收益率') ||
        k.includes('回撤') || k.includes('胜率') || k.includes('VaR') || k.includes('CVaR');
      const val = isPct ? pct(m[k], 2) : (['交易次数', '回撤持续期', '平均持仓天数', '样本数'].includes(k)
        ? String(Math.round(m[k])) : fmt(m[k], 3));
      return `<div class="metric"><div class="k">${k}</div><div class="v">${val}</div></div>`;
    }).join('') +
    Object.entries(extra).filter(([, v]) => v !== null && v !== undefined && !Number.isNaN(v))
      .map(([k, v]) => `<div class="metric"><div class="k">${k}</div>
        <div class="v ${Number(v) > 0.95 ? 'up' : ''}">${fmt(v, 3)}</div></div>`).join('');
}

/* ---------------- 净值曲线 ---------------- */
async function loadEquityCurves(symbol) {
  const d = await api(`/api/equity_curves?symbol=${encodeURIComponent(symbol || state.symbol)}&top_n=8`);
  if (!d.series || !d.series.length) { toast('该标的暂无净值曲线'); return; }
  $('#eqTag').textContent = `${d.symbol} · 前 ${d.series.length} 个模型`;

  const el = $('#equityChart');
  if (state.charts.equity) { state.charts.equity.remove(); state.charts.equity = null; }
  const c = LightweightCharts.createChart(el, {
    ...baseChartOpts(), width: el.clientWidth, height: el.clientHeight,
    localization: { priceFormatter: (p) => p.toFixed(0) },
  });
  state.charts.equity = c;
  const palette = ['#2962ff', '#f23645', '#089981', '#e0b64a', '#7b61ff',
    '#00bcd4', '#ff7043', '#9ccc65'];
  d.series.forEach((s, i) => {
    const ls = c.addLineSeries({
      color: palette[i % palette.length], lineWidth: 2,
      priceLineVisible: false, lastValueVisible: true,
      crosshairMarkerVisible: true,
    });
    ls.setData(s.data);
  });
  c.timeScale().fitContent();
  $('#equityLegend').innerHTML = d.series.map((s, i) =>
    `<span><i style="background:${palette[i % palette.length]}"></i>${s.name}</span>`).join('');
}

/* ---------------- 排名表 ---------------- */
let rankData = null;
async function loadRanking() {
  if (!rankData) rankData = await api('/api/ranking');
  const metric = $('#rankMetric').value;
  const rows = rankData.rows.slice().sort((a, b) => {
    const x = a[metric], y = b[metric];
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    return metric === '最大回撤' ? y - x : y - x;
  });
  const cols = ['策略', '年化收益率', '夏普比率', '卡玛比率', '最大回撤', '胜率', '盈亏比',
    '交易次数', '持仓占比', '跑赢基准占比'];
  const maxAbs = Math.max(...rows.map(r => Math.abs(r[metric] || 0))) || 1;
  $('#rankTable').innerHTML =
    `<thead><tr>${cols.map(c => `<th data-c="${c}">${c}</th>`).join('')}</tr></thead><tbody>` +
    rows.map(r => `<tr data-sym="${r['策略']}">
      <td>${r['策略']}</td>
      <td>${pct(r['年化收益率'], 1)}</td>
      <td class="${cls(r['夏普比率'])}">${fmt(r['夏普比率'])}</td>
      <td>${fmt(r['卡玛比率'])}</td>
      <td class="${cls(r['最大回撤'])}">${pct(r['最大回撤'], 1)}</td>
      <td>${pct(r['胜率'], 0)}</td>
      <td>${fmt(r['盈亏比'])}</td>
      <td>${r['交易次数'] ?? '—'}</td>
      <td>${pct(r['持仓占比'], 0)}</td>
      <td>${pct(r['跑赢基准占比'], 0)}</td></tr>`).join('') + '</tbody>';

  $$('#rankTable tbody tr').forEach(tr => tr.onclick = () => {
    state.strategy = tr.dataset.sym;
    $('#strategySel').value = state.strategy; syncStrategyList();
    $$('.nav-item').forEach(x => x.classList.toggle('active', x.dataset.view === 'chart'));
    $$('.view').forEach(v => v.classList.toggle('active', v.id === 'view-chart'));
    loadSignals(); toast('已切换到 ' + state.strategy);
  });
}

/* ---------------- 风控 / 显著性 ---------------- */
async function loadRiskViews() {
  const sig = await api('/api/significance');
  if (sig.rows.length) {
    const cols = ['策略', '夏普比率', 'PSR(>0)', 'DSR', '夏普95%下界', '夏普95%上界',
      'SR0(期望虚高线)', '样本数', '最小样本量(证明夏普>0)'];
    $('#sigTable').innerHTML =
      `<thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>` +
      sig.rows.map(r => `<tr>
        <td>${r['策略']}</td>
        <td class="${cls(r['夏普比率'])}">${fmt(r['夏普比率'])}</td>
        <td class="${Number(r['PSR(>0)']) > 0.95 ? 'up' : ''}">${fmt(r['PSR(>0)'], 3)}</td>
        <td>${fmt(r['DSR'], 3)}</td>
        <td>${fmt(r['夏普95%下界'])}</td><td>${fmt(r['夏普95%上界'])}</td>
        <td>${fmt(r['SR0(期望虚高线)'])}</td>
        <td>${r['样本数'] ?? '—'}</td>
        <td>${r['最小样本量(证明夏普>0)'] ? Math.round(r['最小样本量(证明夏普>0)']).toLocaleString() : '—'}</td>
      </tr>`).join('') + '</tbody>';
  }

  const ab = await api('/api/riskab');
  if (ab.rows.length) {
    const piv = {};
    ab.rows.forEach(r => {
      piv[r['策略']] = piv[r['策略']] || {};
      piv[r['策略']][r['风控方案']] = r['夏普改善'];
    });
    const schemes = Array.from(new Set(ab.rows.map(r => r['风控方案']))).filter(s => s !== '无风控');
    $('#riskTable').innerHTML =
      `<thead><tr><th>策略</th>${schemes.map(s => `<th>${s}</th>`).join('')}</tr></thead><tbody>` +
      Object.entries(piv).map(([k, v]) => `<tr><td>${k}</td>` +
        schemes.map(s => {
          const x = v[s];
          return `<td class="${cls(x)}">${x === null || x === undefined ? '—' : (x >= 0 ? '+' : '') + fmt(x)}</td>`;
        }).join('') + '</tr>').join('') + '</tbody>';
  }
}

/* ---------------- 信号监控 ---------------- */
async function loadWatchlist(force) {
  const d = await api('/api/watchlist' + (force ? '?_=' + Date.now() : ''));
  if (!d.rows || !d.rows.length) { $('#watchTable').innerHTML = '<tbody><tr><td>暂无数据</td></tr></tbody>'; return; }
  const cols = d.columns.filter(c => !c.endsWith('_变化'));
  $('#watchTag').textContent = `${d.rows.length} 个标的 · 4 模型投票`;
  $('#watchTable').innerHTML =
    `<thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>` +
    d.rows.map(r => `<tr>${cols.map(c => {
      const v = r[c];
      const isPx = c === '最新收盘';
      const isVote = c === '看多票数' || c === '合计建议仓位';
      let s = (typeof v === 'number') ? (isPx ? fmt(v) : fmt(v, 2)) : (v ?? '—');
      let cl = '';
      if (isVote) cl = Number(v) > 0 ? 'up' : '';
      if (typeof v === 'number' && !isPx && !isVote) cl = cls(v);
      return `<td class="${cl}">${s}</td>`;
    }).join('')}</tr>`).join('') + '</tbody>';

  // 点行切换标的
  $$('#watchTable tbody tr').forEach(tr => tr.onclick = () => {
    const sym = tr.children[0] && tr.children[0].textContent.trim();
    if (sym && sym !== '—') setSymbol(sym);
  });
}

/* ---------------- 账户 / 模拟盘 ---------------- */
async function loadAccount() {
  const d = await api('/api/account');
  const box = $('#accountBox');
  if (!d.ok) { box.innerHTML = `<span class="muted">未连接：${d.error || '未知'}</span>`; return; }
  const a = d.account || {};
  const nv = parseFloat(a.NetLiquidation || a.TotalCashValue || 0);
  box.innerHTML =
    `<div>账户 <b>${(a.accounts || '—').split(',')[0]}</b></div>
     <div>净值 <b>${nv ? nv.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—'}</b></div>
     <div>可用 <b>${a.AvailableFunds ? Number(a.AvailableFunds).toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—'}</b></div>
     <div>持仓 <b>${(d.positions || []).length}</b> 个</div>`;
  $('#liveTxt').textContent = (a.accounts || '').startsWith('DU') ? 'PAPER' : 'LIVE';
}

async function loadPaper() {
  const d = await api('/api/paper/state');
  const box = $('#paperBox');
  if (!d.ok || !d.states.length) { box.innerHTML = '<span class="muted">尚无模拟盘运行记录</span>'; return; }
  box.innerHTML = d.states.map(s =>
    `<div style="margin-bottom:8px">
      <div><b>${s.策略 || '—'}</b> · ${s.时间 || ''} · ${s.dry_run ? 'DRY-RUN' : 'LIVE'}</div>
      <div class="muted">风控 ${s.风控 || '无'} · 订单 ${s.订单数 ?? 0} 笔 · 标的 ${s.标的数 ?? 0}</div>
      <div class="muted">权重 ${JSON.stringify(s.目标权重 || {})}</div>
    </div>`).join('');
}

boot().catch(e => {
  $('#boot').innerHTML = `<div class="boot-logo">Quant<span>Lab</span></div>
    <div class="boot-txt" style="color:#f23645">启动失败：${e.message}</div>`;
});
