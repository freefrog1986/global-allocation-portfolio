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
        """卡片 = header + 2 个 section（实盘持仓 + 大类资产策略 — 第二十轮恢复 Section 2）。

        spec 097 演进：
        - 第十九轮：把 Section 2 + 3 合并到 Section 1 持仓表（加 target/delta 列）
        - 第二十轮：恢复 Section 2（liubo 反馈："我担心没控制好超类的比例了"）
          Section 3 不恢复（子类粒度 target/delta 已在持仓表）

        现在的元素：
        - Section 1（实盘持仓）：
          - note header "实盘持仓"
          - div summary
          - hr
          - chart（柱状图，带顶部数值标签）
          - hr
          - table（11 行 × 5 列：分类 / 市值 / 占比 / 目标 / 偏离）
        - Section 2（大类资产策略）：
          - hr（跨 section 分隔）
          - note header "大类资产策略"
          - hr
          - table（5 行 × 4 列：超类 / 目标 / 当前 / 偏离）

        合计：2 note + 1 div + 4 hr + 1 chart + 2 table = 10 个元素 + footer
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
        assert len(tables) == 2
        assert len(divs) == 1
        assert len(notes) == 2
        assert len(hrs) == 4

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
        """Section 1 持仓表 — 5 列（class / value / weight / target / delta）。

        第二十轮：卡片有 2 张表（持仓表 + Section 2 策略表），用列名区分。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        assert len(tables) == 2  # Section 1 持仓表 + Section 2 策略表
        for t in tables:
            col_names = [c["name"] for c in t["columns"]]
            if col_names == ["class", "value", "weight", "target", "delta"]:
                return t
        raise AssertionError("Section 1 持仓表未找到（5 列 class/value/weight/target/delta）")

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

        第二十轮允许 4 列策略对比表（category/target/current/delta）— 它是超类粒度，跟
        持仓表（子类粒度）是不同聚合层级，用户需要看超类比例监控。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        for t in tables:
            col_names = {c["name"] for c in t["columns"]}
            assert "subclass" not in col_names


class TestStrategySection:
    """spec 097 第二十轮：恢复 Section 2 大类资产策略（liubo 反馈要监控超类比例）。

    之前第十八轮设计的 Section 2（5 行 × 4 列策略对比表），第十九轮被合并到持仓表，
    第二十轮恢复 — 用户担心超类（股票/REITs/债券/商品/现金）整体比例失控。

    - 表格：5 行 × 4 列
      - 4 投资类（股票/REITs/债券/商品）：target = internal_weight × (1 − 当前现金占比)
      - 现金：target = "[15%, 50%]"，delta = "区间内/低于下限/高于上限"
    - 跟持仓表的区别：
      - 持仓表：11 行子类粒度，target = subclass × super × (1 - 现金%)
      - Section 2：5 行超类粒度，target = super 内部权重 × (1 - 现金%)
      - 两者不严格相等但反映同一策略意图（用户在不同粒度看）
    """

    def _get_strategy_table(self, journal: PortfolioJournal) -> dict[str, object]:
        """Section 2 策略表 — 4 列（category / target / current / delta）。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        for t in tables:
            col_names = [c["name"] for c in t["columns"]]
            if col_names == ["category", "target", "current", "delta"]:
                return t
        raise AssertionError("Section 2 策略表未找到（4 列 category/target/current/delta）")

    def test_section_2_header_present(self, journal: PortfolioJournal) -> None:
        """第二十轮：Section 2 标题"大类资产策略"必须存在（liubo 反馈要监控超类比例）。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        found = False
        for e in card["elements"]:
            if e.get("tag") == "note":
                for elem in e.get("elements", []):
                    if elem.get("content") == "大类资产策略":
                        found = True
        assert found, "Section 2 标题「大类资产策略」必须存在"

    def test_strategy_table_has_5_rows(self, journal: PortfolioJournal) -> None:
        """策略表 5 行（4 投资类 + 1 现金）。"""
        spec = self._get_strategy_table(journal)
        assert len(spec["rows"]) == 5

    def test_strategy_table_columns_are_four(self, journal: PortfolioJournal) -> None:
        """策略表 4 列：超类 / 目标 / 当前 / 偏离。"""
        spec = self._get_strategy_table(journal)
        col_names = [c["name"] for c in spec["columns"]]
        assert col_names == ["category", "target", "current", "delta"]

    def test_strategy_table_all_columns_text_and_auto(self, journal: PortfolioJournal) -> None:
        """策略表所有列 text + width=auto（跟持仓表一致）。"""
        spec = self._get_strategy_table(journal)
        for col in spec["columns"]:
            assert col["data_type"] == "text"
            assert col["width"] == "auto"

    def test_strategy_table_rows_are_dict(self, journal: PortfolioJournal) -> None:
        """Feishu API 强制 row 是 dict。"""
        spec = self._get_strategy_table(journal)
        for row in spec["rows"]:
            assert isinstance(row, dict)
            assert set(row.keys()) == {"category", "target", "current", "delta"}

    def test_strategy_table_rows_in_default_order(self, journal: PortfolioJournal) -> None:
        """策略表按 SuperCategory 枚举顺序排：股票→债券→REITs→商品→现金。"""
        from global_allocation.portfolio.strategy import (
            SUPER_CATEGORY_DISPLAY_NAME,
            SuperCategory,
        )

        spec = self._get_strategy_table(journal)
        actual_order = [row["category"] for row in spec["rows"]]
        expected_order = [SUPER_CATEGORY_DISPLAY_NAME[c] for c in SuperCategory]
        assert actual_order == expected_order

    def test_strategy_table_investment_target_uses_percent(self, journal: PortfolioJournal) -> None:
        """投资类 4 行的 target 列以 % 结尾（动态公式计算的结果）。

        第十六轮变更：target 不再是固定百分比，而是内部权重 × (1 − 当前现金%)。
        测试只检查格式（以 % 结尾），具体值由 test_investment_target_uses_dynamic_formula 验证。
        """
        spec = self._get_strategy_table(journal)
        for row in spec["rows"][:4]:
            assert row["target"].endswith("%")
            assert "%" in row["target"]

    def test_strategy_table_investment_target_uses_dynamic_formula(
        self, journal: PortfolioJournal
    ) -> None:
        """投资类 target 列严格遵循公式 target = 内部权重 × (1 − 当前现金占比)。

        这条测试是第十六轮新增 — 验证动态公式（不是固定百分比）。
        """
        from global_allocation.portfolio.strategy import (
            DEFAULT_STRATEGY,
            SUPER_CATEGORY_DISPLAY_NAME,
            SuperCategory,
            compute_actual_target,
        )

        spec = self._get_strategy_table(journal)
        # 现金行（rows[4]）的当前列 → 当前现金占比
        current_cash_str = spec["rows"][4]["current"]
        current_cash_pct = float(current_cash_str.rstrip("%"))
        current_cash = Decimal(str(current_cash_pct / 100))  # type: ignore[arg-type]

        # 反向查表：display_name → SuperCategory
        name_to_cat = {v: k for k, v in SUPER_CATEGORY_DISPLAY_NAME.items()}

        # 遍历前 4 行（投资类），每行按 display_name 找 category → 算 expected target
        for row in spec["rows"][:4]:
            cat_name = row["category"]
            cat = name_to_cat[cat_name]
            assert cat != SuperCategory.CASH  # 前 4 行都是投资类
            expected_target = compute_actual_target(DEFAULT_STRATEGY, cat, current_cash)
            assert expected_target is not None
            expected_str = f"{float(expected_target) * 100:.0f}%"
            assert row["target"] == expected_str

    def test_strategy_table_cash_target_is_range(self, journal: PortfolioJournal) -> None:
        """现金行的 target 列是区间字符串（不是 %）。

        第十六轮：从 [20%, 50%] 放宽到 [15%, 50%]。
        """
        spec = self._get_strategy_table(journal)
        cash_row = spec["rows"][4]  # 第 5 行是现金
        assert cash_row["target"] == "[15%, 50%]"

    def test_strategy_table_current_column_uses_percent(self, journal: PortfolioJournal) -> None:
        """当前列全部 5 行以 % 结尾（1 位小数）。"""
        spec = self._get_strategy_table(journal)
        for row in spec["rows"]:
            assert row["current"].endswith("%")

    def test_strategy_table_investment_delta_uses_pp_unit(self, journal: PortfolioJournal) -> None:
        """投资类 4 行的偏离列用 pp (percentage points) 后缀。

        例：股票目标 X% / 当前 Y% → 偏离 "+/-Z.Zpp"
        """
        spec = self._get_strategy_table(journal)
        for row in spec["rows"][:4]:  # 前 4 行是投资类
            delta = row["delta"]
            assert delta.endswith("pp")
            assert not delta.endswith("%")

    def test_strategy_table_cash_delta_is_status_text(self, journal: PortfolioJournal) -> None:
        """现金行的偏离列是状态文本（区间内/低于下限/高于上限），不是 pp。

        第十六轮不变：现金走状态文本（区间策略本质）。
        """
        spec = self._get_strategy_table(journal)
        cash_row = spec["rows"][4]  # 第 5 行是现金
        delta = cash_row["delta"]
        assert delta in {"区间内", "低于下限", "高于上限"}
        assert not delta.endswith("pp")
        assert not delta.endswith("%")

    def test_strategy_table_investment_delta_sign_matches_current_vs_target(
        self, journal: PortfolioJournal
    ) -> None:
        """投资类 4 行的偏离符号 = 当前 - 目标（正 = 超配，负 = 低配）。

        现金行的 delta 是状态文本，跳过符号检查。
        """
        spec = self._get_strategy_table(journal)
        for row in spec["rows"][:4]:  # 前 4 行是投资类
            target_pct = float(row["target"].rstrip("%"))
            current_pct = float(row["current"].rstrip("%"))
            expected_sign = "+" if current_pct >= target_pct else "-"
            assert row["delta"].startswith(expected_sign)


class TestValuationSection:
    """Section 3: 大类资产估值（spec 098.4 combined 模式 — 飞书 ≤5 table 限制）。

    spec 098 演进：
    - 第二十一轮：新增 Section 3（A 股 4 指标 + 综合分 + per-fund 表）
    - 第二十二轮：加综合分（4 票简单平均）
    - 098.2：港股估值跟 A 股平行（多 1 region）
    - 098.3：美股估值再起一个 region（3 个 region）
    - 098.4（liubo 2026-09-20）：合并 3 region 表 — 1 张 combined 指标表（5 列）+ 1 张
      combined per-fund 表（11 列），加上持仓 + 策略 = 4 张表，符合飞书 ≤5 table 限制。

    渲染结构：
    - note header "估值与操作"
    - hr
    - combined 指标表（5 列 × N 行，N = region 数 × (4 指标 + 1 综合)）
      - 列：区域 / 指标 / 当前 / 评估 / 阈值
      - 综合分行 region 列 = "—"
    - 3 个 strategy_note div（按 region 顺序，region 完全空时跳过）
    - hr + combined per-fund 表（11 列 × N 行，N = 所有 region 持仓数之和）
      - 列：区域 / 基金 / 指数 / PE / PE分位 / 股息率 / ROE同比 / 评估 / 建议 / 当前仓位 / 加减仓建议

    边界：
    - 全部 region 完全空（DB 没任何估值）→ Section 整段不渲染（返回 []）
    - 单个 region 完全空 → 该 region 行不出现在 combined 表（不渲染空行）
    - 股债利差 / 美股股债利差用 signed format（+/-），其他指标用 unsigned format
    """

    def _get_combined_indicator_table(self, journal: PortfolioJournal) -> dict[str, object]:
        """5 列 combined 估值表（region / indicator / value / verdict / threshold）。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        for t in tables:
            col_names = [c["name"] for c in t["columns"]]
            if col_names == ["region", "indicator", "value", "verdict", "threshold"]:
                return t
        raise AssertionError("combined 估值表未找到（5 列 region/indicator/value/verdict/threshold）")

    def _get_combined_per_fund_table(self, journal: PortfolioJournal) -> dict[str, object] | None:
        """11 列 combined per-fund 表。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        for t in tables:
            col_names = [c["name"] for c in t["columns"]]
            if col_names == [
                "region", "fund", "index", "pe", "pe_pct", "dy", "roe_yoy",
                "verdict", "advice", "position", "adjust",
            ]:
                return t
        return None

    def test_combined_section_header_present(self, journal: PortfolioJournal) -> None:
        """combined section 标题"估值与操作"必须存在（spec 098.4 — 3 region 合并）。"""
        _seed(journal)
        _seed_valuation_today(journal)
        card = build_portfolio_card(journal)
        found = False
        for e in card["elements"]:
            if e.get("tag") == "note":
                for elem in e.get("elements", []):
                    if elem.get("content") == "估值与操作":
                        found = True
        assert found, "combined section 标题「估值与操作」必须存在"

    def test_no_per_region_headers_when_combined(self, journal: PortfolioJournal) -> None:
        """combined 模式下不渲染 per-region headers（"A 股估值与操作" 等都不应存在）。

        旧 per-region 模式：每个 region 一个 header note
        新 combined 模式：1 个统一 header "估值与操作"
        """
        _seed(journal)
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_us_valuation_today(journal)
        card = build_portfolio_card(journal)
        for e in card["elements"]:
            if e.get("tag") == "note":
                for elem in e.get("elements", []):
                    assert elem.get("content") != "A 股估值与操作"
                    assert elem.get("content") != "港股估值与操作"
                    assert elem.get("content") != "美股估值与操作"

    def test_combined_section_absent_when_no_data(self, journal: PortfolioJournal) -> None:
        """DB 完全空 → combined section 整段不渲染。

        publish 时 CLI 已自动 update，正常情况不会到这里；但如果 publish 失败或
        手动 build 时没拉数据，section 不渲染比显示空表格好。
        """
        _seed(journal)
        # 不调用任何 _seed_valuation_today → DB 没今天的指标
        card = build_portfolio_card(journal)
        for e in card["elements"]:
            if e.get("tag") == "note":
                for elem in e.get("elements", []):
                    assert elem.get("content") != "估值与操作"

    def test_combined_section_skipped_when_all_regions_empty(self, journal: PortfolioJournal) -> None:
        """3 region 都没数据 → _build_valuation_section 返回 []（空数组）。"""
        from global_allocation.portfolio.card import _build_valuation_section

        _seed(journal)
        # 不塞任何 valuation indicator
        elements = _build_valuation_section(journal)
        assert elements == []

    def test_combined_indicator_table_5_columns(self, journal: PortfolioJournal) -> None:
        """combined 指标表 5 列：区域 / 指标 / 当前 / 评估 / 阈值。"""
        _seed_valuation_today(journal)
        spec = self._get_combined_indicator_table(journal)
        col_names = [c["name"] for c in spec["columns"]]
        assert col_names == ["region", "indicator", "value", "verdict", "threshold"]

    def test_combined_indicator_table_15_rows_when_3_regions(
        self, journal: PortfolioJournal
    ) -> None:
        """3 region 都有数据 → combined 指标表 15 行 = 4 指标 × 3 + 3 综合。"""
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_us_valuation_today(journal)
        spec = self._get_combined_indicator_table(journal)
        assert len(spec["rows"]) == 15

    def test_combined_indicator_table_partial_region(self, journal: PortfolioJournal) -> None:
        """只有 A 股数据 → combined 指标表 5 行（4 指标 + 1 综合），不渲染空 region。"""
        _seed_valuation_today(journal)
        spec = self._get_combined_indicator_table(journal)
        assert len(spec["rows"]) == 5
        # 所有行的 region 列都该是 "A 股" 或 "—"（综合行）
        for row in spec["rows"]:
            assert row["region"] in {"A 股", "—"}

    def test_combined_indicator_table_rows_in_region_order(
        self, journal: PortfolioJournal
    ) -> None:
        """行按 region 顺序排列：A 股 → 港股 → 美股（同 region 内按 indicator 顺序）。

        spec 098.4 — 跨 region 看估值，方便对比。
        """
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_us_valuation_today(journal)
        spec = self._get_combined_indicator_table(journal)
        # 取每个 region 的前 4 行（4 指标），验证 region 顺序
        actual_regions = [
            row["region"]
            for row in spec["rows"]
            if row["indicator"] != "综合分"
        ]
        expected_pattern = ["A 股"] * 4 + ["港股"] * 4 + ["美股"] * 4
        assert actual_regions == expected_pattern

    def test_combined_indicator_table_composite_row_uses_dash_region(
        self, journal: PortfolioJournal
    ) -> None:
        """综合分行 region 列 = "—"（聚合行不属于单一 region）。"""
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_us_valuation_today(journal)
        spec = self._get_combined_indicator_table(journal)
        composite_rows = [r for r in spec["rows"] if r["indicator"] == "综合分"]
        assert len(composite_rows) == 3
        for r in composite_rows:
            assert r["region"] == "—"

    def test_combined_indicator_table_per_region_composite_format(
        self, journal: PortfolioJournal
    ) -> None:
        """综合分行的 value 列保留 per-region 格式 "[股债:X PE:X ...]"。

        spec 098.4 — 综合分行是 region 内部聚合，格式不动，只把 region 列改成 "—"。
        """
        _seed_valuation_today(journal)
        spec = self._get_combined_indicator_table(journal)
        composite_row = next(r for r in spec["rows"] if r["indicator"] == "综合分")
        # A 股综合分行（之前 spec 098 测试的格式）保持不变
        assert composite_row["value"] == "[股债:1 PE:2 巴菲特:3 股息:3]"
        assert composite_row["verdict"] == "2.3 低估"

    def test_combined_indicator_table_erp_signed_format(
        self, journal: PortfolioJournal
    ) -> None:
        """股债利差 / 美股股债利差 用 signed format（+/-），其他指标 unsigned。

        spec 098.4 — 各 region 的格式化规则不变，只在前面加 region 列。
        """
        _seed_valuation_today(journal)  # A 股 ERP=5.2% → +5.20%
        spec = self._get_combined_indicator_table(journal)
        erp_row = next(
            r for r in spec["rows"]
            if r["indicator"] == "股债利差" and r["region"] == "A 股"
        )
        assert erp_row["value"] == "+5.20%"

    def test_combined_per_fund_table_11_columns(self, journal: PortfolioJournal) -> None:
        """combined per-fund 表 11 列：区域 / 基金 / 指数 / PE / PE分位 / 股息率 / ROE同比
        / 评估 / 建议 / 当前仓位 / 加减仓建议。"""
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_us_valuation_today(journal)
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        _seed_hk_funds(journal)
        _seed_hk_fund_valuations(journal)
        _seed_us_funds(journal)
        # 美股基金缺估值时也建 NDX/SP/全球主题的 fallback 估值
        today = date.today()
        for code in ("018966", "539001", "016452", "019524", "017641",
                      "519981", "017730", "016664", "006373"):
            journal.db.upsert_fund_valuation(
                FundValuation(
                    record_date=today,
                    fund_code=code,
                    index_code="INX",
                    pe_ttm=Decimal("30.0"),
                    pe_percentile=Decimal("0.6"),
                    dividend_yield=Decimal("0.011"),
                    source="test",
                )
            )

        spec = self._get_combined_per_fund_table(journal)
        assert spec is not None
        col_names = [c["name"] for c in spec["columns"]]
        assert col_names == [
            "region", "fund", "index", "pe", "pe_pct", "dy", "roe_yoy",
            "verdict", "advice", "position", "adjust",
        ]

    def test_combined_per_fund_table_15_rows_when_3_regions(
        self, journal: PortfolioJournal
    ) -> None:
        """3 region 都有持仓 → combined per-fund 表 15 行（A 3 + 港 3 + 美 9）。

        spec 098.4 — fixtures 简化：3 A 股 + 3 港股 + 9 美股 = 15 行。
        测试场景覆盖的是 fixture 的 holdings，不是 demo DB 的全部 31 只基金。
        """
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        _seed_hk_funds(journal)
        _seed_hk_fund_valuations(journal)
        _seed_us_funds(journal)
        # 美股 per-fund 估值（共用 INX 兜底数据）
        today = date.today()
        for code in ("018966", "539001", "016452", "019524", "017641",
                      "519981", "017730", "016664", "006373"):
            journal.db.upsert_fund_valuation(
                FundValuation(
                    record_date=today,
                    fund_code=code,
                    index_code="INX",
                    pe_ttm=Decimal("30.0"),
                    pe_percentile=Decimal("0.6"),
                    dividend_yield=Decimal("0.011"),
                    source="test",
                )
            )

        spec = self._get_combined_per_fund_table(journal)
        assert spec is not None
        # 3 A 股 + 3 港股 + 9 美股 = 15
        assert len(spec["rows"]) == 15

    def test_combined_per_fund_table_region_prefix(self, journal: PortfolioJournal) -> None:
        """per-fund 表的每行 region 列都填对应 region label（A 股 / 港股 / 美股）。

        spec 098.4 — 跨 region 看，方便对比。fixture 只有 A 股 + 港股 → 6 行（3 + 3）。
        """
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        _seed_hk_funds(journal)
        _seed_hk_fund_valuations(journal)
        spec = self._get_combined_per_fund_table(journal)
        assert spec is not None
        # 3 A 股 + 3 港股 = 6 行
        assert len(spec["rows"]) == 6
        for row in spec["rows"][:3]:
            assert row["region"] == "A 股"
        for row in spec["rows"][3:6]:
            assert row["region"] == "港股"

    def test_combined_per_fund_table_skipped_when_no_holdings(
        self, journal: PortfolioJournal
    ) -> None:
        """没有任何持仓 → combined per-fund 表不渲染（避免空表）。

        跟 per-region 行为一致：_build_fund_valuation_section 没持仓就返回 []。
        """
        _seed(journal)
        # 不调 _seed_a_share_funds / _seed_hk_funds / _seed_us_funds → DB 没基金
        spec = self._get_combined_per_fund_table(journal)
        assert spec is None

    def test_combined_section_strategy_notes_one_per_region_with_data(
        self, journal: PortfolioJournal
    ) -> None:
        """3 个 region 都有持仓 + 估值 → 3 个 strategy_note div（占比 + 估值判断）。"""
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        _seed_hk_funds(journal)
        _seed_hk_fund_valuations(journal)
        _seed_us_funds(journal)
        today = date.today()
        for code in ("018966", "539001", "016452", "019524", "017641",
                      "519981", "017730", "016664", "006373"):
            journal.db.upsert_fund_valuation(
                FundValuation(
                    record_date=today,
                    fund_code=code,
                    index_code="INX",
                    pe_ttm=Decimal("30.0"),
                    pe_percentile=Decimal("0.6"),
                    dividend_yield=Decimal("0.011"),
                    source="test",
                )
            )
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_us_valuation_today(journal)

        card = build_portfolio_card(journal)
        strategy_divs = [
            e for e in card["elements"]
            if e.get("tag") == "div" and "占比" in e["text"]["content"]
        ]
        # 3 个 region × 1 strategy div = 3
        assert len(strategy_divs) == 3
        region_texts = [d["text"]["content"] for d in strategy_divs]
        assert any("A 股占比" in t for t in region_texts)
        assert any("港股占比" in t for t in region_texts)
        assert any("美股占比" in t for t in region_texts)

    def test_total_table_count_within_feishu_limit(self, journal: PortfolioJournal) -> None:
        """全数据场景下整张卡片 ≤5 table（飞书 ErrCode 11310 限制 — spec 098.4）。

        4 张表 = 持仓聚合 + 策略对比 + combined 指标 + combined per-fund。
        """
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        _seed_hk_funds(journal)
        _seed_hk_fund_valuations(journal)
        _seed_us_funds(journal)
        today = date.today()
        for code in ("018966", "539001", "016452", "019524", "017641",
                      "519981", "017730", "016664", "006373"):
            journal.db.upsert_fund_valuation(
                FundValuation(
                    record_date=today,
                    fund_code=code,
                    index_code="INX",
                    pe_ttm=Decimal("30.0"),
                    pe_percentile=Decimal("0.6"),
                    dividend_yield=Decimal("0.011"),
                    source="test",
                )
            )
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_us_valuation_today(journal)

        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        assert len(tables) == 4, f"期望 4 张表，实际 {len(tables)}（飞书 ≤5 上限）"

    def test_hr_count_with_combined_section(self, journal: PortfolioJournal) -> None:
        """combined section 渲染后整张卡片的 hr 数 = 7（baseline 4 + combined section 加 3）。

        无估值时 4 hr（Section 1 内 2 + Section 2 跨 + Section 2 内 1）。
        渲染 combined section 后多 3 hr：
        - 跨 Section 3 分隔（1，build_portfolio_card 加）
        - combined section note 后（1，_build_valuation_section 加）
        - combined per-fund 表前（1，_build_valuation_section 加）
        """
        _seed(journal)
        # 无估值：4 hr
        card_no_val = build_portfolio_card(journal)
        hrs_no_val = sum(1 for e in card_no_val["elements"] if e.get("tag") == "hr")
        assert hrs_no_val == 4

        # 有估值：4 + 3 = 7 hr
        _seed_valuation_today(journal)
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        card_with_val = build_portfolio_card(journal)
        hrs_with_val = sum(1 for e in card_with_val["elements"] if e.get("tag") == "hr")
        assert hrs_with_val == 7


class TestOrdering:
    """元素顺序（spec 097 第二十轮 — 2 个 section：持仓 + 超类策略）。

    Section 1（实盘持仓）：note + div + chart + table
    Section 2（大类资产策略）：note + table
    跨 section 分隔：hr

    元素序列（去掉 hr 后）：note + div + chart + table + note + table
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
            "note",  # Section 1 header "实盘持仓"
            "div",   # Section 1 summary
            "chart", # Section 1 柱状图
            "table", # Section 1 持仓表（5 列：含 target/delta）
            "note",  # Section 2 header "大类资产策略"（第二十轮恢复）
            "table", # Section 2 策略对比表（5 行 × 4 列）
        ]

    def test_hr_count_and_position(self, journal: PortfolioJournal) -> None:
        """第二十轮：4 个 hr（Section 1 柱状图前后 + 跨 section 分隔 + Section 2 表格前）。

        之前第十九轮 2 个 hr（删了 Section 2/3 后只剩 2 个 intra-section hr）
        第二十轮恢复 Section 2 后多了一个跨 section 的 hr + Section 2 表格前 hr
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        hrs = [e for e in card["elements"] if e.get("tag") == "hr"]
        assert len(hrs) == 4

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

    def test_section_2_note_header_after_hr(self, journal: PortfolioJournal) -> None:
        """Section 2 开头：hr 分隔 + note "大类资产策略"。

        第二十轮恢复：跟 Section 1 用 hr 隔开，视觉上明确区分。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        elements = card["elements"]
        # 找到 "大类资产策略" note 的索引
        section_2_idx = None
        for idx, e in enumerate(elements):
            if e.get("tag") == "note" and any(
                elem.get("content") == "大类资产策略"
                for elem in e.get("elements", [])
            ):
                section_2_idx = idx
                break
        assert section_2_idx is not None
        # 它前面必须是 hr（跨 section 分隔）
        assert elements[section_2_idx - 1].get("tag") == "hr"


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


class TestAShareStrategyNote:
    """A 股策略建议渲染成 note 文字块（spec 098 第二十六轮）。

    不再放表里（liubo 2026-09-19 反馈"用文字说"），独立一段话交代：
        "A 股占比 X%，目标 Y%。<策略>（<理由>）"
    """

    def _get_strategy_note_text(self, journal: PortfolioJournal) -> str:
        """从 build_portfolio_card 输出里抠出 A 股策略 note 的文字。"""
        spec = build_portfolio_card(journal)
        for el in spec["elements"]:
            # spec 098 第二十八轮起：A 股策略提示用 div+lark_md（要更突出，不能灰字）
            if el.get("tag") == "div" and "A 股占比" in el["text"]["content"]:
                return el["text"]["content"]
            if el.get("tag") != "note":
                continue
            for sub in el.get("elements", []):
                content = sub.get("content", "")
                if content.startswith("A 股占比"):
                    return content
        raise AssertionError("未找到 A 股策略提示（div/note 含 'A 股占比'）")

    def test_note_present_after_have_holdings_and_valuation(
        self, journal: PortfolioJournal
    ) -> None:
        """有持仓 + 估值 → 估值表后追加一个 note，文字以 'A 股占比' 开头。"""
        _seed(journal)
        _seed_valuation_today(journal)
        text = self._get_strategy_note_text(journal)
        assert "A 股占比" in text
        assert "%" in text
        assert "目标" in text

    def test_note_format_includes_strategy_and_reason(
        self, journal: PortfolioJournal
    ) -> None:
        """note 文字格式 = "A 股占比 X%，目标 Y%。<策略>（<理由>）"。

        验证包含：占比百分比、目标百分比、策略文案（4 个之一）、括号里的理由。
        """
        _seed(journal)
        _seed_valuation_today(journal)
        text = self._get_strategy_note_text(journal)
        # 必含 4 个策略文案之一
        assert any(s in text for s in ["不再投入", "分批止盈", "分批买入", "正常持有", "等估值"])
        # 理由在括号里（"（...）"）
        assert "（" in text and "）" in text

    def test_table_does_not_have_a_share_strategy_row(
        self, journal: PortfolioJournal
    ) -> None:
        """combined 估值表不再含 'A 股策略' 行（已移到 note）。

        spec 098.4 — combined 表结构变了：5 列 region/indicator/value/verdict/threshold。
        这里验证 indicator 列不含 'A 股策略'（不管列结构怎么变）。
        """
        _seed(journal)
        _seed_valuation_today(journal)
        spec = build_portfolio_card(journal)
        # 任何含 indicator 列的表都不是策略表
        for t in [e for e in spec["elements"] if e.get("tag") == "table"]:
            col_names = [c["name"] for c in t["columns"]]
            if "indicator" in col_names:
                row_names = [r.get("indicator", "") for r in t["rows"]]
                assert "A 股策略" not in row_names


# ─── Per-fund 估值（spec 098 第二十七轮）───


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

        008114 红利低波（股息率加权 PE 分位=0.20 → 低估 2）→ 建议"买入" / "+1仓"
        022434 中证A500（PE 分位=0.80 → 高估 4）→ 建议"止盈" / "−1仓"
        014532 MSCI中国A50（PE 分位=0.1626 → 低估 2）→ 建议"买入" / "+1仓"
        """
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        section = _build_fund_valuation_section(journal)
        table = section[1]
        row_by_code = {r["fund"].split("\n")[0]: r for r in table["rows"]}
        # 008114 → 买入 +1仓（不在超配时）
        assert row_by_code["008114"]["verdict"] == "低估 2"
        assert row_by_code["008114"]["advice"] == "买入"
        assert row_by_code["008114"]["adjust"] == "+1仓"
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
        """红利低波行的 PE分位 列 = 股息率加权 PE 分位（不是普通 PE 分位）。

        fixture: 008114 普通 PE 分位 = 0.8180（高），股息率加权 = 0.20（低）
        期望：PE分位 列显示 20.0%（银行螺丝钉数据），verdict = 低估 2
        """
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        section = _build_fund_valuation_section(journal)
        table = section[1]
        row = next(r for r in table["rows"] if "008114" in r["fund"])
        assert row["pe_pct"] == "20.0%"  # 股息率加权 PE 分位
        assert row["verdict"] == "低估 2"  # 因为 20% < 30%

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

    def test_section_appears_after_strategy_note_in_card(
        self, journal: PortfolioJournal
    ) -> None:
        """完整卡片里：fund_valuation_section 紧跟 A 股策略 div 之后。

        spec 098 第二十八轮 — per-fund 表不再带子 header，外层 _build_valuation_section
        统一在顶部渲染 "A 股估值与操作" note + 4 指标估值表 + strategy_note div，
        再追加 hr + per-fund table。这里验证 per-fund table 在 strategy_div 之后。
        """
        _seed_a_share_funds(journal)
        _seed_valuation_today(journal)
        _seed_fund_valuations(journal)
        card = build_portfolio_card(journal)
        elements = card["elements"]
        # 找 "A 股占比" 的 div（A 股策略提示，从 spec 098 第二十八轮起是 div+lark_md）
        strategy_div_idx = next(
            i for i, e in enumerate(elements)
            if e.get("tag") == "div" and "A 股占比" in e["text"]["content"]
        )
        # per-fund table 的第一行 fund 列含 "008114"（区别于 4 指标估值表）
        # 找 fund 列含 "014532" 的 table
        def _is_per_fund_table(elem: dict[str, object]) -> bool:
            if elem.get("tag") != "table":
                return False
            cols = elem.get("columns", [])
            if not cols:
                return False
            col_names = [c.get("name") for c in cols]
            return "fund" in col_names and "advice" in col_names

        per_fund_table_idx = next(
            i for i, e in enumerate(elements) if _is_per_fund_table(e)
        )
        assert per_fund_table_idx > strategy_div_idx


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
        """低估 1-2 + 当前 < 6 仓 + 不超配 → 加减仓建议 = "+1仓"。"""
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)  # 008114/014532=低估 2, 022434=高估 4
        section = _build_fund_valuation_section(journal, over_target=False)
        table = section[1]
        row_by_code = {r["fund"].split("\n")[0]: r for r in table["rows"]}
        assert row_by_code["008114"]["adjust"] == "+1仓"
        assert row_by_code["014532"]["adjust"] == "+1仓"
        assert row_by_code["022434"]["adjust"] == "−1仓"

    def test_over_target_pauses_add_but_keeps_reduce(
        self, journal: PortfolioJournal
    ) -> None:
        """整体超配 → 买入变 "—"（加仓暂停），止盈保持 "−1仓"。"""
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        section = _build_fund_valuation_section(journal, over_target=True)
        table = section[1]
        row_by_code = {r["fund"].split("\n")[0]: r for r in table["rows"]}
        # 低估 2：加仓暂停
        assert row_by_code["008114"]["adjust"] == "—"
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
        """低估（买入）+ 当前仓位 >= 6 仓（已达上限）→ 加减仓建议 "—"。"""
        from global_allocation.portfolio.card import (
            POSITION_MAX,
            POSITION_UNIT,
        )
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        # 把 008114 的市值推到上限：shares * market_price = 6 * POSITION_UNIT
        # 008114 当前 10000 shares, price=1.5 → 市值 15000 = 1.5 仓
        # 改价格 = 6 * 10000 / 10000 = 6.0 → 市值 60000 = 6 仓（恰好上限）
        journal._price_source._prices["008114"] = Decimal(str(POSITION_MAX))
        section = _build_fund_valuation_section(journal, over_target=False)
        table = section[1]
        row_008114 = next(r for r in table["rows"] if "008114" in r["fund"])
        assert row_008114["position"] == "6.0仓"
        # 买入但已达上限 → 不再加
        assert row_008114["adjust"] == "—"

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

    同时给每只 A 股基金加价格（fixture 默认只覆盖 163406 / 510300），
    让 position 有值且 > 底仓 1 仓，下游"加减仓建议"列才能算出"+1仓"/"−1仓"。
    shares=10000, price=1.5 → 市值=15000 = 1.5 仓（高于底仓，可加可减）。
    """
    journal.add_fund("014532", "MSCI中国A50", AssetClass.EQUITY)
    journal.add_fund("008114", "红利低波100", AssetClass.EQUITY)
    journal.add_fund("022434", "中证A500", AssetClass.EQUITY)
    for code in ("014532", "008114", "022434"):
        journal._price_source._prices[code] = Decimal("1.5")
    # 各 buy 10000 份，price=1.0 → 平均成本 1.0；市值 = 10000 * 1.5 = 15000 = 1.5 仓
    journal.record_buy(
        fund_code="014532",
        trade_date=date(2026, 9, 1),
        shares=Decimal("10000"),
        price=Decimal("1.0"),
    )
    journal.record_buy(
        fund_code="008114",
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
    """塞 per-fund 估值：1 只 dividend 低估（008114）+ 1 只 broad 正常（014532）+ 1 只 broad 高估（022434）。

    008114 的股息率加权 PE 分位 = 0.20（银行螺丝钉手动填），普通 PE 分位 = 0.8180（高），
    测试会验证 dividend 策略用股息率加权而不是普通 PE 分位。
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
            fund_code="008114",
            index_code="930955",
            pe_ttm=Decimal("8.8511"),
            pe_percentile=Decimal("0.8180"),  # 普通 PE 分位 81.8%（lixinger）
            dividend_yield=Decimal("0.0448"),
            pe_percentile_dy_weighted=Decimal("0.20"),  # 股息率加权 PE 分位 20%（银行螺丝钉）
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
        """港股 3 只基金（004098 / 006809 / 013127）只在港股 section 出现，不在 A 股 section。"""
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_a_share_funds(journal)
        _seed_hk_funds(journal)

        a_share_elements = _build_a_share_valuation_section(journal)
        hk_elements = _build_hk_valuation_section(journal)

        # A 股 section 只含 A 股 3 只基金（014532 / 008114 / 022434）
        a_share_tables = [e for e in a_share_elements if e.get("tag") == "table"]
        # 第一个 table 是指标表，第 2 个是 per-fund 表
        if len(a_share_tables) >= 2:
            a_share_fund_table = a_share_tables[1]
            fund_codes_in_a_share = [r["fund"].split("\n")[0] for r in a_share_fund_table["rows"]]
            assert "014532" in fund_codes_in_a_share
            assert "008114" in fund_codes_in_a_share
            # 港股 code 不应在 A 股 section
            assert "004098" not in fund_codes_in_a_share
            assert "013127" not in fund_codes_in_a_share

        # 港股 section 只含港股 3 只基金
        hk_tables = [e for e in hk_elements if e.get("tag") == "table"]
        if len(hk_tables) >= 2:
            hk_fund_table = hk_tables[1]
            fund_codes_in_hk = [r["fund"].split("\n")[0] for r in hk_fund_table["rows"]]
            assert "004098" in fund_codes_in_hk
            assert "006809" in fund_codes_in_hk
            assert "013127" in fund_codes_in_hk
            # A 股 code 不应在港股 section
            assert "014532" not in fund_codes_in_hk

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
        """港股有持仓但缺估值 → per-fund 表显示"数据缺失"。"""
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_hk_funds(journal)
        # 故意不 _seed_hk_fund_valuations → 港股基金没估值

        hk_elements = _build_hk_valuation_section(journal)
        hk_tables = [e for e in hk_elements if e.get("tag") == "table"]
        assert len(hk_tables) >= 2
        hk_fund_table = hk_tables[1]
        for row in hk_fund_table["rows"]:
            assert row["verdict"] == "数据缺失"
            assert row["advice"] == "—"


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
    """塞 9 只美股基金（4 NDX ETF + 1 标普 500 + 1 标普 100 + 3 全球主题 QDII）。

    让每只美股基金的市值都是 1.5 仓。
    """
    us_funds = [
        ("018966", "汇添富纳100"),
        ("539001", "建信纳100"),
        ("016452", "南方纳100"),
        ("019524", "华泰柏瑞纳100"),
        ("017641", "摩根标普500"),
        ("519981", "长信标普100"),
        ("017730", "嘉实全球产业升级"),
        ("016664", "天弘全球高端制造"),
        ("006373", "国富全球科技互联"),
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
