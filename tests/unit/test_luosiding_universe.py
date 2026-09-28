"""测试 src/global_allocation/portfolio/luosiding_universe.py。

liubo 2026-09-26 拍板：红利策略组合只关注螺丝钉估值表 21 只指数。
- 21 只指数 universe，每只算红利策略仓位（cost / 10000）
- 不在螺丝钉表的 5 只红利 + 1 只主动基金单独列 EXTRAS
- 大类资产 / ETF 轮动组合仓位不串
"""

from __future__ import annotations

from decimal import Decimal

from global_allocation.portfolio.luosiding_universe import (
    EXTRA_HOLDINGS,
    LUOSIDING_SNAPSHOT_DATE,
    LUOSIDING_TO_DIVIDEND_FUND_CODES,
    LUOSIDING_UNIVERSE,
    LuosidingIndex,
    ExtraHolding,
    ExtraReason,
    compute_luosiding_positions,
)


class TestSnapshotDate:
    def test_snapshot_date_set(self) -> None:
        """截图日期 2026-09-25。"""
        assert LUOSIDING_SNAPSHOT_DATE == "2026-09-25"


class TestLuosidingUniverse:
    def test_has_twenty_one_indices(self) -> None:
        """21 只螺丝钉指数（截图 1 10 + 截图 2 11）。"""
        assert len(LUOSIDING_UNIVERSE) == 21

    def test_all_are_luosiding_index(self) -> None:
        for idx in LUOSIDING_UNIVERSE:
            assert isinstance(idx, LuosidingIndex)

    def test_unique_display_names(self) -> None:
        """每只指数显示名唯一。"""
        names = [idx.display_name for idx in LUOSIDING_UNIVERSE]
        assert len(set(names)) == 21

    def test_valuation_values_are_valid(self) -> None:
        """估值只能是偏低/适中/偏高。"""
        for idx in LUOSIDING_UNIVERSE:
            assert idx.valuation in ("偏低", "适中", "偏高"), idx.display_name

    def test_screenshot1_indices(self) -> None:
        """截图 1（10 只）：中证A500 / 标普500 / 中证1000 / 纳100 / 上证红利 / 龙头红利 / 港股红利 / 红利机会 / 基本面50 / 科创50。"""
        names = [idx.display_name for idx in LUOSIDING_UNIVERSE[:10]]
        expected = ["中证A500", "标普500", "中证1000", "纳斯达克100",
                    "上证红利", "龙头红利", "港股红利", "红利机会", "基本面50", "科创50"]
        assert names == expected

    def test_screenshot2_indices(self) -> None:
        """截图 2（11 只）：沪港深红利低波 / 自由现金流 / 中证红利低波动 / 红利低波100 / 恒生红利低波动 / 中证红利 / 消费红利 / 300价值 / 中证消费 / 中证白酒 / 恒生消费。"""
        names = [idx.display_name for idx in LUOSIDING_UNIVERSE[10:]]
        expected = ["沪港深红利低波", "自由现金流", "中证红利低波动", "红利低波100",
                    "恒生红利低波动", "中证红利", "消费红利", "300价值",
                    "中证消费", "中证白酒", "恒生消费"]
        assert names == expected

    def test_screenshot2_all_low(self) -> None:
        """截图 2 11 只全部"偏低"。"""
        for idx in LUOSIDING_UNIVERSE[10:]:
            assert idx.valuation == "偏低", f"{idx.display_name} 应该是偏低"

    def test_kechuang50_is_high(self) -> None:
        """科创50 在截图 1 里是唯一"偏高"。"""
        kechuang = next(i for i in LUOSIDING_UNIVERSE if i.display_name == "科创50")
        assert kechuang.valuation == "偏高"
        assert kechuang.pe_ttm == Decimal("107.37")

    def test_specific_pe_values(self) -> None:
        """抽样几个 PE 值。"""
        expectations = {
            "中证A500": Decimal("19.22"),
            "标普500": Decimal("24.61"),
            "中证红利低波动": Decimal("8.57"),
            "恒生红利低波动": Decimal("8.77"),
        }
        by_name = {i.display_name: i for i in LUOSIDING_UNIVERSE}
        for name, pe in expectations.items():
            assert by_name[name].pe_ttm == pe, name

    def test_dividend_yield_percentages(self) -> None:
        """股息率（4-5% 区间）合理。"""
        for idx in LUOSIDING_UNIVERSE:
            if idx.dividend_yield is not None:
                assert Decimal("0.001") <= idx.dividend_yield <= Decimal("0.10"), (
                    f"{idx.display_name} 股息率 {idx.dividend_yield} 异常"
                )


class TestLuodingMapping:
    def test_maps_five_dividend_funds(self) -> None:
        """5 只红利基金对应 5 只螺丝钉指数。"""
        assert len(LUOSIDING_TO_DIVIDEND_FUND_CODES) == 5

    def test_specific_mappings(self) -> None:
        assert LUOSIDING_TO_DIVIDEND_FUND_CODES["中证红利低波动"] == ["005561"]
        assert LUOSIDING_TO_DIVIDEND_FUND_CODES["红利低波100"] == ["008114"]
        assert LUOSIDING_TO_DIVIDEND_FUND_CODES["沪港深红利低波"] == ["007751"]
        assert LUOSIDING_TO_DIVIDEND_FUND_CODES["自由现金流"] == ["023917"]
        assert LUOSIDING_TO_DIVIDEND_FUND_CODES["恒生红利低波动"] == ["021457"]

    def test_all_funds_map_to_in_universe(self) -> None:
        """所有映射到的指数名都在 universe 里。"""
        names = {i.display_name for i in LUOSIDING_UNIVERSE}
        for display_name in LUOSIDING_TO_DIVIDEND_FUND_CODES:
            assert display_name in names, display_name


class TestComputeLuosidingPositions:
    def test_returns_twenty_one_rows(self) -> None:
        rows = compute_luosiding_positions()
        assert len(rows) == 21

    def test_five_indices_with_positions(self) -> None:
        """5 只指数有红利策略仓位。"""
        rows = compute_luosiding_positions()
        with_pos = [r for r in rows if r.total_position_lots > 0]
        assert len(with_pos) == 5

    def test_sixteen_indices_are_zero(self) -> None:
        """16 只指数红利策略仓位 = 0。"""
        rows = compute_luosiding_positions()
        zero_pos = [r for r in rows if r.total_position_lots == Decimal("0")]
        assert len(zero_pos) == 16

    def test_specific_position_values(self) -> None:
        """抽样验证仓位（cost / 10000）。"""
        rows = compute_luosiding_positions()
        by_name = {r.index.display_name: r for r in rows}

        # 005561 → 中证红利低波动：60,000 CNY = 6 仓
        assert by_name["中证红利低波动"].total_position_lots == Decimal("6")
        assert by_name["中证红利低波动"].total_cost_cny == Decimal("60000")

        # 008114 → 红利低波100：50,000 CNY = 5 仓
        assert by_name["红利低波100"].total_position_lots == Decimal("5")

        # 007751 → 沪港深红利低波：60,000 CNY = 6 仓
        assert by_name["沪港深红利低波"].total_position_lots == Decimal("6")

        # 023917 → 自由现金流：5,000 CNY = 0.5 仓
        assert by_name["自由现金流"].total_position_lots == Decimal("0.5")

        # 021457 → 恒生红利低波动：1,000 CNY = 0.1 仓
        assert by_name["恒生红利低波动"].total_position_lots == Decimal("0.1")

    def test_zero_position_indices_have_no_fund_codes(self) -> None:
        """0 仓的指数 fund_codes 列表为空。"""
        rows = compute_luosiding_positions()
        zero_rows = [r for r in rows if r.total_position_lots == Decimal("0")]
        for r in zero_rows:
            assert r.fund_codes == [], r.index.display_name

    def test_total_dividend_position(self) -> None:
        """所有螺丝钉 universe 里的红利仓位合计 = 17.6 仓（176,000 CNY）。"""
        rows = compute_luosiding_positions()
        total_lots = sum(r.total_position_lots for r in rows)
        assert total_lots == Decimal("17.6")


class TestExtraHoldings:
    def test_has_six_funds(self) -> None:
        """6 只 EXTRAS（5 待查 + 1 主动）。"""
        assert len(EXTRA_HOLDINGS) == 6

    def test_five_pending(self) -> None:
        """5 只 PENDING_INDEX（等 liubo 查跟踪指数）。"""
        pending = [e for e in EXTRA_HOLDINGS if e.reason == ExtraReason.PENDING_INDEX]
        assert len(pending) == 5

    def test_one_active_fund(self) -> None:
        """1 只 ACTIVE_FUND（022164）。"""
        active = [e for e in EXTRA_HOLDINGS if e.reason == ExtraReason.ACTIVE_FUND]
        assert len(active) == 1
        assert active[0].fund_code == "022164"

    def test_all_tracking_index_researched(self) -> None:
        """liubo 2026-09-26 查证完成：4 只联接基金 + 1 只主动(022164) tracking_index 已填。"""
        by_code = {e.fund_code: e for e in EXTRA_HOLDINGS}
        # 4 只联接基金：跟踪指数已查证
        assert by_code["008163"].tracking_index == "标普中国A股大盘红利低波50指数"
        assert by_code["007605"].tracking_index == "沪深300红利低波动指数"
        assert by_code["004098"].tracking_index == "中证港股通高股息投资指数"  # 业绩基准
        assert by_code["025958"].tracking_index == "中证全指自由现金流指数"
        assert by_code["025682"].tracking_index == "中证智选高股息策略指数"
        # 022164 主动基金：tracking_index 仍 None
        assert by_code["022164"].tracking_index is None

    def test_parent_etf_code_for_lianjie_funds(self) -> None:
        """4 只联接基金的母 ETF 代码已查证。"""
        by_code = {e.fund_code: e for e in EXTRA_HOLDINGS}
        assert by_code["008163"].parent_etf_code == "515450"
        assert by_code["007605"].parent_etf_code == "515300"
        assert by_code["025958"].parent_etf_code == "159232"
        assert by_code["025682"].parent_etf_code == "159207"

    def test_parent_etf_code_none_for_active_and_unknown(self) -> None:
        """主动基金（004098/022164）无母 ETF。"""
        by_code = {e.fund_code: e for e in EXTRA_HOLDINGS}
        assert by_code["004098"].parent_etf_code is None  # 前海开源主动
        assert by_code["022164"].parent_etf_code is None  # 西部利得主动

    def test_specific_pending_codes(self) -> None:
        """5 只待查的基金代码。"""
        pending_codes = {
            e.fund_code for e in EXTRA_HOLDINGS
            if e.reason == ExtraReason.PENDING_INDEX
        }
        assert pending_codes == {"008163", "007605", "004098", "025958", "025682"}

    def test_extra_total_cost(self) -> None:
        """EXTRAS 6 只合计 167,000 CNY（剩余的 167,000 不在 universe 里）。"""
        total = sum(e.cost_cny for e in EXTRA_HOLDINGS)
        # 6 + 5 + 5 + 0.1 + 0.5 + 0.1 = 16.7 仓 = 167,000 CNY
        assert total == Decimal("167000")

    def test_universe_plus_extras_equals_dividend_total(self) -> None:
        """universe 176,000 + EXTRAS 167,000 = 343,000（= 红利策略总成本）。"""
        from global_allocation.portfolio.dividend_strategy import DIVIDEND_TOTAL_COST_CNY

        universe_cost = sum(r.total_cost_cny for r in compute_luosiding_positions())
        extra_cost = sum(e.cost_cny for e in EXTRA_HOLDINGS)
        assert universe_cost + extra_cost == DIVIDEND_TOTAL_COST_CNY


class TestSeparationBetweenPortfolios:
    """关键设计点：螺丝钉 universe 只算红利策略仓位，不混大类资产。"""

    def test_global_allocation_funds_not_in_dividend_mapping(self) -> None:
        """大类资产基金不在 LUOSIDING_TO_DIVIDEND_FUND_CODES 里。"""
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE

        all_mapped_codes = set()
        for codes in LUOSIDING_TO_DIVIDEND_FUND_CODES.values():
            all_mapped_codes.update(codes)

        # 全部 5 只都应该来自 dividend_strategy，不来自 breakdown
        for code in all_mapped_codes:
            assert code not in SUBCLASS_BY_CODE, f"{code} 不应该被大类资产组合定义"

    def test_universe_positions_only_reflect_dividend(self) -> None:
        """螺丝钉 universe 里的 A500/中证1000/标普500/纳100 等位置=0，
        因为红利策略没持有（这些在大类资产组合里但分类管理）。"""
        rows = compute_luosiding_positions()
        by_name = {r.index.display_name: r for r in rows}

        # 这些大类资产指数在红利策略里都是 0 仓
        for name in ("中证A500", "中证1000", "标普500", "纳斯达克100", "科创50"):
            assert by_name[name].total_position_lots == Decimal("0"), name