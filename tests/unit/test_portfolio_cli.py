"""测试 src/global_allocation/portfolio/cli.py。

用 typer.testing.CliRunner + monkeypatch 隔离 PortfolioJournal。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from global_allocation.portfolio.cli import app
from global_allocation.portfolio.db import PortfolioDB
from global_allocation.portfolio.journal import PortfolioJournal
from global_allocation.portfolio.valuation import ManualPriceSource


@pytest.fixture
def fake_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PortfolioJournal:
    """把 PortfolioJournal 的 default_db / default_price 注入到 tmp_path + 固定价格。"""
    db_path = tmp_path / "portfolio.db"
    db = PortfolioDB(path=db_path)
    prices = ManualPriceSource({"163406": Decimal("2.50"), "510300": Decimal("4.00")})

    def fake_journal_factory() -> PortfolioJournal:
        return PortfolioJournal(db=db, price_source=prices)

    # 这两个函数在 cli 模块里被引用
    monkeypatch.setattr(
        "global_allocation.portfolio.cli._default_journal",
        fake_journal_factory,
    )
    return fake_journal_factory()


runner = CliRunner(mix_stderr=False)


class TestInit:
    def test_init_creates_db(self, fake_journal: PortfolioJournal, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["init", "--name", "我的组合"],
        )
        assert result.exit_code == 0, result.output
        # db file exists
        assert (tmp_path / "portfolio.db").exists()


class TestFundCommands:
    def test_add_fund(self, fake_journal: PortfolioJournal) -> None:
        result = runner.invoke(
            app,
            ["fund", "add", "163406", "--name", "兴全合润", "--asset-class", "mixed"],
        )
        assert result.exit_code == 0, result.output
        f = fake_journal.get_fund("163406")
        assert f is not None
        assert f.name == "兴全合润"

    def test_add_fund_invalid_asset_class(self, fake_journal: PortfolioJournal) -> None:
        result = runner.invoke(
            app,
            ["fund", "add", "X", "--name", "y", "--asset-class", "invalid"],
        )
        # exit_code != 0（typer 会处理）
        assert result.exit_code != 0

    def test_list_funds(self, fake_journal: PortfolioJournal) -> None:
        fake_journal.add_fund("163406", "兴全合润", asset_class=None) if False else fake_journal.add_fund(  # type: ignore[arg-type]
            "163406", "兴全合润", __import__("global_allocation.models", fromlist=["AssetClass"]).AssetClass.MIXED
        )
        result = runner.invoke(app, ["fund", "list"])
        assert result.exit_code == 0
        assert "163406" in result.stdout
        assert "兴全合润" in result.stdout


class TestBuy:
    def test_buy_unknown_fund(self, fake_journal: PortfolioJournal) -> None:
        result = runner.invoke(
            app,
            [
                "buy",
                "nope",
                "--date", "2026-09-10",
                "--shares", "1000",
                "--price", "2.350",
            ],
        )
        assert result.exit_code != 0

    def test_buy_success(self, fake_journal: PortfolioJournal) -> None:
        from global_allocation.models import AssetClass

        fake_journal.add_fund("163406", "兴全合润", AssetClass.MIXED)
        result = runner.invoke(
            app,
            [
                "buy", "163406",
                "--date", "2026-09-10",
                "--shares", "1000",
                "--price", "2.350",
                "--fee", "1.20",
                "--strategy", "定投扣款",
                "--tags", "dca",
            ],
        )
        assert result.exit_code == 0, result.output
        txs = fake_journal.list_transactions(fund_code="163406")
        assert len(txs) == 1
        assert txs[0].shares == Decimal("1000")

    def test_buy_invalid_date(self, fake_journal: PortfolioJournal) -> None:
        from global_allocation.models import AssetClass

        fake_journal.add_fund("163406", "x", AssetClass.MIXED)
        result = runner.invoke(
            app,
            [
                "buy", "163406",
                "--date", "not-a-date",
                "--shares", "100",
                "--price", "2",
            ],
        )
        assert result.exit_code != 0


class TestSell:
    def test_sell_more_than_held(self, fake_journal: PortfolioJournal) -> None:
        from global_allocation.models import AssetClass

        fake_journal.add_fund("163406", "x", AssetClass.MIXED)
        fake_journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 1),
            shares=Decimal("100"),
            price=Decimal("2"),
        )
        result = runner.invoke(
            app,
            [
                "sell", "163406",
                "--date", "2026-09-10",
                "--shares", "500",
                "--price", "2.5",
            ],
        )
        assert result.exit_code != 0

    def test_sell_success(self, fake_journal: PortfolioJournal) -> None:
        from global_allocation.models import AssetClass

        fake_journal.add_fund("163406", "x", AssetClass.MIXED)
        fake_journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 1),
            shares=Decimal("1000"),
            price=Decimal("2"),
        )
        result = runner.invoke(
            app,
            [
                "sell", "163406",
                "--date", "2026-09-10",
                "--shares", "200",
                "--price", "2.5",
                "--strategy", "止盈",
            ],
        )
        assert result.exit_code == 0, result.output


class TestTxList:
    def test_list_all(self, fake_journal: PortfolioJournal) -> None:
        from global_allocation.models import AssetClass

        fake_journal.add_fund("163406", "x", AssetClass.MIXED)
        fake_journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 1),
            shares=Decimal("1000"),
            price=Decimal("2"),
            tags=["dca"],
        )
        result = runner.invoke(app, ["tx", "list"])
        assert result.exit_code == 0
        assert "163406" in result.stdout

    def test_list_filter_by_tag(self, fake_journal: PortfolioJournal) -> None:
        from global_allocation.models import AssetClass

        fake_journal.add_fund("163406", "x", AssetClass.MIXED)
        fake_journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 1),
            shares=Decimal("1000"),
            price=Decimal("2"),
            tags=["dca"],
        )
        fake_journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 15),
            shares=Decimal("500"),
            price=Decimal("2"),
            tags=["dip-buy"],
        )
        result = runner.invoke(app, ["tx", "list", "--tag", "dca"])
        assert result.exit_code == 0


class TestShow:
    def test_show_no_holdings(self, fake_journal: PortfolioJournal) -> None:
        result = runner.invoke(app, ["show"])
        assert result.exit_code == 0
        assert "无" in result.stdout or "empty" in result.stdout.lower()

    def test_show_with_holdings(self, fake_journal: PortfolioJournal) -> None:
        from global_allocation.models import AssetClass

        fake_journal.add_fund("163406", "兴全合润", AssetClass.MIXED)
        fake_journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 1),
            shares=Decimal("1000"),
            price=Decimal("2.30"),
            fee=Decimal("1"),
        )
        result = runner.invoke(app, ["show"], terminal_width=200)
        assert result.exit_code == 0
        # 基金 code + 总市值 = 1000 * 2.50 = 2500
        assert "163406" in result.stdout
        assert "2,500.00" in result.stdout


class TestSnapshot:
    def test_snapshot_no_holdings_raises(self, fake_journal: PortfolioJournal) -> None:
        result = runner.invoke(app, ["snapshot", "--date", "2026-09-11"])
        assert result.exit_code != 0

    def test_snapshot_success(self, fake_journal: PortfolioJournal) -> None:
        from global_allocation.models import AssetClass

        fake_journal.add_fund("163406", "兴全合润", AssetClass.MIXED)
        fake_journal.record_buy(
            fund_code="163406",
            trade_date=date(2026, 9, 1),
            shares=Decimal("1000"),
            price=Decimal("2.30"),
        )
        result = runner.invoke(
            app, ["snapshot", "--date", "2026-09-11"],
        )
        assert result.exit_code == 0, result.output
        snaps = fake_journal.list_snapshots()
        assert len(snaps) == 1


class TestReport:
    def test_report_empty(self, fake_journal: PortfolioJournal) -> None:
        result = runner.invoke(app, ["report"])
        assert result.exit_code == 0


class TestImport:
    def test_import_success(
        self, fake_journal: PortfolioJournal, tmp_path: Path
    ) -> None:
        import json

        f = tmp_path / "holdings.json"
        f.write_text(
            json.dumps(
                [
                    {
                        "code": "163406",
                        "name": "兴全合润",
                        "asset_class": "mixed",
                        "current_value": "2500",
                        "cumulative_pnl": "0",
                    }
                ]
            ),
            encoding="utf-8",
        )
        result = runner.invoke(app, ["import", "--file", str(f)])
        assert result.exit_code == 0, result.output
        assert "1" in result.stdout  # 已导入 1 只
        assert fake_journal.get_fund("163406") is not None

    def test_import_missing_file(self, fake_journal: PortfolioJournal) -> None:
        result = runner.invoke(app, ["import", "--file", "/nope/x.json"])
        assert result.exit_code != 0

    def test_import_bad_json(
        self, fake_journal: PortfolioJournal, tmp_path: Path
    ) -> None:
        f = tmp_path / "bad.json"
        f.write_text("not json", encoding="utf-8")
        result = runner.invoke(app, ["import", "--file", str(f)])
        assert result.exit_code != 0

    def test_import_uses_default_asset_class(
        self, fake_journal: PortfolioJournal, tmp_path: Path
    ) -> None:
        import json

        f = tmp_path / "h.json"
        f.write_text(
            json.dumps(
                [
                    {
                        "code": "163406",
                        "name": "x",
                        "current_value": "2500",
                        "cumulative_pnl": "0",
                    }
                ]
            ),
            encoding="utf-8",
        )
        result = runner.invoke(
            app,
            [
                "import",
                "--file",
                str(f),
                "--asset-class-default",
                "equity",
            ],
        )
        assert result.exit_code == 0, result.output
        fund = fake_journal.get_fund("163406")
        assert fund is not None
        assert fund.asset_class.value == "equity"
