"""测试 src/global_allocation/portfolio/breakdown.py。

按 Swensen 框架把持仓归到 14 个子类。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from global_allocation.models import AssetClass
from global_allocation.portfolio.breakdown import (
    DISPLAY_NAME,
    SUBCLASS_BY_CODE,
    SwensenClass,
    compute_breakdown,
    get_subclass,
)
from global_allocation.portfolio.db import PortfolioDB
from global_allocation.portfolio.journal import PortfolioJournal


class FakePriceSource:
    def __init__(self, prices: dict[str, Decimal]) -> None:
        self._prices = prices

    def get_price(self, code: str, on: date) -> Decimal | None:
        return self._prices.get(code)


class TestSwensenClass:
    def test_has_14_classes(self) -> None:
        assert len(SwensenClass) == 14

    def test_display_name_for_every_class(self) -> None:
        for sub in SwensenClass:
            assert sub in DISPLAY_NAME
            # 中文显示名（不是 ASCII 字母代号）
            assert not DISPLAY_NAME[sub].isascii()


class TestSubclassByCode:
    def test_every_value_is_swensen_class(self) -> None:
        for code, sub in SUBCLASS_BY_CODE.items():
            assert isinstance(sub, SwensenClass), f"{code} → {sub}"

    def test_known_funds_mapped_correctly(self) -> None:
        # 抽样几个关键分类
        assert get_subclass("013310") == SwensenClass.CN_EQUITY  # A 股
        assert get_subclass("004098") == SwensenClass.HK_EQUITY  # 港股
        assert get_subclass("519981") == SwensenClass.US_EQUITY  # 美股
        assert get_subclass("457001") == SwensenClass.ASIA_DM_EQUITY  # 亚洲发达
        assert get_subclass("378006") == SwensenClass.EM_EQUITY  # 新兴市场
        assert get_subclass("160140") == SwensenClass.US_REIT  # 美国 REITs
        assert get_subclass("028277") == SwensenClass.CN_REIT  # 国内 REITs
        assert get_subclass("100050") == SwensenClass.US_BOND  # 美债
        assert get_subclass("008505") == SwensenClass.CN_CREDIT_BOND  # 国内信用债
        assert get_subclass("000216") == SwensenClass.COMMODITY  # 商品
        assert get_subclass("004137") == SwensenClass.CASH  # 现金

    def test_unknown_code_returns_none(self) -> None:
        assert get_subclass("999999") is None


class TestComputeBreakdown:
    def _make_journal(
        self,
        tmp_path: Path,
        holdings: list[tuple[str, str, AssetClass, Decimal, Decimal]],
    ) -> PortfolioJournal:
        """创建 journal + 买入若干只基金。

        holdings: [(code, name, asset_class, shares, price), ...]
        """
        db = PortfolioDB(path=tmp_path / "p.db")
        prices = FakePriceSource({code: price for code, _, _, _, price in holdings})
        journal = PortfolioJournal(db=db, price_source=prices)
        for code, name, asset_class, shares, price in holdings:
            journal.add_fund(code, name, asset_class)
            journal.record_buy(
                fund_code=code,
                trade_date=date(2026, 9, 1),
                shares=shares,
                price=price,
            )
        return journal

    def test_groups_by_subclass(
        self, tmp_path: Path
    ) -> None:
        journal = self._make_journal(
            tmp_path,
            [
                ("013310", "A 股 1", AssetClass.EQUITY, Decimal("1000"), Decimal("2")),
                ("004098", "港股 1", AssetClass.EQUITY, Decimal("500"), Decimal("3")),
                ("004098", "港股 2", AssetClass.EQUITY, Decimal("500"), Decimal("3")),
            ],
        )
        rows = {r["subclass"]: r for r in compute_breakdown(journal)}
        # 1 只 A 股 + 2 笔港股（同一只基金）= 港股 1 只基金、市值 3000
        assert rows[SwensenClass.CN_EQUITY]["count"] == 1
        assert rows[SwensenClass.CN_EQUITY]["value"] == Decimal("2000")
        assert rows[SwensenClass.HK_EQUITY]["count"] == 1
        assert rows[SwensenClass.HK_EQUITY]["value"] == Decimal("3000")

    def test_weight_sums_to_one_when_total_positive(
        self, tmp_path: Path
    ) -> None:
        journal = self._make_journal(
            tmp_path,
            [
                ("013310", "A 股", AssetClass.EQUITY, Decimal("1000"), Decimal("2")),
                ("004098", "港股", AssetClass.EQUITY, Decimal("500"), Decimal("3")),
            ],
        )
        rows = compute_breakdown(journal)
        total_weight = sum((r["weight"] for r in rows), Decimal("0"))
        assert abs(total_weight - Decimal("1")) < Decimal("1e-9")

    def test_includes_empty_classes(
        self, tmp_path: Path
    ) -> None:
        journal = self._make_journal(
            tmp_path,
            [("013310", "A 股", AssetClass.EQUITY, Decimal("100"), Decimal("2"))],
        )
        rows = compute_breakdown(journal)
        assert len(rows) == 14
        # 没持仓的类是 count=0, value=0, weight=0
        cn_gov = next(r for r in rows if r["subclass"] == SwensenClass.CN_GOV_BOND)
        assert cn_gov["count"] == 0
        assert cn_gov["value"] == Decimal("0")
        assert cn_gov["weight"] == Decimal("0")

    def test_unknown_fund_excluded_silently(
        self, tmp_path: Path
    ) -> None:
        # 999999 不在 SUBCLASS_BY_CODE 里，应该被静默忽略
        journal = self._make_journal(
            tmp_path,
            [
                ("013310", "A 股", AssetClass.EQUITY, Decimal("100"), Decimal("2")),
                ("999999", "未知基金", AssetClass.EQUITY, Decimal("100"), Decimal("5")),
            ],
        )
        rows = compute_breakdown(journal)
        # 只统计 A 股
        total_value = sum((r["value"] for r in rows), Decimal("0"))
        assert total_value == Decimal("200")  # 只算 A 股，未知基金不进任何类

    def test_no_holdings_all_zero(
        self, tmp_path: Path
    ) -> None:
        db = PortfolioDB(path=tmp_path / "p.db")
        journal = PortfolioJournal(db=db, price_source=FakePriceSource({}))
        rows = compute_breakdown(journal)
        assert len(rows) == 14
        for r in rows:
            assert r["count"] == 0
            assert r["value"] == Decimal("0")
            assert r["weight"] == Decimal("0")

    def test_returned_in_enum_order(self, tmp_path: Path) -> None:
        journal = self._make_journal(
            tmp_path,
            [("013310", "A 股", AssetClass.EQUITY, Decimal("100"), Decimal("2"))],
        )
        rows = compute_breakdown(journal)
        subclasses = [r["subclass"] for r in rows]
        assert subclasses == list(SwensenClass)
