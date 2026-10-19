# quant_learning —— 量化交易模型实验平台

> 从「教学 demo」升级为「可对多种量化交易模型做检测与对比的实验平台」。
> 数据来自**盈透证券(IBKR)真实历史行情**，支持 21 个模型 × 任意标的 × 多周期对比，
> 内置参数寻优、走前验证(Walk-Forward)、过拟合诊断与 HTML 报告。

---

## 一、它解决什么问题

| 原来的项目 | 现在的平台 |
|-----------|-----------|
| 合成假数据 | 盈透真实日线/小时线/分钟线（带本地缓存） |
| 3 个策略写死在 main.py | 21 个策略注册表，新增策略只需实现 `signals(df)` |
| 只有一个总收益率看结果 | 20+ 绩效指标 + 综合评分排名 + 稳健性诊断 |
| 没有防过拟合机制 | 网格寻优 + 参数敏感性 + 走前分析（样本内/外对比） |
| 结果打印到控制台 | CSV/JSON 落盘 + 带图表的 HTML 报告 |

---

## 二、快速开始

环境：`E:/Programs/miniconda/envs/aicode/python.exe`（Python 3.10，已装 pandas/numpy/
matplotlib/scipy/scikit-learn/yfinance/ib_insync）

```bash
cd d:/pycharm_file/2026_stork/quant_learning

# 1. 看有哪些模型
python -m quant_platform.cli list

# 2. 拉数据（自动缓存到 data_cache/）
python -m quant_platform.cli fetch SPY AAPL 700.HK --duration "5 Y"

# 3. 单标的回测
python -m quant_platform.cli backtest SPY --strategy 双均线交叉 RSI均值回归

# 4. 参数寻优 + 过拟合体检
python -m quant_platform.cli optimize SPY --strategy 双均线交叉 --metric 夏普比率

# 5. 走前分析（样本内 vs 样本外）
python -m quant_platform.cli walkforward SPY --strategy 时间序列动量

# 6. 一键跑完整实验并出 HTML 报告
python -m quant_platform.cli experiment --config configs/main_compare.yaml

# 7. 风控 A/B：同一信号挂不同风控方案对比
python -m quant_platform.cli riskab SPY --strategy 双均线交叉 ZScore回归 --save

# 8. 多标的组合回测（5 种资金分配方案自动择优）
python -m quant_platform.cli portfolio SPY QQQ AAPL MSFT NVDA AMD TSLA META --strategy ADX趋势过滤

# 9. 统计显著性体检（PSR / DSR / 最小样本量 / 随机置换检验）
python -m quant_platform.cli significance SPY --random 双均线交叉 --iter 300

# 10. 模拟盘监控（默认 dry-run，绝不真发单）
python -m quant_platform.cli paper --preset                    # 跑一次看计划
python -m quant_platform.cli paper --preset --daemon --interval 3600   # 常驻

# 11. 今日信号扫描（只读）/ 账户与持仓
python -m quant_platform.cli signal SPY QQQ NVDA
python -m quant_platform.cli account
```

> 运行前请保证 TWS 已登录并开启 API（本机 127.0.0.1:7497）。

---

## 三、目录结构

```
quant_learning/
├── quant_platform/               # 平台代码
│   ├── config.py                 # 全局配置（IB 连接 / 成本假设 / 路径）
│   ├── indicators.py             # 技术指标库（趋势/动量/波动/量能/ML特征）
│   ├── data/                     # 数据层
│   │   ├── base.py               # 数据源抽象 + 统一 OHLCV 格式
│   │   ├── ibkr_source.py        # 盈透数据源（限频保护/重试/clientId 自动避让）
│   │   ├── other_sources.py      # yfinance / CSV / 带市场状态的合成数据
│   │   ├── cache.py              # 本地 CSV 缓存
│   │   └── manager.py            # get_data() 统一入口（缓存+降级）
│   ├── strategies/               # 策略库（注册表机制）
│   │   ├── base.py               # Strategy 基类 + 参数空间 + 状态机工具
│   │   ├── trend.py              # 趋势跟踪 / 突破
│   │   ├── mean_reversion.py     # 均值回归
│   │   ├── momentum.py           # 动量 / 波动率目标 / ATR 移动止损
│   │   ├── ml.py                 # 机器学习（严格走前重训）
│   │   └── ensemble.py           # 投票 / 加权 / 状态自适应切换
│   ├── backtest/
│   │   ├── engine.py             # 回测引擎（成本/滑点/止损/波动率目标仓位）
│   │   ├── metrics.py            # 20+ 绩效指标 + 综合评分
│   │   ├── optimizer.py          # 网格/随机寻优 + 过拟合体检
│   │   └── walkforward.py        # 走前分析
│   ├── risk/                     # 风控叠加层
│   │   ├── overlay.py            # 波动率目标/ATR移动止损/止损止盈/回撤熔断
│   │   └── compare.py            # 风控 A/B 对比
│   ├── stats/significance.py     # 显著性：bootstrap/PSR/DSR/minTRL/随机置换
│   ├── portfolio/engine.py       # 多标的组合回测（5 种权重方案）
│   ├── experiment/
│   │   ├── runner.py             # 实验编排（策略×标的×参数）
│   │   ├── compare.py            # 排名 / 稳健性 / 相关性 / 市场状态分解
│   │   └── report.py             # HTML 报告 + 图表
│   ├── live/
│   │   ├── broker.py             # 实盘接口（默认 dry-run + 风控限额）
│   │   └── paper.py              # 模拟盘 paper trading 守护进程
│   ├── db/                       # SQL 数据库持久层
│   │   ├── schema.py             # 14 张表 ORM 模型（五层）
│   │   ├── session.py            # engine / session_scope / init_db
│   │   ├── repository.py         # 数据访问层（含只读 SQL）
│   │   ├── features.py           # 训练集构建与读回
│   │   └── migrate.py            # CSV 缓存导入 / 导出 / VACUUM
│   └── cli.py                    # 命令行入口
├── configs/                      # 实验配置（YAML）
│   ├── demo.yaml                 # 冒烟测试
│   ├── main_compare.yaml         # 主对比实验
│   ├── walkforward_validation.yaml
│   └── db_check.yaml             # 数据库落库验证
├── scripts/prefetch_data.py      # 预拉数据
├── scripts/paper_ops.py          # 模拟盘真实委托运营
├── scripts/ml_from_db.py         # 从数据库读训练集训练模型
├── data/                         # SQLite 数据库（.gitignore 已排除）
├── data_cache/                   # 数据缓存（按 数据源_标的_周期_时长 命名）
├── results/                      # 实验结果（CSV + HTML 报告）
├── _env_check/                   # 环境检测脚本与报告
└── data.py / indicators.py / strategy.py / backtest.py / main.py   # 原始教学代码（保留）
```

---

## 四、内置模型一览

| 类别 | 模型 | 核心逻辑 | 适合行情 |
|------|------|---------|---------|
| 基准 | 买入持有 | 始终满仓 | 长期上涨 |
| 趋势跟踪 | 双均线交叉 | 快线上穿慢线买入 | 单边趋势 |
| | MACD交叉 | DIF 上穿 DEA | 中段趋势 |
| | ADX趋势过滤 | ADX>阈值且均线向上 | 强趋势 |
| | 均线+长周期过滤 | 价格在 200 线上方才做多 | 牛市 |
| | ATR移动止损 | 持仓期最高价 - k×ATR 离场 | 大趋势 |
| 突破 | 唐奇安突破 | 突破 N 日最高价（海龟） | 大级别趋势 |
| | ATR通道突破 | 突破 EMA + k×ATR 上轨 | 波动放大 |
| 均值回归 | RSI均值回归 | RSI<30 买，>70 卖 | 震荡市 |
| | RSI2极端反转 | 200线上方 + 2日RSI<5 | 牛市回调 |
| | 布林带回归 | 跌破下轨买，回中轨卖 | 震荡市 |
| | ZScore回归 | 偏离 N 个标准差入场 | 震荡市 |
| | 布林+RSI双确认 | 双条件共振 | 震荡市 |
| 动量 | 时间序列动量 | 过去 N 期收益>0 持仓 | 中长期趋势 |
| | ROC动量阈值 | 变动率突破阈值 | 趋势启动 |
| | 波动率目标动量 | 波动大减仓、波动小加仓 | 全环境 |
| 机器学习 | 机器学习方向预测 | RF/LogReg 滚动重训预测次日涨跌 | 数据充足的活跃标的 |
| | ML+趋势过滤 | ML 看多 且 价格在长期均线上方 | 牛市 |
| 组合 | 多策略投票 | 多模型多数表决 | 全环境 |
| | 信号加权混合 | 多信号线性加权成连续仓位 | 全环境 |
| | 状态自适应切换 | ADX 高用趋势模型、低用回归模型 | 全环境 |

新增自己的模型：

```python
from quant_platform.strategies import Strategy, StrategyMeta, register

@register
class MyStrategy(Strategy):
    meta = StrategyMeta("我的策略", "自定义", "一句话说明")
    default_params = {"n": 20}
    param_space = {"n": [10, 20, 30]}      # 供寻优与走前使用

    def signals(self, df):                 # df: open/high/low/close/volume
        return (df["close"] > df["close"].rolling(self.params["n"]).mean()).astype(float)
```

注册后，`list` / `backtest` / `experiment` 立即支持它，无需改动任何其它代码。

---

## 五、回测的三条诚实原则（代码层面已强制）

1. **无未来函数**：信号统一 `shift(1)` 后才变成持仓，今天只能用昨天的信号。
2. **有交易成本**：手续费（比例 + 每笔最低佣金）+ 滑点，按换手率发生。
3. **防过拟合**：
   - `optimizer.overfitting_score()`：看最优参数邻域是否也是"参数高原"；
   - `walkforward.walk_forward()`：滚动窗口寻优 + 样本外检验；
   - 报告会自动标注"样本外夏普 < 样本内一半"的疑似过拟合模型。

---

## 六、实盘 / 模拟盘

当前账户为**模拟盘**（`DUT097472`，DU 前缀）。平台默认安全：

- `IBBroker(dry_run=True)`：只打印"打算做什么"，不会真发单；
- 风控限额：单笔金额上限、单标的持仓上限、当日下单次数上限；
- 行情：本机 TWS 未订阅实时行情（错误 10089），实时 tick 不可用；
  监控模块走"轮询最新 K 线计算信号"，不依赖流式行情。

```python
from quant_platform.live import IBBroker, SignalMonitor
bk = IBBroker(dry_run=True)      # 试算
bk.connect(); print(bk.account()); print(bk.positions())
print(bk.rebalance("AAPL", target_shares=100))   # 只打印不下单

mon = SignalMonitor(["双均线交叉", "时间序列动量"], ["SPY", "QQQ"])
print(mon.scan())                # 今日各模型信号与合计建议仓位
```

---

## 七、数据库系统（SQL 持久化）

### 为什么需要

回测跑完就只剩一堆 CSV，下次想问"哪个模型在美股科技股上最稳"就得重跑一遍。
数据库的作用是把**每一次 API 抓取、每一次实验、每一笔委托**都沉淀成可查询的资产：

| 需求 | 用文件很难 | 用 SQL 很简单 |
|------|-----------|--------------|
| 这个标的库里有多少根 K 线、覆盖哪段时间 | 翻文件名 | `SELECT COUNT(*), MIN(ts), MAX(ts) FROM bars GROUP BY ...` |
| 哪些模型的 DSR 超过 0.95 | 开一堆 CSV 手工比 | `WHERE dsr > 0.95 ORDER BY dsr DESC` |
| 上个月一共下了几笔单、成交均价多少 | 翻日志 | `SELECT ... FROM orders WHERE ts BETWEEN ...` |
| 换台机器还能复现实验 | 复制整个目录 | 拷一个 .db 文件 |

### 表结构（14 张表，5 层）

```
【行情层 · API 抓取】
  assets        标的主数据（symbol 唯一）
  bars          OHLCV 行情，UNIQUE(asset_id, bar_size, ts) —— 幂等增量
  ingest_log    每次调 API 的审计日志（行数/耗时/成功失败）

【特征层 · 训练集】
  feature_sets  训练集元信息（标的/时间范围/特征清单/切分比例）
  features      特征明细长表：(训练集, 标的, 时间, 特征名) → 值

【实验层 · 回测】
  experiments       一次实验（策略×标的×成本假设）
  backtest_runs     一次回测 = (实验, 策略, 标的)
  backtest_metrics  绩效指标（关键列 + 全量 JSON）
  equity_points     净值曲线采样点（按 stride 抽样）
  trades         逐笔交易（进 → 出）

【模型层】
  model_registry  通过显著性检验、入选实盘的模型

【实盘层】
  orders              委托记录（含 dry-run 计划）
  position_snapshots  持仓快照
  account_snapshots   账户净值快照（账户曲线 / 回撤 / 熔断）
```

外键关系：
```
assets.id ←── bars.asset_id
assets.id ←── features.symbol（按 symbol 关联）
feature_sets.id ←── features.feature_set_id
experiments.id ←── backtest_runs.experiment_id ←── backtest_metrics.run_id
                                              ←── equity_points.run_id
                                              ←── trades.run_id
```

### 数据流（课程报告可直接引用这张图）

```
  盈透 API（TWS 7497）
        │  历史 K 线
        ▼
  data/manager.get_data()
        ├──► data_cache/*.csv   （文件缓存，人类可读）
        └──► db: bars + ingest_log   （SQL 持久化）
                    │
                    ▼
              features（训练集）
                    │
                    ▼
         strategies → backtest → experiments / backtest_runs
                    │            / backtest_metrics / trades
                    ▼
              model_registry（入选模型）
                    │
                    ▼
        paper_ops 实盘委托 → orders / position_snapshots
                             / account_snapshots
```

### 数据库 CLI

```bash
python -m quant_platform.cli db-init          # 建库建表（幂等）
python -m quant_platform.cli db-import        # 把已有 CSV 缓存导入 bars 表
python -m quant_platform.cli db-stats         # 各表行数统计
python -m quant_platform.cli db-coverage      # 行情覆盖总览（标的×周期×时间范围）
python -m quant_platform.cli db-ingests       # API 抓取日志
python -m quant_platform.cli db-features --name demo --symbols SPY QQQ AAPL   # 构建训练集
python -m quant_platform.cli db-features --list                                # 列出训练集
python -m quant_platform.cli db-features --describe demo                        # 字段统计
python -m quant_platform.cli db-register  # 实验结论 → model_registry（按 PSR/DSR 门槛自动标记入选）
python -m quant_platform.cli db-orders --limit 20
python -m quant_platform.cli db-orders --account          # 账户净值曲线
python -m quant_platform.cli db-export SPY --bar-size "1 day"
python -m quant_platform.cli db-query --list               # 列出示例 SQL
python -m quant_platform.cli db-query --example 2          # 执行第 2 个示例
python -m quant_platform.cli db-query --sql "SELECT COUNT(*) FROM bars"
python -m quant_platform.cli db-query        # 进入交互式 SQL（只读）
```

### 切换到 PostgreSQL / MySQL

业务代码全部走 SQLAlchemy，不含任何 SQLite 专有语法。只需改连接串：

```bash
# PowerShell
$env:QP_DATABASE_URL = "postgresql+psycopg2://user:pwd@localhost:5432/quant"
$env:QP_DATABASE_URL = "mysql+pymysql://user:pwd@localhost:3306/quant"

# Bash
export QP_DATABASE_URL="postgresql+psycopg2://user:pwd@localhost:5432/quant"
```

或写进实验配置 YAML：

```yaml
db:
  url: "postgresql+psycopg2://user:pwd@localhost:5432/quant"
  enabled: true
  persist_bars: true         # API 行情落库
  persist_experiments: true  # 实验/回测结果落库
  persist_orders: true       # 委托与账户快照落库
  equity_stride: 3           # 净值曲线抽样步长
```

### 在代码里读写

```python
from quant_platform.db import upsert_bars, load_bars, run_sql, register_model

upsert_bars("AAPL", "1 day", df, source="ibkr")     # 幂等写入
df = load_bars("AAPL", "1 day", start="2024-01-01")  # 读回 DataFrame

r = run_sql("SELECT strategy, AVG(sharpe) s FROM backtest_runs r "
            "JOIN backtest_metrics m ON m.run_id=r.id GROUP BY strategy ORDER BY s DESC")
# r = {"columns": [...], "rows": [[...]], "rowcount": n, "elapsed_ms": 3.1}
```

---

## 八、环境与权限状态

详见 `_env_check/ENV_REPORT.md`。要点：

- TWS 已登录并开启 API：`127.0.0.1:7497`，`readOnlyApi=false`，仅允许本机连接；
- 账户：模拟盘 `DUT097472`，净值约 HKD 100 万，当前无持仓；
- 历史数据：日线最长 5 年，小时线 2 年，1 分钟线 1 个月，批量多标的正常；
- 实时行情：**未订阅**，快照返回空值（不影响回测与日频监控）；
- Git：`git@github.com:vampireli1900-dev/quant-learning.git`，SSH 可推送。

---

## 九、原始教学代码

`data.py` / `indicators.py` / `strategy.py` / `backtest.py` / `main.py` 保留为入门教学版本
（合成数据、5 个文件讲清一条流水线），建议先读它们理解"回测的坑"，再进入平台代码。

---

> 免责声明：本项目仅用于研究与学习，所有回测结果不构成投资建议。
