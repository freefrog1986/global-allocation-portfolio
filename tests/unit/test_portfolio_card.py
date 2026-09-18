"""测试 src/global_allocation/portfolio/card.py。

飞书 chart card for portfolio journal。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from global_allocation.models import AssetClass
from global_allocation.portfolio.card import build_portfolio_card
from global_allocation.portfolio.db import PortfolioDB
from global_allocation.portfolio.journal import PortfolioJournal
from global_allocation.portfolio.models import WeeklySnapshot


class FakePriceSource:
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


def _seed(journal: PortfolioJournal) -> None:
    """塞 2 个基金 + 3 笔交易 + 2 个快照。"""
    import json

    journal.add_fund("163406", "兴全合润", AssetClass.MIXED)
    journal.add_fund("510300", "沪深300", AssetClass.EQUITY)
    journal.record_buy(
        fund_code="163406",
        trade_date=date(2026, 9, 1),
        shares=Decimal("1000"),
        price=Decimal("2.30"),
        fee=Decimal("1"),
        strategy="定投",
        tags=["dca"],
    )
    journal.record_buy(
        fund_code="510300",
        trade_date=date(2026, 9, 1),
        shares=Decimal("500"),
        price=Decimal("3.85"),
    )
    # 历史快照
    journal._db.upsert_snapshot(
        WeeklySnapshot(
            week_end_date=date(2026, 9, 4),
            total_value=Decimal("4300"),
            week_return=Decimal("0"),
            cumulative_return=Decimal("0"),
            holdings_json=json.dumps([]),
            created_at=datetime(2026, 9, 4, 17),
        )
    )


class TestBuildPortfolioCard:
    def test_basic_structure(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal, title="我的实盘")
        assert "header" in card
        assert card["header"]["template"] == "blue"
        assert "elements" in card
        # 有 summary div
        divs = [e for e in card["elements"] if e.get("tag") == "div"]
        assert len(divs) >= 1

    def test_summary_includes_total_and_return(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        divs = [e for e in card["elements"] if e.get("tag") == "div"]
        text = divs[0]["text"]["content"]
        assert "总市值" in text
        assert "CNY" in text

    def test_pie_chart_present(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        charts = [e for e in card["elements"] if e.get("tag") == "chart"]
        assert len(charts) >= 1
        pie = next(
            (c for c in charts if c["chart_spec"]["type"] == "pie"), None
        )
        assert pie is not None
        values = pie["chart_spec"]["data"]["values"]
        # 应该有 2 个 slice（2 个基金）
        assert len(values) >= 2
        # 每条必须含 type + value
        assert "value" in values[0]
        assert "type" in values[0]

    def test_history_line_chart_present(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        charts = [e for e in card["elements"] if e.get("tag") == "chart"]
        line = next(
            (c for c in charts if c["chart_spec"]["type"] == "line"), None
        )
        assert line is not None
        # 至少 1 个历史快照 + 当前
        values = line["chart_spec"]["data"]["values"]
        assert len(values) >= 1
        # 每条是 {date, nav}
        assert "date" in values[0]
        assert "nav" in values[0]

    def test_recent_transactions_table(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        assert len(tables) >= 1

    def test_breakdown_table_present(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        # breakdown table + recent_transactions table
        assert len(tables) >= 2
        breakdown = tables[0]
        assert "columns" in breakdown
        assert "rows" in breakdown
        col_names = [c["name"] for c in breakdown["columns"]]
        assert col_names == ["class", "count", "value", "weight"]

    def test_breakdown_table_position_after_summary(
        self, journal: PortfolioJournal
    ) -> None:
        """持仓现状表必须是 summary div 之后的第一个 table。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        elements = card["elements"]
        # 第一个元素是 summary div
        assert elements[0]["tag"] == "div"
        # 下一个非 hr 元素应该是 breakdown table
        idx = 1
        while elements[idx]["tag"] == "hr":
            idx += 1
        assert elements[idx]["tag"] == "table"
        # 它的列必须是 持仓现状 表
        col_names = [c["name"] for c in elements[idx]["columns"]]
        assert col_names[0] == "class"

    def test_breakdown_table_skips_empty_classes(
        self, journal: PortfolioJournal
    ) -> None:
        """空类不显示。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        breakdown = tables[0]
        rows = breakdown["rows"]
        # 测试只塞了 2 个基金（MIXED + EQUITY），没 SUBCLASS mapping 的会被静默忽略
        # 所以空类不出现 = 表格里都是 count > 0 的
        for row in rows:
            count = int(row[1])
            assert count > 0

    def test_no_holdings_raises(self, journal: PortfolioJournal) -> None:
        # 没有基金和交易
        with pytest.raises(ValueError, match="持仓"):
            build_portfolio_card(journal)

    def test_title_override(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal, title="我的实盘 9 月")
        assert card["header"]["title"]["content"] == "我的实盘 9 月"

    def test_default_title(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        assert card["header"]["title"]["content"] == "实盘持仓"
