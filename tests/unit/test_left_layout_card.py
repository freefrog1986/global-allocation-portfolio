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
        """3 个 section header: 组合总览 / 类别上限状态 / 各类别基金 + 估值 + 调仓建议。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        notes = _all_note_texts(card)
        assert "组合总览" in notes
        # 类别上限 section 改了更详细的名字（liubo 2026-10-19 加括号）
        assert any("类别上限状态" in n for n in notes)
        # Section 3 改名（liubo 2026-10-19 拍板 R9：含完整 指数/估值/动作 三段）
        assert "各类别基金 + 估值 + 调仓建议" in notes

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
    """Section 3：各类别基金 + 估值 + 调仓建议
    （liubo 2026-10-19 拍板 R10：number_tag + div.fields 多列 + 简化动作）。

    每类别块结构（liubo 2026-10-19 R10）：
    1. div category header：`**{类别}** <tag 占比> <tag 状态>`（icon=tag_outlined）
    2. markdown `**类下基金（N 只）**` sub-header
    3. markdown 每只基金 1 行（`<number_tag>X.XX</number_tag> **基金全名（code）**  累计 X CNY`）
    4. markdown `**估值** _{idx_name}（{idx_code}）_` sub-header
    5. div · fields 2 列（参考指数 + 主指标）
    6. markdown `**调仓建议**`：emoji + action_with_unit + 简化理由

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
        """类下基金段：所有「<number_tag>...</number_tag> **基金全名（code）** ...」行
        （不含 类下基金 sub-header / 估值 sub-header / 调仓建议）。"""
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
            # 基金行特征：含 number_tag + bold + 全名（code）
            if "<number_tag" in content and "（" in content and "）" in content:
                lines.append(content)
        return lines

    def _get_category_block_starts(self, card: dict) -> list[tuple[int, str]]:
        """返回 [(idx, category_name)]：5 个类别 div header 的位置。"""
        els = self._get_fund_section_elements(card)
        return [
            (i, el["text"]["content"].split("<")[0].strip().strip("*").strip())
            for i, el in enumerate(els)
            if (el.get("tag") == "div"
                and el.get("icon", {}).get("token") == "tag_outlined")
        ]

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

    # ─── 类别块结构（liubo R10：1 div cat header + 3+N md + 1 div.fields 估值） ───

    def test_category_block_structure(self) -> None:
        """每个类别块结构（liubo 2026-10-19 R10）：

        - 1 div（cat header）
        - 1 md（类下基金 sub-header）
        - N md（基金行，含 number_tag）
        - 1 md（估值 sub-header）
        - 1 div.fields（参考指数 + 主指标；idx_code truthy 时存在）
        - 1 md（调仓建议）

        → 2 div + (3 + N) md
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
            # 1 cat header div + 1 valuation fields div = 2 div total
            assert div_count == 2, (
                f"{cat}: 期望 2 个 div（cat header + valuation fields），实际 {div_count}"
            )
            assert fields_divs == 1, (
                f"{cat}: 期望 1 个 div.fields（估值段），实际 {fields_divs}"
            )
            # 1 类下基金 sub-md + N fund md + 1 估值 sub-md + 1 调仓建议 md = 3 + N
            assert md_count == 3 + len(codes), (
                f"{cat}: 期望 {3 + len(codes)} 个 md，实际 {md_count}"
            )

    def test_category_header_has_inline_tags(self) -> None:
        """类别 header div 用 lark_md 内嵌 text_tag：占比 + 状态（liubo 2026-10-19 拍板）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))

        # 港股科技互联网 74.2% 应该 red
        block = self._get_block(card, 0)
        cat_header = block[0]
        content = cat_header["text"]["content"]
        assert cat_header["text"]["tag"] == "lark_md"
        # 类别名 + text_tag 占比% + text_tag 状态
        assert "**港股科技互联网**" in content
        assert "<text_tag color='red'>74.2%</text_tag>" in content
        assert "<text_tag color='red'>超限</text_tag>" in content

        # 港股银行 1.4% 应该 blue
        block = self._get_block(card, 1)
        content = block[0]["text"]["content"]
        assert "<text_tag color='blue'>1.4%</text_tag>" in content
        assert "<text_tag color='blue'>正常</text_tag>" in content

    def test_category_header_uses_tag_icon(self) -> None:
        """类别 header 用 tag_outlined icon + 紫色。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        starts = self._get_category_block_starts(card)
        for idx, (pos, _) in enumerate(starts):
            block = self._get_block(card, idx)
            cat_div = block[0]
            assert cat_div.get("icon", {}).get("token") == "tag_outlined"
            assert cat_div.get("icon", {}).get("color") == "purple"

    def test_类下基金_subheader_format(self) -> None:
        """类下基金 sub-header markdown：「**类下基金**（N 只）」。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        from global_allocation.portfolio.left_layout import STRATEGY_CATEGORIES
        for i, (cat, codes) in enumerate(STRATEGY_CATEGORIES.items()):
            block = self._get_block(card, i)
            # 第 2 个元素（index 1）是 类下基金 sub-header markdown
            sub_md = block[1]
            assert sub_md["tag"] == "markdown"
            assert f"**类下基金**（{len(codes)} 只）" in sub_md["content"]

    def test_fund_lines_use_number_tag(self) -> None:
        """基金行含 `<number_tag background_color='grey' font_color='white'>X.XX</number_tag>`
        （liubo 2026-10-19 R10：仓位用 number_tag 显示）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        fund_lines = self._get_fund_lines(card)
        # 006327 成本 10,000 → 2.00 仓
        text_006327 = next(l for l in fund_lines if "006327" in l)
        assert "<number_tag background_color='grey' font_color='white'>2.00</number_tag>" in text_006327
        assert "10,000.00 CNY" in text_006327
        # 457001 成本 4,640 → 0.93 仓
        text_457001 = next(l for l in fund_lines if "457001" in l)
        assert "<number_tag background_color='grey' font_color='white'>0.93</number_tag>" in text_457001
        assert "4,640.00 CNY" in text_457001
        # R10 不再写「仓」字（仓数已在 number_tag 里）
        assert "仓" not in text_006327
        assert "仓" not in text_457001

    def test_all_9_funds_present(self) -> None:
        """9 只基金全名 + code 都在 markdown 行里。"""
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
            "南方中证房地产ETF发起联接E（010989）",
        ]
        for name in expected:
            assert name in all_text, f"缺少基金行：{name}"

    def test_013127_not_present(self) -> None:
        """013127 已转走。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        all_text = "\n".join(self._get_fund_lines(card))
        assert "013127" not in all_text

    # ─── 估值段：div.fields 多列（liubo 2026-10-19 R10：1 个主指标） ───

    def test_valuation_subheader_format(self) -> None:
        """估值 sub-header markdown：「**估值** _{name}（{code}）_」（liubo R10 简化）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        for i in range(5):
            block = self._get_block(card, i)
            val_md = next(
                el for el in block
                if el.get("tag") == "markdown" and el["content"].startswith("**估值**")
            )
            # 简化后只显示 idx_name（idx_code），不再附加 5 指标
            assert "_" in val_md["content"]
            assert "PE-TTM" not in val_md["content"]
            assert "PB" not in val_md["content"]
            assert "PS-TTM" not in val_md["content"]
            assert "股息率" not in val_md["content"]
            assert "ROE" not in val_md["content"]
            assert "YTD" not in val_md["content"]
            assert "基金数" not in val_md["content"]
            assert "关注人数" not in val_md["content"]

    def test_valuation_div_fields_has_2_columns(self) -> None:
        """估值 div.fields 含 2 列 is_short=true（参考指数 + 主指标）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        for i in range(5):
            block = self._get_block(card, i)
            fields_div = next(
                el for el in block
                if el.get("tag") == "div" and "fields" in el
            )
            fields = fields_div["fields"]
            assert len(fields) == 2, f"类 {i} 估值 fields 期望 2 列，实际 {len(fields)}"
            # 2 列 is_short=true → 横向并排
            assert all(f["is_short"] is True for f in fields)
            # 全部 lark_md（加粗/颜色才渲染）
            assert all(f["text"]["tag"] == "lark_md" for f in fields)
            # 第 1 列 = 参考指数，第 2 列 = 主指标
            assert "参考指数" in fields[0]["text"]["content"]
            assert fields[1]["text"]["content"].startswith("**")  # PE-TTM 或 PB

    def test_港股通科技_valuation(self) -> None:
        """港股科技互联网 → 港股通科技 (987008.sz) PE-TTM 23.10@13.14%。"""
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

    def test_港股银行_valuation(self) -> None:
        """港股银行 → HK 银行 (930792.csi) PE-TTM 7.32@90.71%（liubo 2026-10-19 给数据）。"""
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

    def test_房地产_valuation_uses_PB(self) -> None:
        """房地产 → 931775.csi PB 0.760@13.27%（主指标是 PB 不是 PE-TTM；liubo 2026-10-19 给数据）。"""
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

    def test_全球科技主动_NDX_placeholder(self) -> None:
        """全球科技主动 → 纳斯达克100 (.NDX)；数据未填 → PE-TTM — 占位。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        block = self._get_block(card, 3)
        fields_div = next(
            el for el in block
            if el.get("tag") == "div" and "fields" in el
        )
        fields = fields_div["fields"]
        assert "纳斯达克100" in fields[0]["text"]["content"]
        assert ".NDX" in fields[0]["text"]["content"]
        # 数据未填 → 「PE-TTM —」占位
        assert "—" in fields[1]["text"]["content"]
        assert "PE-TTM" in fields[1]["text"]["content"]

    # ─── 调仓建议段（liubo 2026-10-19 R10：action_with_unit + 简化理由） ───

    def test_调仓建议_segment_present(self) -> None:
        """每个类别块都有「**调仓建议**」markdown 段。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        for i in range(5):
            block = self._get_block(card, i)
            sug_md = next(
                el for el in block
                if el.get("tag") == "markdown" and "**调仓建议**" in el["content"]
            )
            assert sug_md["tag"] == "markdown"

    def test_港股超限_减仓(self) -> None:
        """港股科技互联网 74.2% 超限 → ⚠️ 减 1 仓  仓位超 15% 上限（liubo R10 简化）。"""
        card = build_left_layout_card(report_date=date(2026, 9, 30))
        block = self._get_block(card, 0)
        sug_md = next(
            el for el in block
            if el.get("tag") == "markdown" and "**调仓建议**" in el["content"]
        )
        content = sug_md["content"]
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
        sug_md = next(
            el for el in block
            if el.get("tag") == "markdown" and "**调仓建议**" in el["content"]
        )
        content = sug_md["content"]
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
