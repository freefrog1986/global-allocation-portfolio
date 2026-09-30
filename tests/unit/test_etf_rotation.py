"""测试 src/global_allocation/portfolio/etf_rotation.py。

liubo 2026-09-28：扩到 9 只基金，全部成本由 liubo 校准完成（91,420 CNY）。
2026-09-29：liubo 把 013127 汇添富恒生科技 ETF 联接发起式(QDII)A 转到大类资产配置组合 → 8 只（66,420 CNY）。
8 只拆成 4 类：
- 港股科技互联网（3）：006327 / 014673 / 016495
- 港股银行（1）：006809
- 亚太除日本（1）：457001
- 全球科技主动（3）：016664 / 006373 / 017730

成本演变：
- 2026-09-28：457001 = 4,640（liubo 第一轮确认），013127/016495（第二轮），其余 6 只（第三轮，全部 liubo 给准确数）
- 2026-09-29：013127 转出 → 8 只
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


# 8 只基金分 4 类（2026-09-29 减 013127 后）
EXPECTED_FUNDS = {
    "006327", "014673", "016495",      # 港股科技互联网（3）
    "006809",                          # 港股银行
    "457001",                          # 亚太除日本
    "016664", "006373", "017730",      # 全球科技主动
}


class TestEtfRotationByCode:
    def test_has_eight_funds(self) -> None:
        """2026-09-29 减 013127 后剩 8 只。"""
        assert len(ETF_ROTATION_BY_CODE) == 8

    def test_all_expected_funds_present(self) -> None:
        assert set(ETF_ROTATION_BY_CODE.keys()) == EXPECTED_FUNDS

    def test_457001_mapped(self) -> None:
        """457001 国富亚洲机会是首批进入 ETF 轮动组合的基金（liubo 2026-09-22）。"""
        assert ETF_ROTATION_BY_CODE["457001"] == "亚太除日本"

    def test_013127_no_longer_in_rotation(self) -> None:
        """013127 2026-09-29 转到大类资产配置组合，不在 ETF 轮动里。"""
        assert "013127" not in ETF_ROTATION_BY_CODE


class TestStrategyCategories:
    """4 类分类（liubo 2026-09-28 拍板）。"""

    def test_has_four_categories(self) -> None:
        assert len(STRATEGY_CATEGORIES) == 4

    def test_hk_tech_internet_has_three(self) -> None:
        """港股科技互联网 3 只：006327/014673/016495（2026-09-29 减 013127）。"""
        assert set(STRATEGY_CATEGORIES["港股科技互联网"]) == {
            "006327", "014673", "016495",
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
        """4 类的基金并集 = ETF_ROTATION_BY_CODE 的全部 8 只。"""
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

    def test_013127_no_longer_in_rotation(self) -> None:
        """013127 已转到大类资产配置组合，不在 ETF 轮动策略里。"""
        assert get_strategy("013127") is None

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

    def test_013127_no_longer_in_display_name(self) -> None:
        """013127 已转走，不再在 DISPLAY_NAME。"""
        assert "013127" not in DISPLAY_NAME


class TestCostBasis:
    """成本数据（liubo 2026-09-28 全部校准 + 2026-09-29 减 013127）。"""

    def test_457001_amount(self) -> None:
        """457001 成本 4,640 CNY（liubo 2026-09-28 确认）。"""
        assert COST_BASIS_BY_CODE["457001"] == Decimal("4640")

    @pytest.mark.parametrize("code,expected_cost", [
        ("006327", Decimal("10000")),     # liubo 2026-09-28 确认
        ("014673", Decimal("23000")),     # liubo 2026-09-28 确认
        ("016495", Decimal("20000")),     # liubo 2026-09-28 确认
        ("006809", Decimal("1010")),      # liubo 2026-09-28 确认
        ("016664", Decimal("2370")),      # liubo 2026-09-28 确认
        ("006373", Decimal("100")),       # liubo 2026-09-28 确认
        ("017730", Decimal("5300")),      # liubo 2026-09-28 确认
    ])
    def test_costs(self, code: str, expected_cost: Decimal) -> None:
        """7 只成本全部 liubo 校准。"""
        assert COST_BASIS_BY_CODE[code] == expected_cost

    def test_013127_no_longer_in_rotation(self) -> None:
        """013127 已转到大类资产配置，不在 ETF 轮动 COST_BASIS_BY_CODE。"""
        assert "013127" not in COST_BASIS_BY_CODE

    def test_total_matches_sum(self) -> None:
        """TOTAL_COST_CNY = sum(COST_BASIS_BY_CODE.values()) = 66,420 CNY。

        8 只全部 liubo 2026-09-28 校准完成 + 2026-09-29 减 013127 -25000。
        """
        total = sum(COST_BASIS_BY_CODE.values(), Decimal("0"))
        assert total == TOTAL_COST_CNY
        assert TOTAL_COST_CNY == Decimal("66420")

    def test_get_cost_basis_known(self) -> None:
        assert get_cost_basis("457001") == Decimal("4640")
        assert get_cost_basis("016495") == Decimal("20000")
        assert get_cost_basis("006327") == Decimal("10000")
        assert get_cost_basis("006373") == Decimal("100")  # 最小持仓

    def test_get_cost_basis_013127_returns_none(self) -> None:
        """013127 不再属于 ETF 轮动组合。"""
        assert get_cost_basis("013127") is None

    def test_get_cost_basis_unknown(self) -> None:
        assert get_cost_basis("999999") is None


class TestPortfolioTotals:
    """组合层面的总资产 / 总盈亏 / 收益率（liubo 2026-09-28 拍板口径 + 2026-09-29 减 013127）。"""

    def test_snapshot_date(self) -> None:
        """snapshot 日期 2026-09-29（013127 移走当天）。"""
        assert CURRENT_SNAPSHOT_DATE.year == 2026
        assert CURRENT_SNAPSHOT_DATE.month == 9
        assert CURRENT_SNAPSHOT_DATE.day == 29

    def test_total_assets(self) -> None:
        """总资产 62,090.41 CNY（8 只「资产」列加总）。

        85460.41 - 013127 估算市值 23,370 = 62,090.41。
        013127 估算市值按 0.935 × 成本 25000 = 23,370（参考整体 PNL 系数 -0.0652）。
        """
        assert CURRENT_TOTAL_ASSETS_CNY == Decimal("62090.41")

    def test_total_pnl_uses_cost_minus_assets(self) -> None:
        """总盈亏 = 资产 - 成本 = -4,329.59 CNY。"""
        expected = CURRENT_TOTAL_ASSETS_CNY - TOTAL_COST_CNY
        assert CURRENT_TOTAL_PNL_CNY == expected
        assert CURRENT_TOTAL_PNL_CNY == Decimal("-4329.59")

    def test_total_return_pct(self) -> None:
        """总收益率 = P&L / 成本 ≈ -6.52%。"""
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
        """券商 app 持仓收益 < 真实 P&L 绝对值（app 漏算历史买入成本）。"""
        real_pnl = CURRENT_TOTAL_PNL_CNY
        # 真实 P&L 是 -4,329.59（亏损），app 应该漏算部分历史买入 → 看起来亏得少
        # 这里只验证真实 P&L 是负数且绝对值合理
        assert real_pnl < Decimal("0")
        assert abs(real_pnl) > Decimal("4000")


class TestSeparateFromOtherPortfolios:
    """关键设计点：8 只都不属于 Swensen 大类资产，也不属于红利策略。"""

    @pytest.mark.parametrize("code", [
        "006327", "014673", "016495", "006809",
        "457001", "016664", "006373", "017730",
    ])
    def test_funds_not_in_global_mapping(self, code: str) -> None:
        """8 只都不在 breakdown.SUBCLASS_BY_CODE 里。"""
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE

        assert code not in SUBCLASS_BY_CODE, code

    def test_013127_now_in_global_mapping(self) -> None:
        """013127 2026-09-29 转到大类资产配置 → SUBCLASS_BY_CODE 里有它。"""
        from global_allocation.portfolio.breakdown import (
            SUBCLASS_BY_CODE, SwensenClass,
        )

        assert SUBCLASS_BY_CODE["013127"] == SwensenClass.HK_EQUITY

    @pytest.mark.parametrize("code", [
        "006327", "014673", "016495", "006809",
        "457001", "016664", "006373", "017730",
    ])
    def test_funds_not_in_global_cost_basis(self, code: str) -> None:
        """8 只都不在 cost_basis.COST_BASIS_BY_CODE 里（大类资产配置的成本表）。"""
        from global_allocation.portfolio.cost_basis import COST_BASIS_BY_CODE as GLOBAL

        assert code not in GLOBAL, code

    def test_013127_now_in_global_cost_basis(self) -> None:
        """013127 2026-09-29 转到大类资产配置 → COST_BASIS_BY_CODE 里有它（25000 CNY）。"""
        from global_allocation.portfolio.cost_basis import COST_BASIS_BY_CODE as GLOBAL

        assert GLOBAL["013127"] == Decimal("25000")

    @pytest.mark.parametrize("code", [
        "006327", "014673", "016495", "006809",
        "457001", "016664", "006373", "017730",
    ])
    def test_funds_not_in_dividend_strategy(self, code: str) -> None:
        """8 只都不在 dividend_strategy 里（红利策略 11 只不含它们）。"""
        from global_allocation.portfolio.dividend_strategy import (
            DIVIDEND_STRATEGY_BY_CODE,
        )

        assert code not in DIVIDEND_STRATEGY_BY_CODE, code


# ─── 策略规则（liubo 2026-09-29 拍板：主观轮动 + 严格仓位管理） ───


class TestPositionUnit:
    """1 仓 = 5,000 CNY（独立于大类资产配置的 1 万；不混）。"""

    def test_position_unit_value(self) -> None:
        from global_allocation.portfolio.etf_rotation import POSITION_UNIT_CNY

        assert POSITION_UNIT_CNY == Decimal("5000")

    def test_position_unit_independent_from_global(self) -> None:
        """跟大类资产配置的 1 万不混。"""
        from global_allocation.portfolio.etf_rotation import POSITION_UNIT_CNY

        assert POSITION_UNIT_CNY != Decimal("10000")


class TestGetPosition:
    """get_position: cost / 5000 = 仓数（未知基金 → 0）。"""

    def test_457001_partial_position(self) -> None:
        """457001 成本 4,640 → 0.928 仓。"""
        from global_allocation.portfolio.etf_rotation import get_position

        pos = get_position("457001")
        assert pos == Decimal("4640") / Decimal("5000")
        assert float(pos) == pytest.approx(0.928, rel=0.01)

    def test_full_position(self) -> None:
        """006327 成本 10,000 = 2 仓整。"""
        from global_allocation.portfolio.etf_rotation import get_position

        pos = get_position("006327")
        assert pos == Decimal("2")

    def test_large_position(self) -> None:
        """014673 成本 23,000 → 4.6 仓。"""
        from global_allocation.portfolio.etf_rotation import get_position

        pos = get_position("014673")
        assert float(pos) == pytest.approx(4.6, rel=0.01)

    def test_unknown_fund_returns_zero(self) -> None:
        """未知基金 → 0 仓。"""
        from global_allocation.portfolio.etf_rotation import get_position

        assert get_position("999999") == Decimal("0")
        assert get_position("") == Decimal("0")


class TestCategoryCapStatus:
    """get_category_cap_status: cap = 总成本 × 30%，使用率 = current / cap。"""

    def test_total_cap_is_30pct_of_total(self) -> None:
        """每类上限 = 66,420 × 30% = 19,926 CNY。"""
        from global_allocation.portfolio.etf_rotation import get_category_cap_status

        status = get_category_cap_status("港股银行")
        assert status["cap_cny"] == TOTAL_COST_CNY * Decimal("0.30")
        assert status["cap_cny"] == Decimal("66420") * Decimal("0.30")

    def test_hk_tech_internet_over_cap(self) -> None:
        """港股科技互联网 3 只累计 53,000 / 19,926 = 266%（超限，老仓位豁免）。"""
        from global_allocation.portfolio.etf_rotation import get_category_cap_status

        status = get_category_cap_status("港股科技互联网")
        assert status["current_cny"] == Decimal("53000")
        assert status["is_over"] is True
        assert float(status["pct_used"]) > Decimal("2")  # 200% 以上

    def test_hk_bank_under_cap(self) -> None:
        """港股银行 1,010 / 19,926 = 5.07%（远低于上限）。"""
        from global_allocation.portfolio.etf_rotation import get_category_cap_status

        status = get_category_cap_status("港股银行")
        assert status["current_cny"] == Decimal("1010")
        assert status["is_over"] is False
        assert float(status["pct_used"]) < Decimal("0.1")  # 10% 以下

    def test_remaining_cny_negative_when_over(self) -> None:
        """超限时 remaining_cny 为负（表示已超多少）。"""
        from global_allocation.portfolio.etf_rotation import get_category_cap_status

        status = get_category_cap_status("港股科技互联网")
        assert status["remaining_cny"] < Decimal("0")

    def test_remaining_cny_positive_when_under(self) -> None:
        """未超限时 remaining_cny 为正（还能加仓多少）。"""
        from global_allocation.portfolio.etf_rotation import get_category_cap_status

        status = get_category_cap_status("港股银行")
        assert status["remaining_cny"] > Decimal("0")

    def test_unknown_category_returns_zero(self) -> None:
        """未知类别 → current_cny=0。"""
        from global_allocation.portfolio.etf_rotation import get_category_cap_status

        status = get_category_cap_status("未知类别")
        assert status["current_cny"] == Decimal("0")


class TestCheckCategoryCapForBuy:
    """check_category_cap_for_buy: 老仓位豁免 — 超限仍允许，只卡新买时的额外加仓。"""

    def test_over_cap_still_allows_buy(self) -> None:
        """港股科技互联网当前 53,000 已超限，新买 5,000 仍允许（豁免）。"""
        from global_allocation.portfolio.etf_rotation import check_category_cap_for_buy

        ok, reason = check_category_cap_for_buy("006327", Decimal("5000"))
        assert ok is True
        assert "豁免" in reason or "老仓位" in reason

    def test_under_cap_allows_normal_buy(self) -> None:
        """港股银行当前 1,010，新买 5,000 后 = 6,010 << 19,926 → 允许。"""
        from global_allocation.portfolio.etf_rotation import check_category_cap_for_buy

        ok, reason = check_category_cap_for_buy("006809", Decimal("5000"))
        assert ok is True
        assert "未超" in reason or "上限" in reason

    def test_unknown_fund_rejected(self) -> None:
        """未知基金 → 拒绝。"""
        from global_allocation.portfolio.etf_rotation import check_category_cap_for_buy

        ok, reason = check_category_cap_for_buy("999999", Decimal("5000"))
        assert ok is False
        assert "未知" in reason


class TestCooldown:
    """check_cooldown: 同基金冷却期 7 天。"""

    def test_no_prior_trade_allows(self) -> None:
        """首次交易：无冷却期记录 → 允许。"""
        from global_allocation.portfolio.etf_rotation import check_cooldown

        ok, reason = check_cooldown("457001", today=__import__("datetime").date(2026, 9, 29))
        assert ok is True
        assert "首次" in reason or "冷却" in reason

    def test_within_cooldown_blocks(self) -> None:
        """5 天前交易过 → 还剩 2 天冷却期。"""
        from datetime import date as _date
        from global_allocation.portfolio.etf_rotation import (
            LAST_TRADE_BY_FUND,
            check_cooldown,
            record_trade,
        )

        record_trade("457001", _date(2026, 9, 24))  # 5 天前
        try:
            ok, reason = check_cooldown("457001", today=_date(2026, 9, 29))
            assert ok is False
            assert "剩 2 天" in reason
        finally:
            # 清理测试副作用（避免污染其他测试）
            LAST_TRADE_BY_FUND.pop("457001", None)

    def test_outside_cooldown_allows(self) -> None:
        """7 天前交易过 → 已过冷却期。"""
        from datetime import date as _date
        from global_allocation.portfolio.etf_rotation import (
            LAST_TRADE_BY_FUND,
            check_cooldown,
            record_trade,
        )

        record_trade("457001", _date(2026, 9, 22))  # 7 天前
        try:
            ok, reason = check_cooldown("457001", today=_date(2026, 9, 29))
            assert ok is True
            assert "已过" in reason
        finally:
            LAST_TRADE_BY_FUND.pop("457001", None)

    def test_record_trade_updates_dict(self) -> None:
        """record_trade 后 LAST_TRADE_BY_FUND 有记录。"""
        from datetime import date as _date
        from global_allocation.portfolio.etf_rotation import (
            LAST_TRADE_BY_FUND,
            record_trade,
        )

        test_date = _date(2026, 9, 28)
        record_trade("006809", test_date)
        try:
            assert LAST_TRADE_BY_FUND["006809"] == test_date
        finally:
            LAST_TRADE_BY_FUND.pop("006809", None)


class TestFormatWeeklySnapshotText:
    """format_weekly_snapshot_text: 纯文本快照（CLI 默认输出）。"""

    def test_contains_total_cost(self) -> None:
        from global_allocation.portfolio.etf_rotation import format_weekly_snapshot_text

        text = format_weekly_snapshot_text(today=__import__("datetime").date(2026, 9, 29))
        assert "66,420" in text  # 总成本
        assert "62,090.41" in text  # 总资产
        assert "-4,329.59" in text or "-4329.59" in text  # 总盈亏

    def test_contains_all_four_categories(self) -> None:
        """4 类都有标题。"""
        from global_allocation.portfolio.etf_rotation import format_weekly_snapshot_text

        text = format_weekly_snapshot_text(today=__import__("datetime").date(2026, 9, 29))
        assert "港股科技互联网" in text
        assert "港股银行" in text
        assert "亚太除日本" in text
        assert "全球科技主动" in text

    def test_over_cap_marked(self) -> None:
        """超限类别有 ⚠️ 标记。"""
        from global_allocation.portfolio.etf_rotation import format_weekly_snapshot_text

        text = format_weekly_snapshot_text(today=__import__("datetime").date(2026, 9, 29))
        assert "⚠️超限" in text or "⚠️ 超限" in text


class TestExports:
    """__all__ 必须包含新加的策略规则函数。"""

    def test_position_management_exports(self) -> None:
        import global_allocation.portfolio.etf_rotation as mod

        assert "POSITION_UNIT_CNY" in mod.__all__
        assert "CATEGORY_CAP_PCT" in mod.__all__
        assert "COOLDOWN_DAYS" in mod.__all__
        assert "LAST_TRADE_BY_FUND" in mod.__all__
        assert "get_position" in mod.__all__
        assert "get_category_cap_status" in mod.__all__
        assert "check_category_cap_for_buy" in mod.__all__
        assert "check_cooldown" in mod.__all__
        assert "record_trade" in mod.__all__
        assert "format_weekly_snapshot_text" in mod.__all__
        assert "get_category_total_cost" in mod.__all__