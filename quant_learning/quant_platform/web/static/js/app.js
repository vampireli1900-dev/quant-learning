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
  bar: 'day',                       // day/week/hour/min15
  view: 'chart',
  ohlc: null,
  signals: null,
  overlays: { ma: true, bb: false, sig: true, vol: true },
  charts: {},
};

/* 非日线周期使用固定区间（日线周期跟随周期下拉框） */
const BAR_CFG = {
  day:   { dur: null },
  week:  { dur: '5 Y' },
  hour:  { dur: '2 Y' },
  min15: { dur: '1 M' },
};
const BAR_LABEL = { day: '日线', week: '周线', hour: '1小时', min15: '15分钟' };

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
  $('#durationSel').onchange = (e) => {
    state.duration = e.target.value;
    if (state.bar !== 'day') setBar('day');      // 区间下拉只对日线生效
    else reloadAll();
  };
  // 周期切换
  $$('#barToggle .sw').forEach(b => b.onclick = () => setBar(b.dataset.bar));

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

  // 组合页
  const pfs = $('#pfStrategySel');
  pfs.innerHTML = meta.strategy_names.map(n =>
    `<option value="${n}"${n === 'ADX趋势过滤' ? ' selected' : ''}>${n}</option>`).join('');
  pfs.onchange = () => loadPortfolio();
  $('#pfSchemeSel').onchange = () => loadPortfolio();
  $('#pfRefresh').onclick = () => loadPortfolio();

  bindNav(); bindOverlays(); bindDb();
  await reloadAll();
  loadRanking(); loadRiskViews(); loadWatchlist(); loadAccount(); loadPaper();
  loadDb();

  // 市场时钟 + 自动刷新
  tickClock(); setInterval(tickClock, 1000);
  setInterval(loadAccount, 30000);
  setInterval(loadPaper, 120000);
  setInterval(() => loadWatchlist(true), 300000);

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
    if (state.view === 'portfolio' && !state.pfLoaded) loadPortfolio();
    if (state.view === 'db') loadDb();
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

function setBar(bar) {
  if (state.bar === bar) return;
  state.bar = bar;
  $$('#barToggle .sw').forEach(b => b.classList.toggle('active', b.dataset.bar === bar));
  // 分钟/小时线没有对齐的回测信号，自动关掉信号标记
  if (bar !== 'day') { state.overlays.sig = false; $('#overlayToggle .sw[data-ov="sig"]').classList.remove('active'); }
  reloadAll();
  toast(`已切换到${BAR_LABEL[bar]}` + (bar !== 'day' ? '（信号回测仅在日线）' : ''));
}

async function reloadAll() {
  await Promise.all([loadOHLC()]);
  if (state.bar === 'day') await loadSignals();
  if (state.view === 'equity') loadEquityCurves($('#eqSymbolSel').value);
}

/* ---------------- K 线 ---------------- */
async function loadOHLC() {
  const dur = BAR_CFG[state.bar].dur || state.duration;
  const d = await api(`/api/ohlc?symbol=${encodeURIComponent(state.symbol)}` +
    `&duration=${encodeURIComponent(dur)}&bar=${state.bar}`);
  state.ohlc = d;
  $('#chartTitle').textContent = `${d.symbol} · ${state.strategy}`;
  $('#chartTag').textContent = `${BAR_LABEL[d.bar] || '日线'} ${d.count} 根 · ${d.start} ~ ${d.end}`;

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
    timeScale: { borderColor: TV.border, timeVisible: state.bar !== 'day',
                 secondsVisible: false, rightOffset: 4 },
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

  if (state.overlays.sig && state.bar === 'day' && state.signals &&
      state.signals.symbol === d.symbol) {
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
  const pel = $('#pfEquityChart'), pwel = $('#pfWeightChart');
  if (state.charts.pfEq && pel.clientWidth) {
    state.charts.pfEq.applyOptions({ width: pel.clientWidth, height: pel.clientHeight });
    state.charts.pfW.applyOptions({ width: pwel.clientWidth, height: pwel.clientHeight });
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
  const seriesRefs = [];
  d.series.forEach((s, i) => {
    const ls = c.addLineSeries({
      color: palette[i % palette.length], lineWidth: 2,
      priceLineVisible: false, lastValueVisible: true,
      crosshairMarkerVisible: true,
    });
    ls.setData(s.data);
    seriesRefs.push({ name: s.name, ls, visible: true });
  });
  c.timeScale().fitContent();
  // 图例可点击显隐
  const leg = $('#equityLegend');
  leg.innerHTML = d.series.map((s, i) =>
    `<span class="lg-item" data-i="${i}"><i style="background:${palette[i % palette.length]}"></i>${s.name}</span>`).join('');
  leg.querySelectorAll('.lg-item').forEach(item => item.onclick = () => {
    const ref = seriesRefs[+item.dataset.i];
    ref.visible = !ref.visible;
    try { ref.ls.applyOptions({ visible: ref.visible }); } catch (e) {}
    item.classList.toggle('off', !ref.visible);
  });
}

/* ---------------- 组合视图 ---------------- */
async function loadPortfolio() {
  const strat = $('#pfStrategySel').value;
  const scheme = $('#pfSchemeSel').value;
  const d = await api(`/api/portfolio_equity?strategy=${encodeURIComponent(strat)}` +
    `&scheme=${encodeURIComponent(scheme)}`);
  state.pfLoaded = true;
  $('#pfTag').textContent = `${d.strategy} · ${schemeLabel(scheme)} · ${d.symbols.length} 标的`;

  // 指标卡
  const m = d.metrics || {};
  const cards = [
    { k: '组合年化', v: m['年化收益率'], s: '%', sub: '基准=等权买入持有' },
    { k: '组合夏普', v: m['夏普比率'], s: '', sub: '单标的平均约 0.55' },
    { k: '最大回撤', v: m['最大回撤'], s: '%', sub: '组合分散化后显著收窄' },
    { k: '卡玛比率', v: m['卡玛比率'], s: '', sub: '年化 / 回撤' },
  ];
  const wrap = $('#pfCards');
  wrap.innerHTML = cards.map((c, i) =>
    `<div class="card"><div class="k">${c.k}</div>
      <div class="v ${cls(c.v)}" data-pf="${i}">0</div>
      <div class="s">${c.sub}</div></div>`).join('');
  cards.forEach((c, i) => {
    const el = wrap.querySelector(`[data-pf="${i}"]`);
    const raw = (c.s === '%') ? (c.v || 0) * 100 : (c.v || 0);
    countUp(el, raw, 2, c.s);
  });

  // 组合净值 vs 基准
  const el = $('#pfEquityChart');
  if (state.charts.pfEq) { state.charts.pfEq.remove(); state.charts.pfEq = null; }
  const c = LightweightCharts.createChart(el, {
    ...baseChartOpts(), width: el.clientWidth, height: el.clientHeight,
    timeScale: { ...baseChartOpts().timeScale, timeVisible: false },
  });
  state.charts.pfEq = c;
  c.addLineSeries({ color: TV.accent, lineWidth: 2, title: '组合',
    priceLineVisible: false, lastValueVisible: true }).setData(d.equity);
  c.addLineSeries({ color: '#787b86', lineWidth: 1, lineStyle: 2, title: '基准',
    priceLineVisible: false, lastValueVisible: false }).setData(d.benchmark);
  c.timeScale().fitContent();
  $('#pfLegend').innerHTML =
    `<span><i style="background:${TV.accent}"></i>${d.strategy}（${schemeLabel(scheme)}）</span>
     <span><i style="background:#787b86"></i>等权买入持有基准</span>`;

  // 权重折线
  const wel = $('#pfWeightChart');
  if (state.charts.pfW) { state.charts.pfW.remove(); state.charts.pfW = null; }
  const wc = LightweightCharts.createChart(wel, {
    ...baseChartOpts(), width: wel.clientWidth, height: wel.clientHeight,
    timeScale: { ...baseChartOpts().timeScale, timeVisible: false },
    localization: { priceFormatter: (p) => (p * 100).toFixed(0) + '%' },
  });
  state.charts.pfW = wc;
  const wRefs = [];
  (d.weights || []).forEach(w => {
    const ls = wc.addLineSeries({ color: w.color, lineWidth: 1,
      priceLineVisible: false, lastValueVisible: false });
    ls.setData(w.data);
    wRefs.push({ name: w.name, ls, visible: true, current: w.current });
  });
  wc.timeScale().fitContent();
  const wleg = $('#pfLegend');
  $('#pfWeightNow').textContent = '当前权重：' +
    (d.weights || []).filter(w => w.current > 0.005)
      .map(w => `${w.name} ${(w.current * 100).toFixed(0)}%`).join(' · ') || '空仓';
  // 权重图例（点击显隐）
  document.getElementById('pfWeightLegend')?.remove();
  const wbox = document.createElement('div');
  wbox.className = 'legend';
  wbox.id = 'pfWeightLegend';
  wbox.style.padding = '6px 14px';
  wbox.innerHTML = (d.weights || []).map((w, i) =>
    `<span class="lg-item" data-w="${i}"><i style="background:${w.color}"></i>${w.name}</span>`).join('');
  $('#pfWeightChart').after(wbox);
  wbox.querySelectorAll('.lg-item').forEach(item => item.onclick = () => {
    const ref = wRefs[+item.dataset.w];
    ref.visible = !ref.visible;
    try { ref.ls.applyOptions({ visible: ref.visible }); } catch (e) {}
    item.classList.toggle('off', !ref.visible);
  });

  // 方案对比表
  await loadSchemeTable(strat, scheme);
}

function schemeLabel(s) {
  return { equal: '等权', signal: '信号加权', vol_inverse: '波动率倒数',
    risk_parity: '风险平价', vol_target: '波动率目标' }[s] || s;
}

let schemeData = null;
async function loadSchemeTable(strat, curScheme) {
  if (!schemeData) schemeData = await api('/api/portfolio_schemes');
  const rows = schemeData.rows.filter(r => r['策略'] === strat);
  const cols = ['权重方案', '年化收益率', '夏普比率', '最大回撤', '卡玛比率', '年化换手', '平均标的数量'];
  $('#pfSchemeTable').innerHTML =
    `<thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>` +
    rows.map(r => `<tr data-scheme="${r['权重方案']}" class="${r['权重方案'] === curScheme ? 'hl' : ''}">
      <td>${schemeLabel(r['权重方案'])}</td>
      <td class="${cls(r['年化收益率'])}">${pct(r['年化收益率'], 1)}</td>
      <td class="${cls(r['夏普比率'])}">${fmt(r['夏普比率'])}</td>
      <td class="${cls(r['最大回撤'])}">${pct(r['最大回撤'], 1)}</td>
      <td>${fmt(r['卡玛比率'])}</td>
      <td>${fmt(r['年化换手'], 1)}</td>
      <td>${fmt(r['平均标的数量'], 1)}</td></tr>`).join('') + '</tbody>';
  $$('#pfSchemeTable tbody tr').forEach(tr => tr.onclick = () => {
    $('#pfSchemeSel').value = tr.dataset.scheme;
    loadPortfolio();
  });
}

/* ---------------- 市场时钟 ---------------- */
function tickClock() {
  const now = new Date();
  const opt = { timeZone: 'America/New_York', hour12: false };
  $('#clockTime').textContent =
    now.toLocaleTimeString('en-US', { ...opt, hour: '2-digit', minute: '2-digit' });
  const parts = new Intl.DateTimeFormat('en-US',
    { ...opt, weekday: 'short', hour: '2-digit', minute: '2-digit' }).formatToParts(now);
  const get = (k) => { const p = parts.find(x => x.type === k); return p ? p.value : ''; };
  const h = (+get('hour')) % 24, m = +get('minute');
  const mins = h * 60 + m;
  const open = !['Sat', 'Sun'].includes(get('weekday')) && mins >= 570 && mins < 960;
  const st = $('#clockState');
  st.textContent = open ? '开市' : '休市';
  st.className = 'clock-state ' + (open ? 'open' : 'closed');
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

/* ==========================================================================
   数据库视图（SQL）
   ========================================================================== */
const DB_GROUPS = [
  {
    title: '行情数据 · API 抓取',
    tables: [
      ['assets', '标的主数据，代码唯一'],
      ['bars', 'OHLCV 行情，按(标的,周期,时间)唯一'],
      ['ingest_log', '每次调 API 的抓取日志'],
    ],
  },
  {
    title: '训练集 · 特征工程',
    tables: [
      ['feature_sets', '训练集元信息（标的/时间/特征清单）'],
      ['features', '特征明细长表（样本×特征→值）'],
    ],
  },
  {
    title: '实验与回测',
    tables: [
      ['experiments', '一次实验（策略×标的×假设）'],
      ['backtest_runs', '一次回测 = (实验,策略,标的)'],
      ['backtest_metrics', '绩效指标（关键列+全量JSON）'],
      ['equity_points', '净值曲线采样点'],
      ['trades', '逐笔交易（进→出）'],
    ],
  },
  {
    title: '模型注册表',
    tables: [['model_registry', '通过显著性检验、入选实盘的模型']],
  },
  {
    title: '实盘运营',
    tables: [
      ['orders', '委托记录（含 dry-run 计划）'],
      ['position_snapshots', '持仓快照'],
      ['account_snapshots', '账户净值快照'],
    ],
  },
];

let dbRows = {};

function renderDbSummary(info) {
  const box = $('#dbSummary');
  if (!info || !info.available) {
    box.innerHTML = `<div class="card"><div class="k">数据库</div>
      <div class="v db-err">不可用</div>
      <div class="s">${(info && info.reason) || '未安装 sqlalchemy'}</div></div>`;
    return;
  }
  const cards = [
    ['方言', info.dialect.toUpperCase(), info.sqlite ? '单文件，零配置' : '客户端/服务器'],
    ['表数量', info.tables.length, '14 张表 · 5 层结构'],
    ['总行数', (info.total_rows || 0).toLocaleString(), '全部表累计'],
    ['文件大小', (info.size_mb ?? 0) + ' MB', 'SQLite 数据文件'],
  ];
  box.innerHTML = cards.map(([k, v, s], i) =>
    `<div class="card" style="animation:dbIn .42s var(--ease) ${i * 60}ms forwards;opacity:0">
       <div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div>
     </div>`).join('');
}

function renderDbTables(info) {
  const box = $('#dbTables');
  if (!info || !info.available) { box.innerHTML = ''; return; }
  const byName = {};
  (info.tables || []).forEach(t => { byName[t['表']] = t['行数']; });
  const maxRows = Math.max(1, ...Object.values(byName));

  box.innerHTML = DB_GROUPS.map((g, gi) => `
    <div class="db-group-title">${g.title}</div>
    <div class="db-grid">${g.tables.map(([n, d], ti) => {
      const rows = byName[n] ?? 0;
      const w = Math.round(Math.log10(rows + 1) / Math.log10(maxRows + 1) * 100);
      return `<div class="db-tbl" style="animation-delay:${(gi * 5 + ti) * 45}ms" title="${d}">
        <div class="n">${n}</div>
        <div class="r${rows ? '' : ' empty'}">${rows.toLocaleString()}</div>
        <div class="m">${d}</div>
        <div class="b"><i data-w="${w}"></i></div>
      </div>`;
    }).join('')}
  </div>`).join('');

  // 条形进度动画：等一帧再设宽度，才能触发 transition
  requestAnimationFrame(() => {
    $$('#dbTables .b i').forEach(el => { el.style.width = el.dataset.w + '%'; });
  });
}

function renderEr() {
  // 五层节点（x, y, 宽, 高, 标题, 副标题, 高亮）
  const nodes = [
    [14, 12, 132, 40, 'assets', '标的主数据', 0],
    [178, 12, 156, 40, 'bars', 'OHLCV 行情', 0],
    [366, 12, 132, 40, 'ingest_log', '抓取日志', 0],
    [14, 96, 156, 40, 'feature_sets', '训练集元信息', 0],
    [202, 96, 156, 40, 'features', '特征长表', 0],
    [14, 180, 132, 40, 'experiments', '实验', 0],
    [178, 180, 156, 40, 'backtest_runs', '回测运行', 0],
    [366, 180, 156, 40, 'backtest_metrics', '绩效指标', 0],
    [554, 180, 132, 40, 'equity_points', '净值曲线', 0],
    [714, 180, 132, 40, 'trades', '逐笔交易', 0],
    [280, 264, 156, 40, 'model_registry', '入选模型', 1],
    [14, 348, 120, 40, 'orders', '委托记录', 0],
    [166, 348, 168, 40, 'position_snapshots', '持仓快照', 0],
    [366, 348, 168, 40, 'account_snapshots', '账户快照', 0],
  ];
  const links = [
    [98, 54, 98, 96], [256, 54, 256, 96], [256, 116, 256, 180],
    [80, 140, 80, 180], [310, 220, 310, 264], [444, 220, 444, 264],
    [80, 220, 80, 348], [250, 300, 74, 348], [250, 300, 250, 348],
    [250, 300, 450, 348],
  ];
  const svg = ['<svg viewBox="0 0 880 404" xmlns="http://www.w3.org/2000/svg">'];
  svg.push('<defs><marker id="ar" markerWidth="8" markerHeight="8" refX="7" refY="3" '
    + 'orient="auto"><path d="M0,0 L7,3 L0,6 z" fill="#2962ff"/></marker></defs>');
  links.forEach(([x1, y1, x2, y2], i) => {
    svg.push(`<path class="er-flow" d="M${x1},${y1} C${x1},${(y1 + y2) / 2} `
      + `${x2},${(y1 + y2) / 2} ${x2},${y2}" marker-end="url(#ar)" `
      + `style="animation-delay:${i * 120}ms" opacity=".55"/>`);
  });
  nodes.forEach(([x, y, w, h, t, s, hi]) => {
    svg.push(`<rect class="er-box-node${hi ? ' er-node-hi' : ''}" x="${x}" y="${y}" `
      + `width="${w}" height="${h}" rx="6"/>`);
    svg.push(`<text class="er-t" x="${x + 11}" y="${y + 18}">${t}</text>`);
    svg.push(`<text class="er-s" x="${x + 11}" y="${y + 32}">${s}</text>`);
  });
  svg.push('</svg>');
  $('#dbEr').innerHTML = svg.join('');
}


async function loadDb() {
  try {
    const info = await api('/api/db/info');
    renderDbSummary(info);
    renderDbTables(info);
    $('#dbTag').textContent = info.available
      ? `${info.dialect.toUpperCase()} · ${info.total_rows.toLocaleString()} 行`
      : '不可用';
    $('#dbPath').textContent = info.location || '';
  } catch (e) {
    $('#dbTag').textContent = '不可用';
  }
  try {
    const ex = await api('/api/db/examples');
    const sel = $('#dbExampleSel');
    sel.innerHTML = '<option value="">— 选择示例查询 —</option>' +
      ex.examples.map((q, i) => `<option value="${i}">${q['名称']}</option>`).join('');
    sel.onchange = () => {
      const i = sel.value;
      if (i === '') return;
      $('#dbSql').value = ex.examples[+i]['sql'];
    };
    window.__dbExamples = ex.examples;
    if (!$('#dbSql').value) $('#dbSql').value = ex.examples[0]['sql'];
  } catch (e) { /* 忽略 */ }
}

async function runDbQuery() {
  const sql = $('#dbSql').value.trim();
  const msg = $('#dbMsg');
  if (!sql) { msg.textContent = '请输入 SQL'; return; }
  loading(true);
  try {
    const r = await fetch('/api/db/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sql, limit: 500 }),
    });
    const d = await r.json();
    if (!r.ok) {
      msg.innerHTML = `<span class="db-err">错误：${d.detail || r.status}</span>`;
      return;
    }
    msg.textContent = `${d.rowcount} 行 · ${d.elapsed_ms} ms`;
    if (!d.columns.length) { $('#dbResult').innerHTML = ''; return; }
    $('#dbResult').innerHTML =
      `<thead><tr>${d.columns.map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>` +
      d.rows.map((row, ri) =>
        `<tr style="animation:dbIn .32s var(--ease) ${Math.min(ri, 20) * 14}ms forwards;opacity:0">` +
        row.map(v => {
          let s = (v === null || v === undefined) ? '—'
            : (typeof v === 'number' ? (Number.isInteger(v) ? v.toLocaleString() : fmt(v, 4)) : String(v));
          if (typeof v === 'string' && v.length > 60) s = v.slice(0, 60) + '…';
          return `<td>${s}</td>`;
        }).join('') + '</tr>').join('') + '</tbody>';
  } catch (e) {
    msg.innerHTML = `<span class="db-err">请求失败：${e.message}</span>`;
  } finally { loading(false); }
}

function bindDb() {
  $('#dbRefresh').onclick = () => loadDb();
  $('#dbRun').onclick = () => runDbQuery();
  $('#dbSql').addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); runDbQuery(); }
  });
}

boot().catch(e => {
  $('#boot').innerHTML = `<div class="boot-logo">Quant<span>Lab</span></div>
    <div class="boot-txt" style="color:#f23645">启动失败：${e.message}</div>`;
});
