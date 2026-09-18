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
        """卡片 = header + 2 个 section（实盘持仓 + 具体策略）。

        spec 097 第十四轮反馈：周报分多 section。
        - Section 1（实盘持仓）：1 note + 1 div + 1 chart + 1 table
        - Section 2（具体策略）：1 note + 1 div + 1 table
        合计：2 chart + 2 table + 2 div + 2 note + 5 hr = 14 个元素 + footer
        """
        _seed(journal)
        card = build_portfolio_card(journal, title="我的实盘")
        assert "header" in card
        assert card["header"]["template"] == "blue"
        assert "elements" in card
        # Section 1: 1 chart + 1 table + 1 div + 1 note
        # Section 2: 1 table + 1 div + 1 note
        charts = [e for e in card["elements"] if e.get("tag") == "chart"]
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        divs = [e for e in card["elements"] if e.get("tag") == "div"]
        notes = [e for e in card["elements"] if e.get("tag") == "note"]
        assert len(charts) == 1
        assert len(tables) == 2
        assert len(divs) == 2
        assert len(notes) == 2

    def test_summary_includes_total_and_return(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        divs = [e for e in card["elements"] if e.get("tag") == "div"]
        # 第一个 div 是 Section 1 summary（总市值 / 盈亏 / 周涨跌）
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
        # Section 1 summary div（不是 Section 2 策略文字 div）
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
    """柱状图：各大类资产市值（vertical bar，按 SwensenClass 枚举顺序，全部 14 类）。"""

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

    def test_no_label_or_axes_field(self, journal: PortfolioJournal) -> None:
        """第十一轮最终：飞书 VChart 卡片组件不支持 % 后缀，去掉 label / axes 字段。

        试过三次都失败：
        - axes[*].label.formatMethod（JS 函数）：飞书不解析，整图加载失败
        - bar.label.formatMethod（JS 函数）：同上
        - bar.label.formatter="{value}%"（字符串模板）：基础 {value} 替换在飞书内置
          VChart 版本不工作，显示成字面 "%Y6%"

        标题"各大类资产占比（%）"明示单位，柱子顶上 VChart 默认显示 yField 数值即可。
        """
        spec = self._get_bar(journal)
        assert "label" not in spec
        assert "axes" not in spec

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
        """柱状图按 SwensenClass 枚举顺序展示全部 14 个子类（不按市值倒序排）。"""
        from global_allocation.portfolio.breakdown import DISPLAY_NAME, SwensenClass

        spec = self._get_bar(journal)
        actual_order = [item["class"] for item in spec["data"]["values"]]
        expected_order = [DISPLAY_NAME[c] for c in SwensenClass]
        assert actual_order == expected_order
        assert len(actual_order) == 14  # 14 个子类，没漏

    def test_empty_classes_have_zero_weight(self, journal: PortfolioJournal) -> None:
        """空子类（count=0）也展示，weight=0。这样能看出框架里哪些没覆盖到。"""
        spec = self._get_bar(journal)
        zero_count = sum(1 for item in spec["data"]["values"] if item["weight"] == 0)
        # 测试只塞了 2 个基金，SUBCLASS mapping 也没覆盖，所以应该全是 0
        # （实际生产时 = count=0 的子类数，演示数据 = 14）
        assert zero_count >= 1  # 至少有一些是 0（SUBCLASS 缺失的）


class TestHoldingsTable:
    """持仓聚合表：按 Swensen 大类聚合（不再下钻单只基金）。

    3 列：class（含 "#. " 前缀）/ value / weight。按 SwensenClass 枚举顺序展示全部 14 个子类。
    第五轮反馈：Feishu table column width 只接受 "auto"，无法把 # 列单独做窄。
    折中方案：把 # 信息嵌进分类名前缀（"1. A 股股票"），干掉单独的 # 列。
    """

    def _get_table(self, journal: PortfolioJournal) -> dict[str, object]:
        """Section 1 持仓表 — 3 列（class / value / weight）。

        卡片现在有 2 个 table（Section 1 持仓表 + Section 2 策略表 4 列），
        用列名区分。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        for t in tables:
            col_names = [c["name"] for c in t["columns"]]
            if col_names == ["class", "value", "weight"]:
                return t
        raise AssertionError("Section 1 持仓表未找到（3 列 class/value/weight）")

    def test_columns_are_three(self, journal: PortfolioJournal) -> None:
        """3 列：分类 / 市值 / 占比（不再有单独 # 列、code、name、count）。"""
        spec = self._get_table(journal)
        col_names = [c["name"] for c in spec["columns"]]
        assert col_names == ["class", "value", "weight"]

    def test_rows_are_dict_shaped(self, journal: PortfolioJournal) -> None:
        """Feishu API 强制 row 是 dict（按列名取）。"""
        spec = self._get_table(journal)
        for row in spec["rows"]:
            assert isinstance(row, dict)
            assert set(row.keys()) == {"class", "value", "weight"}

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
        """表展示全部 14 个子类（含 count=0 的），不是只展示有持仓的。"""
        spec = self._get_table(journal)
        assert len(spec["rows"]) == 14

    def test_all_columns_text_type(self, journal: PortfolioJournal) -> None:
        """class / value / weight 全是预格式化字符串，全 text 列。"""
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


class TestStrategySection:
    """spec 097 第十六轮：Section 2 具体策略（内部权重模型 + 现金 [15%, 50%]）。

    - Layer 2a 文字：4 投资类内部权重（股票 70% / REITs 15% / 债券 10% / 商品 5%，
      按收益率排序，合计 = 投资部分 100%）
    - Layer 2b 文字：现金子弹区间 [15%, 50%]（区间策略）
    - Layer 1 文字：14 子类上限摘要
    - 表格：5 行 × 4 列
      - 投资类 4 行：target = 内部权重 × (1 − 当前现金占比)（动态）
      - 现金 1 行：target = "[15%, 50%]"，delta = "区间内/低于下限/高于上限"
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

    def _get_strategy_text(self, journal: PortfolioJournal) -> str:
        """Section 2 策略 div 文字（现金区间 + 4 投资类内部权重 + 子类上限摘要）。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        # Section 2 div 是第二个 div（第一个是 Section 1 summary）
        divs = [e for e in card["elements"] if e.get("tag") == "div"]
        assert len(divs) >= 2
        return divs[1]["text"]["content"]  # type: ignore[arg-type, return-value]

    def test_strategy_text_lists_4_investment_weights(self, journal: PortfolioJournal) -> None:
        """策略文字列出 4 个投资类内部权重（按收益率排序，第十六轮）。

        内部权重（不是绝对目标）：股票 70% / REITs 15% / 债券 10% / 商品 5%。
        跟第十五轮的"绝对目标"不同：内部权重和 = 100% 投资部分，实际目标 = 权重 × (1 - cash)。
        """
        text = self._get_strategy_text(journal)
        assert "**股票**：70%" in text
        assert "**REITs**：15%" in text
        assert "**债券**：10%" in text
        assert "**商品**：5%" in text

    def test_strategy_text_does_not_list_cash_as_percent(self, journal: PortfolioJournal) -> None:
        """第十六轮不变：现金不再以"**现金**：X%" 形式列出（走区间策略）。"""
        text = self._get_strategy_text(journal)
        # 不应该有 "**现金**：3%"（旧格式）
        assert "**现金**：3%" not in text

    def test_strategy_text_lists_cash_range(self, journal: PortfolioJournal) -> None:
        """策略文字列出现金子弹区间（第十六轮：下限放宽到 15%）。"""
        text = self._get_strategy_text(journal)
        # 默认区间 [15%, 50%]（第十六轮从 [20%, 50%] 放宽）
        assert "**现金子弹**：[15%, 50%]" in text
        # 区间策略说明
        assert "区间策略" in text

    def test_strategy_text_mentions_internal_weights_label(self, journal: PortfolioJournal) -> None:
        """策略文字明确说"内部权重"（让用户区分于绝对目标）。"""
        text = self._get_strategy_text(journal)
        assert "内部权重" in text
        # 强调按收益率排序（用户明确反馈）
        assert "按收益率排序" in text

    def test_strategy_text_mentions_subclass_caps(self, journal: PortfolioJournal) -> None:
        """策略文字提到子类上限（Layer 1 摘要）。"""
        text = self._get_strategy_text(journal)
        assert "子类上限" in text
        assert "14" in text  # 14 个子类

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
        """策略表所有列 text + width=auto（跟 Section 1 持仓表一致）。"""
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


class TestOrdering:
    """元素顺序（spec 097 第十四轮 — 2 个 section）。

    Section 1（实盘持仓）：note + div + chart + table
    Section 2（具体策略）：note + div + table
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
            "table", # Section 1 持仓表
            "note",  # Section 2 header "具体策略"
            "div",   # Section 2 策略文字
            "table", # Section 2 策略对比表
        ]

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
        """Section 2 开头：hr 分隔 + note "具体策略"。

        跟 Section 1 用 hr 隔开，视觉上明确区分。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        elements = card["elements"]
        # 找到 "具体策略" note 的索引
        section_2_idx = None
        for idx, e in enumerate(elements):
            if e.get("tag") == "note" and any(
                elem.get("content") == "具体策略"
                for elem in e.get("elements", [])
            ):
                section_2_idx = idx
                break
        assert section_2_idx is not None
        # 它前面必须是 hr（跨 section 分隔）
        assert elements[section_2_idx - 1].get("tag") == "hr"
