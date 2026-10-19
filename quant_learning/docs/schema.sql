-- ============================================================
-- quant_learning · 量化交易模型实验平台 —— 数据库结构（DDL）
-- 由 ORM 自动生成，请勿手工编辑；方言：sqlite
-- 当前库位置：D:/pycharm_file/2026_stork/quant_learning/data/quant.db
-- ============================================================

-- ---------------- 行情数据（API 抓取） ----------------

-- 标的主数据，代码唯一
CREATE TABLE assets (
	id INTEGER NOT NULL, 
	symbol VARCHAR(32) NOT NULL, 
	name VARCHAR(128), 
	sec_type VARCHAR(16) NOT NULL, 
	exchange VARCHAR(16) NOT NULL, 
	currency VARCHAR(8) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_assets_symbol ON assets (symbol);

-- OHLCV 行情，按(标的,周期,时间)唯一
CREATE TABLE bars (
	id INTEGER NOT NULL, 
	asset_id INTEGER NOT NULL, 
	bar_size VARCHAR(16) NOT NULL, 
	ts DATETIME NOT NULL, 
	open FLOAT NOT NULL, 
	high FLOAT NOT NULL, 
	low FLOAT NOT NULL, 
	close FLOAT NOT NULL, 
	volume FLOAT NOT NULL, 
	source VARCHAR(16) NOT NULL, 
	ingested_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_bars_series UNIQUE (asset_id, bar_size, ts), 
	FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE CASCADE
);
CREATE INDEX ix_bars_series_ts ON bars (asset_id, bar_size, ts);
CREATE INDEX ix_bars_ts ON bars (ts);

-- 每次调 API 的抓取日志
CREATE TABLE ingest_log (
	id INTEGER NOT NULL, 
	source VARCHAR(16) NOT NULL, 
	symbol VARCHAR(32) NOT NULL, 
	bar_size VARCHAR(16) NOT NULL, 
	duration VARCHAR(16), 
	status VARCHAR(16) NOT NULL, 
	rows INTEGER NOT NULL, 
	message TEXT, 
	started_at DATETIME NOT NULL, 
	finished_at DATETIME, 
	duration_ms FLOAT NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_ingest_log_symbol ON ingest_log (symbol);

-- ---------------- 训练集（特征工程） ----------------

-- 训练集元信息（标的/时间/特征清单）
CREATE TABLE feature_sets (
	id INTEGER NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	description TEXT, 
	symbols JSON NOT NULL, 
	feature_names JSON NOT NULL, 
	label VARCHAR(64) NOT NULL, 
	start VARCHAR(32), 
	"end" VARCHAR(32), 
	n_rows INTEGER NOT NULL, 
	n_features INTEGER NOT NULL, 
	train_ratio FLOAT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_feature_sets_name ON feature_sets (name);

-- 特征明细长表（样本×特征→值）
CREATE TABLE features (
	id INTEGER NOT NULL, 
	feature_set_id INTEGER NOT NULL, 
	symbol VARCHAR(32) NOT NULL, 
	ts DATETIME NOT NULL, 
	feature VARCHAR(64) NOT NULL, 
	value FLOAT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(feature_set_id) REFERENCES feature_sets (id) ON DELETE CASCADE
);
CREATE INDEX ix_features_feature ON features (feature);
CREATE INDEX ix_features_set_name ON features (feature_set_id, feature);
CREATE INDEX ix_features_set_symbol_ts ON features (feature_set_id, symbol, ts);
CREATE INDEX ix_features_symbol ON features (symbol);
CREATE INDEX ix_features_ts ON features (ts);

-- ---------------- 实验与回测 ----------------

-- 一次实验（策略×标的×假设）
CREATE TABLE experiments (
	id INTEGER NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	created_at DATETIME NOT NULL, 
	duration VARCHAR(16), 
	bar_size VARCHAR(16), 
	data_source VARCHAR(16), 
	n_symbols INTEGER NOT NULL, 
	n_strategies INTEGER NOT NULL, 
	initial_capital FLOAT NOT NULL, 
	commission_rate FLOAT NOT NULL, 
	slippage_rate FLOAT NOT NULL, 
	runtime_sec FLOAT NOT NULL, 
	config_json JSON NOT NULL, 
	summary_json JSON NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_experiments_name ON experiments (name);

-- 一次回测 = (实验,策略,标的)
CREATE TABLE backtest_runs (
	id INTEGER NOT NULL, 
	experiment_id INTEGER NOT NULL, 
	strategy VARCHAR(64) NOT NULL, 
	symbol VARCHAR(32) NOT NULL, 
	params_json JSON NOT NULL, 
	error TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_run_exp_strategy_symbol UNIQUE (experiment_id, strategy, symbol), 
	FOREIGN KEY(experiment_id) REFERENCES experiments (id) ON DELETE CASCADE
);
CREATE INDEX ix_backtest_runs_strategy ON backtest_runs (strategy);
CREATE INDEX ix_backtest_runs_symbol ON backtest_runs (symbol);

-- 绩效指标（关键列+全量JSON）
CREATE TABLE backtest_metrics (
	id INTEGER NOT NULL, 
	run_id INTEGER NOT NULL, 
	total_return FLOAT, 
	annual_return FLOAT, 
	annual_vol FLOAT, 
	max_drawdown FLOAT, 
	sharpe FLOAT, 
	sortino FLOAT, 
	calmar FLOAT, 
	var95 FLOAT, 
	cvar95 FLOAT, 
	win_rate FLOAT, 
	monthly_win_rate FLOAT, 
	profit_factor FLOAT, 
	turnover FLOAT, 
	exposure FLOAT, 
	trade_count FLOAT, 
	alpha FLOAT, 
	beta FLOAT, 
	info_ratio FLOAT, 
	excess_annual FLOAT, 
	psr FLOAT, 
	dsr FLOAT, 
	sharpe_ci_low FLOAT, 
	sharpe_ci_high FLOAT, 
	metrics_json JSON NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(run_id) REFERENCES backtest_runs (id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX ix_backtest_metrics_run_id ON backtest_metrics (run_id);

-- 净值曲线采样点
CREATE TABLE equity_points (
	id INTEGER NOT NULL, 
	run_id INTEGER NOT NULL, 
	ts DATETIME NOT NULL, 
	equity FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(run_id) REFERENCES backtest_runs (id) ON DELETE CASCADE
);
CREATE INDEX ix_equity_run_ts ON equity_points (run_id, ts);

-- 逐笔交易（进→出）
CREATE TABLE trades (
	id INTEGER NOT NULL, 
	run_id INTEGER, 
	symbol VARCHAR(32) NOT NULL, 
	strategy VARCHAR(64), 
	direction FLOAT NOT NULL, 
	entry_ts DATETIME, 
	exit_ts DATETIME, 
	entry_price FLOAT, 
	exit_price FLOAT, 
	ret FLOAT, 
	bars FLOAT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(run_id) REFERENCES backtest_runs (id) ON DELETE CASCADE
);
CREATE INDEX ix_trades_run_entry ON trades (run_id, entry_ts);
CREATE INDEX ix_trades_symbol ON trades (symbol);

-- ---------------- 模型注册表 ----------------

-- 通过显著性检验、入选实盘的模型
CREATE TABLE model_registry (
	id INTEGER NOT NULL, 
	strategy VARCHAR(64) NOT NULL, 
	symbol VARCHAR(32) NOT NULL, 
	params_json JSON NOT NULL, 
	source_experiment VARCHAR(128), 
	sharpe FLOAT, 
	psr FLOAT, 
	dsr FLOAT, 
	selected BOOLEAN NOT NULL, 
	note TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_model_registry_strategy ON model_registry (strategy);

-- ---------------- 实盘运营 ----------------

-- 委托记录（含 dry-run 计划）
CREATE TABLE orders (
	id INTEGER NOT NULL, 
	ts DATETIME NOT NULL, 
	round_id VARCHAR(64), 
	account VARCHAR(32), 
	symbol VARCHAR(32) NOT NULL, 
	action VARCHAR(8) NOT NULL, 
	qty FLOAT NOT NULL, 
	order_type VARCHAR(8) NOT NULL, 
	limit_price FLOAT, 
	ref_price FLOAT, 
	currency VARCHAR(8) NOT NULL, 
	fx_rate FLOAT, 
	value_hkd FLOAT, 
	status VARCHAR(32) NOT NULL, 
	broker_order_id INTEGER, 
	filled_qty FLOAT NOT NULL, 
	avg_fill_price FLOAT, 
	note TEXT, 
	payload_json JSON NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_orders_round_id ON orders (round_id);
CREATE INDEX ix_orders_symbol ON orders (symbol);
CREATE INDEX ix_orders_ts ON orders (ts);
CREATE INDEX ix_orders_ts_symbol ON orders (ts, symbol);

-- 持仓快照
CREATE TABLE position_snapshots (
	id INTEGER NOT NULL, 
	ts DATETIME NOT NULL, 
	account VARCHAR(32), 
	symbol VARCHAR(32) NOT NULL, 
	position FLOAT NOT NULL, 
	avg_cost FLOAT, 
	market_price FLOAT, 
	market_value FLOAT, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_pos_ts_account ON position_snapshots (ts, account);
CREATE INDEX ix_position_snapshots_symbol ON position_snapshots (symbol);
CREATE INDEX ix_position_snapshots_ts ON position_snapshots (ts);

-- 账户净值快照
CREATE TABLE account_snapshots (
	id INTEGER NOT NULL, 
	ts DATETIME NOT NULL, 
	account VARCHAR(32), 
	net_liquidation FLOAT, 
	total_cash FLOAT, 
	available_funds FLOAT, 
	unrealized_pnl FLOAT, 
	realized_pnl FLOAT, 
	gross_exposure FLOAT, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_account_snapshots_account ON account_snapshots (account);
CREATE INDEX ix_account_snapshots_ts ON account_snapshots (ts);
