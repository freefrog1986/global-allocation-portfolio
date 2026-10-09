"""估值分位 周调仓的飞书 Interactive Card 构建（spec 022, liubo 2026-09-24）。

卡片结构（3 段）：
- header.title: "估值分位 周调仓 (YYYY-MM-DD)"
- Section 1（本周操作 — 仅 ADD/REDUCE/BUILD）：
  - note header "本周操作"
  - table: 操作表（操作 | ETF / 指数 | 基金 | 仓位变化 | 触发原因）
  - hr
- Section 2（各子类估值详情 — 含 HOLD）：
  - note header "各子类估值详情"
  - table: 详情表（子类 | ETF / 指数 | 估值 | 10Y 分位 | 当前仓位 | 操作）
  - 「估值」列显示指标名（PE-TTM / P/FFO / P/NAV）+ 数值
  - hr
- Section 3（跳过的基金 — 估值指标不适用或数据缺失）：
  - note header "估值指标不适用 / 数据缺失"
  - table: 跳过表（子类 | 基金 | 当前仓位 | 原因）

估值指标选择（liubo 2026-09-24 拍板）：
- A 股 / 美股 / 港股 / 国外发达 / 新兴市场 → PE-TTM
- 美国 REITs（160140）→ P/FFO（MSCI/NAREIT 标准；REITs 用 PE 会被折旧扭曲）
- 中证 REITs（028277）→ P/NAV（招商/中金/华泰国内券商惯例）
- 商品（黄金 000216）→ 无估值指标，SKIP

liubo 2026-09-24 拍板：「周报不是飞书卡片吗，你好好设计」— 必须用 Interactive Card，不是纯文本。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from global_allocation.portfolio.breakdown import SwensenClass
from global_allocation.portfolio.pe_rebalance import (
    PESignal,
    RebalanceAction,
    ValuationMetric,
    weekly_rebalance_plan,
)


# ─── 操作信号的飞书文本映射 ─────────────────────────────

SIGNAL_DISPLAY: dict[PESignal, str] = {
    PESignal.ADD: "加仓",
    PESignal.REDUCE: "减仓",
    PESignal.BUILD: "建仓",
    PESignal.HOLD: "不动",
    PESignal.SKIP: "跳过",
}


# ─── 子类枚举的中文显示名（spec 022 — liubo 2026-09-24 拍板）───

SUBCLASS_DISPLAY: dict[SwensenClass, str] = {
    SwensenClass.CN_EQUITY: "A 股",
    SwensenClass.HK_EQUITY: "港股",
    SwensenClass.US_EQUITY: "美股",
    SwensenClass.FOREIGN_DM_EQUITY: "国外发达",
    SwensenClass.EM_EQUITY: "新兴市场",
    SwensenClass.CN_REIT: "国内 REITs",
    SwensenClass.US_REIT: "美国 REITs",
    SwensenClass.CN_GOV_BOND: "国内利率债",
    SwensenClass.US_BOND: "美债",
    SwensenClass.COMMODITY: "商品",
    SwensenClass.CASH: "现金",
}


# ─── 估值指标的中文显示（PE-TTM / P/FFO / P/NAV / 黄金 2 指标）─────────────

METRIC_DISPLAY: dict[ValuationMetric, str] = {
    ValuationMetric.PE_TTM: "PE-TTM",
    ValuationMetric.P_FFO: "P/FFO",
    ValuationMetric.P_NAV: "P/NAV",
    ValuationMetric.PS_TTM: "PS-TTM",                    # liubo 2026-10-09 科创/创业类改 PS
    # spec 099 黄金 2 指标
    ValuationMetric.GOLD_HISTORICAL_PCT: "金价",         # value=SGE Au99.99 CNY/g
    ValuationMetric.GOLD_REAL_YIELD: "实际利率",          # value=FRED DFII10（fraction）
}


def _fmt_position(pos: Decimal) -> str:
    """仓位格式化：'X.XX 仓'（X.XX 单位 = 1 万 CNY）。"""
    return f"{float(pos):.2f} 仓"


def _fmt_pct(pct: Decimal | None) -> str:
    """分位格式化：'XX.XX%'（输入是 fraction 0~1）。"""
    if pct is None:
        return "—"
    return f"{(pct * 100).quantize(Decimal('0.01'))}%"


def _fmt_metric_value(metric: ValuationMetric, value: Decimal | None) -> str:
    """估值倍数 / 价格格式化：
    - PE / P-FFO: 'XX.XX'
    - P-NAV: 'XX.XXx'
    - 金价: 'XXX.XX CNY/g'
    - 实际利率: 'X.XX%'（fraction → percent）
    """
    if value is None:
        return "—"
    if metric == ValuationMetric.GOLD_HISTORICAL_PCT:
        # SGE Au99.99 当前价（CNY/g）
        quantized = value.quantize(Decimal("0.00"))
        return f"{quantized} CNY/g"
    if metric == ValuationMetric.GOLD_REAL_YIELD:
        # FRED DFII10（fraction: 0.0291 = 2.91%）
        pct = (value * Decimal("100")).quantize(Decimal("0.00"))
        return f"{pct}%"
    quantized = value.quantize(Decimal("0.00"))
    if metric == ValuationMetric.P_NAV:
        return f"{quantized}x"
    return str(quantized)


def _fmt_metric_cell(metric: ValuationMetric, value: Decimal | None) -> str:
    """详情表「估值」列：'PE-TTM 15.89' / 'P/FFO 19.70' / 'P/NAV 1.03x' / '金价 907.50 CNY/g'。"""
    name = METRIC_DISPLAY[metric]
    val_str = _fmt_metric_value(metric, value)
    return f"{name} {val_str}"


# ─── Section 1: 本周操作表 ─────────────────────────────


def _build_operation_rows(actions: list[RebalanceAction]) -> list[dict[str, Any]]:
    """本周操作行（仅 ADD/REDUCE/BUILD）。

    列：操作 | ETF / 指数 | 基金 | 仓位变化 | 触发原因
    注意：row keys 用 ASCII（飞书 API 要求），中文走 display_name。
    """
    rows: list[dict[str, Any]] = []
    for a in actions:
        if a.signal not in (PESignal.ADD, PESignal.REDUCE, PESignal.BUILD):
            continue
        sign = "+" if a.change > 0 else ""
        change_text = f"{sign}{float(a.change):.0f} 仓 (≈{sign}{float(a.change) * 1:.0f} 万 CNY)"
        rows.append({
            "op": SIGNAL_DISPLAY[a.signal],
            "etf": a.etf_index_name,
            "fund": f"{a.fund_name} ({a.fund_code})",
            "change": change_text,
            "reason": a.reason,
        })
    return rows


_OPERATION_COLUMNS = [
    {"name": "op", "display_name": "操作", "data_type": "text", "width": "auto"},
    {"name": "etf", "display_name": "ETF / 指数", "data_type": "text", "width": "auto"},
    {"name": "fund", "display_name": "基金", "data_type": "text", "width": "auto"},
    {"name": "change", "display_name": "仓位变化", "data_type": "text", "width": "auto"},
    {"name": "reason", "display_name": "触发原因", "data_type": "text", "width": "auto"},
]


def _build_operation_section(actions: list[RebalanceAction]) -> list[dict[str, Any]]:
    """Section 1：本周操作（ADD/REDUCE/BUILD）。

    无操作时显示「本周无操作」div，不显示空 table。
    """
    rows = _build_operation_rows(actions)
    elements: list[dict[str, Any]] = [
        {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": "本周操作"}],
        }
    ]
    if not rows:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "**本周无操作** — 所有触发估值的基金都在 20-80% 区间内，或仓位已满。",
            },
        })
    else:
        elements.append({
            "tag": "table",
            "columns": _OPERATION_COLUMNS,
            "rows": rows,
        })
    elements.append({"tag": "hr"})
    return elements


# ─── Section 2: 各子类估值详情表 ─────────────────────────────


def _build_detail_rows(actions: list[RebalanceAction]) -> list[dict[str, Any]]:
    """详情行（估值已评估的基金，含 HOLD；不含 SKIP）。

    列：子类 | ETF / 指数 | 估值 | 10Y 分位 | 当前仓位 | 操作
    「估值」列格式："PE-TTM 15.89" / "P/FFO 19.70" / "P/NAV 1.03x"。
    注意：row keys 用 ASCII（飞书 API 要求），中文走 display_name。
    """
    rows: list[dict[str, Any]] = []
    for a in actions:
        if a.signal == PESignal.SKIP:
            continue  # 跳过的进 Section 3
        rows.append({
            "sub": SUBCLASS_DISPLAY.get(a.subclass, a.subclass.value),
            "etf": a.etf_index_name,
            "val": _fmt_metric_cell(a.metric, a.metric_value),
            "pct": _fmt_pct(a.metric_percentile),
            "pos": _fmt_position(a.current_position),
            "act": SIGNAL_DISPLAY[a.signal],
        })
    return rows


_DETAIL_COLUMNS = [
    {"name": "sub", "display_name": "子类", "data_type": "text", "width": "auto"},
    {"name": "etf", "display_name": "ETF / 指数", "data_type": "text", "width": "auto"},
    {"name": "val", "display_name": "估值", "data_type": "text", "width": "auto"},
    # spec 099：黄金 5Y 分位 + 股票 10Y 分位统一显示
    {"name": "pct", "display_name": "分位", "data_type": "text", "width": "auto"},
    {"name": "pos", "display_name": "当前仓位", "data_type": "text", "width": "auto"},
    {"name": "act", "display_name": "操作", "data_type": "text", "width": "auto"},
]


def _build_detail_section(actions: list[RebalanceAction]) -> list[dict[str, Any]]:
    """Section 2：各子类估值详情（HOLD + ADD/REDUCE/BUILD 都在这里）。"""
    rows = _build_detail_rows(actions)
    return [
        {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": "各子类估值详情"}],
        },
        {
            "tag": "table",
            "columns": _DETAIL_COLUMNS,
            "rows": rows,
        },
        {"tag": "hr"},
    ]


# ─── Section 3: 跳过的基金 ─────────────────────────────


def _build_skipped_rows(actions: list[RebalanceAction]) -> list[dict[str, Any]]:
    """跳过的行（SKIP — 商品/无估值指标的基金）。

    列：子类 | 基金 | 当前仓位 | 原因
    注意：row keys 用 ASCII（飞书 API 要求），中文走 display_name。
    """
    rows: list[dict[str, Any]] = []
    for a in actions:
        if a.signal != PESignal.SKIP:
            continue
        rows.append({
            "sub": SUBCLASS_DISPLAY.get(a.subclass, a.subclass.value),
            "fund": f"{a.fund_name} ({a.fund_code})",
            "pos": _fmt_position(a.current_position),
            "why": a.reason,
        })
    return rows


_SKIPPED_COLUMNS = [
    {"name": "sub", "display_name": "子类", "data_type": "text", "width": "auto"},
    {"name": "fund", "display_name": "基金", "data_type": "text", "width": "auto"},
    {"name": "pos", "display_name": "当前仓位", "data_type": "text", "width": "auto"},
    {"name": "why", "display_name": "原因", "data_type": "text", "width": "auto"},
]


def _build_skipped_section(actions: list[RebalanceAction]) -> list[dict[str, Any]] | None:
    """Section 3：跳过的基金（估值指标不适用 / 数据缺失）。

    无跳过项时返回 None（不渲染 section header）。
    """
    rows = _build_skipped_rows(actions)
    if not rows:
        return None
    return [
        {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": "估值指标不适用 / 数据缺失"}],
        },
        {
            "tag": "table",
            "columns": _SKIPPED_COLUMNS,
            "rows": rows,
        },
    ]


# ─── 整卡构建 ─────────────────────────────


def build_pe_rebalance_card(
    actions: list[RebalanceAction] | None = None,
    report_date: date | None = None,
) -> dict[str, Any]:
    """构造估值分位 周调仓飞书 Interactive Card JSON。

    Args:
        actions: 调仓 action 列表；None = 调用 weekly_rebalance_plan()。
        report_date: 报告日期（标题用）；None = 今天。

    Returns:
        dict[str, Any]: 飞书 Interactive Card 顶层 JSON。
    """
    if actions is None:
        actions = weekly_rebalance_plan()
    if report_date is None:
        from datetime import date as _date
        report_date = _date.today()

    title = f"估值分位 周调仓 ({report_date.isoformat()})"

    elements: list[dict[str, Any]] = []
    elements.extend(_build_operation_section(actions))
    elements.extend(_build_detail_section(actions))

    skipped_section = _build_skipped_section(actions)
    if skipped_section is not None:
        elements.extend(skipped_section)

    card: dict[str, Any] = {
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": title,
            },
        },
        "elements": elements,
        "footer": {
            "tag": "note",
            "elements": [
                {"tag": "plain_text", "content": "spec 022 — 每周五自动评估估值分位（A 股/美股 PE-TTM / US REIT P/FFO / 中证 REITs P/NAV）"}
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
    "build_pe_rebalance_card",
    "card_to_json",
]