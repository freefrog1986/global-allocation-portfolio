"""测试 src/global_allocation/portfolio/strategy.py。

spec 097 第二十一轮（liubo 2026-09-24）：整数仓位模型替代 Layer 1/2/3 权重模型。
- 11 个 SwensenClass 子类（含 CASH）各持整数份仓位
- 1 份 = 1 万 CNY
- bounds = 单边浮动份数（默认 2）
- cash_range = (min, max) 现金仓位区间（绝对份数）

target_weight(sub) = positions[sub] / sum(positions.values()) — 不再动态缩放。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from global_allocation.portfolio.breakdown import SwensenClass
from global_allocation.portfolio.strategy import (
    DEFAULT_POSITION_ALLOCATION,
    PositionAllocation,
)


# ─── 构造测试 ────────────────────────────────────────────────────────────


class TestPositionAllocationConstruction:
    """PositionAllocation 构造时的字段验证。"""

    def _make_valid(self, **overrides: object) -> PositionAllocation:
        """构造一个合法默认 PositionAllocation（方便覆盖单个字段测试）。"""
        defaults: dict[str, object] = {
            "positions": {
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
            "bounds": {
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
            "cash_range": (10, 30),
            "unit_size": Decimal("10000"),
        }
        defaults.update(overrides)
        return PositionAllocation(**defaults)  # type: ignore[arg-type]

    def test_valid_construction_succeeds(self) -> None:
        """合法参数 → 构造成功。"""
        pa = self._make_valid()
        assert isinstance(pa, PositionAllocation)

    def test_missing_cash_in_positions_raises(self) -> None:
        """positions 必须包含 CASH（整数仓位模型：现金也是仓）。"""
        positions_no_cash = {
            SwensenClass.CN_EQUITY: 11,
            SwensenClass.US_EQUITY: 6,
        }
        with pytest.raises(ValueError, match="positions 必须包含 CASH"):
            self._make_valid(positions=positions_no_cash)

    def test_cash_in_bounds_raises(self) -> None:
        """CASH 不能在 bounds 里（cash_range 单独管）。"""
        bounds_with_cash = {SwensenClass.CASH: 2}
        with pytest.raises(ValueError, match="CASH 不能在 bounds 里"):
            self._make_valid(bounds=bounds_with_cash)

    def test_negative_position_raises(self) -> None:
        """positions 不能为负。"""
        positions = self._make_valid().positions.copy()
        positions[SwensenClass.CN_EQUITY] = -1
        with pytest.raises(ValueError, match="不能为负"):
            self._make_valid(positions=positions)

    def test_negative_bound_raises(self) -> None:
        """bounds 不能为负。"""
        bounds = self._make_valid().bounds.copy()
        bounds[SwensenClass.CN_EQUITY] = -2
        with pytest.raises(ValueError, match="不能为负"):
            self._make_valid(bounds=bounds)

    def test_negative_cash_min_raises(self) -> None:
        """cash_range[0] 不能为负。"""
        with pytest.raises(ValueError, match="不能为负"):
            self._make_valid(cash_range=(-1, 30))

    def test_cash_range_min_greater_than_max_raises(self) -> None:
        """cash_range[0] 不能 > cash_range[1]。"""
        with pytest.raises(ValueError, match="不能小于"):
            self._make_valid(cash_range=(30, 10))

    def test_zero_unit_size_raises(self) -> None:
        """unit_size 必须 > 0。"""
        with pytest.raises(ValueError, match="必须 > 0"):
            self._make_valid(unit_size=Decimal("0"))

    def test_negative_unit_size_raises(self) -> None:
        """unit_size 必须 > 0。"""
        with pytest.raises(ValueError, match="必须 > 0"):
            self._make_valid(unit_size=Decimal("-100"))

    def test_is_frozen(self) -> None:
        """PositionAllocation frozen — 不能修改字段。"""
        from dataclasses import FrozenInstanceError

        pa = self._make_valid()
        with pytest.raises(FrozenInstanceError):
            pa.positions = {}  # type: ignore[misc]


# ─── 属性测试 ──────────────────────────────────────────────────────────────


class TestPositionAllocationProperties:
    """PositionAllocation 派生属性。"""

    def test_total_positions_sums_all(self) -> None:
        """total_positions = sum(positions.values())。"""
        assert DEFAULT_POSITION_ALLOCATION.total_positions == 60

    def test_total_capital_is_total_times_unit_size(self) -> None:
        """total_capital = total_positions × unit_size。"""
        assert DEFAULT_POSITION_ALLOCATION.total_capital == Decimal("600000")

    def test_investment_capital_excludes_cash(self) -> None:
        """investment_capital = (total - cash_count) × unit_size。"""
        # 60 - 20 = 40 → 400000
        assert DEFAULT_POSITION_ALLOCATION.investment_capital == Decimal("400000")

    def test_cash_position_count_returns_cash_subclass(self) -> None:
        """cash_position_count = positions[CASH]。"""
        assert DEFAULT_POSITION_ALLOCATION.cash_position_count == 20

    def test_cash_weight_is_cash_over_total(self) -> None:
        """cash_weight = cash_count / total_positions。"""
        # 20 / 60 ≈ 0.3333
        assert DEFAULT_POSITION_ALLOCATION.cash_weight == Decimal("20") / Decimal("60")


# ─── 查询方法测试 ──────────────────────────────────────────────────────────


class TestTargetPosition:
    """target_position(sub) — 返回子类的目标份数。"""

    def test_investment_subclass_returns_position(self) -> None:
        """投资子类 → positions[sub]。"""
        assert DEFAULT_POSITION_ALLOCATION.target_position(SwensenClass.CN_EQUITY) == 11
        assert DEFAULT_POSITION_ALLOCATION.target_position(SwensenClass.US_EQUITY) == 6
        assert DEFAULT_POSITION_ALLOCATION.target_position(SwensenClass.HK_EQUITY) == 4
        assert DEFAULT_POSITION_ALLOCATION.target_position(SwensenClass.COMMODITY) == 2

    def test_cash_returns_cash_position(self) -> None:
        """CASH → positions[CASH]。"""
        assert DEFAULT_POSITION_ALLOCATION.target_position(SwensenClass.CASH) == 20


class TestMinMaxPosition:
    """min_position(sub) / max_position(sub) — 区间边界。"""

    def test_investment_subclass_min_uses_bound(self) -> None:
        """投资子类 min = max(0, target - bound)。"""
        # CN_EQUITY 11, bound 2 → min = 9
        assert DEFAULT_POSITION_ALLOCATION.min_position(SwensenClass.CN_EQUITY) == 9
        # US_BOND 1, bound 2 → min = max(0, -1) = 0（允许清仓）
        assert DEFAULT_POSITION_ALLOCATION.min_position(SwensenClass.US_BOND) == 0

    def test_investment_subclass_max_uses_bound(self) -> None:
        """投资子类 max = target + bound。"""
        assert DEFAULT_POSITION_ALLOCATION.max_position(SwensenClass.CN_EQUITY) == 13
        assert DEFAULT_POSITION_ALLOCATION.max_position(SwensenClass.US_EQUITY) == 8

    def test_cash_min_returns_cash_range_low(self) -> None:
        """CASH min = cash_range[0]。"""
        assert DEFAULT_POSITION_ALLOCATION.min_position(SwensenClass.CASH) == 10

    def test_cash_max_returns_cash_range_high(self) -> None:
        """CASH max = cash_range[1]。"""
        assert DEFAULT_POSITION_ALLOCATION.max_position(SwensenClass.CASH) == 30


class TestTargetWeight:
    """target_weight(sub) — 子类目标权重（0~1，固定整数比，不动态缩放）。"""

    def test_cn_equity_target_weight(self) -> None:
        """A 股权重 = 11 / 60 ≈ 0.1833。"""
        expected = Decimal("11") / Decimal("60")
        assert DEFAULT_POSITION_ALLOCATION.target_weight(SwensenClass.CN_EQUITY) == expected

    def test_cash_target_weight(self) -> None:
        """现金权重 = 20 / 60 ≈ 0.3333。"""
        expected = Decimal("20") / Decimal("60")
        assert DEFAULT_POSITION_ALLOCATION.target_weight(SwensenClass.CASH) == expected

    def test_all_11_weights_sum_to_one(self) -> None:
        """11 子类权重和 = 1.0（保证 PositionAllocation 是完备的份额模型）。"""
        total = sum(
            DEFAULT_POSITION_ALLOCATION.target_weight(s) for s in SwensenClass
        )
        assert total == Decimal("1")


class TestTargetAmount:
    """target_amount(sub) — 子类目标金额（CNY）。"""

    def test_investment_subclass_amount(self) -> None:
        """投资子类 = positions[sub] × unit_size。"""
        # CN_EQUITY 11 × 10000 = 110000
        assert DEFAULT_POSITION_ALLOCATION.target_amount(SwensenClass.CN_EQUITY) == Decimal("110000")
        # US_BOND 1 × 10000 = 10000
        assert DEFAULT_POSITION_ALLOCATION.target_amount(SwensenClass.US_BOND) == Decimal("10000")

    def test_cash_amount(self) -> None:
        """现金 = 20 × 10000 = 200000。"""
        assert DEFAULT_POSITION_ALLOCATION.target_amount(SwensenClass.CASH) == Decimal("200000")


# ─── 现金状态测试 ──────────────────────────────────────────────────────────


class TestCashStatus:
    """cash_status(current_cash_weight) — 现金区间状态判断。"""

    def test_in_range_returns_区间内(self) -> None:
        """现金占比在区间内 → "区间内"。"""
        # 默认 cash_range = (10, 30), total = 60 → 区间权重 [0.1667, 0.5]
        mid_weight = Decimal("20") / Decimal("60")  # = 0.3333 — 区间正中
        assert DEFAULT_POSITION_ALLOCATION.cash_status(mid_weight) == "区间内"

    def test_below_min_returns_低于下限(self) -> None:
        """现金占比 < cash_range[0]/total → "低于下限"。"""
        low_weight = Decimal("5") / Decimal("60")  # = 0.0833 — 低于下限 0.1667
        assert DEFAULT_POSITION_ALLOCATION.cash_status(low_weight) == "低于下限"

    def test_above_max_returns_高于上限(self) -> None:
        """现金占比 > cash_range[1]/total → "高于上限"。"""
        high_weight = Decimal("40") / Decimal("60")  # = 0.6667 — 高于上限 0.5
        assert DEFAULT_POSITION_ALLOCATION.cash_status(high_weight) == "高于上限"

    def test_at_min_boundary_is_区间内(self) -> None:
        """等于下限 → "区间内"（闭区间）。"""
        min_weight = Decimal("10") / Decimal("60")
        assert DEFAULT_POSITION_ALLOCATION.cash_status(min_weight) == "区间内"

    def test_at_max_boundary_is_区间内(self) -> None:
        """等于上限 → "区间内"（闭区间）。"""
        max_weight = Decimal("30") / Decimal("60")
        assert DEFAULT_POSITION_ALLOCATION.cash_status(max_weight) == "区间内"


# ─── 默认值测试 ────────────────────────────────────────────────────────────


class TestDefaultPositionAllocation:
    """DEFAULT_POSITION_ALLOCATION 的具体数字（spec 097 第二十一轮 liubo 2026-09-24 给值）。"""

    def test_default_has_11_positions(self) -> None:
        """默认有 11 个 positions（10 投资子类 + CASH）。"""
        assert len(DEFAULT_POSITION_ALLOCATION.positions) == 11
        assert set(DEFAULT_POSITION_ALLOCATION.positions.keys()) == set(SwensenClass)

    def test_default_has_10_bounds(self) -> None:
        """默认有 10 个 bounds（10 投资子类，不含 CASH）。"""
        assert len(DEFAULT_POSITION_ALLOCATION.bounds) == 10
        assert SwensenClass.CASH not in DEFAULT_POSITION_ALLOCATION.bounds

    def test_default_cash_range(self) -> None:
        """默认 cash_range = (10, 30)。"""
        assert DEFAULT_POSITION_ALLOCATION.cash_range == (10, 30)

    def test_default_unit_size(self) -> None:
        """默认 unit_size = 10000（1 仓 = 1 万 CNY）。"""
        assert DEFAULT_POSITION_ALLOCATION.unit_size == Decimal("10000")

    def test_default_total_positions_is_60(self) -> None:
        """默认总仓位 60 仓（11 + 6 + 4 + 4 + 3 + 4 + 2 + 3 + 1 + 2 + 20 = 60）。"""
        assert DEFAULT_POSITION_ALLOCATION.total_positions == 60

    def test_default_investment_capital_is_40_wan(self) -> None:
        """默认投资部分 = 40 仓 × 1 万 = 40 万 CNY。"""
        assert DEFAULT_POSITION_ALLOCATION.investment_capital == Decimal("400000")

    def test_default_specific_positions(self) -> None:
        """默认各子类份数（spec 097 第二十一轮 liubo 2026-09-24 给值）。"""
        expected = {
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
        }
        actual = dict(DEFAULT_POSITION_ALLOCATION.positions)
        assert actual == expected

    def test_default_all_bounds_are_two(self) -> None:
        """默认 bounds 都是 2（投资子类单边浮动 2 仓）。"""
        for sub, bound in DEFAULT_POSITION_ALLOCATION.bounds.items():
            assert bound == 2, f"{sub} bound = {bound}, 应 = 2"


# ─── 向后兼容移除测试 ──────────────────────────────────────────────────────


class TestBackwardCompatibilityRemoved:
    """spec 097 第二十一轮：旧 Layer 1/2/3 API 应该全部不可导入。"""

    def test_subclass_limit_not_importable(self) -> None:
        """SubclassLimit 已删除。"""
        with pytest.raises(ImportError):
            from global_allocation.portfolio.strategy import SubclassLimit  # noqa: F401

    def test_allocation_strategy_not_importable(self) -> None:
        """AllocationStrategy 已删除。"""
        with pytest.raises(ImportError):
            from global_allocation.portfolio.strategy import AllocationStrategy  # noqa: F401

    def test_default_strategy_not_importable(self) -> None:
        """DEFAULT_STRATEGY 已删除（被 DEFAULT_POSITION_ALLOCATION 替代）。"""
        with pytest.raises(ImportError):
            from global_allocation.portfolio.strategy import DEFAULT_STRATEGY  # noqa: F401

    def test_compute_actual_target_not_importable(self) -> None:
        """compute_actual_target 已删除（不需要动态缩放了）。"""
        with pytest.raises(ImportError):
            from global_allocation.portfolio.strategy import compute_actual_target  # noqa: F401

    def test_compute_subclass_actual_target_not_importable(self) -> None:
        """compute_subclass_actual_target 已删除。"""
        with pytest.raises(ImportError):
            from global_allocation.portfolio.strategy import compute_subclass_actual_target  # noqa: F401

    def test_super_category_not_importable(self) -> None:
        """SuperCategory 已删除（整数仓位模型不需要超类聚合）。"""
        with pytest.raises(ImportError):
            from global_allocation.portfolio.strategy import SuperCategory  # noqa: F401

    def test_super_category_display_name_not_importable(self) -> None:
        """SUPER_CATEGORY_DISPLAY_NAME 已删除。"""
        with pytest.raises(ImportError):
            from global_allocation.portfolio.strategy import SUPER_CATEGORY_DISPLAY_NAME  # noqa: F401

    def test_compute_super_category_breakdown_not_importable(self) -> None:
        """compute_super_category_breakdown 已删除。"""
        with pytest.raises(ImportError):
            from global_allocation.portfolio.strategy import compute_super_category_breakdown  # noqa: F401

    def test_cash_range_not_importable(self) -> None:
        """CashRange 已删除（被 cash_range tuple 替代）。"""
        with pytest.raises(ImportError):
            from global_allocation.portfolio.strategy import CashRange  # noqa: F401

    def test_investment_weight_not_importable(self) -> None:
        """InvestmentWeight 已删除。"""
        with pytest.raises(ImportError):
            from global_allocation.portfolio.strategy import InvestmentWeight  # noqa: F401

    def test_subclass_internal_weight_not_importable(self) -> None:
        """SubclassInternalWeight 已删除。"""
        with pytest.raises(ImportError):
            from global_allocation.portfolio.strategy import SubclassInternalWeight  # noqa: F401

    def test_investment_categories_not_importable(self) -> None:
        """INVESTMENT_CATEGORIES 已删除。"""
        with pytest.raises(ImportError):
            from global_allocation.portfolio.strategy import INVESTMENT_CATEGORIES  # noqa: F401

    def test_subclass_to_super_not_importable(self) -> None:
        """SUBCLASS_TO_SUPER 已删除。"""
        with pytest.raises(ImportError):
            from global_allocation.portfolio.strategy import SUBCLASS_TO_SUPER  # noqa: F401