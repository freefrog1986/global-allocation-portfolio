"""ETF 轮动策略组合（独立模块，liubo 2026-09-22 建，2026-09-28 扩到 9 只，2026-09-29 减回 8 只）。

跟 Swensen 大类资产配置 + 红利策略**都不混在一起**，单独一个模块。

按策略标签分 4 类（liubo 2026-09-28 拍板）：

港股科技互联网（3 只，QDII/港股通 ETF 联接，跟踪指数基金）— 合计 53,000
- 006327 易方达中证海外互联网50ETF联接(QDII)A → 中证海外互联网 50 指数
- 014673 富国中证港股通互联网ETF发起式联接A → 中证港股通互联网指数
- 016495 景顺长城中证港股通科技ETF发起联接A → 中证港股通科技指数

港股银行（1 只，QDII）— 1,010
- 006809 泰康香港银行指数A → 港股银行指数（HSSI/HK银行）

亚太除日本（1 只，主动管理 QDII）— 4,640
- 457001 国富亚洲机会股票(QDII)A → 业绩基准 = MSCI AC Asia ex Japan

全球科技主动（3 只，QDII 主动管理主题基金）— 合计 7,770
- 016664 天弘全球高端制造混合(QDII)A → 全球高端制造主题
- 006373 国富全球科技互联混合(QDII)人民币A → 全球科技互联主题
- 017730 嘉实全球产业升级股票发起式(QDII)A → 全球产业升级主题

合计 8 只 / 66,420 CNY（2026-09-28 liubo 全部校准 + 2026-09-29 减 013127 -25000）。

成本历史：
- 457001 = 4,640（旧 module 值；liubo 2026-09-28 确认）
- 014673/016495（liubo 2026-09-28 第二轮确认）
- 2026-09-29 liubo 把 013127 转到大类资产配置（HK 港股子类）
- 其余 6 只 = 截图「资产 - 持仓收益」估算（精度 ±10 元）→ 2026-09-28 全部由 liubo 校准

P&L 口径（重要，liubo 2026-09-28 拍板）：
- **真实 P&L = 当前总资产 - 累计成本**
- 不要用券商 app「持仓收益」 — app 算的是「组合成立日之后的盈亏」，漏算
  成立前的累计买入（liubo 在建「ETF 轮动组合」分组前就已经买入了多只）。
- 2026-09-29 snapshot：总资产 62,090.41 - 累计成本 66,420 = -4,329.59 CNY (-6.52%)
  （013127 估算市值 23,370 已从原 85,460.41 中减掉）
- 见 memory/project_brokerage_pnl.md

策略规则（liubo 2026-09-29 拍板 — 主观轮动 + 严格仓位管理）：
- **标的（完全主观）**：liubo 看哪只顺眼买哪只，觉得不好就卖；**不**用算法信号
- **单笔规模**：每次买/卖 = 1 仓（不超买不超卖）
- **1 仓 = 5,000 CNY**（独立于大类资产配置的 1 万）
- **类别上限**：单类累计成本 ≤ 总成本 × 30%（CAP=30%）
- **老仓位豁免**：当前已超限的类别，新买仍允许（不阻拦），只卡新买时的额外加仓
- **同基金冷却期**：7 天（同一只基金月度内不要频繁交易）
- **无止盈止损**：纯主观，无自动规则

注意：模块名虽然叫"ETF 轮动"，但 4 只是 QDII 主动管理基金（亚太 + 全球科技 3 只）；
"轮动"指按动量/估值信号轮换持有标的；标的本身不限于纯 ETF。

后续这块有自己的成本/估值/再平衡规则就加在这里。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal


# 已知基金 + 策略标签（MVP hardcode；后续可挪到独立数据库）
# 8 只基金（2026-09-29 liubo 把 013127 转到大类资产配置），按上面 4 类分组；007 等留空间新增。
# 之前 9 只，2026-09-29 减 1。
ETF_ROTATION_BY_CODE: dict[str, str] = {
    # ── 港股科技互联网（3）──  2026-09-29 liubo 把 013127 转走
    "006327": "港股科技互联网",   # 易方达中证海外互联网50ETF联接（QDII，海外中概）
    "014673": "港股科技互联网",   # 富国中证港股通互联网ETF联接
    "016495": "港股科技互联网",   # 景顺长城中证港股通科技ETF联接
    # ── 港股银行（1）──
    "006809": "港股银行",         # 泰康香港银行指数（QDII）
    # ── 亚太除日本（1）──
    "457001": "亚太除日本",       # 国富亚洲机会（QDII，跟踪 MSCI AC Asia ex Japan）
    # ── 全球科技主动（3）──
    "016664": "全球科技主动",     # 天弘全球高端制造混合（QDII 主题）
    "006373": "全球科技主动",     # 国富全球科技互联混合（QDII 主题）
    "017730": "全球科技主动",     # 嘉实全球产业升级股票（QDII 主题）
}


# 各基金累计买入总金额（CNY）— 跟 cost_basis.py 同样的口径
# 457001 = 4640（liubo 2026-09-28 确认）；其他 7 只是 2026-09-28 截图估算
# （资产 - 持仓收益），精度 ±10 元；待 liubo 给准确成本后校准。
# 2026-09-29 liubo 把 013127 转到大类资产配置，删 1 只。
COST_BASIS_BY_CODE: dict[str, Decimal] = {
    # ── 港股科技互联网（3）──  2026-09-29 删 013127
    "006327": Decimal("10000"),     # liubo 2026-09-28 确认
    "014673": Decimal("23000"),     # liubo 2026-09-28 确认
    "016495": Decimal("20000"),     # liubo 2026-09-28 确认
    # ── 港股银行（1）──
    "006809": Decimal("1010"),      # liubo 2026-09-28 确认
    # ── 亚太除日本（1）──
    "457001": Decimal("4640"),      # liubo 2026-09-28 确认
    # ── 全球科技主动（3）──
    "016664": Decimal("2370"),      # liubo 2026-09-28 确认
    "006373": Decimal("100"),       # liubo 2026-09-28 确认
    "017730": Decimal("5300"),      # liubo 2026-09-28 确认
}


# 4 类分类常量（飞书顶部卡片 / 报告输出用）
STRATEGY_CATEGORIES: dict[str, list[str]] = {
    "港股科技互联网": ["006327", "014673", "016495"],  # 2026-09-29 删 013127
    "港股银行": ["006809"],
    "亚太除日本": ["457001"],
    "全球科技主动": ["016664", "006373", "017730"],
}


# 合计：66,420 元（8 只 ETF 轮动组合基金；2026-09-28 liubo 全部校准完成 + 2026-09-29 减 013127 -25000）
# - 港股科技互联网 3：53,000（006327=10k + 014673=23k + 016495=20k）
# - 港股银行 1：1,010
# - 亚太除日本 1：4,640
# - 全球科技主动 3：7,770（016664=2,370 + 006373=100 + 017730=5,300）
TOTAL_COST_CNY: Decimal = sum(COST_BASIS_BY_CODE.values(), Decimal("0"))


# ── 当前市值 + 真实 P&L snapshot（2026-09-28 liubo 截图 - 013127 移走）───────────
# 真实 P&L = 当前总资产 - 累计成本；不用券商 app 的「持仓收益」（口径不准）。
# 资产数据来自 liubo 2026-09-28 截图，每只基金的「资产」列加总；每日更新需手动。
# 2026-09-29 013127 转出：估算市值按 0.935 × 成本（整体 PNL 系数 -0.0652）= 23,370
#   → 新总资产 = 85460.41 - 23370 = 62,090.41
#   → 新 PNL = 62090.41 - 66420 = -4,329.59（-6.52%）
CURRENT_SNAPSHOT_DATE: date = date(2026, 9, 29)  # 2026-09-29 013127 移走
CURRENT_TOTAL_ASSETS_CNY: Decimal = Decimal("62090.41")  # 85460.41 - 013127估算市值 23370
CURRENT_TOTAL_PNL_CNY: Decimal = CURRENT_TOTAL_ASSETS_CNY - TOTAL_COST_CNY  # -4,329.59
# 收益率 = P&L / 成本；负数表示亏损
CURRENT_TOTAL_RETURN_PCT: Decimal = CURRENT_TOTAL_PNL_CNY / TOTAL_COST_CNY  # -0.0652


# 显示名（飞书卡片 / 报告输出用）
DISPLAY_NAME: dict[str, str] = {
    code: f"{label}（{code}）"
    for code, label in ETF_ROTATION_BY_CODE.items()
}


def get_strategy(fund_code: str) -> str | None:
    """查某只基金的策略标签（4 类之一）。未知返回 None。"""
    return ETF_ROTATION_BY_CODE.get(fund_code)


def get_cost_basis(fund_code: str) -> Decimal | None:
    """查某只基金的累计买入总金额（CNY）。未知返回 None。"""
    return COST_BASIS_BY_CODE.get(fund_code)


# ── 仓位管理（liubo 2026-09-29 拍板）─────────────────────────────────

# 1 仓 = 5,000 CNY（独立于大类资产配置的 1 万；不混）
POSITION_UNIT_CNY: Decimal = Decimal("5000")

# 类别上限：单类累计成本 ≤ 总成本 × 30%（老仓位豁免，只卡新买）
CATEGORY_CAP_PCT: Decimal = Decimal("0.30")

# 同基金冷却期：7 天（月度内不要频繁交易同一只）
COOLDOWN_DAYS: int = 7

# 同基金最后交易日（liubo 手动维护；初始为空 dict，每次 record_trade 后更新）
# 格式：{fund_code: date}
# liubo 2026-09-29 拍板：同基金冷却 7 天；启用前需要先填入基线日期
LAST_TRADE_BY_FUND: dict[str, date] = {}


def get_position(fund_code: str) -> Decimal:
    """查某只基金的当前仓位（仓数；小数 = cost / 5000）。

    未知基金 → 0。
    例：cost=4640 → 0.928 仓；cost=23000 → 4.6 仓。
    """
    cost = COST_BASIS_BY_CODE.get(fund_code)
    if cost is None:
        return Decimal("0")
    return cost / POSITION_UNIT_CNY


def get_category_total_cost(category: str) -> Decimal:
    """算某类别所有基金的累计成本总和（CNY）。

    未知类别 → 0。
    """
    codes = STRATEGY_CATEGORIES.get(category, [])
    return sum(
        (COST_BASIS_BY_CODE.get(c, Decimal("0")) for c in codes),
        Decimal("0"),
    )


def get_category_cap_status(category: str) -> dict[str, Decimal | bool]:
    """算某类别的上限状态。

    Returns:
        dict 含:
          - cap_cny: 上限 CNY（= 当前总成本 × 30%）
          - current_cny: 当前类别累计成本
          - remaining_cny: 还能加仓多少 CNY（负数表示已超限）
          - pct_used: 当前占上限比例（current / cap；可能 > 1）
          - is_over: 是否已超限（老仓位豁免不影响这个字段，只是提醒）
    """
    cap_cny = TOTAL_COST_CNY * CATEGORY_CAP_PCT
    current_cny = get_category_total_cost(category)
    remaining_cny = cap_cny - current_cny
    pct_used = (current_cny / cap_cny) if cap_cny > 0 else Decimal("0")
    return {
        "cap_cny": cap_cny,
        "current_cny": current_cny,
        "remaining_cny": remaining_cny,
        "pct_used": pct_used,
        "is_over": current_cny > cap_cny,
    }


def check_category_cap_for_buy(
    fund_code: str, add_cost: Decimal
) -> tuple[bool, str]:
    """检查新买入是否会让该基金所在类别超过 30% 上限。

    老仓位豁免：当前已超限时，仍然允许新买（不阻拦），但 reason 里有"已达上限，新买仍允许"提示。

    Args:
        fund_code: 基金代码。
        add_cost: 计划新增成本（CNY）。

    Returns:
        (ok: bool, reason: str)
        ok=True 表示可以买；ok=False 表示拒绝（虽然现在因为老仓位豁免，永远 ok=True）。
        reason 给出人类可读的状态。
    """
    category = get_strategy(fund_code)
    if category is None:
        return False, f"未知基金 {fund_code} 不在 ETF 轮动组合"
    status = get_category_cap_status(category)
    new_total = status["current_cny"] + add_cost
    cap_cny = status["cap_cny"]
    new_pct = (new_total / cap_cny) if cap_cny > 0 else Decimal("0")

    if status["is_over"]:
        # 老仓位超限 — 豁免；提示但不阻拦
        return True, (
            f"⚠️ {category} 已超 30% 上限（当前 {float(status['pct_used']) * 100:.1f}%，"
            f"新买后会变 {float(new_pct) * 100:.1f}%），老仓位豁免，仍允许"
        )
    if new_total > cap_cny:
        return False, (
            f"✗ {category} 类别上限 30% ({float(cap_cny):.0f} CNY)，"
            f"新买后累计 {float(new_total):.0f} CNY = {float(new_pct) * 100:.1f}%，超限"
        )
    return True, (
        f"✓ {category} 累计 {float(new_total):.0f} / {float(cap_cny):.0f} CNY "
        f"({float(new_pct) * 100:.1f}%)，未超 30% 上限"
    )


def check_cooldown(fund_code: str, today: date | None = None) -> tuple[bool, str]:
    """检查同基金冷却期（liubo 2026-09-29 拍板：7 天）。

    Args:
        fund_code: 基金代码。
        today: 评估日期；None = date.today()。

    Returns:
        (ok: bool, reason: str)
        ok=True 表示可交易；ok=False 表示冷却中。
        reason 给出人类可读的状态。
    """
    if today is None:
        today = date.today()
    last = LAST_TRADE_BY_FUND.get(fund_code)
    if last is None:
        return True, "首次交易，无冷却期记录"
    days_since = (today - last).days
    if days_since >= COOLDOWN_DAYS:
        return True, f"上次交易 {last.isoformat()}（{days_since} 天前），已过冷却期"
    remaining = COOLDOWN_DAYS - days_since
    return False, (
        f"上次交易 {last.isoformat()}（{days_since} 天前），"
        f"冷却期还剩 {remaining} 天"
    )


def record_trade(fund_code: str, trade_date: date) -> None:
    """记录某基金的最后交易日（用于冷却期检查）。

    实际 BUY/SELL 用 journal.record_buy / record_sell；这里只更新冷却期表。
    liubo 在每次手动交易后调一下，或者 botmux 帮我记。
    """
    LAST_TRADE_BY_FUND[fund_code] = trade_date


def format_weekly_snapshot_text(today: date | None = None) -> str:
    """生成每周快照的纯文本格式（用于 dry-run / 日志 / CLI 输出）。

    飞书卡片版见 etf_rotation_card.py。
    """
    if today is None:
        today = date.today()
    lines: list[str] = []
    lines.append(f"📊 ETF 轮动组合 周快照 ({today.isoformat()})")
    lines.append("")
    lines.append(f"组合总成本：{float(TOTAL_COST_CNY):,.0f} CNY")
    lines.append(f"当前总资产：{float(CURRENT_TOTAL_ASSETS_CNY):,.2f} CNY")
    lines.append(f"总盈亏：{float(CURRENT_TOTAL_PNL_CNY):+,.2f} CNY "
                 f"({float(CURRENT_TOTAL_RETURN_PCT) * 100:+.2f}%)")
    lines.append("")

    # 按类别分组
    for category in STRATEGY_CATEGORIES:
        status = get_category_cap_status(category)
        cap_pct = float(status["pct_used"]) * 100
        over_mark = " ⚠️超限（老仓位豁免）" if status["is_over"] else ""
        lines.append(
            f"**{category}**  类别累计 {float(status['current_cny']):,.0f} / "
            f"上限 {float(status['cap_cny']):,.0f} CNY "
            f"({cap_pct:.1f}%){over_mark}"
        )
        for code in STRATEGY_CATEGORIES[category]:
            cost = COST_BASIS_BY_CODE.get(code, Decimal("0"))
            pos = get_position(code)
            cooldown_ok, cooldown_reason = check_cooldown(code, today)
            cd_mark = "" if cooldown_ok else f"  [{cooldown_reason}]"
            lines.append(
                f"  · {code} {DISPLAY_NAME[code]}：{float(cost):,.0f} CNY = "
                f"{float(pos):.2f} 仓{cd_mark}"
            )
        lines.append("")

    return "\n".join(lines)


def get_total_assets_cny() -> Decimal:
    """当前总资产（CNY；最近一次 snapshot 的 9 只资产合计）。

    注：来自 2026-09-28 liubo 截图，每日更新需手动。后续如果接入行情 API
    可以改成动态算（fetch 最新净值 × 持仓份额）。
    """
    return CURRENT_TOTAL_ASSETS_CNY


def get_total_pnl_cny() -> Decimal:
    """真实总盈亏（CNY）= 当前总资产 - 累计成本。

    liubo 2026-09-28 拍板：不用券商 app 的「持仓收益」（口径不准 — app 漏算
    组合成立前的买入成本）。
    """
    return CURRENT_TOTAL_PNL_CNY


def get_total_return_pct() -> Decimal:
    """总收益率（小数形式；-0.0652 = -6.52%）。"""
    return CURRENT_TOTAL_RETURN_PCT


__all__ = [
    "ETF_ROTATION_BY_CODE",
    "COST_BASIS_BY_CODE",
    "STRATEGY_CATEGORIES",
    "TOTAL_COST_CNY",
    "CURRENT_SNAPSHOT_DATE",
    "CURRENT_TOTAL_ASSETS_CNY",
    "CURRENT_TOTAL_PNL_CNY",
    "CURRENT_TOTAL_RETURN_PCT",
    "DISPLAY_NAME",
    "POSITION_UNIT_CNY",
    "CATEGORY_CAP_PCT",
    "COOLDOWN_DAYS",
    "LAST_TRADE_BY_FUND",
    "get_strategy",
    "get_cost_basis",
    "get_position",
    "get_category_total_cost",
    "get_category_cap_status",
    "check_category_cap_for_buy",
    "check_cooldown",
    "record_trade",
    "format_weekly_snapshot_text",
    "get_total_assets_cny",
    "get_total_pnl_cny",
    "get_total_return_pct",
]