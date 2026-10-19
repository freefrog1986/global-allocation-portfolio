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

    def test_three_sections(self) -> None:
        """3 个 section header: 组合总览 / 类别上限状态 / 各基金仓位 + 估值 + 调仓建议。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        notes = _all_note_texts(card)
        assert "组合总览" in notes
        # 类别上限 section 改了更详细的名字（liubo 2026-10-19 加括号）
        assert any("类别上限状态" in n for n in notes)
        assert "各基金仓位 + 估值 + 调仓建议" in notes

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
        # 总基金数量 → 9 只
        assert "9 只" in labels
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


class TestFundSection:
    """Section 3：各基金仓位 + 估值 + 调仓建议（liubo 2026-10-19 拍板：删 table，每类别一个 div + hr 隔开）。

    结构：
    - section header div（icon=wallet_outlined）
    - 5 个类别区块，每个区块：
      - category header div（icon=tag_outlined + 类别名 bold）
      - N 个 markdown 行（**基金全名（code）**  累计 X · 仓位 X · 估值 X · 调仓 X）
      - 前 4 个类别后跟 hr（最后一个不加，避免 footer 前 hr 重复）
    """

    def _get_fund_section_elements(self, card: dict) -> list[dict]:
        """提取 Section 3 的所有 elements（从 section header 到灰色 footer 之前，不含灰色 footer）。"""
        elements = card["body"]["elements"]
        start = None
        for i, el in enumerate(elements):
            if _is_section_header(el, "各基金仓位 + 估值 + 调仓建议"):
                start = i
                break
        assert start is not None, "没找到 Section 3 header"
        end = len(elements)
        for j in range(start + 1, len(elements)):
            el = elements[j]
            if (el.get("tag") == "markdown"
                    and "color='grey'" in el.get("content", "")):
                end = j
                break
        return elements[start:end]

    def _get_fund_lines(self, card: dict) -> list[str]:
        """提取 Section 3 里所有「**基金全名（code）** ...」markdown 行。"""
        els = self._get_fund_section_elements(card)
        return [
            el["content"]
            for el in els
            if el.get("tag") == "markdown"
            and el["content"].startswith("**")
            and "（" in el["content"]
            and "）" in el["content"]
        ]

    def _get_category_names(self, card: dict) -> list[str]:
        """提取 Section 3 里所有类别 div header（用 tag_outlined icon 区分于 section header）。"""
        els = self._get_fund_section_elements(card)
        names: list[str] = []
        for el in els:
            if el.get("tag") == "div":
                text_obj = el.get("text", {})
                if text_obj.get("tag") == "lark_md":
                    content = text_obj.get("content", "")
                    s = content.strip().strip("*").strip()
                    icon_token = el.get("icon", {}).get("token")
                    if icon_token == "tag_outlined" and s:
                        names.append(s)
        return names

    def test_section_header_present(self) -> None:
        """section header：div + wallet_outlined icon + 「各基金仓位 + 估值 + 调仓建议」。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        els = self._get_fund_section_elements(card)
        assert els[0]["tag"] == "div"
        assert els[0]["icon"]["token"] == "wallet_outlined"
        assert "各基金仓位 + 估值 + 调仓建议" in els[0]["text"]["content"]

    def test_no_table_in_fund_section(self) -> None:
        """Section 3 完全没用 table（liubo 2026-10-19 拍板改 div+hr）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        els = self._get_fund_section_elements(card)
        for el in els:
            assert el.get("tag") != "table", "Section 3 不应有 table 元素"

    def test_5_category_divs_in_order(self) -> None:
        """5 个类别 div 按 STRATEGY_CATEGORIES 顺序：港股科技互联网 → 房地产。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        names = self._get_category_names(card)
        assert names == ["港股科技互联网", "港股银行", "亚太除日本", "全球科技主动", "房地产"]

    def test_category_div_uses_tag_icon_and_bold(self) -> None:
        """每个类别 div 带 tag_outlined icon + bold 类别名。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        els = self._get_fund_section_elements(card)
        cat_divs = [
            el for el in els
            if el.get("tag") == "div"
            and el.get("icon", {}).get("token") == "tag_outlined"
        ]
        assert len(cat_divs) == 5
        for d in cat_divs:
            content = d["text"]["content"]
            assert content.startswith("**") and content.endswith("**")

    def test_9_fund_markdown_lines(self) -> None:
        """9 只基金各自 1 个 markdown 行（5 类共 9 只，2026-09-30 加 010989 后）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        lines = self._get_fund_lines(card)
        assert len(lines) == 9

    def test_4_hr_between_categories(self) -> None:
        """5 个类别之间 4 个 hr（最后一个不加，避免和 footer 前的 hr 视觉重叠）。

        Footer 前的 hr 在 _get_fund_section_elements 范围里会被算进来，
        所以预期是 4 + 1（footer 分隔）= 5 个 hr。
        """
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        els = self._get_fund_section_elements(card)
        hr_count = sum(1 for el in els if el.get("tag") == "hr")
        assert hr_count == 5, f"期望 5 个 hr（4 个类别分隔 + 1 个 footer 分隔），实际 {hr_count}"

    def test_hr_pattern_5_category_blocks(self) -> None:
        """5 个类别块：每个块以 hr 结尾（除最后 1 个，最后以 footer 分隔 hr 接续）。
        """
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        els = self._get_fund_section_elements(card)

        # 找所有类别 div 索引
        cat_indices = [
            i for i, el in enumerate(els)
            if el.get("tag") == "div"
            and el.get("icon", {}).get("token") == "tag_outlined"
        ]
        assert len(cat_indices) == 5

        # 前 4 个类别块（块之间）应该有 4 个 hr
        # 最后一个块（最后一个 hr 之前）应该有 footer 分隔 hr
        # 用更简单的检查：5 个 hr / 5 个类别 div 比 = 1:1（"每块都跟 hr"）
        assert len(cat_indices) == 5
        # 每块以 hr 结尾（最后 1 个块以 footer hr 接续，所以也以 hr 结尾）
        for idx, cat_pos in enumerate(cat_indices):
            next_cat_pos = cat_indices[idx + 1] if idx + 1 < len(cat_indices) else len(els)
            block_els = els[cat_pos + 1:next_cat_pos]
            # 所有类别块以 hr 结尾（最后 1 个块的 hr 就是 footer 分隔 hr）
            assert block_els[-1].get("tag") == "hr", (
                f"类别 #{idx+1} 之后应有 hr，实际以 {block_els[-1].get('tag')} 结尾"
            )

    def test_no_hr_after_last_category_block(self) -> None:
        """footer 前 hr 在最后 1 个基金行之后（最后一个类别块后没有 hr）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        elements = card["body"]["elements"]
        last_fund_idx = None
        for i, el in enumerate(elements):
            if (el.get("tag") == "markdown"
                    and el["content"].startswith("**")
                    and "（" in el["content"]):
                last_fund_idx = i
        assert last_fund_idx is not None
        # 在最后 1 个基金行后面、灰色 footer 之前应该是 1 个 hr（footer 前的分隔）
        # 找灰色 footer 之前的 hr
        grey_footer_idx = None
        for i, el in enumerate(elements):
            if (el.get("tag") == "markdown"
                    and "color='grey'" in el.get("content", "")):
                grey_footer_idx = i
                break
        assert grey_footer_idx is not None
        # 在 [last_fund_idx, grey_footer_idx) 区间内只有 1 个 hr（即 footer 前的分隔）
        hr_in_between = sum(
            1 for i in range(last_fund_idx, grey_footer_idx)
            if elements[i].get("tag") == "hr"
        )
        assert hr_in_between == 1, f"最后一个类别块之后只应有 1 个 hr（footer 分隔），实际 {hr_in_between}"

    def test_all_9_funds_present(self) -> None:
        """9 只基金全名 + code 都出现在 markdown 行里。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        lines = self._get_fund_lines(card)
        all_text = "\n".join(lines)
        expected_names = [
            "易方达中证海外互联网50ETF联接(QDII)A（006327）",
            "富国中证港股通互联网ETF发起式联接A（014673）",
            "景顺长城中证港股通科技ETF发起联接A（016495）",
            "泰康香港银行指数A（006809）",
            "国富亚洲机会股票(QDII)A（457001）",
            "天弘全球高端制造混合(QDII)A（016664）",
            "国富全球科技互联混合(QDII)人民币A（006373）",
            "嘉实全球产业升级股票发起式(QDII)A（017730）",
            "南方中证房地产ETF发起联接E（010989）",
        ]
        for name in expected_names:
            assert name in all_text, f"缺少基金行：{name}"

    def test_013127_not_in_section(self) -> None:
        """013127 已转走，不在 Section 3。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        lines = self._get_fund_lines(card)
        all_text = "\n".join(lines)
        assert "013127" not in all_text

    def test_funds_grouped_under_correct_category(self) -> None:
        """每只基金行出现在它所属类别 div 之后、下一个类别 div 之前。"""
        import re
        from global_allocation.portfolio.left_layout import STRATEGY_CATEGORIES

        card = build_left_layout_card(report_date=date(2026, 9, 30))
        els = self._get_fund_section_elements(card)

        # 类别 div 索引
        cat_positions: list[tuple[int, str]] = []
        for i, el in enumerate(els):
            if (el.get("tag") == "div"
                    and el.get("icon", {}).get("token") == "tag_outlined"):
                name = el["text"]["content"].strip().strip("*")
                cat_positions.append((i, name))

        for i, el in enumerate(els):
            if not (el.get("tag") == "markdown"
                    and el["content"].startswith("**")
                    and "（" in el["content"]):
                continue
            # 找这只基金归属的类别（最近的更早 cat div）
            fund_cat = None
            for pos, name in cat_positions:
                if pos < i:
                    fund_cat = name
                else:
                    break
            assert fund_cat is not None
            # 提取基金 code
            m = re.search(r"（(\d{6})）", el["content"])
            assert m is not None, f"基金行没找到 code：{el['content']}"
            code = m.group(1)
            assert code in STRATEGY_CATEGORIES[fund_cat], (
                f"基金 {code} 出现在 {fund_cat} 区块下，但 STRATEGY_CATEGORIES 不一致"
            )

    def test_position_values_correct(self) -> None:
        """仓位数字 = cost / 5000。006327 (cost=10000) → 2.00 仓；457001 (cost=4640) → 0.93 仓。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        lines = self._get_fund_lines(card)
        line_006327 = next(l for l in lines if "006327" in l)
        assert "2.00 仓" in line_006327
        line_457001 = next(l for l in lines if "457001" in l)
        assert "0.93 仓" in line_457001

    def test_cost_values_in_line(self) -> None:
        """每行包含「累计 X CNY」（10,000 / 23,000 / 20,000 / 5,000 / ...）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        lines = self._get_fund_lines(card)
        all_text = "\n".join(lines)
        assert "10,000" in all_text   # 006327
        assert "23,000" in all_text   # 014673
        assert "20,000" in all_text   # 016495
        assert "5,000" in all_text    # 010989

    def test_valuation_default_dash(self) -> None:
        """估值列：VALUATION_BY_CODE 未填 → 9 行全部显示「—」。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        lines = self._get_fund_lines(card)
        for line in lines:
            assert "估值 —" in line, f"估值列应为 「—」：{line}"

    def test_suggestion_subjective_hold_for_normal_funds(self) -> None:
        """非港股科技互联网的 6 只基金行 → 调仓 「主观持有」（liubo 2026-09-29 拍板）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        lines = self._get_fund_lines(card)
        non_hk_tech_codes = ["006809", "457001", "016664", "006373", "017730", "010989"]
        for code in non_hk_tech_codes:
            line = next(l for l in lines if code in l)
            assert "主观持有" in line, f"{code} 调仓建议应为 「主观持有」：{line}"
            assert "超限" not in line
            assert "冷却中" not in line

    def test_suggestion_hk_tech_over_cap(self) -> None:
        """港股科技互联网 3 只（74.2% 超 15% 上限）→ 调仓列显示「⚠️ 类别超限 74.2%」。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        lines = self._get_fund_lines(card)
        for code in ("006327", "014673", "016495"):
            line = next(l for l in lines if code in l)
            assert "超限" in line, f"{code} 应显示超限：{line}"
            assert "74" in line, f"{code} 超限比例应含 74.x：{line}"

    def test_line_contains_every_field(self) -> None:
        """每行 markdown 包含 3 个 · 分隔：累计 + 仓位 + 估值 + 调仓（4 段）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        lines = self._get_fund_lines(card)
        for line in lines:
            assert line.count("·") == 3, f"应有 3 个 · 分隔符（4 段）：{line}"
            assert "累计" in line
            assert "仓位" in line
            assert "估值" in line
            assert "调仓" in line


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
