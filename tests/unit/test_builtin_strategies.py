"""测试 src/global_allocation/strategies/builtin.py。

参照 specs/030-built-in-strategies.md。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from global_allocation.models import (
    AssetClass,
    Currency,
    DataSource,
    Region,
)
from global_allocation.strategies.base import StrategyBase
from global_allocation.strategies.builtin import (
    AllWeather,
    PermanentPortfolio,
    RiskParity,
    SixtyForty,
)


# ────────────────────────────────────────────────────────────────────
# SixtyForty
# ────────────────────────────────────────────────────────────────────


class TestSixtyForty:
    def test_id(self) -> None:
        s = SixtyForty()
        assert s.id == "60_40"

    def test_build_returns_strategy(self) -> None:
        s = SixtyForty()
        strat = s.build()
        assert strat.id == "60_40"
        assert len(strat.target_weights) == 2

    def test_weight_sum_is_one(self) -> None:
        s = SixtyForty()
        # Should not raise
        s.validate()

    def test_targets_are_equity_and_bond(self) -> None:
        s = SixtyForty()
        strat = s.build()
        classes = {tw.asset.asset_class for tw in strat.target_weights}
        assert AssetClass.EQUITY in classes
        assert AssetClass.BOND in classes

    def test_specific_symbols(self) -> None:
        s = SixtyForty()
        strat = s.build()
        symbols = {tw.asset.symbol for tw in strat.target_weights}
        assert "VT" in symbols
        assert "BND" in symbols

    def test_rebalance_frequency(self) -> None:
        s = SixtyForty()
        assert s.rebalance.frequency == "yearly"


# ────────────────────────────────────────────────────────────────────
# PermanentPortfolio
# ────────────────────────────────────────────────────────────────────


class TestPermanentPortfolio:
    def test_id(self) -> None:
        s = PermanentPortfolio()
        assert s.id == "permanent_portfolio"

    def test_four_targets(self) -> None:
        s = PermanentPortfolio()
        strat = s.build()
        assert len(strat.target_weights) == 4

    def test_each_weight_is_quarter(self) -> None:
        s = PermanentPortfolio()
        for tw in s.build().target_weights:
            assert tw.weight == Decimal("0.25")

    def test_validates(self) -> None:
        PermanentPortfolio().validate()

    def test_includes_gold(self) -> None:
        s = PermanentPortfolio()
        symbols = {tw.asset.symbol for tw in s.build().target_weights}
        assert "GLD" in symbols


# ────────────────────────────────────────────────────────────────────
# AllWeather
# ────────────────────────────────────────────────────────────────────


class TestAllWeather:
    def test_id(self) -> None:
        s = AllWeather()
        assert s.id == "all_weather"

    def test_validates(self) -> None:
        AllWeather().validate()

    def test_five_assets(self) -> None:
        s = AllWeather()
        strat = s.build()
        assert len(strat.target_weights) == 5

    def test_includes_tlt_and_gld(self) -> None:
        s = AllWeather()
        symbols = {tw.asset.symbol for tw in s.build().target_weights}
        assert "TLT" in symbols
        assert "GLD" in symbols


# ────────────────────────────────────────────────────────────────────
# RiskParity
# ────────────────────────────────────────────────────────────────────


class TestRiskParity:
    def test_id(self) -> None:
        s = RiskParity()
        assert s.id == "risk_parity"

    def test_build_returns_equal_weight_fallback(self) -> None:
        s = RiskParity()
        strat = s.build()
        n = len(strat.target_weights)
        for tw in strat.target_weights:
            assert tw.weight == pytest.approx(Decimal("1") / Decimal(n), abs=Decimal("0.01"))

    def test_validates(self) -> None:
        RiskParity().validate()

    def test_weights_at_fallback_to_build(self) -> None:
        s = RiskParity()
        # 不传 prices，应 fallback 到 build() 的等权
        weights = s.weights_at(date(2024, 1, 1))
        strat = s.build()
        assert len(weights) == len(strat.target_weights)
        for w, tw in zip(weights, strat.target_weights, strict=True):
            assert w.asset == tw.asset

    def test_base_currency_is_usd(self) -> None:
        s = RiskParity()
        assert s.build().base_currency == Currency.USD


# ────────────────────────────────────────────────────────────────────
# All built-in strategies share contract
# ────────────────────────────────────────────────────────────────────


class TestBuiltInCommonContract:
    @pytest.mark.parametrize(
        "cls",
        [SixtyForty, PermanentPortfolio, AllWeather, RiskParity],
    )
    def test_inherits_strategy_base(self, cls: type[StrategyBase]) -> None:
        assert issubclass(cls, StrategyBase)

    @pytest.mark.parametrize(
        "cls",
        [SixtyForty, PermanentPortfolio, AllWeather, RiskParity],
    )
    def test_validate_passes(self, cls: type[StrategyBase]) -> None:
        cls().validate()

    @pytest.mark.parametrize(
        "cls",
        [SixtyForty, PermanentPortfolio, AllWeather, RiskParity],
    )
    def test_inception_is_date(self, cls: type[StrategyBase]) -> None:
        strat = cls().build()
        assert isinstance(strat.inception, date)

    @pytest.mark.parametrize(
        "cls",
        [SixtyForty, PermanentPortfolio, AllWeather, RiskParity],
    )
    def test_base_currency_in_allowed_set(
        self, cls: type[StrategyBase]
    ) -> None:
        strat = cls().build()
        assert strat.base_currency in {Currency.USD, Currency.CNY, Currency.HKD}

    @pytest.mark.parametrize(
        "cls",
        [SixtyForty, PermanentPortfolio, AllWeather, RiskParity],
    )
    def test_data_source_is_set(self, cls: type[StrategyBase]) -> None:
        strat = cls().build()
        for tw in strat.target_weights:
            assert tw.asset.data_source in {DataSource.YFINANCE, DataSource.AKSHARE}
