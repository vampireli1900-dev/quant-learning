# 量化交易入门学习项目（quant_learning）

一套**零依赖门槛**（只需 pandas + numpy）、**中文注释**、**开箱即跑**的量化交易学习代码。
目标是让你理解一条完整的量化策略流水线，而不是给一个能赚钱的黑盒。

## 一句话看懂量化交易

> 用历史数据（回测）验证一条机械交易规则过去能不能赚钱、风险多大，再决定要不要拿去实盘。

五个环节，正好对应五个文件：

```
data.py        数据      —— 从哪拿 K 线（现在用"假数据"，保证离线能跑）
indicators.py  指标      —— 把价格算成 SMA / EMA / RSI / MACD / 布林带
strategy.py    策略      —— 把指标翻译成"每天买/卖"的信号(0/1)
backtest.py    回测      —— 按信号模拟交易，算收益、风险、胜率
main.py        入口      —— 把上面串起来跑一遍给你看结果
```

## 怎么运行

用你的 `aicode` conda 环境（已装 pandas / numpy / ib_insync）：

```bash
cd d:/pycharm_file/2026_stork/quant_learning
E:/Programs/miniconda/envs/aicode/python.exe main.py
```

在 PyCharm 里更方便：直接用 `aicode` 解释器打开 `main.py` 点运行即可。

（可选）想画资金曲线图：`pip install matplotlib`，`main.py` 末尾会自动画图。

## 三个内置策略（从易到难）

| 策略 | 思路 | 类型 |
|------|------|------|
| 双均线交叉 (20/50) | 快线上穿慢线=金叉买入，下穿=死叉卖出 | 趋势跟随 |
| RSI 均值回归 (14) | 超卖(RSI<30)买入，超买(RSI>70)卖出 | 均值回归 |
| 布林带突破 (20,2) | 突破上轨买入，跌破中轨卖出 | 突破 |

## 你需要理解的核心概念（建议按顺序读代码）

1. **无未来函数（look-ahead bias）** —— 见 `backtest.py` 里的 `signal.shift(1)`。
   今天持仓只能用昨天的信号，否则等于"作弊"，回测再好也是假的。
2. **交易成本** —— 每换一次仓收一次手续费（`backtest.py` 里的 `turnover`）。
   很多策略"看着赚钱，扣掉手续费就不赚了"。
3. **风险比收益重要** —— 别只看总收益率，要看**最大回撤**（最惨能亏多少）和
   **夏普比率**（每承担一单位风险赚多少）。
4. **过拟合** —— 参数(如 20/50)调得太好，往往只是"拟合了历史噪音"，实盘失效。
   这是新手最容易踩的坑，也是量化最大的难点。

## 下一步怎么练

1. 改 `main.py` 里的参数（比如均线改成 5/20、RSI 阈值改成 20/80），观察结果怎么变。
2. 在 `strategy.py` 里照着现有模板，自己写一个新策略（例如 MACD 金叉死叉）。
3. 把 `data.py` 文件末尾的 TODO 接上**真实数据**：
   - 用你 `t.py` 里的 ib_insync 拉盈透真实 K 线，或
   - `pip install yfinance` 免费下载美股数据。
4. 换不同股票/周期回测，看看同一策略在别处是否还能成立（这叫"鲁棒性"）。

## 推荐的进阶资源（GitHub 项目）

| 项目 | 一句话说明 | 适合 |
|------|-----------|------|
| [vnpy/vnpy](https://github.com/vnpy/vnpy) | 中文量化交易框架，支持盈透 IB、回测+实盘一体，国内生态最成熟 | 中文用户、想接实盘 |
| [mementum/backtrader](https://github.com/mementum/backtrader) | 最经典的 Python 回测框架，文档/例子极多 | 入门学回测 |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | 极快的向量化回测，适合大规模参数寻优 | 进阶、批量研究 |
| [microsoft/qlib](https://github.com/microsoft/qlib) | 微软的 AI 量化平台，机器学习和因子挖掘方向 | 想走 AI/ML 量化 |
| [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL) | 深度强化学习做交易，学术项目配套论文 | 想做 DRL 交易 |
| [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | 专业级多资产算法交易引擎(C#/Python) | 想做严肃系统 |

### 学习路线建议

1. 先吃透本项目的 5 个文件，理解"回测的坑"（未来函数、成本、过拟合）。
2. 用 **backtrader** 把同样的双均线策略再实现一遍，体会成熟框架怎么抽象。
3. 中文社区：注册**聚宽(JoinQuant)**、**米筐(RiceQuant)** 玩在线的研究+回测环境。
4. 有数据/ML 基础后，再看 **qlib / FinRL** 走 AI 方向。
5. 全程记住：**量化交易的难点从来不是代码，而是策略逻辑本身是否稳健**。

> 免责声明：本项目仅用于学习，任何策略回测结果都不构成投资建议。
