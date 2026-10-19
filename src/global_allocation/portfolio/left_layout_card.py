"""左侧布局 周快照的飞书 Interactive Card 构建（liubo 2026-09-29 拍板，2026-10-19 改名）。

策略规则（liubo 2026-09-29）：
- **标的（完全主观）**：liubo 看哪只顺眼买哪只，觉得不好就卖；不**用算法信号
- **单笔规模**：每次买/卖 = 1 仓（不超买不超卖）
- **1 仓 = 5,000 CNY**（独立于大类资产配置的 1 万）
- **类别上限**：单类累计成本 ≤ 总成本 × 15%（CAP=15%；liubo 2026-10-19 拍板从 30% 降到 15%）
- **老仓位豁免**：当前已超限的类别，新买仍允许（不阻拦），只卡新买时的额外加仓
- **同基金冷却期**：30 天（同一只基金月度内不要频繁交易；liubo 2026-10-19 拍板从 7 天延长）
- **无止盈止损**：纯主观，无自动规则

卡片结构（liubo 2026-10-19 拍板，Card 2.0 schema）：
- header.title: "左侧布局组合周报"
- header.subtitle: "YYYY-MM-DD"（独立副标题字段，v2 才支持）
- header.template: purple
- header.text_tag_list: ["左侧布局"] 状态标签
- body.elements（3 段）：
  - Section 1（组合总览）：div · fields 5 个 label-value + hr
  - Section 2（类别上限状态）：chart · 柱状图（VChart spec）+ hr（liubo 2026-10-19 拍板从 table 改 chart）
  - Section 3（各基金详情）：table 9 行 + 5 列
  - footer 灰色 markdown（原 v1 footer 字段在 v2 没了，改用 body 末尾灰色注释）

Schema 升级原因：v1.0 header 没 `subtitle` 字段，要支持"主标题/副标题分开"必须升 v2.0。
publisher 走的是 `msg_type: "interactive"`，v1/v2 同样适用，不影响发送层。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from global_allocation.portfolio.left_layout import (
    COST_BASIS_BY_CODE,
    DISPLAY_NAME,
    FUND_FULL_NAME_BY_CODE,
    POSITION_UNIT_CNY,
    STRATEGY_CATEGORIES,
    TOTAL_COST_CNY,
    CURRENT_TOTAL_ASSETS_CNY,
    CURRENT_TOTAL_PNL_CNY,
    CURRENT_TOTAL_RETURN_PCT,
    CURRENT_SNAPSHOT_DATE,
    CATEGORY_CAP_PCT,
    COOLDOWN_DAYS,
    check_cooldown,
    get_category_cap_status,
    get_position,
    get_rebalance_suggestion,
    get_strategy,
    get_valuation,
)


# ─── 格式化 helpers ─────────────────────────────


def _fmt_money(cny: Decimal, with_sign: bool = False) -> str:
    """金额格式化：1,234.56 / +1,234.56 / -4,329.59。"""
    if with_sign:
        return f"{float(cny):+,.2f}"
    return f"{float(cny):,.2f}"


def _fmt_pct(pct: Decimal, with_sign: bool = True) -> str:
    """分位/收益率格式化（输入 fraction 0~1）：+12.34% / -6.52%。"""
    sign = "+" if with_sign else ""
    return f"{sign}{float(pct) * 100:.2f}%"


def _fmt_position(pos: Decimal) -> str:
    """仓位格式化：'X.XX 仓'（X.XX 单位 = 5,000 CNY）。"""
    return f"{float(pos):.2f} 仓"


# ─── Section 1: 组合总览 ─────────────────────────────


def _build_overview_section(today: date) -> list[dict[str, Any]]:
    """Section 1：组合总览（liubo 2026-10-19 拍板精简 + 加总类别数量）。

    6 个 div.fields label-value（is_short=True 两两并排，刚好 3 行）：
    - 总成本：TOTAL_COST_CNY CNY
    - 总基金数量：n_funds 只
    - 总类别数量：n_categories 类（liubo 2026-10-19 拍板加）
    - 单仓：POSITION_UNIT_CNY CNY
    - 类别上限：CATEGORY_CAP_PCT × 总成本
    - 同基金冷却期：30 天（liubo 2026-10-19 拍板从 7 天延长）

    删掉了之前的 总资产 / 总盈亏 / 总收益率 —— liubo 的 P&L / 市值在别的视图看，
    Section 1 只留「组合怎么运转」的关键参数（参考 memory project_cost_basis_is_truth.md）。
    """
    n_funds = len(COST_BASIS_BY_CODE)
    n_categories = len(STRATEGY_CATEGORIES)
    fields = [
        {"is_short": True, "text": {"tag": "lark_md",
                                    "content": f"**总成本：** {_fmt_money(TOTAL_COST_CNY)} CNY"}},
        {"is_short": True, "text": {"tag": "lark_md",
                                    "content": f"**总基金数量：** {n_funds} 只"}},
        {"is_short": True, "text": {"tag": "lark_md",
                                    "content": f"**总类别数量：** {n_categories} 类"}},
        {"is_short": True, "text": {"tag": "lark_md",
                                    "content": f"**单仓：** {float(POSITION_UNIT_CNY):,.0f} CNY"}},
        {"is_short": True, "text": {"tag": "lark_md",
                                    "content": f"**类别上限：** {float(CATEGORY_CAP_PCT) * 100:.0f}% 总成本"}},
        {"is_short": True, "text": {"tag": "lark_md",
                                    "content": f"**同基金冷却期：** {COOLDOWN_DAYS} 天"}},
    ]
    return [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": "**组合总览**"},
            "icon": {"tag": "standard_icon", "token": "report_outlined", "color": "purple"},
        },
        {"tag": "div", "fields": fields},
        {"tag": "hr"},
    ]


# ─── Section 2: 类别上限状态 ─────────────────────────────


def _build_category_rows(today: date) -> list[dict[str, Any]]:
    """类别上限行：类别 | 累计 | 上限 | 占总成本 | 状态。

    超限类别（老仓位豁免）：状态显示「⚠️ 超限（豁免）」
    pct 是实际占比（current / TOTAL_COST_CNY），不是「使用率」（liubo 2026-10-19 拍板）。
    """
    rows: list[dict[str, Any]] = []
    for category in STRATEGY_CATEGORIES:
        status = get_category_cap_status(category)
        pct_of_total = float(status["pct_of_total"]) * 100
        if status["is_over"]:
            state_text = "⚠️ 超限（豁免）"
        elif pct_of_total >= 80:
            state_text = "接近上限"
        else:
            state_text = "✓ 未超限"
        rows.append({
            "cat": category,
            "cur": f"{_fmt_money(status['current_cny'])} CNY",
            "cap": f"{_fmt_money(status['cap_cny'])} CNY",
            "pct": f"{pct_of_total:.1f}%",
            "state": state_text,
        })
    return rows


def _build_category_section(today: date) -> list[dict[str, Any]]:
    """Section 2：类别上限状态（liubo 2026-10-19 拍板改饼图 — 类别占总成本比例）。

    chart · pie（VChart spec）：
    - type=pie（环形，innerRadius=0.5）
    - categoryField=cat（类别名）
    - valueField=pct（占总成本的 %）
    - color: 超限类别红 #FF6B6B，其余蓝/绿/橙/紫（视觉上一眼看到哪类超限）
    - aspect_ratio 16:9

    老的柱状图 + markLine 删了（liubo 觉得饼图更直观；饼图无法画阈值线，
    所以用「颜色」替代 — 超限类别红色突出，其他类别中性色）。

    飞书 Feishu chart API 验证过 color 用纯数组 OK；{type:ordinal, range} 会被拒
    （schema 不认 anyOf）。
    """
    rows = _build_category_rows(today)
    chart_values = [
        {"cat": r["cat"], "pct": float(r["pct"].rstrip("%"))}
        for r in rows
    ]
    # 颜色：超限类别红，其他依次 蓝/绿/橙/紫/粉
    base_colors = ["#5B8FF9", "#5AD8A6", "#F6BD16", "#6F5EF9", "#FF99C3"]
    slice_colors = [
        "#FF6B6B" if r["state"].startswith("⚠️") else base_colors[i % len(base_colors)]
        for i, r in enumerate(rows)
    ]
    return [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": "**类别上限状态（占总成本 %）**"},
            "icon": {"tag": "standard_icon", "token": "filter_outlined", "color": "purple"},
        },
        {
            "tag": "chart",
            "chart_spec": {
                "type": "pie",
                "title": {"text": "类别占总成本比例"},
                "data": {"values": chart_values},
                "categoryField": "cat",
                "valueField": "pct",
                "outerRadius": 0.85,
                "innerRadius": 0.5,
                "color": slice_colors,
            },
            "aspect_ratio": "16:9",
        },
        {"tag": "hr"},
    ]


# ─── Section 3: 各基金详情 ─────────────────────────────


def _format_fund_line(code: str, today: date) -> str:
    """生成单只基金的展示行（飞书表 Section 3 用，liubo 2026-10-19 拍板 div+hr 布局）。

    格式：**基金全名（code）**  累计 X CNY · 仓位 X.XX · 估值 — · 调仓 XXX
    """
    cost = COST_BASIS_BY_CODE.get(code, Decimal("0"))
    pos = get_position(code)
    fund_name = f"{FUND_FULL_NAME_BY_CODE.get(code, DISPLAY_NAME.get(code, code))}（{code}）"
    val = get_valuation(code)
    sug = get_rebalance_suggestion(code, today)
    return (
        f"**{fund_name}**  "
        f"累计 {_fmt_money(cost)} CNY · "
        f"仓位 {_fmt_position(pos)} · "
        f"估值 {val} · "
        f"调仓 {sug}"
    )


def _build_fund_section(today: date) -> list[dict[str, Any]]:
    """Section 3：每类别一个 div + hr 隔开（liubo 2026-10-19 拍板，删 table）。

    结构：
    - div header「各基金仓位 + 估值 + 调仓建议」
    - 每个类别：
      - div header（带 icon + 类别名）
      - 每只基金：1 个 markdown 行（**基金全名（code）**  累计 X · 仓位 X · 估值 X · 调仓 X）
      - hr（最后一个不加）
    """
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": "**各基金仓位 + 估值 + 调仓建议**"},
            "icon": {"tag": "standard_icon", "token": "wallet_outlined", "color": "purple"},
        },
    ]

    categories = list(STRATEGY_CATEGORIES.keys())
    for i, category in enumerate(categories):
        # 类别 header（div + icon）
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{category}**"},
            "icon": {"tag": "standard_icon", "token": "tag_outlined", "color": "purple"},
        })
        # 该类别下每只基金 1 个 markdown 行
        for code in STRATEGY_CATEGORIES[category]:
            elements.append({
                "tag": "markdown",
                "content": _format_fund_line(code, today),
            })
        # hr 隔开类别（最后一个不加）
        if i < len(categories) - 1:
            elements.append({"tag": "hr"})

    return elements


# ─── 整卡构建 ─────────────────────────────


def build_left_layout_card(
    report_date: date | None = None,
) -> dict[str, Any]:
    """构造 左侧布局 周快照飞书 Interactive Card JSON（Card 2.0 schema）。

    Args:
        report_date: 报告日期（标题 + 副标题用）；None = 今天。

    Returns:
        dict[str, Any]: 飞书 Interactive Card 顶层 JSON（schema=2.0）。
    """
    if report_date is None:
        from datetime import date as _date
        report_date = _date.today()

    elements: list[dict[str, Any]] = []
    elements.extend(_build_overview_section(report_date))
    elements.extend(_build_category_section(report_date))
    elements.extend(_build_fund_section(report_date))

    # 总基金数（信息栏用）
    n_funds = len(COST_BASIS_BY_CODE)

    # Card 2.0 没 v1 的 footer 字段，用 body 末尾灰色 markdown 代替
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "markdown",
        "content": (
            "<font color='grey'>"
            f"左侧布局 — 主观选标的 + 严格仓位管理 "
            f"({n_funds} 只基金 · 1 仓 = {float(POSITION_UNIT_CNY):,.0f} CNY)"
            "</font>"
        ),
    })

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {"width_mode": "default", "enable_forward": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "左侧布局组合周报",
            },
            "subtitle": {
                "tag": "plain_text",
                "content": report_date.isoformat(),
            },
            "template": "purple",
            "text_tag_list": [
                {"tag": "text_tag", "text": {"tag": "plain_text", "content": "左侧布局"}, "color": "violet"},
            ],
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 20px 12px",
            "elements": elements,
        },
    }
    return card


def card_to_json(card: dict[str, Any]) -> str:
    """Card dict → 飞书 API 接受的 JSON 字符串。

    ensure_ascii=False 让中文正常显示。
    """
    import json
    return json.dumps(card, ensure_ascii=False)


__all__ = [
    "build_left_layout_card",
    "card_to_json",
]
