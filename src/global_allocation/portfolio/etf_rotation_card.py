"""ETF 轮动组合 周快照的飞书 Interactive Card 构建（liubo 2026-09-29 拍板）。

策略规则（liubo 2026-09-29）：
- **标的（完全主观）**：liubo 看哪只顺眼买哪只，觉得不好就卖；不**用算法信号
- **单笔规模**：每次买/卖 = 1 仓（不超买不超卖）
- **1 仓 = 5,000 CNY**（独立于大类资产配置的 1 万）
- **类别上限**：单类累计成本 ≤ 总成本 × 30%（CAP=30%）
- **老仓位豁免**：当前已超限的类别，新买仍允许（不阻拦），只卡新买时的额外加仓
- **同基金冷却期**：7 天（同一只基金月度内不要频繁交易）
- **无止盈止损**：纯主观，无自动规则

卡片结构（3 段）：
- header.title: "ETF 轮动组合 周快照 (YYYY-MM-DD)"
- Section 1（组合总览）：
  - note header "组合总览"
  - div: 总成本 / 总资产 / 总盈亏 / 总收益率
  - hr
- Section 2（类别上限状态）：
  - note header "类别上限状态"
  - table: 类别 | 累计成本 | 上限 | 使用率 | 状态
  - 超限类别会有 ⚠️ 标记（老仓位豁免）
  - hr
- Section 3（各基金详情）：
  - note header "各基金仓位 + 冷却期"
  - table: 基金 | 类别 | 累计成本 | 仓数 | 冷却期
  - 冷却中基金会显示"剩 X 天"
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from global_allocation.portfolio.etf_rotation import (
    COST_BASIS_BY_CODE,
    DISPLAY_NAME,
    POSITION_UNIT_CNY,
    STRATEGY_CATEGORIES,
    TOTAL_COST_CNY,
    CURRENT_TOTAL_ASSETS_CNY,
    CURRENT_TOTAL_PNL_CNY,
    CURRENT_TOTAL_RETURN_PCT,
    CURRENT_SNAPSHOT_DATE,
    CATEGORY_CAP_PCT,
    check_cooldown,
    get_category_cap_status,
    get_position,
    get_strategy,
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
    """Section 1：组合总览（总成本 / 总资产 / P&L / 收益率）。

    P&L = 当前总资产 - 累计成本；不用券商 app 的「持仓收益」（口径不准）。
    snapshot 日期 vs 报告日期：snapshot 是最近一次手动数据采集日，报告日期是周报生成日。
    """
    elements: list[dict[str, Any]] = [
        {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": "组合总览"}],
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f"**总成本：** {_fmt_money(TOTAL_COST_CNY)} CNY\n"
                    f"**当前总资产：** {_fmt_money(CURRENT_TOTAL_ASSETS_CNY)} CNY "
                    f"(snapshot {CURRENT_SNAPSHOT_DATE.isoformat()})\n"
                    f"**总盈亏：** {_fmt_money(CURRENT_TOTAL_PNL_CNY, with_sign=True)} CNY "
                    f"({_fmt_pct(CURRENT_TOTAL_RETURN_PCT)})\n"
                    f"**单仓：** {float(POSITION_UNIT_CNY):,.0f} CNY  |  "
                    f"**类别上限：** {float(CATEGORY_CAP_PCT) * 100:.0f}% 总成本  |  "
                    f"**同基金冷却期：** 7 天"
                ),
            },
        },
        {"tag": "hr"},
    ]
    return elements


# ─── Section 2: 类别上限状态 ─────────────────────────────


def _build_category_rows(today: date) -> list[dict[str, Any]]:
    """类别上限行：类别 | 累计 | 上限 | 使用率 | 状态。

    超限类别（老仓位豁免）：状态显示「⚠️ 超限（豁免）」
    """
    rows: list[dict[str, Any]] = []
    for category in STRATEGY_CATEGORIES:
        status = get_category_cap_status(category)
        cap_pct = float(status["pct_used"]) * 100
        if status["is_over"]:
            state_text = "⚠️ 超限（豁免）"
        elif cap_pct >= 80:
            state_text = "接近上限"
        else:
            state_text = "✓ 未超限"
        rows.append({
            "cat": category,
            "cur": f"{_fmt_money(status['current_cny'])} CNY",
            "cap": f"{_fmt_money(status['cap_cny'])} CNY",
            "pct": f"{cap_pct:.1f}%",
            "state": state_text,
        })
    return rows


_CATEGORY_COLUMNS = [
    {"name": "cat", "display_name": "类别", "data_type": "text", "width": "auto"},
    {"name": "cur", "display_name": "累计成本", "data_type": "text", "width": "auto"},
    {"name": "cap", "display_name": "上限 (30%)", "data_type": "text", "width": "auto"},
    {"name": "pct", "display_name": "使用率", "data_type": "text", "width": "auto"},
    {"name": "state", "display_name": "状态", "data_type": "text", "width": "auto"},
]


def _build_category_section(today: date) -> list[dict[str, Any]]:
    """Section 2：类别上限状态表。"""
    rows = _build_category_rows(today)
    return [
        {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": "类别上限状态"}],
        },
        {
            "tag": "table",
            "columns": _CATEGORY_COLUMNS,
            "rows": rows,
        },
        {"tag": "hr"},
    ]


# ─── Section 3: 各基金详情 ─────────────────────────────


def _build_fund_rows(today: date) -> list[dict[str, Any]]:
    """基金详情行：基金 | 类别 | 累计成本 | 仓数 | 冷却期。

    按 STRATEGY_CATEGORIES 顺序遍历（港股科技互联网 → 港股银行 → 亚太除日本 → 全球科技主动）。
    冷却中基金会在「冷却期」列显示「剩 X 天」。
    """
    rows: list[dict[str, Any]] = []
    for category in STRATEGY_CATEGORIES:
        for code in STRATEGY_CATEGORIES[category]:
            cost = COST_BASIS_BY_CODE.get(code, Decimal("0"))
            pos = get_position(code)
            cooldown_ok, cooldown_reason = check_cooldown(code, today)
            if cooldown_ok:
                cooldown_text = "✓ 可交易"
            else:
                # 提取剩余天数（"冷却期还剩 X 天"）
                cooldown_text = cooldown_reason.split("，")[-1] if "，" in cooldown_reason else cooldown_reason
            rows.append({
                "fund": DISPLAY_NAME.get(code, code),
                "cat": category,
                "cost": f"{_fmt_money(cost)} CNY",
                "pos": _fmt_position(pos),
                "cd": cooldown_text,
            })
    return rows


_FUND_COLUMNS = [
    {"name": "fund", "display_name": "基金", "data_type": "text", "width": "auto"},
    {"name": "cat", "display_name": "类别", "data_type": "text", "width": "auto"},
    {"name": "cost", "display_name": "累计成本", "data_type": "text", "width": "auto"},
    {"name": "pos", "display_name": "仓位", "data_type": "text", "width": "auto"},
    {"name": "cd", "display_name": "冷却期", "data_type": "text", "width": "auto"},
]


def _build_fund_section(today: date) -> list[dict[str, Any]]:
    """Section 3：各基金仓位 + 冷却期。"""
    rows = _build_fund_rows(today)
    return [
        {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": "各基金仓位 + 冷却期"}],
        },
        {
            "tag": "table",
            "columns": _FUND_COLUMNS,
            "rows": rows,
        },
    ]


# ─── 整卡构建 ─────────────────────────────


def build_etf_rotation_card(
    report_date: date | None = None,
) -> dict[str, Any]:
    """构造 ETF 轮动组合 周快照飞书 Interactive Card JSON。

    Args:
        report_date: 报告日期（标题用）；None = 今天。

    Returns:
        dict[str, Any]: 飞书 Interactive Card 顶层 JSON。
    """
    if report_date is None:
        from datetime import date as _date
        report_date = _date.today()

    title = f"ETF 轮动组合 周快照 ({report_date.isoformat()})"

    elements: list[dict[str, Any]] = []
    elements.extend(_build_overview_section(report_date))
    elements.extend(_build_category_section(report_date))
    elements.extend(_build_fund_section(report_date))

    # 总基金数（信息栏用）
    n_funds = len(COST_BASIS_BY_CODE)

    card: dict[str, Any] = {
        "header": {
            "template": "purple",
            "title": {
                "tag": "plain_text",
                "content": title,
            },
        },
        "elements": elements,
        "footer": {
            "tag": "note",
            "elements": [
                {"tag": "plain_text", "content": (
                    f"ETF 轮动组合 — 主观选标的 + 严格仓位管理 "
                    f"({n_funds} 只基金 · 1 仓 = {float(POSITION_UNIT_CNY):,.0f} CNY)"
                )}
            ],
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
    "build_etf_rotation_card",
    "card_to_json",
]
