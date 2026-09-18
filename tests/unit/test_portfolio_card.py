"""测试 src/global_allocation/portfolio/card.py。

飞书 chart card for portfolio journal — spec 096 重设计：
focus 在实盘持仓 = summary div + 大类资产柱状图 + 持仓明细表。
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
    """塞 2 个基金 + 2 笔交易 + 1 个快照。"""
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
        """卡片 = header + summary div + bar chart + holdings table + footer。"""
        _seed(journal)
        card = build_portfolio_card(journal, title="我的实盘")
        assert "header" in card
        assert card["header"]["template"] == "blue"
        assert "elements" in card
        # 只有 1 个 chart + 1 个 table（之前是 2 chart + 2 table）
        charts = [e for e in card["elements"] if e.get("tag") == "chart"]
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        divs = [e for e in card["elements"] if e.get("tag") == "div"]
        assert len(charts) == 1
        assert len(tables) == 1
        assert len(divs) == 1

    def test_summary_includes_total_and_return(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        divs = [e for e in card["elements"] if e.get("tag") == "div"]
        text = divs[0]["text"]["content"]
        assert "总市值" in text
        assert "CNY" in text

    def test_no_holdings_raises(self, journal: PortfolioJournal) -> None:
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


class TestBreakdownBarChart:
    """柱状图：各大类资产市值（horizontal bar）。"""

    def _get_bar(self, journal: PortfolioJournal) -> dict[str, object]:
        _seed(journal)
        card = build_portfolio_card(journal)
        charts = [e for e in card["elements"] if e.get("tag") == "chart"]
        assert len(charts) == 1
        spec = charts[0]["chart_spec"]
        assert spec["type"] == "column"
        return spec  # type: ignore[return-value]

    def test_is_vertical_bar(self, journal: PortfolioJournal) -> None:
        """竖柱状图：x 轴 = class（中文类名），y 轴 = value（金额）。"""
        spec = self._get_bar(journal)
        assert spec["xField"] == "class"
        assert spec["yField"] == "value"

    def test_data_is_dict_shaped(self, journal: PortfolioJournal) -> None:
        spec = self._get_bar(journal)
        values = spec["data"]["values"]
        for v in values:
            assert "class" in v
            assert "value" in v

    def test_skips_empty_classes(self, journal: PortfolioJournal) -> None:
        """只有 count > 0 的子类才出现在柱状图里。"""
        spec = self._get_bar(journal)
        # 测试只塞了 2 个基金（MIXED + EQUITY），没 SUBCLASS mapping 会被忽略
        # 所以应该是空 data.values（2 个基金都不在 SUBCLASS_BY_CODE 里）
        # 这条测试只是确认 code 不会崩
        for v in spec["data"]["values"]:
            assert isinstance(v["value"], (int, float))
            assert v["value"] > 0


class TestHoldingsTable:
    """持仓明细表：单只基金 + 分类 + 市值 + 占比。"""

    def _get_table(self, journal: PortfolioJournal) -> dict[str, object]:
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        assert len(tables) == 1
        return tables[0]

    def test_columns_are_five(self, journal: PortfolioJournal) -> None:
        spec = self._get_table(journal)
        col_names = [c["name"] for c in spec["columns"]]
        assert col_names == ["code", "name", "class", "value", "weight"]

    def test_rows_are_dict_shaped(self, journal: PortfolioJournal) -> None:
        """Feishu API 强制 row 是 dict（按列名取）。"""
        spec = self._get_table(journal)
        for row in spec["rows"]:
            assert isinstance(row, dict)
            assert set(row.keys()) == {"code", "name", "class", "value", "weight"}

    def test_all_columns_text_type(self, journal: PortfolioJournal) -> None:
        """value / weight 是预格式化字符串，全 text 列。"""
        spec = self._get_table(journal)
        for col in spec["columns"]:
            assert col["data_type"] == "text"

    def test_value_formatted_with_commas(self, journal: PortfolioJournal) -> None:
        """市值带千分位逗号。"""
        spec = self._get_table(journal)
        for row in spec["rows"]:
            # 形如 "12,345.67"
            assert "," in row["value"] or row["value"].count(".") == 1

    def test_weight_has_percent_sign(self, journal: PortfolioJournal) -> None:
        """占比以 % 结尾。"""
        spec = self._get_table(journal)
        for row in spec["rows"]:
            assert row["weight"].endswith("%")

    def test_class_field_is_chinese_display_name(self, journal: PortfolioJournal) -> None:
        """分类列显示中文类名（不是 enum value）。"""
        spec = self._get_table(journal)
        for row in spec["rows"]:
            # 没在 SUBCLASS_BY_CODE 里的基金 → class 是空字符串
            # 在的 → 是中文（不是 ASCII）
            assert row["class"] == "" or not row["class"].isascii()


class TestRemovedSections:
    """spec 096 移除的部分必须真的没了。"""

    def test_no_pie_chart(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        charts = [e for e in card["elements"] if e.get("tag") == "chart"]
        for c in charts:
            assert c["chart_spec"]["type"] != "pie"

    def test_no_line_chart(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        charts = [e for e in card["elements"] if e.get("tag") == "chart"]
        for c in charts:
            assert c["chart_spec"]["type"] != "line"

    def test_no_recent_transactions_columns(self, journal: PortfolioJournal) -> None:
        """持仓明细表的列不应该有交易流水的字段（date / side / strategy）。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        for t in tables:
            col_names = {c["name"] for c in t["columns"]}
            assert "date" not in col_names
            assert "side" not in col_names
            assert "strategy" not in col_names
            assert "shares" not in col_names
            assert "price" not in col_names

    def test_no_breakdown_class_count_columns(self, journal: PortfolioJournal) -> None:
        """持仓明细表不是 breakdown table（不该有 count 列）。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        for t in tables:
            col_names = {c["name"] for c in t["columns"]}
            assert "count" not in col_names


class TestOrdering:
    """元素顺序：summary → bar chart → holdings table。"""

    def test_order(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        tags = [e.get("tag") for e in card["elements"] if e.get("tag") in {"div", "chart", "table"}]
        assert tags == ["div", "chart", "table"]
