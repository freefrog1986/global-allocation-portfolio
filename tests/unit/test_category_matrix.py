"""测试 src/global_allocation/portfolio/category_matrix.py。

liubo 2026-09-26 拍板 v6：
- 9 个有效 cell（3 市场 × 5 策略）
- 10 只被动基金映射到 6 个 cell
- 022164 主动基金独立（不在矩阵里）
"""

from __future__ import annotations

from global_allocation.portfolio.category_matrix import (
    ACTIVE_FUND_CODES,
    MARKET_STRATEGY_CELLS,
    Market,
    Strategy,
    all_cells,
    all_fund_codes,
    all_indices,
    get_cell,
    get_cell_by_fund,
    get_cell_by_index,
    is_active_fund,
)


class TestMarketEnum:
    def test_three_markets(self) -> None:
        assert {m.value for m in Market} == {"A 股", "港股", "A+H"}


class TestStrategyEnum:
    def test_five_strategies(self) -> None:
        assert {s.value for s in Strategy} == {
            "红利",
            "红利+低波",
            "红利+价值",
            "自由现金流",
            "高股息",
        }


class TestMarketStrategyCells:
    def test_has_eight_cells(self) -> None:
        """8 个有效 cell：5 (A 股) + 2 (港股) + 1 (A+H)。"""
        assert len(all_cells()) == 8

    def test_a_stock_five_strategies(self) -> None:
        """A 股 5 个策略都覆盖了。"""
        a_stock_strategies = set(MARKET_STRATEGY_CELLS[Market.A_STOCK].keys())
        assert a_stock_strategies == set(Strategy)

    def test_hk_two_strategies(self) -> None:
        """港股 2 个策略（红利 + 红利+低波）。"""
        hk_strategies = set(MARKET_STRATEGY_CELLS[Market.HK].keys())
        assert hk_strategies == {Strategy.DIVIDEND, Strategy.DIVIDEND_LOW_VOL}

    def test_a_h_one_strategy(self) -> None:
        """A+H 只 1 个策略（红利+低波 — 沪港深红利低波）。"""
        a_h_strategies = set(MARKET_STRATEGY_CELLS[Market.A_H].keys())
        assert a_h_strategies == {Strategy.DIVIDEND_LOW_VOL}

    def test_a_stock_indices_count(self) -> None:
        """A 股 13 只指数（红利 4 + 红利+低波 4 + 红利+价值 2 + FCF 2 + 高股息 1）。"""
        total = sum(
            len(cell["indices"])
            for cell in MARKET_STRATEGY_CELLS[Market.A_STOCK].values()
        )
        assert total == 13

    def test_hk_indices_count(self) -> None:
        """港股 2 只指数（港股红利 + 恒生红利低波动）。"""
        total = sum(
            len(cell["indices"])
            for cell in MARKET_STRATEGY_CELLS[Market.HK].values()
        )
        assert total == 2

    def test_a_h_indices_count(self) -> None:
        """A+H 1 只指数（沪港深红利低波）。"""
        total = sum(
            len(cell["indices"])
            for cell in MARKET_STRATEGY_CELLS[Market.A_H].values()
        )
        assert total == 1

    def test_total_indices_sixteen(self) -> None:
        """全部 16 只螺丝钉红利相关指数归类（5 宽基 + 3 非红利消费已排除）。"""
        assert len(all_indices()) == 16


class TestAStockDividendLowVol:
    """A 股·红利+低波 — 4 只基金 / 4 只指数（占 55%，超 35% 上限）。"""

    def test_indices(self) -> None:
        cell = get_cell(Market.A_STOCK, Strategy.DIVIDEND_LOW_VOL)
        assert cell is not None
        assert cell["indices"] == [
            "中证红利低波动",
            "红利低波100",
            "沪深300红利低波动",
            "标普中国A股大盘红利低波50",
        ]

    def test_fund_codes(self) -> None:
        cell = get_cell(Market.A_STOCK, Strategy.DIVIDEND_LOW_VOL)
        assert cell is not None
        assert cell["fund_codes"] == ["008163", "005561", "007605", "008114"]


class TestAStockFreeCashFlow:
    """A 股·自由现金流 — 2 只基金（023917 + 025958）。"""

    def test_indices(self) -> None:
        cell = get_cell(Market.A_STOCK, Strategy.FREE_CASH_FLOW)
        assert cell is not None
        assert "自由现金流" in cell["indices"]

    def test_fund_codes(self) -> None:
        cell = get_cell(Market.A_STOCK, Strategy.FREE_CASH_FLOW)
        assert cell is not None
        assert cell["fund_codes"] == ["023917", "025958"]


class TestAHStock:
    """A+H·红利+低波 — 1 只基金 / 1 只指数。"""

    def test_indices(self) -> None:
        cell = get_cell(Market.A_H, Strategy.DIVIDEND_LOW_VOL)
        assert cell is not None
        assert cell["indices"] == ["沪港深红利低波"]

    def test_fund_codes(self) -> None:
        cell = get_cell(Market.A_H, Strategy.DIVIDEND_LOW_VOL)
        assert cell is not None
        assert cell["fund_codes"] == ["007751"]


class TestHKDividend:
    """港股·红利 — 1 只基金（004098）。"""

    def test_indices(self) -> None:
        cell = get_cell(Market.HK, Strategy.DIVIDEND)
        assert cell is not None
        assert cell["indices"] == ["港股红利"]

    def test_fund_codes(self) -> None:
        cell = get_cell(Market.HK, Strategy.DIVIDEND)
        assert cell is not None
        assert cell["fund_codes"] == ["004098"]


class TestActiveFundCodes:
    def test_only_022164(self) -> None:
        """11 只里只有 022164 主动。"""
        assert ACTIVE_FUND_CODES == frozenset({"022164"})


class TestAllFundCodes:
    def test_ten_passive_funds(self) -> None:
        """10 只被动基金（022164 主动不在矩阵里）。"""
        codes = all_fund_codes()
        assert len(codes) == 10
        assert "022164" not in codes


class TestGetCellByFund:
    def test_005561_to_a_stock_low_vol(self) -> None:
        cell = get_cell_by_fund("005561")
        assert cell == (Market.A_STOCK, Strategy.DIVIDEND_LOW_VOL)

    def test_023917_to_a_stock_fcf(self) -> None:
        cell = get_cell_by_fund("023917")
        assert cell == (Market.A_STOCK, Strategy.FREE_CASH_FLOW)

    def test_007751_to_a_h_low_vol(self) -> None:
        cell = get_cell_by_fund("007751")
        assert cell == (Market.A_H, Strategy.DIVIDEND_LOW_VOL)

    def test_004098_to_hk_dividend(self) -> None:
        cell = get_cell_by_fund("004098")
        assert cell == (Market.HK, Strategy.DIVIDEND)

    def test_021457_to_hk_low_vol(self) -> None:
        cell = get_cell_by_fund("021457")
        assert cell == (Market.HK, Strategy.DIVIDEND_LOW_VOL)

    def test_025682_to_a_stock_high_div(self) -> None:
        cell = get_cell_by_fund("025682")
        assert cell == (Market.A_STOCK, Strategy.HIGH_DIVIDEND)

    def test_active_fund_returns_none(self) -> None:
        """022164 主动基金不在矩阵。"""
        assert get_cell_by_fund("022164") is None

    def test_unknown_fund_returns_none(self) -> None:
        assert get_cell_by_fund("999999") is None
        assert get_cell_by_fund("") is None


class TestGetCellByIndex:
    def test_zhongzheng_hongli_low_vol_to_a_stock(self) -> None:
        cell = get_cell_by_index("中证红利低波动")
        assert cell == (Market.A_STOCK, Strategy.DIVIDEND_LOW_VOL)

    def test_hugangshen_to_a_h(self) -> None:
        cell = get_cell_by_index("沪港深红利低波")
        assert cell == (Market.A_H, Strategy.DIVIDEND_LOW_VOL)

    def test_hengsheng_low_vol_to_hk(self) -> None:
        cell = get_cell_by_index("恒生红利低波动")
        assert cell == (Market.HK, Strategy.DIVIDEND_LOW_VOL)

    def test_unknown_index_returns_none(self) -> None:
        assert get_cell_by_index("未知指数") is None
        assert get_cell_by_index("") is None


class TestIsActiveFund:
    def test_true_for_022164(self) -> None:
        assert is_active_fund("022164") is True

    def test_false_for_passive(self) -> None:
        assert is_active_fund("008163") is False
        assert is_active_fund("023917") is False
        assert is_active_fund("025682") is False

    def test_false_for_unknown(self) -> None:
        assert is_active_fund("999999") is False


class TestGetCellNone:
    def test_nonexistent_cell_returns_none(self) -> None:
        """A 股·红利+价值 有定义但应该可以查到；A+H·红利（无）应该 None。"""
        assert get_cell(Market.A_H, Strategy.DIVIDEND) is None
        assert get_cell(Market.HK, Strategy.HIGH_DIVIDEND) is None
        assert get_cell(Market.A_H, Strategy.FREE_CASH_FLOW) is None


class TestSeparationFromGlobalAllocation:
    """关键设计点：红利策略 matrix 跟大类资产 SUBCLASS_BY_CODE 完全独立。"""

    def test_no_global_subclass_codes_in_matrix(self) -> None:
        """矩阵里所有基金都不在大类资产 SUBCLASS_BY_CODE 里。"""
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE

        for fund_code in all_fund_codes():
            assert fund_code not in SUBCLASS_BY_CODE, fund_code

    def test_no_global_subclass_codes_active(self) -> None:
        """022164 主动基金也不在大类资产 SUBCLASS_BY_CODE 里。"""
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE

        for fund_code in ACTIVE_FUND_CODES:
            assert fund_code not in SUBCLASS_BY_CODE, fund_code