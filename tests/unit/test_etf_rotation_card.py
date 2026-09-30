"""测试 src/global_allocation/portfolio/etf_rotation_card.py。

飞书 Interactive Card 3 段结构：
- Section 1: 组合总览（总成本 / 总资产 / 总盈亏 / 总收益率）
- Section 2: 类别上限状态（4 类累计 / 上限 / 使用率 / 状态）
- Section 3: 各基金仓位 + 冷却期（8 只基金 + 冷却期状态）

liubo 2026-09-29 拍板：ETF 轮动组合 周报必须用 Interactive Card。
"""

from __future__ import annotations

import json
from datetime import date

from global_allocation.portfolio.etf_rotation_card import (
    build_etf_rotation_card,
    card_to_json,
)


def _find_table_after_note(card: dict, note_text: str) -> dict | None:
    """找到指定 note header 后面的 table。返回 None 表示没找到。"""
    elements = card["elements"]
    for i, el in enumerate(elements):
        if (
            el["tag"] == "note"
            and note_text in el["elements"][0]["content"]
        ):
            # 找这个 note 后面的下一个 table
            for j in range(i + 1, len(elements)):
                if elements[j]["tag"] == "table":
                    return elements[j]
                if elements[j]["tag"] == "note":
                    return None  # 碰到下一个 note 就停
            return None
    return None


def _all_note_texts(card: dict) -> list[str]:
    """提取所有 note header 的文字。"""
    notes: list[str] = []
    for el in card["elements"]:
        if el["tag"] == "note":
            for sub in el["elements"]:
                if "content" in sub:
                    notes.append(sub["content"])
    return notes


def _all_tables(card: dict) -> list[dict]:
    """提取所有 table 元素。"""
    return [el for el in card["elements"] if el["tag"] == "table"]


class TestCardStructure:
    """Card 顶层结构验证。"""

    def test_has_header(self) -> None:
        """header.template + title。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        assert "header" in card
        assert card["header"]["template"] == "purple"
        assert "title" in card["header"]
        assert card["header"]["title"]["tag"] == "plain_text"
        assert "ETF 轮动组合 周快照" in card["header"]["title"]["content"]
        assert "2026-09-29" in card["header"]["title"]["content"]

    def test_has_elements(self) -> None:
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        assert "elements" in card
        assert isinstance(card["elements"], list)
        assert len(card["elements"]) > 0

    def test_has_footer(self) -> None:
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        assert "footer" in card
        footer_text = card["footer"]["elements"][0]["content"]
        assert "ETF 轮动组合" in footer_text

    def test_three_sections(self) -> None:
        """3 个 section header: 组合总览 / 类别上限状态 / 各基金仓位 + 冷却期。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        notes = _all_note_texts(card)
        assert "组合总览" in notes
        assert "类别上限状态" in notes
        assert "各基金仓位 + 冷却期" in notes

    def test_default_report_date_is_today(self) -> None:
        """report_date=None → 用今天（日期格式是 ISO）。"""
        card = build_etf_rotation_card()
        title = card["header"]["title"]["content"]
        assert "ETF 轮动组合 周快照 (" in title


class TestOverviewSection:
    """Section 1：组合总览。"""

    def test_overview_div_contains_totals(self) -> None:
        """div 文本含总成本 / 总资产 / 总盈亏 / 收益率。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        # 找组合总览后面的 div
        overview_div = None
        for i, el in enumerate(card["elements"]):
            if (
                el["tag"] == "note"
                and "组合总览" in el["elements"][0]["content"]
            ):
                # 下一个 div
                for j in range(i + 1, len(card["elements"])):
                    if card["elements"][j]["tag"] == "div":
                        overview_div = card["elements"][j]
                        break
                break
        assert overview_div is not None
        text = overview_div["text"]["content"]
        assert "总成本" in text
        assert "66,420" in text  # 总成本数字
        assert "62,090.41" in text  # 总资产
        assert "总盈亏" in text
        assert "-4,329.59" in text  # P&L 数字
        assert "总收益率" in text or "%" in text

    def test_overview_mentions_strategy_params(self) -> None:
        """div 文本包含单仓 / 类别上限 / 冷却期常量。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        overview_div = None
        for i, el in enumerate(card["elements"]):
            if (
                el["tag"] == "note"
                and "组合总览" in el["elements"][0]["content"]
            ):
                for j in range(i + 1, len(card["elements"])):
                    if card["elements"][j]["tag"] == "div":
                        overview_div = card["elements"][j]
                        break
                break
        text = overview_div["text"]["content"]
        assert "5,000" in text  # 1 仓
        assert "30%" in text  # 类别上限
        assert "7 天" in text  # 冷却期


class TestCategorySection:
    """Section 2：类别上限状态表。"""

    def test_has_table(self) -> None:
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        table = _find_table_after_note(card, "类别上限状态")
        assert table is not None

    def test_table_has_5_columns(self) -> None:
        """5 列：类别 / 累计成本 / 上限 / 使用率 / 状态。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        table = _find_table_after_note(card, "类别上限状态")
        assert len(table["columns"]) == 5

    def test_table_has_4_rows(self) -> None:
        """4 个类别（港股科技互联网 / 港股银行 / 亚太除日本 / 全球科技主动）。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        table = _find_table_after_note(card, "类别上限状态")
        assert len(table["rows"]) == 4

    def test_row_keys_are_ascii(self) -> None:
        """row keys 用 ASCII（飞书 API 要求）。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        table = _find_table_after_note(card, "类别上限状态")
        for row in table["rows"]:
            for key in row.keys():
                assert all(ord(c) < 128 for c in key), f"非 ASCII key: {key}"

    def test_hk_tech_internet_marked_as_over(self) -> None:
        """港股科技互联网 53,000 / 19,926 = 266% → 标 '⚠️ 超限（豁免）'。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        table = _find_table_after_note(card, "类别上限状态")
        hk_tech_row = next(
            r for r in table["rows"] if r["cat"] == "港股科技互联网"
        )
        assert "⚠️" in hk_tech_row["state"]
        assert "超限" in hk_tech_row["state"]
        assert "豁免" in hk_tech_row["state"]
        # 使用率 > 100%
        pct_text = hk_tech_row["pct"]
        assert float(pct_text.rstrip("%")) > 100

    def test_hk_bank_marked_as_under(self) -> None:
        """港股银行 1,010 / 19,926 = 5% → 标 '✓ 未超限'。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        table = _find_table_after_note(card, "类别上限状态")
        hk_bank_row = next(r for r in table["rows"] if r["cat"] == "港股银行")
        assert "✓" in hk_bank_row["state"]
        assert "未超限" in hk_bank_row["state"]


class TestFundSection:
    """Section 3：各基金仓位 + 冷却期表。"""

    def test_has_table(self) -> None:
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        table = _find_table_after_note(card, "各基金仓位 + 冷却期")
        assert table is not None

    def test_table_has_5_columns(self) -> None:
        """5 列：基金 / 类别 / 累计成本 / 仓位 / 冷却期。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        table = _find_table_after_note(card, "各基金仓位 + 冷却期")
        assert len(table["columns"]) == 5

    def test_table_has_8_funds(self) -> None:
        """8 只基金（2026-09-29 减 013127 后）。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        table = _find_table_after_note(card, "各基金仓位 + 冷却期")
        assert len(table["rows"]) == 8

    def test_all_8_funds_present(self) -> None:
        """8 只基金都在表格里。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        table = _find_table_after_note(card, "各基金仓位 + 冷却期")
        fund_names = {r["fund"] for r in table["rows"]}
        expected_names = {
            "港股科技互联网（006327）",
            "港股科技互联网（014673）",
            "港股科技互联网（016495）",
            "港股银行（006809）",
            "亚太除日本（457001）",
            "全球科技主动（016664）",
            "全球科技主动（006373）",
            "全球科技主动（017730）",
        }
        assert fund_names == expected_names

    def test_013127_not_in_table(self) -> None:
        """013127 已转走，不在表格里。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        table = _find_table_after_note(card, "各基金仓位 + 冷却期")
        fund_names = {r["fund"] for r in table["rows"]}
        assert not any("013127" in name for name in fund_names)

    def test_position_values_correct(self) -> None:
        """仓位数字 = cost / 5000。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        table = _find_table_after_note(card, "各基金仓位 + 冷却期")
        # 找 006327 (cost=10000) → 2.00 仓
        row_006327 = next(r for r in table["rows"] if "006327" in r["fund"])
        assert row_006327["pos"] == "2.00 仓"
        # 找 457001 (cost=4640) → 0.93 仓
        row_457001 = next(r for r in table["rows"] if "457001" in r["fund"])
        assert "0.93 仓" in row_457001["pos"]

    def test_cooldown_first_time_trade(self) -> None:
        """首次交易：冷却期列显示 '✓ 可交易'。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        table = _find_table_after_note(card, "各基金仓位 + 冷却期")
        # 默认 LAST_TRADE_BY_FUND 空 → 全部首次交易
        for row in table["rows"]:
            assert "可交易" in row["cd"]


class TestCardToJson:
    """card_to_json 序列化。"""

    def test_json_serializable(self) -> None:
        """Card 能 JSON 序列化（ensure_ascii=False 让中文正常）。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        json_str = card_to_json(card)
        # 重新解析，确认合法 JSON
        parsed = json.loads(json_str)
        assert parsed["header"]["template"] == "purple"

    def test_chinese_not_escaped(self) -> None:
        """ensure_ascii=False → 中文保留原样（不变成 \\uXXXX）。"""
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        json_str = card_to_json(card)
        assert "ETF 轮动组合" in json_str
        assert "\\u" not in json_str or "组合" not in json_str.replace("\\u", "")


class TestSpecialSnapshotDate:
    """snapshot 日期是 2026-09-29（013127 移走当天）。"""

    def test_snapshot_date_in_overview(self) -> None:
        card = build_etf_rotation_card(report_date=date(2026, 9, 29))
        # 找组合总览后面的 div
        for i, el in enumerate(card["elements"]):
            if (
                el["tag"] == "note"
                and "组合总览" in el["elements"][0]["content"]
            ):
                for j in range(i + 1, len(card["elements"])):
                    if card["elements"][j]["tag"] == "div":
                        text = card["elements"][j]["text"]["content"]
                        assert "2026-09-29" in text
                        return
        raise AssertionError("没找到组合总览 div")


__all__ = []  # 标记为测试模块（pytest 自动收集）
