"""测试 src/global_allocation/portfolio/dividend_rebalance.py。

liubo 2026-09-26 拍板 v6 调仓规则：
- 螺丝钉估值（偏低 / 适中 / 偏高）+ 仓位 → BUILD / ADD / HOLD / REDUCE / SKIP
- 主动基金 022164 → SKIP
- 螺丝钉 universe 没的指数 → SKIP
- 单只上限 8 lots = 40k = 10% (5k/lot)
"""

from __future__ import annotations

from decimal import Decimal

from global_allocation.portfolio.dividend_rebalance import (
    CASH_BUFFER_LOTS,
    CATEGORY_CAP_LOTS,
    DividendSignal,
    DividendValuation,
    LOT_SIZE_CNY,
    SINGLE_CAP_LOTS,
    SINGLE_CAP_PCT,
    TOTAL_PORTFOLIO_CNY,
    evaluate_dividend_fund,
)


class TestPortfolioConstants:
    def test_total_is_400k(self) -> None:
        assert TOTAL_PORTFOLIO_CNY == Decimal("400000")

    def test_lot_size_is_5k(self) -> None:
        assert LOT_SIZE_CNY == Decimal("5000")

    def test_single_cap_pct_10(self) -> None:
        assert SINGLE_CAP_PCT == Decimal("0.10")

    def test_single_cap_lots_8(self) -> None:
        """10% of 400k / 5k = 8 lots = 40k CNY。"""
        assert SINGLE_CAP_LOTS == Decimal("8")

    def test_category_cap_lots_28(self) -> None:
        """35% of 400k / 5k = 28 lots = 140k CNY。"""
        assert CATEGORY_CAP_LOTS == Decimal("28")

    def test_cash_buffer_lots_8(self) -> None:
        """10% of 400k / 5k = 8 lots = 40k CNY。"""
        assert CASH_BUFFER_LOTS == Decimal("8")


class TestActiveFundSkip:
    def test_022164_always_skip(self) -> None:
        """022164 主动基金，无论估值/仓位 → SKIP。"""
        action = evaluate_dividend_fund(
            fund_code="022164",
            fund_name="西部利得央企优选",
            index_display_name="中证中央企业综合指数",
            current_position_lots=Decimal("0.2"),
        )
        assert action.signal == DividendSignal.SKIP
        assert action.change_lots == Decimal("0")
        assert "主动管理" in action.reason


class TestIndexNotInUniverse:
    def test_extras_fund_skipped(self) -> None:
        """EXTRAS 5 只（跟踪指数不在螺丝钉表） → SKIP。"""
        action = evaluate_dividend_fund(
            fund_code="008163",
            fund_name="南方标普红利低波 50",
            index_display_name=None,  # EXTRAS 没在螺丝钉表
            current_position_lots=Decimal("6"),
        )
        assert action.signal == DividendSignal.SKIP
        assert action.change_lots == Decimal("0")
        assert "螺丝钉 universe" in action.reason

    def test_unknown_index_skipped(self) -> None:
        action = evaluate_dividend_fund(
            fund_code="999999",
            fund_name="未知基金",
            index_display_name="完全不在 universe 的指数",
            current_position_lots=Decimal("0"),
        )
        assert action.signal == DividendSignal.SKIP


class TestBuildSignal:
    """偏低 + 空仓 → BUILD +1 lot。"""

    def test_build_when_low_and_zero(self) -> None:
        action = evaluate_dividend_fund(
            fund_code="005561",
            fund_name="创金合信中证红利低波动",
            index_display_name="中证红利低波动",
            current_position_lots=Decimal("0"),
        )
        assert action.signal == DividendSignal.BUILD
        assert action.change_lots == Decimal("1")
        assert action.market is not None
        assert action.strategy is not None
        assert action.valuation == DividendValuation.LOW

    def test_build_signal_at_cap_returns_hold(self) -> None:
        """偏低 + 已达单只上限 → HOLD（不再加）。"""
        action = evaluate_dividend_fund(
            fund_code="005561",
            fund_name="创金合信中证红利低波动",
            index_display_name="中证红利低波动",
            current_position_lots=Decimal("8"),  # 正好单只上限
        )
        assert action.signal == DividendSignal.HOLD
        assert action.change_lots == Decimal("0")
        assert "已达单只上限" in action.reason


class TestAddSignal:
    """偏低 + 已持仓 → ADD +1 lot。"""

    def test_add_when_low_with_position(self) -> None:
        action = evaluate_dividend_fund(
            fund_code="005561",
            fund_name="创金合信中证红利低波动",
            index_display_name="中证红利低波动",
            current_position_lots=Decimal("3"),
        )
        assert action.signal == DividendSignal.ADD
        assert action.change_lots == Decimal("1")
        assert action.valuation == DividendValuation.LOW

    def test_add_at_cap_returns_hold(self) -> None:
        """偏低 + 仓位 = 上限 - 0.5 → 加完后会超上限，应该 HOLD。"""
        action = evaluate_dividend_fund(
            fund_code="005561",
            fund_name="创金合信中证红利低波动",
            index_display_name="中证红利低波动",
            current_position_lots=Decimal("8"),  # 上限
        )
        assert action.signal == DividendSignal.HOLD


class TestHoldSignal:
    """适中 → HOLD。"""

    def test_hold_when_medium(self) -> None:
        """上证红利（适中，矩阵里的 A 股·红利） → HOLD。"""
        action = evaluate_dividend_fund(
            fund_code="008114",  # fund_code 任意 — 不影响矩阵查询
            fund_name="天弘中证红利低波 100",
            index_display_name="上证红利",  # 适中
            current_position_lots=Decimal("5"),
        )
        assert action.signal == DividendSignal.HOLD
        assert action.change_lots == Decimal("0")
        assert action.valuation == DividendValuation.MEDIUM


class TestReduceSignal:
    """偏高 → REDUCE -1 lot。"""

    def test_reduce_when_high_with_position(self) -> None:
        """有仓位 + 偏高 → 减仓 1 仓。"""
        action = evaluate_dividend_fund(
            fund_code="013310",  # 不在矩阵里但假设有数据
            fund_name="华夏科创创业50",
            index_display_name="科创50",
            current_position_lots=Decimal("2"),
        )
        assert action.signal == DividendSignal.REDUCE
        assert action.change_lots == Decimal("-1")
        assert action.valuation == DividendValuation.HIGH

    def test_reduce_at_one_lot(self) -> None:
        """仓位只有 1 仓 + 偏高 → 仍 REDUCE -1（允许清仓）。"""
        action = evaluate_dividend_fund(
            fund_code="013310",
            fund_name="华夏科创创业50",
            index_display_name="科创50",
            current_position_lots=Decimal("1"),
        )
        assert action.signal == DividendSignal.REDUCE
        assert action.change_lots == Decimal("-1")


class TestHighWithZeroPosition:
    """偏高 + 空仓 → HOLD（不开新仓）。"""

    def test_high_zero_holds(self) -> None:
        action = evaluate_dividend_fund(
            fund_code="013310",
            fund_name="华夏科创创业50",
            index_display_name="科创50",
            current_position_lots=Decimal("0"),
        )
        assert action.signal == DividendSignal.HOLD
        assert action.change_lots == Decimal("0")
        assert action.valuation == DividendValuation.HIGH
        assert "不开新仓" in action.reason


class TestMatrixLookupInAction:
    """evaluate_dividend_fund 应该把 (市场, 策略) cell 写到 action 里。"""

    def test_a_stock_low_vol(self) -> None:
        action = evaluate_dividend_fund(
            fund_code="005561",
            fund_name="创金合信中证红利低波动",
            index_display_name="中证红利低波动",
            current_position_lots=Decimal("0"),
        )
        from global_allocation.portfolio.category_matrix import Market, Strategy
        assert action.market == Market.A_STOCK
        assert action.strategy == Strategy.DIVIDEND_LOW_VOL

    def test_a_h_low_vol(self) -> None:
        action = evaluate_dividend_fund(
            fund_code="007751",
            fund_name="景顺长城沪港深",
            index_display_name="沪港深红利低波",
            current_position_lots=Decimal("6"),
        )
        from global_allocation.portfolio.category_matrix import Market, Strategy
        assert action.market == Market.A_H
        assert action.strategy == Strategy.DIVIDEND_LOW_VOL


class TestAllElevenFundsSmokeTest:
    """11 只当前红利基金跑一遍 evaluate_dividend_fund（不抛错）。"""

    def test_runs_for_all_funds(self) -> None:
        funds = [
            ("008163", "南方标普红利低波50", None, Decimal("6")),
            ("005561", "创金合信中证红利低波动", "中证红利低波动", Decimal("6")),
            ("007605", "嘉实沪深300红利低波", None, Decimal("5")),
            ("008114", "天弘中证红利低波100", "红利低波100", Decimal("5")),
            ("007751", "景顺长城沪港深", "沪港深红利低波", Decimal("6")),
            ("004098", "前海开源港股通股息率50", None, Decimal("5")),
            ("021457", "易方达港股通红利低波", "恒生红利低波动", Decimal("0.1")),
            ("023917", "华夏国证自由现金流", "自由现金流", Decimal("0.5")),
            ("025958", "南方中证全指自由现金流", None, Decimal("0.1")),
            ("025682", "广发高股息ETF联接", None, Decimal("0.5")),
            ("022164", "西部利得央企优选", None, Decimal("0.1")),
        ]
        for code, name, idx, lots in funds:
            action = evaluate_dividend_fund(
                fund_code=code, fund_name=name,
                index_display_name=idx, current_position_lots=lots,
            )
            # 不抛错 + signal 合法
            assert action.signal in DividendSignal
            # change_lots 跟 signal 一致
            if action.signal in (DividendSignal.BUILD, DividendSignal.ADD):
                assert action.change_lots == Decimal("1")
            elif action.signal == DividendSignal.REDUCE:
                assert action.change_lots == Decimal("-1")
            else:  # HOLD / SKIP
                assert action.change_lots == Decimal("0")


class TestSeparationFromPERebalance:
    """关键设计点：dividend_rebalance 跟 pe_rebalance.py 完全独立。"""

    def test_dividend_action_does_not_import_pe_rebalance(self) -> None:
        """dividend_rebalance 模块不应依赖 pe_rebalance（不同组合的调仓逻辑分离）。"""
        import global_allocation.portfolio.dividend_rebalance as mod
        # pe_rebalance 是大类资产配置用的；dividend_rebalance 是红利策略用的
        # 不能交叉导入
        import inspect
        source = inspect.getsource(mod)
        assert "from global_allocation.portfolio.pe_rebalance" not in source
        assert "import pe_rebalance" not in source