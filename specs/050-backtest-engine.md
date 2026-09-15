# Spec 050: 回测引擎

> 状态：Draft
> 最后更新：2026-09-15

## 目标

输入一个 `Strategy` + 历史价格数据，输出资金曲线 + 调仓事件 + 性能指标。引擎必须是**确定性的**：相同输入永远产出相同输出（不能有随机性）。

## 不在范围内

- 实时交易（spec 045 实盘跟踪另说）
- 期权/期货/杠杆
- 做空
- 税费模型（按统一 10 bps 双向手续费 + 0 bps 印花税简化处理）

## API 概览

```python
from datetime import date
from decimal import Decimal
from global_allocation.backtest import BacktestEngine
from global_allocation.models import Strategy
from global_allocation.data import fetch_many

strategy = ...  # Strategy 对象
prices = fetch_many(strategy.assets(), start=..., end=...)

engine = BacktestEngine(
    initial_capital=Decimal("100000"),
    cost_bps=Decimal("10"),         # 0.10% 双向手续费
    slippage_bps=Decimal("0"),      # MVP 不模拟滑点
)

result = engine.run(strategy=strategy, prices=prices)

print(result.metrics.cagr)
print(result.equity_curve.tail())
print(len(result.rebalance_events), "次再平衡")
```

## 核心算法

```
inputs:
  - prices: DataFrame, columns=asset.symbol, index=Date, values=Adj Close
  - strategy: Strategy
  - initial_capital: Decimal

state:
  - positions: dict[symbol, shares]      # 当前持有
  - cash: Decimal
  - last_rebalance: date | None

loop over each trading day t:
    if today is rebalance day (per strategy.rebalance):
        target_weights = strategy.weights_at(t)
        rebalance_to(target_weights, prices[t])

    nav = sum(positions[s] * prices[t][s] for s) + cash
    record snapshot at t

return BacktestResult
```

### 再平衡子函数

```python
def rebalance_to(target_weights, current_prices):
    """调仓到目标权重。

    算法：
    1. 算当前总市值 NAV
    2. 算每个标的的目标市值 = NAV * target_weight
    3. 算每个标的的 delta shares = (target_value - current_value) / price
    4. 按 delta 正负买入/卖出，扣手续费
    """
    pass
```

### 再平衡触发条件

- **按 schedule**：`rebalance.frequency == "monthly/quarterly/yearly"`，到日子就调
- **按 threshold**：`rebalance.threshold` 不为空，且当前权重偏离 target 超过 threshold，调
- **none**：永远不调（实际上变成 buy-and-hold）

两种可同时存在：到日子 OR 偏离超阈值，任一触发即调。

## 数据契约

涉及 [`specs/010-data-models.md`](010-data-models.md) 里的：

- `BacktestResult`
- `PortfolioSnapshot`
- `RebalanceEvent`
- `Trade`
- `PerformanceMetrics`（见 spec 060）

## 边界情况

1. **数据不齐** —— 某资产在某天缺价，跳过那天不交易（不假装 fill）
2. **现金不够** —— 卖出先于买入（避免负 cash），卖出仍要扣手续费
3. **目标权重因 rebalance 频率未到而不变** —— buy-and-hold 等价
4. **回测期内某资产中途上市** —— 实际从上市日开始持仓（按比例缩放其他持仓的初始权重）
5. **回测期内某资产退市** —— 最后有效日的 price 用于计算 NAV，退市后仓位不再变动
6. **初始资金为 0 或负** —— 报错
7. **价格数据为空** —— 报错
8. **手续费为负** —— 报错

## 验收标准

- [ ] `BacktestEngine.run()` 跑通一个简单 buy-and-hold 策略，NAV 单调增（除手续费外）
- [ ] 确定性：相同输入跑两次，结果 bit-exact 一致
- [ ] 再平衡事件记录完整（日期、触发原因、trades、cost）
- [ ] equity_curve 是 pandas DataFrame，index 是 Date
- [ ] `tests/unit/test_backtest_engine.py` 至少 10 个测试
- [ ] 覆盖率 ≥ 90%

## 依赖

- `specs/020-strategy-base.md`
- `specs/030-built-in-strategies.md`
- `specs/040-data-fetch.md`
- `specs/010-data-models.md`
- numpy / pandas

## 备注

- **MVP 不模拟滑点** —— 真实滑点要 order book 数据，本期做不到
- **现金股息不复投** —— yfinance 的 Adj Close 已经包含股息调整，复权价已经隐含股息再投资
- **手续费 10 bps** —— A 股双边约 30 bps（含印花税），美股约 5 bps。MVP 取中庸值 10 bps，可在 BacktestEngine 参数里覆盖
- **不模拟融资融券** —— 现金为负直接报错（不让账户"穿仓"）
- **向量化 vs 事件驱动** —— MVP 用日循环（清晰、易调试），vectorbt 优化留到 v0.2
