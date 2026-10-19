# 开发环境检测报告

> 检测时间：2026-10-19 20:20 ~ 20:45 (GMT+8)
> 项目：`D:\pycharm_file\2026_stork\quant_learning`
> 检测脚本：`_env_check/test_ib.py`、`test_ib2.py`、`test_ib3.py`

---

## 一、总览结论

| 项目 | 状态 | 说明 |
|------|------|------|
| Python 解释器 (`aicode` conda) | ✅ 可用 | Python 3.10.20，`E:/Programs/miniconda/envs/aicode/python.exe` |
| pandas / numpy | ✅ 已装 | pandas 2.3.3 / numpy 2.2.6 |
| ib_insync | ✅ 已装 | 0.9.86 |
| Git 仓库 + 远程推送 | ✅ 可用 | SSH 已认证 (`vampireli1900-dev`)，`origin/main` 可推 |
| PyPI 安装 | ✅ 可用 | 可正常下载 wheel（matplotlib 8.2MB 实测通过） |
| 盈透 TWS 连接 | ✅ 已连 | 127.0.0.1:7497，0.4s 连上 |
| 盈透账户 | ✅ 已登录 | `DUT097472`（**DU 前缀 = 模拟账户 Paper**），净值 ≈ HKD 1,003,383 |
| 历史 K 线 | ✅ 可用 | 日线/小时线/5min/1min，最长 5 年日线 |
| 实时/延迟行情 | ⚠️ 无订阅 | 报错 10089：需额外市场数据订阅 |
| 下单权限 | ✅ 已开启 | TWS 配置 `readOnlyApi="false"` |
| 磁盘 / 写权限 | ✅ 充足 | D 盘剩余 131 GB，项目目录可写 |

---

## 二、盈透 API 详细结果

### 1. 连接与账户
```
连接 127.0.0.1:7497 clientId=91  → 成功，耗时 0.40s
managedAccounts = ['DUT097472']     (DU 开头 = 模拟盘)
AccountType     = INDIVIDUAL
NetLiquidation  ≈ 1,003,383 HKD
BuyingPower     ≈ 6,689,226 HKD
当前持仓        = 无
未成交委托      = 无
```

### 2. TWS 配置（读自 `E:\Programs\tws\...\tws.xml` / `jts.ini`）
```
readOnlyApi        = false     ← 可下单（非只读）
allowOnlyLocalhost = true      ← 仅允许本机连接
socketClient       = true, port = 7497
tradingMode        = p         ← paper 模拟盘
Locale             = zh_CN, 时区 Asia/Shanghai
```

### 3. 合约资格
| 标的 | conId | 状态 |
|------|-------|------|
| AAPL | 265598 | ✅ |
| SPY | 756733 | ✅ |
| MSFT | 272093 | ✅ |
| IWM / DIA | 9579970 / 73128548 | ✅ |
| 700.HK 腾讯 | 152791428 | ✅ (SEHK) |
| 2800.HK 盈富基金 | 6244735 | ✅ (SEHK) |
| BABA | 166090175 | ✅ |
| EURUSD 外汇 | 12087792 | ⚠️ 历史数据需订阅，返回 0 根 |

### 4. 历史数据可得性（AAPL）
| 周期 | 时长 | 根数 |
|------|------|------|
| 1 day | 1 Y | 251 |
| 1 day | 5 Y | 1255 |
| 1 hour | 2 Y | 3492 |
| 5 mins | 1 M | 1716 |
| 1 min | 1 M | 8580 |

多标的日线 2 年批量拉取 10 只全部成功（SPY/QQQ/MSFT/NVDA/TSLA/AMD/META/GOOGL/AMZN/JPM）。

### 5. 限频（pacing）
连续 5 次快速请求 1min 数据，全部成功（各 390 根），无 162 限频报错。
→ 平台侧仍会加 1.2s 间隔 + 重试 + 本地缓存以策安全。

### 6. 实时行情 ⚠️
```
reqMarketDataType(1 实时/3 延迟/4 延迟冻结) → last/bid/ask 全为 nan
Error 10089: 该合约市场数据需额外订阅
```
**影响与对策**：平台不依赖 tick 流；以「历史 K 线 + 定时增量刷新」为数据基础，
实盘/监控模块走"轮询最新 bar"而非订阅流式行情。

---

## 三、其他环境

- **Git**：`git@github.com:vampireli1900-dev/quant-learning.git`，
  `ssh -T git@github.com` 认证通过；`git push --dry-run` 正常；当前分支 `main`，工作区干净。
- **基线代码**：`main.py` 可正常运行（合成数据三策略回测打印正常，仅缺 matplotlib 绘图）。
- **依赖补充（已全部安装完成）**：
  | 包 | 版本 | 用途 |
  |----|------|------|
  | PyYAML | 6.0.3 | 实验配置文件 |
  | tqdm | 4.68.4 | 进度条 |
  | matplotlib | 3.10.9 | 资金曲线/对比图表 |
  | scipy | 1.15.3 | 统计检验 |
  | scikit-learn | 1.7.2 | 机器学习策略 |
  | yfinance | 1.7.0 | 备用免费数据源 |
  | SQLAlchemy | 2.0.55 | SQL 数据库 ORM（默认 SQLite，可切 PostgreSQL / MySQL） |

---

## 三·补、数据库环境

| 项目 | 状态 |
|------|------|
| 默认数据库 | SQLite：`<项目根>/data/quant.db`（WAL 模式，自动创建） |
| 表数量 | 14 张（行情 3 / 训练集 2 / 实验 5 / 模型 1 / 实盘 3） |
| 写入方式 | 旁路写入（bypass）—— 数据库故障绝不中断取数/回测/下单 |
| 幂等性 | `bars` 按 (标的, 周期, 时间戳) 唯一约束 + upsert，可重复执行 |
| 只读保护 | `run_sql()` 只放行 SELECT / WITH / EXPLAIN / PRAGMA，拦截写操作与多语句 |
| 迁移能力 | `db-import` 一键把历史 CSV 缓存搬进库；`QP_DATABASE_URL` 切 PG/MySQL |
| 落库内容 | API 抓取日志、OHLCV、训练集、实验/回测指标、逐笔交易、委托、账户与持仓快照 |

> 注：`data/*.db*` 已加入 `.gitignore`——数据库是可随时重建的派生数据，不入库。

---

## 四、对平台设计的约束（重要）

1. **数据是历史 K 线，不是实时流** → 回测/实验平台完全可用；实盘监控走轮询。
2. **账户是模拟盘** → 可以放心做下单链路联调，但默认仍以"只读 + dry-run"为安全默认值。
3. **IB 历史请求有限频** → 必须做本地缓存（避免重复拉取、加速实验迭代）。
4. **Python 3.10** → 依赖选择需兼容（sklearn/scipy 均有 cp310 wheel）。
