"""测试 src/global_allocation/portfolio/dividend_strategy.py。

参照 spec 097（红利策略组合独立模块，liubo 2026-09-20 建）。

2026-09-26 第二次扩展：从 2 只扩到 11 只 / 343,000 CNY；按 ETF/指数族分 6 类。
- 022164 是唯一主动基金（盛丰衍量化），不能用 PE 分位当调仓信号。
"""

from __future__ import annotations

from decimal import Decimal

from global_allocation.portfolio.dividend_strategy import (
    ACTIVE_FUND_CODES,
    DISPLAY_NAME,
    DIVIDEND_COST_BASIS_BY_CODE,
    DIVIDEND_STRATEGY_BY_CODE,
    DIVIDEND_TOTAL_COST_CNY,
    PARENT_EFFOCODE_BY_CODE,
    TRACKING_INDEX_BY_CODE,
    get_cost_basis,
    get_parent_etf_code,
    get_strategy,
    get_tracking_index,
    is_active_fund,
)


class TestDividendStrategyByCode:
    def test_has_eleven_funds(self) -> None:
        """2026-09-26 扩到 11 只。"""
        assert len(DIVIDEND_STRATEGY_BY_CODE) == 11

    def test_a_share_low_vol_funds(self) -> None:
        """A 股红利低波 4 只。"""
        a_share = ["008163", "005561", "007605", "008114"]
        for code in a_share:
            assert DIVIDEND_STRATEGY_BY_CODE[code] == "A 股红利低波", code

    def test_ah_growth_low_vol_funds(self) -> None:
        """A+H 红利成长低波 1 只（含港股通）。"""
        assert DIVIDEND_STRATEGY_BY_CODE["007751"] == "A+H 红利成长低波"

    def test_hk_high_div_funds(self) -> None:
        """港股高股息 2 只。"""
        for code in ("004098", "021457"):
            assert DIVIDEND_STRATEGY_BY_CODE[code] == "港股高股息", code

    def test_free_cash_flow_funds(self) -> None:
        """自由现金流 2 只。"""
        for code in ("023917", "025958"):
            assert DIVIDEND_STRATEGY_BY_CODE[code] == "自由现金流", code

    def test_smart_high_div_funds(self) -> None:
        """高股息策略 1 只。"""
        assert DIVIDEND_STRATEGY_BY_CODE["025682"] == "高股息策略"

    def test_active_soe_quant_funds(self) -> None:
        """主动-央企-量化 1 只（唯一主动管理基金）。"""
        assert DIVIDEND_STRATEGY_BY_CODE["022164"] == "主动-央企-量化"


class TestTrackingIndex:
    def test_has_eleven_indexes(self) -> None:
        """11 只基金都应记录底层指数/ETF。"""
        assert len(TRACKING_INDEX_BY_CODE) == 11
        assert set(TRACKING_INDEX_BY_CODE.keys()) == set(DIVIDEND_STRATEGY_BY_CODE.keys())

    def test_a_share_indexes(self) -> None:
        assert TRACKING_INDEX_BY_CODE["008163"] == "标普中国A股大盘红利低波50指数"
        assert TRACKING_INDEX_BY_CODE["005561"] == "中证红利低波动指数"
        assert TRACKING_INDEX_BY_CODE["007605"] == "沪深300红利低波动指数"
        assert TRACKING_INDEX_BY_CODE["008114"] == "中证红利低波动100指数"

    def test_ah_index_includes_hk_connect(self) -> None:
        assert TRACKING_INDEX_BY_CODE["007751"] == "中证沪港深红利成长低波动指数"

    def test_hk_indexes(self) -> None:
        assert TRACKING_INDEX_BY_CODE["004098"] == "中证港股通高股息投资指数"
        assert TRACKING_INDEX_BY_CODE["021457"] == "恒生港股通高股息低波动指数"

    def test_free_cash_flow_indexes(self) -> None:
        assert TRACKING_INDEX_BY_CODE["023917"] == "国证自由现金流指数"
        assert TRACKING_INDEX_BY_CODE["025958"] == "中证全指自由现金流指数"

    def test_active_fund_records_benchmark(self) -> None:
        """022164 是主动基金，TRACKING_INDEX 里记业绩基准（不是真跟踪）。"""
        assert TRACKING_INDEX_BY_CODE["022164"] == "中证中央企业综合指数"


class TestActiveFundCodes:
    def test_only_022164_is_active(self) -> None:
        """11 只里只有 022164 主动。"""
        assert ACTIVE_FUND_CODES == frozenset({"022164"})

    def test_index_funds_are_passive(self) -> None:
        """其余 10 只都算被动跟踪指数（用指数分位调仓）。"""
        passive = {"008163", "005561", "007605", "008114", "007751",
                   "004098", "021457", "023917", "025958", "025682"}
        for code in passive:
            assert code not in ACTIVE_FUND_CODES


class TestCostBasis:
    def test_has_eleven_costs(self) -> None:
        """11 只基金都应有成本数字。"""
        assert len(DIVIDEND_COST_BASIS_BY_CODE) == 11
        assert set(DIVIDEND_COST_BASIS_BY_CODE.keys()) == set(DIVIDEND_STRATEGY_BY_CODE.keys())

    def test_total_is_343000(self) -> None:
        """合计 343,000 CNY（liubo 2026-09-26 截图 + 录入）。"""
        assert DIVIDEND_TOTAL_COST_CNY == Decimal("343000")
        assert sum(DIVIDEND_COST_BASIS_BY_CODE.values()) == DIVIDEND_TOTAL_COST_CNY

    def test_a_share_costs_sum(self) -> None:
        """A 股红利低波 4 只 = 220,000 CNY。"""
        a_share_cost = sum(
            DIVIDEND_COST_BASIS_BY_CODE[c]
            for c in ("008163", "005561", "007605", "008114")
        )
        assert a_share_cost == Decimal("220000")

    def test_ah_cost(self) -> None:
        assert DIVIDEND_COST_BASIS_BY_CODE["007751"] == Decimal("60000")

    def test_hk_costs_sum(self) -> None:
        """港股高股息 2 只 = 51,000 CNY。"""
        hk_cost = sum(DIVIDEND_COST_BASIS_BY_CODE[c] for c in ("004098", "021457"))
        assert hk_cost == Decimal("51000")

    def test_fcf_costs_sum(self) -> None:
        """自由现金流 2 只 = 6,000 CNY。"""
        fcf_cost = sum(DIVIDEND_COST_BASIS_BY_CODE[c] for c in ("023917", "025958"))
        assert fcf_cost == Decimal("6000")

    def test_specific_smart_high_div_cost(self) -> None:
        assert DIVIDEND_COST_BASIS_BY_CODE["025682"] == Decimal("5000")

    def test_active_fund_cost(self) -> None:
        assert DIVIDEND_COST_BASIS_BY_CODE["022164"] == Decimal("1000")


class TestGetStrategy:
    def test_known_fund(self) -> None:
        assert get_strategy("023917") == "自由现金流"
        assert get_strategy("025682") == "高股息策略"
        assert get_strategy("022164") == "主动-央企-量化"

    def test_unknown_fund_returns_none(self) -> None:
        """未在 11 只清单里的基金返回 None，不抛错。"""
        assert get_strategy("999999") is None
        assert get_strategy("") is None

    def test_global_allocation_funds_return_none(self) -> None:
        """大类资产配置基金（如 013310）不在红利策略里。"""
        assert get_strategy("013310") is None
        assert get_strategy("004137") is None


class TestGetTrackingIndex:
    def test_known_fund(self) -> None:
        assert get_tracking_index("023917") == "国证自由现金流指数"
        assert get_tracking_index("022164") == "中证中央企业综合指数"

    def test_unknown_fund_returns_none(self) -> None:
        assert get_tracking_index("999999") is None


class TestGetCostBasis:
    def test_known_fund(self) -> None:
        assert get_cost_basis("023917") == Decimal("5000")
        assert get_cost_basis("022164") == Decimal("1000")

    def test_unknown_fund_returns_none(self) -> None:
        assert get_cost_basis("999999") is None


class TestIsActiveFund:
    def test_true_for_022164(self) -> None:
        assert is_active_fund("022164") is True

    def test_false_for_passive_funds(self) -> None:
        assert is_active_fund("023917") is False
        assert is_active_fund("025682") is False
        assert is_active_fund("008163") is False

    def test_false_for_unknown(self) -> None:
        assert is_active_fund("999999") is False


class TestParentEtfCode:
    """联接基金 → 母 ETF 代码映射（liubo 2026-09-26 查证）。"""

    def test_has_four_lianjie_funds(self) -> None:
        """4 只联接基金有母 ETF（008163/007605/025958/025682）。"""
        assert len(PARENT_EFFOCODE_BY_CODE) == 4

    def test_specific_mappings(self) -> None:
        assert PARENT_EFFOCODE_BY_CODE["008163"] == "515450"   # 南方标普红利低波50
        assert PARENT_EFFOCODE_BY_CODE["007605"] == "515300"   # 嘉实沪深300红利低波动
        assert PARENT_EFFOCODE_BY_CODE["025958"] == "159232"   # 南方中证全指自由现金流
        assert PARENT_EFFOCODE_BY_CODE["025682"] == "159207"   # 广发中证智选高股息策略

    def test_004098_not_in_parent_etf_dict(self) -> None:
        """004098 是主动基金，无母 ETF。"""
        assert "004098" not in PARENT_EFFOCODE_BY_CODE

    def test_022164_not_in_parent_etf_dict(self) -> None:
        """022164 是主动基金，无母 ETF。"""
        assert "022164" not in PARENT_EFFOCODE_BY_CODE


class TestGetParentEtfCode:
    def test_known_lianjie_fund(self) -> None:
        assert get_parent_etf_code("008163") == "515450"
        assert get_parent_etf_code("007605") == "515300"
        assert get_parent_etf_code("025958") == "159232"
        assert get_parent_etf_code("025682") == "159207"

    def test_active_fund_returns_none(self) -> None:
        """主动基金（022164 / 004098）返回 None。"""
        assert get_parent_etf_code("022164") is None
        assert get_parent_etf_code("004098") is None

    def test_unknown_fund_returns_none(self) -> None:
        assert get_parent_etf_code("999999") is None

    def test_global_allocation_funds_return_none(self) -> None:
        """大类资产配置基金不在红利策略里，parent ETF 返回 None。"""
        assert get_parent_etf_code("013310") is None
        assert get_parent_etf_code("004137") is None


class TestDisplayName:
    def test_contains_label_and_code(self) -> None:
        """显示名 = "标签（基金代码）"格式，方便飞书表格展示。"""
        assert DISPLAY_NAME["023917"] == "自由现金流（023917）"
        assert DISPLAY_NAME["022164"] == "主动-央企-量化（022164）"

    def test_count_matches_mapping(self) -> None:
        """显示名跟 mapping 一一对应。"""
        assert set(DISPLAY_NAME.keys()) == set(DIVIDEND_STRATEGY_BY_CODE.keys())


class TestSeparateFromGlobalAllocation:
    """关键设计点：这 11 只基金**不属于** Swensen 大类资产配置。"""

    def test_023917_not_in_global_mapping(self) -> None:
        """023917 不在 breakdown.SUBCLASS_BY_CODE 里——它属于红利策略组合。"""
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE

        assert "023917" not in SUBCLASS_BY_CODE

    def test_all_dividend_funds_not_in_global(self) -> None:
        """红利策略 11 只都不在大类资产配置清单里。"""
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE

        for code in DIVIDEND_STRATEGY_BY_CODE:
            assert code not in SUBCLASS_BY_CODE, f"{code} 不应该同时属于两个组合"

    def test_moved_funds_now_in_global(self) -> None:
        """第十九轮移走的 022448/007997 现在属于大类资产配置，不属于红利策略。"""
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE

        # 022448 → CN_EQUITY（国泰A500 联接）
        assert "022448" in SUBCLASS_BY_CODE
        # 007997 → CN_GOV_BOND（易方达年年恒秋）
        assert "007997" in SUBCLASS_BY_CODE

    def test_014673_not_in_either(self) -> None:
        """014673 港股（2026-09-22 砍掉）现在不在两个组合里。"""
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE

        assert "014673" not in SUBCLASS_BY_CODE
        assert get_strategy("014673") is None