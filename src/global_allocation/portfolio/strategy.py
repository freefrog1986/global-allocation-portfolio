"""大类资产配置策略（spec 097 第二十一轮 — liubo 2026-09-24：整数仓位模型）。

设计：
- 11 个 SwensenClass 子类（含 CASH）各持整数份仓位。
- 1 份 = 1 万 CNY（unit_size 默认 10000）。
- bounds = 单边浮动份数（默认 2，下限 = max(0, target - bound)）。
- cash_range = (min, max) 现金仓位区间（绝对份数）。

公式：
- 总仓位 = sum(positions.values()) × unit_size
- 投资部分 = (总仓位 - CASH 份) × unit_size
- target_weight(sub) = positions[sub] / sum(positions.values())

为什么改成整数仓位（liubo 2026-09-24 拍板）：
- 旧 Layer 1/2/3 权重模型（四层 dataclass + 动态公式）过于工程化。
- 权重小数（如 19.6%）用户难以直观理解和操作。
- 整数仓位更接近实盘："A 股持 11 仓 / 港股 4 仓 / 美债 1 仓" — 整数清楚明白。
- target 不再随现金占比动态缩放（"动态缩放"心智负担大）— 直接是固定整数份。

数字依据（spec 097 第二十一轮 liubo 2026-09-24 给值）：
- 现金 20 仓（区间 [10, 30]）— 总仓位含约 1/3 现金当子弹
- A 股 11 仓（最大头，国内为主）
- 美股 6 仓
- 港股 4 仓 / 国外发达 4 仓
- 新兴市场 3 仓
- 国内 REITs 4 仓 / 美国 REITs 2 仓
- 国内利率债 3 仓 / 美债 1 仓
- 商品 2 仓
- 11 子类合计 = 60 仓 × 1 万 = 60 万 CNY
- 投资部分 = 40 仓 × 1 万 = 40 万 CNY（现金 1/3）

后续演进：
- 策略数字存本模块的 `DEFAULT_POSITION_ALLOCATION` 常量（frozen dataclass，后续可改）
- 不存数据库
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping

from global_allocation.portfolio.breakdown import SwensenClass


@dataclass(frozen=True, slots=True)
class PositionAllocation:
    """整数仓位分配（spec 097 第二十一轮 — liubo 2026-09-24）。

    11 个子类（含 CASH）各持整数份仓位 + 浮动范围 + 单份金额。

    字段：
    - positions[subclass] = 默认持仓份数（整数）
    - bounds[subclass] = 单边浮动份数（默认 2，下限 = max(0, target - bound)）
    - cash_range = (min, max) 现金仓位区间（绝对份数）
    - unit_size = 单份金额（默认 1 万 CNY = Decimal("10000")）

    公式：
    - 总仓位 = sum(positions.values()) × unit_size
    - 投资部分 = (总仓位 - CASH 份) × unit_size
    - target_weight(sub) = positions[sub] / sum(positions.values())

    不变量：
    - CASH 必须在 positions 里（整数仓位模型的关键：现金也是"仓"）
    - CASH 不在 bounds 里（cash_range 单独处理现金的浮动区间）
    - 所有 positions >= 0，所有 bounds >= 0
    - cash_range[0] <= cash_range[1]，都 >= 0
    - unit_size > 0
    """

    positions: Mapping[SwensenClass, int]
    bounds: Mapping[SwensenClass, int]
    cash_range: tuple[int, int]
    unit_size: Decimal = Decimal("10000")

    def __post_init__(self) -> None:
        # CASH 必须在 positions 里
        if SwensenClass.CASH not in self.positions:
            raise ValueError("positions 必须包含 CASH（整数仓位模型：现金也是仓）")
        # CASH 不在 bounds 里（cash_range 单独管）
        if SwensenClass.CASH in self.bounds:
            raise ValueError("CASH 不能在 bounds 里（用 cash_range 控制现金浮动区间）")
        # 所有 positions >= 0
        for sub, count in self.positions.items():
            if count < 0:
                raise ValueError(f"positions[{sub}] = {count} 不能为负")
        # 所有 bounds >= 0
        for sub, bound in self.bounds.items():
            if bound < 0:
                raise ValueError(f"bounds[{sub}] = {bound} 不能为负")
        # cash_range min <= max，都 >= 0
        min_cash, max_cash = self.cash_range
        if min_cash < 0:
            raise ValueError(f"cash_range[0] = {min_cash} 不能为负")
        if max_cash < min_cash:
            raise ValueError(f"cash_range[1] = {max_cash} 不能小于 cash_range[0] = {min_cash}")
        # unit_size > 0
        if self.unit_size <= 0:
            raise ValueError(f"unit_size = {self.unit_size} 必须 > 0")

    @property
    def total_positions(self) -> int:
        """总仓位份数（11 子类之和，含 CASH）。"""
        return sum(self.positions.values())

    @property
    def total_capital(self) -> Decimal:
        """总市值 = total_positions × unit_size（CNY）。"""
        return Decimal(self.total_positions) * self.unit_size

    @property
    def investment_capital(self) -> Decimal:
        """投资部分市值 = (总仓位 - CASH 份) × unit_size（CNY，不含现金）。"""
        cash_count = self.positions[SwensenClass.CASH]
        return Decimal(self.total_positions - cash_count) * self.unit_size

    @property
    def cash_position_count(self) -> int:
        """CASH 的目标份数（= positions[CASH]）。"""
        return self.positions[SwensenClass.CASH]

    @property
    def cash_weight(self) -> Decimal:
        """现金目标占比 = cash_count / total_positions（0~1）。"""
        total = self.total_positions
        if total == 0:
            return Decimal("0")
        return Decimal(self.cash_position_count) / Decimal(total)

    def target_position(self, subclass: SwensenClass) -> int:
        """某子类的目标份数（= positions[subclass]）。"""
        return self.positions[subclass]

    def min_position(self, subclass: SwensenClass) -> int:
        """某子类的下限份数。

        - CASH → cash_range[0]
        - 投资子类 → max(0, target - bound)
        """
        if subclass == SwensenClass.CASH:
            return self.cash_range[0]
        target = self.target_position(subclass)
        bound = self.bounds.get(subclass, 0)
        return max(0, target - bound)

    def max_position(self, subclass: SwensenClass) -> int:
        """某子类的上限份数。

        - CASH → cash_range[1]
        - 投资子类 → target + bound
        """
        if subclass == SwensenClass.CASH:
            return self.cash_range[1]
        target = self.target_position(subclass)
        bound = self.bounds.get(subclass, 0)
        return target + bound

    def target_weight(self, subclass: SwensenClass) -> Decimal:
        """某子类的目标权重 = positions[subclass] / total_positions（0~1）。

        跟旧 compute_subclass_actual_target() 不同：
        - 旧版 = subclass × super × (1 - 现金%) — 动态缩放公式
        - 新版 = 固定整数比 — 心智负担小，跟 cash_range 解耦
        """
        total = self.total_positions
        if total == 0:
            return Decimal("0")
        return Decimal(self.target_position(subclass)) / Decimal(total)

    def target_amount(self, subclass: SwensenClass) -> Decimal:
        """某子类的目标金额（CNY）= positions[subclass] × unit_size。"""
        return Decimal(self.target_position(subclass)) * self.unit_size

    def cash_status(self, current_cash_weight: Decimal) -> str:
        """当前现金占比的状态文本（卡片展示用）。

        - 现金占比 < cash_range[0]/total_positions → "低于下限"
        - 现金占比 > cash_range[1]/total_positions → "高于上限"
        - 其它 → "区间内"

        用 current_cash_weight 输入是为了跟 breakdown 的 weight 数据对齐（0~1 Decimal）。
        """
        total = self.total_positions
        if total == 0:
            return "区间内"  # 兜底：total = 0 → 当作区间内
        min_weight = Decimal(self.cash_range[0]) / Decimal(total)
        max_weight = Decimal(self.cash_range[1]) / Decimal(total)
        if current_cash_weight < min_weight:
            return "低于下限"
        if current_cash_weight > max_weight:
            return "高于上限"
        return "区间内"


# 默认整数仓位分配（spec 097 第二十一轮 — liubo 2026-09-24 给值）
#
# 数字依据：
# - 总仓位 60 仓 × 1 万 = 60 万 CNY（含现金）
# - 现金 20 仓（区间 [10, 30]）— 1/3 仓位当子弹，区间对应权重 [16.7%, 50%]
# - A 股 11 仓（最大头，国内为主 — 权重 18.3%）
# - 美股 6 仓（QDII 费用 + 汇率风险控制）
# - 港股 4 仓 / 国外发达 4 仓（海外分散）
# - 新兴市场 3 仓（小额分散）
# - 国内 REITs 4 仓 / 美国 REITs 2 仓（抗通胀）
# - 国内利率债 3 仓 / 美债 1 仓（避险）
# - 商品 2 仓（黄金抗通胀）
#
# bounds 默认都是 2（投资子类）：单边浮动 2 仓，下限允许 0（target - 2 >= 0 时）。
# 例：A 股 11 仓 → 区间 [9, 13] 仓；港股 4 仓 → 区间 [2, 6] 仓。
DEFAULT_POSITION_ALLOCATION = PositionAllocation(
    positions={
        SwensenClass.CN_EQUITY: 11,
        SwensenClass.US_EQUITY: 6,
        SwensenClass.HK_EQUITY: 4,
        SwensenClass.FOREIGN_DM_EQUITY: 4,
        SwensenClass.EM_EQUITY: 3,
        SwensenClass.CN_REIT: 4,
        SwensenClass.US_REIT: 2,
        SwensenClass.CN_GOV_BOND: 3,
        SwensenClass.US_BOND: 1,
        SwensenClass.COMMODITY: 2,
        SwensenClass.CASH: 20,
    },
    bounds={
        SwensenClass.CN_EQUITY: 2,
        SwensenClass.US_EQUITY: 2,
        SwensenClass.HK_EQUITY: 2,
        SwensenClass.FOREIGN_DM_EQUITY: 2,
        SwensenClass.EM_EQUITY: 2,
        SwensenClass.CN_REIT: 2,
        SwensenClass.US_REIT: 2,
        SwensenClass.CN_GOV_BOND: 2,
        SwensenClass.US_BOND: 2,
        SwensenClass.COMMODITY: 2,
    },
    cash_range=(10, 30),
    unit_size=Decimal("10000"),
)


__all__ = [
    "PositionAllocation",
    "DEFAULT_POSITION_ALLOCATION",
]