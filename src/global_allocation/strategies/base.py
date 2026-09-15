"""策略基类。

参照 specs/020-strategy-base.md。

所有策略（内置 + 用户自定义）都继承自 StrategyBase。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal
from typing import final

from global_allocation.models import RebalanceRule, Strategy, TargetWeight


class StrategyBase(ABC):
    """所有策略的基类。

    子类必须：
      - 定义类属性 ``id``, ``name``, ``description``, ``rebalance``
      - 实现 :meth:`build` 返回完整 Strategy 对象

    静态策略只需要实现 build()。
    动态策略（如 risk parity）重写 :meth:`weights_at`。
    """

    # 子类必须定义的类属性（无默认，强制子类显式赋值）
    id: str
    name: str
    description: str
    rebalance: RebalanceRule

    @abstractmethod
    def build(self) -> Strategy:
        """构造完整的 Strategy 对象（含 target_weights 和 rebalance rule）。"""

    def weights_at(self, as_of: date) -> list[TargetWeight]:
        """返回某一天的目标权重。默认调用 build().target_weights。

        动态策略重写此方法。
        """
        return self.build().target_weights

    @final
    def validate(self) -> None:
        """校验策略合法性：权重和 = 1.0，无重复标的，每个 weight ∈ [0, 1]。

        这是 @final，子类不能重写——这是策略正确性的不变量。
        """
        strategy = self.build()
        targets = strategy.target_weights

        # 至少需要 1 个标的（build 时 Pydantic 也强制了；这里 defense-in-depth）
        if not targets:
            raise ValueError(f"{self.id}: 至少需要 1 个标的")

        # 权重和 = 1.0（容忍 0.0001 二进制浮点误差）
        total = sum(tw.weight for tw in targets)
        tolerance = Decimal("0.0001")
        if abs(total - Decimal("1.0")) > tolerance:
            raise ValueError(
                f"{self.id}: 权重和 = {total}, 必须 = 1.0 (允许误差 {tolerance})"
            )

        # 无重复标的
        seen: set[str] = set()
        for tw in targets:
            if tw.asset.symbol in seen:
                raise ValueError(f"{self.id}: 重复标的 {tw.asset.symbol}")
            seen.add(tw.asset.symbol)

            # weight ∈ [0, 1]（Pydantic 在 build 时就应拦住，这里再次防御）
            if not (Decimal("0") <= tw.weight <= Decimal("1")):
                raise ValueError(
                    f"{self.id}: 权重 {tw.weight} (标的 {tw.asset.symbol}) 越界 [0, 1]"
                )


__all__ = ["StrategyBase"]
