"""测试 src/global_allocation/portfolio/breakdown.py。

按 Swensen 框架把持仓归到 11 个子类（spec 097 第十八轮：47 → 36，只留宽基 ETF）。
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
    def test_has_11_classes(self) -> None:
        """第十七轮：从 14 子类精简到 11 子类（合并 + 删信用债）。

        14 - 4（全球主题/欧洲发达/亚洲发达/国内信用债） + 1（国外发达）= 11。
        """
        assert len(SwensenClass) == 11

    def test_removed_classes_no_longer_exist(self) -> None:
        """第十七轮：被砍掉的子类不再存在。
        - GLOBAL_THEMED_EQUITY（并入 US_EQUITY）
        - EU_EQUITY + ASIA_DM_EQUITY（合并成 FOREIGN_DM_EQUITY）
        - CN_CREDIT_BOND（斯文森说没阿尔法，砍掉；基金并入 CN_GOV_BOND）
        """
        names = {m.name for m in SwensenClass}
        assert "GLOBAL_THEMED_EQUITY" not in names
        assert "EU_EQUITY" not in names
        assert "ASIA_DM_EQUITY" not in names
        assert "CN_CREDIT_BOND" not in names

    def test_added_class_exists(self) -> None:
        """第十七轮新增：FOREIGN_DM_EQUITY（合并欧美亚达）。"""
        names = {m.name for m in SwensenClass}
        assert "FOREIGN_DM_EQUITY" in names
        assert SwensenClass.FOREIGN_DM_EQUITY.value == "foreign_dm_equity"

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
        # A 股宽基 6（第十九轮加 022448 国泰A500 联接）
        assert get_subclass("013310") == SwensenClass.CN_EQUITY  # 科创创业50
        assert get_subclass("022434") == SwensenClass.CN_EQUITY  # A500
        assert get_subclass("022424") == SwensenClass.CN_EQUITY  # A500
        assert get_subclass("017644") == SwensenClass.CN_EQUITY  # 1000 增强
        assert get_subclass("014532") == SwensenClass.CN_EQUITY  # A50
        assert get_subclass("022448") == SwensenClass.CN_EQUITY  # A500 联接（第十九轮从红利策略移过来）
        # 美股宽基 6（第十九轮补回 016452）
        assert get_subclass("519981") == SwensenClass.US_EQUITY  # 标普100
        assert get_subclass("017641") == SwensenClass.US_EQUITY  # 标普500
        assert get_subclass("018966") == SwensenClass.US_EQUITY  # 纳100
        assert get_subclass("539001") == SwensenClass.US_EQUITY  # 纳100
        assert get_subclass("016452") == SwensenClass.US_EQUITY  # 纳100（第十九轮补回）
        assert get_subclass("019524") == SwensenClass.US_EQUITY  # 纳100
        # 国外发达（liubo 2026-09-22 把 457001 移到 ETF 轮动组合，子类暂留空）+ 新兴
        assert get_subclass("378006") == SwensenClass.EM_EQUITY  # MSCI Emerging Markets
        # 457001 移走：现在不属于大类资产配置组合
        assert get_subclass("457001") is None
        # REITs / 债 / 商品 / 现金
        assert get_subclass("028277") == SwensenClass.CN_REIT
        assert get_subclass("160140") == SwensenClass.US_REIT
        assert get_subclass("100050") == SwensenClass.US_BOND
        # 原国内信用债基金 → CN_GOV_BOND（第十七轮：斯文森说信用债没阿尔法，并入利率债）
        assert get_subclass("008505") == SwensenClass.CN_GOV_BOND  # 第十九轮解锁
        assert get_subclass("004827") == SwensenClass.CN_GOV_BOND  # 第十九轮解锁
        assert get_subclass("003547") == SwensenClass.CN_GOV_BOND
        assert get_subclass("000931") == SwensenClass.CN_GOV_BOND
        assert get_subclass("000216") == SwensenClass.COMMODITY  # 商品
        assert get_subclass("004137") == SwensenClass.CASH  # 现金

    def test_nineteen_new_funds_added(self) -> None:
        """第十九轮（liubo 2026-09-22）加 9 只基金到大类资产配置：
        - 022448 国泰中证A500ETF发起联接A（CN_EQUITY，从红利策略移过来）
        - 007997 易方达年年恒秋一年定开债A（CN_GOV_BOND，从红利策略移过来）
        - 7 只新增中债（CN_GOV_BOND）：004534/110017/009625/005690/400030/010942/008420
        """
        assert get_subclass("022448") == SwensenClass.CN_EQUITY  # 从红利策略移过来
        for code in ("007997", "004534", "110017", "009625", "005690",
                     "400030", "010942", "008420"):
            assert get_subclass(code) == SwensenClass.CN_GOV_BOND, f"{code} 第十九轮新增"

    def test_eleven_non_broad_funds_removed(self) -> None:
        """第十八轮砍 11 只非宽基股权：港股 5 + 美股 3 QDII 主题 + A 股 3 红利低波。

        这些基金不在 SUBCLASS_BY_CODE 里了（不再属于大类资产配置组合）。
        """
        # 港股 5
        for code in ("004098", "013127", "006809", "014673", "016495"):
            assert code not in SUBCLASS_BY_CODE, f"{code} 已砍"
            assert get_subclass(code) is None
        # 美股 QDII 主题 3
        for code in ("017730", "016664", "006373"):
            assert code not in SUBCLASS_BY_CODE, f"{code} 已砍"
            assert get_subclass(code) is None
        # A 股红利低波 3
        for code in ("005561", "007605", "008114"):
            assert code not in SUBCLASS_BY_CODE, f"{code} 已砍"
            assert get_subclass(code) is None

    def test_no_more_hk_equity_in_mapping(self) -> None:
        """第十八轮：港股全部砍完，SUBCLASS_BY_CODE 里没有 HK_EQUITY 基金。
        （HK_EQUITY 枚举值保留，但当前 0 只基金映射过去。）"""
        hk_funds = [
            code for code, sub in SUBCLASS_BY_CODE.items()
            if sub == SwensenClass.HK_EQUITY
        ]
        assert hk_funds == []

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
        # 第十九轮（liubo 2026-09-22）：457001 移到 ETF 轮动组合，
        # 国外发达市场股票子类暂时为 0；改用新兴市场 378006 当海外子类代表
        journal = self._make_journal(
            tmp_path,
            [
                ("013310", "A 股 1", AssetClass.EQUITY, Decimal("1000"), Decimal("2")),
                ("378006", "新兴市场 1", AssetClass.EQUITY, Decimal("500"), Decimal("3")),
                ("378006", "新兴市场 2", AssetClass.EQUITY, Decimal("500"), Decimal("3")),
            ],
        )
        rows = {r["subclass"]: r for r in compute_breakdown(journal)}
        # 1 只 A 股 + 2 笔新兴市场（同一只基金）= 新兴市场 1 只基金、市值 3000
        assert rows[SwensenClass.CN_EQUITY]["count"] == 1
        assert rows[SwensenClass.CN_EQUITY]["value"] == Decimal("2000")
        assert rows[SwensenClass.EM_EQUITY]["count"] == 1
        assert rows[SwensenClass.EM_EQUITY]["value"] == Decimal("3000")
        # 国外发达市场股票子类（liubo 2026-09-22 移走 457001 后）：当前为 0
        assert rows[SwensenClass.FOREIGN_DM_EQUITY]["count"] == 0
        assert rows[SwensenClass.FOREIGN_DM_EQUITY]["value"] == Decimal("0")

    def test_weight_sums_to_one_when_total_positive(
        self, tmp_path: Path
    ) -> None:
        # 港股砍了、457001 移到 ETF 轮动组合，改用 378006（新兴市场）当海外代表
        journal = self._make_journal(
            tmp_path,
            [
                ("013310", "A 股", AssetClass.EQUITY, Decimal("1000"), Decimal("2")),
                ("378006", "新兴市场", AssetClass.EQUITY, Decimal("500"), Decimal("3")),
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
        assert len(rows) == 11
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
        assert len(rows) == 11
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
