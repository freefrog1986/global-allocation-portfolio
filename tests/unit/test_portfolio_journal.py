"""测试 src/global_allocation/portfolio/journal.py。

业务逻辑：add_fund / record_buy / record_sell / compute_holdings / take_snapshot / compute_report。
价格用 mock，不依赖网络。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from global_allocation.models import AssetClass
from global_allocation.portfolio.db import PortfolioDB
from global_allocation.portfolio.journal import PortfolioJournal
from global_allocation.portfolio.models import (
    TransactionSide,
)


class FakePriceSource:
    """固定价格的测试用价格源。"""

    def __init__(self, prices: dict[str, Decimal]) -> None:
        self._prices = prices

    def get_price(self, code: str, on: date) -> Decimal | None:
        return self._prices.get(code)


@pytest.fixture
def journal(tmp_path: Path) -> PortfolioJournal:
    db = PortfolioDB(path=tmp_path / "p.db")
    prices = FakePriceSource(
        {
            "163406": Decimal("2.50"),
            "510300": Decimal("4.00"),
        }
    )
    return PortfolioJournal(db=db, price_source=prices)


class TestFundManagement:
    def test_add_and_get_fund(self, journal: PortfolioJournal) -> None:
        fund = journal.add_fund(code="163406", name="兴全合润", asset_class=AssetClass.MIXED)
        assert fund.code == "163406"
        assert fund.asset_class == AssetClass.MIXED
        loaded = journal.get_fund("163406")
        assert loaded is not None
        assert loaded.name == "兴全合润"

    def test_list_funds(self, journal: PortfolioJournal) -> None:
        journal.add_fund("163406", "兴全合润", AssetClass.MIXED)
        journal.add_fund("510300", "沪深300", AssetClass.EQUITY)
        funds = journal.list_funds()
        assert len(funds) == 2


class TestRecordBuy:
    def test_first_buy(self, journal: PortfolioJournal) -> None:
        journal.add_fund("163406", "兴全合润", AssetClass.MIXED)
        tx = journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 10),
            shares=Decimal("1000"),
            price=Decimal("2.350"),
            fee=Decimal("1.20"),
            strategy="定投扣款",
            tags=["dca"],
        )
        assert tx.side == TransactionSide.BUY
        assert tx.fund_code == "163406"
        assert tx.shares == Decimal("1000")
        assert tx.fee == Decimal("1.20")

    def test_buy_unknown_fund_raises(self, journal: PortfolioJournal) -> None:
        with pytest.raises(ValueError, match="未登记"):
            journal.record_buy(
                fund_code="nope",
                trade_date=date(2026, 9, 10),
                shares=Decimal("100"),
                price=Decimal("2"),
            )

    def test_buy_negative_shares_rejected(self, journal: PortfolioJournal) -> None:
        journal.add_fund("163406", "x", AssetClass.MIXED)
        with pytest.raises(ValueError):
            journal.record_buy(
                fund_code="163406",
                trade_date=date(2026, 9, 10),
                shares=Decimal("-100"),
                price=Decimal("2"),
            )

    def test_buy_with_note(self, journal: PortfolioJournal) -> None:
        journal.add_fund("163406", "x", AssetClass.MIXED)
        tx = journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 10),
            shares=Decimal("100"),
            price=Decimal("2"),
            note="市场大跌加仓",
        )
        assert tx.note == "市场大跌加仓"


class TestRecordSell:
    def test_sell_partial(self, journal: PortfolioJournal) -> None:
        journal.add_fund("163406", "x", AssetClass.MIXED)
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 10),
            shares=Decimal("1000"),
            price=Decimal("2"),
        )
        tx = journal.record_sell(
            fund_code="163406",
            trade_date=date(2026, 9, 12),
            shares=Decimal("200"),
            price=Decimal("2.5"),
            strategy="止盈",
        )
        assert tx.side == TransactionSide.SELL

    def test_sell_more_than_held_raises(self, journal: PortfolioJournal) -> None:
        journal.add_fund("163406", "x", AssetClass.MIXED)
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 10),
            shares=Decimal("100"),
            price=Decimal("2"),
        )
        with pytest.raises(ValueError, match="不足"):
            journal.record_sell(
                fund_code="163406",
                trade_date=date(2026, 9, 12),
                shares=Decimal("500"),
                price=Decimal("2"),
            )

    def test_sell_unknown_fund_raises(self, journal: PortfolioJournal) -> None:
        with pytest.raises(ValueError, match="未登记"):
            journal.record_sell(
                fund_code="nope",
                trade_date=date(2026, 9, 10),
                shares=Decimal("100"),
                price=Decimal("2"),
            )


class TestComputeHoldings:
    def test_no_transactions_empty(self, journal: PortfolioJournal) -> None:
        assert journal.compute_holdings() == []

    def test_single_buy(self, journal: PortfolioJournal) -> None:
        journal.add_fund("163406", "兴全合润", AssetClass.MIXED)
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 10),
            shares=Decimal("1000"),
            price=Decimal("2.30"),
            fee=Decimal("1"),
        )
        holdings = journal.compute_holdings()
        assert len(holdings) == 1
        h = holdings[0]
        assert h.fund.code == "163406"
        assert h.shares == Decimal("1000")
        # avg_cost = (1000 * 2.30 + 1) / 1000
        assert abs(h.avg_cost - Decimal("2.301")) < Decimal("1E-10")
        assert h.market_price == Decimal("2.50")
        assert h.market_value == Decimal("2500")
        assert abs(h.cost_basis - Decimal("2301")) < Decimal("1E-10")
        # pnl = 2500 - 2301 = 199
        assert abs(h.unrealized_pnl - Decimal("199")) < Decimal("1E-10")

    def test_buy_sell_net(self, journal: PortfolioJournal) -> None:
        journal.add_fund("163406", "x", AssetClass.MIXED)
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 1),
            shares=Decimal("1000"),
            price=Decimal("2.00"),
        )
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 5),
            shares=Decimal("500"),
            price=Decimal("2.20"),
        )
        journal.record_sell(
            fund_code="163406",
            trade_date=date(2026, 9, 10),
            shares=Decimal("300"),
            price=Decimal("2.50"),
        )
        holdings = journal.compute_holdings()
        assert len(holdings) == 1
        # net shares = 1500 - 300 = 1200
        assert holdings[0].shares == Decimal("1200")

    def test_zero_shares_excluded(self, journal: PortfolioJournal) -> None:
        journal.add_fund("163406", "x", AssetClass.MIXED)
        journal.add_fund("510300", "y", AssetClass.EQUITY)
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 10),
            shares=Decimal("1000"),
            price=Decimal("2"),
        )
        holdings = journal.compute_holdings()
        # 510300 没交易，不在 holdings
        codes = {h.fund.code for h in holdings}
        assert codes == {"163406"}

    def test_fund_without_price_kept(self, tmp_path: Path) -> None:
        # 用空价格源，让所有基金都拿不到价格
        db = PortfolioDB(path=tmp_path / "p2.db")
        empty_prices = FakePriceSource({})
        j = PortfolioJournal(db=db, price_source=empty_prices)
        j.add_fund("163406", "x", AssetClass.MIXED)
        j.add_fund("510300", "y", AssetClass.EQUITY)
        j.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 10),
            shares=Decimal("1000"),
            price=Decimal("2"),
        )
        j.record_buy(
            fund_code="510300",
            trade_date=date(2026, 9, 10),
            shares=Decimal("500"),
            price=Decimal("3"),
        )
        holdings = {h.fund.code: h for h in j.compute_holdings()}
        assert holdings["163406"].market_price is None
        assert holdings["510300"].market_price is None
        # 但 cost_basis / shares 仍能算
        assert holdings["163406"].shares == Decimal("1000")
        assert holdings["163406"].market_value is None


class TestTakeSnapshot:
    def test_first_snapshot_no_prev(self, journal: PortfolioJournal) -> None:
        journal.add_fund("163406", "x", AssetClass.MIXED)
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 10),
            shares=Decimal("1000"),
            price=Decimal("2.30"),
        )
        snap = journal.take_snapshot(week_end_date=date(2026, 9, 11))
        assert snap.total_value == Decimal("2500")  # 1000 * 2.50
        # 第一次没有 prev
        assert snap.week_return == Decimal("0")
        assert snap.cumulative_return == Decimal("0")
        # holdings_json 可解析
        import json
        parsed = json.loads(snap.holdings_json)
        assert len(parsed) == 1
        assert parsed[0]["fund_code"] == "163406"

    def test_second_snapshot_with_return(self, journal: PortfolioJournal) -> None:
        journal.add_fund("163406", "x", AssetClass.MIXED)
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 5),
            shares=Decimal("1000"),
            price=Decimal("2.30"),
        )
        # 第一周（价格 2.50 → 总值 2500）
        s1 = journal.take_snapshot(week_end_date=date(2026, 9, 11))
        assert s1.total_value == Decimal("2500")
        assert s1.week_return == Decimal("0")
        # 价格 2.75 → 总值 2750 → 周涨 10%
        journal._price_source = FakePriceSource({"163406": Decimal("2.75")})
        s2 = journal.take_snapshot(week_end_date=date(2026, 9, 18))
        assert s2.total_value == Decimal("2750")
        # 2750 / 2500 - 1 = 0.10
        assert abs(s2.week_return - Decimal("0.10")) < Decimal("1E-10")
        # cumulative = 2750/2500 - 1 = 0.10
        assert abs(s2.cumulative_return - Decimal("0.10")) < Decimal("1E-10")

    def test_snapshot_idempotent(self, journal: PortfolioJournal) -> None:
        journal.add_fund("163406", "x", AssetClass.MIXED)
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 10),
            shares=Decimal("1000"),
            price=Decimal("2"),
        )
        s1 = journal.take_snapshot(week_end_date=date(2026, 9, 11))
        s2 = journal.take_snapshot(week_end_date=date(2026, 9, 11))  # 同周
        # UNIQUE 约束 → 第二次覆盖（不是新 id）
        # s1 是被覆盖的那条；s2 是新的 lastrowid 但 total_value 不变（因为价格不变）
        assert s1.total_value == s2.total_value
        snaps = journal._db.list_snapshots()
        # 只有一行（同 week_end_date）
        assert len([s for s in snaps if s.week_end_date == date(2026, 9, 11)]) == 1

    def test_snapshot_with_missing_price_raises(self, journal: PortfolioJournal) -> None:
        journal.add_fund("163406", "x", AssetClass.MIXED)
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 10),
            shares=Decimal("1000"),
            price=Decimal("2"),
        )
        journal._price_source = FakePriceSource({})  # 拉不到价格
        with pytest.raises(ValueError, match="价格"):
            journal.take_snapshot(week_end_date=date(2026, 9, 11))


class TestListTransactions:
    def test_filter_by_tag(self, journal: PortfolioJournal) -> None:
        journal.add_fund("163406", "x", AssetClass.MIXED)
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 1),
            shares=Decimal("1000"),
            price=Decimal("2"),
            tags=["dca"],
        )
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 15),
            shares=Decimal("500"),
            price=Decimal("2"),
            tags=["dip-buy"],
        )
        dca_txs = journal.list_transactions(tag="dca")
        assert len(dca_txs) == 1

    def test_filter_by_strategy(self, journal: PortfolioJournal) -> None:
        journal.add_fund("163406", "x", AssetClass.MIXED)
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 1),
            shares=Decimal("1000"),
            price=Decimal("2"),
            strategy="月度定投",
        )
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 15),
            shares=Decimal("500"),
            price=Decimal("2"),
            strategy="抄底",
        )
        txs = journal.list_transactions(strategy="抄底")
        assert len(txs) == 1
        assert txs[0].strategy == "抄底"


class TestWeightedAverageCost:
    def test_simple_weighted_avg(self, journal: PortfolioJournal) -> None:
        # 1000 @ 2.00 + 500 @ 2.40 = (2000 + 1200) / 1500 = 2.1333...
        journal.add_fund("163406", "x", AssetClass.MIXED)
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 1),
            shares=Decimal("1000"),
            price=Decimal("2.00"),
        )
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 5),
            shares=Decimal("500"),
            price=Decimal("2.40"),
        )
        holdings = journal.compute_holdings()
        h = holdings[0]
        # avg_cost = (1000*2 + 500*2.4) / 1500 = 3200/1500
        expected = Decimal("3200") / Decimal("1500")
        assert abs(h.avg_cost - expected) < Decimal("1E-10")
        # cost_basis = 1500 * avg_cost = 3200
        assert abs(h.cost_basis - Decimal("3200")) < Decimal("1E-10")

    def test_avg_cost_includes_fees(self, journal: PortfolioJournal) -> None:
        # 1000 @ 2.00 + fee 1.20 → 2001.20 / 1000 = 2.00120
        journal.add_fund("163406", "x", AssetClass.MIXED)
        journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 1),
            shares=Decimal("1000"),
            price=Decimal("2.00"),
            fee=Decimal("1.20"),
        )
        holdings = journal.compute_holdings()
        expected_avg = Decimal("2001.20") / Decimal("1000")
        assert abs(holdings[0].avg_cost - expected_avg) < Decimal("1E-10")


class TestImportHoldings:
    """从截图/快照导入当前持仓：每条 holdings 合成一笔 buy 交易。"""

    def test_import_creates_fund_and_buy(
        self, journal: PortfolioJournal
    ) -> None:
        journal.import_holdings(
            [
                {
                    "code": "163406",
                    "name": "兴全合润",
                    "asset_class": AssetClass.MIXED,
                    "current_value": "2500",
                    "cumulative_pnl": "0",
                }
            ]
        )
        fund = journal.get_fund("163406")
        assert fund is not None
        assert fund.name == "兴全合润"
        txs = journal.list_transactions(fund_code="163406")
        assert len(txs) == 1
        assert txs[0].side == TransactionSide.BUY

    def test_import_computes_shares_from_price(
        self, journal: PortfolioJournal
    ) -> None:
        # 163406 NAV = 2.50, current_value = 2500 → shares = 1000
        journal.import_holdings(
            [
                {
                    "code": "163406",
                    "name": "兴全合润",
                    "asset_class": AssetClass.MIXED,
                    "current_value": "2500",
                    "cumulative_pnl": "0",
                }
            ]
        )
        h = journal.compute_holdings()[0]
        assert h.shares == Decimal("1000")
        assert h.market_price == Decimal("2.50")
        assert h.market_value == Decimal("2500.00")

    def test_import_uses_today_date(self, journal: PortfolioJournal) -> None:
        journal.import_holdings(
            [
                {
                    "code": "163406",
                    "name": "x",
                    "asset_class": AssetClass.MIXED,
                    "current_value": "2500",
                    "cumulative_pnl": "0",
                }
            ]
        )
        txs = journal.list_transactions(fund_code="163406")
        assert txs[0].date == date.today()

    def test_import_strategy_and_tags_marked(
        self, journal: PortfolioJournal
    ) -> None:
        journal.import_holdings(
            [
                {
                    "code": "163406",
                    "name": "x",
                    "asset_class": AssetClass.MIXED,
                    "current_value": "2500",
                    "cumulative_pnl": "0",
                }
            ]
        )
        txs = journal.list_transactions(fund_code="163406")
        assert "import" in txs[0].tags
        assert "[imported]" in (txs[0].strategy or "")

    def test_import_skips_zero_value(self, journal: PortfolioJournal) -> None:
        # current_value=0 → 不该建持仓
        journal.import_holdings(
            [
                {
                    "code": "163406",
                    "name": "x",
                    "asset_class": AssetClass.MIXED,
                    "current_value": "0",
                    "cumulative_pnl": "0",
                }
            ]
        )
        assert journal.list_funds() == []
        assert journal.list_transactions() == []

    def test_import_skips_missing_price(self, journal: PortfolioJournal) -> None:
        # 999999 不在 mock 价格里 → 跳过
        journal.import_holdings(
            [
                {
                    "code": "999999",
                    "name": "未知基金",
                    "asset_class": AssetClass.EQUITY,
                    "current_value": "1000",
                    "cumulative_pnl": "0",
                }
            ]
        )
        assert journal.list_funds() == []

    def test_import_multiple_funds(self, journal: PortfolioJournal) -> None:
        journal.import_holdings(
            [
                {
                    "code": "163406",
                    "name": "兴全合润",
                    "asset_class": AssetClass.MIXED,
                    "current_value": "2500",
                    "cumulative_pnl": "0",
                },
                {
                    "code": "510300",
                    "name": "沪深300",
                    "asset_class": AssetClass.EQUITY,
                    "current_value": "2000",
                    "cumulative_pnl": "0",
                },
            ]
        )
        assert len(journal.list_funds()) == 2
        assert len(journal.list_transactions()) == 2

    def test_import_returns_count(self, journal: PortfolioJournal) -> None:
        n = journal.import_holdings(
            [
                {
                    "code": "163406",
                    "name": "x",
                    "asset_class": AssetClass.MIXED,
                    "current_value": "2500",
                    "cumulative_pnl": "0",
                },
                {
                    "code": "510300",
                    "name": "x",
                    "asset_class": AssetClass.EQUITY,
                    "current_value": "2000",
                    "cumulative_pnl": "0",
                },
            ]
        )
        assert n == 2

    def test_import_invalid_decimals_raise(
        self, journal: PortfolioJournal
    ) -> None:
        from decimal import InvalidOperation

        with pytest.raises((ValueError, InvalidOperation)):
            journal.import_holdings(
                [
                    {
                        "code": "163406",
                        "name": "x",
                        "asset_class": AssetClass.MIXED,
                        "current_value": "not-a-number",
                        "cumulative_pnl": "0",
                    }
                ]
            )
