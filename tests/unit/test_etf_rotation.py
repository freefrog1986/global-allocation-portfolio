"""测试 src/global_allocation/portfolio/etf_rotation.py。

liubo 2026-09-28：扩到 9 只基金，全部成本由 liubo 校准完成（91,420 CNY）：
- 港股科技互联网（4）：006327 / 013127 / 014673 / 016495
- 港股银行（1）：006809
- 亚太除日本（1）：457001
- 全球科技主动（3）：016664 / 006373 / 017730

成本演变（2026-09-28）：
- 457001 = 4,640（旧 module 值；liubo 第一轮确认）
- 013127/016495 第二轮确认（替换截图估算）
- 其余 6 只第三轮确认（全部用 liubo 给的准确数字，不用截图估算）
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from global_allocation.portfolio.etf_rotation import (
    COST_BASIS_BY_CODE,
    CURRENT_SNAPSHOT_DATE,
    CURRENT_TOTAL_ASSETS_CNY,
    CURRENT_TOTAL_PNL_CNY,
    CURRENT_TOTAL_RETURN_PCT,
    DISPLAY_NAME,
    ETF_ROTATION_BY_CODE,
    STRATEGY_CATEGORIES,
    TOTAL_COST_CNY,
    get_cost_basis,
    get_strategy,
    get_total_assets_cny,
    get_total_pnl_cny,
    get_total_return_pct,
)


# 9 只基金分 4 类（liubo 2026-09-28 拍板）
EXPECTED_FUNDS = {
    "006327", "013127", "014673", "016495",  # 港股科技互联网
    "006809",                                  # 港股银行
    "457001",                                  # 亚太除日本
    "016664", "006373", "017730",              # 全球科技主动
}


class TestEtfRotationByCode:
    def test_has_nine_funds(self) -> None:
        """2026-09-28 截图扩到 9 只基金。"""
        assert len(ETF_ROTATION_BY_CODE) == 9

    def test_all_expected_funds_present(self) -> None:
        assert set(ETF_ROTATION_BY_CODE.keys()) == EXPECTED_FUNDS

    def test_457001_mapped(self) -> None:
        """457001 国富亚洲机会是首批进入 ETF 轮动组合的基金（liubo 2026-09-22）。"""
        assert ETF_ROTATION_BY_CODE["457001"] == "亚太除日本"


class TestStrategyCategories:
    """4 类分类（liubo 2026-09-28 拍板）。"""

    def test_has_four_categories(self) -> None:
        assert len(STRATEGY_CATEGORIES) == 4

    def test_hk_tech_internet_has_four(self) -> None:
        """港股科技互联网 4 只：006327/013127/014673/016495。"""
        assert set(STRATEGY_CATEGORIES["港股科技互联网"]) == {
            "006327", "013127", "014673", "016495",
        }

    def test_hk_bank_has_one(self) -> None:
        """港股银行 1 只：006809。"""
        assert STRATEGY_CATEGORIES["港股银行"] == ["006809"]

    def test_asia_pacific_ex_japan_has_one(self) -> None:
        """亚太除日本 1 只：457001。"""
        assert STRATEGY_CATEGORIES["亚太除日本"] == ["457001"]

    def test_global_tech_active_has_three(self) -> None:
        """全球科技主动 3 只：016664/006373/017730。"""
        assert set(STRATEGY_CATEGORIES["全球科技主动"]) == {
            "016664", "006373", "017730",
        }

    def test_categories_cover_all_funds(self) -> None:
        """4 类的基金并集 = ETF_ROTATION_BY_CODE 的全部 9 只。"""
        all_in_categories: set[str] = set()
        for codes in STRATEGY_CATEGORIES.values():
            all_in_categories.update(codes)
        assert all_in_categories == EXPECTED_FUNDS

    def test_no_duplicate_funds_across_categories(self) -> None:
        """每只基金只属于 1 个分类。"""
        all_codes: list[str] = []
        for codes in STRATEGY_CATEGORIES.values():
            all_codes.extend(codes)
        assert len(all_codes) == len(set(all_codes))


class TestGetStrategy:
    @pytest.mark.parametrize("code,expected", [
        ("006327", "港股科技互联网"),
        ("013127", "港股科技互联网"),
        ("014673", "港股科技互联网"),
        ("016495", "港股科技互联网"),
        ("006809", "港股银行"),
        ("457001", "亚太除日本"),
        ("016664", "全球科技主动"),
        ("006373", "全球科技主动"),
        ("017730", "全球科技主动"),
    ])
    def test_known_funds(self, code: str, expected: str) -> None:
        assert get_strategy(code) == expected

    def test_unknown_fund_returns_none(self) -> None:
        """不在 ETF 轮动清单里的基金返回 None，不抛错。"""
        assert get_strategy("999999") is None
        assert get_strategy("") is None


class TestDisplayName:
    @pytest.mark.parametrize("code,expected", [
        ("006327", "港股科技互联网（006327）"),
        ("457001", "亚太除日本（457001）"),
        ("006809", "港股银行（006809）"),
        ("017730", "全球科技主动（017730）"),
    ])
    def test_format(self, code: str, expected: str) -> None:
        assert DISPLAY_NAME[code] == expected

    def test_count_matches_mapping(self) -> None:
        assert set(DISPLAY_NAME.keys()) == set(ETF_ROTATION_BY_CODE.keys())


class TestCostBasis:
    """成本数据（liubo 2026-09-28 全部校准完成）。"""

    def test_457001_amount(self) -> None:
        """457001 成本 4,640 CNY（liubo 2026-09-28 确认）。"""
        assert COST_BASIS_BY_CODE["457001"] == Decimal("4640")

    @pytest.mark.parametrize("code,expected_cost", [
        ("006327", Decimal("10000")),     # liubo 2026-09-28 确认
        ("013127", Decimal("25000")),     # liubo 2026-09-28 确认
        ("014673", Decimal("23000")),     # liubo 2026-09-28 确认
        ("016495", Decimal("20000")),     # liubo 2026-09-28 确认
        ("006809", Decimal("1010")),      # liubo 2026-09-28 确认
        ("016664", Decimal("2370")),      # liubo 2026-09-28 确认
        ("006373", Decimal("100")),       # liubo 2026-09-28 确认
        ("017730", Decimal("5300")),      # liubo 2026-09-28 确认
    ])
    def test_costs(self, code: str, expected_cost: Decimal) -> None:
        """8 只成本全部 liubo 校准。"""
        assert COST_BASIS_BY_CODE[code] == expected_cost

    def test_total_matches_sum(self) -> None:
        """TOTAL_COST_CNY = sum(COST_BASIS_BY_CODE.values()) = 91,420 CNY。

        9 只全部 liubo 2026-09-28 校准完成。
        """
        total = sum(COST_BASIS_BY_CODE.values(), Decimal("0"))
        assert total == TOTAL_COST_CNY
        assert TOTAL_COST_CNY == Decimal("91420")

    def test_get_cost_basis_known(self) -> None:
        assert get_cost_basis("457001") == Decimal("4640")
        assert get_cost_basis("013127") == Decimal("25000")
        assert get_cost_basis("016495") == Decimal("20000")
        assert get_cost_basis("006327") == Decimal("10000")
        assert get_cost_basis("006373") == Decimal("100")  # 最小持仓

    def test_get_cost_basis_unknown(self) -> None:
        assert get_cost_basis("999999") is None


class TestPortfolioTotals:
    """组合层面的总资产 / 总盈亏 / 收益率（liubo 2026-09-28 拍板口径）。"""

    def test_snapshot_date(self) -> None:
        """snapshot 日期 2026-09-28（liubo 截图当天）。"""
        assert CURRENT_SNAPSHOT_DATE.year == 2026
        assert CURRENT_SNAPSHOT_DATE.month == 9
        assert CURRENT_SNAPSHOT_DATE.day == 28

    def test_total_assets(self) -> None:
        """总资产 85,460.41 CNY（9 只「资产」列加总）。"""
        assert CURRENT_TOTAL_ASSETS_CNY == Decimal("85460.41")

    def test_total_pnl_uses_cost_minus_assets(self) -> None:
        """总盈亏 = 资产 - 成本 = -5,959.59 CNY。"""
        expected = CURRENT_TOTAL_ASSETS_CNY - TOTAL_COST_CNY
        assert CURRENT_TOTAL_PNL_CNY == expected
        assert CURRENT_TOTAL_PNL_CNY == Decimal("-5959.59")

    def test_total_return_pct(self) -> None:
        """总收益率 = P&L / 成本 = -6.52%（保留 4 位小数）。"""
        expected = CURRENT_TOTAL_PNL_CNY / TOTAL_COST_CNY
        assert CURRENT_TOTAL_RETURN_PCT == expected
        # 验证数值在 -6.5% 附近
        assert Decimal("-0.07") < CURRENT_TOTAL_RETURN_PCT < Decimal("-0.06")

    def test_get_total_assets_returns_constant(self) -> None:
        assert get_total_assets_cny() == CURRENT_TOTAL_ASSETS_CNY

    def test_get_total_pnl_returns_constant(self) -> None:
        assert get_total_pnl_cny() == CURRENT_TOTAL_PNL_CNY

    def test_get_total_return_pct_returns_constant(self) -> None:
        assert get_total_return_pct() == CURRENT_TOTAL_RETURN_PCT


class TestPnLMethodology:
    """P&L 口径验证（liubo 2026-09-28 拍板：不用券商 app 持仓收益数字）。

    真实 P&L = 资产 - 成本；券商 app 显示的持仓收益偏小（漏算组合成立前买入）。
    """

    def test_app_pnl_is_smaller_than_real(self) -> None:
        """券商 app 持仓收益 -1,318.65（截图 9 只合计） < 真实 P&L 绝对值 5,959.59。"""
        app_pnl_sum = Decimal("-1318.65")  # 2026-09-28 截图 9 只持仓收益加总
        real_pnl = CURRENT_TOTAL_PNL_CNY
        # app 显示的亏损绝对值小于真实亏损（app 漏算历史买入成本）
        assert abs(app_pnl_sum) < abs(real_pnl)
        # 差额
        diff = real_pnl - app_pnl_sum
        assert diff < Decimal("-4640")  # 约 -4,641 CNY（接近 457001 成本）


class TestSeparateFromOtherPortfolios:
    """关键设计点：9 只都不属于 Swensen 大类资产，也不属于红利策略。"""

    @pytest.mark.parametrize("code", [
        "006327", "013127", "014673", "016495", "006809",
        "457001", "016664", "006373", "017730",
    ])
    def test_funds_not_in_global_mapping(self, code: str) -> None:
        """9 只都不在 breakdown.SUBCLASS_BY_CODE 里。"""
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE

        assert code not in SUBCLASS_BY_CODE, code

    @pytest.mark.parametrize("code", [
        "006327", "013127", "014673", "016495", "006809",
        "457001", "016664", "006373", "017730",
    ])
    def test_funds_not_in_global_cost_basis(self, code: str) -> None:
        """9 只都不在 cost_basis.COST_BASIS_BY_CODE 里（大类资产配置的成本表）。"""
        from global_allocation.portfolio.cost_basis import COST_BASIS_BY_CODE as GLOBAL

        assert code not in GLOBAL, code

    @pytest.mark.parametrize("code", [
        "006327", "013127", "014673", "016495", "006809",
        "457001", "016664", "006373", "017730",
    ])
    def test_funds_not_in_dividend_strategy(self, code: str) -> None:
        """9 只都不在 dividend_strategy 里（红利策略 11 只不含它们）。"""
        from global_allocation.portfolio.dividend_strategy import (
            DIVIDEND_STRATEGY_BY_CODE,
        )

        assert code not in DIVIDEND_STRATEGY_BY_CODE, code