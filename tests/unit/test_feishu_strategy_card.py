"""测试 src/global_allocation/feishu/card.py 的策略再平衡卡片构建。"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from global_allocation.feishu.card import (
    build_strategy_rebalance_card,
    card_to_json,
)
from global_allocation.strategy.models import RebalanceAction, RebalanceSuggestion


def _suggestion(actions: list[RebalanceAction]) -> RebalanceSuggestion:
    return RebalanceSuggestion(
        strategy_id="a-share-dividend",
        version=1,
        as_of=date(2026, 9, 18),
        total_value=Decimal("1000.00"),
        actions=actions,
        summary="2 只基金需要调整",
    )


def _buy_action(fund: str = "fund_0") -> RebalanceAction:
    return RebalanceAction(
        fund_code=fund,
        sleeve_code="sleeve_0",
        action="buy",
        current_weight=Decimal("0.30"),
        target_weight=Decimal("0.50"),
        drift=Decimal("-0.20"),
        current_shares=Decimal("300"),
        target_shares=Decimal("500"),
        delta_shares=Decimal("200"),
        est_value=Decimal("200"),
        note="超出 band [38.00%, 59.40%]",
    )


def _sell_action(fund: str = "fund_1") -> RebalanceAction:
    return RebalanceAction(
        fund_code=fund,
        sleeve_code="sleeve_1",
        action="sell",
        current_weight=Decimal("0.70"),
        target_weight=Decimal("0.50"),
        drift=Decimal("0.20"),
        current_shares=Decimal("700"),
        target_shares=Decimal("500"),
        delta_shares=Decimal("-200"),
        est_value=Decimal("200"),
        note="超出 band [38.00%, 59.40%]",
    )


class TestBuildStrategyRebalanceCard:
    def test_returns_dict(self) -> None:
        card = build_strategy_rebalance_card(_suggestion([]))
        assert isinstance(card, dict)
        assert "header" in card
        assert "elements" in card

    def test_header_has_title(self) -> None:
        card = build_strategy_rebalance_card(_suggestion([]))
        title = card["header"]["title"]  # type: ignore[index]
        assert title["content"] == "a-share-dividend 再平衡建议"

    def test_custom_title(self) -> None:
        card = build_strategy_rebalance_card(_suggestion([]), title="我的策略")
        title = card["header"]["title"]  # type: ignore[index]
        assert title["content"] == "我的策略"

    def test_empty_actions_shows_no_adjustment_message(self) -> None:
        card = build_strategy_rebalance_card(_suggestion([]))
        # 找一个 div 包含 "无需调整"
        elements = card["elements"]  # type: ignore[index]
        found = False
        for e in elements:
            if e.get("tag") == "div":  # type: ignore[union-attr]
                content = e["text"]["content"]  # type: ignore[index]
                if "无需调整" in content:
                    found = True
                    break
        assert found, "Empty actions should show '无需调整' message"

    def test_actions_become_table(self) -> None:
        card = build_strategy_rebalance_card(
            _suggestion([_buy_action(), _sell_action()])
        )
        elements = card["elements"]  # type: ignore[index]
        tables = [e for e in elements if e.get("tag") == "table"]
        assert len(tables) == 1
        table = tables[0]
        columns = table["columns"]
        assert "基金" in columns
        assert "动作" in columns
        rows = table["rows"]
        assert len(rows) == 2
        # buy action 应该有 🟢
        buy_row = next(r for r in rows if "fund_0" in r[0])
        assert "买" in buy_row[2]
        # sell action 应该有 🔴
        sell_row = next(r for r in rows if "fund_1" in r[0])
        assert "卖" in sell_row[2]

    def test_summary_in_md(self) -> None:
        card = build_strategy_rebalance_card(_suggestion([_buy_action()]))
        # 第一个 element 应该是 div 含 summary
        first = card["elements"][0]  # type: ignore[index]
        assert first["tag"] == "div"
        text = first["text"]["content"]
        assert "a-share-dividend" in text
        assert "2026-09-18" in text
        assert "2 只基金需要调整" in text or "1000" in text

    def test_card_to_json_serializable(self) -> None:
        card = build_strategy_rebalance_card(
            _suggestion([_buy_action(), _sell_action()])
        )
        s = card_to_json(card)
        # 应该能反序列化
        parsed = json.loads(s)
        assert parsed["header"]["title"]["content"] == "a-share-dividend 再平衡建议"

    def test_ascii_chinese_preserved(self) -> None:
        """ensure_ascii=False 让中文不被 \\u 转义。"""
        card = build_strategy_rebalance_card(_suggestion([]), title="A 股红利")
        s = card_to_json(card)
        assert "A 股红利" in s  # 中文直出，不是 \u 转义
