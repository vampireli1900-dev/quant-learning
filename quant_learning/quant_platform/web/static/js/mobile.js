/* ==========================================================================
   QuantLab Mobile —— 前端逻辑（/m）
   --------------------------------------------------------------------------
   与桌面版共用同一套只读 API 与 i18n 字典，但信息架构按手机重排：
     · 底部 5 Tab：行情 / 净值 / 组合 / 账户 / 更多
     · 表格 → 卡片列表；侧栏模型库 → 横向 chips
     · K 线关闭纵向拖动，让页面滚动不被图表吞掉
     · 视图首次进入才加载，避免手机上一次性打 8 个请求
   ========================================================================== */

/* ---------------- 移动端补充文案（并入共享字典） ---------------- */
Object.assign(DICT.zh, {
  'm.more': '更多',
  'm.pull': '下拉刷新',
  'm.logout': '退出登录',
  'm.release': '松开刷新',
  'm.refreshing': '刷新中…',
  'm.strategy': '策略',
  'm.sigEq': '策略净值 vs 基准',
  'm.norm100': '起点 = 100',
  'm.pickSymbol': '选择标的',
  'm.pickStrategy': '选择策略',
  'm.risk.sub': 'PSR / DSR / 风控 A-B',
  'm.db.sub': '行数总览 · 只读 SQL',
  'm.langHint': '中英文切换',
  'm.desktop': '桌面版',
  'm.desktopHint': '宽屏完整终端',
  'm.noData': '暂无数据',
  'm.tapTip': '点顶部代码可切换标的',
  'm.updated': '更新于 {t}',
  'm.strategy': '策略',
  'm.allocNote': '含现金 · 汇总已折 {cur}',
  'm.posEmpty': '当前无持仓',
  'm.ordEmpty': '暂无委托记录',
  'm.sig':'策略',
  'm.bench': '基准',
  'm.db.tables': '数据表',
  'm.db.rows': '总行数',
  'm.db.size': '库大小',
  'm.db.dialect': '方言',
  'm.sql.run': '执行',
  'm.sql.ph': 'SELECT * FROM bars LIMIT 20',
  'm.sql.ok': '返回 {n} 行',
  'm.watch.hint': '4 个趋势/回归模型对 8 个标的的当日投票，只读不动仓位。',
  'm.rank.hint': '跨 8 个标的平均，点任一策略可直接切到它的 K 线。',
  'm.risk.hint': 'PSR > 0.95 才算统计显著；DSR 已扣除"试了很多次"的选择偏差。',
  'm.refreshed': '已刷新',
});

Object.assign(DICT.en, {
  'm.more': 'More',
  'm.pull': 'Pull to refresh',
  'm.logout': 'Sign out',
  'm.release': 'Release to refresh',
  'm.refreshing': 'Refreshing…',
  'm.strategy': 'Strategy',
  'm.sigEq': 'Strategy vs Benchmark',
  'm.norm100': 'Base = 100',
  'm.pickSymbol': 'Select symbol',
  'm.pickStrategy': 'Select strategy',
  'm.risk.sub': 'PSR / DSR / Risk A-B',
  'm.db.sub': 'Row counts · read-only SQL',
  'm.langHint': 'Chinese / English',
  'm.desktop': 'Desktop',
  'm.desktopHint': 'Full wide-screen terminal',
  'm.noData': 'No data',
  'm.tapTip': 'Tap the code above to switch symbol',
  'm.updated': 'Updated {t}',
  'm.allocNote': 'Cash included · converted to {cur}',
  'm.posEmpty': 'No open positions',
  'm.ordEmpty': 'No orders yet',
  'm.sig': 'Strategy',
  'm.bench': 'Benchmark',
  'm.db.tables': 'Tables',
  'm.db.rows': 'Total rows',
  'm.db.size': 'DB size',
  'm.db.dialect': 'Dialect',
  'm.sql.run': 'Run',
  'm.sql.ph': 'SELECT * FROM bars LIMIT 20',
  'm.sql.ok': '{n} rows returned',
  'm.watch.hint': 'Daily votes of 4 trend/reversion models across 8 symbols. Read-only.',
  'm.rank.hint': 'Averaged across 8 symbols. Tap a strategy to load its chart.',
  'm.risk.hint': 'PSR > 0.95 counts as significant; DSR deflates for multiple trials.',
  'm.refreshed': 'Refreshed',
});

/* ---------------- 主题色（与桌面一致） ---------------- */
const TVM = {
  bg: '#1a1e29', grid: '#222736', border: '#2a2e39',
  text: '#9ca3b0', up: '#f23645', down: '#089981',
  accent: '#2962ff', accent2: '#5b8cff', flat: '#787b86', gold: '#e0b64a',
};
const PALETTE = ['#2962ff', '#f23645', '#089981', '#e0b64a',
                 '#7b61ff', '#00bcd4', '#ff7043', '#9ccc65'];

/* ---------------- 状态 ---------------- */
const state = {
  symbol: 'SPY',
  strategy: 'ADX趋势过滤',
  duration: '5 Y',
  bar: 'day',
  view: 'chart',
  overlays: { ma: true, bb: false, sig: true },
  meta: null,
  ohlc: null,
  signals: null,
  loaded: {},          // 视图 -> 是否已加载
  charts: {},
  acct: { data: null, timer: null, auto: true },
};

const BAR_DUR = { day: null, week: '5 Y', hour: '2 Y', min15: '1 M' };
const barLabel = (b) => t('bar.label.' + (BAR_DUR.hasOwnProperty(b) ? b : 'day'));
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

/* ---------------- 工具 ---------------- */
const isNum = (v) => v !== null && v !== undefined && !Number.isNaN(Number(v));

function fmt(v, d = 2) {
  if (!isNum(v)) return '—';
  return Number(v).toFixed(d);
}
function pct(v, d = 2) {
  if (!isNum(v)) return '—';
  return (Number(v) * 100).toFixed(d) + '%';
}
function pctSign(v, d = 2) {
  if (!isNum(v)) return '—';
  const n = Number(v);
  return (n > 0 ? '+' : '') + (n * 100).toFixed(d) + '%';
}
function nf(v, d = 0) {
  if (!isNum(v)) return '—';
  return Number(v).toLocaleString(undefined, {
    minimumFractionDigits: d, maximumFractionDigits: d,
  });
}
function signed(v, d = 0) {
  if (!isNum(v)) return '—';
  return (Number(v) > 0 ? '+' : '') + nf(v, d);
}
function cls(v) { return Number(v) > 0 ? 'up' : (Number(v) < 0 ? 'down' : ''); }
function colorOf(v) { return Number(v) > 0 ? TVM.up : (Number(v) < 0 ? TVM.down : TVM.text); }
function hexA(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}
function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
/* "2026-10-20 01:42" → UNIX 秒（按 UTC 打包，图上标签即墙上时间） */
function toTs(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/.exec(String(s || ''));
  if (!m) return null;
  return Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0)) / 1000;
}
/* 图表数据清洗：去 null/NaN、去重、排序 —— 否则折线会跌到 0 画出假竖线 */
function cleanSeries(data) {
  const seen = new Map();
  (data || []).forEach(p => {
    if (!p) return;
    const v = Number(p.value);
    const t = p.time;
    if (t === null || t === undefined || !isFinite(v)) return;
    seen.set(t, v);
  });
  const out = [];
  Array.from(seen.keys()).sort((a, b) => (a > b ? 1 : a < b ? -1 : 0))
    .forEach(t => out.push({ time: t, value: seen.get(t) }));
  return out;
}

let toastTimer = null;
function toast(msg) {
  const el = $('#mToast');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('on'), 2000);
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return await r.json();
}

/* ---------------- 图表工厂 ---------------- */
function chartBase(extra) {
  return Object.assign({
    layout: {
      background: { color: TVM.bg }, textColor: TVM.text,
      fontFamily: '"PingFang SC","Microsoft YaHei",sans-serif',
      fontSize: 10, attributionLogo: false,
    },
    grid: { vertLines: { color: TVM.grid }, horzLines: { color: TVM.grid } },
    rightPriceScale: {
      borderColor: TVM.border, scaleMargins: { top: 0.12, bottom: 0.08 },
    },
    timeScale: {
      borderColor: TVM.border, timeVisible: state.bar !== 'day',
      secondsVisible: false, rightOffset: 3, barSpacing: 5,
    },
    crosshair: {
      mode: 0,
      vertLine: { color: '#4a5163', width: 1, style: 3, labelBackgroundColor: TVM.accent },
      horzLine: { color: '#4a5163', width: 1, style: 3, labelBackgroundColor: TVM.accent },
    },
    // 关键：关掉纵向触控拖动，手指上下滑 = 滚页面；左右滑才是平移图表
    handleScroll: { mouseWheel: true, pressedMouseMove: true,
                    horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
    localization: { locale: L() === 'en' ? 'en-US' : 'zh-CN' },
  }, extra || {});
}

function mkChart(key, el, extra) {
  if (state.charts[key]) { try { state.charts[key].remove(); } catch (e) {} }
  const c = LightweightCharts.createChart(el, Object.assign(chartBase(extra), {
    width: el.clientWidth || 340, height: el.clientHeight || 220,
  }));
  state.charts[key] = c;
  // 记住容器：图表实例没有稳定 API 反查 DOM，resize 时要用
  state.chartEls = state.chartEls || {};
  state.chartEls[key] = el;
  if (window.ResizeObserver) {
    if (!state._ro) {
      let raf = null;
      state._ro = new ResizeObserver(() => {
        if (raf) return;
        raf = requestAnimationFrame(() => { raf = null; resizeCharts(); });
      });
    }
    try { state._ro.observe(el); } catch (e) {}
  }
  return c;
}

function resizeCharts() {
  const els = state.chartEls || {};
  Object.keys(state.charts).forEach(k => {
    const c = state.charts[k], el = els[k];
    if (!c || !el) return;
    // 视图隐藏时 clientWidth = 0，此时不要 applyOptions，否则图表会被压成 0×0
    if (!el.clientWidth || !el.clientHeight) return;
    try { c.applyOptions({ width: el.clientWidth, height: el.clientHeight }); } catch (e) {}
  });
}

/* 公网模式（服务端开了登录鉴权）才显示「退出登录」按钮 */
async function initAuthUi() {
  let authed = false;
  try {
    const r = await fetch('/healthz', { cache: 'no-store' });
    const d = await r.json();
    authed = !!(d && d.auth);
  } catch (e) { return; }
  const btn = document.getElementById('mlogout');
  if (!btn || !authed) return;
  btn.style.display = '';
  btn.onclick = async () => {
    try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (e) { /* ignore */ }
    location.href = '/login';
  };
}

/* ---------------- 启动 ---------------- */
async function boot() {
  applyI18n();
  document.title = 'QuantLab';
  initAuthUi();
  bindLangBtn();
  bindTabs();
  bindSheet();
  bindPull();
  bindChartControls();

  const meta = await api('/api/bootstrap');
  state.meta = meta;

  // 顶栏 / 抽屉里的标的价格
  renderSymbolList('');
  $('#mEqSym').innerHTML = meta.symbols.map(s =>
    `<option value="${s}"${s === state.symbol ? ' selected' : ''}>${s}</option>`).join('');

  // 策略 chips（横向滑动）
  $('#mStratChips').innerHTML = meta.strategy_names.map(n =>
    `<button class="mchip-i${n === state.strategy ? ' on' : ''}" data-n="${esc(n)}">${esc(stratName(n))}</button>`).join('');
  $$('#mStratChips .mchip-i').forEach(b => b.onclick = () => {
    state.strategy = b.dataset.n;
    $$('#mStratChips .mchip-i').forEach(x => x.classList.toggle('on', x.dataset.n === state.strategy));
    loadSignals();
  });

  $('#mPfStrat').innerHTML = meta.strategy_names.map(n =>
    `<option value="${esc(n)}"${n === state.strategy ? ' selected' : ''}>${esc(stratName(n))}</option>`).join('');
  $('#mPfStrat').onchange = () => loadPortfolio();
  $('#mPfScheme').onchange = () => loadPortfolio();
  $('#mEqSym').onchange = () => loadEquity($('#mEqSym').value);
  $('#mEqRefresh').onclick = () => loadEquity($('#mEqSym').value);
  $('#mMoreLang').onclick = () => setLang(QP_LANG === 'en' ? 'zh' : 'en');
  $('#mLangNow').textContent = QP_LANG === 'en' ? 'EN' : '中';

  bindSub();
  bindAccountControls();

  // 先把主容器显出来再画图：隐藏元素 clientWidth=0，图表会按兜底尺寸建出来
  $('#mapp').hidden = false;
  await loadOHLC();
  loadSignals();
  tickMarket(); setInterval(tickMarket, 30000);

  // 恢复语言切换前的视图
  try {
    const v = sessionStorage.getItem('qp_view');
    sessionStorage.removeItem('qp_view');
    sessionStorage.removeItem('qp_symbol');
    if (v && $('.mtab[data-mv="' + v + '"]')) switchView(v);
  } catch (e) { /* ignore */ }

  $('#mbBoot').classList.add('hide');
  setTimeout(() => { const b = $('#mbBoot'); if (b) b.remove(); }, 500);
  setTimeout(resizeCharts, 60);
}

function bindLangBtn() {
  const btn = $('#mlang');
  btn.textContent = QP_LANG === 'en' ? '中' : 'EN';
  btn.onclick = () => setLang(QP_LANG === 'en' ? 'zh' : 'en');
}

/* 美东时间 → 开市状态（本地算，不依赖 IB 连接） */
function tickMarket() {
  const box = $('#mkt'), txt = $('#mktTxt');
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', weekday: 'short',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(new Date());
    const get = (k) => { const p = parts.find(x => x.type === k); return p ? p.value : ''; };
    const mins = (+get('hour') % 24) * 60 + (+get('minute'));
    const open = !['Sat', 'Sun'].includes(get('weekday')) && mins >= 570 && mins < 960;
    box.classList.toggle('open', open);
    txt.textContent = open ? t('top.clock.open') : t('top.clock.closed');
  } catch (e) {
    txt.textContent = 'SIM';
  }
}

/* ---------------- 视图切换 ---------------- */
function bindTabs() {
  $$('.mtab').forEach(b => b.onclick = () => switchView(b.dataset.mv));
}

function switchView(v) {
  state.view = v;
  $$('.mtab').forEach(x => x.classList.toggle('on', x.dataset.mv === v));
  $$('.mv').forEach(s => s.classList.toggle('on', s.dataset.mv === v));
  $('#mbody').scrollTop = 0;

  if (!state.loaded[v]) {
    state.loaded[v] = true;
    if (v === 'equity') loadEquity($('#mEqSym').value);
    if (v === 'portfolio') loadPortfolio();
    if (v === 'account') { loadAccount(); startAcctPoll(); }
  } else if (v === 'account') {
    loadAccount();
    startAcctPoll();
  }
  if (v !== 'account') stopAcctPoll();
  requestAnimationFrame(() => { resizeCharts(); setTimeout(resizeCharts, 80); });
}

/* ---------------- 标的抽屉 ---------------- */
function renderSymbolList(q) {
  const box = $('#mSheetBody');
  const syms = (state.meta ? state.meta.symbols : []);
  const kw = String(q || '').trim().toUpperCase();
  const list = kw ? syms.filter(s => s.includes(kw)) : syms;
  if (!list.length) {
    box.innerHTML = `<div class="sym-empty">${t('m.noData')}</div>`;
    return;
  }
  box.innerHTML = list.map(s => {
    const sel = s === state.symbol;
    return `<button class="sym-item${sel ? ' on' : ''}" data-sym="${esc(s)}" type="button">
      <b>${esc(s)}</b>
      <span class="s">${sel ? t('chart.tag') : 'US Equity'}</span>
      <span class="p">${sel && state.ohlc ? fmt(state.ohlc.last) : ''}</span>
    </button>`;
  }).join('');
  box.querySelectorAll('.sym-item').forEach(b => b.onclick = () => {
    closeSheet();
    setSymbol(b.dataset.sym);
  });
}

function bindSheet() {
  $('#msymBtn').onclick = () => {
    renderSymbolList($('#mSymSearch').value);
    $('#mSheetMask').classList.add('on');
    $('#mSheet').classList.add('on');
  };
  const close = () => closeSheet();
  $('#mSheetMask').onclick = close;
  $('#mSheetX').onclick = close;
  $('#mSymSearch').oninput = (e) => renderSymbolList(e.target.value);
  $('#mSymSearch').addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      const v = e.target.value.trim().toUpperCase();
      if (v) { closeSheet(); setSymbol(v); e.target.value = ''; }
    }
  });
}
function closeSheet() {
  $('#mSheetMask').classList.remove('on');
  $('#mSheet').classList.remove('on');
}

function setSymbol(s) {
  if (!s || s === state.symbol) return;
  state.symbol = s;
  $('#msymCode').textContent = s;
  $('#mEqSym').value = s;
  loadOHLC(); loadSignals();
  if (state.loaded.equity) loadEquity(s);
  toast(t('toast.switched', { name: s }));
}

/* ---------------- 行情视图 ---------------- */
function bindChartControls() {
  $$('#mBarSeg button').forEach(b => b.onclick = () => setBar(b.dataset.bar));
  $$('#mOvSeg button').forEach(b => b.onclick = () => {
    b.classList.toggle('on');
    state.overlays[b.dataset.ov] = b.classList.contains('on');
    if (state.ohlc) drawPrice();
    if (b.dataset.ov === 'sig' && state.signals && state.charts.candle) {
      try {
        state.charts.candle.setMarkers(state.overlays.sig && state.bar === 'day'
          ? state.signals.markers : []);
      } catch (e) {}
    }
  });
}

function setBar(bar) {
  if (state.bar === bar) return;
  state.bar = bar;
  $$('#mBarSeg button').forEach(b => b.classList.toggle('on', b.dataset.bar === bar));
  if (bar !== 'day') {
    state.overlays.sig = false;
    const sb = $('#mOvSeg button[data-ov="sig"]');
    if (sb) sb.classList.remove('on');
  }
  $('#mreadT').textContent = t('bar.label.' + bar);
  loadOHLC().then(() => { if (bar === 'day') loadSignals(); });
}

async function loadOHLC() {
  const dur = BAR_DUR[state.bar] || state.duration;
  try {
    state.ohlc = await api(`/api/ohlc?symbol=${encodeURIComponent(state.symbol)}` +
      `&duration=${encodeURIComponent(dur)}&bar=${encodeURIComponent(state.bar)}`);
  } catch (e) {
    state.ohlc = null;
    toast(e.message);
    return;
  }
  const d = state.ohlc;
  // 顶栏价格
  $('#msymCode').textContent = d.symbol;
  $('#msymPx').textContent = fmt(d.last);
  const chg = (d.prev_close && isNum(d.last))
    ? (d.last - d.prev_close) / d.prev_close : null;
  const chgEl = $('#msymChg');
  chgEl.textContent = isNum(chg) ? pctSign(chg) : '—';
  chgEl.className = cls(chg);
  $('#mreadT').textContent = barLabel(state.bar);

  drawPrice();
}

function drawPrice() {
  const d = state.ohlc;
  if (!d) return;
  const pel = $('#mPrice'), vel = $('#mVol');

  if (!state.charts.price) {
    const c = mkChart('price', pel, {});
    const vc = mkChart('vol', vel, {
      rightPriceScale: { visible: false },
      timeScale: { borderColor: TVM.border, visible: false },
      handleScroll: false, handleScale: false,
    });
    // 十字光标读数（手机没有悬停，靠长按拖动）
    c.subscribeCrosshairMove((p) => {
      if (!p || !p.time || !state.charts.candle) { showRead(); return; }
      try {
        const bar = p.seriesData.get(state.charts.candle);
        if (bar) showRead(p.time, bar, p.time);
      } catch (e) {}
    });
    c.timeScale().subscribeVisibleLogicalRangeChange(r => {
      if (r && state.charts.vol) {
        try { state.charts.vol.timeScale().setVisibleLogicalRange(r); } catch (e) {}
      }
    });
  }
  const c = state.charts.price, vc = state.charts.vol;

  ['candle', 'ma20', 'ma60', 'bbU', 'bbL'].forEach(k => {
    if (state.charts[k]) {
      try { c.removeSeries(state.charts[k]); } catch (e) {}
      state.charts[k] = null;
    }
  });
  if (state.charts.volSeries) {
    try { vc.removeSeries(state.charts.volSeries); } catch (e) {}
    state.charts.volSeries = null;
  }

  state.charts.candle = c.addCandlestickSeries({
    upColor: TVM.up, downColor: TVM.down,
    borderUpColor: TVM.up, borderDownColor: TVM.down,
    wickUpColor: TVM.up, wickDownColor: TVM.down,
    priceLineColor: TVM.accent, priceLineStyle: 2, priceLineWidth: 1,
  });
  state.charts.candle.setData(d.bars);

  if (state.overlays.ma) {
    state.charts.ma20 = c.addLineSeries({ color: TVM.gold, lineWidth: 1,
      priceLineVisible: false, lastValueVisible: false });
    state.charts.ma20.setData(zip(d.times, d.ma20));
    state.charts.ma60 = c.addLineSeries({ color: '#7b61ff', lineWidth: 1,
      priceLineVisible: false, lastValueVisible: false });
    state.charts.ma60.setData(zip(d.times, d.ma60));
  }
  if (state.overlays.bb) {
    state.charts.bbU = c.addLineSeries({ color: 'rgba(41,98,255,.55)', lineWidth: 1,
      priceLineVisible: false, lastValueVisible: false });
    state.charts.bbU.setData(zip(d.times, d.bb_upper));
    state.charts.bbL = c.addLineSeries({ color: 'rgba(41,98,255,.55)', lineWidth: 1,
      priceLineVisible: false, lastValueVisible: false });
    state.charts.bbL.setData(zip(d.times, d.bb_lower));
  }

  state.charts.volSeries = vc.addHistogramSeries({
    priceFormat: { type: 'volume' }, priceScaleId: '',
  });
  state.charts.volSeries.setData(d.volume);

  c.timeScale().fitContent();
  vc.timeScale().fitContent();

  if (state.overlays.sig && state.bar === 'day' && state.signals &&
      state.signals.symbol === d.symbol) {
    try { state.charts.candle.setMarkers(state.signals.markers); } catch (e) {}
  }
  showRead();
  resizeCharts();
}

function zip(times, vals) {
  const out = [];
  for (let i = 0; i < times.length; i++) {
    const v = vals[i];
    if (v !== null && v !== undefined && !Number.isNaN(v)) out.push({ time: times[i], value: v });
  }
  return out;
}

/* O/H/L/C 读数：默认显示最后一根，拖动十字光标时跟随 */
function showRead(time, bar) {
  const d = state.ohlc;
  if (!d || !d.bars.length) return;
  let b = bar, tlabel = '';
  if (time) {
    b = bar || d.bars.find(x => x.time === time) || d.bars[d.bars.length - 1];
    const i = d.times.indexOf(time);
    tlabel = i >= 0 ? (d.time_labels[i] || time) : '';
  } else {
    b = d.bars[d.bars.length - 1];
    tlabel = d.time_labels[d.time_labels.length - 1];
  }
  if (!b) return;
  $('#mO').textContent = fmt(b.open);
  $('#mH').textContent = fmt(b.high);
  $('#mL').textContent = fmt(b.low);
  $('#mC').textContent = fmt(b.close);
  if (tlabel) $('#mreadT').textContent = String(tlabel);
}

/* ---------------- 信号 + 指标 ---------------- */
async function loadSignals() {
  let d;
  try {
    d = await api(`/api/signals?symbol=${encodeURIComponent(state.symbol)}` +
      `&strategy=${encodeURIComponent(state.strategy)}` +
      `&duration=${encodeURIComponent(BAR_DUR[state.bar] || state.duration)}`);
  } catch (e) { toast(e.message); return; }
  state.signals = d;

  if (state.overlays.sig && state.bar === 'day' && state.charts.candle) {
    try { state.charts.candle.setMarkers(d.markers || []); } catch (e) {}
  }

  const m = d.metrics || {}, b = d.benchmark_metrics || {};
  const bench = t('m.bench');
  const items = [
    { k: '年化收益率', v: m['年化收益率'], f: 'pct', sub: `${bench} ${pct(b['年化收益率'], 1)}`, c: cls(m['年化收益率']) },
    { k: '夏普比率', v: m['夏普比率'], f: 'num', sub: `${bench} ${fmt(b['夏普比率'])}`, c: cls(m['夏普比率']) },
    { k: '最大回撤', v: m['最大回撤'], f: 'pct', sub: `${bench} ${pct(b['最大回撤'], 1)}`, c: 'down' },
    { k: '卡玛比率', v: m['卡玛比率'], f: 'num', sub: t('card.calmar.sub'), c: cls(m['卡玛比率']) },
    { k: '胜率', v: m['胜率'], f: 'pct', sub: t('card.bench') + ' ' + pct(b['胜率'], 0) },
    { k: '交易次数', v: m['交易次数'], f: 'int', sub: t('card.bench') + ' ' + (isNum(b['交易次数']) ? Math.round(b['交易次数']) : '—') },
  ];
  $('#mMetrics').innerHTML = items.map((it, i) =>
    `<div class="mi" style="animation-delay:${i * 40}ms">
       <div class="k">${label(it.k)}</div>
       <div class="v ${it.c || ''}">${it.f === 'pct' ? pctSign(it.v) : it.f === 'int' ? (isNum(it.v) ? Math.round(it.v) : '—') : fmt(it.v)}</div>
       <div class="s">${it.sub}</div>
     </div>`).join('');

  $('#mParams').textContent = Object.entries(d.params || {})
    .map(([k, v]) => `${k}=${v}`).join(' ');
  $('#mBtRange').textContent = `${d.symbol} · ${stratName(d.strategy)}`;

  drawSigEq(d);
}

function drawSigEq(d) {
  const el = $('#mSigEq');
  if (!state.charts.sigEq) state.charts.sigEq = mkChart('sigEq', el, {});
  const c = state.charts.sigEq;
  ['sigLine', 'benchLine'].forEach(k => {
    if (state.charts[k]) { try { c.removeSeries(state.charts[k]); } catch (e) {} state.charts[k] = null; }
  });
  const eq = cleanSeries(d.equity);
  const bm = cleanSeries(d.benchmark);
  state.charts.sigLine = c.addAreaSeries({
    lineColor: TVM.accent, lineWidth: 2,
    topColor: 'rgba(41,98,255,.26)', bottomColor: 'rgba(41,98,255,.01)',
    priceLineVisible: false, lastValueVisible: true,
  });
  state.charts.sigLine.setData(eq);
  state.charts.benchLine = c.addLineSeries({
    color: TVM.flat, lineWidth: 1, lineStyle: 2,
    priceLineVisible: false, lastValueVisible: false,
  });
  state.charts.benchLine.setData(bm);
  c.timeScale().fitContent();

  const last = eq.length ? eq[eq.length - 1].value : null;
  const lastB = bm.length ? bm[bm.length - 1].value : null;
  $('#mSigLegend').innerHTML =
    `<span class="lg"><i style="background:${TVM.accent}"></i>${esc(stratName(d.strategy))}
       <b>${fmt(last, 0)}</b></span>
     <span class="lg"><i style="background:${TVM.flat}"></i>${t('m.bench')}
       <b>${fmt(lastB, 0)}</b></span>`;
  resizeCharts();
}

/* ---------------- 净值对比 ---------------- */
async function loadEquity(symbol) {
  let d;
  try {
    d = await api(`/api/equity_curves?symbol=${encodeURIComponent(symbol || state.symbol)}&top_n=8`);
  } catch (e) { toast(e.message); return; }
  const el = $('#mEqChart');
  if (!d.series || !d.series.length) {
    el.innerHTML = `<div class="mempty">${t('eq.empty')}</div>`;
    $('#mEqLegend').innerHTML = '';
    return;
  }
  if (!state.charts.eq) {
    el.innerHTML = '';                     // 清掉空态占位
    state.charts.eq = mkChart('eq', el, {});
  }
  const c = state.charts.eq;
  if (state._eqSeries) state._eqSeries.forEach(s => { try { c.removeSeries(s.ls); } catch (e) {} });
  state._eqSeries = [];

  d.series.forEach((s, i) => {
    const pts = cleanSeries(s.data);
    if (pts.length < 2) return;
    const color = PALETTE[i % PALETTE.length];
    const ls = c.addLineSeries({
      color, lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
      crosshairMarkerVisible: true,
    });
    ls.setData(pts);
    state._eqSeries.push({ name: s.name, color, ls });
  });
  c.timeScale().fitContent();

  $('#mEqLegend').innerHTML = state._eqSeries.map(s =>
    `<span class="lg" data-n="${esc(s.name)}"><i style="background:${s.color}"></i>${esc(stratName(s.name))}</span>`).join('');
  $$('#mEqLegend .lg').forEach(item => item.onclick = () => {
    const name = item.dataset.n;
    const rec = state._eqSeries.find(x => x.name === name);
    if (!rec) return;
    rec.visible = !rec.visible;
    item.classList.toggle('off', !rec.visible);
    try { rec.ls.applyOptions({ visible: rec.visible }); } catch (e) {}
  });
  resizeCharts();
}

/* ---------------- 组合 ---------------- */
async function loadPortfolio() {
  const strat = $('#mPfStrat').value, scheme = $('#mPfScheme').value;
  let d;
  try {
    d = await api(`/api/portfolio_equity?strategy=${encodeURIComponent(strat)}` +
      `&scheme=${encodeURIComponent(scheme)}`);
  } catch (e) { toast(e.message); return; }
  state.pf = d;

  const m = d.metrics || {};
  const cards = [
    { k: t('pf.card.cagr'), v: pctSign(m['年化收益率']), c: cls(m['年化收益率']) },
    { k: t('pf.card.sharpe'), v: fmt(m['夏普比率']), c: cls(m['夏普比率']) },
    { k: t('pf.card.mdd'), v: pct(m['最大回撤'], 2), c: 'down' },
    { k: t('pf.card.calmar'), v: fmt(m['卡玛比率']), c: cls(m['卡玛比率']) },
  ];
  $('#mPfCards').innerHTML = cards.map(x =>
    `<div class="mi"><div class="k">${esc(x.k)}</div>
      <div class="v ${x.c || ''}">${x.v}</div></div>`).join('');

  // 组合净值 vs 等权基准
  const el = $('#mPfEq');
  if (!state.charts.pf) state.charts.pf = mkChart('pf', el, {});
  const c = state.charts.pf;
  if (state._pfSeries) state._pfSeries.forEach(s => { try { c.removeSeries(s); } catch (e) {} });
  const l1 = c.addLineSeries({ color: TVM.accent, lineWidth: 2,
    priceLineVisible: false, lastValueVisible: true });
  l1.setData(cleanSeries(d.equity));
  const l2 = c.addLineSeries({ color: TVM.flat, lineWidth: 1, lineStyle: 2,
    priceLineVisible: false, lastValueVisible: false });
  l2.setData(cleanSeries(d.benchmark));
  state._pfSeries = [l1, l2];
  c.timeScale().fitContent();
  $('#mPfLegend').innerHTML =
    `<span class="lg"><i style="background:${TVM.accent}"></i>${esc(stratName(d.strategy))}
       <b>${(d.symbols || []).length}</b></span>
     <span class="lg"><i style="background:${TVM.flat}"></i>${t('pf.benchmark')}</span>`;

  drawWeights(d);
  renderSchemeTable(strat, scheme);
  resizeCharts();
}

function drawWeights(d) {
  const el = $('#mPfW');
  if (!state.charts.pfW) { state.charts.pfW = mkChart('pfW', el, {
    localization: { priceFormatter: (p) => (p * 100).toFixed(0) + '%' },
  }); }
  const wc = state.charts.pfW;
  // 重绘式堆叠：隐藏某层时上层要落下来，直接 visible:false 会留空洞
  (state._wSeries || []).forEach(ls => { try { wc.removeSeries(ls); } catch (e) {} });
  state._wSeries = [];

  const raw = (d.weights || []).map(w => ({
    name: w.name, color: w.color, current: w.current || 0,
    map: new Map(cleanSeries(w.data).map(p => [p.time, p.value])),
  })).filter(w => w.map.size);
  const times = [...new Set(raw.flatMap(w => [...w.map.keys()]))].sort();
  const avg = (w) => [...w.map.values()].reduce((a, b) => a + b, 0) / w.map.size;
  const order = [...raw].sort((a, b) => avg(b) - avg(a));

  state.wHidden = state.wHidden || new Set();
  const cum = new Array(times.length).fill(0);
  order.forEach(w => {
    if (state.wHidden.has(w.name)) return;
    for (let i = 0; i < times.length; i++) cum[i] += (w.map.get(times[i]) || 0);
    const data = cleanSeries(times.map((t, i) => ({ time: t, value: +cum[i].toFixed(4) })));
    if (data.length < 2) return;
    const ls = wc.addAreaSeries({
      lineColor: w.color, lineWidth: 1,
      topColor: hexA(w.color, 0.62), bottomColor: hexA(w.color, 0.62),
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    ls.setData(data);
    state._wSeries.push(ls);
  });
  wc.timeScale().fitContent();

  $('#mPfWeightNow').textContent =
    (d.weights || []).filter(w => w.current > 0.005)
      .map(w => `${stratName(w.name)} ${(w.current * 100).toFixed(0)}%`).join(' · ')
    || t('pf.flat');

  const box = $('#mPfWLegend');
  box.innerHTML = (d.weights || []).map(w =>
    `<span class="lg${state.wHidden.has(w.name) ? ' off' : ''}" data-n="${esc(w.name)}">
       <i style="background:${w.color}"></i>${esc(stratName(w.name))}</span>`).join('');
  box.querySelectorAll('.lg').forEach(item => item.onclick = () => {
    const n = item.dataset.n;
    if (state.wHidden.has(n)) state.wHidden.delete(n); else state.wHidden.add(n);
    drawWeights(state.pf);
    resizeCharts();
  });
}

async function renderSchemeTable(strat, cur) {
  let d = state.schemes;
  if (!d) {
    try { d = state.schemes = await api('/api/portfolio_schemes'); } catch (e) { return; }
  }
  const rows = (d.rows || []).filter(r => r['策略'] === strat);
  if (!rows.length) { $('#mPfTbl').innerHTML = `<div class="mempty">${t('m.noData')}</div>`; return; }
  const cols = ['权重方案', '年化收益率', '夏普比率', '最大回撤', '卡玛比率'];
  const head = `<thead><tr>${cols.map(c => `<th>${label(c)}</th>`).join('')}</tr></thead>`;
  const body = rows.map(r =>
    `<tr><td>${esc(schemeLabel(r['权重方案']))}${r['权重方案'] === cur ? ' ●' : ''}</td>
      <td class="${cls(r['年化收益率'])}">${pct(r['年化收益率'], 1)}</td>
      <td>${fmt(r['夏普比率'])}</td>
      <td class="down">${pct(r['最大回撤'], 1)}</td>
      <td>${fmt(r['卡玛比率'])}</td></tr>`).join('');
  $('#mPfTbl').innerHTML = `<table class="mtbl">${head}<tbody>${body}</tbody></table>`;
}

function schemeLabel(s) {
  const k = 'scheme.' + s, v = t(k);
  return v === k ? s : v;
}

/* ---------------- 账户 ---------------- */
function bindAccountControls() {
  if (document.addEventListener) {
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) stopAcctPoll();
      else if (state.view === 'account') startAcctPoll();
    });
  }
}

function startAcctPoll() {
  stopAcctPoll();
  state.acct.timer = setInterval(() => {
    if (state.view === 'account' && !document.hidden) loadAccount(true);
  }, 12000);
}
function stopAcctPoll() {
  clearInterval(state.acct.timer);
  state.acct.timer = null;
}

async function loadAccount(quiet) {
  let d;
  try {
    d = await api('/api/account/board', { cache: 'no-store' });
  } catch (e) {
    if (!quiet) toast(e.message);
    return;
  }
  state.acct.data = d;
  renderHero(d);
  renderStat4(d);
  renderAlloc(d);
  renderPositions(d);
  renderAcctCurve(d);
  loadOrders();
}

function renderHero(d) {
  const ok = !!d.ok;
  const conn = $('#mConn');
  conn.classList.toggle('on', ok);
  const mkt = ok ? (d.market_state || {}) : {};
  conn.querySelector('span').textContent = !ok ? t('acct.conn.off')
    : (mkt.open ? t('acct.conn.on') : t('acct.conn.closed'));

  if (!ok) {
    $('#mNetliq').textContent = '—';
    $('#mNetliq').className = 'mhero-v';
    $('#mHeroTs').textContent = d.error || t('acct.noTwS');
    $('#mDayPnl').textContent = '—';
    $('#mPnl').textContent = '—';
    $('#mLev').textContent = '—';
    return;
  }
  const tot = d.totals || {};
  $('#mNetliq').textContent = nf(tot['净值'], 0);
  $('#mHeroCur').textContent = tot['基准币种'] || 'HKD';
  $('#mHeroTs').textContent = d.ts || '';
  const day = tot['当日盈亏'], pnl = tot['持仓盈亏'];
  const dayEl = $('#mDayPnl'), pnlEl = $('#mPnl');
  dayEl.textContent = signed(day, 0);
  dayEl.className = cls(day);
  pnlEl.textContent = signed(pnl, 0);
  pnlEl.className = cls(pnl);
  $('#mLev').textContent = fmt(tot['杠杆'], 2) + '×';
}

function renderStat4(d) {
  const tot = (d.ok && d.totals) || {};
  const items = [
    [t('card.cash'), nf(tot['现金'], 0)],
    [t('card.mv'), nf(tot['持仓市值'], 0)],
    [t('card.avail'), nf(tot['可用资金'], 0)],
    [t('card.bp'), nf(tot['购买力'], 0)],
  ];
  $('#mStat4').innerHTML = items.map(([k, v]) =>
    `<div class="mi"><div class="k">${esc(k)}</div><div class="v">${v}</div></div>`).join('');
}

function renderAlloc(d) {
  const bar = $('#mAllocBar'), leg = $('#mAllocLegend');
  const tot = (d.ok && d.totals) || {};
  const netliq = Number(tot['净值'] || 0);
  const cash = Number(tot['现金'] || 0);
  const gross = Number(tot['持仓市值'] || 0);
  $('#mAllocNote').textContent = d.ok
    ? t('m.allocNote', { cur: tot['基准币种'] || 'HKD' }) : '';
  if (!d.ok || netliq <= 0) {
    bar.innerHTML = ''; leg.innerHTML = `<span class="muted">${t('acct.unavailable')}</span>`;
    return;
  }
  const segs = [{ name: t('acct.cash'), v: cash, color: '#39415c' }];
  (d.rows || []).forEach((r, i) => {
    const mv = Number(r['市值'] || 0);
    if (!mv) return;
    segs.push({
      name: r['标的'],
      v: Math.abs(mv) * Number(tot['汇率'] || 1),
      color: PALETTE[i % PALETTE.length],
    });
  });
  const other = Math.max(0, netliq - cash - gross);
  if (other > netliq * 0.005) segs.push({ name: t('acct.other'), v: other, color: '#4a5163' });

  const total = segs.reduce((a, b) => a + b.v, 0) || 1;
  bar.innerHTML = segs.map(s =>
    `<i style="background:${s.color};width:0" data-w="${(s.v / total * 100).toFixed(2)}"></i>`).join('');
  leg.innerHTML = segs.map(s =>
    `<span class="lg"><i style="background:${s.color}"></i>${esc(s.name)}
       <b>${(s.v / total * 100).toFixed(1)}%</b></span>`).join('');
  requestAnimationFrame(() => {
    $$('#mAllocBar i').forEach(el => { el.style.width = el.dataset.w + '%'; });
  });
}

function renderPositions(d) {
  const box = $('#mPosList');
  const rows = (d.ok && d.rows) || [];
  if (!rows.length) {
    box.innerHTML = `<div class="mempty">${d.ok ? t('m.posEmpty') : t('acct.noTwS')}</div>`;
    $('#mPosSum').textContent = '';
    return;
  }
  const tot = d.totals || {};
  $('#mPosSum').textContent = `${nf(tot['持仓市值'], 0)} · ${signed(tot['持仓盈亏'], 0)}`;
  const maxW = Math.max(...rows.map(r => Math.abs(Number(r['权重%'] || 0))), 0.0001);
  box.innerHTML = rows.map((r, i) => {
    const w = Number(r['权重%'] || 0);
    const pnlv = Number(r['浮动盈亏'] || 0);
    const pnlp = Number(r['盈亏%'] || 0);
    return `<div class="pos-row" style="animation:mvIn .3s var(--ease) ${i * 45}ms both">
      <div class="pos-l">
        <div class="s"><b>${esc(r['标的'])}</b><em>${esc(r['币种'] || '')}</em></div>
        <div class="m">
          <span>${nf(r['股数'], 0)} ${label('股数')}</span>
          <span>${fmt(r['成本价'])} → ${fmt(r['现价'])}</span>
        </div>
        <div class="pos-wbar"><i style="width:0" data-w="${(Math.abs(w) / maxW * 100).toFixed(1)}"></i></div>
      </div>
      <div class="pos-r">
        <div class="v">${nf(r['市值'], 0)}</div>
        <div class="p ${cls(pnlv)}">${signed(pnlv, 0)} · ${pctSign(pnlp)}</div>
      </div>
    </div>`;
  }).join('');
  requestAnimationFrame(() => {
    $$('#mPosList .pos-wbar i').forEach(el => { el.style.width = el.dataset.w + '%'; });
  });
}

function renderAcctCurve(d) {
  const hist = (d.ok && d.history) || {};
  const times = hist.times || [];
  const vals = hist['净值'] || [];
  const el = $('#mAcctCurve');
  $('#mCurveTag').textContent = d.ok
    ? t('acctCurve.tag', { n: times.length }) : t('acctCurve.none');

  const seen = new Map();
  times.forEach((t, i) => {
    const ts = toTs(t), v = Number(vals[i]);
    if (ts === null || !isFinite(v)) return;
    seen.set(ts, v);
  });
  const pts = Array.from(seen.keys()).sort((a, b) => a - b)
    .map(ts => ({ time: ts, value: seen.get(ts) }));

  if (pts.length < 2) {
    if (state.charts.acct) { try { state.charts.acct.remove(); } catch (e) {} state.charts.acct = null; }
    el.innerHTML = `<div class="mempty">${t('acctCurve.empty')}</div>`;
    return;
  }
  if (!state.charts.acct) {
    el.innerHTML = '';                     // 清掉空态占位，否则会留在画布上层
    state.charts.acct = mkChart('acct', el, {
      timeScale: Object.assign(chartBase().timeScale, { timeVisible: true }),
    });
  }
  const c = state.charts.acct;
  if (state.charts.acctLine) { try { c.removeSeries(state.charts.acctLine); } catch (e) {} }
  state.charts.acctLine = c.addAreaSeries({
    lineColor: TVM.accent, lineWidth: 2,
    topColor: 'rgba(41,98,255,.28)', bottomColor: 'rgba(41,98,255,.01)',
    priceLineVisible: true, lastValueVisible: true,
  });
  state.charts.acctLine.setData(pts);
  c.timeScale().fitContent();
  resizeCharts();
}

async function loadOrders() {
  let d;
  try { d = await api('/api/db/orders?limit=8'); } catch (e) { return; }
  const rows = d.rows || [];
  const box = $('#mOrderList');
  if (!rows.length) { box.innerHTML = `<div class="mempty">${t('m.ordEmpty')}</div>`; return; }
  box.innerHTML = rows.map(r => {
    const buy = r['动作'] === 'BUY';
    return `<div class="ord-row">
      <span class="${buy ? 'tag-buy' : 'tag-sell'}">${buy ? 'BUY' : 'SELL'}</span>
      <span class="n">${esc(r['标的'])}</span>
      <span class="t">${esc(String(r['时间'] || '').slice(0, 16))}</span>
      <span class="muted">${nf(r['股数'], 0)}</span>
      <span class="st">${esc(r['状态'] || '')}</span>
    </div>`;
  }).join('');
}

/* ---------------- 更多 / 二级页 ---------------- */
const SUB = {
  rank: {
    title: () => t('nav.rank'),
    load: loadSubRank,
  },
  risk: {
    title: () => t('nav.risk'),
    load: loadSubRisk,
  },
  watch: {
    title: () => t('nav.watch'),
    load: loadSubWatch,
    act: () => ({ label: t('watch.rescan'), fn: () => loadSubWatch(true) }),
  },
  db: {
    title: () => t('nav.db'),
    load: loadSubDb,
  },
};

function bindSub() {
  $('#msubBack').onclick = closeSub;
  $$('.mrow[data-sub]').forEach(b => b.onclick = () => openSub(b.dataset.sub));
}

function openSub(name) {
  const cfg = SUB[name];
  if (!cfg) return;
  $('#msubTitle').textContent = cfg.title();
  const act = $('#msubAct');
  if (cfg.act) {
    const a = cfg.act();
    act.hidden = false;
    act.textContent = a.label;
    act.onclick = a.fn;
  } else {
    act.hidden = true;
  }
  $('#msubBody').innerHTML = `<div class="mempty">${t('common.loading')}</div>`;
  $('#msub').classList.add('on');
  cfg.load(false);
}
function closeSub() { $('#msub').classList.remove('on'); }

/* —— 排名 —— */
async function loadSubRank() {
  let d = state.rank;
  if (!d) {
    try { d = state.rank = await api('/api/ranking'); } catch (e) { toast(e.message); return; }
  }
  const metrics = [
    ['夏普比率', 'rank.by.sharpe'], ['卡玛比率', 'rank.by.calmar'],
    ['年化收益率', 'rank.by.cagr'], ['最大回撤', 'rank.by.mdd'],
  ];
  const cur = state.rankMetric || '夏普比率';
  const rows = (d.rows || []).slice().sort((a, b) => (Number(b[cur]) || -9e9) - (Number(a[cur]) || -9e9));
  const maxAbs = Math.max(...rows.map(r => Math.abs(Number(r[cur]) || 0)), 0.0001);

  $('#msubBody').innerHTML = `
    <div class="mchips" id="mRankChips" style="margin-bottom:10px">
      ${metrics.map(([k, key]) => `<button class="mchip-i${k === cur ? ' on' : ''}" data-k="${esc(k)}">${t(key)}</button>`).join('')}
    </div>
    <div class="mlist-card" id="mRankList">
      ${rows.map((r, i) => {
        const v = Number(r[cur]);
        const w = Math.abs(v) / maxAbs * 100;
        const isPct = cur === '年化收益率' || cur === '最大回撤';
        return `<div class="rank-item" data-s="${esc(r['策略'])}">
          <div class="rank-no${i < 3 ? ' top' : ''}">${i + 1}</div>
          <div class="rank-mid">
            <div class="n">${esc(stratName(r['策略']))}</div>
            <div class="b"><i style="width:${w.toFixed(1)}%"></i></div>
            <div class="m"><span>夏普 ${fmt(r['夏普比率'])}</span>
              <span>回撤 ${pct(r['最大回撤'], 1)}</span>
              <span>胜率 ${pct(r['胜率'], 0)}</span></div>
          </div>
          <div class="rank-v ${cls(v)}">${isPct ? pct(v, 1) : fmt(v)}</div>
        </div>`;
      }).join('')}
    </div>
    <p class="mhint">${t('m.rank.hint')}</p>`;

  $$('#mRankChips .mchip-i').forEach(b => b.onclick = () => {
    state.rankMetric = b.dataset.k;
    loadSubRank();
  });
  $$('#mRankList .rank-item').forEach(it => it.onclick = () => {
    const name = it.dataset.s;
    state.strategy = name;
    $$('#mStratChips .mchip-i').forEach(x => x.classList.toggle('on', x.dataset.n === name));
    $('#mPfStrat').value = name;
    closeSub();
    switchView('chart');
    loadSignals();
    toast(t('toast.switched', { name: stratName(name) }));
  });
}

/* —— 风控 —— */
async function loadSubRisk() {
  let sig = state.sig, ab = state.ab;
  try {
    if (!sig) sig = state.sig = await api('/api/significance');
    if (!ab) ab = state.ab = await api('/api/riskab');
  } catch (e) { toast(e.message); return; }

  const srows = sig.rows || [];
  const cols = ['策略', '夏普比率', 'PSR(>0)', 'DSR', '样本数'];
  const head = `<thead><tr>${cols.map(c => `<th>${label(c)}</th>`).join('')}</tr></thead>`;
  const body = srows.map(r => `<tr>
      <td>${esc(stratName(r['策略']))}</td>
      <td class="${cls(r['夏普比率'])}">${fmt(r['夏普比率'])}</td>
      <td class="${Number(r['PSR(>0)']) > 0.95 ? 'up' : ''}">${fmt(r['PSR(>0)'], 3)}</td>
      <td>${fmt(r['DSR'], 3)}</td>
      <td>${isNum(r['样本数']) ? Math.round(r['样本数']).toLocaleString() : '—'}</td>
    </tr>`).join('');

  // 风控 A/B → 透视成「策略 × 方案」的夏普改善矩阵
  const rows = ab.rows || [];
  const schemes = Array.from(new Set(rows.map(r => r['风控方案']))).filter(s => s !== '无风控');
  const piv = {};
  rows.forEach(r => {
    piv[r['策略']] = piv[r['策略']] || {};
    piv[r['策略']][r['风控方案']] = r['夏普改善'];
  });
  const head2 = `<thead><tr><th>${label('策略')}</th>${schemes.map(s => `<th>${esc(stratName(s))}</th>`).join('')}</tr></thead>`;
  const body2 = Object.entries(piv).map(([k, v]) =>
    `<tr><td>${esc(stratName(k))}</td>${schemes.map(s => {
      const x = v[s];
      return `<td class="${cls(x)}">${isNum(x) ? (x >= 0 ? '+' : '') + fmt(x) : '—'}</td>`;
    }).join('')}</tr>`).join('');

  $('#msubBody').innerHTML = `
    <div class="msec-h" style="margin-top:2px"><h3>${t('panel.sig')}</h3>
      <span class="msec-s">${t('sig.tag')}</span></div>
    <div class="mscroll mcard"><table class="mtbl">${head}<tbody>${body || `<tr><td>${t('m.noData')}</td></tr>`}</tbody></table></div>
    <p class="mhint">${t('m.risk.hint')}</p>
    <div class="msec-h" style="margin-top:18px"><h3>${t('panel.riskab')}</h3>
      <span class="msec-s">${t('riskab.tag')}</span></div>
    <div class="mscroll mcard"><table class="mtbl">${head2}<tbody>${body2 || `<tr><td>${t('m.noData')}</td></tr>`}</tbody></table></div>
    <p class="mhint">${t('riskab.hint')}</p>`;
}

/* —— 信号监控 —— */
async function loadSubWatch(force) {
  const box = $('#msubBody');
  if (force) box.innerHTML = `<div class="mempty">${t('common.loading')}</div>`;
  let d;
  try {
    d = await api('/api/watchlist' + (force ? '?_=' + Date.now() : ''));
  } catch (e) { toast(e.message); return; }
  const rows = d.rows || [];
  if (!rows.length) {
    box.innerHTML = `<div class="mempty">${t('watch.empty')}</div>`;
    return;
  }
  const stratKeys = (state.meta && state.meta.strategy_names) || [];
  box.innerHTML = `<div class="mlist-card">${rows.map(r => {
    const votes = Number(r['看多票数'] || 0);
    const w = Number(r['合计建议仓位'] || 0);
    const tags = stratKeys.filter(k => r[k] !== undefined).map(k =>
      `<span class="${Number(r[k]) > 0 ? 'up' : ''}">${esc(stratName(k).slice(0, 8))}${Number(r[k]) > 0 ? ' 多' : ' 空'}</span>`).join('');
    return `<div class="watch-item" data-s="${esc(r['标的'])}">
      <div class="h"><b>${esc(r['标的'])}</b>
        <span class="v">${fmt(r['最新收盘'])}</span>
        <span class="w ${votes >= 3 ? 'up' : (votes <= 1 ? 'down' : '')}">${votes}/4 · ${(w * 100).toFixed(0)}%</span>
      </div>
      <div class="d">${tags}<span>${esc(r['数据日期'] || '')}</span></div>
    </div>`;
  }).join('')}</div><p class="mhint">${t('m.watch.hint')}</p>`;

  $$('#msubBody .watch-item').forEach(it => it.onclick = () => {
    closeSub();
    setSymbol(it.dataset.s);
    switchView('chart');
  });
}

/* —— 数据库 —— */
/* 表分层顺序与桌面「数据库」页保持一致（后端 分组 是中文，靠这张表映射 i18n） */
const DB_GROUPS = [
  { key: 'db.group.market', tables: ['assets', 'bars', 'ingest_log'] },
  { key: 'db.group.feature', tables: ['feature_sets', 'features'] },
  { key: 'db.group.exp', tables: ['experiments', 'backtest_runs', 'backtest_metrics',
    'equity_points', 'trades'] },
  { key: 'db.group.model', tables: ['model_registry'] },
  { key: 'db.group.live', tables: ['orders', 'position_snapshots', 'account_snapshots'] },
];

async function loadSubDb() {
  let info;
  try { info = await api('/api/db/info'); } catch (e) { toast(e.message); return; }
  if (!info.available) {
    $('#msubBody').innerHTML = `<div class="mempty">${t('db.unavailable')}<br>${esc(info.reason || '')}</div>`;
    return;
  }
  const rows = {};
  (info.tables || []).forEach(t => { rows[t['表']] = t['行数'] || 0; });
  const total = (info.tables || []).length;

  const groupsHtml = DB_GROUPS.map(g => {
    const items = g.tables.filter(n => n in rows);
    if (!items.length) return '';
    return `<div class="msec-h" style="margin-top:16px">
        <h3>${t(g.key)}</h3><span class="msec-s">${items.length}</span></div>
      <div class="db-grid">${items.map(n => `
        <div class="db-t">
          <div class="n">${esc(n)}</div>
          <div class="r${rows[n] ? '' : ' empty'}">${nf(rows[n], 0)}</div>
        </div>`).join('')}</div>`;
  }).join('');

  $('#msubBody').innerHTML = `
    <div class="mstat4" style="margin-top:0">
      <div class="mi"><div class="k">${t('m.db.tables')}</div><div class="v">${total}</div></div>
      <div class="mi"><div class="k">${t('m.db.rows')}</div><div class="v">${nf(info.total_rows, 0)}</div></div>
      <div class="mi"><div class="k">${t('m.db.size')}</div><div class="v">${info.size_mb === null || info.size_mb === undefined ? '—' : info.size_mb + ' MB'}</div></div>
      <div class="mi"><div class="k">${t('m.db.dialect')}</div><div class="v">${esc(info.dialect || '—')}</div></div>
    </div>
    ${groupsHtml}

    <div class="msec-h" style="margin-top:18px"><h3>${t('panel.sql')}</h3>
      <span class="msec-s">${t('sql.tag')}</span></div>
    <textarea id="mSql" class="msql" spellcheck="false"
      placeholder="${esc(t('m.sql.ph'))}">SELECT * FROM bars LIMIT 20</textarea>
    <div style="display:flex; gap:8px; margin-top:9px">
      <select id="mSqlEx" class="msel"></select>
      <button class="mbtn" id="mSqlRun" type="button">${t('m.sql.run')}</button>
    </div>
    <div id="mSqlMsg" class="mhint"></div>
    <div class="mscroll mcard" id="mSqlOut" style="margin-top:8px"></div>`;

  $('#mSqlRun').onclick = () => runSql();
  try {
    const ex = await api('/api/db/examples');
    const list = ex.examples || [];
    $('#mSqlEx').innerHTML = list.map((e, i) =>
      `<option value="${i}">${esc(L() === 'en' ? (e.name_en || e.name) : e.name)}</option>`).join('')
      || `<option>—</option>`;
    $('#mSqlEx').onchange = (ev) => {
      const e = list[Number(ev.target.value)];
      if (!e) return;
      $('#mSql').value = e.sql;
    };
    if (list.length) $('#mSql').value = list[0].sql;
  } catch (e) { /* 示例缺失不影响手写 SQL */ }
}

async function runSql() {
  const sql = ($('#mSql').value || '').trim();
  if (!sql) return;
  const msg = $('#mSqlMsg'), out = $('#mSqlOut');
  msg.textContent = t('common.loading');
  try {
    const r = await fetch('/api/db/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sql, limit: 200 }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
    // 后端 rows 是「数组的数组」，不是对象数组 —— 按下标对齐 columns
    const rows = d.rows || [], cols = d.columns || [];
    msg.textContent = t('m.sql.ok', { n: d.rowcount ?? rows.length });
    if (!rows.length) { out.innerHTML = `<div class="mempty">${t('m.noData')}</div>`; return; }
    out.innerHTML = `<table class="mtbl">
      <thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead>
      <tbody>${rows.map(row => `<tr>${(row || []).map(v => {
        let s = v === null || v === undefined ? '—'
          : (typeof v === 'number'
            ? (Number.isInteger(v) ? v.toLocaleString() : fmt(v, 4))
            : String(v));
        if (s.length > 44) s = s.slice(0, 44) + '…';
        return `<td>${esc(s)}</td>`;
      }).join('')}</tr>`).join('')}</tbody></table>`;
  } catch (e) {
    msg.innerHTML = `<span class="up">${esc(e.message)}</span>`;
    out.innerHTML = '';
  }
}

/* ---------------- 下拉刷新 ---------------- */
function bindPull() {
  const body = $('#mbody'), ind = $('#mpull'), txt = $('#mpullTxt');
  let startY = 0, dy = 0, active = false, TH = 62;

  body.addEventListener('touchstart', (e) => {
    if (body.scrollTop > 0 || $('#msub').classList.contains('on')) return;
    startY = e.touches[0].clientY;
    dy = 0; active = true;
    ind.classList.add('show');
  }, { passive: true });

  body.addEventListener('touchmove', (e) => {
    if (!active) return;
    dy = e.touches[0].clientY - startY;
    if (dy <= 0) { ind.classList.remove('show'); return; }
    const d = Math.min(dy * 0.55, 90);
    ind.style.height = (30 + d * 0.5) + 'px';
    txt.textContent = d >= TH * 0.55 ? t('m.release') : t('m.pull');
  }, { passive: true });

  body.addEventListener('touchend', () => {
    if (!active) return;
    active = false;
    const far = dy * 0.55 >= TH * 0.55;
    ind.style.height = '';
    if (far) {
      ind.classList.add('show', 'spin');
      txt.textContent = t('m.refreshing');
      refreshView().finally(() => {
        setTimeout(() => {
          ind.classList.remove('show', 'spin');
          txt.textContent = t('m.pull');
        }, 320);
      });
    } else {
      ind.classList.remove('show');
    }
    dy = 0;
  });
}

async function refreshView() {
  try {
    if (state.view === 'chart') { await loadOHLC(); await loadSignals(); }
    else if (state.view === 'equity') await loadEquity($('#mEqSym').value);
    else if (state.view === 'portfolio') await loadPortfolio();
    else if (state.view === 'account') await loadAccount(true);
    else if (state.view === 'more') { state.rank = null; }
    toast(t('m.refreshed'));
  } catch (e) { /* 单个接口失败不阻断 */ }
}

/* ---------------- 尺寸变化 ---------------- */
let rzTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(rzTimer);
  rzTimer = setTimeout(resizeCharts, 120);
});
window.addEventListener('orientationchange', () => setTimeout(resizeCharts, 260));

/* ---------------- 起飞 ---------------- */
boot().catch(e => {
  const b = $('#mbBoot');
  if (b) {
    b.innerHTML = `<div class="mb-txt">${t('boot.failed')}：${esc(e.message)}</div>`;
  }
  console.error(e);
});
