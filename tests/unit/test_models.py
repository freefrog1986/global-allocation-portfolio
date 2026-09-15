"""测试 src/global_allocation/models.py。

参照 specs/010-data-models.md。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from global_allocation.models import (
    Asset,
    AssetClass,
    BacktestResult,
    Currency,
    DataSource,
    PerformanceMetrics,
    PortfolioSnapshot,
    RebalanceEvent,
    RebalanceRule,
    Region,
    Strategy,
    TargetWeight,
    Trade,
)

# ────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def vti() -> Asset:
    return Asset(
        symbol="VTI",
        name="Vanguard Total Stock Market ETF",
        asset_class=AssetClass.EQUITY,
        region=Region.US,
        currency=Currency.USD,
        data_source=DataSource.YFINANCE,
    )


@pytest.fixture
def bnd(vti: Asset) -> Asset:
    return Asset(
        symbol="BND",
        name="Vanguard Total Bond Market ETF",
        asset_class=AssetClass.BOND,
        region=Region.US,
        currency=Currency.USD,
        data_source=DataSource.YFINANCE,
    )


@pytest.fixture
def gld() -> Asset:
    return Asset(
        symbol="GLD",
        name="SPDR Gold Shares",
        asset_class=AssetClass.COMMODITY,
        region=Region.GLOBAL,
        currency=Currency.USD,
        data_source=DataSource.YFINANCE,
    )


@pytest.fixture
def rebalance_yearly() -> RebalanceRule:
    return RebalanceRule(frequency="yearly")


# ────────────────────────────────────────────────────────────────────
# Asset
# ────────────────────────────────────────────────────────────────────


class TestAsset:
    def test_basic_construction(self, vti: Asset) -> None:
        # Arrange / Act
        # Assert
        assert vti.symbol == "VTI"
        assert vti.asset_class == AssetClass.EQUITY
        assert vti.region == Region.US
        assert vti.currency == Currency.USD
        assert vti.data_source == DataSource.YFINANCE

    def test_is_immutable(self, vti: Asset) -> None:
        # Pydantic BaseModel 默认 mutable；要确保我们用 frozen=True
        with pytest.raises(ValidationError):
            vti.symbol = "VOO"  # type: ignore[misc]

    def test_missing_required_field_fails(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValidationError) as exc_info:
            Asset(symbol="X", name="X")  # type: ignore[call-arg]
        assert "asset_class" in str(exc_info.value)

    def test_equality_by_symbol(self, vti: Asset) -> None:
        # Arrange
        vti2 = Asset(
            symbol="VTI",
            name="Different Name",
            asset_class=AssetClass.EQUITY,
            region=Region.US,
            currency=Currency.USD,
            data_source=DataSource.YFINANCE,
        )
        # Assert
        assert vti == vti2  # same symbol means same asset


# ────────────────────────────────────────────────────────────────────
# TargetWeight + RebalanceRule
# ────────────────────────────────────────────────────────────────────


class TestTargetWeight:
    def test_weight_is_decimal(self, vti: Asset) -> None:
        # Arrange
        tw = TargetWeight(asset=vti, weight=Decimal("0.60"))
        # Assert
        assert isinstance(tw.weight, Decimal)

    def test_accepts_float_via_coercion(self, vti: Asset) -> None:
        # Arrange
        tw = TargetWeight(asset=vti, weight=0.60)  # type: ignore[arg-type]
        # Assert
        assert tw.weight == Decimal("0.6")

    def test_weight_out_of_range_fails(self, vti: Asset) -> None:
        with pytest.raises(ValidationError):
            TargetWeight(asset=vti, weight=Decimal("1.5"))
        with pytest.raises(ValidationError):
            TargetWeight(asset=vti, weight=Decimal("-0.1"))


class TestRebalanceRule:
    def test_default_frequency(self) -> None:
        r = RebalanceRule(frequency="monthly")
        assert r.frequency == "monthly"
        assert r.threshold is None

    def test_threshold_must_be_in_unit_interval(self) -> None:
        with pytest.raises(ValidationError):
            RebalanceRule(frequency="yearly", threshold=Decimal("0"))
        with pytest.raises(ValidationError):
            RebalanceRule(frequency="yearly", threshold=Decimal("1.5"))

    def test_threshold_at_boundaries_fails(self) -> None:
        # 0 和 1 都不允许（开区间）
        with pytest.raises(ValidationError):
            RebalanceRule(frequency="yearly", threshold=Decimal("0.0"))
        with pytest.raises(ValidationError):
            RebalanceRule(frequency="yearly", threshold=Decimal("1.0"))

    def test_threshold_in_open_interval_ok(self) -> None:
        r = RebalanceRule(frequency="yearly", threshold=Decimal("0.05"))
        assert r.threshold == Decimal("0.05")


# ────────────────────────────────────────────────────────────────────
# Strategy
# ────────────────────────────────────────────────────────────────────


class TestStrategy:
    def test_basic_construction(
        self,
        vti: Asset,
        bnd: Asset,
        rebalance_yearly: RebalanceRule,
    ) -> None:
        # Arrange
        strategy = Strategy(
            id="60_40",
            name="60/40",
            description="Classic 60/40",
            target_weights=[
                TargetWeight(asset=vti, weight=Decimal("0.6")),
                TargetWeight(asset=bnd, weight=Decimal("0.4")),
            ],
            rebalance=rebalance_yearly,
            base_currency=Currency.USD,
            inception=date(2007, 9, 26),
        )
        # Assert
        assert strategy.id == "60_40"
        assert len(strategy.target_weights) == 2
        assert strategy.base_currency == Currency.USD

    def test_immutable(self, vti: Asset, bnd: Asset, rebalance_yearly: RebalanceRule) -> None:
        strategy = Strategy(
            id="60_40",
            name="60/40",
            description="Classic 60/40",
            target_weights=[
                TargetWeight(asset=vti, weight=Decimal("0.6")),
                TargetWeight(asset=bnd, weight=Decimal("0.4")),
            ],
            rebalance=rebalance_yearly,
            base_currency=Currency.USD,
            inception=date(2020, 1, 1),
        )
        with pytest.raises(ValidationError):
            strategy.id = "80_20"  # type: ignore[misc]

    def test_missing_target_weights_fails(self, rebalance_yearly: RebalanceRule) -> None:
        with pytest.raises(ValidationError):
            Strategy(
                id="empty",
                name="Empty",
                description="",
                target_weights=[],
                rebalance=rebalance_yearly,
                base_currency=Currency.USD,
                inception=date(2020, 1, 1),
            )


# ────────────────────────────────────────────────────────────────────
# Trade / RebalanceEvent / PortfolioSnapshot / BacktestResult
# ────────────────────────────────────────────────────────────────────


class TestTrade:
    def test_basic(self) -> None:
        # Arrange / Act
        t = Trade(symbol="VTI", side="buy", shares=Decimal("10"), price=Decimal("200"), fee=Decimal("1"))
        # Assert
        assert t.side == "buy"
        assert t.shares == Decimal("10")

    def test_invalid_side_fails(self) -> None:
        with pytest.raises(ValidationError):
            Trade(symbol="VTI", side="hold", shares=Decimal("10"), price=Decimal("200"), fee=Decimal("0"))


class TestRebalanceEvent:
    def test_basic(self) -> None:
        # Arrange
        trade = Trade(symbol="VTI", side="buy", shares=Decimal("1"), price=Decimal("200"), fee=Decimal("0"))
        event = RebalanceEvent(
            date=date(2020, 1, 1),
            triggered_by="schedule",
            trades=[trade],
            cost_bps=Decimal("10"),
        )
        # Assert
        assert event.triggered_by == "schedule"
        assert len(event.trades) == 1


class TestPortfolioSnapshot:
    def test_basic(self) -> None:
        snap = PortfolioSnapshot(
            date=date(2020, 1, 1),
            total_value=Decimal("100000"),
            positions={"VTI": Decimal("60000"), "BND": Decimal("40000")},
            weights={"VTI": Decimal("0.6"), "BND": Decimal("0.4")},
            cash=Decimal("0"),
        )
        assert snap.total_value == Decimal("100000")

    def test_default_cash_is_zero(self) -> None:
        snap = PortfolioSnapshot(
            date=date(2020, 1, 1),
            total_value=Decimal("100"),
            positions={},
            weights={},
        )
        assert snap.cash == Decimal("0")


class TestPerformanceMetrics:
    def test_basic(self) -> None:
        # Arrange / Act
        m = PerformanceMetrics(
            cagr=Decimal("0.08"),
            sharpe=Decimal("0.85"),
            max_drawdown=Decimal("-0.22"),
            volatility=Decimal("0.12"),
            total_return=Decimal("0.49"),
            annual_return=Decimal("0.083"),
            correlation=None,  # type: ignore[arg-type]
            best_day=Decimal("0.05"),
            worst_day=Decimal("-0.07"),
            win_rate=Decimal("0.55"),
        )
        # Assert
        assert m.cagr == Decimal("0.08")
        assert m.max_drawdown < 0


class TestBacktestResult:
    def _make_metrics(self) -> PerformanceMetrics:
        return PerformanceMetrics(
            cagr=Decimal("0.08"),
            sharpe=Decimal("0.85"),
            max_drawdown=Decimal("-0.22"),
            volatility=Decimal("0.12"),
            total_return=Decimal("0.49"),
            annual_return=Decimal("0.083"),
            correlation=None,
            best_day=Decimal("0.05"),
            worst_day=Decimal("-0.07"),
            win_rate=Decimal("0.55"),
        )

    def test_basic(self) -> None:
        # Arrange / Act
        result = BacktestResult(
            strategy_id="60_40",
            start_date=date(2019, 1, 1),
            end_date=date(2024, 1, 1),
            initial_capital=Decimal("100000"),
            final_value=Decimal("148000"),
            equity_curve=None,  # type: ignore[arg-type]
            snapshots=[],
            metrics=self._make_metrics(),
            rebalance_events=[],
        )
        # Assert
        assert result.strategy_id == "60_40"
        assert result.final_value > result.initial_capital

    def test_end_before_start_fails(self) -> None:
        with pytest.raises(ValidationError):
            BacktestResult(
                strategy_id="bad",
                start_date=date(2024, 1, 1),
                end_date=date(2019, 1, 1),
                initial_capital=Decimal("100000"),
                final_value=Decimal("100000"),
                equity_curve=None,  # type: ignore[arg-type]
                snapshots=[],
                metrics=self._make_metrics(),
                rebalance_events=[],
            )

    def test_zero_initial_capital_fails(self) -> None:
        with pytest.raises(ValidationError):
            BacktestResult(
                strategy_id="zero",
                start_date=date(2019, 1, 1),
                end_date=date(2024, 1, 1),
                initial_capital=Decimal("0"),
                final_value=Decimal("0"),
                equity_curve=None,  # type: ignore[arg-type]
                snapshots=[],
                metrics=self._make_metrics(),
                rebalance_events=[],
            )
