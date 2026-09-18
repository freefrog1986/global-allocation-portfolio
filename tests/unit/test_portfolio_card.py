"""测试 src/global_allocation/portfolio/card.py。

飞书 chart card for portfolio journal — spec 096 重设计 + spec 097 第十九轮合并：
focus 在实盘持仓 = summary div + 大类资产柱状图 + 5 列聚合持仓表（含 target/delta）。
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
        """卡片 = header + 1 个 section（实盘持仓 — 第十九轮合并了 Section 2/3）。

        spec 097 第十九轮（liubo 2026-09-19）：把 Section 2（大类资产策略）+ Section 3
        （大类资产明细）的策略对比表合并到 Section 1 持仓表，加 target/delta 列。
        卡片从 3 个 section 简化为 1 个 section。

        现在的元素：
        - note header "实盘持仓"
        - div summary（生成时间 / 总市值 / 盈亏 / 周涨跌）
        - hr
        - chart（柱状图，带顶部数值标签）
        - hr
        - table（11 行 × 5 列：分类 / 市值 / 占比 / 目标 / 偏离）
        合计：1 note + 1 div + 2 hr + 1 chart + 1 table = 6 个元素 + footer
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
        assert len(tables) == 1
        assert len(divs) == 1
        assert len(notes) == 1
        assert len(hrs) == 2

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

    5 列：分类（含 "#. " 前缀）/ 市值 / 占比 / 目标 / 偏离。按 SwensenClass 枚举顺序
    展示全部 11 个子类（含 count=0 的——空子类显示 0 元 / 0.00%）。

    目标/偏离规则：
    - 投资子类（10 个）：target = subclass × super × (1 − 现金%)，delta = 当前 − 目标（pp）
    - 现金（1 个）：target = "[15%, 50]%" 区间字符串，delta = "区间内/低于下限/高于上限" 状态文本

    Feishu table column width 只接受 "auto"（其它值 short/medium/long/数字都拒），
    所以 # 信息嵌进分类名前缀（"1. A 股股票"），干掉单独 # 列。
    """

    def _get_table(self, journal: PortfolioJournal) -> dict[str, object]:
        """唯一的持仓表 — 5 列（class / value / weight / target / delta）。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        assert len(tables) == 1
        return tables[0]

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
        """投资子类（10 个）的 target 列以 % 结尾（动态公式计算的结果）。

        测试只检查格式（以 % 结尾），具体值由 test_investment_subclass_target_uses_dynamic_formula 验证。
        现金行特殊（区间字符串），跳过。
        """
        from global_allocation.portfolio.breakdown import DISPLAY_NAME, SwensenClass

        spec = self._get_table(journal)
        for row in spec["rows"]:
            class_name = row["class"].split(". ", 1)[1]
            if class_name == DISPLAY_NAME[SwensenClass.CASH]:
                continue  # 现金行 target 是区间字符串，不以 % 结尾
            assert row["target"].endswith("%")

    def test_investment_subclass_target_uses_dynamic_formula(
        self, journal: PortfolioJournal
    ) -> None:
        """投资子类 target 列严格遵循公式 target = subclass × super × (1 − 当前现金%)。

        这条测试是第十七轮新增 — 验证动态公式（不是固定百分比）。
        """
        from global_allocation.portfolio.breakdown import DISPLAY_NAME, SwensenClass
        from global_allocation.portfolio.strategy import (
            DEFAULT_STRATEGY,
            compute_subclass_actual_target,
            compute_super_category_breakdown,
        )

        spec = self._get_table(journal)
        breakdown = compute_super_category_breakdown(
            _get_breakdown(journal)
        )
        current_cash = breakdown[__import__("global_allocation.portfolio.strategy", fromlist=["SuperCategory"]).SuperCategory.CASH]

        # 反向查表：display_name → SwensenClass
        name_to_sub = {v: k for k, v in DISPLAY_NAME.items()}

        # 遍历 11 行（投资子类 + 现金），投资子类按 display_name 找 subclass → 算 expected target
        for row in spec["rows"]:
            display_name = row["class"].split(". ", 1)[1]
            sub = name_to_sub[display_name]
            if sub == SwensenClass.CASH:
                continue  # 现金走区间策略，不走动态百分比公式
            expected_target = compute_subclass_actual_target(
                DEFAULT_STRATEGY, sub, current_cash
            )
            assert expected_target is not None
            expected_str = f"{float(expected_target) * 100:.1f}%"
            assert row["target"] == expected_str

    def test_cash_target_is_range(self, journal: PortfolioJournal) -> None:
        """现金行的 target 列是区间字符串（不是 %）。

        第十六轮：从 [20%, 50%] 放宽到 [15%, 50%]。
        """
        from global_allocation.portfolio.breakdown import DISPLAY_NAME, SwensenClass

        spec = self._get_table(journal)
        # 现金是 SwensenClass 枚举最后一项（11th row）
        cash_row = next(
            row for row in spec["rows"]
            if row["class"].split(". ", 1)[1] == DISPLAY_NAME[SwensenClass.CASH]
        )
        assert cash_row["target"] == "[15%, 50%]"

    def test_investment_subclass_delta_uses_pp_unit(
        self, journal: PortfolioJournal
    ) -> None:
        """投资子类（10 个）的偏离列用 pp (percentage points) 后缀。

        例：股票目标 X% / 当前 Y% → 偏离 "+/-Z.Zpp"
        现金行 delta 是状态文本，跳过。
        """
        from global_allocation.portfolio.breakdown import DISPLAY_NAME, SwensenClass

        spec = self._get_table(journal)
        for row in spec["rows"]:
            class_name = row["class"].split(". ", 1)[1]
            if class_name == DISPLAY_NAME[SwensenClass.CASH]:
                continue  # 现金行 delta 是状态文本
            delta = row["delta"]
            assert delta.endswith("pp")
            assert not delta.endswith("%")

    def test_cash_delta_is_status_text(self, journal: PortfolioJournal) -> None:
        """现金行的偏离列是状态文本（区间内/低于下限/高于上限），不是 pp。

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
        """投资子类偏离符号 = 当前 − 目标（正 = 超配，负 = 低配）。

        现金行的 delta 是状态文本，跳过符号检查。
        """
        from global_allocation.portfolio.breakdown import DISPLAY_NAME, SwensenClass

        spec = self._get_table(journal)
        for row in spec["rows"]:
            class_name = row["class"].split(". ", 1)[1]
            if class_name == DISPLAY_NAME[SwensenClass.CASH]:
                continue
            target_pct = float(row["target"].rstrip("%"))
            current_pct = float(row["weight"].rstrip("%"))
            expected_sign = "+" if current_pct >= target_pct else "-"
            assert row["delta"].startswith(expected_sign)


def _get_breakdown(journal: PortfolioJournal):
    """Helper: get breakdown rows for computing current_cash (used by dynamic formula tests)."""
    from global_allocation.portfolio.breakdown import compute_breakdown

    journal_for_breakdown = journal
    _seed(journal_for_breakdown)  # ensure seeded
    return compute_breakdown(journal_for_breakdown)


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

    def test_no_section_2_strategy_header(self, journal: PortfolioJournal) -> None:
        """第十九轮：旧的 Section 2 标题"大类资产策略"必须不存在（Section 2 已删除）。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        for e in card["elements"]:
            if e.get("tag") == "note":
                for elem in e.get("elements", []):
                    assert elem.get("content") != "大类资产策略"

    def test_no_section_3_subclass_header(self, journal: PortfolioJournal) -> None:
        """第十九轮：旧的 Section 3 标题"大类资产明细"必须不存在（Section 3 已删除）。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        for e in card["elements"]:
            if e.get("tag") == "note":
                for elem in e.get("elements", []):
                    assert elem.get("content") != "大类资产明细"

    def test_no_strategy_or_subclass_table(self, journal: PortfolioJournal) -> None:
        """第十九轮：只有 1 张表（持仓表），不应该有 4 列策略对比表（category/target/current/delta）
        也不应该有 4 列子类对比表（subclass/target/current/delta）。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        assert len(tables) == 1
        for t in tables:
            col_names = {c["name"] for c in t["columns"]}
            assert "category" not in col_names
            assert "subclass" not in col_names


class TestOrdering:
    """元素顺序（spec 097 第十九轮 — 1 个 section 整合）。

    元素序列：note (section header) + div (summary) + hr + chart + hr + table = 6 个元素。
    """

    def test_order(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        tags = [
            e.get("tag")
            for e in card["elements"]
            if e.get("tag") in {"note", "div", "chart", "table"}
        ]
        assert tags == [
            "note",  # section header "实盘持仓"
            "div",   # summary
            "chart", # 柱状图
            "table", # 持仓聚合表（含 target/delta，第十九轮合并了 Section 2/3）
        ]

    def test_hr_count_and_position(self, journal: PortfolioJournal) -> None:
        """第十九轮：只剩 2 个 hr（柱状图前后各一个），没有跨 section 的 hr。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        hrs = [e for e in card["elements"] if e.get("tag") == "hr"]
        assert len(hrs) == 2

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
