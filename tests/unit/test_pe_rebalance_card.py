"""测试 src/global_allocation/portfolio/pe_rebalance_card.py。

飞书 Interactive Card 3 段结构：
- Section 1: 本周操作（仅 ADD/REDUCE/BUILD）
- Section 2: 各子类估值详情（含 HOLD）
- Section 3: PE 不适用 / 数据缺失（SKIP）

测试用 mock 后的 weekly_rebalance_plan() 输出喂给 build_pe_rebalance_card()。
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from global_allocation.portfolio.breakdown import SwensenClass
from global_allocation.portfolio.pe_rebalance import (
    PESignal,
    RebalanceAction,
)
from global_allocation.portfolio.pe_rebalance_card import (
    SIGNAL_DISPLAY,
    SUBCLASS_DISPLAY,
    build_pe_rebalance_card,
    card_to_json,
)


def _make_action(
    signal: PESignal,
    subclass: SwensenClass,
    fund_code: str = "000000",
    fund_name: str = "测试基金",
    etf_index_code: str = "TEST",
    etf_index_name: str = "测试指数",
    current_position: Decimal = Decimal("0"),
    metric_value: Decimal | None = Decimal("20.0"),
    metric_percentile: Decimal | None = Decimal("0.50"),
    change: Decimal = Decimal("0"),
    reason: str = "测试原因",
) -> RebalanceAction:
    """构造一个 RebalanceAction（测试用）。"""
    from global_allocation.portfolio.pe_rebalance import ValuationMetric
    return RebalanceAction(
        fund_code=fund_code,
        fund_name=fund_name,
        subclass=subclass,
        etf_index_code=etf_index_code,
        etf_index_name=etf_index_name,
        metric=ValuationMetric.PE_TTM,
        metric_value=metric_value,
        metric_percentile=metric_percentile,
        current_position=current_position,
        signal=signal,
        change=change,
        reason=reason,
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


class TestSignalDisplay:
    def test_all_signals_have_display(self) -> None:
        for s in PESignal:
            assert SIGNAL_DISPLAY[s]  # 非空字符串


class TestSubclassDisplay:
    def test_cn_equity_shows_chinese(self) -> None:
        assert SUBCLASS_DISPLAY[SwensenClass.CN_EQUITY] == "A 股"

    def test_us_equity_shows_chinese(self) -> None:
        assert SUBCLASS_DISPLAY[SwensenClass.US_EQUITY] == "美股"

    def test_em_equity_shows_chinese(self) -> None:
        assert SUBCLASS_DISPLAY[SwensenClass.EM_EQUITY] == "新兴市场"

    def test_reits_show_chinese(self) -> None:
        assert SUBCLASS_DISPLAY[SwensenClass.CN_REIT] == "国内 REITs"
        assert SUBCLASS_DISPLAY[SwensenClass.US_REIT] == "美国 REITs"

    def test_all_subclasses_have_display(self) -> None:
        for sc in SwensenClass:
            assert SUBCLASS_DISPLAY[sc]  # 非空


class TestCardStructure:
    def test_returns_valid_dict(self) -> None:
        """调用空 actions 返回完整 dict 结构。"""
        card = build_pe_rebalance_card(actions=[], report_date=date(2026, 9, 24))
        assert "header" in card
        assert "elements" in card
        assert "footer" in card
        assert card["header"]["title"]["content"] == "估值分位 周调仓 (2026-09-24)"

    def test_card_to_json_returns_parseable_string(self) -> None:
        """card_to_json 返回的字符串能被 json.loads 解析。"""
        card = build_pe_rebalance_card(actions=[], report_date=date(2026, 9, 24))
        s = card_to_json(card)
        parsed = json.loads(s)
        assert parsed["header"]["title"]["content"] == "估值分位 周调仓 (2026-09-24)"

    def test_card_to_json_preserves_chinese(self) -> None:
        """ensure_ascii=False 让中文正常显示。"""
        card = build_pe_rebalance_card(actions=[], report_date=date(2026, 9, 24))
        s = card_to_json(card)
        # 不应该有 \uXXXX 转义
        assert "\\u" not in s
        assert "估值分位" in s
        assert "周调仓" in s


class TestSection1Operations:
    def test_add_action_appears_in_section1(self) -> None:
        """ADD 出现在 Section 1。"""
        actions = [
            _make_action(
                signal=PESignal.ADD,
                subclass=SwensenClass.CN_EQUITY,
                etf_index_name="中证 A50",
                fund_code="014532",
                fund_name="易方达 MSCI 中国 A50",
                current_position=Decimal("1.00"),
                change=Decimal("1"),
                reason="分位 16.94% < 20% → 加仓",
            )
        ]
        card = build_pe_rebalance_card(actions=actions, report_date=date(2026, 9, 24))
        elements = card["elements"]

        # 找第一个 table（Section 1）
        section1_table = next(el for el in elements if el["tag"] == "table")
        rows = section1_table["rows"]
        assert len(rows) == 1
        assert rows[0]["op"] == "加仓"
        assert rows[0]["etf"] == "中证 A50"
        assert rows[0]["change"] == "+1 仓 (≈+1 万 CNY)"

    def test_hold_action_does_not_appear_in_section1(self) -> None:
        """HOLD 不出现在 Section 1（只在 Section 2）。"""
        actions = [
            _make_action(signal=PESignal.HOLD, subclass=SwensenClass.CN_EQUITY)
        ]
        card = build_pe_rebalance_card(actions=actions, report_date=date(2026, 9, 24))
        elements = card["elements"]

        # Section 1 无 table — 用 div 显示「本周无操作」
        tables = [el for el in elements if el["tag"] == "table"]
        assert len(tables) == 1  # 只有 Section 2
        divs = [el for el in elements if el["tag"] == "div"]
        assert any("本周无操作" in d["text"]["content"] for d in divs)

    def test_empty_actions_shows_no_operation_message(self) -> None:
        """无操作时显示"本周无操作"div。"""
        actions = [
            _make_action(signal=PESignal.HOLD, subclass=SwensenClass.CN_EQUITY)
        ]
        card = build_pe_rebalance_card(actions=actions, report_date=date(2026, 9, 24))
        elements = card["elements"]

        # Section 1: note + div (no table)
        assert elements[0]["tag"] == "note"
        assert elements[1]["tag"] == "div"
        assert "本周无操作" in elements[1]["text"]["content"]


class TestSection2Detail:
    def test_hold_action_appears_in_section2(self) -> None:
        """HOLD 出现在 Section 2。"""
        actions = [
            _make_action(
                signal=PESignal.HOLD,
                subclass=SwensenClass.CN_EQUITY,
                etf_index_name="科创创业 50",
                current_position=Decimal("2.00"),
                metric_percentile=Decimal("0.6507"),
            )
        ]
        card = build_pe_rebalance_card(actions=actions, report_date=date(2026, 9, 24))
        section2_table = _find_table_after_note(card, "各子类估值详情")
        assert section2_table is not None
        assert len(section2_table["rows"]) == 1
        row = section2_table["rows"][0]
        assert row["sub"] == "A 股"  # 中文显示，不是 "cn_equity"
        assert row["etf"] == "科创创业 50"
        assert row["act"] == "不动"

    def test_subclass_shown_in_chinese_not_enum_value(self) -> None:
        """子类显示中文，不是 enum 值。"""
        actions = [
            _make_action(signal=PESignal.HOLD, subclass=SwensenClass.US_EQUITY),
        ]
        card = build_pe_rebalance_card(actions=actions, report_date=date(2026, 9, 24))
        section2_table = _find_table_after_note(card, "各子类估值详情")
        assert section2_table is not None
        sub_classes = [r["sub"] for r in section2_table["rows"]]
        assert sub_classes == ["美股"]

    def test_pe_ttm_and_percentile_formatted(self) -> None:
        """PE / 分位格式化正确。"""
        actions = [
            _make_action(
                signal=PESignal.HOLD,
                subclass=SwensenClass.US_EQUITY,
                metric_value=Decimal("26.215"),
                metric_percentile=Decimal("0.6245"),
            )
        ]
        card = build_pe_rebalance_card(actions=actions, report_date=date(2026, 9, 24))
        section2_table = _find_table_after_note(card, "各子类估值详情")
        row = section2_table["rows"][0]
        assert "PE-TTM 26.22" in row["val"]  # 估值格式：指标名 + 数值
        assert row["pct"] == "62.45%"  # × 100 + 2 位小数

    def test_missing_pe_ttm_shows_dash(self) -> None:
        """PE 缺失显示 —。"""
        actions = [
            _make_action(
                signal=PESignal.HOLD,
                subclass=SwensenClass.US_EQUITY,
                metric_value=None,
                metric_percentile=None,
            )
        ]
        card = build_pe_rebalance_card(actions=actions, report_date=date(2026, 9, 24))
        section2_table = _find_table_after_note(card, "各子类估值详情")
        row = section2_table["rows"][0]
        # 估值缺失时 val 单元格显示 "PE-TTM —"（指标名还在）
        assert "—" in row["val"]
        assert row["pct"] == "—"


class TestSection3Skipped:
    def test_skip_action_appears_in_section3(self) -> None:
        """SKIP 出现在 Section 3。"""
        actions = [
            _make_action(
                signal=PESignal.SKIP,
                subclass=SwensenClass.CN_REIT,
                fund_code="028277",
                fund_name="华夏中证 REITs",
                current_position=Decimal("0.50"),
                reason="REITs 用 P/NAV 估值，PE 不适用",
            )
        ]
        card = build_pe_rebalance_card(actions=actions, report_date=date(2026, 9, 24))
        section3_table = _find_table_after_note(card, "估值指标不适用")
        assert section3_table is not None
        row = section3_table["rows"][0]
        assert row["sub"] == "国内 REITs"
        assert row["fund"] == "华夏中证 REITs (028277)"
        assert row["why"] == "REITs 用 P/NAV 估值，PE 不适用"

    def test_no_skips_omits_section3(self) -> None:
        """无 SKIP 时 Section 3 不渲染（没有 note header 也没有 table）。"""
        actions = [
            _make_action(signal=PESignal.HOLD, subclass=SwensenClass.CN_EQUITY),
            _make_action(signal=PESignal.ADD, subclass=SwensenClass.US_EQUITY),
        ]
        card = build_pe_rebalance_card(actions=actions, report_date=date(2026, 9, 24))
        elements = card["elements"]

        notes = [el for el in elements if el["tag"] == "note"]
        note_texts = [n["elements"][0]["content"] for n in notes]
        # Section 1 + Section 2 各 1 个 note，无 Section 3 note
        assert any("本周操作" in t for t in note_texts)
        assert any("各子类估值详情" in t for t in note_texts)
        assert not any("估值指标不适用" in t for t in note_texts)


class TestIntegration:
    def test_full_card_with_mixed_signals(self) -> None:
        """完整的混合信号卡片（1 ADD + 1 HOLD + 1 SKIP）。"""
        actions = [
            _make_action(
                signal=PESignal.ADD,
                subclass=SwensenClass.CN_EQUITY,
                etf_index_name="中证 A50",
                fund_code="014532",
                current_position=Decimal("1.00"),
                change=Decimal("1"),
                metric_percentile=Decimal("0.1694"),
            ),
            _make_action(
                signal=PESignal.HOLD,
                subclass=SwensenClass.US_EQUITY,
                etf_index_name="标普 500",
                current_position=Decimal("0.36"),
                metric_percentile=Decimal("0.6245"),
            ),
            _make_action(
                signal=PESignal.SKIP,
                subclass=SwensenClass.CN_REIT,
                fund_code="028277",
                current_position=Decimal("0.50"),
                reason="REITs 用 P/NAV 估值，PE 不适用",
            ),
        ]
        card = build_pe_rebalance_card(actions=actions, report_date=date(2026, 9, 24))
        elements = card["elements"]

        # 验证 3 个 section 都出现
        notes = [el for el in elements if el["tag"] == "note"]
        assert len(notes) == 3
        note_texts = [n["elements"][0]["content"] for n in notes]
        assert any("本周操作" in t for t in note_texts)
        assert any("各子类估值详情" in t for t in note_texts)
        assert any("估值指标不适用" in t for t in note_texts)

        # 验证 Section 1: 1 ADD
        s1 = _find_table_after_note(card, "本周操作")
        assert len(s1["rows"]) == 1
        assert s1["rows"][0]["op"] == "加仓"

        # 验证 Section 2: 1 HOLD + 1 ADD = 2 行
        s2 = _find_table_after_note(card, "各子类估值详情")
        assert len(s2["rows"]) == 2

        # 验证 Section 3: 1 SKIP
        s3 = _find_table_after_note(card, "估值指标不适用")
        assert len(s3["rows"]) == 1
        assert s3["rows"][0]["sub"] == "国内 REITs"