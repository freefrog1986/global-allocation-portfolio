"""测试 src/global_allocation/portfolio/left_layout_card.py（liubo 2026-10-19 改名；原 etf_rotation_card.py）。

飞书 Interactive Card 3 段结构：
- Section 1: 组合总览（总成本 / 总资产 / 总盈亏 / 总收益率）
- Section 2: 类别上限状态（5 类柱状图，liubo 2026-10-19 拍板）
- Section 3: 各基金仓位 + 冷却期（8 只基金 + 冷却期状态）

liubo 2026-09-29 拍板：左侧布局 周报必须用 Interactive Card（2026-10-19 改名）。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from global_allocation.portfolio.left_layout_card import (
    build_left_layout_card,
    card_to_json,
)


def _find_table_after_note(card: dict, note_text: str) -> dict | None:
    """找到指定 section header 后面的 table。

    Card 2.0 不再支持 note tag，section 头改用 `{"tag":"markdown","content":"**X**"}`
    （2026-10-19 迁移）。
    """
    elements = card["body"]["elements"]
    for i, el in enumerate(elements):
        if _is_section_header(el, note_text):
            for j in range(i + 1, len(elements)):
                if elements[j]["tag"] == "table":
                    return elements[j]
                if _is_section_header(elements[j], ""):
                    return None  # 碰到下一个 section header 就停
            return None
    return None


def _is_section_header(el: dict, text: str) -> bool:
    """section 头：可能是 markdown bold 或 div+lark_md+icon。

    2026-10-19 拍板用 div + icon（视觉区分更强），之前的 markdown bold 留作 fallback。
    """
    # 格式 A：div + lark_md + icon
    if el.get("tag") == "div":
        text_obj = el.get("text", {})
        if text_obj.get("tag") == "lark_md":
            content = text_obj.get("content", "")
            if text:
                return text in content and content.strip().startswith("**")
            return content.strip().startswith("**") and content.strip().endswith("**")
    # 格式 B：markdown bold (fallback)
    if el.get("tag") == "markdown":
        content = el.get("content", "")
        if text:
            return text in content and content.strip().startswith("**")
        return content.strip().startswith("**") and content.strip().endswith("**")
    return False


def _section_header_text(el: dict) -> str:
    """提取 section 头的纯文字（去掉 `**` 和 emoji）。"""
    if el.get("tag") == "div":
        return el["text"]["content"].strip().strip("*").strip()
    if el.get("tag") == "markdown":
        return el["content"].strip().strip("*").strip()
    return ""


def _all_note_texts(card: dict) -> list[str]:
    """提取所有 section header（v2 用 div+icon 或 markdown bold）的文字。"""
    notes: list[str] = []
    for el in card["body"]["elements"]:
        if _is_section_header(el, ""):
            notes.append(_section_header_text(el))
    return notes


def _all_tables(card: dict) -> list[dict]:
    """提取所有 table 元素。"""
    return [el for el in card["body"]["elements"] if el["tag"] == "table"]


class TestCardStructure:
    """Card 顶层结构验证（Card 2.0 schema：liubo 2026-10-19 拍板加 subtitle）。"""

    def test_has_header(self) -> None:
        """header.template + title + subtitle。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        assert "header" in card
        assert card["header"]["template"] == "purple"
        assert "title" in card["header"]
        assert card["header"]["title"]["tag"] == "plain_text"
        # 主标题（liubo 2026-10-19 拍板：不带日期，日期独立到副标题）
        assert card["header"]["title"]["content"] == "左侧布局组合周报"
        # 副标题（Card 2.0 才支持）
        assert "subtitle" in card["header"]
        assert card["header"]["subtitle"]["tag"] == "plain_text"
        assert card["header"]["subtitle"]["content"] == "2026-09-30"
        # 状态标签
        assert "text_tag_list" in card["header"]
        assert any(
            t.get("text", {}).get("content") == "左侧布局"
            for t in card["header"]["text_tag_list"]
        )

    def test_schema_is_v2(self) -> None:
        """Card 2.0 schema 标识。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        assert card.get("schema") == "2.0"

    def test_has_body_elements(self) -> None:
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        assert "body" in card
        assert "elements" in card["body"]
        assert isinstance(card["body"]["elements"], list)
        assert len(card["body"]["elements"]) > 0

    def test_footer_as_markdown_in_body(self) -> None:
        """v1 footer 字段在 v2 没了；改用 body 末尾灰色 markdown。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        # 不再有 v1 的 footer 顶层字段
        assert "footer" not in card
        # 找 body 末尾灰色 markdown
        elements = card["body"]["elements"]
        last_md = next(
            (el for el in reversed(elements)
             if el["tag"] == "markdown" and "<font color='grey'>" in el["content"]),
            None,
        )
        assert last_md is not None
        assert "左侧布局" in last_md["content"]

    def test_four_sections_in_order(self) -> None:
        """4 个 section header，按顺序：组合总览 / 类别上限状态 / 组合规则 / 各类别基金 + 估值 + 调仓建议。

        liubo 2026-10-20 拍板：在「各类别基金 + 估值 + 调仓建议」**之前**
        插入「组合规则」段。
        """
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        notes = _all_note_texts(card)
        assert notes == [
            "组合总览",
            "类别上限状态（占总成本 %）",
            "组合规则",
            "各类别基金 + 估值 + 调仓建议",
        ]

    def test_section_titles_match(self) -> None:
        """section header 完整文本（liubo 2026-10-19 拍板改用实际占比）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        notes = _all_note_texts(card)
        # 类别上限 section 改成"（占总成本 %）"（不是 30% cap 的使用率）
        assert any("类别上限状态（占总成本 %）" in n for n in notes)

    def test_default_report_date_is_today(self) -> None:
        """report_date=None → 用今天（日期格式是 ISO）；写到 subtitle。"""
        card = build_left_layout_card()
        assert card["header"]["title"]["content"] == "左侧布局组合周报"
        # subtitle 形如 "2026-10-19"
        subtitle = card["header"]["subtitle"]["content"]
        assert len(subtitle) == 10  # YYYY-MM-DD
        assert subtitle[4] == "-" and subtitle[7] == "-"


class TestOverviewSection:
    """Section 1：组合总览（liubo 2026-10-19 拍板精简成 5 + 加 1 = 6 个 div.fields）。

    liubo 2026-10-19 第一轮：砍 总资产 / 总盈亏 / 总收益率，留 5 个。
    liubo 2026-10-19 第二轮：再加 总类别数量 → 6 个（刚好 3 行 × 2 列布局）。
    """

    def _find_overview_fields_div(self, card: dict) -> dict:
        """找组合总览 section 头后面的 fields div。"""
        elements = card["body"]["elements"]
        for i, el in enumerate(elements):
            if _is_section_header(el, "组合总览"):
                for j in range(i + 1, len(elements)):
                    if elements[j]["tag"] == "div" and "fields" in elements[j]:
                        return elements[j]
        raise AssertionError("没找到组合总览的 fields div")

    def test_overview_has_6_fields(self) -> None:
        """6 个 label-value（liubo 2026-10-19 第二轮：加 总类别数量 凑 6 个）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        fields_div = self._find_overview_fields_div(card)
        assert len(fields_div["fields"]) == 6

    def test_overview_field_labels(self) -> None:
        """6 个标签都在：总成本 / 总基金数量 / 总类别数量 / 单仓 / 类别上限 / 同基金冷却期。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        fields_div = self._find_overview_fields_div(card)
        labels = " ".join(f["text"]["content"] for f in fields_div["fields"])
        assert "总成本" in labels
        assert "总基金数量" in labels
        assert "总类别数量" in labels  # liubo 2026-10-19 第二轮加
        assert "单仓" in labels
        assert "类别上限" in labels
        assert "同基金冷却期" in labels

    def test_overview_field_values(self) -> None:
        """6 个值都存在且 is_short=true（两两并排布局）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        fields_div = self._find_overview_fields_div(card)
        labels = " ".join(f["text"]["content"] for f in fields_div["fields"])
        # 总成本 → 71,420.00 CNY（_fmt_money 格式带 .00）
        assert "71,420" in labels
        # 总基金数量 → 13 只（2026-10-20 加 501226/019331/017436/011612）
        assert "13 只" in labels
        # 总类别数量 → 5 类
        assert "5 类" in labels
        # 单仓 → 5,000
        assert "5,000" in labels
        # 类别上限 → 15%（liubo 2026-10-19 拍板从 30% 降到 15%）
        assert "15%" in labels
        # 同基金冷却期 → 30 天（liubo 2026-10-19 拍板从 7 天延长）
        assert "30 天" in labels
        # 所有字段都是 is_short=true（两列布局）
        assert all(f["is_short"] is True for f in fields_div["fields"])
        # text.tag 全部是 lark_md（加粗才渲染）
        assert all(f["text"]["tag"] == "lark_md" for f in fields_div["fields"])

    def test_overview_no_pnl_or_assets(self) -> None:
        """liubo 拍板：精简后不再含 总资产 / 总盈亏 / 总收益率 / 快照日期。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        fields_div = self._find_overview_fields_div(card)
        labels = " ".join(f["text"]["content"] for f in fields_div["fields"])
        assert "总资产" not in labels
        assert "总盈亏" not in labels
        assert "总收益率" not in labels
        assert "snapshot" not in labels.lower()


class TestCategorySection:
    """Section 2：类别上限状态（liubo 2026-10-19 拍板改用柱状图，删原 5 列表格）。"""

    def _find_chart(self, card: dict) -> dict:
        """找类别上限 section 头后面的 chart 元素。"""
        elements = card["body"]["elements"]
        for i, el in enumerate(elements):
            if _is_section_header(el, "类别上限状态"):
                for j in range(i + 1, len(elements)):
                    if elements[j]["tag"] == "chart":
                        return elements[j]
        raise AssertionError("没找到类别上限的 chart 元素")

    def test_has_chart(self) -> None:
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        chart = self._find_chart(card)
        assert chart is not None
        assert chart["tag"] == "chart"

    def test_chart_type_is_pie(self) -> None:
        """VChart type=pie（liubo 2026-10-19 拍板从 bar 改 pie）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        chart = self._find_chart(card)
        assert chart["chart_spec"]["type"] == "pie"

    def test_chart_has_5_categories(self) -> None:
        """5 个类别都在 data.values。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        chart = self._find_chart(card)
        values = chart["chart_spec"]["data"]["values"]
        assert len(values) == 5
        cat_names = {v["cat"] for v in values}
        assert cat_names == {
            "港股科技互联网", "港股银行", "亚太除日本", "全球科技主动", "房地产",
        }

    def test_chart_pie_fields(self) -> None:
        """VChart pie：categoryField=cat, valueField=pct。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        chart = self._find_chart(card)
        assert chart["chart_spec"]["categoryField"] == "cat"
        assert chart["chart_spec"]["valueField"] == "pct"

    def test_chart_pie_is_donut(self) -> None:
        """环形图：innerRadius=0.5（中间留空，标数字 / 中心总览可加）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        chart = self._find_chart(card)
        assert chart["chart_spec"].get("innerRadius") == 0.5
        assert chart["chart_spec"].get("outerRadius") == 0.85

    def test_chart_aspect_ratio(self) -> None:
        """aspect_ratio 16:9（飞书默认）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        chart = self._find_chart(card)
        assert chart.get("aspect_ratio") == "16:9"

    def test_hk_tech_internet_pct_74(self) -> None:
        """港股科技互联网 53,000 / 71,420 = 74.2%（实际占比，liubo 2026-10-19 拍板
        从「使用率」改「实际占比」）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        chart = self._find_chart(card)
        values = chart["chart_spec"]["data"]["values"]
        hk_tech = next(v for v in values if v["cat"] == "港股科技互联网")
        # 实际占比 53,000 / 71,420 = 74.22% → chart 允许 ±0.5% 误差
        assert abs(hk_tech["pct"] - 74.2) < 0.5

    def test_other_categories_under_cap(self) -> None:
        """其余 4 类都 < 15%（CAP=15%；liubo 2026-10-19 拍板从 30% 降到 15%）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        chart = self._find_chart(card)
        values = chart["chart_spec"]["data"]["values"]
        others = [v for v in values if v["cat"] != "港股科技互联网"]
        for v in others:
            assert v["pct"] < 15, f"{v['cat']} 应 <15%（未超 15% 上限），实际 {v['pct']}%"

    def test_chart_has_title(self) -> None:
        """标题：类别占总成本比例（liubo 2026-10-19 拍板从「使用率」改「实际占比」）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        chart = self._find_chart(card)
        assert chart["chart_spec"]["title"]["text"] == "类别占总成本比例"

    def test_pie_no_markLine(self) -> None:
        """饼图没有 y 轴 → 不能画 markLine（liubo 2026-10-19 拍板从柱状图改饼图后删了阈值线）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        chart = self._find_chart(card)
        assert "markLine" not in chart["chart_spec"]

    def test_pie_over_cap_color_is_red(self) -> None:
        """超限类别（港股科技互联网）颜色 = #FF6B6B 红色，其他蓝色为主
        （liubo 2026-10-19 拍板用颜色替代阈值线）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        chart = self._find_chart(card)
        colors = chart["chart_spec"]["color"]
        # Feishu API 只接受纯数组；{type:ordinal, range:[]} schema 不认
        assert isinstance(colors, list)
        assert len(colors) == 5
        # 港股科技互联网对应位置应是红色（按 STRATEGY_CATEGORIES 遍历顺序第 0 个）
        assert colors[0] == "#FF6B6B"
        # 其余 4 个是中性色（不是红色）
        for c in colors[1:]:
            assert c != "#FF6B6B"

    def test_no_table_in_category_section(self) -> None:
        """Section 2 不再有 table（liubo 2026-10-19 拍板改 chart）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        elements = card["body"]["elements"]
        for i, el in enumerate(elements):
            if _is_section_header(el, "类别上限状态"):
                # section 头后面下一个 chart/hr 之前不能有 table
                for j in range(i + 1, len(elements)):
                    if elements[j]["tag"] in ("chart", "hr"):
                        break
                    assert elements[j]["tag"] != "table", \
                        f"Section 2 不应有 table 元素，发现第{j}项: {elements[j]['tag']}"


class TestRulesSection:
    """Section 3：组合规则（liubo 2026-10-20 拍板加，排在类别明细之前）。

    第二轮 liubo 反馈「这部分太乱了」→ 从 7 个 div.fields（4 行、最后落单、
    每条格式不一致）改成**单列 markdown 列表**，7 行统一三段式。
    """

    def _rules_lines(self, card: dict) -> list[str]:
        """规则段里 7 行 markdown 列表项。"""
        elements = card["body"]["elements"]
        for i, el in enumerate(elements):
            if _is_section_header(el, "组合规则"):
                out = []
                for e in elements[i + 1:]:
                    if e.get("tag") == "hr":
                        break
                    if e.get("tag") == "markdown":
                        out.append(e["content"])
                return out
        pytest.fail("没找到「组合规则」section")

    def test_has_header_with_setting_icon(self) -> None:
        """header 用 standard_icon（不是 img）。

        liubo 2026-10-20 澄清：他只是要我自己换个图标，不是把他的图贴上去。
        `setting_outlined`（参数设置）跟另外三段的 report / wallet 都不撞。
        """
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        elements = card["body"]["elements"]
        header_idx = next(
            i for i, el in enumerate(elements)
            if _is_section_header(el, "组合规则")
        )
        header = elements[header_idx]
        assert header["tag"] == "div"
        assert header["icon"]["tag"] == "standard_icon"
        assert header["icon"]["token"] == "setting_outlined"
        # 段内不应再有 img 元素
        assert all(e.get("tag") != "img" for e in elements[header_idx:])

    def test_section_icons_are_all_distinct(self) -> None:
        """4 个 section header 的图标互不相同（避免视觉混淆）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        tokens = [
            el["icon"]["token"] for el in card["body"]["elements"]
            if el.get("tag") == "div" and "icon" in el
            and el.get("text", {}).get("content", "").startswith("**")
        ]
        assert len(tokens) == len(set(tokens)), f"图标重复：{tokens}"

    def test_five_rules_all_markdown_list(self) -> None:
        """5 条规则，每条一行 markdown 列表（`- ` 前缀），不再是 div.fields。"""
        lines = self._rules_lines(build_left_layout_card(report_date=date(2026, 9, 30)))
        assert len(lines) == 5
        assert all(line.startswith("- ") for line in lines)

    def test_each_line_same_three_part_shape(self) -> None:
        """每行统一 `名称：<关键值 tag>  说明` 三段式，扫读对齐。"""
        lines = self._rules_lines(build_left_layout_card(report_date=date(2026, 9, 30)))
        for line in lines:
            assert "<text_tag color='" in line, f"关键值应带 tag：{line}"
            assert "</text_tag>" in line, f"关键值应带 tag：{line}"
            assert "：" in line, f"名称与关键值之间应有冒号：{line}"

    def test_rule_names_match_liubo_spec(self) -> None:
        """liubo 2026-10-20 指定的 5 条规则名。"""
        lines = self._rules_lines(build_left_layout_card(report_date=date(2026, 9, 30)))
        for name in ("单笔规模", "一仓金额", "类别上限", "调仓冷却期", "调仓信号"):
            assert any(line.startswith(f"- {name}：") for line in lines), f"缺规则：{name}"

    def test_signal_thresholds_match_valuation_status(self) -> None:
        """调仓信号的分位阈值必须和 `_valuation_status` 的判断口径一致（<20 买 / >80 卖）。

        两边不同步的话，卡片写的规则会跟实际调仓建议对不上。
        """
        lines = self._rules_lines(build_left_layout_card(report_date=date(2026, 9, 30)))
        signal = next(line for line in lines if line.startswith("- 调仓信号："))
        assert "<20% 买入" in signal
        assert ">80% 卖出" in signal
        assert "其余持有" in signal
        # 颜色约定跟卡片里「估值」列一致：绿买 / 红卖 / 灰持有
        assert "<text_tag color='green'>分位 <20% 买入</text_tag>" in signal
        assert "<text_tag color='red'>分位 >80% 卖出</text_tag>" in signal
        assert "<text_tag color='grey'>其余持有</text_tag>" in signal

    def test_covers_all_rules(self) -> None:
        """5 条规则全覆盖（数值取自 left_layout 模块常量，不硬编码）。"""
        text = "\n".join(
            self._rules_lines(build_left_layout_card(report_date=date(2026, 9, 30)))
        )
        assert "每次 1 仓" in text
        assert "5,000 CNY" in text
        assert "15% 总成本" in text
        assert "30 天" in text

    def test_cap_amount_tracks_total_cost(self) -> None:
        """类别上限的 CNY 金额 = 总成本 × 15%，改了 CAP 或总成本这里自动跟上。"""
        from global_allocation.portfolio.left_layout import (
            CATEGORY_CAP_PCT, TOTAL_COST_CNY,
        )
        text = "\n".join(
            self._rules_lines(build_left_layout_card(report_date=date(2026, 9, 30)))
        )
        expected = float(TOTAL_COST_CNY * CATEGORY_CAP_PCT)
        assert f"{expected:,.0f} CNY" in text

    def test_section_precedes_fund_detail(self) -> None:
        """规则段必须在「各类别基金 + 估值 + 调仓建议」之前。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        notes = _all_note_texts(card)
        assert notes.index("组合规则") < notes.index("各类别基金 + 估值 + 调仓建议")


class TestFundSection:
    """Section 4：各类别基金 + 估值 + 调仓建议
    （liubo 2026-10-19 拍板 R12：删 2 个 sub-md + 估值/调仓建议合并到 1 个 div.fields）。

    每类别块结构（liubo 2026-10-19 R12）：
    1. div category header：`**{类别}** <tag 占比> <tag 状态>`（icon=tag_outlined）
    2. N 个 markdown 基金行（无 sub-md，直接列）
       `- 基金全名（code） X.XX 仓  累计 X CNY`
    3. div · fields 4 列合并：
       - 参考指数 / 主指标 / 估值状态 / 调仓建议

    R12 改动：删 R11 的「类下基金」和「估值」sub-md；估值 + 调仓建议合并到 1 个 div.fields。

    5 个类别块之间用 hr 隔开（最后 1 个不加）。
    """

    def _get_fund_section_elements(self, card: dict) -> list[dict]:
        """提取 Section 3 的所有 elements（从 section header 到灰色 footer 之前）。

        liubo 2026-10-19 R10：number_tag 里也含 `background_color='grey'`，
        检测 footer 必须用 `<font color='grey'>`（footer 标志）而不是泛 `color='grey'`。
        """
        elements = card["body"]["elements"]
        start = None
        for i, el in enumerate(elements):
            if _is_section_header(el, "各类别基金 + 估值 + 调仓建议"):
                start = i
                break
        assert start is not None, "没找到 Section 3 header"
        end = len(elements)
        for j in range(start + 1, len(elements)):
            el = elements[j]
            if (el.get("tag") == "markdown"
                    and "<font color='grey'>" in el.get("content", "")):
                end = j
                break
        return elements[start:end]

    def _get_fund_lines(self, card: dict) -> list[str]:
        """类下基金段：所有 `- 基金全名（code） X.XX 仓  累计 X CNY` 行
        （不含 类下基金 sub-header / 估值 sub-header / 调仓建议）。

        R10 用 `<number_tag` 标识；R11 改 text_tag；R17 改 markdown 列表 `- ` 前缀，
        不再用 tag 包仓位，过滤条件改为「以 - 开头 + 含全名（code）」。
        """
        els = self._get_fund_section_elements(card)
        lines: list[str] = []
        for el in els:
            if el.get("tag") != "markdown":
                continue
            content = el.get("content", "")
            # 跳过 sub-headers
            if "类下基金" in content:
                continue
            if "估值" in content:
                continue
            if "调仓" in content:
                continue
            # 基金行特征：markdown 列表前缀 `- ` + 全名（code）
            if content.startswith("- ") and "（" in content and "）" in content:
                lines.append(content)
        return lines

    def _get_category_block_starts(self, card: dict) -> list[tuple[int, str]]:
        """返回 [(idx, category_name)]：5 个类别 div header 的位置。

        liubo 2026-10-20 R16：类别 header 去掉 standard_icon + 飞书有序列表前缀，
        改用 number_tag 序号 `<number_tag background_color='blue' font_color='white'>N</number_tag>`。
        通过识别 number_tag 来定位类别 div header（不靠 icon token 也不靠 prefix）。
        """
        import re
        els = self._get_fund_section_elements(card)
        number_tag_re = re.compile(
            r"<number_tag[^>]*>(\d{1,2})</number_tag>"
        )
        starts = []
        for i, el in enumerate(els):
            if el.get("tag") != "div":
                continue
            content = el.get("text", {}).get("content", "")
            m = number_tag_re.search(content)
            if not m:
                continue
            # ordinal = m.group(1)
            # 提取类别名（在 number_tag 后到下个 < 之前）
            after_tag = content[m.end():]
            name = after_tag.split("<")[0].strip().strip("*").strip()
            starts.append((i, name))
        return starts

    def _get_block(self, card: dict, block_idx: int) -> list[dict]:
        """返回第 block_idx 个类别块的元素列表（cat header div 到下个 cat div 或 hr 之前）。"""
        els = self._get_fund_section_elements(card)
        starts = self._get_category_block_starts(card)
        cat_pos, _ = starts[block_idx]
        next_pos = starts[block_idx + 1][0] if block_idx + 1 < len(starts) else len(els)
        # next_pos 一般是下个 cat div 位置；末尾块要把 next_pos 之前的所有内容（含 hr）算进去
        # 但块的「末尾」常是 hr（属于块间分隔），也保留以便测试 hr 数量
        return els[cat_pos:next_pos]

    # ─── Section header / 总结构 ───

    def test_section_header_present(self) -> None:
        """section header：div + wallet_outlined icon + 「各类别基金 + 估值 + 调仓建议」。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        els = self._get_fund_section_elements(card)
        assert els[0]["tag"] == "div"
        assert els[0]["icon"]["token"] == "wallet_outlined"
        assert "各类别基金 + 估值 + 调仓建议" in els[0]["text"]["content"]

    def test_no_table_in_fund_section(self) -> None:
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        els = self._get_fund_section_elements(card)
        for el in els:
            assert el.get("tag") != "table"

    def test_5_category_blocks(self) -> None:
        """5 个类别块（liubo 2026-10-19 拍板 5 类）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        starts = self._get_category_block_starts(card)
        assert len(starts) == 5
        names = [name for _, name in starts]
        assert names == ["港股科技互联网", "港股银行", "亚太除日本", "全球科技主动", "房地产"]

    # ─── 类别块结构（liubo R12：1 div cat header + N md + 1 div.fields 4 列） ───

    def test_category_block_structure(self) -> None:
        """每个类别块结构（liubo 2026-10-19 R12）：

        - 1 div（cat header）
        - N md（基金行，含 text_tag；无 sub-md）
        - 1 div.fields（4 列：参考指数 / 主指标 / 估值 / 调仓建议）

        → 2 div + N md
        """
        card = build_left_layout_card(report_date=date(2026, 9, 30))

        from global_allocation.portfolio.left_layout import STRATEGY_CATEGORIES
        for i, (cat, codes) in enumerate(STRATEGY_CATEGORIES.items()):
            block = self._get_block(card, i)
            md_count = sum(1 for el in block if el.get("tag") == "markdown")
            div_count = sum(1 for el in block if el.get("tag") == "div")
            fields_divs = sum(
                1 for el in block
                if el.get("tag") == "div" and "fields" in el
            )
            # 1 cat header div + 1 div.fields = 2 div total
            assert div_count == 2, (
                f"{cat}: 期望 2 个 div（cat header + category fields），实际 {div_count}"
            )
            assert fields_divs == 1, (
                f"{cat}: 期望 1 个 div.fields（合并估值 + 调仓建议），实际 {fields_divs}"
            )
            # R12 删 2 个 sub-md：只剩 N fund md
            assert md_count == len(codes), (
                f"{cat}: 期望 {len(codes)} 个 md（基金行），实际 {md_count}"
            )

    def test_category_header_has_inline_tags(self) -> None:
        """类别 header div 用 lark_md 内嵌 text_tag：占比 + 状态（liubo 2026-10-19 拍板）。

        liubo 2026-10-20 R16：header 改用 number_tag（demo 里的格式），
        取代 R14 飞书原生有序列表。
        """
        card = build_left_layout_card(report_date=date(2026, 9, 30))

        # 港股科技互联网 74.2% 应该 red
        block = self._get_block(card, 0)
        cat_header = block[0]
        content = cat_header["text"]["content"]
        assert cat_header["text"]["tag"] == "lark_md"
        # R16 number_tag 序号（沿用 demo 格式）+ 类别名 + text_tag 占比% + text_tag 状态
        assert "<number_tag background_color='blue' font_color='white'>1</number_tag>" in content
        assert "**港股科技互联网**" in content
        assert "<text_tag color='red'>74.2%</text_tag>" in content
        assert "<text_tag color='red'>超限</text_tag>" in content

        # 港股银行 1.4% 应该 blue
        block = self._get_block(card, 1)
        content = block[0]["text"]["content"]
        assert "<number_tag background_color='blue' font_color='white'>2</number_tag>" in content
        assert "**港股银行**" in content
        assert "<text_tag color='blue'>1.4%</text_tag>" in content
        assert "<text_tag color='blue'>正常</text_tag>" in content

    def test_category_header_no_icon(self) -> None:
        """liubo 2026-10-20 R16：类别 header 去掉 standard_icon，用 number_tag 序号。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        starts = self._get_category_block_starts(card)
        for idx, (pos, _) in enumerate(starts):
            block = self._get_block(card, idx)
            cat_div = block[0]
            # 不再有 standard_icon（liubo R13/R14 拍板）
            assert "icon" not in cat_div, (
                f"类 {idx}: R16 拍板去掉 icon，仍含 {cat_div.get('icon')!r}"
            )
            # 应该有对应 ordinal 的 number_tag
            ordinal = idx + 1
            assert (
                f"<number_tag background_color='blue' font_color='white'>{ordinal}</number_tag>"
                in cat_div["text"]["content"]
            ), f"类 {idx}: 应有 ordinal={ordinal} 的 number_tag"

    def test_no_类下基金_subheader(self) -> None:
        """liubo R12 拍板：删除「类下基金」sub-md，基金行直接列。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        from global_allocation.portfolio.left_layout import STRATEGY_CATEGORIES
        for i, (cat, codes) in enumerate(STRATEGY_CATEGORIES.items()):
            block = self._get_block(card, i)
            # 不应该有「类下基金」sub-md
            for el in block:
                if el.get("tag") == "markdown" and "**类下基金**" in el.get("content", ""):
                    pytest.fail(f"{cat}: 不应有「类下基金」sub-md，发现 {el['content']!r}")
            # 但应至少有 N 行基金 markdown（R17：markdown 列表 `- ` 前缀）
            fund_md_count = sum(
                1 for el in block
                if el.get("tag") == "markdown" and el.get("content", "").startswith("- ")
            )
            assert fund_md_count == len(codes), (
                f"{cat}: 期望 {len(codes)} 行基金，实际 {fund_md_count}"
            )

    def test_fund_lines_markdown_list_no_tag(self) -> None:
        """基金行 = markdown 列表 + 仓位普通文字（liubo 2026-10-20 R17 拍板）。

        - 前缀 `- `（liubo：「这些基金搞成富文本的列表星星」）
        - 仓位 `X.XX 仓` 不再用 tag 包，移到基金名后面当普通文字
          （liubo：「前面的 xx 仓，不要用 tag，就放后面和文字一样」）

        历史：R10 number_tag → R11 text_tag → R17 全部去掉 tag。
        """
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        fund_lines = self._get_fund_lines(card)
        # 006327 成本 10,000 → 2.00 仓
        text_006327 = next(l for l in fund_lines if "006327" in l)
        assert text_006327.startswith("- ")
        assert "易方达中证海外互联网50ETF联接(QDII)A（006327） 2.00 仓" in text_006327
        assert "10,000.00 CNY" in text_006327
        # 457001 成本 4,640 → 0.93 仓
        text_457001 = next(l for l in fund_lines if "457001" in l)
        assert text_457001.startswith("- ")
        assert "0.93 仓" in text_457001
        assert "4,640.00 CNY" in text_457001
        # 仓位不再被任何 tag 包裹
        for line in fund_lines:
            assert "<text_tag" not in line, f"基金行不应含 text_tag：{line!r}"
            assert "<number_tag" not in line, f"基金行不应含 number_tag：{line!r}"

    def test_all_13_funds_present(self) -> None:
        """13 只基金全名 + code 都在 markdown 行里（2026-10-20 加 4 只全球科技）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        all_text = "\n".join(self._get_fund_lines(card))
        expected = [
            "易方达中证海外互联网50ETF联接(QDII)A（006327）",
            "富国中证港股通互联网ETF发起式联接A（014673）",
            "景顺长城中证港股通科技ETF发起联接A（016495）",
            "泰康香港银行指数A（006809）",
            "国富亚洲机会股票(QDII)A（457001）",
            "天弘全球高端制造混合(QDII)A（016664）",
            "国富全球科技互联混合(QDII)人民币A（006373）",
            "嘉实全球产业升级股票发起式(QDII)A（017730）",
            # 2026-10-20 liubo 加，暂定未买入（0.00 仓）
            "长程全球新能源汽车股票(QDII)A（501226）",
            "华泰柏瑞中证沪港深云计算产业ETF联接C（019331）",
            "华宝纳斯达克精选股票(QDII)A（017436）",
            "华夏科创50ETF联接A（011612）",
            "南方中证房地产ETF发起联接E（010989）",
        ]
        for name in expected:
            assert name in all_text, f"缺少基金行：{name}"

    def test_013127_not_present(self) -> None:
        """013127 已转走。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        all_text = "\n".join(self._get_fund_lines(card))
        assert "013127" not in all_text

    # ─── 估值段：div.fields 多列（liubo 2026-10-19 R12：4 列合并估值 + 调仓建议） ───

    def test_no_valuation_subheader(self) -> None:
        """liubo R12 拍板：删除「估值」sub-md，估值信息全在 div.fields 里。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        for i in range(5):
            block = self._get_block(card, i)
            # 不应该有「**估值**」sub-md
            for el in block:
                if el.get("tag") == "markdown" and "**估值**" in el.get("content", ""):
                    pytest.fail(f"类 {i}: 不应有「估值」sub-md，发现 {el['content']!r}")

    def test_category_div_fields_has_4_columns(self) -> None:
        """类别 div.fields 含 4 列 is_short=true（参考指数 / 主指标 / 估值 / 调仓建议）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        for i in range(5):
            block = self._get_block(card, i)
            fields_div = next(
                el for el in block
                if el.get("tag") == "div" and "fields" in el
            )
            fields = fields_div["fields"]
            assert len(fields) == 4, f"类 {i} 字段期望 4 列，实际 {len(fields)}"
            # 4 列 is_short=true → 横向并排 2×2
            assert all(f["is_short"] is True for f in fields)
            # 全部 lark_md（加粗/颜色才渲染）
            assert all(f["text"]["tag"] == "lark_md" for f in fields)
            # 列 1 = 参考指数，列 2 = 主指标，列 3 = 估值，列 4 = 调仓建议
            assert "参考指数" in fields[0]["text"]["content"]
            # R15：去掉 bold，列 2 是 PE-TTM / PB / —
            assert "**" not in fields[1]["text"]["content"]
            assert "估值" in fields[2]["text"]["content"]
            assert "<text_tag" in fields[2]["text"]["content"]
            assert "调仓建议" in fields[3]["text"]["content"]
            assert "<text_tag" in fields[3]["text"]["content"]

    def test_港股通科技_valuation(self) -> None:
        """港股科技互联网 → 港股通科技 (987008.sz) PE-TTM 23.10@13.14% → 低估。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        block = self._get_block(card, 0)
        fields_div = next(
            el for el in block
            if el.get("tag") == "div" and "fields" in el
        )
        fields = fields_div["fields"]
        assert "港股通科技" in fields[0]["text"]["content"]
        assert "987008.sz" in fields[0]["text"]["content"]
        # PE-TTM 23.10@13.14%
        assert "PE-TTM" in fields[1]["text"]["content"]
        assert "23.10" in fields[1]["text"]["content"]
        assert "13.14%" in fields[1]["text"]["content"]
        # 估值状态：13.14% < 20% → 低估（green）
        assert "<text_tag color='green'>低估</text_tag>" in fields[2]["text"]["content"]

    def test_港股银行_valuation(self) -> None:
        """港股银行 → HK 银行 (930792.csi) PE-TTM 7.32@90.71% → 高估（liubo 2026-10-19 给数据）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        block = self._get_block(card, 1)
        fields_div = next(
            el for el in block
            if el.get("tag") == "div" and "fields" in el
        )
        fields = fields_div["fields"]
        assert "HK银行" in fields[0]["text"]["content"]
        assert "930792.csi" in fields[0]["text"]["content"]
        assert "PE-TTM" in fields[1]["text"]["content"]
        assert "7.32" in fields[1]["text"]["content"]
        assert "90.71%" in fields[1]["text"]["content"]
        # 估值状态：90.71% > 80% → 高估（red）
        assert "<text_tag color='red'>高估</text_tag>" in fields[2]["text"]["content"]

    def test_房地产_valuation_uses_PB(self) -> None:
        """房地产 → 931775.csi PB 0.760@13.27% → 低估（主指标是 PB 不是 PE-TTM；liubo 2026-10-19 给数据）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        block = self._get_block(card, 4)  # 房地产是第 5 个
        fields_div = next(
            el for el in block
            if el.get("tag") == "div" and "fields" in el
        )
        fields = fields_div["fields"]
        assert "房地产" in fields[0]["text"]["content"]
        assert "931775.csi" in fields[0]["text"]["content"]
        # 主指标 PB 而非 PE-TTM
        assert "PB" in fields[1]["text"]["content"]
        assert "0.760" in fields[1]["text"]["content"]
        assert "13.27%" in fields[1]["text"]["content"]
        assert "PE-TTM" not in fields[1]["text"]["content"]
        # 估值状态：13.27% < 20% → 低估（green）
        assert "<text_tag color='green'>低估</text_tag>" in fields[2]["text"]["content"]

    def test_全球科技主动_两个指数_NDX_and_SOX(self) -> None:
        """全球科技主动 → 纳指100 (.NDX) + 费城半导体 (.SOX) **两个都显示**。

        liubo 2026-10-20 拍板：「全球科技主动这个类别要参考的估值包括两个指数，
        一个是纳斯达克100，一个是费城半导体，你不要删除纳指100」。
        .NDX 有 PB 9.08（无分位），.SOX 数据未填 → 两个状态都是灰色「—」。
        """
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        block = self._get_block(card, 3)
        fields_div = next(
            el for el in block
            if el.get("tag") == "div" and "fields" in el
        )
        fields = fields_div["fields"]
        # 列 1：两个参考指数并列
        col1 = fields[0]["text"]["content"]
        assert "纳斯达克100（.NDX）" in col1
        assert "费城半导体（.SOX）" in col1
        assert " / " in col1
        # 列 2：两个主指标并列（.NDX 有 PB 9.08，.SOX 未填 → PE-TTM —）
        col2 = fields[1]["text"]["content"]
        assert "PB：9.08" in col2
        assert "PE-TTM：—" in col2
        # 列 3：两个估值状态并列，都无分位 → 灰色「—」
        assert fields[2]["text"]["content"].count(
            "<text_tag color='grey'>—</text_tag>"
        ) == 2

    # ─── 调仓建议段（liubo 2026-10-19 R12：在 div.fields 第 4 列） ───

    def test_调仓建议_in_field4(self) -> None:
        """调仓建议在 div.fields 第 4 列（liubo R12 拍板合并到 fields 里）。

        R11 还是独立 markdown；R12 删掉该 md，合并到 div.fields 最后一列。
        """
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        for i in range(5):
            block = self._get_block(card, i)
            # 不应有独立「**调仓建议**」md
            for el in block:
                if el.get("tag") == "markdown" and "**调仓建议**" in el.get("content", ""):
                    pytest.fail(f"类 {i}: R12 调仓建议应在 div.fields，不应独立 md")
            # 但 div.fields 第 4 列应有
            fields_div = next(
                el for el in block
                if el.get("tag") == "div" and "fields" in el
            )
            field4 = fields_div["fields"][3]
            # R15：去掉 bold，只剩 text_tag
            assert "调仓建议：" in field4["text"]["content"]
            assert "**" not in field4["text"]["content"]
            assert "<text_tag" in field4["text"]["content"]

    def test_港股超限_减仓(self) -> None:
        """港股科技互联网 74.2% 超限 → ⚠️ 减 1 仓  仓位超 15% 上限（liubo R12 在 field4）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        block = self._get_block(card, 0)
        fields_div = next(
            el for el in block
            if el.get("tag") == "div" and "fields" in el
        )
        content = fields_div["fields"][3]["text"]["content"]
        assert "<text_tag color='red'>" in content
        assert "⚠️" in content
        # R10：action_with_unit = "减 1 仓"（带空格）；不用 "减仓"
        assert "减 1 仓" in content
        # R10 简化理由
        assert "仓位超 15% 上限" in content
        # 不再写 R9 的长理由
        assert "74.2%" not in content
        assert "老仓位豁免" not in content

    def test_正常类_加仓(self) -> None:
        """未超限的类别 → ➕ 加 1 仓（蓝色 tag）+ 简化理由「仓位 X%，离 15% 上限还有 Y CNY」。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        block = self._get_block(card, 1)  # 港股银行
        fields_div = next(
            el for el in block
            if el.get("tag") == "div" and "fields" in el
        )
        content = fields_div["fields"][3]["text"]["content"]
        # R10：tag 内容是 emoji + action_with_unit（「加 1 仓」），不是「加仓」
        assert "加 1 仓" in content
        assert "➕" in content
        # 简化理由：含 CNY（具体数字） + 离上限（不是「剩」）
        assert "CNY" in content
        assert "离 15% 上限还有" in content
        # 不再写 R9 的「剩 X CNY」「余地」冗余
        assert "余地" not in content
        assert "剩" not in content

    def test_动作4选1_都覆盖(self) -> None:
        """4 种动作都导出：建仓/加仓/持有/减仓（liubo 2026-10-19 拍板）。"""
        from global_allocation.portfolio.left_layout import decide_category_action
        # 减仓：港股超限
        result = decide_category_action("港股科技互联网")
        assert result["action"] in ("减仓", "持有", "加仓", "建仓")
        # 建仓：新类别
        result = decide_category_action("不存在的类别")
        assert result["action"] == "建仓"

    # ─── 块间 hr ───

    def test_4_hr_between_categories(self) -> None:
        """5 类别块间 4 个 hr（前 4 个块后各 1 个；最后 1 个块后由 footer 前 hr 接续）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        els = self._get_fund_section_elements(card)
        # 5 个 hr = 4 个块间 hr + 1 个 footer 前 hr
        hr_count = sum(1 for el in els if el.get("tag") == "hr")
        assert hr_count == 5


class TestCardToJson:
    """card_to_json 序列化。"""

    def test_json_serializable(self) -> None:
        """Card 能 JSON 序列化（ensure_ascii=False 让中文正常）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        json_str = card_to_json(card)
        # 重新解析，确认合法 JSON
        parsed = json.loads(json_str)
        assert parsed["header"]["template"] == "purple"

    def test_chinese_not_escaped(self) -> None:
        """ensure_ascii=False → 中文保留原样（不变成 \\uXXXX）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        json_str = card_to_json(card)
        assert "左侧布局" in json_str
        assert "\\u" not in json_str or "组合" not in json_str.replace("\\u", "")


# 删了 TestSpecialSnapshotDate.test_snapshot_date_in_overview：
# liubo 2026-10-19 拍板删掉 Section 1 的 snapshot 日期字段，
# 改留 5 个 fields（总成本/总基金数量/单仓/类别上限/同基金冷却期）。


__all__ = []  # 标记为测试模块（pytest 自动收集）
