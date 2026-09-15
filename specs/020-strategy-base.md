# Spec 020: 策略基类 API

> 状态：Draft
> 最后更新：2026-09-15

## 目标

定义所有策略必须实现的接口：抽象基类 `Strategy`，让内置策略和用户自定义策略走同一套代码路径。

## 不在范围内

- 具体的策略实现（spec 030）
- 策略文件加载（spec 040 / 070 包含）

## API 概览

```python
from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal
from global_allocation.models import Strategy, TargetWeight

class StrategyBase(ABC):
    """所有策略的基类。

    子类必须定义类属性 id/name/description，并实现 build() 方法。
    静态策略（权重不随时间变）只需实现 build()。
    动态策略（如 risk parity）需要重写 weights_at(date) 返回该日期的目标权重。
    """

    id: str
    name: str
    description: str
    rebalance: RebalanceRule

    @abstractmethod
    def build(self) -> Strategy:
        """构造完整的 Strategy 对象（含 target_weights 和 rebalance rule）。

        对于静态策略，每次调用返回的 target_weights 必须一致。
        对于动态策略，可返回基准权重，运行时通过 weights_at() 覆盖。
        """

    def weights_at(self, as_of: date) -> list[TargetWeight]:
        """返回某一天的目标权重。默认调用 build().target_weights。

        动态策略（如 risk parity）重写此方法。
        """
        return self.build().target_weights

    @final
    def validate(self) -> None:
        """校验策略合法性：权重和 = 1.0，无重复标的，每个 weight ∈ [0, 1]。"""
        s = self.build()
        total = sum(tw.weight for tw in s.target_weights)
        if abs(total - Decimal("1.0")) > Decimal("0.0001"):
            raise ValueError(f"{self.id}: 权重和 = {total}, 必须 = 1.0")
        seen = set()
        for tw in s.target_weights:
            if tw.weight < 0 or tw.weight > 1:
                raise ValueError(f"{self.id}: 权重 {tw.weight} 越界")
            if tw.asset.symbol in seen:
                raise ValueError(f"{self.id}: 重复标的 {tw.asset.symbol}")
            seen.add(tw.asset.symbol)
```

## 数据契约

涉及 [`specs/010-data-models.md`](010-data-models.md) 里的：

- `Strategy`
- `TargetWeight`
- `RebalanceRule`
- `Asset` / `AssetClass` / `Region` / `Currency`

## 边界情况

1. **权重和略偏离 1.0**（如 `0.6 + 0.4 = 0.9999...`）—— 容忍误差 ≤ 0.0001，否则报错
2. **同一资产出现两次** —— 报重复标的错误
3. **权重为负** —— 报越界错误
4. **空 target_weights 列表** —— 报"至少需要 1 个标的"错误
5. **动态策略 `weights_at()` 抛异常** —— 不被吞，原样上抛
6. **`build()` 不是幂等的**（返回不同对象）—— 这是允许的，只要 `target_weights` 内容一致

## 验收标准

- [ ] `StrategyBase` 是 `ABC`，不能直接实例化
- [ ] `validate()` 覆盖所有边界情况
- [ ] `tests/unit/test_strategy_base.py` 至少 6 个测试
- [ ] 覆盖率 ≥ 90%

## 依赖

- pydantic（数据 model）
- decimal（金额计算）
- abc（抽象基类）

## 备注

- 用 `@abstractmethod` 而不是 `NotImplementedError` —— Pythonic 且能在实例化时立即报错
- `validate()` 是 `@final`，子类不能改 —— 这是策略正确性的不变量
- 动态策略（如 risk parity）的 `weights_at()` 需要历史价格数据，由回测引擎传入（spec 050 处理）
