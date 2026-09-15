# Spec 060: 性能指标

> 状态：Draft
> 最后更新：2026-09-15

## 目标

从 equity curve 计算标准的组合业绩指标：CAGR、夏普、最大回撤、波动率、年化收益、相关系数矩阵。

## 不在范围内

- 因子分析（Fama-French）
- 风险贡献分解
- 压力测试 / 蒙特卡洛

## API 概览

```python
from global_allocation.backtest.metrics import compute_metrics

metrics = compute_metrics(
    equity_curve=result.equity_curve,   # DataFrame
    risk_free_rate=Decimal("0.02"),     # 年化无风险利率，默认 2%
    trading_days_per_year=252,
)

print(metrics.cagr)            # Decimal
print(metrics.sharpe)           # Decimal
print(metrics.max_drawdown)     # Decimal（负数，如 -0.25）
print(metrics.volatility)      # Decimal（年化波动率）
print(metrics.correlation)     # pd.DataFrame，资产间相关性
```

## 指标定义

### CAGR（年化复合增长率）

```
CAGR = (NAV_end / NAV_start) ** (252 / trading_days) - 1
```

### Sharpe Ratio（年化）

```
daily_returns = NAV.pct_change().dropna()
excess = daily_returns - risk_free_rate / 252
sharpe = excess.mean() / excess.std() * sqrt(252)
```

### Max Drawdown（最大回撤）

```
peak = NAV.cummax()
drawdown = (NAV - peak) / peak
max_drawdown = drawdown.min()   # 负数
```

### Volatility（年化波动率）

```
daily_returns = NAV.pct_change().dropna()
volatility = daily_returns.std() * sqrt(252)
```

### Correlation Matrix

```python
returns = prices.pct_change().dropna()  # wide-format 各资产日收益
correlation = returns.corr()             # pd.DataFrame
```

## 数据契约

```python
class PerformanceMetrics(BaseModel):
    cagr: Decimal
    sharpe: Decimal
    max_drawdown: Decimal
    volatility: Decimal
    total_return: Decimal
    annual_return: Decimal
    correlation: pd.DataFrame       # index/columns 都是 asset symbol
    best_day: Decimal
    worst_day: Decimal
    win_rate: Decimal               # 正收益天数占比
```

## 边界情况

1. **NAV 全程不变化**（如 buy-and-hold 没波动）—— `std=0` → sharpe 返回 `inf` 或 NaN，按 inf 处理
2. **NAV 单调下降** —— `cagr < 0`，`max_drawdown < 0`，OK
3. **数据 < 30 天** —— 警告但不阻断（指标照样算）
4. **空 equity_curve** —— 报错
5. **有 NaN** —— 用 `dropna()`，但记录丢了多少行
6. **风险利率 0** —— OK
7. **负收益的 std** —— std 永远非负，OK

## 验收标准

- [ ] `compute_metrics()` 在 equity_curve 是单调增/单调减/震荡三种场景下都正确
- [ ] `sharpe` 在 std=0 时返回 `inf` 而不是 NaN
- [ ] `max_drawdown` 永远是 ≤ 0
- [ ] `correlation` 是对称矩阵，对角线 = 1.0
- [ ] `tests/unit/test_metrics.py` 至少 8 个测试，含手算 reference 案例
- [ ] 覆盖率 ≥ 95%（纯计算）

## 依赖

- `specs/010-data-models.md`
- numpy / pandas

## 备注

- **Decimal vs float**：计算内部用 numpy（float），输出 cast 回 Decimal（4 位小数）
- **Trading days**：默认 252（美股），A 股 244；MVP 统一 252
- **年化方式**：√252（简单几何）vs (1+r)^252 - 1（精确）—— MVP 用简单几何，足够准确
- **Sortino / Calmar**：MVP 不做，留 v0.2
