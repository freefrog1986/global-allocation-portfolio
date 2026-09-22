"""测试 src/global_allocation/portfolio/dividend_strategy.py。

参照 spec 097（红利策略组合独立模块，liubo 2026-09-20）。
"""

from __future__ import annotations

from global_allocation.portfolio.dividend_strategy import (
    DIVIDEND_STRATEGY_BY_CODE,
    DISPLAY_NAME,
    get_strategy,
)


class TestDividendStrategyByCode:
    def test_has_four_funds(self) -> None:
        """4 只已知基金。"""
        assert len(DIVIDEND_STRATEGY_BY_CODE) == 4

    def test_specific_mappings(self) -> None:
        """liubo 2026-09-20 给的 4 只基金 + 标签。"""
        assert DIVIDEND_STRATEGY_BY_CODE["022448"] == "A500宽基"   # 国泰中证A500ETF发起联接A
        assert DIVIDEND_STRATEGY_BY_CODE["007997"] == "短债"        # 易方达年年恒秋一年定开债A
        assert DIVIDEND_STRATEGY_BY_CODE["023917"] == "自由现金流"  # 华夏国证自由现金流ETF发起式联接A
        assert DIVIDEND_STRATEGY_BY_CODE["025682"] == "高股息"      # 广发高股息ETF联接A


class TestGetStrategy:
    def test_known_fund(self) -> None:
        assert get_strategy("022448") == "A500宽基"
        assert get_strategy("025682") == "高股息"

    def test_unknown_fund_returns_none(self) -> None:
        """未在 4 只清单里的基金返回 None，不抛错。"""
        assert get_strategy("999999") is None
        assert get_strategy("") is None


class TestDisplayName:
    def test_contains_label_and_code(self) -> None:
        """显示名 = "标签（基金代码）"格式，方便飞书表格展示。"""
        assert DISPLAY_NAME["022448"] == "A500宽基（022448）"
        assert DISPLAY_NAME["025682"] == "高股息（025682）"

    def test_count_matches_mapping(self) -> None:
        """显示名跟 mapping 一一对应。"""
        assert set(DISPLAY_NAME.keys()) == set(DIVIDEND_STRATEGY_BY_CODE.keys())


class TestSeparateFromGlobalAllocation:
    """关键设计点：这 4 只基金**不属于** Swensen 大类资产配置。"""

    def test_022448_not_in_global_mapping(self) -> None:
        """022448 不在 breakdown.SBCLASS_BY_CODE 里——它属于红利策略组合，不属于大类资产配置。"""
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE

        assert "022448" not in SUBCLASS_BY_CODE

    def test_dividend_funds_not_in_global(self) -> None:
        """红利策略 4 只都不在大类资产配置清单里。"""
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE

        for code in DIVIDEND_STRATEGY_BY_CODE:
            assert code not in SUBCLASS_BY_CODE, f"{code} 不应该同时属于两个组合"

    def test_014673_also_not_in_global(self) -> None:
        """014673 港股（liubo 2026-09-20 加过，2026-09-22 又砍掉）现在不在两个组合里。

        第十八轮港股全砍，014673 从 SUBCLASS_BY_CODE 移除；它也不在红利策略里。
        """
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE

        assert "014673" not in SUBCLASS_BY_CODE
        assert get_strategy("014673") is None
