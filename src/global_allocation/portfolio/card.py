"""飞书 chart card for portfolio journal。

参照 specs/090-portfolio-journal.md。
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from global_allocation.portfolio.journal import PortfolioJournal

_MAX_CHART_POINTS = 1000


def _downsample(values: list[float], max_points: int = _MAX_CHART_POINTS) -> list[float]:
    if len(values) <= max_points:
        return values
    step = len(values) / max_points
    return [values[int(i * step)] for i in range(max_points)]


def _format_pct(value: Decimal | None, decimals: int = 2) -> str:
    if value is None:
        return "n/a"
    pct = float(value) * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.{decimals}f}%"


def _format_money(value: Decimal, decimals: int = 2) -> str:
    return f"{float(value):,.{decimals}f}"


def _build_summary(journal: PortfolioJournal, title: str) -> str:
    holdings = journal.compute_holdings()
    if not holdings:
        raise ValueError("没有持仓，无法生成卡片")

    total = sum(
        (h.market_value for h in holdings if h.market_value is not None),
        Decimal("0"),
    )
    total_cost = sum((h.cost_basis for h in holdings), Decimal("0"))
    total_pnl = total - total_cost
    total_pnl_pct = (total_pnl / total_cost) if total_cost > 0 else Decimal("0")

    latest = journal.get_latest_snapshot()
    week_str = "n/a"
    cumulative_str = "n/a"
    if latest is not None:
        week_str = _format_pct(latest.week_return)
        cumulative_str = _format_pct(latest.cumulative_return)

    return (
        f"**{title}**\n"
        f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}  \n"
        f"**总市值**：{_format_money(total)} CNY  \n"
        f"**总成本**：{_format_money(total_cost)} CNY  \n"
        f"**浮动盈亏**：{_format_money(total_pnl)} ({_format_pct(total_pnl_pct)})  \n"
        f"**上周涨跌**：{week_str}  \n"
        f"**累计涨跌**：{cumulative_str}"
    )


def _build_pie(journal: PortfolioJournal) -> dict[str, object]:
    holdings = journal.compute_holdings()
    values: list[dict[str, Any]] = []
    for h in holdings:
        if h.market_value is None or h.market_value == 0:
            continue
        values.append(
            {
                "type": f"{h.fund.code} {h.fund.name}",
                "value": float(h.market_value),
            }
        )
    return {
        "type": "pie",
        "title": {"text": "当前持仓"},
        "data": {"values": values},
        "valueField": "value",
        "categoryField": "type",
        "outerRadius": 0.85,
        "innerRadius": 0.4,
        "legends": {"visible": True, "orient": "right"},
        "label": {"visible": True},
    }


def _build_history_line(journal: PortfolioJournal) -> dict[str, object]:
    """历史 NAV 曲线（所有 snapshots + 当前）。"""
    snaps = journal.list_snapshots()  # 已按 week_end_date ASC 排
    holdings = journal.compute_holdings()
    current_total = sum(
        (h.market_value for h in holdings if h.market_value is not None),
        Decimal("0"),
    )

    values: list[dict[str, Any]] = []
    for s in snaps:
        values.append(
            {"date": s.week_end_date.isoformat(), "nav": float(s.total_value)}
        )
    if current_total > 0:
        values.append({"date": datetime.now().date().isoformat(), "nav": float(current_total)})

    return {
        "type": "line",
        "title": {"text": "组合净值"},
        "data": {"values": values},
        "xField": "date",
        "yField": "nav",
        "smooth": False,
        "point": {"visible": True},
        "legends": {"visible": False},
    }


def _build_recent_transactions(journal: PortfolioJournal) -> dict[str, object]:
    """最近 10 笔交易。"""
    txs = journal.list_transactions()[:10]
    rows: list[dict[str, Any]] = []
    for tx in txs:
        is_buy = tx.side.value == "buy"
        rows.append(
            {
                "date": tx.date.isoformat(),
                "side": [{"text": "买" if is_buy else "卖", "color": "green" if is_buy else "red"}],
                "fund": tx.fund_code,
                "shares": float(tx.shares),
                "price": float(tx.price),
                "strategy": tx.strategy or "-",
            }
        )
    return {
        "columns": [
            {"name": "date", "display_name": "日期", "data_type": "text", "width": "auto"},
            {"name": "side", "display_name": "方向", "data_type": "options", "width": "auto"},
            {"name": "fund", "display_name": "基金", "data_type": "text", "width": "auto"},
            {"name": "shares", "display_name": "份额", "data_type": "number", "width": "auto"},
            {"name": "price", "display_name": "价格", "data_type": "number", "width": "auto"},
            {"name": "strategy", "display_name": "策略", "data_type": "text", "width": "auto"},
        ],
        "rows": rows,
    }


def build_portfolio_card(
    journal: PortfolioJournal,
    title: str | None = None,
) -> dict[str, object]:
    """构造实盘账本的飞书交互卡片。"""
    actual_title = title or "实盘持仓"
    holdings = journal.compute_holdings()
    if not holdings:
        raise ValueError("没有持仓，无法生成卡片")

    card: dict[str, object] = {
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": actual_title,
            },
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": _build_summary(journal, actual_title),
                },
            },
            {"tag": "hr"},
            {
                "tag": "chart",
                "chart_spec": _build_pie(journal),
            },
            {"tag": "hr"},
            {
                "tag": "chart",
                "chart_spec": _build_history_line(journal),
            },
            {"tag": "hr"},
            {
                "tag": "table",
                **_build_recent_transactions(journal),
            },
        ],
        "footer": {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": f"Generated by gap @ {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                }
            ],
        },
    }
    return card


def card_to_json(card: dict[str, object]) -> str:
    """序列化（ensure_ascii=False 保中文）。"""
    return json.dumps(card, ensure_ascii=False)


__all__ = ["build_portfolio_card", "card_to_json"]
