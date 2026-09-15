"""测试 src/global_allocation/portfolio/models.py。

参照 specs/090-portfolio-journal.md。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from global_allocation.models import AssetClass, Currency, DataSource
from global_allocation.portfolio.models import (
    Fund,
    Holding,
    Transaction,
    TransactionSide,
    WeeklySnapshot,
)


class TestFund:
    def test_basic(self) -> None:
        f = Fund(code="163406", name="兴全合润", asset_class=AssetClass.MIXED)
        assert f.code == "163406"
        assert f.name == "兴全合润"
        assert f.asset_class == AssetClass.MIXED
        assert f.currency == Currency.CNY  # default
        assert f.data_source == DataSource.AKSHARE  # default for CN funds

    def test_explicit_currency(self) -> None:
        f = Fund(
            code="VT", name="Vanguard Total World", asset_class=AssetClass.EQUITY,
            currency=Currency.USD, data_source=DataSource.YFINANCE,
        )
        assert f.currency == Currency.USD
        assert f.data_source == DataSource.YFINANCE

    def test_code_required(self) -> None:
        with pytest.raises(ValidationError):
            Fund(name="x", asset_class=AssetClass.EQUITY)  # type: ignore[call-arg]

    def test_immutable(self) -> None:
        from dataclasses import FrozenInstanceError

        f = Fund(code="163406", name="x", asset_class=AssetClass.EQUITY)
        with pytest.raises((FrozenInstanceError, ValidationError)):
            f.name = "y"  # type: ignore[misc]


class TestTransaction:
    def test_buy(self) -> None:
        tx = Transaction(
            fund_code="163406",
            side=TransactionSide.BUY,
            date=date(2026, 9, 10),
            shares=Decimal("1000"),
            price=Decimal("2.350"),
            fee=Decimal("1.20"),
            strategy="月度定投扣款",
            tags=["dca", "monthly"],
        )
        assert tx.side == TransactionSide.BUY
        assert tx.fund_code == "163406"
        assert tx.shares == Decimal("1000")
        assert tx.fee == Decimal("1.20")
        assert tx.tags == ["dca", "monthly"]
        assert tx.strategy == "月度定投扣款"
        assert tx.note is None

    def test_sell(self) -> None:
        tx = Transaction(
            fund_code="163406",
            side=TransactionSide.SELL,
            date=date(2026, 9, 12),
            shares=Decimal("200"),
            price=Decimal("2.45"),
        )
        assert tx.side == TransactionSide.SELL

    def test_negative_shares_rejected(self) -> None:
        with pytest.raises(ValidationError, match="shares"):
            Transaction(
                fund_code="163406",
                side=TransactionSide.BUY,
                date=date(2026, 9, 10),
                shares=Decimal("-100"),
                price=Decimal("2.350"),
            )

    def test_negative_price_rejected(self) -> None:
        with pytest.raises(ValidationError, match="price"):
            Transaction(
                fund_code="163406",
                side=TransactionSide.BUY,
                date=date(2026, 9, 10),
                shares=Decimal("100"),
                price=Decimal("-2"),
            )

    def test_negative_fee_rejected(self) -> None:
        with pytest.raises(ValidationError, match="fee"):
            Transaction(
                fund_code="163406",
                side=TransactionSide.BUY,
                date=date(2026, 9, 10),
                shares=Decimal("100"),
                price=Decimal("2"),
                fee=Decimal("-1"),
            )

    def test_default_fee_zero(self) -> None:
        tx = Transaction(
            fund_code="163406",
            side=TransactionSide.BUY,
            date=date(2026, 9, 10),
            shares=Decimal("100"),
            price=Decimal("2"),
        )
        assert tx.fee == Decimal("0")

    def test_default_tags_empty(self) -> None:
        tx = Transaction(
            fund_code="163406",
            side=TransactionSide.BUY,
            date=date(2026, 9, 10),
            shares=Decimal("100"),
            price=Decimal("2"),
        )
        assert tx.tags == []


class TestHolding:
    def _make_fund(self) -> Fund:
        return Fund(code="163406", name="兴全合润", asset_class=AssetClass.MIXED)

    def test_with_market_price(self) -> None:
        h = Holding(
            fund=self._make_fund(),
            shares=Decimal("1000"),
            avg_cost=Decimal("2.30"),
            market_price=Decimal("2.50"),
        )
        assert h.market_value == Decimal("2500")
        assert h.cost_basis == Decimal("2300")
        assert h.unrealized_pnl == Decimal("200")
        # 200/2300 = 0.086956...
        assert abs(h.unrealized_pnl_pct - Decimal("0.08695652173913043478260869565")) < Decimal("1E-10")

    def test_without_market_price(self) -> None:
        h = Holding(
            fund=self._make_fund(),
            shares=Decimal("1000"),
            avg_cost=Decimal("2.30"),
            market_price=None,
        )
        assert h.market_value is None
        assert h.unrealized_pnl is None
        assert h.unrealized_pnl_pct is None


class TestWeeklySnapshot:
    def test_basic(self) -> None:
        holdings = [
            {"fund_code": "163406", "shares": "1000", "market_value": "2500", "weight": "0.5", "pnl_pct": "0.08"},
            {"fund_code": "510300", "shares": "500", "market_value": "2500", "weight": "0.5", "pnl_pct": "0.04"},
        ]
        snap = WeeklySnapshot(
            week_end_date=date(2026, 9, 11),
            total_value=Decimal("5000"),
            week_return=Decimal("0.012"),
            cumulative_return=Decimal("0.085"),
            holdings_json=json.dumps(holdings),
            created_at=datetime(2026, 9, 11, 17, 0, 0),
        )
        assert snap.total_value == Decimal("5000")
        assert snap.week_return == Decimal("0.012")
        assert snap.cumulative_return == Decimal("0.085")
        # holdings_json 可往返解析
        parsed = json.loads(snap.holdings_json)
        assert len(parsed) == 2
        assert parsed[0]["fund_code"] == "163406"

    def test_defaults(self) -> None:
        snap = WeeklySnapshot(
            week_end_date=date(2026, 9, 11),
            total_value=Decimal("5000"),
            holdings_json="[]",
        )
        assert snap.week_return == Decimal("0")
        assert snap.cumulative_return == Decimal("0")

    def test_negative_total_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WeeklySnapshot(
                week_end_date=date(2026, 9, 11),
                total_value=Decimal("-1"),
                holdings_json="[]",
            )


class TestTransactionSide:
    def test_values(self) -> None:
        assert TransactionSide.BUY.value == "buy"
        assert TransactionSide.SELL.value == "sell"
