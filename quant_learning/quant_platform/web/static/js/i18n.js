/* ==========================================================================
   QuantLab —— 中英文双语字典
   --------------------------------------------------------------------------
   设计要点：
   1. 后端返回的列名 / 策略名 / 账户字段名都是中文，且已写进 CSV、数据库、
      实验配置里，属于数据契约，不能为了界面翻译去改后端。
      所以翻译只发生在展示层：数据仍然用中文 key 取，显示时过 label()。
   2. 静态文案在 index.html 上用 data-i18n 标注，启动时 applyI18n() 批量替换。
   3. 切换语言后整页 reload。界面有 8 个视图 + 大量动态渲染点，
      逐个就地重绘容易漏；reload 是唯一能保证不漏的简单做法，
      当前视图与标的通过 sessionStorage 还原。
   ========================================================================== */

const LANG_KEY = 'qp_lang';

function detectLang() {
  const saved = localStorage.getItem(LANG_KEY);
  if (saved === 'zh' || saved === 'en') return saved;
  // 首次访问跟随浏览器；非英文一律给中文（团队默认语言）
  const nav = (navigator.language || 'zh').toLowerCase();
  return nav.startsWith('en') ? 'en' : 'zh';
}

const QP_LANG = detectLang();

const DICT = {
  zh: {
    'app.title': 'QuantLab · 量化模型实验终端',
    'boot.connecting': '正在连接行情与实验结果…',
    'boot.failed': '启动失败',

    'top.strategy': '策略',
    'top.period': '周期',
    'top.period.2y': '2 年',
    'top.period.5y': '5 年',
    'top.clock.title': '美东时间 · 美股 9:30-16:00 开盘',
    'top.clock.open': '开市',
    'top.clock.closed': '休市',
    'top.symbol.ph': '输入代码…',
    'top.lang': '语言',

    'nav.chart': '图表',
    'nav.equity': '净值对比',
    'nav.portfolio': '组合',
    'nav.rank': '模型排名',
    'nav.risk': '风控与显著性',
    'nav.watch': '信号监控',
    'nav.account': '账户',
    'nav.db': '数据库',

    'side.models': '模型库',
    'side.account': '账户',
    'common.loading': '加载中…',
    'common.refresh': '刷新',
    'common.paused': '已暂停',

    'bar.day': '日',
    'bar.week': '周',
    'bar.hour': '1时',
    'bar.min15': '15分',
    'chart.tag': '{label} {n} 根 · {start} ~ {end}',
    'bar.label.day': '日线',
    'bar.label.week': '周线',
    'bar.label.hour': '1小时',
    'bar.label.min15': '15分钟',
    'ov.ma': 'MA',
    'ov.bb': '布林',
    'ov.sig': '信号',
    'ov.vol': '量',

    'panel.btMetrics': '策略回测指标',
    'panel.equity': '净值曲线对比',
    'eq.tag': '{symbol} · 前 {n} 个模型 · 起点归一 100',
    'eq.empty': '该标的暂无净值曲线',

    'panel.portfolio': '组合回测',
    'pf.tag': '{strategy} · {scheme} · {n} 标的',
    'card.bench': '基准',
    'card.calmar.sub': '年化收益 / 最大回撤',
    'panel.weights': '标的权重',
    'weights.tag': '逐日权重堆叠 · 点击图例可隐藏',
    'panel.schemes': '权重方案对比',
    'schemes.tag': 'enhanced 实验结果',
    'schemes.hint': '实测结论：6 个策略里等权全部最优；波动率倒数/风险平价/波动率目标结果一致且更差 —— 都在系统性压低仓位。',
    'pf.weightNow': '当前权重：',
    'pf.flat': '空仓',
    'pf.benchmark': '等权买入持有基准',
    'pf.series': '组合',
    'pf.benchSeries': '基准',
    'pf.card.cagr': '组合年化',
    'pf.card.cagr.sub': '基准=等权买入持有',
    'pf.card.sharpe': '组合夏普',
    'pf.card.sharpe.sub': '单标的平均约 0.55',
    'pf.card.mdd': '最大回撤',
    'pf.card.mdd.sub': '组合分散化后显著收窄',
    'pf.card.calmar': '卡玛比率',
    'pf.card.calmar.sub': '年化 / 回撤',

    'scheme.equal': '等权',
    'scheme.signal': '信号加权',
    'scheme.vol_inverse': '波动率倒数',
    'scheme.risk_parity': '风险平价',
    'scheme.vol_target': '波动率目标',

    'panel.rank': '模型横向排名',
    'rank.tag': '跨 8 标的平均',
    'rank.by.sharpe': '按夏普',
    'rank.by.calmar': '按卡玛',
    'rank.by.cagr': '按年化',
    'rank.by.mdd': '按回撤',

    'panel.sig': '统计显著性体检',
    'sig.tag': '组合层',
    'sig.hint': 'PSR = 真实夏普 > 0 的概率（>0.95 才算显著）；DSR 扣除「试了很多次」的选择偏差；SR0 是纯靠试次数能刷出的期望虚高线。',
    'panel.riskab': '风控 A/B：夏普变化',
    'riskab.tag': '相对无风控',
    'riskab.hint': '正值 = 风控有效。结论：风控只对高暴露趋势策略有效，对本来就低暴露的择时策略是负优化。',

    'panel.watch': '今日信号扫描',
    'watch.tag': '只读',
    'watch.rescan': '重新扫描',
    'watch.summary': '{n} 个标的 · 4 模型投票',
    'watch.empty': '暂无数据',
    'panel.paper': '模拟盘状态',
    'paper.empty': '尚无模拟盘运行记录',
    'paper.risk': '风控',
    'paper.orders': '订单',
    'paper.symbols': '标的',
    'paper.weights': '权重',
    'paper.none': '无',

    'panel.acct': '账户总览',
    'acct.conn.off': '未连接',
    'acct.conn.on': '已连接 · 交易中',
    'acct.conn.closed': '已连接 · 休市',
    'acct.auto': '自动刷新',
    'acct.next': '{s}s 后刷新',
    'acct.connStatus': '连接状态',
    'acct.unavailable': '不可用',
    'acct.noTwS': '无法连接 TWS / Gateway',
    'acct.alloc': '资产配置',
    'acct.other': '其它',
    'acct.cash': '现金',

    'panel.positions': '持仓明细',
    'positions.tag': '市值按最新价 · 盈亏按成本价 · 标的原币种',
    'positions.hint': '账户基准币种为 HKD，美股标的以 USD 计价，逐行数值保留标的原币种；账户层面汇总已按汇率折算。',
    'positions.none': '当前无持仓',
    'positions.sum': '持仓市值 {gross} · 浮动盈亏 {pnl}',

    'panel.acctCurve': '账户净值曲线',
    'acctCurve.tag': '来自 account_snapshots · {n} 个采样点',
    'acctCurve.none': '无数据',
    'acctCurve.empty': '快照不足（需要至少 2 条）。刷新几次看板即可累积，或常驻运行 scripts/account_daemon.py。',
    'acctCurve.hint': '看板每被访问一次会节流写入一条快照（最快 60s 一条），曲线因此随使用自然生长；也可以在后台常驻采样脚本让它更密。',
    'panel.orders': '最近委托',
    'orders.tag': 'orders 表',

    'card.netliq': '账户净值',
    'card.netliq.sub': '{cur} · 含持仓与现金',
    'card.cash': '账面现金',
    'card.cash.sub': '占净值 {p}',
    'card.avail': '可用资金',
    'card.avail.sub': '可立即下单',
    'card.bp': '购买力',
    'card.bp.sub': '含保证金杠杆',
    'card.mv': '持仓市值',
    'card.mv.sub': '{n} 个持仓',
    'card.pnl': '持仓盈亏',
    'card.pnl.sub': '按成本价计算',
    'card.day': '当日盈亏',
    'card.day.sub': '对比今日首个快照',
    'card.day.sub2': 'IB 未提供该字段',
    'card.realized': '已实现盈亏',
    'card.realized.sub': '已平仓落袋',
    'card.lev': '杠杆',
    'card.lev.sub': '持仓市值 / 净值',

    'panel.dbOverview': '数据库总览',
    'db.unavailable': '不可用',
    'db.noSqlalchemy': '未安装 sqlalchemy',
    'db.dialect': '方言',
    'db.tables': '表数量',
    'db.rows': '总行数',
    'db.size': '文件大小',
    'db.singleFile': '单文件，零配置',
    'db.clientServer': '客户端/服务器',
    'db.structure': '14 张表 · 5 层结构',
    'db.allRows': '全部表累计',
    'db.sqliteFile': 'SQLite 数据文件',
    'db.tag': '{dialect} · {n} 行',
    'panel.sql': 'SQL 查询',
    'sql.tag': '只读 · SELECT / WITH',
    'sql.run': '执行 ⌘↵',
    'sql.ph': 'SELECT * FROM bars LIMIT 20',
    'sql.pick': '— 选择示例查询 —',
    'sql.enter': '请输入 SQL',
    'sql.error': '错误：',
    'sql.failed': '请求失败：',
    'sql.rows': '{n} 行 · {ms} ms',
    'panel.er': '数据模型（ER）',
    'er.tag': '14 张表 · 5 层',

    'toast.reqFail': '请求失败：',
    'toast.switched': '已切换到 {name}',
    'toast.bar': '已切换到{label}',
    'toast.barNote': '（信号回测仅在日线）',

    'db.group.market': '行情数据 · API 抓取',
    'db.group.feature': '训练集 · 特征工程',
    'db.group.exp': '实验与回测',
    'db.group.model': '模型注册表',
    'db.group.live': '实盘运营',

    'db.desc.assets': '标的主数据，代码唯一',
    'db.desc.bars': 'OHLCV 行情，按(标的,周期,时间)唯一',
    'db.desc.ingest_log': '每次调 API 的抓取日志',
    'db.desc.feature_sets': '训练集元信息（标的/时间/特征清单）',
    'db.desc.features': '特征明细长表（样本×特征→值）',
    'db.desc.experiments': '一次实验（策略×标的×假设）',
    'db.desc.backtest_runs': '一次回测 = (实验,策略,标的)',
    'db.desc.backtest_metrics': '绩效指标（关键列+全量JSON）',
    'db.desc.equity_points': '净值曲线采样点',
    'db.desc.trades': '逐笔交易（进→出）',
    'db.desc.model_registry': '通过显著性检验、入选实盘的模型',
    'db.desc.orders': '委托记录（含 dry-run 计划）',
    'db.desc.position_snapshots': '持仓快照',
    'db.desc.account_snapshots': '账户净值快照',

    'er.assets': '标的主数据',
    'er.bars': 'OHLCV 行情',
    'er.ingest_log': '抓取日志',
    'er.feature_sets': '训练集元信息',
    'er.features': '特征长表',
    'er.experiments': '实验',
    'er.backtest_runs': '回测运行',
    'er.backtest_metrics': '绩效指标',
    'er.equity_points': '净值曲线',
    'er.trades': '逐笔交易',
    'er.model_registry': '入选模型',
    'er.orders': '委托记录',
    'er.position_snapshots': '持仓快照',
    'er.account_snapshots': '账户快照',
  },

  en: {
    'app.title': 'QuantLab · Quant Model Research Terminal',
    'boot.connecting': 'Connecting to market data and experiment results…',
    'boot.failed': 'Startup failed',

    'top.strategy': 'Strategy',
    'top.period': 'Period',
    'top.period.2y': '2 Y',
    'top.period.5y': '5 Y',
    'top.clock.title': 'US Eastern · Market open 9:30-16:00',
    'top.clock.open': 'OPEN',
    'top.clock.closed': 'CLOSED',
    'top.symbol.ph': 'Enter symbol…',
    'top.lang': 'Language',

    'nav.chart': 'Charts',
    'nav.equity': 'Equity',
    'nav.portfolio': 'Portfolio',
    'nav.rank': 'Ranking',
    'nav.risk': 'Risk & Sig.',
    'nav.watch': 'Signals',
    'nav.account': 'Account',
    'nav.db': 'Database',

    'side.models': 'Model Library',
    'side.account': 'Account',
    'common.loading': 'Loading…',
    'common.refresh': 'Refresh',
    'common.paused': 'Paused',

    'bar.day': 'D',
    'bar.week': 'W',
    'bar.hour': '1H',
    'bar.min15': '15m',
    'chart.tag': '{label} · {n} bars · {start} ~ {end}',
    'bar.label.day': 'Daily',
    'bar.label.week': 'Weekly',
    'bar.label.hour': '1 Hour',
    'bar.label.min15': '15 Min',
    'ov.ma': 'MA',
    'ov.bb': 'BB',
    'ov.sig': 'Signal',
    'ov.vol': 'Vol',

    'panel.btMetrics': 'Backtest Metrics',
    'panel.equity': 'Equity Curve Comparison',
    'eq.tag': '{symbol} · Top {n} models · Rebased to 100',
    'eq.empty': 'No equity curve for this symbol',

    'panel.portfolio': 'Portfolio Backtest',
    'pf.tag': '{strategy} · {scheme} · {n} symbols',
    'card.bench': 'BM',
    'card.calmar.sub': 'CAGR / Max Drawdown',
    'panel.weights': 'Position Weights',
    'weights.tag': 'Daily weight stack · click legend to hide',
    'panel.schemes': 'Weighting Schemes',
    'schemes.tag': 'enhanced experiment results',
    'schemes.hint': 'Conclusion: equal-weight wins across all 6 strategies; inverse-vol / risk-parity / vol-target agree with each other and perform worse — all of them systematically suppress exposure.',
    'pf.weightNow': 'Current weights: ',
    'pf.flat': 'Flat',
    'pf.benchmark': 'Equal-weight buy & hold',
    'pf.series': 'Portfolio',
    'pf.benchSeries': 'Benchmark',
    'pf.card.cagr': 'Portfolio CAGR',
    'pf.card.cagr.sub': 'benchmark = equal-weight B&H',
    'pf.card.sharpe': 'Portfolio Sharpe',
    'pf.card.sharpe.sub': 'single-asset avg ≈ 0.55',
    'pf.card.mdd': 'Max Drawdown',
    'pf.card.mdd.sub': 'notably tighter after diversification',
    'pf.card.calmar': 'Calmar Ratio',
    'pf.card.calmar.sub': 'CAGR / Drawdown',

    'scheme.equal': 'Equal',
    'scheme.signal': 'Signal-weighted',
    'scheme.vol_inverse': 'Inverse Vol',
    'scheme.risk_parity': 'Risk Parity',
    'scheme.vol_target': 'Vol Target',

    'panel.rank': 'Cross-Model Ranking',
    'rank.tag': 'Average across 8 symbols',
    'rank.by.sharpe': 'By Sharpe',
    'rank.by.calmar': 'By Calmar',
    'rank.by.cagr': 'By CAGR',
    'rank.by.mdd': 'By Drawdown',

    'panel.sig': 'Statistical Significance',
    'sig.tag': 'Portfolio level',
    'sig.hint': 'PSR = probability that true Sharpe > 0 (>0.95 to count as significant); DSR deflates for the selection bias of "tried many times"; SR0 is the expected max Sharpe you would get purely from multiple trials.',
    'panel.riskab': 'Risk Control A/B: Sharpe Δ',
    'riskab.tag': 'vs. no risk control',
    'riskab.hint': 'Positive = risk control helps. Conclusion: it only helps high-exposure trend strategies; for already low-exposure timing strategies it is a negative optimization.',

    'panel.watch': "Today's Signal Scan",
    'watch.tag': 'read-only',
    'watch.rescan': 'Rescan',
    'watch.summary': '{n} symbols · 4-model vote',
    'watch.empty': 'No data',
    'panel.paper': 'Paper Trading Status',
    'paper.empty': 'No paper trading runs yet',
    'paper.risk': 'Risk',
    'paper.orders': 'orders',
    'paper.symbols': 'symbols',
    'paper.weights': 'Weights',
    'paper.none': 'none',

    'panel.acct': 'Account Overview',
    'acct.conn.off': 'Disconnected',
    'acct.conn.on': 'Connected · Trading',
    'acct.conn.closed': 'Connected · Closed',
    'acct.auto': 'Auto refresh',
    'acct.next': 'refresh in {s}s',
    'acct.connStatus': 'Connection',
    'acct.unavailable': 'Unavailable',
    'acct.noTwS': 'Cannot reach TWS / Gateway',
    'acct.alloc': 'Allocation',
    'acct.other': 'Other',
    'acct.cash': 'Cash',

    'panel.positions': 'Positions',
    'positions.tag': 'MV at last price · P&L at cost · native currency',
    'positions.hint': 'Base currency is HKD while US equities are priced in USD. Per-row figures stay in the native currency; account-level totals are FX-converted.',
    'positions.none': 'No open positions',
    'positions.sum': 'Market value {gross} · Unrealized {pnl}',

    'panel.acctCurve': 'Account Equity Curve',
    'acctCurve.tag': 'from account_snapshots · {n} samples',
    'acctCurve.none': 'No data',
    'acctCurve.empty': 'Not enough snapshots (need at least 2). Refresh the board a few times, or keep scripts/account_daemon.py running.',
    'acctCurve.hint': 'Each board visit writes a throttled snapshot (60s minimum), so the curve grows with usage. Keep the sampling daemon running in the background for denser data.',
    'panel.orders': 'Recent Orders',
    'orders.tag': 'orders table',

    'card.netliq': 'Net Liquidation',
    'card.netliq.sub': '{cur} · positions + cash',
    'card.cash': 'Cash Balance',
    'card.cash.sub': '{p} of NAV',
    'card.avail': 'Available Funds',
    'card.avail.sub': 'ready to trade',
    'card.bp': 'Buying Power',
    'card.bp.sub': 'incl. margin',
    'card.mv': 'Position Value',
    'card.mv.sub': '{n} positions',
    'card.pnl': 'Unrealized P&L',
    'card.pnl.sub': 'computed from cost basis',
    'card.day': 'Daily P&L',
    'card.day.sub': "vs. today's first snapshot",
    'card.day.sub2': 'not provided by IB',
    'card.realized': 'Realized P&L',
    'card.realized.sub': 'closed trades',
    'card.lev': 'Leverage',
    'card.lev.sub': 'position value / NAV',

    'panel.dbOverview': 'Database Overview',
    'db.unavailable': 'Unavailable',
    'db.noSqlalchemy': 'sqlalchemy not installed',
    'db.dialect': 'Dialect',
    'db.tables': 'Tables',
    'db.rows': 'Total Rows',
    'db.size': 'File Size',
    'db.singleFile': 'single file, zero config',
    'db.clientServer': 'client / server',
    'db.structure': '14 tables · 5 layers',
    'db.allRows': 'all tables combined',
    'db.sqliteFile': 'SQLite data file',
    'db.tag': '{dialect} · {n} rows',
    'panel.sql': 'SQL Console',
    'sql.tag': 'read-only · SELECT / WITH',
    'sql.run': 'Run ⌘↵',
    'sql.ph': 'SELECT * FROM bars LIMIT 20',
    'sql.pick': '— pick an example query —',
    'sql.enter': 'Please enter SQL',
    'sql.error': 'Error: ',
    'sql.failed': 'Request failed: ',
    'sql.rows': '{n} rows · {ms} ms',
    'panel.er': 'Data Model (ER)',
    'er.tag': '14 tables · 5 layers',

    'toast.reqFail': 'Request failed: ',
    'toast.switched': 'Switched to {name}',
    'toast.bar': 'Switched to {label}',
    'toast.barNote': ' (signal backtest is daily only)',

    'db.group.market': 'Market Data · from API',
    'db.group.feature': 'Training Sets · Features',
    'db.group.exp': 'Experiments & Backtests',
    'db.group.model': 'Model Registry',
    'db.group.live': 'Live Operations',

    'db.desc.assets': 'symbol master data, unique ticker',
    'db.desc.bars': 'OHLCV bars, unique per (symbol, interval, ts)',
    'db.desc.ingest_log': 'audit log of every API fetch',
    'db.desc.feature_sets': 'training set metadata (symbols / range / features)',
    'db.desc.features': 'long-format features (sample × feature → value)',
    'db.desc.experiments': 'one experiment (strategy × symbols × hypothesis)',
    'db.desc.backtest_runs': 'one run = (experiment, strategy, symbol)',
    'db.desc.backtest_metrics': 'metrics (key columns + full JSON)',
    'db.desc.equity_points': 'equity curve sample points',
    'db.desc.trades': 'individual trades (entry → exit)',
    'db.desc.model_registry': 'models passing significance and promoted to live',
    'db.desc.orders': 'order records (incl. dry-run plans)',
    'db.desc.position_snapshots': 'position snapshots',
    'db.desc.account_snapshots': 'account equity snapshots',

    'er.assets': 'symbol master',
    'er.bars': 'OHLCV bars',
    'er.ingest_log': 'fetch log',
    'er.feature_sets': 'training meta',
    'er.features': 'feature long table',
    'er.experiments': 'experiments',
    'er.backtest_runs': 'backtest runs',
    'er.backtest_metrics': 'metrics',
    'er.equity_points': 'equity curve',
    'er.trades': 'trades',
    'er.model_registry': 'selected models',
    'er.orders': 'orders',
    'er.position_snapshots': 'position snapshots',
    'er.account_snapshots': 'account snapshots',
  },
};

/* ---------------- 数据字段名：中文 key → 英文显示 ---------------- */
const FIELD_EN = {
  '策略': 'Strategy', '类别': 'Category', '默认参数': 'Defaults',
  '适合行情': 'Regime', '说明': 'Notes',

  '总收益率': 'Total Return', '年化收益率': 'CAGR', '年化波动率': 'Ann. Vol',
  '下行波动率': 'Downside Vol', '最大回撤': 'Max Drawdown',
  '回撤持续期': 'DD Duration', 'VaR95(日)': 'VaR95 (1D)', 'CVaR95(日)': 'CVaR95 (1D)',
  '夏普比率': 'Sharpe', '索提诺比率': 'Sortino', '卡玛比率': 'Calmar', 'Omega': 'Omega',
  '偏度': 'Skew', '峰度': 'Kurtosis', '峰度(原始)': 'Kurtosis (raw)',
  '月度胜率': 'Monthly Win %', '单日最大亏损': 'Worst Day',
  '最长连亏(月)': 'Longest Losing Streak (mo)', '交易次数': 'Trades',
  '胜率': 'Win Rate', '盈亏比': 'Win/Loss Ratio', '平均持仓天数': 'Avg Hold Days',
  '单笔均值': 'Avg Trade', '最好一笔': 'Best Trade', '最差一笔': 'Worst Trade',
  '年化换手': 'Ann. Turnover', '持仓占比': 'Exposure', 'Beta': 'Beta',
  '年化Alpha': 'Ann. Alpha', '跟踪误差': 'Tracking Error',
  '信息比率': 'Information Ratio', '相对基准超额': 'Excess vs BM',
  '超额年化': 'Ann. Excess', '标的数': 'Symbols', '跑赢基准占比': 'Beat BM %',

  'PSR(>0)': 'PSR (>0)', 'PSR(>1)': 'PSR (>1)', 'DSR': 'DSR',
  'PSR(显著概率)': 'PSR (significance)', 'DSR(去膨胀)': 'DSR (deflated)',
  '夏普95%下界': 'Sharpe 95% LB', '夏普95%上界': 'Sharpe 95% UB',
  'SR0(期望虚高线)': 'SR0 (expected max)', '样本数': 'Samples',
  '最小样本量(证明夏普>0)': 'Min Samples (Sharpe>0)',
  '夏普标准误(年化)': 'Sharpe SE (ann.)', '试验次数': 'Trials',

  '风控方案': 'Risk Scheme', '夏普改善': 'Sharpe Δ', '回撤改善': 'Drawdown Δ',
  '权重方案': 'Scheme', '平均标的数量': 'Avg Symbols',

  '标的': 'Symbol', '最新收盘': 'Last Close', '数据日期': 'Data Date',
  '看多票数': 'Bull Votes', '合计建议仓位': 'Suggested Weight',

  '净值': 'Net Liquidation', '现金': 'Cash', '现金占比': 'Cash %',
  '可用资金': 'Available Funds', '购买力': 'Buying Power',
  '持仓市值': 'Position Value', '持仓盈亏': 'Unrealized P&L',
  'IB浮动盈亏': 'IB Unrealized', '已实现盈亏': 'Realized P&L',
  '当日盈亏': 'Daily P&L', '维持保证金': 'Maint. Margin',
  '剩余流动性': 'Excess Liquidity', '持仓数': 'Positions',
  '杠杆': 'Leverage', '基准币种': 'Base Ccy', '汇率': 'FX Rate',
  '当日盈亏基线': 'Day Baseline',

  '币种': 'Ccy', '股数': 'Qty', '成本价': 'Avg Cost', '现价': 'Last',
  '市值': 'Market Value', '成本': 'Cost', '浮动盈亏': 'Unrealized',
  '盈亏%': 'P&L %', '权重%': 'Weight %', '方向': 'Side',

  '时间': 'Time', '账户': 'Account', '动作': 'Action', '类型': 'Type',
  '限价': 'Limit', '状态': 'Status', '金额(HKD)': 'Amount (HKD)',
  '行数': 'Rows', '表': 'Table',
};

/* 策略名：中文 data key → 英文显示 */
const STRAT_EN = {
  'ADX趋势过滤': 'ADX Trend Filter',
  'ATR移动止损': 'ATR Trailing Stop',
  'ATR通道突破': 'ATR Channel Breakout',
  'MACD交叉': 'MACD Crossover',
  'ML+趋势过滤': 'ML + Trend Filter',
  'ROC动量阈值': 'ROC Momentum Threshold',
  'RSI2极端反转': 'RSI2 Extreme Reversal',
  'RSI均值回归': 'RSI Mean Reversion',
  'ZScore回归': 'Z-Score Reversion',
  '买入持有': 'Buy & Hold',
  '信号加权混合': 'Signal-Weighted Blend',
  '双均线交叉': 'Dual MA Crossover',
  '唐奇安突破': 'Donchian Breakout',
  '均线+长周期过滤': 'MA + Long-Term Filter',
  '多策略投票': 'Multi-Strategy Vote',
  '布林+RSI双确认': 'Bollinger + RSI Confirm',
  '布林带回归': 'Bollinger Reversion',
  '时间序列动量': 'Time-Series Momentum',
  '机器学习方向预测': 'ML Direction Forecast',
  '波动率目标动量': 'Vol-Target Momentum',
  '状态自适应切换': 'Regime-Adaptive Switch',

  '动量': 'Momentum', '均值回归': 'Mean Reversion', '基准': 'Benchmark',
  '机器学习': 'Machine Learning', '突破': 'Breakout', '组合': 'Composite',
  '趋势跟踪': 'Trend Following',

  '无风控': 'No Risk Control',
  'ATR止损+止盈': 'ATR Stop + Take Profit',
  'ATR移动止损': 'ATR Trailing Stop',
  '全套风控': 'Full Risk Suite',
  '固定止损8%': 'Fixed 8% Stop',
  '波动率目标15%': '15% Vol Target',
  '波动目标+ATR止损': 'Vol Target + ATR Stop',
};

/* ---------------- 取值接口 ---------------- */

/** 取当前语言 */
function L() { return QP_LANG; }

/** 取翻译文案，支持 {name} 占位 */
function t(key, vars) {
  const pack = DICT[QP_LANG] || DICT.zh;
  let s = pack[key];
  if (s === undefined) s = (DICT.zh[key] !== undefined ? DICT.zh[key] : key);
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] !== undefined ? vars[k] : m));
  return s;
}

/** 数据字段名展示（中文模式原样返回；英文模式查表，查不到原样） */
function label(c) {
  if (QP_LANG !== 'en') return c;
  return FIELD_EN[c] || c;
}

/** 策略 / 类别 / 风控方案名展示（值本身仍用中文去调后端） */
function stratName(s) {
  if (QP_LANG !== 'en') return s;
  return STRAT_EN[s] || s;
}

/** 批量替换带 data-i18n 的静态节点 */
function applyI18n(root) {
  const scope = root || document;
  // 查不到的键保留 HTML 原文，不要用键名把界面刷成 "nav.xxx"
  scope.querySelectorAll('[data-i18n]').forEach(el => {
    const k = el.dataset.i18n, v = t(k);
    if (v !== k) el.textContent = v;
  });
  scope.querySelectorAll('[data-i18n-ph]').forEach(el => {
    const k = el.dataset.i18nPh, v = t(k);
    if (v !== k) el.setAttribute('placeholder', v);
  });
  scope.querySelectorAll('[data-i18n-title]').forEach(el => {
    const k = el.dataset.i18nTitle, v = t(k);
    if (v !== k) el.setAttribute('title', v);
  });
  document.title = t('app.title');
  document.documentElement.lang = QP_LANG === 'en' ? 'en' : 'zh-CN';
}

/** 切换语言：存档 + 保留当前视图/标的 + reload（最可靠，不会漏翻译） */
function setLang(lang) {
  if (lang !== 'zh' && lang !== 'en') return;
  localStorage.setItem(LANG_KEY, lang);
  // state 是 app.js 里的全局 const，不是 window 属性，直接引用即可
  try {
    sessionStorage.setItem('qp_view', state.view);
    sessionStorage.setItem('qp_symbol', state.symbol);
  } catch (e) { /* 隐私模式 / state 未就绪时忽略 */ }
  location.reload();
}
