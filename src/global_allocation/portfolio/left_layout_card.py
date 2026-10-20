"""左侧布局 周快照的飞书 Interactive Card 构建（liubo 2026-09-29 拍板，2026-10-19 改名）。

策略规则（liubo 2026-09-29）：
- **标的（完全主观）**：liubo 看哪只顺眼买哪只，觉得不好就卖；不**用算法信号
- **单笔规模**：每次买/卖 = 1 仓（不超买不超卖）
- **1 仓 = 5,000 CNY**（独立于大类资产配置的 1 万）
- **类别上限**：单类累计成本 ≤ 总成本 × 15%（CAP=15%；liubo 2026-10-19 拍板从 30% 降到 15%）
- **老仓位豁免**：当前已超限的类别，新买仍允许（不阻拦），只卡新买时的额外加仓
- **同基金冷却期**：30 天（同一只基金月度内不要频繁交易；liubo 2026-10-19 拍板从 7 天延长）
- **无止盈止损**：纯主观，无自动规则

卡片结构（liubo 2026-10-19 拍板 R10，Card 2.0 schema）：
- header.title: "左侧布局组合周报"
- header.subtitle: "YYYY-MM-DD"（独立副标题字段，v2 才支持）
- header.template: purple
- header.text_tag_list: ["左侧布局"] 状态标签
- body.elements（3 段）：
  - Section 1（组合总览）：div · fields 6 个 label-value + hr
  - Section 2（类别上限状态）：chart · 饼图（VChart spec）+ hr（liubo 2026-10-19 拍板从柱图改饼图）
  - Section 3（各类别基金 + 估值 + 调仓建议）：5 个类别块，每块 5 段（liubo 2026-10-19 R10）：
    1. 类别 header div：bold 类别名 + 2 个 inline text_tag（占比% + 状态）
    2. 「类下基金」 sub-md + 每只基金 md（**基金全名（code）** 含 number_tag + 累计 CNY）
    3. 「估值」 sub-md + div.fields 多列（参考指数 + 主指标，如 PE-TTM）
    4. 「调仓建议」 md：emoji + action_with_unit + 简化理由
  - footer 灰色 markdown（原 v1 footer 字段在 v2 没了，改用 body 末尾灰色注释）

R10 关键变化（liubo 2026-10-19 拍板）：
- 类下基金行：用 `<number_tag>` 显示仓数（X.XX 仓），bold 基金全名 + 累计成本
- 估值段：从 R9 的「5 指标 + YTD + ROE 季度史」巨长 markdown，简化为 div.fields 2 列
  （参考指数 + 1 个主指标，如 PE-TTM：23.10@13.14%）
- 调仓建议：简化 `⚠️ 减 1 仓  仓位超 15% 上限`，去掉冗余长理由

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
    CATEGORY_CAP_PCT,
    COOLDOWN_DAYS,
    decide_category_action,
    get_category_cap_status,
    get_category_index,
    get_index_name,
    get_index_primary_metric,
    get_position,
)


# ─── 格式化 helpers ─────────────────────────────


def _fmt_money(cny: Decimal, with_sign: bool = False) -> str:
    """金额格式化：1,234.56 / +1,234.56 / -4,329.59。"""
    if with_sign:
        return f"{float(cny):+,.2f}"
    return f"{float(cny):,.2f}"


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


def _format_fund_line(code: str) -> str:
    """生成单只基金的展示行（liubo 2026-10-19 拍板 R10/R11：text_tag 显示仓位）。

    格式：`<text_tag color='grey'>X.XX 仓</text_tag> **基金全名（code）**  累计 X CNY`

    R11 修订：R10 用 `<number_tag value='X.XX' color='grey' />` 是 OK 的（API 200），
    但 liubo 飞书客户端 renderer 不识别 number_tag，把源码字面量当文本显示出来。
    退回 R10 之前的方案：用 text_tag 包「X.XX 仓」整个串，text_tag 在 R9 验证能渲染。

    text_tag 视觉：灰底白字圆角 tag，bold 基金全名便于扫读，累计成本紧跟其后。
    """
    cost = COST_BASIS_BY_CODE.get(code, Decimal("0"))
    pos = get_position(code)
    pos_str = f"{float(pos):.2f}"
    fund_name = f"{FUND_FULL_NAME_BY_CODE.get(code, DISPLAY_NAME.get(code, code))}（{code}）"
    return (
        f"<text_tag color='grey'>{pos_str} 仓</text_tag> "
        f"**{fund_name}**  "
        f"累计 {_fmt_money(cost)} CNY"
    )


def _category_status_color(pct_of_total: float, is_over: bool) -> str:
    """类别占比 tag 颜色（飞书 card 2.0 markdown text_tag）。

    liubo 2026-10-19 拍板三档：
    - is_over 或 ≥80%×cap（12%） → red（突出）
    - ≥5%                          → orange
    - <5%                          → blue（默认）
    """
    if is_over or pct_of_total >= 12:
        return "red"
    if pct_of_total >= 5:
        return "orange"
    return "blue"


def _parse_primary_pct(primary_value: str) -> float | None:
    """从主指标 value 解析「当前值分位点%」。

    liubo 2026-10-19 R11 反馈：要在估值后给「低估 / 中性 / 高估」判断。

    格式："{value}@{pct}%"，如 "23.10@13.14%" → pct = 13.14。
    无 @分位（liubo 暂未填）→ 返回 None（卡片显示「—」）。
    """
    if "@" not in primary_value:
        return None
    pct_str = primary_value.split("@")[-1].rstrip("%").strip()
    try:
        return float(pct_str)
    except ValueError:
        return None


def _valuation_status(pct: float | None) -> tuple[str, str]:
    """估值状态（liubo 2026-10-19 拍板：<20% 低估 / 20-80% 中性 / >80% 高估）。

    Returns:
        (status_text, color) 元组：
        - ("低估", "green")  — 分位 < 20%（估值便宜，可以加仓）
        - ("中性", "blue")   — 20-80%（正常）
        - ("高估", "red")    — > 80%（估值贵，建议减仓或观望）
        - ("—", "grey")      — 无分位数据（liubo 暂未填）
    """
    if pct is None:
        return ("—", "grey")
    if pct < 20:
        return ("低估", "green")
    if pct > 80:
        return ("高估", "red")
    return ("中性", "blue")


def _build_category_fields(idx_code: str, action: dict[str, str]) -> list[dict[str, Any]]:
    """类别块底部 div.fields（liubo 2026-10-19 拍板 R12：4 列合并）。

    4 列 is_short=true（横向并排 2×2）：
    1. 参考指数：name（code）或「—」（无统一基准）
    2. 主指标：PE-TTM/PB 等 value@pct% 或「—」
    3. 估值：<text_tag 低估/中性/高估/—>（按主指标分位判断）
    4. 调仓建议：<text_tag emoji+动作>  reason

    合并 R11 的「估值 div.fields（3 列）+ 调仓建议 md（1 段）」到 1 个 div.fields，
    删掉两个 sub-md 小标题（类下基金 / 估值），更紧凑。
    """
    # 列 1-3：估值数据
    if idx_code:
        idx_name = get_index_name(idx_code)
        primary_label, primary_value = get_index_primary_metric(idx_code)
        pct = _parse_primary_pct(primary_value)
        status_text, status_color = _valuation_status(pct)
        col1 = f"**参考指数：** {idx_name}（{idx_code}）"
        col2 = f"**{primary_label}：** {primary_value}"
        col3 = f"**估值：** <text_tag color='{status_color}'>{status_text}</text_tag>"
    else:
        # 无统一基准（QDII 主动主题）
        col1 = "**参考指数：** _无统一基准_"
        col2 = "**主指标：** —"
        col3 = "**估值：** <text_tag color='grey'>—</text_tag>"

    # 列 4：调仓建议（合并自 R11 的调仓建议 md）
    action_color_map = {
        "减仓": "red",
        "持有": "orange",
        "加仓": "blue",
        "建仓": "green",
    }
    action_color = action_color_map.get(action["action"], "blue")
    col4 = (
        f"**调仓建议：** "
        f"<text_tag color='{action_color}'>"
        f"{action['emoji']} {action['action_with_unit']}"
        f"</text_tag>  "
        f"{action['reason']}"
    )

    return [
        {"is_short": True, "text": {"tag": "lark_md", "content": col1}},
        {"is_short": True, "text": {"tag": "lark_md", "content": col2}},
        {"is_short": True, "text": {"tag": "lark_md", "content": col3}},
        {"is_short": True, "text": {"tag": "lark_md", "content": col4}},
    ]


def _build_category_block(category: str, today: date, ordinal: int) -> list[dict[str, Any]]:
    """单个类别的完整块（liubo 2026-10-20 拍板 R13：序号 emoji + 文字标题，无 icon）。

    3 段：
    1. 类别 header div：`{emoji}{类别名}`（liubo R13 拍板，去掉 standard_icon）+ 2 个 inline text_tag
    2. N 个基金 markdown（直接列基金行，删 R11 的「类下基金」sub-md）
    3. div.fields 4 列合并：参考指数 / 主指标 / 估值 / 调仓建议
       （合并 R11 的「估值 sub-md + 估值 div.fields 3 列 + 调仓建议 md」三段）

    R12 简化（liubo 2026-10-19 拍板）：
    - 删 2 个 sub-md 小标题（类下基金 / 估值），让 4 个 label-value 紧凑挤进 1 个 div.fields
    - 估值 + 调仓建议合并到 1 个 div.fields（不再单独占段）

    R13 改动（liubo 2026-10-20 拍板）：
    - 类别 header 不再用 standard_icon (tag_outlined)
    - 改用「序号 emoji + 类别名」格式：`1️⃣港股科技互联网` `2️⃣港股银行` 等
    - 序号 ordinal 由 _build_fund_section 传入（1-based，从 STRATEGY_CATEGORIES 顺序）
    """
    status = get_category_cap_status(category)
    pct = float(status["pct_of_total"]) * 100
    is_over = status["is_over"]
    status_text = "超限" if is_over else "正常"
    status_color = _category_status_color(pct, is_over)

    # 1. 类别 header（div，无 icon）：序号 emoji + bold 类别名 + 2 个 inline tag
    #    liubo 2026-10-20 R13：1️⃣2️⃣4️⃣5️⃣6️⃣7️⃣8️⃣9️⃣ 圆圈数字（Keycap digit 风格）
    digit_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
    prefix = digit_emojis[ordinal - 1] if 1 <= ordinal <= 9 else f"{ordinal}."
    header_content = (
        f"{prefix}**{category}** "
        f"<text_tag color='{status_color}'>{pct:.1f}%</text_tag> "
        f"<text_tag color='{status_color}'>{status_text}</text_tag>"
    )
    block: list[dict[str, Any]] = [{
        "tag": "div",
        "text": {"tag": "lark_md", "content": header_content},
    }]

    # 2. 类下基金 markdown（无 sub-md，liubo 2026-10-19 R12 拍板删除）
    codes = STRATEGY_CATEGORIES[category]
    for code in codes:
        block.append({
            "tag": "markdown",
            "content": _format_fund_line(code),
        })

    # 3. 合并的 div.fields 4 列：参考指数 / 主指标 / 估值 / 调仓建议
    idx_code = get_category_index(category)
    action = decide_category_action(category)
    fields = _build_category_fields(idx_code, action)
    block.append({"tag": "div", "fields": fields})

    return block


def _build_fund_section(today: date) -> list[dict[str, Any]]:
    """Section 3：每类别一块（liubo 2026-10-19 拍板 R9）。

    结构：
    - section header div「各类别基金 + 估值 + 调仓建议」
    - 5 个类别块（每个块 4-5 段 + 之间用 hr 隔开）
    """
    elements: list[dict[str, Any]] = [{
        "tag": "div",
        "text": {"tag": "lark_md", "content": "**各类别基金 + 估值 + 调仓建议**"},
        "icon": {"tag": "standard_icon", "token": "wallet_outlined", "color": "purple"},
    }]

    categories = list(STRATEGY_CATEGORIES.keys())
    for i, category in enumerate(categories):
        # liubo 2026-10-20 R13：传入 ordinal 让 header 加序号 emoji
        elements.extend(_build_category_block(category, today, ordinal=i + 1))
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
