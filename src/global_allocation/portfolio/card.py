"""飞书 chart card for portfolio journal。

参照 specs/090-portfolio-journal.md + specs/096-portfolio-card-redesign.md。

设计原则（spec 096）：
- 只回答用户三个问题：现在持有什么 / 大类怎么分布 / 具体买了哪几只
- 3 段元素：summary div + 大类资产柱状图 + 持仓明细表
- 不再包含 pie / line chart / 交易流水表
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from global_allocation.portfolio.breakdown import DISPLAY_NAME, compute_breakdown
from global_allocation.portfolio.journal import PortfolioJournal
from global_allocation.portfolio.models import Holding


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


def _build_breakdown_bar(journal: PortfolioJournal) -> dict[str, object]:
    """各大类资产市值柱状图（horizontal bar，按 value 倒序）。

    空类（count=0）不显示。
    """
    breakdown = compute_breakdown(journal)
    bars: list[dict[str, object]] = []
    for b in breakdown:
        if b["count"] == 0:
            continue
        bars.append(
            {
                "class": b["display_name"],
                "value": float(b["value"]),
                "weight": float(b["weight"]),
            }
        )
    # 按 value 倒序（VChart `sort: True` 也会排，但客户端排序更确定）
    bars.sort(key=lambda x: float(x["value"]), reverse=True)  # type: ignore[arg-type]

    return {
        "type": "column",
        "title": {"text": "各大类资产市值（按 Swensen 框架）"},
        "data": {"values": bars},
        "xField": "class",
        "yField": "value",
        "sort": True,
        "label": {"visible": True, "position": "top"},
        "legends": {"visible": False},
    }


def _build_holdings_table(journal: PortfolioJournal) -> dict[str, object]:
    """持仓明细表：单只基金 + 分类 + 市值 + 占比。

    按 market_value 倒序；market_value is None 的跳过。
    Feishu 表格 row 必须是 dict（按列名取）。
    """
    holdings = journal.compute_holdings()
    total_value = sum(
        (h.market_value for h in holdings if h.market_value is not None),
        Decimal("0"),
    )

    # 按 market_value 倒序（先排除 None）
    priced_pairs: list[tuple[Holding, Decimal]] = []
    for h in holdings:
        if h.market_value is not None:
            priced_pairs.append((h, h.market_value))
    priced_pairs.sort(key=lambda p: p[1], reverse=True)

    from global_allocation.portfolio.breakdown import get_subclass

    rows: list[dict[str, object]] = []
    for h, market_value in priced_pairs:
        sub = get_subclass(h.fund.code)
        class_name = DISPLAY_NAME[sub] if sub is not None else ""
        weight = (
            (market_value / total_value) if total_value > 0 else Decimal("0")
        )
        rows.append(
            {
                "code": h.fund.code,
                "name": h.fund.name,
                "class": class_name,
                "value": f"{float(market_value):,.2f}",
                "weight": f"{float(weight) * 100:.2f}%",
            }
        )

    return {
        "columns": [
            {"name": "code", "display_name": "代码", "data_type": "text", "width": "auto"},
            {"name": "name", "display_name": "基金", "data_type": "text", "width": "auto"},
            {"name": "class", "display_name": "分类", "data_type": "text", "width": "auto"},
            {"name": "value", "display_name": "市值(¥)", "data_type": "text", "width": "auto"},
            {"name": "weight", "display_name": "占比", "data_type": "text", "width": "auto"},
        ],
        "rows": rows,
    }


def build_portfolio_card(
    journal: PortfolioJournal,
    title: str | None = None,
) -> dict[str, object]:
    """构造实盘账本的飞书交互卡片。

    3 段元素：
    1. summary div（标题 + 总市值 + 盈亏 + 周涨跌）
    2. 各大类资产柱状图
    3. 持仓明细表（按市值倒序）
    """
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
                "chart_spec": _build_breakdown_bar(journal),
            },
            {"tag": "hr"},
            {
                "tag": "table",
                **_build_holdings_table(journal),
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
