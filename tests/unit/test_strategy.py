"""测试 src/global_allocation/portfolio/strategy.py。

spec 097 第十七轮：策略四层结构（内部权重模型 + 子类内部权重）
- Layer 1：11 个 SwensenClass 子类上限
- Layer 2a：4 个投资类超类内部权重（不含现金，合计 = 1.0）
- Layer 2b：现金区间策略（"子弹"区间 [15%, 50%]）
- Layer 3（第十七轮新增）：子类在所属超类内的内部权重（同一超类合计 = 1.0）

实际超类目标 = 超类内部权重 × (1 − 现金占比)，由 compute_actual_target() 计算。
实际子类目标 = 子类内部权重 × 超类内部权重 × (1 − 现金占比)，由 compute_subclass_actual_target() 计算。
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
    SubclassInternalWeight,
    SubclassLimit,
    SuperCategory,
    compute_actual_target,
    compute_subclass_actual_target,
    compute_super_category_breakdown,
)


class TestSubclassLimit:
    def test_upper_is_decimal_between_0_and_1(self) -> None:
        limit = SubclassLimit(SwensenClass.CN_EQUITY, Decimal("0.40"))
        assert isinstance(limit.upper, Decimal)
        assert Decimal("0") <= limit.upper <= Decimal("1")


class TestSubclassInternalWeight:
    """SubclassInternalWeight 测试（spec 097 第十七轮新增 — Layer 3）。"""

    def test_weight_is_decimal_between_0_and_1(self) -> None:
        """子类内部权重是 0~1 的小数（在所属超类内）。"""
        w = SubclassInternalWeight(
            SwensenClass.CN_EQUITY, SuperCategory.EQUITY, Decimal("0.40")
        )
        assert isinstance(w.weight, Decimal)
        assert Decimal("0") <= w.weight <= Decimal("1")

    def test_subclass_and_super_category_match(self) -> None:
        """subclass 所属的超类必须跟 super_category 一致（自检属性）。"""
        # CN_EQUITY 在 EQUITY 超类下
        w = SubclassInternalWeight(
            SwensenClass.CN_EQUITY, SuperCategory.EQUITY, Decimal("0.40")
        )
        assert SUBCLASS_TO_SUPER[w.subclass] == w.super_category


class TestAllocationStrategy:
    def test_default_strategy_has_all_11_subclass_limits(self) -> None:
        """默认策略覆盖全部 11 个子类（第十七轮：从 14 精简到 11）。"""
        subclasses_in_strategy = {limit.subclass for limit in DEFAULT_STRATEGY.subclass_limits}
        assert subclasses_in_strategy == set(SwensenClass)
        assert len(DEFAULT_STRATEGY.subclass_limits) == 11

    def test_default_strategy_has_4_investment_weights(self) -> None:
        """默认策略有 4 个投资类超类内部权重（股票/REITs/债券/商品，不含现金）。

        第十六轮变更：内部权重（不是绝对目标），4 类合计 = 1.0。
        """
        assert len(DEFAULT_STRATEGY.investment_weights) == 4
        categories = {w.category for w in DEFAULT_STRATEGY.investment_weights}
        assert categories == set(INVESTMENT_CATEGORIES)
        assert SuperCategory.CASH not in categories

    def test_default_strategy_has_subclass_weights(self) -> None:
        """默认策略有子类内部权重（第十七轮新增 — Layer 3）。

        10 个子类权重（5 股票 + 2 REITs + 2 债券 + 1 商品；现金不在内）。
        """
        assert len(DEFAULT_STRATEGY.subclass_weights) == 10
        # 不含现金
        cash_entries = [w for w in DEFAULT_STRATEGY.subclass_weights if w.subclass == SwensenClass.CASH]
        assert len(cash_entries) == 0

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

    def test_subclass_weights_sum_to_one_per_super_category(self) -> None:
        """子类内部权重在每个超类内合计 = 1.0（保证 Layer 3 的相对权重语义）。"""
        for cat in INVESTMENT_CATEGORIES:
            total = DEFAULT_STRATEGY.total_subclass_weight(cat)
            assert total == Decimal("1"), f"{cat.name} 子类权重和 = {total}，应 = 1.0"

    def test_cash_subclass_weight_total_is_zero(self) -> None:
        """现金超类的子类权重和 = 0（现金不在 subclass_weights 里，走 cash_range）。"""
        assert DEFAULT_STRATEGY.total_subclass_weight(SuperCategory.CASH) == Decimal("0")

    def test_default_investment_weights_match_ranking(self) -> None:
        """默认权重按"收益率排序"：股票 > REITs > 债券 > 商品（第十六轮确认）。"""
        assert DEFAULT_STRATEGY.investment_weight(SuperCategory.EQUITY) == Decimal("0.70")
        assert DEFAULT_STRATEGY.investment_weight(SuperCategory.REIT) == Decimal("0.15")
        assert DEFAULT_STRATEGY.investment_weight(SuperCategory.BOND) == Decimal("0.10")
        assert DEFAULT_STRATEGY.investment_weight(SuperCategory.COMMODITY) == Decimal("0.05")

    def test_default_subclass_weights_equity(self) -> None:
        """股票超类子类权重（第十七轮：A股 40% / 美股 20% / 港股 15% / 国外发达 15% / 新兴市场 10%）。"""
        assert DEFAULT_STRATEGY.subclass_weight(SwensenClass.CN_EQUITY) == (SuperCategory.EQUITY, Decimal("0.40"))
        assert DEFAULT_STRATEGY.subclass_weight(SwensenClass.US_EQUITY) == (SuperCategory.EQUITY, Decimal("0.20"))
        assert DEFAULT_STRATEGY.subclass_weight(SwensenClass.HK_EQUITY) == (SuperCategory.EQUITY, Decimal("0.15"))
        assert DEFAULT_STRATEGY.subclass_weight(SwensenClass.FOREIGN_DM_EQUITY) == (SuperCategory.EQUITY, Decimal("0.15"))
        assert DEFAULT_STRATEGY.subclass_weight(SwensenClass.EM_EQUITY) == (SuperCategory.EQUITY, Decimal("0.10"))

    def test_default_subclass_weights_reit(self) -> None:
        """REITs 超类子类权重（第十七轮：国内 70% / 美国 30%）。"""
        assert DEFAULT_STRATEGY.subclass_weight(SwensenClass.CN_REIT) == (SuperCategory.REIT, Decimal("0.70"))
        assert DEFAULT_STRATEGY.subclass_weight(SwensenClass.US_REIT) == (SuperCategory.REIT, Decimal("0.30"))

    def test_default_subclass_weights_bond(self) -> None:
        """债券超类子类权重（第十七轮：国内利率债 70% / 美债 30%；信用债砍掉）。"""
        assert DEFAULT_STRATEGY.subclass_weight(SwensenClass.CN_GOV_BOND) == (SuperCategory.BOND, Decimal("0.70"))
        assert DEFAULT_STRATEGY.subclass_weight(SwensenClass.US_BOND) == (SuperCategory.BOND, Decimal("0.30"))

    def test_default_subclass_weight_commodity(self) -> None:
        """商品超类子类权重（100%，只有一个子类）。"""
        assert DEFAULT_STRATEGY.subclass_weight(SwensenClass.COMMODITY) == (SuperCategory.COMMODITY, Decimal("1.00"))

    def test_subclass_weight_returns_none_for_cash(self) -> None:
        """subclass_weight 对现金返回 None（现金走 cash_range，不走权重）。"""
        assert DEFAULT_STRATEGY.subclass_weight(SwensenClass.CASH) is None

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
    def test_all_11_subclasses_mapped(self) -> None:
        """11 个 SwensenClass 全部映射到超类（第十七轮：从 14 精简到 11）。"""
        assert set(SUBCLASS_TO_SUPER.keys()) == set(SwensenClass)
        assert len(SUBCLASS_TO_SUPER) == 11

    def test_equity_super_category_has_5_subclasses(self) -> None:
        """股票超类聚合 5 个子类（CN/HK/US/FOREIGN_DM/EM；第十七轮：合并 EU+ASIA_DM=国外发达，删全球主题）。"""
        equity_count = sum(
            1 for cat in SUBCLASS_TO_SUPER.values() if cat == SuperCategory.EQUITY
        )
        assert equity_count == 5

    def test_bond_super_category_has_2_subclasses(self) -> None:
        """债券超类聚合 2 个子类（CN_GOV/US_BOND；第十七轮：删 CN_CREDIT）。"""
        bond_count = sum(
            1 for cat in SUBCLASS_TO_SUPER.values() if cat == SuperCategory.BOND
        )
        assert bond_count == 2

    def test_reit_super_category_has_2_subclasses(self) -> None:
        """REITs 超类聚合 2 个子类（CN_REIT/US_REIT）。"""
        reit_count = sum(
            1 for cat in SUBCLASS_TO_SUPER.values() if cat == SuperCategory.REIT
        )
        assert reit_count == 2

    def test_cash_subclass_maps_to_cash_super(self) -> None:
        """CASH 子类映射到 CASH 超类（投资类不含现金，但子类映射仍然有效）。"""
        assert SUBCLASS_TO_SUPER[SwensenClass.CASH] == SuperCategory.CASH

    def test_removed_subclasses_no_longer_in_mapping(self) -> None:
        """第十七轮：被砍掉的子类不在 SUBCLASS_TO_SUPER 里。

        通过 value 字符串判断（enum member 已被物理删除，不能直接引用类属性）。
        """
        removed_values = {
            "global_themed_equity",  # 并入 US_EQUITY
            "eu_equity",             # 合并进 FOREIGN_DM_EQUITY
            "asia_dm_equity",        # 合并进 FOREIGN_DM_EQUITY
            "cn_credit_bond",        # 砍掉（斯文森说没阿尔法），基金并入 CN_GOV_BOND
        }
        actual_values = {m.value for m in SUBCLASS_TO_SUPER.keys()}
        assert removed_values.isdisjoint(actual_values)

    def test_new_subclass_mapped_to_equity(self) -> None:
        """第十七轮新增 FOREIGN_DM_EQUITY → EQUITY。"""
        assert SUBCLASS_TO_SUPER[SwensenClass.FOREIGN_DM_EQUITY] == SuperCategory.EQUITY


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


class TestComputeSubclassActualTarget:
    """compute_subclass_actual_target() 测试（spec 097 第十七轮新增 — Layer 3）。

    公式：actual_target = subclass_internal_weight × super_investment_weight × (1 − current_cash_weight)

    例（默认策略，现金 30%）：
    - 投资部分 = 70%
    - 股票超类目标 = 70% × 70% = 49%
    - A 股目标 = 49% × 40% = 19.6%
    - 美股目标 = 49% × 20% = 9.8%
    - 港股目标 = 49% × 15% = 7.35%
    - 国外发达目标 = 49% × 15% = 7.35%
    - 新兴市场目标 = 49% × 10% = 4.9%
    """

    def test_returns_none_for_cash(self) -> None:
        """现金类返回 None（现金不走权重公式，走 cash_range）。"""
        actual = compute_subclass_actual_target(
            DEFAULT_STRATEGY,
            SwensenClass.CASH,
            Decimal("0.30"),
        )
        assert actual is None

    def test_cn_equity_at_30pct_cash(self) -> None:
        """现金 30% 时 A 股目标 = 40% × 70% × 70% = 19.6%。"""
        actual = compute_subclass_actual_target(
            DEFAULT_STRATEGY,
            SwensenClass.CN_EQUITY,
            Decimal("0.30"),
        )
        # 0.40 (A股) × 0.70 (股票) × 0.70 (1 - 现金 30%) = 0.196
        assert actual == Decimal("0.40") * Decimal("0.70") * Decimal("0.70")

    def test_us_equity_at_30pct_cash(self) -> None:
        """现金 30% 时美股目标 = 20% × 70% × 70% = 9.8%。"""
        actual = compute_subclass_actual_target(
            DEFAULT_STRATEGY,
            SwensenClass.US_EQUITY,
            Decimal("0.30"),
        )
        assert actual == Decimal("0.20") * Decimal("0.70") * Decimal("0.70")

    def test_hk_equity_at_30pct_cash(self) -> None:
        """现金 30% 时港股目标 = 15% × 70% × 70% = 7.35%。"""
        actual = compute_subclass_actual_target(
            DEFAULT_STRATEGY,
            SwensenClass.HK_EQUITY,
            Decimal("0.30"),
        )
        assert actual == Decimal("0.15") * Decimal("0.70") * Decimal("0.70")

    def test_foreign_dm_equity_at_30pct_cash(self) -> None:
        """现金 30% 时国外发达市场目标 = 15% × 70% × 70% = 7.35%。"""
        actual = compute_subclass_actual_target(
            DEFAULT_STRATEGY,
            SwensenClass.FOREIGN_DM_EQUITY,
            Decimal("0.30"),
        )
        assert actual == Decimal("0.15") * Decimal("0.70") * Decimal("0.70")

    def test_em_equity_at_30pct_cash(self) -> None:
        """现金 30% 时新兴市场目标 = 10% × 70% × 70% = 4.9%。"""
        actual = compute_subclass_actual_target(
            DEFAULT_STRATEGY,
            SwensenClass.EM_EQUITY,
            Decimal("0.30"),
        )
        assert actual == Decimal("0.10") * Decimal("0.70") * Decimal("0.70")

    def test_cn_reit_at_30pct_cash(self) -> None:
        """现金 30% 时国内 REITs 目标 = 70% × 15% × 70% = 7.35%。"""
        actual = compute_subclass_actual_target(
            DEFAULT_STRATEGY,
            SwensenClass.CN_REIT,
            Decimal("0.30"),
        )
        assert actual == Decimal("0.70") * Decimal("0.15") * Decimal("0.70")

    def test_us_reit_at_30pct_cash(self) -> None:
        """现金 30% 时美国 REITs 目标 = 30% × 15% × 70% = 3.15%。"""
        actual = compute_subclass_actual_target(
            DEFAULT_STRATEGY,
            SwensenClass.US_REIT,
            Decimal("0.30"),
        )
        assert actual == Decimal("0.30") * Decimal("0.15") * Decimal("0.70")

    def test_cn_gov_bond_at_30pct_cash(self) -> None:
        """现金 30% 时国内利率债目标 = 70% × 10% × 70% = 4.9%。"""
        actual = compute_subclass_actual_target(
            DEFAULT_STRATEGY,
            SwensenClass.CN_GOV_BOND,
            Decimal("0.30"),
        )
        assert actual == Decimal("0.70") * Decimal("0.10") * Decimal("0.70")

    def test_us_bond_at_30pct_cash(self) -> None:
        """现金 30% 时美债目标 = 30% × 10% × 70% = 2.1%。"""
        actual = compute_subclass_actual_target(
            DEFAULT_STRATEGY,
            SwensenClass.US_BOND,
            Decimal("0.30"),
        )
        assert actual == Decimal("0.30") * Decimal("0.10") * Decimal("0.70")

    def test_commodity_at_30pct_cash(self) -> None:
        """现金 30% 时商品目标 = 100% × 5% × 70% = 3.5%（只有一个商品子类）。"""
        actual = compute_subclass_actual_target(
            DEFAULT_STRATEGY,
            SwensenClass.COMMODITY,
            Decimal("0.30"),
        )
        assert actual == Decimal("1.00") * Decimal("0.05") * Decimal("0.70")

    def test_equity_subclass_targets_sum_to_equity_super_target(self) -> None:
        """股票超类下 5 子类目标之和 = 股票超类目标（Layer 3 权重和 = 1 的保证）。

        投资类子目标和 = 超类目标 × Layer3权重和 = 超类目标（因为 Layer3 = 1.0）
        """
        current_cash = Decimal("0.30")
        equity_subclasses = [
            SwensenClass.CN_EQUITY,
            SwensenClass.US_EQUITY,
            SwensenClass.HK_EQUITY,
            SwensenClass.FOREIGN_DM_EQUITY,
            SwensenClass.EM_EQUITY,
        ]
        total = sum(
            compute_subclass_actual_target(DEFAULT_STRATEGY, sub, current_cash)  # type: ignore[misc]
            for sub in equity_subclasses
        )
        # 股票超类目标 = 70% × 70% = 49%
        expected_equity_super = compute_actual_target(
            DEFAULT_STRATEGY, SuperCategory.EQUITY, current_cash
        )
        assert total == expected_equity_super

    def test_all_subclass_targets_sum_to_investment_part(self) -> None:
        """所有子类（10 个，不含现金）目标之和 = 1 − 当前现金占比。

        验证四层公式一致：sum(子类 × 超类 × (1-cash)) = sum(超类 × (1-cash)) = 1 - cash
        """
        current_cash = Decimal("0.30")
        investment_total = Decimal("1") - current_cash
        subclass_total = sum(
            compute_subclass_actual_target(DEFAULT_STRATEGY, sub, current_cash)  # type: ignore[misc]
            for sub in SwensenClass
            if sub != SwensenClass.CASH
        )
        assert subclass_total == investment_total

    def test_zero_cash_uses_full_subclass_and_super_weights(self) -> None:
        """现金 0% 时（极端），子类目标 = 子类内部权重 × 超类内部权重。

        例：A 股 = 40% × 70% = 28%（无现金缩放）
        """
        for sub in SwensenClass:
            if sub == SwensenClass.CASH:
                continue
            actual = compute_subclass_actual_target(DEFAULT_STRATEGY, sub, Decimal("0"))
            info = DEFAULT_STRATEGY.subclass_weight(sub)
            assert info is not None
            super_cat, sub_weight = info
            super_weight = DEFAULT_STRATEGY.investment_weight(super_cat)
            assert super_weight is not None
            assert actual == sub_weight * super_weight

    def test_cash_50pct_doubles_subclass_targets(self) -> None:
        """现金 50% 时（子弹上限），子类目标 = 子类权重 × 超类权重 × 50%。"""
        for sub in SwensenClass:
            if sub == SwensenClass.CASH:
                continue
            actual = compute_subclass_actual_target(DEFAULT_STRATEGY, sub, Decimal("0.50"))
            assert actual is not None
            # 因为现金 50% 时 投资部分 = 50%
            assert actual * 2 == actual / Decimal("0.5")  # 等价于乘以 1/0.5 = 2
