"""飞书 Interactive Card 构建。

参照 specs/080-feishu-card.md。

不直接调 API——只构造卡片 JSON。发送逻辑在 publisher.py。
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from global_allocation.models import BacktestResult
from global_allocation.strategy.models import RebalanceSuggestion

# 飞书 chart_spec 最大数据点数
_MAX_CHART_POINTS = 1000


def _downsample_series(values: list[float], max_points: int = _MAX_CHART_POINTS) -> list[float]:
    """数据点 > max_points 时均匀降采样。"""
    if len(values) <= max_points:
        return values
    step = len(values) / max_points
    return [values[int(i * step)] for i in range(max_points)]


def _downsample_index(index: pd.Index[Any], max_points: int = _MAX_CHART_POINTS) -> list[str]:
    """同步降采样日期标签。"""
    if len(index) <= max_points:
        return [pd.Timestamp(d).strftime("%Y-%m-%d") for d in index]
    step = len(index) / max_points
    return [pd.Timestamp(index[int(i * step)]).strftime("%Y-%m-%d") for i in range(max_points)]


def _format_pct(value: Decimal, decimals: int = 2) -> str:
    """Decimal 格式化成带符号的百分比字符串。"""
    pct = float(value) * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.{decimals}f}%"


def _format_decimal(value: Decimal, decimals: int = 2) -> str:
    """Decimal 格式化成浮点字符串。"""
    return f"{float(value):.{decimals}f}"


def _build_equity_chart(result: BacktestResult) -> dict[str, object]:
    """构造资金曲线 line chart。"""
    equity: pd.DataFrame = result.equity_curve
    nav_series = equity["nav"].astype(float).tolist()
    dates = _downsample_index(equity.index)
    nav_sampled = _downsample_series(nav_series)

    return {
        "type": "line",
        "title": {"text": "资金曲线"},
        "x_axis": {"type": "category", "data": dates},
        "y_axis": {"type": "value"},
        "series": [
            {
                "name": "组合 NAV",
                "type": "line",
                "data": nav_sampled,
                "smooth": False,
            }
        ],
    }


def _build_weights_pie(result: BacktestResult) -> dict[str, object]:
    """构造当前权重饼图。"""
    last_snapshot = result.snapshots[-1]
    weights = last_snapshot.weights

    pie_data = [
        {"name": sym, "value": float(weight)}
        for sym, weight in sorted(
            weights.items(), key=lambda x: -float(x[1])
        )
        if float(weight) > 0
    ]

    return {
        "type": "pie",
        "title": {"text": "当前权重"},
        "series": [
            {
                "name": "权重",
                "type": "pie",
                "data": pie_data,
                "radius": ["30%", "70%"],
            }
        ],
    }


def _build_metrics_table(result: BacktestResult) -> dict[str, object]:
    """构造关键指标表格。"""
    m = result.metrics

    rows = [
        [{"metric": "CAGR", "value": _format_pct(m.cagr)}],
        [{"metric": "夏普", "value": _format_decimal(m.sharpe)}],
        [{"metric": "最大回撤", "value": _format_pct(m.max_drawdown)}],
        [{"metric": "年化波动率", "value": _format_pct(m.volatility)}],
        [{"metric": "总收益", "value": _format_pct(m.total_return)}],
        [{"metric": "胜率", "value": _format_pct(m.win_rate)}],
        [{"metric": "最佳单日", "value": _format_pct(m.best_day)}],
        [{"metric": "最差单日", "value": _format_pct(m.worst_day)}],
        [{"metric": "再平衡次数", "value": str(len(result.rebalance_events))}],
    ]

    return {
        "columns": [
            {"name": "metric", "display_name": "指标", "width": "auto"},
            {"name": "value", "display_name": "值", "width": "auto"},
        ],
        "rows": rows,
    }


def _build_summary_text(result: BacktestResult, title: str | None) -> str:
    """构造 markdown 摘要文字。"""
    actual_title = title or result.strategy_id
    start = result.start_date.strftime("%Y-%m-%d")
    end = result.end_date.strftime("%Y-%m-%d")
    init = _format_decimal(result.initial_capital, 0)
    final = _format_decimal(result.final_value, 2)
    total = _format_pct(result.metrics.total_return)

    return (
        f"**{actual_title}**\n"
        f"**周期**：{start} → {end}  \n"
        f"**初始**：{init}  \n"
        f"**终值**：{final}  \n"
        f"**总收益**：{total}"
    )


def build_backtest_card(
    result: BacktestResult,
    title: str | None = None,
) -> dict[str, object]:
    """构造完整的飞书交互卡片 JSON。

    Args:
        result: 回测结果。
        title: 卡片标题，默认用 result.strategy_id。

    Returns:
        飞书卡片 JSON dict。
    """
    if result.equity_curve.empty or len(result.equity_curve) < 2:
        raise ValueError("equity_curve 不足 2 个数据点，无法画图")

    actual_title = title or result.strategy_id

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
                    "content": _build_summary_text(result, title),
                },
            },
            {"tag": "hr"},
            {
                "tag": "chart",
                "chart_spec": _build_equity_chart(result),
            },
            {"tag": "hr"},
            {
                "tag": "chart",
                "chart_spec": _build_weights_pie(result),
            },
            {"tag": "hr"},
            {
                "tag": "table",
                **_build_metrics_table(result),
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


# ─── 策略再平衡卡片 ──────────────────────────────────────────


def _build_rebalance_actions_table(
    suggestion: RebalanceSuggestion,
) -> dict[str, object]:
    """构造再平衡 actions 表格。"""
    columns = ["基金", "sleeve", "动作", "当前→目标", "Δ股数", "估算金额", "原因"]
    rows: list[list[str]] = []
    for a in suggestion.actions:
        action_emoji = {"buy": "🟢 买", "sell": "🔴 卖", "hold": "⚪ 持"}.get(
            a.action, a.action
        )
        weight_str = (
            f"{a.current_weight:.2%} → {a.target_weight:.2%}"
        )
        delta_str = f"{a.delta_shares:+.0f}"
        est_str = f"{a.est_value:.0f}"
        rows.append(
            [
                a.fund_code,
                a.sleeve_code,
                action_emoji,
                weight_str,
                delta_str,
                est_str,
                a.note or "",
            ]
        )
    return {
        "columns": columns,
        "rows": rows,
    }


def build_strategy_rebalance_card(
    suggestion: RebalanceSuggestion,
    title: str | None = None,
) -> dict[str, object]:
    """构造策略再平衡建议的飞书卡片。"""
    actual_title = title or f"{suggestion.strategy_id} 再平衡建议"
    total_value_str = (
        f"{float(suggestion.total_value):.2f}" if suggestion.total_value else "0.00"
    )

    summary_md = (
        f"**{actual_title}**\n"
        f"**as_of**：{suggestion.as_of.isoformat()}  \n"
        f"**总市值**：{total_value_str}  \n"
        f"**摘要**：{suggestion.summary}"
    )

    elements: list[dict[str, object]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": summary_md,
            },
        },
        {"tag": "hr"},
    ]

    if suggestion.actions:
        elements.append(
            {
                "tag": "table",
                **_build_rebalance_actions_table(suggestion),
            }
        )
    else:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "✅ **无需调整**：当前持仓在所有 band 内。",
                },
            }
        )

    elements.append({"tag": "hr"})
    elements.append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": (
                        f"Generated by gap @ "
                        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
                    ),
                }
            ],
        }
    )

    return {
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": actual_title,
            },
        },
        "elements": elements,
    }


def card_to_json(card: dict[str, object]) -> str:
    """把卡片 dict 序列化成 JSON 字符串（ensure_ascii=False 保中文）。"""
    return json.dumps(card, ensure_ascii=False)


__all__ = [
    "build_backtest_card",
    "build_strategy_rebalance_card",
    "card_to_json",
]
