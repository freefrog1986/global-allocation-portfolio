"""测试 src/global_allocation/portfolio/strategy.py。

spec 097 第十六轮：策略三层结构（内部权重模型）
- Layer 1：14 个 SwensenClass 子类上限
- Layer 2a：4 个投资类超类内部权重（不含现金，合计 = 1.0）
- Layer 2b：现金区间策略（"子弹"区间 [15%, 50%]）

实际目标 = 内部权重 × (1 − 现金占比)，由 compute_actual_target() 计算。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from global_allocation.portfolio.breakdown import SwensenClass
from global_allocation.portfolio.strategy import (
    DEFAULT_STRATEGY,
    INVESTMENT_CATEGORIES,
    SUBCLASS_TO_SUPER,
    SUPER_CATEGORY_DISPLAY_NAME,
    CashRange,
    SubclassLimit,
    SuperCategory,
    compute_actual_target,
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

    def test_default_strategy_has_4_investment_weights(self) -> None:
        """默认策略有 4 个投资类超类内部权重（股票/REITs/债券/商品，不含现金）。

        第十六轮变更：内部权重（不是绝对目标），4 类合计 = 1.0。
        """
        assert len(DEFAULT_STRATEGY.investment_weights) == 4
        categories = {w.category for w in DEFAULT_STRATEGY.investment_weights}
        assert categories == set(INVESTMENT_CATEGORIES)
        assert SuperCategory.CASH not in categories

    def test_default_strategy_has_cash_range(self) -> None:
        """默认策略有 cash_range（第十五轮新增，第十六轮改为 [15%, 50%]）。"""
        assert isinstance(DEFAULT_STRATEGY.cash_range, CashRange)
        # 默认区间 [15%, 50%]（第十六轮：下限从 20% 放宽到 15%）
        assert DEFAULT_STRATEGY.cash_range.min_weight == Decimal("0.15")
        assert DEFAULT_STRATEGY.cash_range.max_weight == Decimal("0.50")

    def test_investment_weights_sum_to_one(self) -> None:
        """4 个投资类内部权重之和 = 1.0（精确等于，相对权重）。

        第十六轮变更：之前是 < 1.0（绝对目标和 = 97%），现在必须 = 1.0（相对权重和）。
        """
        total = DEFAULT_STRATEGY.total_investment_weight
        assert total == Decimal("1")

    def test_default_investment_weights_match_ranking(self) -> None:
        """默认权重按"收益率排序"：股票 > REITs > 债券 > 商品（第十六轮确认）。"""
        assert DEFAULT_STRATEGY.investment_weight(SuperCategory.EQUITY) == Decimal("0.70")
        assert DEFAULT_STRATEGY.investment_weight(SuperCategory.REIT) == Decimal("0.15")
        assert DEFAULT_STRATEGY.investment_weight(SuperCategory.BOND) == Decimal("0.10")
        assert DEFAULT_STRATEGY.investment_weight(SuperCategory.COMMODITY) == Decimal("0.05")

    def test_subclass_upper_lookup(self) -> None:
        """subclass_upper 能查到具体子类的上限。"""
        upper = DEFAULT_STRATEGY.subclass_upper(SwensenClass.CN_EQUITY)
        assert upper == Decimal("0.40")

    def test_investment_weight_lookup(self) -> None:
        """investment_weight 能查到具体超类的内部权重。"""
        weight = DEFAULT_STRATEGY.investment_weight(SuperCategory.EQUITY)
        assert weight == Decimal("0.70")

    def test_investment_weight_returns_none_for_cash(self) -> None:
        """investment_weight 对现金返回 None（现金不在投资权重里，走 cash_range）。"""
        weight = DEFAULT_STRATEGY.investment_weight(SuperCategory.CASH)
        assert weight is None

    def test_strategy_is_frozen(self) -> None:
        """frozen — 不能修改字段（保证不可变性）。"""
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            DEFAULT_STRATEGY.investment_weights = ()  # type: ignore[misc]


class TestInvestmentCategories:
    def test_investment_categories_count(self) -> None:
        """INVESTMENT_CATEGORIES 含 4 个超类。"""
        assert len(INVESTMENT_CATEGORIES) == 4

    def test_investment_categories_excludes_cash(self) -> None:
        """INVESTMENT_CATEGORIES 不含现金（现金走区间策略）。"""
        assert SuperCategory.CASH not in INVESTMENT_CATEGORIES
        assert set(INVESTMENT_CATEGORIES) == {
            SuperCategory.EQUITY,
            SuperCategory.REIT,
            SuperCategory.BOND,
            SuperCategory.COMMODITY,
        }

    def test_investment_categories_follows_return_ranking(self) -> None:
        """INVESTMENT_CATEGORIES 按收益率排序（spec 097 第十六轮）：股票 > REITs > 债券 > 商品。

        跟 SuperCategory 枚举顺序（EQUITY/BOND/REIT/COMMODITY）不同，
        这是策略上的排序：股票 #1，REITs #2，债券 #3，商品 #4。
        """
        expected = (
            SuperCategory.EQUITY,    # 收益最高，"占大头"
            SuperCategory.REIT,      # 介于股债之间
            SuperCategory.BOND,      # 中等收益
            SuperCategory.COMMODITY, # 长期没那么值钱
        )
        assert INVESTMENT_CATEGORIES == expected


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

    def test_cash_subclass_maps_to_cash_super(self) -> None:
        """CASH 子类映射到 CASH 超类（投资类不含现金，但子类映射仍然有效）。"""
        assert SUBCLASS_TO_SUPER[SwensenClass.CASH] == SuperCategory.CASH


class TestCashRange:
    """现金区间策略测试（spec 097 第十五轮新增，第十六轮 [15%, 50%]）。

    CashRange 表示"子弹"区间 [min_weight, max_weight]：
    - 当前 < min → 子弹打光了
    - 当前 > max → 子弹囤太多
    - min ≤ 当前 ≤ max → 区间内
    """

    def test_is_in_range_returns_true_when_in_range(self) -> None:
        """当前值在区间内 → True。"""
        cr = CashRange(min_weight=Decimal("0.15"), max_weight=Decimal("0.50"))
        assert cr.is_in_range(Decimal("0.15")) is True  # 等于下限
        assert cr.is_in_range(Decimal("0.50")) is True  # 等于上限
        assert cr.is_in_range(Decimal("0.30")) is True  # 区间正中

    def test_is_in_range_returns_false_when_below_min(self) -> None:
        """当前值低于下限 → False。"""
        cr = CashRange(min_weight=Decimal("0.15"), max_weight=Decimal("0.50"))
        assert cr.is_in_range(Decimal("0.10")) is False
        assert cr.is_in_range(Decimal("0.149")) is False

    def test_is_in_range_returns_false_when_above_max(self) -> None:
        """当前值高于上限 → False。"""
        cr = CashRange(min_weight=Decimal("0.15"), max_weight=Decimal("0.50"))
        assert cr.is_in_range(Decimal("0.60")) is False
        assert cr.is_in_range(Decimal("0.501")) is False

    def test_status_in_range(self) -> None:
        """区间内 → "区间内"。"""
        cr = CashRange(min_weight=Decimal("0.15"), max_weight=Decimal("0.50"))
        assert cr.status(Decimal("0.30")) == "区间内"
        assert cr.status(Decimal("0.15")) == "区间内"
        assert cr.status(Decimal("0.50")) == "区间内"

    def test_status_below_min(self) -> None:
        """低于下限 → "低于下限"。"""
        cr = CashRange(min_weight=Decimal("0.15"), max_weight=Decimal("0.50"))
        assert cr.status(Decimal("0.10")) == "低于下限"
        assert cr.status(Decimal("0.149")) == "低于下限"

    def test_status_above_max(self) -> None:
        """高于上限 → "高于上限"。"""
        cr = CashRange(min_weight=Decimal("0.15"), max_weight=Decimal("0.50"))
        assert cr.status(Decimal("0.60")) == "高于上限"
        assert cr.status(Decimal("0.501")) == "高于上限"

    def test_display_range(self) -> None:
        """display_range 输出 "[min%, max%]" 字符串（卡片展示用）。"""
        cr = CashRange(min_weight=Decimal("0.15"), max_weight=Decimal("0.50"))
        assert cr.display_range == "[15%, 50%]"

    def test_cash_range_is_frozen(self) -> None:
        """CashRange frozen — 不能修改字段。"""
        from dataclasses import FrozenInstanceError

        cr = CashRange(min_weight=Decimal("0.15"), max_weight=Decimal("0.50"))
        with pytest.raises(FrozenInstanceError):
            cr.min_weight = Decimal("0.30")  # type: ignore[misc]

    def test_default_cash_range_uses_correct_numbers(self) -> None:
        """默认策略的现金区间是 [15%, 50%]（liubo 第十六轮明确）。"""
        cr = DEFAULT_STRATEGY.cash_range
        # 0.15 * 100 = 15, 0.50 * 100 = 50
        assert int(cr.min_weight * 100) == 15
        assert int(cr.max_weight * 100) == 50


class TestComputeActualTarget:
    """compute_actual_target() 测试（spec 097 第十六轮新增）。

    公式：actual_target = internal_weight × (1 − current_cash_weight)

    例（默认策略）：
    - 现金 30% → 投资 70% → 股票目标 = 70% × 70% = 49%
    - 现金 50% → 投资 50% → 股票目标 = 70% × 50% = 35%
    - 现金 15% → 投资 85% → 股票目标 = 70% × 85% = 59.5%
    """

    def test_returns_none_for_cash(self) -> None:
        """现金类返回 None（现金不走权重公式，走 cash_range）。"""
        actual = compute_actual_target(
            DEFAULT_STRATEGY,
            SuperCategory.CASH,
            Decimal("0.30"),
        )
        assert actual is None

    def test_equity_at_30pct_cash(self) -> None:
        """现金 30% 时股票实际目标 = 70% × 70% = 49%。"""
        actual = compute_actual_target(
            DEFAULT_STRATEGY,
            SuperCategory.EQUITY,
            Decimal("0.30"),
        )
        assert actual == Decimal("0.70") * Decimal("0.70")  # = 0.49

    def test_equity_at_50pct_cash(self) -> None:
        """现金 50% 时股票实际目标 = 70% × 50% = 35%。"""
        actual = compute_actual_target(
            DEFAULT_STRATEGY,
            SuperCategory.EQUITY,
            Decimal("0.50"),
        )
        assert actual == Decimal("0.70") * Decimal("0.50")  # = 0.35

    def test_equity_at_15pct_cash(self) -> None:
        """现金 15% 时股票实际目标 = 70% × 85% = 59.5%（子弹下限的最大仓位）。"""
        actual = compute_actual_target(
            DEFAULT_STRATEGY,
            SuperCategory.EQUITY,
            Decimal("0.15"),
        )
        assert actual == Decimal("0.70") * Decimal("0.85")  # = 0.595

    def test_reit_at_30pct_cash(self) -> None:
        """现金 30% 时 REITs 实际目标 = 15% × 70% = 10.5%。"""
        actual = compute_actual_target(
            DEFAULT_STRATEGY,
            SuperCategory.REIT,
            Decimal("0.30"),
        )
        assert actual == Decimal("0.15") * Decimal("0.70")  # = 0.105

    def test_bond_at_30pct_cash(self) -> None:
        """现金 30% 时债券实际目标 = 10% × 70% = 7%。"""
        actual = compute_actual_target(
            DEFAULT_STRATEGY,
            SuperCategory.BOND,
            Decimal("0.30"),
        )
        assert actual == Decimal("0.10") * Decimal("0.70")  # = 0.07

    def test_commodity_at_30pct_cash(self) -> None:
        """现金 30% 时商品实际目标 = 5% × 70% = 3.5%。"""
        actual = compute_actual_target(
            DEFAULT_STRATEGY,
            SuperCategory.COMMODITY,
            Decimal("0.30"),
        )
        assert actual == Decimal("0.05") * Decimal("0.70")  # = 0.035

    def test_total_actual_targets_equal_investment_part(self) -> None:
        """4 个投资类实际目标之和 = 1 − 当前现金占比（内部权重和 = 1.0 的保证）。"""
        current_cash = Decimal("0.30")
        investment_total = Decimal("1") - current_cash
        for cat in INVESTMENT_CATEGORIES:
            actual = compute_actual_target(DEFAULT_STRATEGY, cat, current_cash)
            assert actual is not None
        # 所有投资类加总
        total = sum(
            compute_actual_target(DEFAULT_STRATEGY, cat, current_cash)  # type: ignore[misc]
            for cat in INVESTMENT_CATEGORIES
        )
        # 因为 内部权重和 = 1，所以 实际目标和 = (1 − cash) × 1 = (1 − cash)
        assert total == investment_total

    def test_zero_cash_doubles_weights(self) -> None:
        """现金 0% 时（极端情况），投资类实际目标 = 内部权重 × 100%。"""
        for cat in INVESTMENT_CATEGORIES:
            actual = compute_actual_target(DEFAULT_STRATEGY, cat, Decimal("0"))
            assert actual is not None
            assert actual == DEFAULT_STRATEGY.investment_weight(cat)


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
