"""测试 src/global_allocation/portfolio/strategy.py。

spec 097：双层大类资产配置策略模块
- Layer 1：14 个 SwensenClass 子类上限
- Layer 2：5 个超类目标
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from global_allocation.portfolio.breakdown import SwensenClass
from global_allocation.portfolio.strategy import (
    DEFAULT_STRATEGY,
    SUBCLASS_TO_SUPER,
    SUPER_CATEGORY_DISPLAY_NAME,
    SubclassLimit,
    SuperCategory,
    compute_super_category_breakdown,
)


class TestSubclassLimit:
    def test_upper_is_decimal_between_0_and_1(self) -> None:
        limit = SubclassLimit(SwensenClass.CN_EQUITY, Decimal("0.40"))
        assert isinstance(limit.upper, Decimal)
        assert Decimal("0") <= limit.upper <= Decimal("1")


class TestAllocationStrategy:
    def test_default_strategy_has_all_14_subclass_limits(self) -> None:
        """默认策略覆盖全部 14 个子类。"""
        subclasses_in_strategy = {limit.subclass for limit in DEFAULT_STRATEGY.subclass_limits}
        assert subclasses_in_strategy == set(SwensenClass)
        assert len(DEFAULT_STRATEGY.subclass_limits) == 14

    def test_default_strategy_has_5_super_category_targets(self) -> None:
        """默认策略有 5 个超类目标。"""
        assert len(DEFAULT_STRATEGY.super_category_targets) == 5
        categories = {t.category for t in DEFAULT_STRATEGY.super_category_targets}
        assert categories == set(SuperCategory)

    def test_super_category_targets_sum_to_one(self) -> None:
        """5 个超类目标之和 = 1.0（不能多不能少）。"""
        total = DEFAULT_STRATEGY.total_super_target
        assert abs(total - Decimal("1")) < Decimal("0.001")

    def test_subclass_upper_lookup(self) -> None:
        """subclass_upper 能查到具体子类的上限。"""
        upper = DEFAULT_STRATEGY.subclass_upper(SwensenClass.CN_EQUITY)
        assert upper == Decimal("0.40")

    def test_super_category_target_lookup(self) -> None:
        """super_category_target 能查到具体超类的目标。"""
        target = DEFAULT_STRATEGY.super_category_target(SuperCategory.EQUITY)
        assert target == Decimal("0.70")

    def test_strategy_is_frozen(self) -> None:
        """frozen — 不能修改字段（保证不可变性）。"""
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            DEFAULT_STRATEGY.subclass_limits = ()  # type: ignore[misc]


class TestSubclassToSuperMapping:
    def test_all_14_subclasses_mapped(self) -> None:
        """14 个 SwensenClass 全部映射到超类。"""
        assert set(SUBCLASS_TO_SUPER.keys()) == set(SwensenClass)
        assert len(SUBCLASS_TO_SUPER) == 14

    def test_equity_super_category_has_7_subclasses(self) -> None:
        """股票超类聚合 7 个子类（CN/HK/US/EU/ASIA_DM/EM/GLOBAL_THEMED）。"""
        equity_count = sum(
            1 for cat in SUBCLASS_TO_SUPER.values() if cat == SuperCategory.EQUITY
        )
        assert equity_count == 7

    def test_bond_super_category_has_3_subclasses(self) -> None:
        """债券超类聚合 3 个子类（CN_GOV/CN_CREDIT/US_BOND）。"""
        bond_count = sum(
            1 for cat in SUBCLASS_TO_SUPER.values() if cat == SuperCategory.BOND
        )
        assert bond_count == 3

    def test_reit_super_category_has_2_subclasses(self) -> None:
        """REITs 超类聚合 2 个子类（CN_REIT/US_REIT）。"""
        reit_count = sum(
            1 for cat in SUBCLASS_TO_SUPER.values() if cat == SuperCategory.REIT
        )
        assert reit_count == 2


class TestComputeSuperCategoryBreakdown:
    def test_empty_breakdown_returns_zeros(self) -> None:
        """空 breakdown → 所有超类权重都是 0。"""
        result = compute_super_category_breakdown([])
        assert all(v == Decimal("0") for v in result.values())
        assert set(result.keys()) == set(SuperCategory)

    def test_aggregates_subclass_weights_by_super_category(self) -> None:
        """聚合各子类权重到超类。"""
        # 3 个子类分到股票超类，每个 0.1（0.3 总和）
        breakdown = [
            {
                "subclass": SwensenClass.CN_EQUITY,
                "display_name": "A 股股票",
                "count": 1,
                "value": Decimal("100"),
                "weight": Decimal("0.1"),
            },
            {
                "subclass": SwensenClass.US_EQUITY,
                "display_name": "美股股票",
                "count": 1,
                "value": Decimal("100"),
                "weight": Decimal("0.1"),
            },
            {
                "subclass": SwensenClass.HK_EQUITY,
                "display_name": "港股",
                "count": 1,
                "value": Decimal("100"),
                "weight": Decimal("0.1"),
            },
            {
                "subclass": SwensenClass.CN_REIT,
                "display_name": "国内 REITs",
                "count": 1,
                "value": Decimal("100"),
                "weight": Decimal("0.1"),
            },
        ]
        result = compute_super_category_breakdown(breakdown)
        # 股票超类 = CN + US + HK = 0.3
        assert result[SuperCategory.EQUITY] == Decimal("0.3")
        # REITs 超类 = 0.1
        assert result[SuperCategory.REIT] == Decimal("0.1")
        # 其他超类都是 0
        assert result[SuperCategory.BOND] == Decimal("0")
        assert result[SuperCategory.COMMODITY] == Decimal("0")
        assert result[SuperCategory.CASH] == Decimal("0")


class TestSuperCategoryDisplayName:
    def test_all_super_categories_have_display_name(self) -> None:
        """5 个超类都有显示名（飞书卡片用）。

        4 个用中文（股票/债券/商品/现金），1 个用英文缩写 REITs（金融惯用）。
        """
        assert set(SUPER_CATEGORY_DISPLAY_NAME.keys()) == set(SuperCategory)
        # 4 个超类用中文（不是纯 ASCII）
        chinese_names = [
            SUPER_CATEGORY_DISPLAY_NAME[SuperCategory.EQUITY],
            SUPER_CATEGORY_DISPLAY_NAME[SuperCategory.BOND],
            SUPER_CATEGORY_DISPLAY_NAME[SuperCategory.COMMODITY],
            SUPER_CATEGORY_DISPLAY_NAME[SuperCategory.CASH],
        ]
        for name in chinese_names:
            assert not name.isascii()
        # REITs 是金融惯用英文缩写
        assert SUPER_CATEGORY_DISPLAY_NAME[SuperCategory.REIT] == "REITs"
