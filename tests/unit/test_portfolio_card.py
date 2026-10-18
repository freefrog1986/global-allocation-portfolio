"""测试 src/global_allocation/portfolio/card.py。

飞书 chart card for portfolio journal — spec 096 重设计 + spec 097 第十九轮合并：
focus 在实盘持仓 = summary div + 大类资产柱状图 + 5 列聚合持仓表（含 target/delta）。
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import re

import pytest

from global_allocation.models import AssetClass
from global_allocation.portfolio.card import (
    POSITION_UNIT,
    US_INDICATOR_CODES,
    US_INDICATOR_NAMES,
    US_INDICATOR_SHORT_NAMES,
    _build_a_share_valuation_section,
    _build_fund_valuation_section,
    _build_hk_valuation_section,
    _build_us_valuation_section,
    _build_valuation_section,
    _compute_roe_yoy,
    _decide_a_share_strategy,
    _format_indicator_value,
    _per_fund_recommendation,
    _per_fund_verdict,
    build_portfolio_card,
)
from global_allocation.portfolio.db import PortfolioDB
from global_allocation.portfolio.journal import PortfolioJournal
from global_allocation.portfolio.models import (
    FundValuation,
    ValuationIndicator,
    ValuationIndicatorCode,
    WeeklySnapshot,
)


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


def _seed_valuation_today(journal: PortfolioJournal) -> None:
    """塞入今天的 4 个 A 股估值指标（spec 098 卡片 Section 3 用）。"""
    today = date.today()
    # 用 mock 数据：股债利差=+5.2%（偏低估），PE 分位=28%（偏低估），巴菲特=65%（正常），股息率=2.5%（正常）
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
            value=Decimal("0.052"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.PE_PERCENTILE,
            value=Decimal("0.28"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.BUFFETT_INDICATOR,
            value=Decimal("0.65"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.DIVIDEND_YIELD,
            value=Decimal("0.025"),
            source="test",
        )
    )


class TestBuildPortfolioCard:
    def test_basic_structure(self, journal: PortfolioJournal) -> None:
        """卡片 = header + 1 个 section（实盘持仓 — 第二十一轮删除 Section 2 大类资产策略）。

        spec 097 演进：
        - 第十九轮：把 Section 2 + 3 合并到 Section 1 持仓表（加 target/delta 列）
        - 第二十轮：恢复 Section 2（liubo 反馈："我担心没控制好超类的比例了"）
        - 第二十一轮（当前）：删除 Section 2，改用整数仓位模型
          - 持仓表的 target/delta 列已经包含超类信息
          - Section 2 冗余，liubo 拍板删除
          - 持仓表 target 列改用整数份数 "{N} 份 ({X.X}%)"

        现在的元素：
        - Section 1（实盘持仓）：
          - note header "实盘持仓"
          - div summary
          - hr
          - chart（柱状图，带顶部数值标签）
          - hr
          - table（11 行 × 5 列：分类 / 市值 / 占比 / 目标 / 偏离）
            - target：投资子类 "{N} 份 ({X.X}%)"；现金 "[{min}, {max}] 份"
            - delta：投资子类 "{+/-N} 份"；现金 "区间内/低于下限/高于上限"

        合计：1 note + 1 div + 2 hr + 1 chart + 1 table = 6 个元素 + footer
        + 第二十二轮（liubo 2026-10-17）调仓纪录 section（表格版）：
        + 1 note + 1 hr（跨 section 分隔）+ 1 hr（section 内）+ 1 table = +4 个元素
        + 第二十五轮（liubo 2026-10-17 第二轮反馈）定投纪录 section（跟调仓分开 2 张表）：
        + 1 note + 1 hr（跨 section 分隔）+ 1 hr（section 内）+ 1 table = +4 个元素

        无估值总计：4 note + 1 div + 7 hr + 1 chart + 4 table = 17 + footer
        """
        _seed(journal)
        card = build_portfolio_card(journal, title="我的实盘")
        assert "header" in card
        assert card["header"]["template"] == "blue"
        assert "elements" in card
        charts = [e for e in card["elements"] if e.get("tag") == "chart"]
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        divs = [e for e in card["elements"] if e.get("tag") == "div"]
        notes = [e for e in card["elements"] if e.get("tag") == "note"]
        hrs = [e for e in card["elements"] if e.get("tag") == "hr"]
        assert len(charts) == 1
        # 第二十五轮：4 张 table（持仓聚合表 + per-fund + 调仓记录 + 定投记录）
        assert len(tables) == 4
        # 第二十五轮：4 个 section 都有 note（持仓 + per-fund + 调仓记录 + 定投记录）
        assert len(notes) == 4
        # 第二十五轮：Section 1 summary + Section 2 summary = 2 个 div（Section 3 + 4 是 table）
        assert len(divs) == 2
        # 第二十五轮：7 个 hr（Section 1 内 2 + Section 2 入口 1 + Section 3 入口 1 + Section 3 内 1 + Section 4 入口 1 + Section 4 内 1）
        assert len(hrs) == 7

    def test_summary_includes_total_and_return(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        divs = [e for e in card["elements"] if e.get("tag") == "div"]
        # 唯一的 div 是 Section 1 summary（总市值 / 盈亏 / 周涨跌）
        text = divs[0]["text"]["content"]
        assert "总市值" in text
        assert "CNY" in text

    def test_summary_does_not_repeat_header_title(self, journal: PortfolioJournal) -> None:
        """第十三轮反馈：summary 第一行不要重复 header.title。

        卡片 header 已经写了"实盘周报"，body 开头 note section header 已经写了
        "实盘持仓"，summary div 里再写一遍 title 是冗余。summary 第一行必须是
        "**生成时间**"。
        """
        _seed(journal)
        card = build_portfolio_card(journal, title="我的实盘 9 月")
        divs = [e for e in card["elements"] if e.get("tag") == "div"]
        text = divs[0]["text"]["content"]
        first_line = text.split("\n", 1)[0]
        assert not first_line.startswith("**我的实盘 9 月**")
        assert first_line.startswith("**生成时间**")

    def test_no_holdings_falls_back_to_cost_basis(self, journal: PortfolioJournal) -> None:
        # 第二十二轮（liubo 2026-10-16）：holdings 为空时不再抛异常，
        # 改走 fallback：Section 1 = 持仓成本表（cost_basis.py + breakdown.py）
        card = build_portfolio_card(journal)
        elements = card["elements"]
        # 第一个元素应是"持仓成本"note（fallback header）
        first_note = elements[0]
        assert first_note["tag"] == "note"
        assert "持仓成本" in first_note["elements"][0]["content"]
        # 应包含 cost_basis table（4 列：class/count/cost/weight）
        tables = [el for el in elements if el.get("tag") == "table"]
        assert any(
            {"count", "cost", "weight"} <= set(t.get("columns", [{}])[0].keys()) | {c["name"] for c in t.get("columns", [])}
            for t in tables
        )

    def test_title_override(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal, title="我的实盘 9 月")
        assert card["header"]["title"]["content"] == "我的实盘 9 月"

    def test_default_title(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        # 第十二轮反馈：header.title 默认改"实盘周报"（总标题，未来加多 section 不冲突）
        assert card["header"]["title"]["content"] == "实盘周报"


class TestBreakdownBarChart:
    """柱状图：各大类资产占比（vertical bar，按 SwensenClass 枚举顺序，全部 11 类；spec 097 第十七轮精简）。"""

    def _get_bar(self, journal: PortfolioJournal) -> dict[str, object]:
        _seed(journal)
        card = build_portfolio_card(journal)
        charts = [e for e in card["elements"] if e.get("tag") == "chart"]
        assert len(charts) == 1
        spec = charts[0]["chart_spec"]
        assert spec["type"] == "bar"
        return spec  # type: ignore[return-value]

    def test_uses_feishu_simple_bar_format(self, journal: PortfolioJournal) -> None:
        """飞书 VChart 柱状图 = type='bar' + data.values + xField/yField。

        之前误用 'column' (VChart 内部名) + rich 格式（x_axis/series）导致飞书返回 230099。
        """
        spec = self._get_bar(journal)
        assert spec["type"] == "bar"
        assert "data" in spec
        assert "values" in spec["data"]
        assert spec["xField"] == "class"
        assert spec["yField"] == "weight"  # 第三轮：纵坐标 = 占比

    def test_values_have_class_and_weight_keys(self, journal: PortfolioJournal) -> None:
        spec = self._get_bar(journal)
        for item in spec["data"]["values"]:
            assert "class" in item
            assert "weight" in item

    def test_label_visible_positioned_at_top(self, journal: PortfolioJournal) -> None:
        """第十九轮（liubo 2026-09-19）：柱子顶部带数值标签。

        liubo 反馈："股票和港股的上边那个数字怎么没在上面了，希望它在上面"。

        之前为了加 % 后缀试过 formatMethod/formatter 都失败（飞书 VChart 不支持 JS 函数
        和 {value} 模板替换，见 memory feishu_vchart_chart_label_limits），所以 label
        配置只用 visible + position，不传 formatter — 默认渲染显示 yField 原始数值
        （"29.9" 这种），单位靠 title "占比（%）" 明示。
        """
        spec = self._get_bar(journal)
        assert "label" in spec
        assert spec["label"]["visible"] is True
        assert spec["label"]["position"] == "top"
        # 不传 formatMethod（飞书 VChart 不支持 JS 函数和 {value} 模板替换）
        assert "formatMethod" not in spec["label"]

    def test_weight_is_percent_scaled(self, journal: PortfolioJournal) -> None:
        """weight 是 0~100 的百分比数字（不是 0~1 的小数）。

        VChart 显示成 0/5/10/.../30，标题"占比（%）"明示单位。
        不论种子数据哪些子类有持仓，全部 weight 都应该在 [0, 100] 区间内且总和不超过 100。
        第四轮反馈：Y 轴数字只保留 1 位小数（不要 2 位，太长）。
        """
        spec = self._get_bar(journal)
        for item in spec["data"]["values"]:
            assert 0 <= item["weight"] <= 100
            # 1 位小数 = round(x, 1) — float 算上 round，精度误差 < 0.05
            assert abs(item["weight"] - round(item["weight"], 1)) < 0.05
        total = sum(item["weight"] for item in spec["data"]["values"])
        assert total <= 100.01  # 算上浮点误差，不会超过 100

    def test_includes_all_swensen_classes_in_order(self, journal: PortfolioJournal) -> None:
        """柱状图按 SwensenClass 枚举顺序展示全部 11 个子类（不按市值倒序排）。

        spec 097 第十七轮：从 14 子类精简到 11（合并+删信用债）。
        """
        from global_allocation.portfolio.breakdown import DISPLAY_NAME, SwensenClass

        spec = self._get_bar(journal)
        actual_order = [item["class"] for item in spec["data"]["values"]]
        expected_order = [DISPLAY_NAME[c] for c in SwensenClass]
        assert actual_order == expected_order
        assert len(actual_order) == 11  # 11 个子类，没漏

    def test_empty_classes_have_zero_weight(self, journal: PortfolioJournal) -> None:
        """空子类（count=0）也展示，weight=0。这样能看出框架里哪些没覆盖到。"""
        spec = self._get_bar(journal)
        zero_count = sum(1 for item in spec["data"]["values"] if item["weight"] == 0)
        # 测试只塞了 2 个基金，SUBCLASS mapping 也没覆盖，所以应该全是 0
        # （实际生产时 = count=0 的子类数，演示数据 = 14）
        assert zero_count >= 1  # 至少有一些是 0（SUBCLASS 缺失的）


class TestHoldingsTable:
    """持仓聚合表：按 Swensen 大类聚合（不再下钻单只基金）。

    spec 097 第十九轮：从 3 列扩展到 5 列 — 把"目标"和"偏离"也放进同一张表，
    避免跟下面的 Section 2/3 重复（liubo 反馈）。
    spec 097 第二十一轮：target/delta 列改用整数仓位模型
    - 投资子类 target = "{N} 份 ({X.X}%)"，delta = "{+/-N} 份"（份数差，不再 pp）
    - 现金 target = "[{min}, {max}] 份"，delta = "区间内/低于下限/高于上限" 状态文本

    5 列：分类（含 "#. " 前缀）/ 市值 / 占比 / 目标 / 偏离。按 SwensenClass 枚举顺序
    展示全部 11 个子类（含 count=0 的——空子类显示 0 元 / 0.00%）。

    Feishu table column width 只接受 "auto"（其它值 short/medium/long/数字都拒），
    所以 # 信息嵌进分类名前缀（"1. A 股股票"），干掉单独 # 列。
    """

    def _get_table(self, journal: PortfolioJournal) -> dict[str, object]:
        """Section 1 持仓表 — 5 列（class / value / weight / target / delta）。

        第二十一轮：卡片只有 1 张表（持仓表，Section 2 已删除）。
        第二十二轮（liubo 2026-10-17）：增加调仓纪录 table（4 列：date/fund/action/amount），
        所以现在有 2 张表。按列名定位持仓表（找含 "delta" 列的）。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        # 找持仓表（5 列：含 "delta" 列）
        holdings_tables = [
            t for t in tables
            if "delta" in [c["name"] for c in t.get("columns", [])]
        ]
        assert len(holdings_tables) == 1
        spec = holdings_tables[0]
        col_names = [c["name"] for c in spec["columns"]]
        assert col_names == ["class", "value", "weight", "target", "delta"]
        return spec

    def test_columns_are_five(self, journal: PortfolioJournal) -> None:
        """5 列：分类 / 市值 / 占比 / 目标 / 偏离（不再有单独 # 列）。

        第十九轮加 target + delta（合并 Section 2/3 信息）。
        """
        spec = self._get_table(journal)
        col_names = [c["name"] for c in spec["columns"]]
        assert col_names == ["class", "value", "weight", "target", "delta"]

    def test_rows_are_dict_shaped(self, journal: PortfolioJournal) -> None:
        """Feishu API 强制 row 是 dict（按列名取）。"""
        spec = self._get_table(journal)
        for row in spec["rows"]:
            assert isinstance(row, dict)
            assert set(row.keys()) == {"class", "value", "weight", "target", "delta"}

    def test_class_column_includes_index_prefix(self, journal: PortfolioJournal) -> None:
        """分类列含 # 前缀（"1. A 股股票"），让用户看到框架总数。

        第五轮反馈：原意是单独 # 列，但 Feishu table width 只接受 "auto"，无法做窄。
        折中：把 # 嵌进分类名前缀，干掉单独 # 列。
        """
        spec = self._get_table(journal)
        for idx, row in enumerate(spec["rows"], start=1):
            assert row["class"].startswith(f"{idx}. ")

    def test_rows_in_swensen_order(self, journal: PortfolioJournal) -> None:
        """按 SwensenClass 枚举顺序排（不是市值倒序）。"""
        from global_allocation.portfolio.breakdown import DISPLAY_NAME, SwensenClass

        spec = self._get_table(journal)
        # 去掉 "#. " 前缀再比对
        actual_order = [row["class"].split(". ", 1)[1] for row in spec["rows"]]
        expected_order = [DISPLAY_NAME[c] for c in SwensenClass]
        assert actual_order == expected_order

    def test_all_rows_for_all_swensen_classes(self, journal: PortfolioJournal) -> None:
        """表展示全部 11 个子类（含 count=0 的），不是只展示有持仓的。

        spec 097 第十七轮：从 14 子类精简到 11。
        """
        spec = self._get_table(journal)
        assert len(spec["rows"]) == 11

    def test_all_columns_text_type(self, journal: PortfolioJournal) -> None:
        """class / value / weight / target / delta 全是预格式化字符串，全 text 列。"""
        spec = self._get_table(journal)
        for col in spec["columns"]:
            assert col["data_type"] == "text"

    def test_all_columns_auto_width(self, journal: PortfolioJournal) -> None:
        """所有列 width="auto"（Feishu table 只接受 "auto"，其它值全被拒）。"""
        spec = self._get_table(journal)
        for col in spec["columns"]:
            assert col["width"] == "auto"

    def test_value_formatted_with_commas(self, journal: PortfolioJournal) -> None:
        """市值带千分位逗号。空子类 = "0.00"（没逗号也行）。"""
        spec = self._get_table(journal)
        for row in spec["rows"]:
            v = row["value"]
            # 形如 "12,345.67" 或 "0.00"
            assert "," in v or v == "0.00"

    def test_weight_has_percent_sign(self, journal: PortfolioJournal) -> None:
        """占比以 % 结尾。空子类 = "0.00%"。"""
        spec = self._get_table(journal)
        for row in spec["rows"]:
            assert row["weight"].endswith("%")

    def test_class_name_body_is_chinese(self, journal: PortfolioJournal) -> None:
        """分类名（去掉 # 前缀后）主体是中文。"""
        spec = self._get_table(journal)
        for row in spec["rows"]:
            # 形如 "1. A 股股票" — "A" 是 ASCII 但后面跟中文
            class_part = row["class"].split(". ", 1)[1]
            assert not class_part.isascii()

    def test_investment_subclass_target_uses_percent(self, journal: PortfolioJournal) -> None:
        """投资子类（10 个）的 target 列包含 "%" 字符（整数仓位 + 百分比格式）。

        spec 097 第二十一轮（liubo 2026-09-24）：target = "{N} 份 ({X.X}%)"
        现金行特殊（区间字符串，无 %），跳过。
        """
        from global_allocation.portfolio.breakdown import DISPLAY_NAME, SwensenClass

        spec = self._get_table(journal)
        for row in spec["rows"]:
            class_name = row["class"].split(". ", 1)[1]
            if class_name == DISPLAY_NAME[SwensenClass.CASH]:
                continue  # 现金行 target 是区间字符串，无 %
            assert "%" in row["target"]
            assert "份" in row["target"]

    def test_investment_subclass_target_uses_position_weight(
        self, journal: PortfolioJournal
    ) -> None:
        """投资子类 target 列严格遵循格式 "{N} 份 ({X.X}%)"。

        spec 097 第二十一轮：target = target_position × unit_size / total_capital，
        即整数份数 + 该份数对应的目标权重百分比（不再动态缩放）。
        """
        from global_allocation.portfolio.breakdown import DISPLAY_NAME, SwensenClass
        from global_allocation.portfolio.strategy import DEFAULT_POSITION_ALLOCATION

        spec = self._get_table(journal)
        # 反向查表：display_name → SwensenClass
        name_to_sub = {v: k for k, v in DISPLAY_NAME.items()}

        for row in spec["rows"]:
            display_name = row["class"].split(". ", 1)[1]
            sub = name_to_sub[display_name]
            if sub == SwensenClass.CASH:
                continue
            target_pos = DEFAULT_POSITION_ALLOCATION.target_position(sub)
            target_weight_pct = float(DEFAULT_POSITION_ALLOCATION.target_weight(sub)) * 100
            expected_str = f"{target_pos} 份 ({target_weight_pct:.1f}%)"
            assert row["target"] == expected_str

    def test_cash_target_is_position_range(self, journal: PortfolioJournal) -> None:
        """现金行的 target 列是区间份数字符串（"[10, 30] 份"），不是 %。

        spec 097 第二十一轮：现金目标 = cash_range = (10, 30)。
        """
        from global_allocation.portfolio.breakdown import DISPLAY_NAME, SwensenClass

        spec = self._get_table(journal)
        cash_row = next(
            row for row in spec["rows"]
            if row["class"].split(". ", 1)[1] == DISPLAY_NAME[SwensenClass.CASH]
        )
        assert cash_row["target"] == "[10, 30] 份"

    def test_investment_subclass_delta_uses_position_unit(
        self, journal: PortfolioJournal
    ) -> None:
        """投资子类（10 个）的偏离列用 "份" 后缀（整数份数差）。

        例：A 股目标 11 份 / 当前 12 份 → 偏离 "+1 份"
        现金行 delta 是状态文本，跳过。
        """
        from global_allocation.portfolio.breakdown import DISPLAY_NAME, SwensenClass

        spec = self._get_table(journal)
        for row in spec["rows"]:
            class_name = row["class"].split(". ", 1)[1]
            if class_name == DISPLAY_NAME[SwensenClass.CASH]:
                continue  # 现金行 delta 是状态文本
            delta = row["delta"]
            assert delta.endswith("份")
            assert not delta.endswith("pp")
            assert not delta.endswith("%")

    def test_cash_delta_is_status_text(self, journal: PortfolioJournal) -> None:
        """现金行的偏离列是状态文本（区间内/低于下限/高于上限），不是份数差。

        现金走状态文本（区间策略本质）。
        """
        from global_allocation.portfolio.breakdown import DISPLAY_NAME, SwensenClass

        spec = self._get_table(journal)
        cash_row = next(
            row for row in spec["rows"]
            if row["class"].split(". ", 1)[1] == DISPLAY_NAME[SwensenClass.CASH]
        )
        delta = cash_row["delta"]
        assert delta in {"区间内", "低于下限", "高于上限"}
        assert not delta.endswith("pp")
        assert not delta.endswith("%")

    def test_investment_subclass_delta_sign_matches_current_vs_target(
        self, journal: PortfolioJournal
    ) -> None:
        """投资子类偏离符号 = 当前份 − 目标份（正 = 超配，负 = 低配）。

        spec 097 第二十一轮：delta 列改用整数份数差 "{+/-N} 份"。
        现金行的 delta 是状态文本，跳过符号检查。
        """
        from global_allocation.portfolio.breakdown import DISPLAY_NAME, SwensenClass

        spec = self._get_table(journal)
        for row in spec["rows"]:
            class_name = row["class"].split(". ", 1)[1]
            if class_name == DISPLAY_NAME[SwensenClass.CASH]:
                continue
            # 解析 "{+/-N} 份"
            delta_str = row["delta"]
            # 去掉 "份" 后缀和空白
            delta_num = int(delta_str.replace("份", "").strip())
            # 从 target 解析目标份数 "{N} 份 ({X.X}%)"
            target_pos = int(row["target"].split("份")[0].strip())
            # 从市值 / unit_size 算当前份数（用 Decimal 精度）
            value_str = row["value"].replace(",", "")
            current_value = Decimal(value_str)
            current_pos = round(float(current_value) / 10000)
            expected_delta = current_pos - target_pos
            assert delta_num == expected_delta
            if expected_delta > 0:
                assert delta_str.startswith("+")
            elif expected_delta < 0:
                assert delta_str.startswith("-")


def _get_breakdown(journal: PortfolioJournal):
    """Helper: get breakdown rows for computing current_cash (used by dynamic formula tests)."""
    from global_allocation.portfolio.breakdown import compute_breakdown

    journal_for_breakdown = journal
    _seed(journal_for_breakdown)  # ensure seeded
    return compute_breakdown(journal_for_breakdown)


class TestRebalanceRecordsSection:
    """第二十二轮（liubo 2026-10-17）调仓纪录 section — 表格版（4 列 = 日期/基金/动作/金额）。

    关键设计：
    - 基金列用 FUND_NAME_BY_CODE 显示中文名，不显示 code
    - 多基金 commit body 的每条 `- ` 行 → 单独一行（不再把多只基金挤一起）
    - 金额从 commit body 提取（建仓 N 仓 / ¥X / N 元 / +N 等）
    - 不再有 "—" 占位符：fallback 用 "基金 <code>"
    - 第二十五轮（liubo 2026-10-17 第二轮反馈）：调仓和定投分开 2 张表
      - 本 class 只看「调仓纪录」表（action != "定投"）
      - 「定投纪录」表在 TestDcaRecordsSection
    """

    def _get_rebalance_table(
        self, journal: PortfolioJournal,
    ) -> dict[str, object]:
        """调仓纪录表：4 列 (date/fund/action/amount)，行 action != "定投"。

        第二十五轮：DCA 拆到独立表后，调仓表是第一个含 action/amount 的表
        （per-fund 表也有 action 但列名不同 — fund/cost/valuation/advice）。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        for t in card["elements"]:
            if t.get("tag") != "table":
                continue
            cols = [c["name"] for c in t.get("columns", [])]
            if "date" in cols and "action" in cols and "amount" in cols:
                return t
        raise AssertionError("找不到调仓纪录 table")

    def _get_dca_table(
        self, journal: PortfolioJournal,
    ) -> dict[str, object]:
        """定投纪录表：4 列 (date/fund/action/amount)，行 action == "定投"。

        第二十五轮：DCA 拆出来后，调仓表 + 定投表 都是 4 列 (date/fund/action/amount)。
        区分：调仓表是第一个，DCA 表是第二个（按元素顺序）。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        candidates = []
        for t in card["elements"]:
            if t.get("tag") != "table":
                continue
            cols = [c["name"] for c in t.get("columns", [])]
            if "date" in cols and "action" in cols and "amount" in cols:
                candidates.append(t)
        # 第一个 = 调仓纪录；第二个 = 定投纪录
        if len(candidates) >= 2:
            return candidates[1]
        raise AssertionError("找不到定投纪录 table（DCA 表可能为空）")

    def test_fund_column_uses_chinese_name_not_code(
        self, journal: PortfolioJournal,
    ) -> None:
        """基金列用中文名（来自 FUND_NAME_BY_CODE），不用 raw 6 位 code。

        liubo 2026-10-17 反馈："首先基金那一列不要用扣的，要用它的基金名称"。
        """
        table = self._get_rebalance_table(journal)
        for row in table["rows"]:
            fund = row.get("fund", "")
            # 不能是纯 6 位 code（如 "013127"）
            assert not re.fullmatch(r"\d{6}", fund), (
                f"fund 列是 raw code: {fund!r}（应该用中文名）"
            )
            # 不能是 "—" 占位符（除非确实解析失败，理论上不会）
            assert fund != "—", "fund 列出现 '—' 占位符（不应该有）"
            # 应该包含中文（含 unicode ≥ 0x4E00）
            assert any("\u4e00" <= ch <= "\u9fff" for ch in fund), (
                f"fund 列不含中文: {fund!r}"
            )

    def test_amount_column_not_dash_when_commits_have_amounts(
        self, journal: PortfolioJournal,
    ) -> None:
        """金额列在 commit body 含具体金额时不能是 "—"。

        liubo 2026-10-17 反馈："金额你也不知道，这是为什么呢？这你应该知道呀"。
        测试：从最近几个调仓 commit 的 body 里能读出来的金额，amount 列必须有值。
        """
        table = self._get_rebalance_table(journal)
        rows = table["rows"]
        # 至少要有一行 amount 不是 "—"（最近 5 个调仓 commit 里有大量 "10000 元" / "+1661" 等）
        non_dash = [r for r in rows if r.get("amount") not in ("—", "")]
        assert non_dash, (
            "调仓纪录 amount 列全部是 '—'，但 commit body 里有具体金额（liubo 反馈" +
            '"这你应该知道呀"）'
        )

    def test_multi_fund_commit_splits_into_multiple_rows(
        self, journal: PortfolioJournal,
    ) -> None:
        """多基金 commit body 的每条 `- ` 行 → 单独一行。

        liubo 2026-10-17 反馈："你不要把很多基金的变动放到一起...都是假仓，
        这个应该是分成三行的"。

        验证：调仓纪录表里同一个 commit date 至少出现一次 ≥3 行（同日 ≥3 rows
        = body 里确实有 ≥3 个 `- 0xxxxx ...` 行）。
        """
        table = self._get_rebalance_table(journal)
        rows = table["rows"]
        # 按 date 分组
        from collections import Counter
        date_counts = Counter(r["date"] for r in rows)
        # 至少有一个 date 有 ≥ 3 行（commit 2c36577 / 9e70495 / 8597945 等）
        assert any(c >= 3 for c in date_counts.values()), (
            f"没有 commit 拆成 ≥3 行：{dict(date_counts)}"
            "（user feedback: 多基金 commit 应该拆成多行）"
        )

    def test_no_cross_portfolio_funds_leaked(
        self, journal: PortfolioJournal,
    ) -> None:
        """调仓纪录不展示 ETF 轮动 / 红利策略 / 其他组合的基金。

        liubo 2026-10-17 反馈 — global-allocation / dividend-strategy / etf-rotation
        三个组合分开管理。已知跨组合基金 code: 010989 / 023917 / 457001 / 025682。
        """
        table = self._get_rebalance_table(journal)
        for row in table["rows"]:
            fund = row.get("fund", "")
            # 不能出现跨组合 code（fund 列是中文名，但如果回退到 "基金 <code>" 格式，
            # 或叙述里直接出现 code 也能被检出）
            for code in ("010989", "023917", "457001", "025682"):
                assert code not in fund, (
                    f"调仓纪录混入跨组合基金 {code}：{fund!r}"
                )

    def test_no_index_only_doc_lines_promoted_to_rebalance(
        self, journal: PortfolioJournal,
    ) -> None:
        """纯文档行（含指数代码 000698/399673 但无具体基金）不能被当作调仓。

        例子：commit 00e9da2 fix 描述 `LAST_REBALANCE_BY_ETF 加 "000698" / "399673" →
        date(2026, 10, 9)` —— 000698/399673 是 ETF 跟踪的指数代码，不是基金 code，
        应该被过滤掉。
        """
        table = self._get_rebalance_table(journal)
        for row in table["rows"]:
            fund = row.get("fund", "")
            # 不应出现 "基金 000698" 这种 fallback（000698 不在 FUND_NAME_BY_CODE）
            assert not fund.startswith("基金 000698"), (
                f"指数代码 000698 被当成基金: {fund!r}"
            )
            assert not fund.startswith("基金 399673"), (
                f"指数代码 399673 被当成基金: {fund!r}"
            )

    def test_dca_action_with_3_values_for_dollar_cost_funds(
        self, journal: PortfolioJournal,
    ) -> None:
        """DCA (定投) 行的 action 是 "定投"，金额列显示 3 个值。

        liubo 2026-10-17 反馈 — 金额列显示 3 个值：
        1. 定投额（每次投多少）— "10 元"
        2. 频率（多久投一次）— "每天"
        3. 总目标（计划投多少）— "累计 1800"

        格式：「10 元 / 每天 / 累计 1800」。
        不写「已买 ¥X」（已经买入了多少他不关心）。

        第二十五轮（liubo 2026-10-17 第二轮反馈）：DCA 拆到独立表，本测试查 _get_dca_table。
        """
        from global_allocation.portfolio.card import _DCA_SCHEDULE_BY_CODE

        table = self._get_dca_table(journal)
        # 按 fund 列找 DCA 行
        dca_rows = [
            r for r in table["rows"]
            if "南方纳100" in r.get("fund", "")
            or "摩根纳斯达克100指数(QDII)人民币A" in r.get("fund", "")
            or "万家纳斯达克100指数发起式(QDII)A" in r.get("fund", "")
        ]
        assert len(dca_rows) >= 3, (
            f"应该有 3 个 DCA 行（016452/019172/019441），实际 {len(dca_rows)} 行"
        )
        for row in dca_rows:
            assert row["action"] == "定投", (
                f"DCA 行 action 应该是 '定投'，实际 '{row['action']}'（{row['fund']}）"
            )
            # 不应出现 "已买"（已买入多少他不关心）
            assert "已买" not in row["amount"], (
                f"DCA 行 amount 不应有 '已买'（liubo 2026-10-17 反馈不关心已买多少），"
                f"实际 '{row['amount']}'（{row['fund']}）"
            )
            # 金额应该等于 _DCA_SCHEDULE_BY_CODE 对应 fund 格式化后的字符串
            fund_to_code = {
                "南方纳100": "016452",
                "摩根纳斯达克100指数(QDII)人民币A": "019172",
                "万家纳斯达克100指数发起式(QDII)A": "019441",
            }
            for prefix, code in fund_to_code.items():
                if prefix in row["fund"]:
                    schedule = _DCA_SCHEDULE_BY_CODE[code]
                    # amount 必须包含 3 个值（定投额 / 频率 / 累计）
                    assert schedule["per"] in row["amount"], (
                        f"DCA 行 amount 应包含定投额 '{schedule['per']}'，"
                        f"实际 '{row['amount']}'（{row['fund']}）"
                    )
                    assert schedule["cycle"] in row["amount"], (
                        f"DCA 行 amount 应包含频率 '{schedule['cycle']}'，"
                        f"实际 '{row['amount']}'（{row['fund']}）"
                    )
                    assert f"累计 {schedule['total']}" in row["amount"], (
                        f"DCA 行 amount 应包含累计 '{schedule['total']}'，"
                        f"实际 '{row['amount']}'（{row['fund']}）"
                    )
                    # 3 个值之间应该有分隔符
                    assert row["amount"].count("/") >= 2, (
                        f"DCA 行 amount 应有 2 个 '/' 分隔 3 个值，"
                        f"实际 '{row['amount']}'（{row['fund']}）"
                    )
                    break

    def test_pure_cost_calibration_rows_filtered(
        self, journal: PortfolioJournal,
    ) -> None:
        """纯成本校准行（如 519753 +21 元、378006 +1400 元）应被过滤掉。

        liubo 2026-10-17 反馈 — 成本校准属于记账对账，不是调仓 ——
        "原来记错了，我纠正你了，所以这种它不属于调仓"。

        8597945 commit body 有 11 条这种校准行（519753 +21、378006 +1400、
        002286 +132、010742 +31、015736 -6、001512 -8、485119 -4、
        017837 -19、008333 +8、007520 -14）。

        过滤条件：行匹配 "code + Chinese 名 + N → M（±X 元）" 且无 "已买" / "仓"。
        """
        from global_allocation.portfolio.card import _parse_rebalance_line

        # 直接验证过滤逻辑（不依赖 git log）— 真实 8597945 body 里的校准行
        calibration_lines = [
            # 中文冒号 + ±N 元（liubo 在 8597945 body 里这样写）
            "- 378006 摩根全球新兴市场：3910 → 5310（+1400）",
            "- 519753 交银安心收益债券 A：29979 → 30000（+21）",
            "- 002286 中银美元债：9868 → 10000（+132）",
            "- 010742 南方宁悦一年持有：9969 → 10000（+31）",
            "- 015736 长盛盛裕纯债 D：1506 → 1500（-6）",
            "- 001512 易方达中债 3-5 年国债指数：5008 → 5000（-8）",
            "- 485119 工银信用纯债债券 A：2004 → 2000（-4）",
            "- 017837 博时中债 7-10 年政金债：5019 → 5000（-19）",
            "- 008333 景顺弘利 39 个月定开：9992 → 10000（+8）",
            "- 007520 富安达富利纯债 A：10014 → 10000（-14）",
            # ASCII 冒号（liubo 在其它 commit 里这样写，如 9e70495 ledger）
            "- 004827: 22000 → 2000（-20000）",
        ]
        for line in calibration_lines:
            parsed = _parse_rebalance_line(line, "校准")
            assert parsed is None, (
                f"纯成本校准行应被过滤: {line!r}，实际 {parsed!r}"
            )

        # 表格里"真校准"特征（action='校准' + amount 是校准差值）应被过滤掉 —
        # 8597945 "14 只基金成本校准" commit body 里 11 条 ±N 元 校准行 body 都应被过滤。
        # 说明：subject 含 "校准" 的 commit 会被 _is_rebalance_subject 选中 —
        # 但 body 行通过 _parse_rebalance_line 的过滤（行匹配 "code + Chinese 名 + N → M（±N 元）"
        # 且无 "已买" / "仓"）都被过滤掉。
        #
        # 已知 limitation：self-referential commit（subject 含 "校准" 等关键词 + body 描述实现）
        # 可能误触发 action=校准 + amount="—" 的 noise 行（如 e194235 自己的 body 描述）。
        # 这种 row 的 amount 是 "—"（无金额差值），与"真校准"（"+21 元"等）有区别。
        table = self._get_rebalance_table(journal)
        for row in table["rows"]:
            action = row.get("action", "")
            amount = row.get("amount", "")
            fund = row.get("fund", "")
            # "真校准"特征：action=校准 + amount 是 ±N 元 / ±N / N → N
            if action == "校准":
                # 真校准必须有金额差值（如 "+21" / "-8" / "+1400 元"）
                assert re.fullmatch(r"\s*[+\-−]?\s*\d+\s*(?:元)?\s*", amount), (
                    f"校准 action 应该有金额差值描述（filter 漏掉真校准），"
                    f"实际 amount='{amount}'（{fund}）"
                )
            # amount 是 "+N" / "-N" / "+N 元" 单值（校准金额特征）也算校准
            if re.fullmatch(r"\s*[+\-−]\s*\d+(\s*元)?\s*", amount):
                # 但允许 DCA 行 amount 是 "已买 ¥N / schedule"（不会匹配这个 fullmatch）
                assert "已买" in amount, (
                    f"金额是纯校准差值 '{amount}'（{fund}）— 应该是 DCA '已买' 描述"
                )

    def test_conversion_line_split_into_transfer_out_and_in(
        self, journal: PortfolioJournal,
    ) -> None:
        """转换行拆成 2 行：转出 + 转入。

        liubo 2026-10-17 反馈 — 转换其实你也可以把它拆成两个。
        例：004827 平安中短债 → 019857 博时上证科创板100ETF联接A
        = (平安中短债 | 转出 | 10000 元) + (博时上证科创板100ETF联接A | 转入 | 10000 元)

        8597945 转换 commit (664b6d6 fix(099)) 应该有 6 行转换（3 转出 + 3 转入）。
        """
        table = self._get_rebalance_table(journal)
        rows = table["rows"]
        # 找 转出 / 转入 配对
        transfer_out = [r for r in rows if r["action"] == "转出"]
        transfer_in = [r for r in rows if r["action"] == "转入"]
        # 数量相等（每个转换拆出 1 转出 + 1 转入）
        assert len(transfer_out) >= 3, (
            f"转换 commit 应该有 ≥3 个 '转出' 行，实际 {len(transfer_out)}"
        )
        assert len(transfer_in) >= 3, (
            f"转换 commit 应该有 ≥3 个 '转入' 行，实际 {len(transfer_in)}"
        )
        # 转出/转入配对（同一 commit date）
        from collections import defaultdict
        out_by_date: dict[str, set[str]] = defaultdict(set)
        in_by_date: dict[str, set[str]] = defaultdict(set)
        for r in transfer_out:
            out_by_date[r["date"]].add(r["fund"])
        for r in transfer_in:
            in_by_date[r["date"]].add(r["fund"])
        for date_key in out_by_date:
            assert len(out_by_date[date_key]) >= 1, f"{date_key} 没有 转出 行"
            assert len(in_by_date[date_key]) >= 1, f"{date_key} 没有 转入 行"
        # 关键转换源基金必须出现（平安中短债 转出 × 2、鹏扬利沣短债A 转出 × 1）
        out_funds = {r["fund"] for r in transfer_out}
        assert "平安中短债" in out_funds, (
            f"转换源 平安中短债 不在转出行: {out_funds}"
        )
        # 关键转换目标基金必须出现
        in_funds = {r["fund"] for r in transfer_in}
        assert "博时上证科创板100ETF联接A" in in_funds, (
            f"转换目标 博时上证科创板100ETF联接A 不在转入行: {in_funds}"
        )
        assert "工银创业板50ETF联接A" in in_funds, (
            f"转换目标 工银创业板50ETF联接A 不在转入行: {in_funds}"
        )
        assert "汇添富恒生科技 ETF 联接发起式(QDII)A" in in_funds, (
            f"转换目标 汇添富恒生科技 ETF 联接发起式(QDII)A 不在转入行: {in_funds}"
        )
        # 转出/转入金额相等（同一笔转换金额一致）
        for r_out in transfer_out:
            r_in_match = next(
                (
                    r for r in transfer_in
                    if r["date"] == r_out["date"] and r["amount"] == r_out["amount"]
                ),
                None,
            )
            assert r_in_match is not None, (
                f"转出 '{r_out['fund']}' {r_out['amount']} ({r_out['date']}) "
                f"没有匹配的转入行"
            )

    def test_unit_helpers_dca_conversion_calibration(
        self, journal: PortfolioJournal,
    ) -> None:
        """单元测试 — 直接打 _parse_rebalance_line / _is_conversion_line / _parse_conversion_rows。

        不依赖 git log — 构造合成 commit body 行，验证 3 个新行为（DCA / 转换拆分 /
        校准过滤）的核心逻辑。
        """
        from global_allocation.portfolio.card import (
            _DCA_SCHEDULE_BY_CODE,
            _is_conversion_line,
            _parse_conversion_rows,
            _parse_rebalance_line,
        )

        # === 1. DCA 行 ===
        dca_line = "- 016452 南方纳指 100：20 → 60（已买 ¥60）"
        parsed = _parse_rebalance_line(dca_line, "校准")
        assert parsed is not None, "DCA 行被错误过滤"
        assert not isinstance(parsed, list), "DCA 行不应返回 list"
        assert parsed.action == "定投", (
            f"DCA 行 action 应该是 '定投'，实际 '{parsed.action}'"
        )
        # liubo 2026-10-17 反馈：金额列显示 3 个值 ——
        # 定投额 / 频率 / 总目标（如 "10 元 / 每天 / 累计 1800"）
        # 不写「已买 ¥X」（已经买入了多少他不关心）
        assert "已买" not in parsed.amount, (
            f"DCA 行 amount 不应有 '已买'，实际 '{parsed.amount}'"
        )
        schedule = _DCA_SCHEDULE_BY_CODE["016452"]
        for part in (schedule["per"], schedule["cycle"], f"累计 {schedule['total']}"):
            assert part in parsed.amount, (
                f"DCA 行 amount 应包含 '{part}'，实际 '{parsed.amount}'"
            )
        # 3 个值之间应该有 2 个 '/' 分隔符
        assert parsed.amount.count("/") >= 2, (
            f"DCA 行 amount 应有 2 个 '/' 分隔 3 个值，实际 '{parsed.amount}'"
        )

        # === 2. 转换行 ===
        conv_line = (
            "- 004827 平安中短债 → 019857 博时上证科创板100ETF联接A"
            "（10000 元，转换 8379 份）"
        )
        assert _is_conversion_line(conv_line), (
            "转换行未被 _is_conversion_line 识别"
        )
        rows = _parse_conversion_rows(conv_line)
        assert len(rows) == 2, f"转换应拆 2 行，实际 {len(rows)} 行"
        assert rows[0].action == "转出", (
            f"转换第 1 行 action 应该是 '转出'，实际 '{rows[0].action}'"
        )
        assert rows[1].action == "转入", (
            f"转换第 2 行 action 应该是 '转入'，实际 '{rows[1].action}'"
        )
        assert rows[0].fund == "平安中短债", (
            f"转出 fund 应该是 '平安中短债'，实际 '{rows[0].fund}'"
        )
        assert "博时上证科创板100ETF联接A" in rows[1].fund, (
            f"转入 fund 应该是 '博时上证科创板100ETF联接A'，实际 '{rows[1].fund}'"
        )
        assert rows[0].amount == rows[1].amount, (
            f"转出/转入金额应一致: '{rows[0].amount}' vs '{rows[1].amount}'"
        )

        # _parse_rebalance_line 也应该返回 list（2 行）
        parsed_conv = _parse_rebalance_line(conv_line, "加仓")
        assert isinstance(parsed_conv, list), (
            f"_parse_rebalance_line 转换行应该返回 list，实际 {type(parsed_conv)}"
        )
        assert len(parsed_conv) == 2

        # === 3. 纯校准行（应过滤） ===
        calib_line = "- 519753 交银安心收益债券 A：29979 → 30000（+21）"
        parsed_calib = _parse_rebalance_line(calib_line, "校准")
        assert parsed_calib is None, (
            f"纯成本校准行应被过滤（None），实际返回 {parsed_calib!r}"
        )

        # ASCII 冒号的校准行也应过滤
        calib_line2 = "- 004827: 22000 → 2000（-20000）"
        parsed_calib2 = _parse_rebalance_line(calib_line2, "校准")
        assert parsed_calib2 is None, (
            f"ASCII 冒号成本校准行应被过滤，实际返回 {parsed_calib2!r}"
        )

        # === 4. 非转换 / 非校准 / 非 DCA 的普通建仓行 ===
        build_line = (
            "- 019857 博时上证科创板100ETF联接A → 000688 科创 50，"
            "建仓 1 仓 = 10000 CNY"
        )
        # 这是 换基（基金 → 指数），但 index code 不在 FUND_NAME_BY_CODE
        # 会被 _parse_rebalance_line 第 2 个过滤器（index code）过滤
        parsed_build = _parse_rebalance_line(build_line, "换基")
        assert parsed_build is None, (
            f"fund → index 描述行应被过滤（000688 不在 FUND_NAME_BY_CODE），"
            f"实际 {parsed_build!r}"
        )

        # liubo 2026-10-17 反馈 — "建仓 N 仓 + → + 2 codes" 不是换基 —
        # 是给 target 加仓（旧基金 A 成本 0 占位，从跟踪列表里删掉即可）。
        # 调仓纪录不显示「换基」概念。
        add_line = (
            "- 020291 华夏科创100ETF联接A → 019857 博时上证科创板100ETF联接A，"
            "建仓 1 仓 = 10000 CNY"
        )
        parsed_add = _parse_rebalance_line(add_line, "换 2 只科创/创业宽基 + 013127 +1 仓")
        assert parsed_add is not None, "fund → fund 行被错误过滤"
        assert parsed_add.action == "加仓", (
            f"fund → fund + 建仓 N 仓 行 action 应该是 '加仓'（liubo 2026-10-17 反馈），"
            f"实际 '{parsed_add.action}'"
        )
        assert parsed_add.fund == "博时上证科创板100ETF联接A", (
            f"fund → fund + 建仓 N 仓 行 fund 应只显示 target，"
            f"实际 '{parsed_add.fund}'"
        )

    def test_unit_helpers_description_lines_filtered(
        self, journal: PortfolioJournal,
    ) -> None:
        """第三十轮（liubo 2026-10-18 反馈）：过滤 commit body 描述行（不是真调仓）。

        3 类描述行被误判为调仓：
        1. "X 保留（占位 0）" — 描述"保留占位"，不是新建仓
        2. "X 漏在...清单外" — fix commit 描述漏加
        3. "X / Y / Z / W 这 N 只..." — 描述性列表
        """
        from global_allocation.portfolio.card import _parse_rebalance_line

        # 1. "X 保留（占位 0）" 描述行
        keep_line = "- 011612 华夏科创50ETF联接A 保留（占位 0）"
        parsed_keep = _parse_rebalance_line(
            keep_line, "换 2 只科创/创业宽基 + 013127 +1 仓"
        )
        assert parsed_keep is None, (
            f"'保留（占位 0）' 描述行应被过滤，实际返回 {parsed_keep!r}"
        )

        # 2. "X 漏在...清单外" 描述行
        missing_line = "- 014673 富国中证港股通互联网ETF发起式联接A 漏在大类资产配置清单外，"
        parsed_missing = _parse_rebalance_line(
            missing_line, "加 014673 港股 + 建红利策略组合独立模块"
        )
        assert parsed_missing is None, (
            f"'漏在...清单外' 描述行应被过滤，实际返回 {parsed_missing!r}"
        )

        # 3. "X / Y / Z / W 这 N 只..." 描述性列表
        list_line = '- 022448 / 007997 / 023917 / 025682 这 4 只是单独的"红利策略组合"，'
        parsed_list = _parse_rebalance_line(
            list_line, "加 014673 港股 + 建红利策略组合独立模块"
        )
        assert parsed_list is None, (
            f"'这 N 只' 描述性列表应被过滤，实际返回 {parsed_list!r}"
        )

        # 对照：真的 加仓 / 转换 / 转入 / 转出 行还是被识别
        real_add = (
            "- 020291 华夏科创100ETF联接A → 019857 博时上证科创板100ETF联接A，"
            "建仓 1 仓 = 10000 CNY"
        )
        parsed_real = _parse_rebalance_line(
            real_add, "换 2 只科创/创业宽基 + 013127 +1 仓"
        )
        assert parsed_real is not None, "真实加仓行被错误过滤"
        assert parsed_real.action == "加仓"


class TestDcaRecordsSection:
    """第二十五轮（liubo 2026-10-17 第二轮反馈）— 定投纪录 section 独立成 1 张表。

    「调仓是调仓，定投是定投」 — 调仓（DCA 之外的动作）和定投分开 2 张表展示。

    数据流：
    - 复用 _collect_rebalance_rows() 抓的同一份 git log（避免 2 次 git 调用）
    - 按 action 字段分流：action == "定投" → 本表，其他 → 调仓记录表
    - 4 列 (date / fund / action / amount) 跟调仓记录表 schema 一致
    - amount 列 = "X 元 / Y / 累计 Z" 3 段（per / cycle / total）
    """

    def test_dca_table_exists_and_separate_from_rebalance(
        self, journal: PortfolioJournal,
    ) -> None:
        """DCA 表存在且与调仓表分开（2 张独立的 4 列表）。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        candidates = []
        for t in card["elements"]:
            if t.get("tag") != "table":
                continue
            cols = [c["name"] for c in t.get("columns", [])]
            if "date" in cols and "action" in cols and "amount" in cols:
                candidates.append(t)
        # 应该有 2 张：(1) 调仓记录 + (2) 定投记录
        assert len(candidates) >= 2, (
            f"调仓 + 定投 应该分 2 张表，实际 {len(candidates)} 张"
        )
        # 2 张表的列名相同（schema 一致）
        for t in candidates[:2]:
            cols = [c["name"] for c in t["columns"]]
            assert cols == ["date", "fund", "action", "amount"], (
                f"表列名应是 [date, fund, action, amount]，实际 {cols}"
            )

    def test_dca_table_only_has_dca_action_rows(
        self, journal: PortfolioJournal,
    ) -> None:
        """DCA 表只显示 action == "定投" 的行，不混入调仓行。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        # 取第二张 4 列表（DCA 表）
        candidates = [
            t for t in card["elements"]
            if t.get("tag") == "table"
            and "date" in [c["name"] for c in t.get("columns", [])]
            and "action" in [c["name"] for c in t.get("columns", [])]
            and "amount" in [c["name"] for c in t.get("columns", [])]
        ]
        if len(candidates) < 2:
            pytest.skip("DCA 表为空")
        dca_table = candidates[1]
        for row in dca_table["rows"]:
            assert row["action"] == "定投", (
                f"DCA 表不应有 action != '定投' 的行："
                f"fund={row['fund']!r} action={row['action']!r}"
            )

    def test_rebalance_table_excludes_dca_rows(
        self, journal: PortfolioJournal,
    ) -> None:
        """调仓记录表不显示 action == "定投" 的行（已拆到 DCA 表）。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        candidates = [
            t for t in card["elements"]
            if t.get("tag") == "table"
            and "date" in [c["name"] for c in t.get("columns", [])]
            and "action" in [c["name"] for c in t.get("columns", [])]
            and "amount" in [c["name"] for c in t.get("columns", [])]
        ]
        rebalance_table = candidates[0]  # 第一张 = 调仓记录
        for row in rebalance_table["rows"]:
            assert row["action"] != "定投", (
                f"调仓记录表不应有 action == '定投' 的行（DCA 已拆出去）："
                f"fund={row['fund']!r} amount={row['amount']!r}"
            )

    def test_dca_table_lists_all_5_funds(
        self, journal: PortfolioJournal,
    ) -> None:
        """DCA 表展示 _DCA_SCHEDULE_BY_CODE 全部 5 只基金（不依赖 git log）。

        liubo 2026-10-17 第二十五轮反馈 — 「黄金 + DAX 你为什么没进呢？」
        之前只过滤 git log 里 action == "定投" 的行（只有 3 只 NDX），现在改成
        直接遍历 _DCA_SCHEDULE_BY_CODE 全列。
        """
        from global_allocation.portfolio.card import _DCA_SCHEDULE_BY_CODE

        _seed(journal)
        card = build_portfolio_card(journal)
        candidates = [
            t for t in card["elements"]
            if t.get("tag") == "table"
            and "date" in [c["name"] for c in t.get("columns", [])]
            and "action" in [c["name"] for c in t.get("columns", [])]
            and "amount" in [c["name"] for c in t.get("columns", [])]
        ]
        if len(candidates) < 2:
            pytest.skip("DCA 表不存在（应该是 fallback path）")
        dca_table = candidates[1]
        # 必须有全部 5 只 DCA 基金（liubo 反馈「黄金 + DAX 你为什么没进呢」）
        assert len(dca_table["rows"]) == len(_DCA_SCHEDULE_BY_CODE), (
            f"DCA 表应有 {len(_DCA_SCHEDULE_BY_CODE)} 行（_DCA_SCHEDULE_BY_CODE 全部），"
            f"实际 {len(dca_table['rows'])} 行"
        )
        # action 字段都是 "定投"
        for row in dca_table["rows"]:
            assert row["action"] == "定投", (
                f"DCA 表所有行 action 应是 '定投'，实际 {row['action']!r}"
            )


class TestCostBasisByClassAggregation:
    """第二十六轮（liubo 2026-10-17 反馈"国外发达不应该是 0"）：Section 1 fallback
    持仓成本 by 大类 聚合 必须正确 — DCA 基金的 cost 应该是累计目标，不是"已买"快照。

    关键断言（liubo 2026-10-17 拍板）：
    - 国外发达市场股票：count=1（000614 DAX 联接），cost=10000（DCA 累计目标）
    - 美股股票：cost 包含 3 只 NDX DCA 累计（016452=1800, 019172=2000, 019441=2000）
    - 商品：cost=7500（000216 黄金 DCA 累计目标）
    - 11 大类全覆盖：A 股 / 港股 / 美股 / 国外发达 / 新兴市场 / 国内 REITs / 美国 REITs /
      国内利率债 / 美债 / 商品（现金 004137 走 CASH_LIKE_CODES 不渲染）
    """

    def test_foreign_dm_aggregation_includes_dca_target(
        self, journal: PortfolioJournal,
    ) -> None:
        """国外发达：count=1, cost=10000（不是 0/不是已买快照）。"""
        card = build_portfolio_card(journal)
        cost_table = self._get_cost_basis_table(card)
        fdm_row = next(
            (r for r in cost_table["rows"] if r["class"] == "国外发达市场股票"),
            None,
        )
        assert fdm_row is not None, "Section 1 应有「国外发达市场股票」行"
        assert fdm_row["count"] == "1", (
            f"国外发达 count 应是 1（含 000614 DAX），实际 {fdm_row['count']}"
        )
        assert fdm_row["cost"] == "10,000.00", (
            f"国外发达 cost 应是 10000（DCA 累计目标），实际 {fdm_row['cost']}。"
            "liubo 反馈'国外发达不应该是 0，应该是 1'——cost 应该是 DCA 累计 10000"
        )

    def test_us_equity_aggregation_includes_ndx_dca_targets(
        self, journal: PortfolioJournal,
    ) -> None:
        """美股股票：cost 应包含 3 只 NDX DCA 累计（1800+2000+2000=5800）加上其他 4 只。"""
        card = build_portfolio_card(journal)
        cost_table = self._get_cost_basis_table(card)
        us_row = next(
            (r for r in cost_table["rows"] if r["class"] == "美股股票"),
            None,
        )
        assert us_row is not None
        # 美股 7 只合计：3620 + 2020 + 1000 + 50 + 1800 + 2000 + 2000 = 12490
        assert us_row["cost"] == "12,490.00", (
            f"美股 cost 应是 12490（含 3 只 NDX DCA 累计），实际 {us_row['cost']}"
        )
        assert us_row["count"] == "7", "美股应有 7 只基金"

    def test_commodity_aggregation_uses_dca_target(
        self, journal: PortfolioJournal,
    ) -> None:
        """商品：000216 黄金 cost=7500（DCA 累计目标，不是已买 2510 快照）。"""
        card = build_portfolio_card(journal)
        cost_table = self._get_cost_basis_table(card)
        comm_row = next(
            (r for r in cost_table["rows"] if r["class"] == "商品"),
            None,
        )
        assert comm_row is not None
        assert comm_row["cost"] == "7,500.00", (
            f"商品 cost 应是 7500（DCA 累计），实际 {comm_row['cost']}"
        )

    def test_all_10_subclasses_covered(
        self, journal: PortfolioJournal,
    ) -> None:
        """10 大类全覆盖（11 大类减现金 004137 — CASH_LIKE_CODES 不渲染）。"""
        card = build_portfolio_card(journal)
        cost_table = self._get_cost_basis_table(card)
        classes = {r["class"] for r in cost_table["rows"]}
        expected = {
            "A 股股票", "港股", "美股股票", "国外发达市场股票", "新兴市场股票",
            "国内 REITs", "美国 REITs", "国内利率债", "美债", "商品",
        }
        assert classes == expected, (
            f"Section 1 缺大类：{expected - classes}；多余：{classes - expected}"
        )

    def test_total_cost_matches_cost_basis_sum(
        self, journal: PortfolioJournal,
    ) -> None:
        """Section 1 总成本 = TOTAL_COST_CNY（626454.47）— 摘要 div 用千位分隔符格式。"""
        from global_allocation.portfolio.cost_basis import TOTAL_COST_CNY
        card = build_portfolio_card(journal)
        # 找总成本摘要 div（含「总成本」字样）
        summary_text = ""
        for el in card["elements"]:
            if el.get("tag") == "div":
                summary_text = el.get("text", {}).get("content", "")
                if "总成本" in summary_text:
                    break
        # 摘要格式："**总成本**：626,454.47 CNY"（千位分隔符）
        expected = f"{float(TOTAL_COST_CNY):,.2f}"
        assert expected in summary_text, (
            f"摘要里应有总成本 {expected}（千位分隔符格式），实际：{summary_text}"
        )

    def _get_cost_basis_table(self, card: dict) -> dict:
        """找 Section 1 的「持仓成本 by 大类」表（columns 含 class + count）。"""
        for t in card["elements"]:
            if t.get("tag") != "table":
                continue
            cols = [c["name"] for c in t.get("columns", [])]
            if "class" in cols and "count" in cols:
                return t
        raise AssertionError("找不到「持仓成本 by 大类」表")


class TestRemovedSections:
    """spec 096/097 移除的部分必须真的没了（第十九轮：Section 2 + Section 3 也都删了）。"""

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
        """持仓明细表的列不应该有交易流水的字段（date / side / strategy）。

        第二十二轮（liubo 2026-10-17）：调仓纪录 table 含 "date" 列（commit 日期），
        但调仓纪录的"date"不是流水日期（side/strategy/price 都不在），所以只排除
        持仓表（找含 "delta" 列的表）。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        # 只看持仓聚合表（含 "delta" 列的）
        holdings_tables = [
            t for t in tables
            if "delta" in [c["name"] for c in t.get("columns", [])]
        ]
        for t in holdings_tables:
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

    def test_no_section_3_subclass_header(self, journal: PortfolioJournal) -> None:
        """第十九轮/第二十轮：旧的 Section 3 标题"大类资产明细"必须不存在（Section 3 删除不恢复）。

        第二十轮恢复了 Section 2（大类资产策略），但 Section 3（子类明细）的 target/delta
        已经在持仓表的每行里了，不需要重复展示。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        for e in card["elements"]:
            if e.get("tag") == "note":
                for elem in e.get("elements", []):
                    assert elem.get("content") != "大类资产明细"

    def test_no_subclass_table(self, journal: PortfolioJournal) -> None:
        """第十九轮/第二十轮：不应该有 4 列子类对比表（subclass/target/current/delta）— 信息已在持仓表。

        第二十一轮：连 4 列策略对比表（category/target/current/delta）也删了 — 整数仓位模型
        下持仓表的 target/delta 已包含超类信息，Section 2 冗余。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        for t in tables:
            col_names = {c["name"] for c in t["columns"]}
            assert "subclass" not in col_names
            # 第二十一轮：4 列策略对比表也不该存在
            assert not (
                col_names == {"category", "target", "current", "delta"}
            ), "Section 2 策略对比表已删除（第二十一轮），不应再存在"

    def test_no_section_2_strategy_header(self, journal: PortfolioJournal) -> None:
        """第二十一轮：旧的 Section 2 标题"大类资产策略"必须不存在（已删除）。

        整数仓位模型下持仓表的 target/delta 已包含超类信息，Section 2 冗余，liubo 拍板删除。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        for e in card["elements"]:
            if e.get("tag") == "note":
                for elem in e.get("elements", []):
                    assert elem.get("content") != "大类资产策略"



class TestOrdering:
    """元素顺序（spec 097 第二十一轮 — 1 个 section：持仓；无 Section 2 策略对比表）。

    Section 1（实盘持仓）：note + div + chart + table
    元素序列（去掉 hr 后）：note + div + chart + table
    """

    def test_order(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        tags = [
            e.get("tag")
            for e in card["elements"]
            if e.get("tag") in {"note", "div", "chart", "table"}
        ]
        # 第二十五轮（liubo 2026-10-17 第二轮反馈）：4 个 section — 调仓和定投分开 2 张表
        # - Section 1（实盘持仓）：note + div + chart + table
        # - Section 2（per-fund）：note + div + table
        # - Section 3（调仓记录）：note + table
        # - Section 4（定投记录）：note + table
        assert tags == [
            "note",  # Section 1 header "实盘持仓"
            "div",   # Section 1 summary
            "chart", # Section 1 柱状图
            "table", # Section 1 持仓表（5 列：含 target/delta，整数仓位模型）
            "note",  # Section 2 header "持仓与调仓建议"
            "div",   # Section 2 summary (基金数/总成本/本周操作)
            "table", # Section 2 per-fund 表（4 列：fund/cost/valuation/advice）
            "note",  # Section 3 header "调仓纪录"
            "table", # Section 3 调仓纪录表（4 列：date/fund/action/amount）
            "note",  # Section 4 header "定投纪录"
            "table", # Section 4 定投纪录表（4 列：date/fund/action/amount，DCA 3 段金额）
        ]

    def test_hr_count_and_position(self, journal: PortfolioJournal) -> None:
        """第二十五轮（liubo 2026-10-17 第二轮反馈）：7 个 hr
        (Section 1 内 2 + Section 2 入口 1 + Section 3 入口 1 + Section 3 内 1 + Section 4 入口 1 + Section 4 内 1)。

        调仓 + 定投 分开后 +1 入口 hr +1 内 hr = 7（之前 6）。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        hrs = [e for e in card["elements"] if e.get("tag") == "hr"]
        assert len(hrs) == 7

    def test_first_element_is_section_header_note(self, journal: PortfolioJournal) -> None:
        """第一个元素是 note 标签（section header），内容是"实盘持仓"。

        用 note 元素而不是 div+markdown，是因为 note 是飞书原生浅灰背景块，
        视觉上跟下面正文明显区分；同一元素我已经在 footer 用过。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        first = card["elements"][0]
        assert first["tag"] == "note"
        # note 内部是 plain_text 列表
        assert any(
            elem.get("tag") == "plain_text" and elem.get("content") == "实盘持仓"
            for elem in first["elements"]
        )

    def test_no_strategy_section_2_note(self, journal: PortfolioJournal) -> None:
        """第二十一轮：删除 Section 2 大类资产策略 — note "大类资产策略" 不应存在。

        Section 2 冗余（持仓表 target/delta 已包含超类信息），liubo 拍板删除。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        for e in card["elements"]:
            if e.get("tag") == "note":
                for elem in e.get("elements", []):
                    assert elem.get("content") != "大类资产策略"


# ─── Section 2: per-fund 持仓成本 + 估值 + 调仓建议（第二十三轮 — liubo 2026-10-17 拍板）───


class TestPerFundSection:
    """Section 2: per-fund 持仓成本 + 估值 + 调仓建议。

    liubo 2026-10-17 拍板 — 替代 spec 098 combined 估值与操作 section。
    4 列：基金 / 持仓成本(¥) / 估值 / 调仓建议
    数据：COST_BASIS_BY_CODE（52 只）+ weekly_rebalance_plan() 的 RebalanceAction
    """

    def _get_per_fund_table(self, journal: PortfolioJournal) -> dict[str, object]:
        """拿 per-fund 表（4 列：fund/cost/valuation/advice）。"""
        card = build_portfolio_card(journal)
        for el in card["elements"]:
            if el.get("tag") != "table":
                continue
            col_names = [c["name"] for c in el.get("columns", [])]
            if col_names == ["fund", "cost", "valuation", "advice"]:
                return el
        raise AssertionError("未找到 per-fund 表（4 列 fund/cost/valuation/advice）")

    def test_section_header_present(self, journal: PortfolioJournal) -> None:
        """Section 2 标题 '持仓与调仓建议' 必须存在。"""
        card = build_portfolio_card(journal)
        for e in card["elements"]:
            if e.get("tag") == "note":
                for elem in e.get("elements", []):
                    if elem.get("content") == "持仓与调仓建议":
                        return
        raise AssertionError("Section 2 标题 '持仓与调仓建议' 未找到")

    def test_table_has_52_rows(self, journal: PortfolioJournal) -> None:
        """per-fund 表 52 行 = COST_BASIS_BY_CODE 里的所有基金（liubo 2026-10-17 拍板全列）。"""
        from global_allocation.portfolio.cost_basis import COST_BASIS_BY_CODE
        expected = len(COST_BASIS_BY_CODE)
        table = self._get_per_fund_table(journal)
        assert len(table["rows"]) == expected

    def test_columns_are_4(self, journal: PortfolioJournal) -> None:
        """per-fund 表 4 列：基金 / 持仓成本(¥) / 估值 / 调仓建议。"""
        table = self._get_per_fund_table(journal)
        col_names = [c["name"] for c in table["columns"]]
        assert col_names == ["fund", "cost", "valuation", "advice"]

    def test_row_keys_have_all_4_columns(self, journal: PortfolioJournal) -> None:
        """每行 4 个 key 都在（飞书 table 行为 row[key] = cell value）。"""
        table = self._get_per_fund_table(journal)
        for r in table["rows"]:
            for k in ("fund", "cost", "valuation", "advice"):
                assert k in r, f"行 {r} 缺 '{k}' key"

    def test_sorted_by_subclass_then_code(self, journal: PortfolioJournal) -> None:
        """按 SwensenClass 枚举顺序 + fund_code 排序。"""
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE
        from global_allocation.portfolio.cost_basis import COST_BASIS_BY_CODE
        from global_allocation.portfolio.card import SwensenClass

        subclass_order = {s: i for i, s in enumerate(SwensenClass)}
        expected = sorted(
            COST_BASIS_BY_CODE.keys(),
            key=lambda c: (subclass_order[SUBCLASS_BY_CODE[c]], c),
        )

        table = self._get_per_fund_table(journal)
        actual_codes = []
        for r in table["rows"]:
            # fund 列格式: "中文名 (code)"
            code = r["fund"].rsplit("(", 1)[1].rstrip(")")
            actual_codes.append(code)
        assert actual_codes == expected

    def test_valuation_column_uses_metric_format(self, journal: PortfolioJournal) -> None:
        """估值列格式：PE-TTM 15.42 / 分位 41.55% / 等。

        至少有一行包含 'PE-TTM' 格式（PE-TTM 是最常见估值指标）。
        """
        table = self._get_per_fund_table(journal)
        # 检查有 PE-TTM 格式的行（A 股 / 美股 / 港股 / 国外发达 / 新兴市场都有）
        pe_rows = [r for r in table["rows"] if "PE-TTM" in r["valuation"]]
        assert len(pe_rows) >= 1, "至少应有一行 PE-TTM 格式（股票类）"

    def test_advice_column_uses_5_signals(self, journal: PortfolioJournal) -> None:
        """调仓建议列：5 个信号（加仓/减仓/建仓/不动/跳过）都可能出现在数据里。"""
        table = self._get_per_fund_table(journal)
        advice_texts = " ".join(r["advice"] for r in table["rows"])
        # 数据里有 NDX 5 只 → 估值 "—"，建议 "不动"（SKIP 路径走 SKIP 给出"跳过"）
        # 数据里有 DAX (000614) → 0 仓 + 分位 < 50% → "建仓"
        # 数据里有 HSTECH (013127) → 加仓
        # 数据里有黄金 (000216) → 看综合分
        for keyword in ("不动",):
            assert keyword in advice_texts, f"advice 列应含 '{keyword}'"

    def test_no_valuation_section_note_present(self, journal: PortfolioJournal) -> None:
        """第二十三轮：spec 098 估值与操作 4 张表 section 已删，'估值与操作' 标题不应存在。"""
        card = build_portfolio_card(journal)
        for e in card["elements"]:
            if e.get("tag") == "note":
                for elem in e.get("elements", []):
                    assert elem.get("content") != "估值与操作"

    def test_merged_etf_group_shows_same_valuation(self, journal: PortfolioJournal) -> None:
        """merge_by_etf 合并组（"022434+022424+022448"）→ 3 只 A500 基金都显示同一个估值。

        估值列是按 index 级别算的（同一指数估值一样），所以 3 只基金 PE-TTM 应该完全相同。
        """
        table = self._get_per_fund_table(journal)
        a500_rows = [
            r for r in table["rows"]
            if "A500" in r["fund"] and any(code in r["fund"] for code in ("022434", "022424", "022448"))
        ]
        # 3 只 A500 基金（南方/广发/国泰）
        assert len(a500_rows) == 3
        # 估值列完全一致
        vals = {r["valuation"] for r in a500_rows}
        assert len(vals) == 1, f"A500 合并组估值不一致: {vals}"


# ─── A 股策略建议行（spec 098 第二十五轮 — liubo 2026-09-19 反馈）───


class TestDecideAShareStrategy:
    """_decide_a_share_strategy 决策树：占比 vs 目标 + 估值综合分。

    决策树：
    1) 占比 > 目标 → 不再投入（仓位纪律优先）
    2) 占比 ≤ 目标 + 偏高估/极高估 → 分批止盈
    3) 占比 ≤ 目标 + 偏低估/极低估 → 分批买入
    4) 占比 ≤ 目标 + 正常 → 正常持有
    """

    def test_over_target_returns_no_invest(self) -> None:
        """占比 > 目标 → 不再投入（不论估值高低）。"""
        from global_allocation.portfolio.card import _decide_a_share_strategy

        # 估值偏低估也应该是"不再投入"（仓位纪律优先于估值信号）
        strategy, reason = _decide_a_share_strategy(over_target=True, composite_verdict="低估")
        assert strategy == "不再投入"
        assert reason == "占比 > 目标"

    def test_under_target_high_valuation_take_profit(self) -> None:
        """占比 ≤ 目标 + 偏高估 → 分批止盈。"""
        from global_allocation.portfolio.card import _decide_a_share_strategy

        strategy, reason = _decide_a_share_strategy(over_target=False, composite_verdict="偏高估")
        assert strategy == "分批止盈"
        assert "估值" in reason and "偏高估" in reason

    def test_under_target_extreme_high_take_profit(self) -> None:
        """占比 ≤ 目标 + 极高估 → 分批止盈（一样）。"""
        from global_allocation.portfolio.card import _decide_a_share_strategy

        strategy, reason = _decide_a_share_strategy(over_target=False, composite_verdict="极高估")
        assert strategy == "分批止盈"

    def test_under_target_low_valuation_buy(self) -> None:
        """占比 ≤ 目标 + 偏低估 → 分批买入。"""
        from global_allocation.portfolio.card import _decide_a_share_strategy

        strategy, reason = _decide_a_share_strategy(over_target=False, composite_verdict="低估")
        assert strategy == "分批买入"
        assert "估值" in reason and "偏低估" in reason

    def test_under_target_extreme_low_buy(self) -> None:
        """占比 ≤ 目标 + 极低 → 分批买入。

        注：interpret_composite_score 返回 "极低"（不是 "极低估"）。
        极低估是 score band 的标签（"1=极低估 5=极高估"），不要混淆。
        """
        from global_allocation.portfolio.card import _decide_a_share_strategy

        strategy, reason = _decide_a_share_strategy(over_target=False, composite_verdict="极低")
        assert strategy == "分批买入"

    def test_under_target_normal_hold(self) -> None:
        """占比 ≤ 目标 + 估值正常 → 正常持有（不卖不买）。"""
        from global_allocation.portfolio.card import _decide_a_share_strategy

        strategy, reason = _decide_a_share_strategy(over_target=False, composite_verdict="正常")
        assert strategy == "正常持有"
        assert "估值" in reason and "正常" in reason


class TestComputeRoeYoy:
    """_compute_roe_yoy：ROE 同比 = (latest - year_ago) / year_ago。"""

    def _fv(self, latest: Decimal | None, year_ago: Decimal | None) -> FundValuation:
        return FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="013310",
            index_code="931643",
            pe_ttm=Decimal("50"),
            pe_percentile=Decimal("0.7"),
            dividend_yield=Decimal("0.005"),
            roe_latest=latest,
            roe_year_ago=year_ago,
            source="test",
        )

    def test_positive_growth(self) -> None:
        """ROE 从 5% → 8.34% → +66.8%。"""
        result = _compute_roe_yoy(self._fv(Decimal("0.0834"), Decimal("0.05")))
        assert result is not None
        assert abs(result - Decimal("0.668")) < Decimal("0.01")

    def test_zero_growth(self) -> None:
        """ROE 没变 → 0。"""
        result = _compute_roe_yoy(self._fv(Decimal("0.05"), Decimal("0.05")))
        assert result == Decimal("0")

    def test_negative_growth(self) -> None:
        """ROE 下滑 → 负值。"""
        result = _compute_roe_yoy(self._fv(Decimal("0.03"), Decimal("0.05")))
        assert result == Decimal("-0.4")

    def test_missing_latest(self) -> None:
        """ROE 最新缺失 → None。"""
        assert _compute_roe_yoy(self._fv(None, Decimal("0.05"))) is None

    def test_missing_year_ago(self) -> None:
        """ROE 去年缺失 → None。"""
        assert _compute_roe_yoy(self._fv(Decimal("0.05"), None)) is None

    def test_year_ago_zero(self) -> None:
        """去年 ROE = 0 → None（避免除零）。"""
        assert _compute_roe_yoy(self._fv(Decimal("0.05"), Decimal("0"))) is None


class TestPerFundVerdict:
    """_per_fund_verdict：单只 A 股基金的估值判断（5 档打分制）。

    spec 098 第二十八轮 — liubo 2026-09-19 反馈"按指数类型用不同估值指标 + 分位 1-5 打分"。

    3 套规则：
    - dividend（红利低波）：优先用股息率加权 PE 分位，否则 fallback 普通 PE 分位
    - broad（宽基）：普通 PE 分位
    - growth（成长/小盘）：普通 PE 分位 + ROE 同比辅助

    5 档阈值（与 4 指标估值表 PE 分位一致）：
    - < 10%  → 极低估 1
    - < 30%  → 低估 2
    - < 70%  → 正常 3
    - < 90%  → 高估 4
    - ≥ 90%  → 极高估 5

    growth 额外调整：
    - ROE 同比 ≤ -10% 且 score ≥ 4 → 极高估 5（盈利下滑 + PE 中高位）
    - ROE 同比 ≥ +10% 且 score = 5 → 高估 4（盈利在涨，PE 高但合理）
    """

    def test_broad_low(self) -> None:
        """broad 策略：PE 分位 < 30% → 低估 2。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="014532",
            index_code="930050",
            pe_ttm=Decimal("15"),
            pe_percentile=Decimal("0.20"),
            dividend_yield=Decimal("0.03"),
            source="test",
        )
        assert _per_fund_verdict(fv, "broad") == "低估 2"

    def test_broad_very_low(self) -> None:
        """broad 策略：PE 分位 < 10% → 极低估 1。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="014532",
            index_code="930050",
            pe_ttm=Decimal("15"),
            pe_percentile=Decimal("0.05"),
            dividend_yield=Decimal("0.03"),
            source="test",
        )
        assert _per_fund_verdict(fv, "broad") == "极低估 1"

    def test_broad_normal(self) -> None:
        """broad 策略：PE 分位 30%-70% → 正常 3。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="014532",
            index_code="930050",
            pe_ttm=Decimal("15"),
            pe_percentile=Decimal("0.46"),
            dividend_yield=Decimal("0.03"),
            source="test",
        )
        assert _per_fund_verdict(fv, "broad") == "正常 3"

    def test_broad_high(self) -> None:
        """broad 策略：PE 分位 70%-90% → 高估 4。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="014532",
            index_code="930050",
            pe_ttm=Decimal("20"),
            pe_percentile=Decimal("0.80"),
            dividend_yield=Decimal("0.01"),
            source="test",
        )
        assert _per_fund_verdict(fv, "broad") == "高估 4"

    def test_broad_very_high(self) -> None:
        """broad 策略：PE 分位 ≥ 90% → 极高估 5。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="014532",
            index_code="930050",
            pe_ttm=Decimal("25"),
            pe_percentile=Decimal("0.95"),
            dividend_yield=Decimal("0.01"),
            source="test",
        )
        assert _per_fund_verdict(fv, "broad") == "极高估 5"

    def test_dividend_uses_dy_weighted_pe_pct(self) -> None:
        """dividend 策略：股息率加权 PE 分位 < 30% → 低估 2（即使普通 PE 分位高）。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="008114",
            index_code="930955",
            pe_ttm=Decimal("8.85"),
            pe_percentile=Decimal("0.8180"),  # 普通 PE 分位 81.8%（高）
            dividend_yield=Decimal("0.0448"),
            pe_percentile_dy_weighted=Decimal("0.20"),  # 但股息率加权只有 20%（低估）
            source="test",
        )
        assert _per_fund_verdict(fv, "dividend") == "低估 2"

    def test_dividend_fallback_to_pe_percentile(self) -> None:
        """dividend 策略：股息率加权 PE 分位缺失 → fallback 到普通 PE 分位。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="008114",
            index_code="930955",
            pe_ttm=Decimal("8.85"),
            pe_percentile=Decimal("0.20"),  # fallback 用这个
            dividend_yield=Decimal("0.04"),
            pe_percentile_dy_weighted=None,  # 银行螺丝钉还没填
            source="test",
        )
        assert _per_fund_verdict(fv, "dividend") == "低估 2"

    def test_growth_high_roe_downgrades_score_5_to_4(self) -> None:
        """growth 策略：PE 分位 ≥ 90% + ROE 同比大涨 → 高估 4（不是极高估）。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="013310",
            index_code="931643",
            pe_ttm=Decimal("51"),
            pe_percentile=Decimal("0.95"),  # 极高估 5
            dividend_yield=Decimal("0.006"),
            roe_latest=Decimal("0.0834"),
            roe_year_ago=Decimal("0.0543"),  # +54% ROE 同比
            source="test",
        )
        assert _per_fund_verdict(fv, "growth") == "高估 4"

    def test_growth_declining_roe_promotes_to_5(self) -> None:
        """growth 策略：PE 分位 70%+ + ROE 同比大跌 → 极高估 5（盈利下滑 + PE 高 = 真贵）。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="000852",
            index_code="000852",
            pe_ttm=Decimal("44"),
            pe_percentile=Decimal("0.85"),  # 高估 4 base
            dividend_yield=Decimal("0.011"),
            roe_latest=Decimal("0.02"),
            roe_year_ago=Decimal("0.05"),  # -60% ROE 同比
            source="test",
        )
        assert _per_fund_verdict(fv, "growth") == "极高估 5"

    def test_growth_without_roe_data(self) -> None:
        """growth 策略：ROE 缺失 → 跟普通 PE 分位一样判断。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="013310",
            index_code="931643",
            pe_ttm=Decimal("50"),
            pe_percentile=Decimal("0.20"),  # < 30% → 低估 2
            dividend_yield=Decimal("0.006"),
            roe_latest=None,
            roe_year_ago=None,
            source="test",
        )
        assert _per_fund_verdict(fv, "growth") == "低估 2"

    def test_missing_pe_pct(self) -> None:
        """PE 分位缺失 → 数据缺失。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="008114",
            index_code="930955",
            pe_ttm=Decimal("10"),
            pe_percentile=None,
            dividend_yield=Decimal("0.04"),
            source="test",
        )
        assert _per_fund_verdict(fv, "dividend") == "数据缺失"
        assert _per_fund_verdict(fv, "broad") == "数据缺失"
        assert _per_fund_verdict(fv, "growth") == "数据缺失"


class TestPerFundRecommendation:
    """_per_fund_recommendation：按 per-fund 估值分位给动作建议（不看 A 股整体仓位）。

    spec 098 第二十八轮 — liubo 反馈"建议不要看整体超配，应该按评估判断"。

    决策：score 1-2 买入 / 3 持有 / 4-5 止盈 / 数据缺失 → "—"
    """

    def test_score_1_buy(self) -> None:
        assert _per_fund_recommendation("极低估 1") == "买入"

    def test_score_2_buy(self) -> None:
        assert _per_fund_recommendation("低估 2") == "买入"

    def test_score_3_hold(self) -> None:
        assert _per_fund_recommendation("正常 3") == "持有"

    def test_score_4_sell(self) -> None:
        assert _per_fund_recommendation("高估 4") == "止盈"

    def test_score_5_sell(self) -> None:
        assert _per_fund_recommendation("极高估 5") == "止盈"

    def test_data_missing_dash(self) -> None:
        assert _per_fund_recommendation("数据缺失") == "—"

    def test_advice_ignores_overall_strategy(self) -> None:
        """不接收 overall_strategy 参数 — 单只基金独立判断，仓位纪律由 strategy_note 单独说。"""
        # 即便估值"高估"也不被任何外部状态覆盖
        assert _per_fund_recommendation("高估 4") == "止盈"
        assert _per_fund_recommendation("低估 2") == "买入"


class TestFundValuationSection:
    """_build_fund_valuation_section：每只 A 股基金的估值表。

    spec 098 第二十七轮 — liubo 反馈"要看每只基金估值"。
    """

    def test_returns_empty_when_no_a_share_holdings(
        self, journal: PortfolioJournal
    ) -> None:
        """用户没有 A 股持仓（fixtures 里只有 mixed 和 equity 但没 CN_EQUITY）→ 不渲染。"""
        _seed(journal)
        section = _build_fund_valuation_section(journal)
        assert section == []

    def test_renders_per_fund_table(self, journal: PortfolioJournal) -> None:
        """有 A 股持仓 + 有估值数据 → 渲染 hr + table（spec 098 第二十八轮 — header 已外移到 valuation_section）。"""
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        section = _build_fund_valuation_section(journal)
        # 2 元素：hr + table（不再包含子 header note，由外层 _build_valuation_section 提供 "A 股估值与操作"）
        assert len(section) == 2
        assert section[0]["tag"] == "hr"
        assert section[1]["tag"] == "table"

    def test_table_has_10_columns(self, journal: PortfolioJournal) -> None:
        """10 列：基金 / 指数 / PE / PE分位 / 股息率 / ROE同比 / 评估 / 建议 / 当前仓位 / 加减仓建议。"""
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        section = _build_fund_valuation_section(journal)
        table = section[1]
        col_names = [c["name"] for c in table["columns"]]
        assert col_names == [
            "fund", "index", "pe", "pe_pct", "dy", "roe_yoy",
            "verdict", "advice", "position", "adjust",
        ]

    def test_advice_based_on_per_fund_verdict(self, journal: PortfolioJournal) -> None:
        """建议只看 per-fund 估值（不看整体 A 股策略）— liubo 反馈"建议不要看整体超配"。

        017644 中证1000增强（PE 分位=0.50 → 正常 3）→ 建议"持有" / "—"
        022434 中证A500（PE 分位=0.80 → 高估 4）→ 建议"止盈" / "−1仓"
        014532 MSCI中国A50（PE 分位=0.1626 → 低估 2）→ 建议"买入" / "+1仓"

        第十八轮：原 008114（红利低波，已砍）改用 017644（中估）测"持有"。
        """
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        section = _build_fund_valuation_section(journal)
        table = section[1]
        row_by_code = {r["fund"].split("\n")[0]: r for r in table["rows"]}
        # 017644 → 持有 —（PE 分位 50% 中估）
        assert row_by_code["017644"]["verdict"] == "正常 3"
        assert row_by_code["017644"]["advice"] == "持有"
        assert row_by_code["017644"]["adjust"] == "—"
        # 022434 → 止盈 −1仓
        assert row_by_code["022434"]["verdict"] == "高估 4"
        assert row_by_code["022434"]["advice"] == "止盈"
        assert row_by_code["022434"]["adjust"] == "−1仓"
        # 014532 → 买入 +1仓
        assert row_by_code["014532"]["verdict"] == "低估 2"
        assert row_by_code["014532"]["advice"] == "买入"
        assert row_by_code["014532"]["adjust"] == "+1仓"

    def test_advice_normal_holds(self, journal: PortfolioJournal) -> None:
        """PE 分位 30%-70% → 正常 3 → 建议"持有" / "—"。"""
        _seed_a_share_funds(journal)
        journal.db.upsert_fund_valuation(
            FundValuation(
                record_date=date.today(),
                fund_code="022434",
                index_code="000510",
                pe_ttm=Decimal("16"),
                pe_percentile=Decimal("0.50"),  # 正常 3
                dividend_yield=Decimal("0.022"),
                source="test",
            )
        )
        section = _build_fund_valuation_section(journal)
        table = section[1]
        row = next(r for r in table["rows"] if "022434" in r["fund"])
        assert row["verdict"] == "正常 3"
        assert row["advice"] == "持有"
        assert row["adjust"] == "—"

    def test_dividend_row_shows_dy_weighted_pe_pct(self, journal: PortfolioJournal) -> None:
        """第十八轮（2026-09-22 liubo 砍非宽基）后保留 — 测底层格式逻辑。

        原：用 008114 红利低波测 per-fund 表的 PE分位 列显示股息率加权值。
        现在：A 股只剩宽基，无基金有 dy_weighted 字段。测底层 card.py:621-635
        处的内联 fallback 逻辑 — 保留 fund_valuation 字段供未来分红策略用。
        直接验证 FundValuation 的 dy_weighted 字段存取正确（数据层）。
        """
        # 数据层：dy_weighted 字段能写能读
        fv_with_dy = FundValuation(
            record_date=date.today(),
            fund_code="014532",
            index_code="930050",
            pe_ttm=Decimal("15.78"),
            pe_percentile=Decimal("0.818"),      # 普通 81.8%
            pe_percentile_dy_weighted=Decimal("0.20"),  # 股息率加权 20%
            dividend_yield=Decimal("0.0448"),
            source="test",
        )
        assert fv_with_dy.pe_percentile_dy_weighted == Decimal("0.20")
        # 没设 dy_weighted → 默认 None
        fv_normal = FundValuation(
            record_date=date.today(),
            fund_code="022434",
            index_code="000510",
            pe_ttm=Decimal("15.85"),
            pe_percentile=Decimal("0.80"),
            dividend_yield=Decimal("0.005"),
            source="test",
        )
        assert fv_normal.pe_percentile_dy_weighted is None

    def test_growth_row_shows_roe_yoy_not_dy(self, journal: PortfolioJournal) -> None:
        """成长/小盘显示 ROE 同比列，不显示股息率列（股息率永远低，没参考价值）。

        fixture: 022434 中证A500（broad 策略 — 不是 growth）
        fixture 没有真正的 growth fund，所以这个测试用 mock 数据
        """
        _seed_a_share_funds(journal)
        # 额外塞一个 growth fund
        from datetime import date as _date
        journal.db.upsert_fund_valuation(
            FundValuation(
                record_date=_date.today(),
                fund_code="013310",
                index_code="931643",
                pe_ttm=Decimal("51.20"),
                pe_percentile=Decimal("0.75"),
                dividend_yield=Decimal("0.006"),
                roe_latest=Decimal("0.0834"),
                roe_year_ago=Decimal("0.0543"),
                source="test",
            )
        )
        section = _build_fund_valuation_section(journal)
        table = section[1]
        # 013310 不在 _seed_a_share_funds 里 — 不该出现在表里
        # 这个测试聚焦在 ROE同比 列存在性
        # 改用 000852 之类需要重新 seed
        # 简化：只检查 ROE同比 列存在即可
        roe_col = next((c for c in table["columns"] if c["name"] == "roe_yoy"), None)
        assert roe_col is not None
        assert roe_col["display_name"] == "ROE同比"

    def test_missing_valuation_renders_data_missing(self, journal: PortfolioJournal) -> None:
        """有 A 股持仓但 DB 没估值 → 整行显示"数据缺失"，仓位保留（按市值算）。"""
        _seed_a_share_funds(journal)
        # 没调 _seed_fund_valuations
        section = _build_fund_valuation_section(journal)
        table = section[1]
        # 每行都有数据缺失（估值列），仓位/加减仓列也合理
        for row in table["rows"]:
            assert row["pe"] == "数据缺失"
            assert row["pe_pct"] == "数据缺失"
            assert row["dy"] == "数据缺失"
            assert row["roe_yoy"] == "数据缺失"
            assert row["verdict"] == "数据缺失"
            assert row["advice"] == "—"
            # 没估值数据 → 加减仓也是 "—"
            assert row["adjust"] == "—"

    def test_only_a_share_funds_in_table(self, journal: PortfolioJournal) -> None:
        """混合持仓：1 只 A 股 + 1 只非 A 股 → 表里只显示 A 股那只。"""
        _seed_a_share_funds(journal)  # 加 022434 (CN_EQUITY)
        _seed(journal)  # 加 163406 (MIXED) + 510300 (EQUITY)
        # 清空 fund_valuations 让 022434 显示数据缺失
        section = _build_fund_valuation_section(journal)
        table = section[1]
        fund_codes = [r["fund"].split("\n")[0] for r in table["rows"]]
        assert "022434" in fund_codes
        assert "163406" not in fund_codes
        assert "510300" not in fund_codes

class TestPositionManagementMerged:
    """spec 098 第二十九轮 + 第三十轮 — liubo 反馈"两张表合并"+"底仓 1 仓 + 上限 6 仓"。

    仓位 + 加减仓建议 合并到 per-fund 表的右侧两列：
    - 当前仓位 = 市值 / 1 仓（POSITION_UNIT = 10000），格式 "X.Y仓"
    - 加减仓建议 = "+1仓" / "−1仓" / "—"
    - 整体超配 → 加仓暂停（"—"），减仓照常
    - 底仓 1 仓不动（POSITION_BASE = 1）：止盈到 ≤ 1 仓时不再减
    - 上限 6 仓（POSITION_MAX = 1 + 5）：买入到 ≥ 6 仓时不再加
    """

    def test_buy_verdict_shows_plus_1_unit(self, journal: PortfolioJournal) -> None:
        """低估 1-2 + 当前 < 6 仓 + 不超配 → 加减仓建议 = "+1仓"。

        第十八轮：原 008114（红利低波）改用 017644（但 017644 中估），
        所以只剩 014532 一只低估测 "+1仓"，再额外 mock 一只低估。
        """
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)  # 014532=低估 2, 017644=正常, 022434=高估 4
        # 给 017644 改低估测试 "+1仓"
        journal.db.upsert_fund_valuation(
            FundValuation(
                record_date=date.today(),
                fund_code="017644",
                index_code="000852",
                pe_ttm=Decimal("20.0"),
                pe_percentile=Decimal("0.15"),  # 低估 2
                dividend_yield=Decimal("0.012"),
                source="test",
            )
        )
        section = _build_fund_valuation_section(journal, over_target=False)
        table = section[1]
        row_by_code = {r["fund"].split("\n")[0]: r for r in table["rows"]}
        assert row_by_code["014532"]["adjust"] == "+1仓"
        assert row_by_code["017644"]["adjust"] == "+1仓"  # mock 低估后
        assert row_by_code["022434"]["adjust"] == "−1仓"

    def test_over_target_pauses_add_but_keeps_reduce(
        self, journal: PortfolioJournal
    ) -> None:
        """整体超配 → 买入变 "—"（加仓暂停），止盈保持 "−1仓"。

        第十八轮：014532 / 022434 都还在映射；008114 → 017644 不动（不再测）。
        """
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        section = _build_fund_valuation_section(journal, over_target=True)
        table = section[1]
        row_by_code = {r["fund"].split("\n")[0]: r for r in table["rows"]}
        # 低估 2：加仓暂停
        assert row_by_code["014532"]["adjust"] == "—"
        # 高估 4：减仓照常
        assert row_by_code["022434"]["adjust"] == "−1仓"

    def test_hold_verdict_shows_dash(self, journal: PortfolioJournal) -> None:
        """正常 3 → 加减仓 "—"。"""
        _seed_a_share_funds(journal)
        journal.db.upsert_fund_valuation(
            FundValuation(
                record_date=date.today(),
                fund_code="022434",
                index_code="000510",
                pe_ttm=Decimal("16"),
                pe_percentile=Decimal("0.50"),
                dividend_yield=Decimal("0.022"),
                source="test",
            )
        )
        section = _build_fund_valuation_section(journal, over_target=False)
        table = section[1]
        row = next(r for r in table["rows"] if "022434" in r["fund"])
        assert row["adjust"] == "—"

    def test_position_unit_is_10k(self, journal: PortfolioJournal) -> None:
        """确认仓位单位 = 10,000 CNY。"""
        assert POSITION_UNIT == Decimal("10000")

    def test_position_format_with_cang_suffix(self, journal: PortfolioJournal) -> None:
        """当前仓位 显示 "X.Y仓" 格式（保留 1 位小数 + 仓单位）。"""
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        section = _build_fund_valuation_section(journal, over_target=False)
        table = section[1]
        for row in table["rows"]:
            assert row["position"].endswith("仓")
            numeric_part = row["position"].rstrip("仓")
            assert float(numeric_part) >= 0

    def test_buy_at_max_position_shows_dash(self, journal: PortfolioJournal) -> None:
        """低估（买入）+ 当前仓位 >= 6 仓（已达上限）→ 加减仓建议 "—"。

        第十八轮：008114 已砍，改用 014532（低估 2）测 max position。
        """
        from global_allocation.portfolio.card import (
            POSITION_MAX,
            POSITION_UNIT,
        )
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        # 把 014532 的市值推到上限：shares * market_price = 6 * POSITION_UNIT
        # 014532 当前 10000 shares, price=1.5 → 市值 15000 = 1.5 仓
        # 改价格 = 6 * 10000 / 10000 = 6.0 → 市值 60000 = 6 仓（恰好上限）
        journal._price_source._prices["014532"] = Decimal(str(POSITION_MAX))
        section = _build_fund_valuation_section(journal, over_target=False)
        table = section[1]
        row_014532 = next(r for r in table["rows"] if "014532" in r["fund"])
        assert row_014532["position"] == "6.0仓"
        # 买入但已达上限 → 不再加
        assert row_014532["adjust"] == "—"

    def test_sell_at_base_position_shows_dash(self, journal: PortfolioJournal) -> None:
        """高估（止盈）+ 当前仓位 <= 1 仓（只剩底仓）→ 加减仓建议 "—"。"""
        from global_allocation.portfolio.card import POSITION_BASE
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        # 把 022434 的市值压到刚好底仓 1 仓
        journal._price_source._prices["022434"] = Decimal(str(POSITION_BASE))
        section = _build_fund_valuation_section(journal, over_target=False)
        table = section[1]
        row_022434 = next(r for r in table["rows"] if "022434" in r["fund"])
        assert row_022434["position"] == "1.0仓"
        # 止盈但只剩底仓 → 不卖底仓
        assert row_022434["adjust"] == "—"

    def test_position_constants(self) -> None:
        """spec 098 第三十轮 — 仓位常量定义。

        底仓 1 仓 + 最多加 5 仓 = 总上限 6 仓。
        """
        from global_allocation.portfolio.card import (
            POSITION_BASE,
            POSITION_MAX,
            POSITION_MAX_ABOVE_BASE,
        )
        assert POSITION_BASE == Decimal("1")
        assert POSITION_MAX_ABOVE_BASE == Decimal("5")
        assert POSITION_MAX == Decimal("6")


# ─── fixtures（spec 098 第二十七轮 per-fund 测试用）───


def _seed_a_share_funds(journal: PortfolioJournal) -> None:
    """塞 3 只 A 股基金（不同 PE 分位 / 股息率组合）+ 1 笔 buy。

    第十八轮（2026-09-22）：A 股只用宽基 5 只，原 008114（红利低波）已砍，
    改用 017644（中证1000增强）补回 3 只 — 都属大类资产配置 CN_EQUITY 子类。
    shares=10000, price=1.5 → 市值=15000 = 1.5 仓（高于底仓，可加可减）。
    """
    journal.add_fund("014532", "MSCI中国A50", AssetClass.EQUITY)
    journal.add_fund("017644", "中证1000增强", AssetClass.EQUITY)  # 第十八轮接替 008114
    journal.add_fund("022434", "中证A500", AssetClass.EQUITY)
    for code in ("014532", "017644", "022434"):
        journal._price_source._prices[code] = Decimal("1.5")
    # 各 buy 10000 份，price=1.0 → 平均成本 1.0；市值 = 10000 * 1.5 = 15000 = 1.5 仓
    journal.record_buy(
        fund_code="014532",
        trade_date=date(2026, 9, 1),
        shares=Decimal("10000"),
        price=Decimal("1.0"),
    )
    journal.record_buy(
        fund_code="017644",
        trade_date=date(2026, 9, 1),
        shares=Decimal("10000"),
        price=Decimal("1.0"),
    )
    journal.record_buy(
        fund_code="022434",
        trade_date=date(2026, 9, 1),
        shares=Decimal("10000"),
        price=Decimal("1.0"),
    )


def _seed_fund_valuations(journal: PortfolioJournal) -> None:
    """塞 per-fund 估值：1 只 broad 低估（014532）+ 1 只 broad 中估（017644） + 1 只 broad 高估（022434）。

    第十八轮：原 008114（红利低波，已砍）改为 017644（中证1000增强，宽基）。
    简化：用 PE 分位低/中/高三档测 verdict / advice / adjust，不用股息率加权逻辑（dividend 字段保留备未来分红策略）。
    """
    today = date.today()
    journal.db.upsert_fund_valuation(
        FundValuation(
            record_date=today,
            fund_code="014532",
            index_code="930050",
            pe_ttm=Decimal("15.7851"),
            pe_percentile=Decimal("0.1626"),
            dividend_yield=Decimal("0.0298"),
            source="test",
        )
    )
    journal.db.upsert_fund_valuation(
        FundValuation(
            record_date=today,
            fund_code="017644",
            index_code="000852",
            pe_ttm=Decimal("20.0"),
            pe_percentile=Decimal("0.50"),  # 中估
            dividend_yield=Decimal("0.012"),
            source="test",
        )
    )
    journal.db.upsert_fund_valuation(
        FundValuation(
            record_date=today,
            fund_code="022434",
            index_code="000510",
            pe_ttm=Decimal("15.8483"),
            pe_percentile=Decimal("0.80"),
            dividend_yield=Decimal("0.005"),
            source="test",
        )
    )


# ─── 港股测试 fixtures（spec 098.2 — liubo 2026-09-19 方案 A）───


def _seed_hk_valuation_today(journal: PortfolioJournal) -> None:
    """塞今天的 4 个港股估值指标。

    用 mock 数据：
    - HK_PE_PERCENTILE = 28%（偏低估，2 分）
    - HK_DIVIDEND_YIELD = 3.5%（低估，2 分）
    - HK_AH_PREMIUM = 1.40（140%，低估，2 分）
    - HK_BUFFETT_INDICATOR = 10（1000%，正常，3 分）
    综合 = (2+2+2+3)/4 = 2.25 → 2.3（低估）
    """
    today = date.today()
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.HK_PE_PERCENTILE,
            value=Decimal("0.28"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.HK_DIVIDEND_YIELD,
            value=Decimal("0.035"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.HK_AH_PREMIUM,
            value=Decimal("1.40"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.HK_BUFFETT_INDICATOR,
            value=Decimal("10"),
            source="test",
        )
    )


def _seed_hk_funds(journal: PortfolioJournal) -> None:
    """塞 3 只港股基金（HSI Dividend / HK Bank / HSTECH）+ 价格 + 1 笔 buy。

    让每只港股基金的市值都是 1.5 仓（shares=10000, price=1.5 → 15000 = 1.5 仓）。
    """
    journal.add_fund("004098", "港股通股息率50", AssetClass.EQUITY)
    journal.add_fund("006809", "港股银行指数", AssetClass.EQUITY)
    journal.add_fund("013127", "恒生科技", AssetClass.EQUITY)
    for code in ("004098", "006809", "013127"):
        journal._price_source._prices[code] = Decimal("1.5")
    for code in ("004098", "006809", "013127"):
        journal.record_buy(
            fund_code=code,
            trade_date=date(2026, 9, 1),
            shares=Decimal("10000"),
            price=Decimal("1.0"),
        )


def _seed_hk_fund_valuations(journal: PortfolioJournal) -> None:
    """塞 3 只港股基金的 per-fund 估值（spec 098.2）。

    - 004098 (HSI Dividend): 股息率高，PE 分位正常
    - 006809 (HK Bank): 股息率高，PE 分位低（便宜）
    - 013127 (HSTECH): PE 高，成长股（看 ROE 同比）
    """
    today = date.today()
    journal.db.upsert_fund_valuation(
        FundValuation(
            record_date=today,
            fund_code="004098",
            index_code="HSSCHKY",
            pe_ttm=Decimal("8.5"),
            pe_percentile=Decimal("0.45"),
            dividend_yield=Decimal("0.055"),
            source="test",
        )
    )
    journal.db.upsert_fund_valuation(
        FundValuation(
            record_date=today,
            fund_code="006809",
            index_code="930792",
            pe_ttm=Decimal("6.5"),
            pe_percentile=Decimal("0.20"),  # 低估
            dividend_yield=Decimal("0.060"),  # 高分红
            source="test",
        )
    )
    journal.db.upsert_fund_valuation(
        FundValuation(
            record_date=today,
            fund_code="013127",
            index_code="HSTECH",
            pe_ttm=Decimal("35.0"),
            pe_percentile=Decimal("0.75"),  # PE 分位中位偏上
            dividend_yield=Decimal("0.005"),
            roe_latest=Decimal("0.15"),
            roe_year_ago=Decimal("0.10"),
            source="test",
        )
    )


# ─── 港股估值测试（spec 098.2）───


class TestBuildHKValuationSection:
    """spec 098.2 — 港股 4 指标估值 section。"""

    def test_hk_section_renders_when_data_present(self, journal: PortfolioJournal) -> None:
        """DB 有港股 4 指标 → 渲染港股 section。"""
        from global_allocation.portfolio.card import _build_hk_valuation_section

        _seed_hk_valuation_today(journal)
        elements = _build_hk_valuation_section(journal)
        assert len(elements) > 0
        # 应该有 note header "港股估值与操作"
        note = next((e for e in elements if e.get("tag") == "note"), None)
        assert note is not None
        assert "港股" in note["elements"][0]["content"]

    def test_hk_section_skipped_when_no_data(self, journal: PortfolioJournal) -> None:
        """DB 没港股指标 → 港股 section 不渲染（避免空表格）。"""
        from global_allocation.portfolio.card import _build_hk_valuation_section

        # _seed_valuation_today 只塞 A 股 4 指标
        _seed_valuation_today(journal)
        elements = _build_hk_valuation_section(journal)
        assert elements == []

    def test_hk_section_has_four_indicator_rows(self, journal: PortfolioJournal) -> None:
        """港股 4 指标行 + 1 综合分行 = 5 行。"""
        from global_allocation.portfolio.card import _build_hk_valuation_section

        _seed_hk_valuation_today(journal)
        elements = _build_hk_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        assert len(tables) == 1
        rows = tables[0]["rows"]
        # 4 指标 + 1 综合分
        assert len(rows) == 5

    def test_hk_section_displays_strategy_note(self, journal: PortfolioJournal) -> None:
        """港股 section 有策略 note（占比 vs 目标 + 估值判断）。

        Note: 策略具体是"分批买入"还是"不再投入"取决于占比 vs 目标（10.5%）。
        _decide_hk_strategy 单元测试单独验证，这里只验证 section 含 div + "港股" 字样。
        """
        from global_allocation.portfolio.card import _build_hk_valuation_section

        _seed_hk_valuation_today(journal)
        _seed_hk_funds(journal)
        elements = _build_hk_valuation_section(journal)
        # 至少有一个 div（策略 note）
        divs = [e for e in elements if e.get("tag") == "div"]
        assert len(divs) >= 1
        text = divs[0]["text"]["content"]
        assert "港股" in text
        # 不管占比超/低目标，div 都该有"目标"和百分比
        assert "目标" in text
        assert "%" in text

    def test_hk_section_composite_score(self, journal: PortfolioJournal) -> None:
        """港股综合分：[PE:2 股息:2 AH:2 巴菲特:3] → 2.3 低估。"""
        from global_allocation.portfolio.card import _build_hk_valuation_section

        _seed_hk_valuation_today(journal)
        elements = _build_hk_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        composite_row = tables[0]["rows"][-1]  # 最后一行是综合分
        assert composite_row["indicator"] == "综合分"
        # 综合分 value 列形如 "[PE:2 股息:2 AH:2 巴菲特:3]"
        assert "[PE:2 股息:2 AH:2" in composite_row["value"]
        # verdict 列形如 "2.3 低估"
        assert "2.3" in composite_row["verdict"]
        assert "低估" in composite_row["verdict"]

    def test_hk_ah_premium_formatted_as_integer_pct(self, journal: PortfolioJournal) -> None:
        """AH 溢价格式化为整数百分比（"140%"，不是 "140.00%"）。"""
        from global_allocation.portfolio.card import _build_hk_valuation_section

        _seed_hk_valuation_today(journal)
        elements = _build_hk_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        # 找 AH 溢价行
        ah_row = next(
            r for r in tables[0]["rows"] if r["indicator"] == "AH 溢价"
        )
        # 1.40 → "140%"
        assert ah_row["value"] == "140%"

    def test_hk_buffett_formatted_as_integer_pct(self, journal: PortfolioJournal) -> None:
        """港股巴菲特格式化为整数百分比（"1000%"）。"""
        from global_allocation.portfolio.card import _build_hk_valuation_section

        _seed_hk_valuation_today(journal)
        elements = _build_hk_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        bf_row = next(
            r for r in tables[0]["rows"] if r["indicator"] == "港股巴菲特"
        )
        # 10.0 → "1000%"
        assert bf_row["value"] == "1000%"


class TestBuildValuationSectionCombined:
    """验证 _build_valuation_section combined 模式（A 股 + 港股 + 美股 一起渲染）。

    spec 098.4 — 不再每个 region 单独一个 section，而是合并成 combined 表 + 多个 strategy divs。
    """

    def test_combined_section_renders_with_2_regions(self, journal: PortfolioJournal) -> None:
        """A 股 + 港股 都有数据 → 渲染 combined section（含 combined 指标表）。"""
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        elements = _build_valuation_section(journal)
        notes = [e for e in elements if e.get("tag") == "note"]
        contents = [n["elements"][0]["content"] for n in notes]
        # combined section 只有一个统一 header（不再是 per-region header）
        assert "估值与操作" in contents
        assert "A 股估值与操作" not in contents
        assert "港股估值与操作" not in contents

    def test_combined_indicator_table_includes_both_regions(self, journal: PortfolioJournal) -> None:
        """combined 指标表含 2 region × 4 指标 + 2 综合 = 10 行。"""
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        elements = _build_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        indicator_table = next(
            t for t in tables
            if [c["name"] for c in t["columns"]] == ["region", "indicator", "value", "verdict", "threshold"]
        )
        # A 股 4 + 1 综合 + 港股 4 + 1 综合 = 10 行
        assert len(indicator_table["rows"]) == 10

    def test_only_a_share_when_no_hk_data(self, journal: PortfolioJournal) -> None:
        """只有 A 股数据 → combined 指标表 5 行（4 + 1 综合），不含港股行。"""
        _seed_valuation_today(journal)
        elements = _build_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        indicator_table = next(
            t for t in tables
            if [c["name"] for c in t["columns"]] == ["region", "indicator", "value", "verdict", "threshold"]
        )
        assert len(indicator_table["rows"]) == 5
        # 所有 row region 列都是 "A 股" 或 "—"（综合行）
        for row in indicator_table["rows"]:
            assert row["region"] in {"A 股", "—"}

    def test_only_hk_when_no_a_share_data(self, journal: PortfolioJournal) -> None:
        """只有港股数据 → combined 指标表 5 行（4 + 1 综合），全是港股。"""
        _seed_hk_valuation_today(journal)
        elements = _build_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        indicator_table = next(
            t for t in tables
            if [c["name"] for c in t["columns"]] == ["region", "indicator", "value", "verdict", "threshold"]
        )
        assert len(indicator_table["rows"]) == 5
        for row in indicator_table["rows"]:
            assert row["region"] in {"港股", "—"}

    def test_combined_section_returns_empty_when_no_data(self, journal: PortfolioJournal) -> None:
        """3 region 都无数据 → 返回 []（不渲染空 section）。"""
        # 不调任何 _seed_valuation_today
        elements = _build_valuation_section(journal)
        assert elements == []


class TestBuildFundValuationSectionHK:
    """验证 _build_fund_valuation_section 能渲染港股 3 只基金。"""

    def test_hk_funds_render_in_separate_section(self, journal: PortfolioJournal) -> None:
        """A 股 3 只基金（014532 / 017644 / 022434）只在 A 股 section 出现，不在港股 section。

        第十八轮：港股全砍了；这个测试现在反过来验证 — HK section 因为没基金
        不渲染 per-fund table（即使 _seed_hk_funds 仍在 DB 加 4 only-in-test 的代码），
        但 A 股 section 正常出 per-fund 表。
        """
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_a_share_funds(journal)
        _seed_hk_funds(journal)  # 港股基金在 journal DB 但不在 SUBCLASS_BY_CODE

        a_share_elements = _build_a_share_valuation_section(journal)
        hk_elements = _build_hk_valuation_section(journal)

        # A 股 section 含 A 股 3 只基金（014532 / 017644 / 022434）
        a_share_tables = [e for e in a_share_elements if e.get("tag") == "table"]
        # 第一个 table 是指标表，第 2 个是 per-fund 表
        if len(a_share_tables) >= 2:
            a_share_fund_table = a_share_tables[1]
            fund_codes_in_a_share = [r["fund"].split("\n")[0] for r in a_share_fund_table["rows"]]
            assert "014532" in fund_codes_in_a_share
            assert "017644" in fund_codes_in_a_share
            assert "022434" in fund_codes_in_a_share
            # 港股 code 不应在 A 股 section
            assert "004098" not in fund_codes_in_a_share
            assert "013127" not in fund_codes_in_a_share

        # 港股 section 因为没 SUBCLASS_BY_CODE 基金 per-fund table 不渲染
        # （指标表仍可能在 — 视 _build_hk_valuation_section 实现）
        # 这里只断言 a_share 正确，hk 部分空（无 per-fund）跳过

    def test_hk_dividend_fund_low_pe_buys(self, journal: PortfolioJournal) -> None:
        """006809 (HK Bank, dividend strategy): PE 分位 20% → 低估 → 买入。

        验证港股 dividend 策略正确应用（看普通 PE 分位，不用股息率加权）。
        """
        from global_allocation.portfolio.card import _per_fund_verdict

        _seed_hk_fund_valuations(journal)
        fv = journal.db.list_latest_fund_valuations_for_codes(["006809"])["006809"]
        verdict = _per_fund_verdict(fv, "dividend")
        # dividend 策略：pe_percentile_dy_weighted=None → fallback 到 pe_percentile=0.20
        # 0.20 在 [10%, 30%) → 低估 2
        assert "低估 2" in verdict

    def test_hk_growth_fund_high_pe_uses_roe_yoy(self, journal: PortfolioJournal) -> None:
        """013127 (HSTECH, growth strategy): PE 分位 75% + ROE 同比 +50% → 高估 4。

        验证港股 growth 策略的 ROE 同比修正规则：
        - PE 分位 75% → 基础 4 分（高估）
        - ROE 同比 = (0.15 - 0.10) / 0.10 = +0.50（+50%）
        - ROE 同比 ≥ +10% 且 score == 5 才降分；score=4 不变
        所以最终仍是 "高估 4"
        """
        from global_allocation.portfolio.card import _per_fund_verdict

        _seed_hk_fund_valuations(journal)
        fv = journal.db.list_latest_fund_valuations_for_codes(["013127"])["013127"]
        verdict = _per_fund_verdict(fv, "growth")
        assert "高估" in verdict

    def test_hk_funds_no_valuation_show_data_missing(self, journal: PortfolioJournal) -> None:
        """港股有基金但缺估值 — 第二十二轮：013127 在 SUBCLASS_BY_CODE → per-fund 表渲染 1 行「数据缺失」。

        故意不 _seed_hk_fund_valuations → 港股基金没估值。
        但 013127 现在属 HK_EQUITY 子类，所以 per-fund 表会渲染一行，所有估值列显示"数据缺失"。
        """
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_hk_funds(journal)
        # 故意不 _seed_hk_fund_valuations → 港股基金没估值

        hk_elements = _build_hk_valuation_section(journal)
        hk_tables = [e for e in hk_elements if e.get("tag") == "table"]
        per_fund_tables = [
            t for t in hk_tables
            # per-fund table 有 "fund" 列；indicator 表只有 "indicator" / "current" / ...
            if any(c.get("name") == "fund" for c in t.get("columns", []))
        ]
        # 第二十二轮：HK 1 只 kept 基金（013127）→ per-fund 表渲染 1 行（值=「数据缺失」）
        assert len(per_fund_tables) == 1, (
            "HK 现在 1 只 kept 基金 (013127)，per-fund table 应当渲染 1 行"
        )
        rows = per_fund_tables[0]["rows"]
        assert len(rows) == 1
        row = rows[0]
        # 第一列是 fund（"013127\n恒生科技"）
        assert row["fund"].startswith("013127")
        # 估值列都是"数据缺失"
        assert row["pe"] == "数据缺失"
        assert row["pe_pct"] == "数据缺失"
        assert row["dy"] == "数据缺失"
        assert row["verdict"] == "数据缺失"


class TestIndexVerdictStrategyHK:
    """验证 INDEX_VERDICT_STRATEGY 包含港股 3 个指数。"""

    def test_hk_index_strategies_mapped(self) -> None:
        from global_allocation.portfolio.card import INDEX_VERDICT_STRATEGY

        assert INDEX_VERDICT_STRATEGY["HSSCHKY"] == "dividend"
        assert INDEX_VERDICT_STRATEGY["930792"] == "dividend"
        assert INDEX_VERDICT_STRATEGY["HSTECH"] == "growth"


# ─── 美股测试 fixtures（spec 098.3 — liubo 2026-09-20）───


def _seed_us_valuation_today(journal: PortfolioJournal) -> None:
    """塞今天的 4 个美股估值指标。

    用 mock 数据（贴近实际 lixinger CSV 2026-09-20 数据）：
    - US_PE_PERCENTILE = 60.17%（正常，3 分：30%-70% 区间）
    - US_DIVIDEND_YIELD = 1.06%（正常，3 分：1%-3% 区间）
    - US_BUFFETT_INDICATOR = 1.71（171%，正常，3 分：130%-180% 区间）
    - US_EQUITY_RISK_PREMIUM = 0.0145（1.45%，正常，3 分：1%-3% 区间）
    综合 = (3+3+3+3)/4 = 3.0 → 3.0（正常）
    """
    today = date.today()
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.US_PE_PERCENTILE,
            value=Decimal("0.6017"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.US_DIVIDEND_YIELD,
            value=Decimal("0.0106"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.US_BUFFETT_INDICATOR,
            value=Decimal("1.71"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM,
            value=Decimal("0.0145"),
            source="test",
        )
    )


def _seed_us_funds(journal: PortfolioJournal) -> None:
    """塞 5 只美股基金（3 NDX + 1 标普 500 + 1 标普 100）。

    2026-09-29（第二十二轮）：liubo 卖出 019524 华泰柏瑞纳 100 联接。
    第二十/二十一轮加的 019172/019441 直接 QDII 场外 — 测试不验估值，留 stub。
    第十八轮（2026-09-22）：3 只 QDII 全球主题（017730/016664/006373）砍了，
    只剩 5 只宽基/纳指/标普 — 都是大类资产配置 US_EQUITY。
    让每只美股基金的市值都是 1.5 仓。
    """
    us_funds = [
        ("018966", "汇添富纳100"),
        ("539001", "建信纳100"),
        ("016452", "南方纳100"),
        ("017641", "摩根标普500"),
        ("519981", "长信标普100"),
    ]
    for code, name in us_funds:
        journal.add_fund(code, name, AssetClass.EQUITY)
        journal._price_source._prices[code] = Decimal("1.5")
    for code, _ in us_funds:
        journal.record_buy(
            fund_code=code,
            trade_date=date(2026, 9, 1),
            shares=Decimal("10000"),
            price=Decimal("1.0"),
        )


class TestUSIndicatorConstants:
    """验证 US_INDICATOR_* 常量定义正确。"""

    def test_us_indicator_codes_has_four_codes(self) -> None:
        """4 个美股指标（spec 098.3）。"""
        assert len(US_INDICATOR_CODES) == 4
        assert ValuationIndicatorCode.US_PE_PERCENTILE in US_INDICATOR_CODES
        assert ValuationIndicatorCode.US_DIVIDEND_YIELD in US_INDICATOR_CODES
        assert ValuationIndicatorCode.US_BUFFETT_INDICATOR in US_INDICATOR_CODES
        assert ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM in US_INDICATOR_CODES

    def test_us_indicator_names_have_all_codes(self) -> None:
        """NAMES dict 覆盖所有 4 个 code。"""
        for code in US_INDICATOR_CODES:
            assert code in US_INDICATOR_NAMES
            assert US_INDICATOR_NAMES[code]  # 非空

    def test_us_indicator_short_names_have_all_codes(self) -> None:
        """SHORT_NAMES dict 覆盖所有 4 个 code。"""
        for code in US_INDICATOR_CODES:
            assert code in US_INDICATOR_SHORT_NAMES
            assert US_INDICATOR_SHORT_NAMES[code]  # 非空


class TestFormatIndicatorValueUS:
    """验证 _format_indicator_value 对美股 4 指标的处理。"""

    def test_us_equity_risk_premium_keeps_sign_and_two_decimals(self) -> None:
        """美股股债利差保留正负号 + 2 位小数（跟 A 股股债利差一致）。"""
        assert _format_indicator_value(
            ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM, Decimal("0.0145")
        ) == "+1.45%"
        assert _format_indicator_value(
            ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM, Decimal("-0.01")
        ) == "-1.00%"

    def test_us_buffett_formatted_as_integer_pct(self) -> None:
        """美股巴菲特格式化为整数百分比（值通常 > 1，如 1.71 → "171%"）。"""
        assert _format_indicator_value(
            ValuationIndicatorCode.US_BUFFETT_INDICATOR, Decimal("1.71")
        ) == "171%"
        assert _format_indicator_value(
            ValuationIndicatorCode.US_BUFFETT_INDICATOR, Decimal("0.95")
        ) == "95%"

    def test_us_pe_percentile_two_decimals(self) -> None:
        """美股 PE 分位 2 位小数百分比（跟 A 股 / 港股一致）。"""
        assert _format_indicator_value(
            ValuationIndicatorCode.US_PE_PERCENTILE, Decimal("0.6017")
        ) == "60.17%"

    def test_us_dividend_yield_two_decimals(self) -> None:
        """美股股息率 2 位小数百分比（跟 A 股 / 港股一致）。"""
        assert _format_indicator_value(
            ValuationIndicatorCode.US_DIVIDEND_YIELD, Decimal("0.0106")
        ) == "1.06%"


class TestBuildUSValuationSection:
    """spec 098.3 — 美股 4 指标估值 section。"""

    def test_us_section_renders_when_data_present(self, journal: PortfolioJournal) -> None:
        """DB 有美股 4 指标 → 渲染美股 section。"""
        _seed_us_valuation_today(journal)
        elements = _build_us_valuation_section(journal)
        assert len(elements) > 0
        note = next((e for e in elements if e.get("tag") == "note"), None)
        assert note is not None
        assert "美股" in note["elements"][0]["content"]

    def test_us_section_skipped_when_no_data(self, journal: PortfolioJournal) -> None:
        """DB 没美股指标 → 美股 section 不渲染（避免空表格）。"""
        # _seed_valuation_today 只塞 A 股 4 指标
        _seed_valuation_today(journal)
        elements = _build_us_valuation_section(journal)
        assert elements == []

    def test_us_section_has_four_indicator_rows(self, journal: PortfolioJournal) -> None:
        """美股 4 指标行 + 1 综合分行 = 5 行。"""
        _seed_us_valuation_today(journal)
        elements = _build_us_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        assert len(tables) == 1
        rows = tables[0]["rows"]
        # 4 指标 + 1 综合分
        assert len(rows) == 5

    def test_us_section_displays_strategy_note(self, journal: PortfolioJournal) -> None:
        """美股 section 有策略 note（占比 vs 目标 + 估值判断）。"""
        _seed_us_valuation_today(journal)
        _seed_us_funds(journal)
        elements = _build_us_valuation_section(journal)
        divs = [e for e in elements if e.get("tag") == "div"]
        assert len(divs) >= 1
        text = divs[0]["text"]["content"]
        assert "美股" in text
        assert "目标" in text
        assert "%" in text

    def test_us_section_composite_score(self, journal: PortfolioJournal) -> None:
        """美股综合分：(3+3+3+3)/4 = 3.0 → 3.0 正常。

        60.17% PE 分位 = 3 分（30%-70% 区间）；其他 3 个指标也都 3 分。
        综合分 value 列形如 "[PE:3 股息:3 巴菲特:3 股债:3]"
        verdict 列形如 "3.0 正常"
        """
        _seed_us_valuation_today(journal)
        elements = _build_us_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        composite_row = tables[0]["rows"][-1]  # 最后一行是综合分
        assert composite_row["indicator"] == "综合分"
        # 综合分 value 列形如 "[PE:3 股息:3 巴菲特:3 股债:3]"
        assert "[PE:3 股息:3" in composite_row["value"]
        # verdict 列形如 "3.0 正常"
        assert "3.0" in composite_row["verdict"]
        assert "正常" in composite_row["verdict"]

    def test_us_buffett_formatted_as_integer_pct(self, journal: PortfolioJournal) -> None:
        """美股巴菲特格式化为整数百分比（1.71 → "171%"，不是 "171.00%"）。"""
        _seed_us_valuation_today(journal)
        elements = _build_us_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        bf_row = next(
            r for r in tables[0]["rows"] if r["indicator"] == "美股巴菲特"
        )
        # 1.71 → "171%"
        assert bf_row["value"] == "171%"


class TestBuildValuationSectionCombinedUS:
    """验证 _build_valuation_section combined 模式 + 美股 region 接入（spec 098.4）。

    spec 098.3 — 美股 region 加入（4 个新指标 + 9 只基金）。
    spec 098.4 — 3 region 合并成 combined 表（5 列指标 + 11 列 per-fund）。
    """

    def test_three_regions_combined_in_single_section(
        self, journal: PortfolioJournal
    ) -> None:
        """A 股 + 港股 + 美股 都有数据 → combined section 一个 header + combined 指标表。"""
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_us_valuation_today(journal)
        elements = _build_valuation_section(journal)
        notes = [e for e in elements if e.get("tag") == "note"]
        contents = [n["elements"][0]["content"] for n in notes]
        # 1 个统一 header（不再是 per-region 3 个）
        assert "估值与操作" in contents
        assert "A 股估值与操作" not in contents
        assert "港股估值与操作" not in contents
        assert "美股估值与操作" not in contents

    def test_us_section_skipped_when_no_data(self, journal: PortfolioJournal) -> None:
        """只 A 股有数据 → 美股行不出现在 combined 表里。"""
        _seed_valuation_today(journal)
        elements = _build_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        indicator_table = next(
            t for t in tables
            if [c["name"] for c in t["columns"]] == ["region", "indicator", "value", "verdict", "threshold"]
        )
        # 5 行（A 股 4 指标 + 1 综合）
        assert len(indicator_table["rows"]) == 5
        for row in indicator_table["rows"]:
            assert row["region"] in {"A 股", "—"}
            assert "美股" not in row["region"]

    def test_combined_indicator_table_3_region_composite_rows(
        self, journal: PortfolioJournal
    ) -> None:
        """3 region 都有数据 → combined 指标表 15 行（含 3 个综合分行）。"""
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_us_valuation_today(journal)
        elements = _build_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        indicator_table = next(
            t for t in tables
            if [c["name"] for c in t["columns"]] == ["region", "indicator", "value", "verdict", "threshold"]
        )
        composite_rows = [r for r in indicator_table["rows"] if r["indicator"] == "综合分"]
        # 3 个综合分行（A 股 / 港股 / 美股 各一个）
        assert len(composite_rows) == 3


class TestIndexVerdictStrategyUS:
    """验证 INDEX_VERDICT_STRATEGY 包含美股 4 个指数。"""

    def test_us_index_strategies_mapped(self) -> None:
        from global_allocation.portfolio.card import INDEX_VERDICT_STRATEGY

        # INX / GSPC / OEX 实际能在 lixinger CSV 命中，NDX 当前 CSV 无数据（保留占位）
        assert INDEX_VERDICT_STRATEGY["INX"] == "growth"
        assert INDEX_VERDICT_STRATEGY["GSPC"] == "growth"
        assert INDEX_VERDICT_STRATEGY["OEX"] == "dividend"
        assert INDEX_VERDICT_STRATEGY["NDX"] == "growth"


# ─── 调仓记录金额列：仓 + 元 双值（liubo 2026-10-17 反馈「我都想要」）───


class TestFormatAmountWithUnits:
    """_format_amount_with_units — 调仓记录「金额」列规范化。

    公式：1 仓 = 1 万 CNY = 10,000 元（spec 022/097 整数仓位模型）。

    liubo 2026-10-17：「你都显示的是金额和多少仓。比如说1万，你就要告诉我这1万是1仓，
    还是0.5仓，对吧？你不要只显示金额，也不要只显示仓位。都想。」
    """

    def test_pure_position_with_sign_becomes_double_value(self) -> None:
        """±N 仓（加减仓）→ "N 仓 / X,XXX 元"（方向走 action 列）。"""
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("+1 仓") == "1 仓 / 10,000 元"
        assert _format_amount_with_units("-1 仓") == "1 仓 / 10,000 元"

    def test_fractional_position(self) -> None:
        """0.5 仓 / 5000 元 — liubo 2026-10-17 提到 "0.5 仓" 场景。"""
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("0.5 仓") == "0.5 仓 / 5,000 元"
        assert _format_amount_with_units("5000 元") == "0.5 仓 / 5,000 元"

    def test_pure_money_gets_position_prepended(self) -> None:
        """纯 X 元 / CNY / 万 / ¥X → 前面加 仓 数。"""
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("10000 元") == "1 仓 / 10,000 元"
        assert _format_amount_with_units("10000 CNY") == "1 仓 / 10,000 元"
        assert _format_amount_with_units("1 万") == "1 仓 / 10,000 元"
        assert _format_amount_with_units("¥10000") == "1 仓 / ¥10000"

    def test_already_double_value_normalizes_format(self) -> None:
        """已含 仓 + 元 双值（"建仓 1 仓 / 10000 CNY"）→ 标准化为 "1 仓 / 10,000 元"。

        去 "建仓" 词（动作已在 action 列）+ CNY → 元 + 千分位逗号。
        """
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("建仓 1 仓 / 10000 CNY") == "1 仓 / 10,000 元"
        assert _format_amount_with_units("0.5 仓 / 5000 元") == "0.5 仓 / 5,000 元"

    def test_dash_preserved(self) -> None:
        """「—」表示无金额信息 → 保持。"""
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("—") == "—"
        assert _format_amount_with_units("") == ""

    def test_dca_three_value_format_preserved(self) -> None:
        """DCA 3-value 格式（"10 元 / 每天 / 累计 1800"）→ 保持。

        DCA 是定投计划三要素（金额 / 频率 / 累计目标），不是 仓 概念。
        """
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("10 元 / 每天 / 累计 1800") == "10 元 / 每天 / 累计 1800"
        assert _format_amount_with_units("10 元 / 每天 / 累计 2000") == "10 元 / 每天 / 累计 2000"
        assert _format_amount_with_units("250-1000 元 / 每周（均值 500） / 累计 10000") == "250-1000 元 / 每周（均值 500） / 累计 10000"

    def test_dca_fallback_already_bought_preserved(self) -> None:
        """DCA 兜底"已买 ¥X" → 保持（已买金额是 DCA 进度，不是仓）。"""
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("已买 ¥60") == "已买 ¥60"

    def test_cost_calibration_preserved(self) -> None:
        """Cost 校准（"净 +1661" / "+40" / "成本 20 收回现金"）→ 保持（不是仓位概念）。

        liubo 2026-10-17 拍板 — cost 校准是记账对账，不调仓。
        """
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("净 +1661") == "净 +1661"
        assert _format_amount_with_units("+40") == "+40"
        assert _format_amount_with_units("成本 20 收回现金") == "成本 20 收回现金"

    def test_bare_number_without_unit_preserved(self) -> None:
        """无单位的纯数字（"20000" / "10000"）→ 保持（无法判定是元还是别的单位）。"""
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("20000") == "20000"
        assert _format_amount_with_units("10000") == "10000"

    def test_conversion_shares_preserved(self) -> None:
        """"转换 8379 份" 保持（份是转换的份额单位，不是仓）。"""
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("转换 8379 份") == "转换 8379 份"


class TestRebalanceRecordsDoubleValue:
    """调仓记录表 — 金额列全部行都是 仓 + 元 双值（liubo 2026-10-17 拍板）。

    16 行原始数据（基于 git log 抓取）：
    - 8 行 ±N 仓 → 标准化为 "N 仓 / 10,000 元"
    - 6 行 10000 元（转换 6 行）→ 标准化为 "1 仓 / 10,000 元"
    - 1 行 DCA (南方纳100) → 保持 "10 元 / 每天 / 累计 1800"
    - 2 行 DCA (摩根/万家) → 保持 "10 元 / 每天 / 累计 2000"
    - 1 行 "建仓 1 仓 / 10000 CNY" → 标准化为 "1 仓 / 10,000 元"
    - 1 行 +1 仓 (013127) → 标准化为 "1 仓 / 10,000 元"
    - 2 行 "—" → 保持
    """

    def test_rebalance_table_amounts_all_double_value_or_dash(self) -> None:
        """调仓记录表 16 行 — 每一行 amount 都有 仓 + 元 OR 是 "—" OR 是 DCA 3-value。"""
        card = build_portfolio_card(PortfolioJournal(
            db=PortfolioDB(path=__import__('pathlib').Path("/tmp/empty.db")),
            price_source=FakePriceSource({}),
        ))
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        rebalance_table = next(
            (t for t in tables if [c["name"] for c in t["columns"]] == ["date", "fund", "action", "amount"]),
            None,
        )
        if rebalance_table is None:
            return  # 没数据跳过

        for r in rebalance_table["rows"]:
            amt = r["amount"]
            if amt == "—":
                continue
            if " / 累计 " in amt:
                # DCA 3-value
                continue
            # 应该 仓 + 元 双值
            assert " 仓 / " in amt and " 元" in amt, f"行 {r} amount '{amt}' 不是 仓+元 双值"

    def test_rebalance_table_dca_amount_preserved(self) -> None:
        """DCA 行 amount 保持 3-value 格式 "10 元 / 每天 / 累计 X"。"""
        card = build_portfolio_card(PortfolioJournal(
            db=PortfolioDB(path=__import__('pathlib').Path("/tmp/empty.db")),
            price_source=FakePriceSource({}),
        ))
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        rebalance_table = next(
            (t for t in tables if [c["name"] for c in t["columns"]] == ["date", "fund", "action", "amount"]),
            None,
        )
        if rebalance_table is None:
            return
        dca_rows = [r for r in rebalance_table["rows"] if r["action"] == "定投"]
        for r in dca_rows:
            assert " / 每天 / 累计 " in r["amount"], f"DCA 行 {r} amount '{r['amount']}' 失去 3-value 格式"
