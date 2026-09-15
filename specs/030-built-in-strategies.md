# Spec 030: 4 个内置策略

> 状态：Draft
> 最后更新：2026-09-15

## 目标

提供 4 个经典全球资产配置策略的实现：60/40、永久组合、全天候、风险平价。覆盖最常见的"教科书"组合，作为内置参考。

## 不在范围内

- 优化器（如 Black-Litterman）—— 后续 spec 120
- 自适应/动量策略 —— 后续 spec 130
- 个性化建议（基于年龄/风险偏好） —— 不做

## 4 个策略

### 1. `60_40` — 经典 60/40

> "60% 股票 + 40% 债券"的传统退休金组合。

```yaml
id: 60_40
name: 60/40 经典股债
target_weights:
  - symbol: VT     # 全球股票 ETF（Vanguard Total World Stock）
    weight: 0.60
    asset_class: equity
    region: global
    currency: usd
  - symbol: BND    # 全债 ETF（Vanguard Total Bond Market）
    weight: 0.40
    asset_class: bond
    region: us
    currency: usd
rebalance: yearly
base_currency: usd
inception: 2007-09-26   # BND 成立日
```

学术支撑：Brinson, Hood, Beebower (1986)；被金融从业者引用 50 年。

### 2. `permanent_portfolio` — 永久组合

> Harry Browne 的四等分组合：股、债、金、现金各 25%。

```yaml
id: permanent_portfolio
name: 永久组合（Harry Browne）
target_weights:
  - symbol: VTI       # 美股
    weight: 0.25
  - symbol: AGG       # 美债
    weight: 0.25
  - symbol: GLD       # 黄金
    weight: 0.25
  - symbol: SHY       # 短期国债（作为现金）
    weight: 0.25
rebalance: yearly
base_currency: usd
inception: 2004-11-18  # GLD 成立日
```

学术支撑：Browne (1999) "Fail-Safe Investing"。

### 3. `all_weather` — 桥水全天候

> Ray Dalio 的"经济周期四象限"组合：通过分散通胀/通缩、增长/衰退两个维度来抗周期。

```yaml
id: all_weather
name: 桥水全天候（简化版）
target_weights:
  - symbol: VTI       # 股票（增长）
    weight: 0.30
  - symbol: TLT       # 长债（通缩）
    weight: 0.40
  - symbol: IEF       # 中债
    weight: 0.15
  - symbol: GLD       # 黄金（通胀）
    weight: 0.075
  - symbol: DJP       # 大宗商品（通胀）
    weight: 0.075
rebalance: yearly
base_currency: usd
inception: 2002-07-26  # TLT 成立日
```

注：这是简化版，桥水原版用杠杆 + 通胀挂钩债券 + 衍生品。零售版按等比例缩放。

### 4. `risk_parity` — 风险平价

> 不按金额配，按**风险贡献**配：每种资产对组合总风险的贡献相等。

实现不是静态权重，而是在回测时计算：
- 取过去 60 个交易日收益率
- 算每种资产的协方差矩阵
- 用迭代法求解权重 `w` 使 `w_i * (Σw)_i / sqrt(w'Σw)` 相等

```python
class RiskParityStrategy(StrategyBase):
    def build(self) -> Strategy:
        # 返回基准权重（等权作为 fallback）
        return Strategy(
            id="risk_parity",
            target_weights=[...等权...],
            ...
        )

    def weights_at(self, as_of: date) -> list[TargetWeight]:
        # 回测引擎调用时传入历史价格
        # 这里是动态计算 —— 见实现
        ...
```

学术支撑：Qian (2005) "Risk Parity Portfolios"；Bridgewater All Weather 底层思想。

## API 概览

```python
from global_allocation.strategies import get_strategy, list_strategies
from global_allocation.strategies.builtin import (
    SixtyForty,
    PermanentPortfolio,
    AllWeather,
    RiskParity,
)

# 用类
s = SixtyForty()
s.validate()

# 用注册表
strategy_cls = get_strategy("60_40")
s = strategy_cls()

# 列所有
for sid in list_strategies():
    print(sid)
```

注册表 `global_allocation.strategies.registry.STRATEGY_REGISTRY`：
```python
STRATEGY_REGISTRY: dict[str, type[StrategyBase]] = {
    "60_40": SixtyForty,
    "permanent_portfolio": PermanentPortfolio,
    "all_weather": AllWeather,
    "risk_parity": RiskParity,
}
```

## 验收标准

- [ ] 4 个策略类全部实现并通过 `validate()`
- [ ] 静态策略（60_40, permanent_portfolio, all_weather）权重和 = 1.0
- [ ] 风险平价策略的 `weights_at()` 输入协方差矩阵返回的权重满足风险贡献相等（±5%）
- [ ] 列表/注册表 API 可用
- [ ] `tests/unit/test_builtin_strategies.py` 至少 8 个测试
- [ ] 覆盖率 ≥ 85%

## 依赖

- `specs/020-strategy-base.md`
- `specs/010-data-models.md`
- numpy（risk parity 协方差计算）
- scipy.optimize（risk parity 权重求解）

## 备注

- ETF 选型以流动性优先（VT > ACWI 因为成立更早、跟踪误差小）
- Risk Parity 的回看窗口默认 60 天，可在策略 metadata 里覆盖
- 这 4 个是参考实现，不构成投资建议
