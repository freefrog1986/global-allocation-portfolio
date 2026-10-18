"""飞书 chart card for portfolio journal。

参照 specs/090-portfolio-journal.md + specs/096-portfolio-card-redesign.md
+ specs/097-strategy-spike.md + specs/098-valuation-section.md。

设计原则（spec 097 第二十一轮 — 整数仓位模型）：
- 整数仓位 = 每个子类持 N 仓（1 仓 = 1 万 CNY），不再用 Layer 1/2/3 权重小数
- 持仓表（Section 1）的 target/delta 列改用整数份数："{N} 份 ({X.X}%)"
- 不再有独立的"大类资产策略" Section 2（持仓表的 target/delta 已包含超类信息，
  Section 2 冗余；第二十一轮 liubo 拍板删除）
- Section 3 仍是大类资产估值（A 股 / 港股 / 美股 combined 模式）

第二十一轮（spec 098）：Section 3 — 大类资产估值（A 股 + 港股 + 美股 combined）。

第二十二轮（liubo 2026-10-16 拍板）：Section 1 改成"持仓成本"为主 ——
- 当 journal 有 holdings（市值可算）→ 显示 持仓表（分类 / 市值 / 占比 / 目标 / 偏离）
- 当 journal holdings 为空 → fallback 到 持仓成本表（分类 / 成本 / 基金数 / 占比）
  （cost_basis.py + breakdown.py 拼出，不依赖 akshare 拉实时价）
- 任何时候都跟一段"最近调仓记录" note（git log 5 条 commit 摘要）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from global_allocation.portfolio.breakdown import (
    FUND_NAME_BY_CODE,
    SwensenClass,
    compute_breakdown,
    get_fund_name,
    get_subclass,
)

# 跨组合白名单：这些基金属于其他组合（ETF 轮动 / 红利策略 / 其他），不在大类资产配置 card 里展示
# liubo 2026-10-17 反馈 — global-allocation / dividend-strategy / etf-rotation 三个组合分开管理
# 历史基金（019524/020291/160422）虽然不在 COST_BASIS_BY_CODE，仍属于大类资产配置历史持仓 → 保留
_CROSS_PORTFOLIO_EXCLUDE_CODES: frozenset[str] = frozenset({
    "010989",   # 南方中证房地产 ETF 联接 E — ETF 轮动组合
    "023917",   # 红利策略基金
    "457001",   # 国富亚洲机会 — ETF 轮动组合
    "025682",   # 红利策略基金
})

# 定投计划 (DCA) — 显示在调仓纪录的"金额"列
# liubo 2026-10-17 反馈 — 金额列显示 3 个值：定投额 / 频率 / 总目标
# 例：016452 南方纳100 → "10 元 / 每天 / 累计 1800"
# 不写「已买 ¥X」（已经买入了多少他不关心）。
# 数据源：cost_basis.py 注释里的 DCA 设定。
# liubo 2026-10-17 反馈 — 金额列显示 3 个值：定投额 / 频率 / 总目标
# 例：016452 南方纳100 → "10 元 / 每天 / 累计 1800"
# 不写「已买 ¥X」（已经买入了多少他不关心）。
# 数据源：liubo 2026-10-17 提供的天天基金 DCA 计划详情截图（不是 commit 注释）。
_DCA_SCHEDULE_BY_CODE: dict[str, dict[str, str]] = {
    # code: {"per": 定投额, "cycle": 频率, "total": 总目标, "status": 状态}
    # status 值（liubo 2026-10-18 拍板）：
    # - "进行中"：DCA 计划激活，自动扣款中
    # - "到期"：累计金额达到目标，自动暂停
    # - "终止"：手动停掉 DCA 计划，不再扣款
    # 优先级：手动 status override > 自动检测（cost_basis >= total → 到期）> 默认 "进行中"
    "016452": {"per": "10 元", "cycle": "每天", "total": "1800", "status": "进行中"},         # 南方纳100（DCA ¥10/天 → 累计 1800 暂停）
    "019172": {"per": "10 元", "cycle": "每天", "total": "2000", "status": "进行中"},         # 摩根纳斯达克100(QDII)人民币A（DCA ¥10/天 → 累计 2000 暂停）
    "019441": {"per": "10 元", "cycle": "每天", "total": "2000", "status": "进行中"},         # 万家纳斯达克100发起式(QDII)A（DCA ¥10/天 → 累计 2000 暂停）
    "000614": {"per": "250-1000 元", "cycle": "每周三", "total": "10000", "status": "进行中"}, # 华安德国(DAX)联接(QDII)A（慧定投 250-1000/周三 → 累计 10000 暂停）
    "000216": {"per": "250-1000 元", "cycle": "每周一", "total": "7500", "status": "进行中"},  # 华安黄金ETF联接A（慧定投 250-1000/周一 → 累计 7500 暂停）
}

# DCA 计划创建日（liubo 2026-10-17 提供的天天基金截图）
# 用于定投记录表 date 列 — 不是从 git log 拿（git log 里的 10-09 行是 校准 事件不是 DCA 起始）
_DCA_PLAN_START_BY_CODE: dict[str, str] = {
    "016452": "2026-09-29",  # 南方纳100（天天基金 DCA 计划创建日）
    "019172": "2026-09-29",  # 摩根纳100
    "019441": "2026-09-29",  # 万家纳100
    "000614": "2026-09-29",  # 华安 DAX
    "000216": "2026-10-08",  # 华安黄金（10-08 单独创建，比 NDX 4 只晚 9 天）
}


def _format_dca_amount(schedule: dict[str, str]) -> str:
    """把 DCA schedule dict 格式化成 "10 元 / 每天 / 累计 1800"。

    liubo 2026-10-17 反馈 — 金额列显示 3 个值：定投额 / 频率 / 总目标。
    """
    return f"{schedule['per']} / {schedule['cycle']} / 累计 {schedule['total']}"


def _compute_dca_status(code: str, schedule: dict[str, str]) -> str:
    """计算 DCA 状态（liubo 2026-10-18 拍板 — 加「状态」字段）。

    状态值：
    - "进行中"：DCA 计划激活，天天基金自动扣款中
    - "到期"：累计金额达到目标，自动暂停
    - "终止"：手动停掉 DCA 计划，不再扣款

    优先级（liubo 2026-10-18 拍板）：
    1. 手动 override（schedule["status"] 显式设置）→ 直接用
    2. 自动检测（cost_basis >= total）→ "到期"
    3. 默认 → "进行中"

    注意：cost_basis 自 2026-10-17 commit 0e9e430 后对 DCA 基金是「累计目标」不是「实际已买」，
    所以自动检测只对非 DCA 基金或 cost_basis 已切换为「实际已买」的 DCA 基金准确。
    对 5 只当前 DCA 基金，靠手动 status override（status="进行中"）保证正确显示。
    """
    explicit = schedule.get("status")
    if explicit in ("进行中", "到期", "终止"):
        return explicit
    # 自动检测：cost_basis >= total → "到期"
    try:
        from global_allocation.portfolio.cost_basis import COST_BASIS_BY_CODE

        cost = COST_BASIS_BY_CODE.get(code)
        total = int(schedule["total"])
        if cost is not None and cost >= Decimal(str(total)):
            return "到期"
    except (ValueError, TypeError, ImportError):
        pass
    return "进行中"
from global_allocation.portfolio.journal import PortfolioJournal
from global_allocation.portfolio.models import (
    FundValuation,
    ValuationIndicator,
    ValuationIndicatorCode,
)
from global_allocation.portfolio.strategy import (
    DEFAULT_POSITION_ALLOCATION,
    PositionAllocation,
)
from global_allocation.portfolio.valuation_indicators import (
    DEFAULT_SCORE_BANDS,
    compute_composite_score,
    compute_verdict,
    format_5band_threshold,
    interpret_composite_score,
    score_indicator,
)


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
        # 第十三轮反馈（liubo 2026-09-18）：去掉第一行 "**{title}**"
        # 卡片 header.title 已经是"实盘周报"，body 开头 note section header 已经是"实盘持仓"，
        # summary div 里再写一遍 title 是冗余。
        f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}  \n"
        f"**总市值**：{_format_money(total)} CNY  \n"
        f"**总成本**：{_format_money(total_cost)} CNY  \n"
        f"**浮动盈亏**：{_format_money(total_pnl)} ({_format_pct(total_pnl_pct)})  \n"
        f"**上周涨跌**：{week_str}  \n"
        f"**累计涨跌**：{cumulative_str}"
    )


def _build_breakdown_bar(journal: PortfolioJournal) -> dict[str, object]:
    """各大类资产占比柱状图（vertical bar，X 轴 = 类名，Y 轴 = 占比 %）。

    第三轮反馈：纵坐标改成占比（用户更关心配置比例而非绝对金额）。
    weight 原始值是 0~1 的 Decimal，乘以 100 转成百分比数字交给 VChart（Y 轴
    会显示成 0/5/10/.../30，标题"各大类资产占比（%）"明示单位）。

    按 SwensenClass 枚举自然顺序展示全部 11 个子类（spec 097 第十七轮精简到 11），
    含 count=0 的——这样能直观看到哪些子类没覆盖到，是配置漏洞）。
    空子类 weight=0 在柱状图上不画柱子、保留 X 轴标签。

    飞书 VChart 柱状图 = simple 格式：type="bar" + data.values + xField/yField
    （column 是 VChart 内部名，飞书对外只认 "bar"；不加 direction 默认就是垂直柱状图）

    第十九轮（liubo 2026-09-19）：加 data labels — 柱子顶上显示数值。

    之前为了加 % 后缀试过 formatMethod/formatter 都失败（飞书 VChart 不支持 JS 函数和
    {value} 模板替换，见 memory feishu_vchart_chart_label_limits）。但只要不传 formatter，
    默认的 label 渲染就只显示原始数值（"29.9" 这种），单位靠 title "占比（%）" 明示。
    liubo 反馈："股票和港股的上边那个数字怎么没在上面了，希望它在上面" — 加
    label.visible + position="top" 解决。
    """
    breakdown = compute_breakdown(journal)  # 已经是 SwensenClass 枚举顺序
    bars: list[dict[str, object]] = [
        {
            "class": b["display_name"],
            # round 到 1 位小数（第四轮反馈：Y 轴数字 .1f 就够了，2 位太长了）
            "weight": round(float(b["weight"]) * 100, 1),  # 0~1 → 0~100, 1 位
        }
        for b in breakdown
    ]

    return {
        "type": "bar",
        "title": {"text": "各大类资产占比（%，按 Swensen 框架顺序）"},
        "data": {"values": bars},
        "xField": "class",
        "yField": "weight",
        "legends": {"visible": False},
        # 第十九轮（liubo 2026-09-19）：加 data labels（柱子顶上的数字）
        "label": {
            "visible": True,
            "position": "top",
            # 不传 formatMethod — 飞书 VChart 不支持 JS 函数和 {value} 模板替换
            # （memory feishu_vchart_chart_label_limits），让默认渲染显示原始数值
        },
    }


def _build_holdings_table(
    journal: PortfolioJournal,
    position: PositionAllocation = DEFAULT_POSITION_ALLOCATION,
) -> dict[str, object]:
    """11 子类聚合表：分类 / 市值 / 占比 / 目标 / 偏离。

    spec 097 第二十一轮（liubo 2026-09-24）：target/delta 列改用整数仓位模型。

    历史：
    - 第十四~第十八轮：3 个 section（持仓 + 策略 + 估值）
    - 第十九轮：合并 Section 2 + 3 到持仓表（加 target/delta 列）
    - 第二十轮：恢复 Section 2（用户要监控超类比例）
    - 第二十一轮（当前）：删除 Section 2，改用整数仓位模型（target = 固定份数，不动态缩放）
      - target 列：投资子类显示 "{N} 份 ({X.X}%)"，现金显示 "[{min}, {max}] 份"
      - delta 列：投资子类显示 "{+/-N} 份"（份数差），现金显示状态文本

    5 列含义：
    - 分类（含 #. 前缀，例 "1. A 股股票"）：子类中文名
    - 市值(¥)：当前市值（CNY，千分位逗号）
    - 占比：当前权重（%，2 位小数）
    - 目标：固定整数份数 + 百分比（不再动态缩放）
      - 投资子类（10 个）："{target_position} 份 ({target_weight:.1f}%)"
      - 现金（1 个）："[{cash_min}, {cash_max}] 份"
    - 偏离：份数差（不是 pp 后缀）
      - 投资子类（10 个）："{+/-N} 份"（当前份 - 目标份，正 = 超配，负 = 低配）
      - 现金（1 个）："区间内" / "低于下限" / "高于上限"

    spec 096：
    - 第二轮反馈：用户不要"细致到具体基金"，表只回答"我每个大类持了多少"
    - 第三轮反馈：表加序号信息
    - 第四轮反馈：# 列太宽了
    - 第五轮反馈（实测后的折中）：Feishu table 列 width 只接受 "auto"，其它值（short/
      medium/long/数字）API 都拒。所以把 # 信息嵌进分类名前缀（"1. A 股股票"），
      干掉单独 # 列——既保留序号信息，又没有多余宽列

    按 SwensenClass 枚举顺序展示全部 11 个子类（含 count=0 的——空子类显示 0 元 / 0.00%，
    这样能直观看到 Swensen 框架里哪些子类没覆盖到；spec 097 第十七轮精简到 11）。

    Feishu 表格 row 必须是 dict（按列名取）；所有列 data_type=text（value/weight/target/delta
    都是预格式化的字符串）。
    """
    breakdown = compute_breakdown(journal)  # 已经是 SwensenClass 枚举顺序

    rows: list[dict[str, object]] = []
    for idx, b in enumerate(breakdown, start=1):
        sub = b["subclass"]
        weight_pct = float(b["weight"]) * 100
        target_position = position.target_position(sub)
        target_weight_pct = float(position.target_weight(sub)) * 100

        if sub == SwensenClass.CASH:
            # 现金行：target 是区间份数，delta 是状态文本
            cash_min, cash_max = position.cash_range
            target_str = f"[{cash_min}, {cash_max}] 份"
            delta_str = position.cash_status(b["weight"])
        else:
            # 投资子类：target = "{N} 份 ({X.X}%)"，delta = 当前份 - 目标份
            target_str = f"{target_position} 份 ({target_weight_pct:.1f}%)"
            # 当前份数 = round(current_value / unit_size)（用市值 / 1 仓近似）
            current_value = b["value"]
            current_position = (
                round(float(current_value) / float(position.unit_size))
                if current_value > 0 else 0
            )
            position_delta = current_position - target_position
            sign = "+" if position_delta >= 0 else ""
            delta_str = f"{sign}{position_delta} 份"

        rows.append(
            {
                "class": f"{idx}. {b['display_name']}",  # 序号嵌进分类名前缀
                "value": f"{float(b['value']):,.2f}",
                "weight": f"{weight_pct:.2f}%",
                "target": target_str,
                "delta": delta_str,
            }
        )

    return {
        "columns": [
            # 全部 width="auto"（实测 Feishu table 只接受 "auto"；short/medium/long/数字都拒）
            {"name": "class", "display_name": "分类", "data_type": "text", "width": "auto"},
            {"name": "value", "display_name": "市值(¥)", "data_type": "text", "width": "auto"},
            {"name": "weight", "display_name": "占比", "data_type": "text", "width": "auto"},
            {"name": "target", "display_name": "目标", "data_type": "text", "width": "auto"},
            {"name": "delta", "display_name": "偏离", "data_type": "text", "width": "auto"},
        ],
        "rows": rows,
    }


# ─── 第二十二轮（liubo 2026-10-16）：持仓成本表 + 最近调仓 ───
#
# 当 journal holdings 为空（DB 没灌基金/流水）时，Section 1 改成"持仓成本"为主：
# - 持仓成本表：基于 cost_basis.py + breakdown.py，按子类聚合（11 行 × 4 列）
# - 调仓纪录：独立 section（位置在 Section 1 和 Section 2 之间），从 git log 过滤出
#   真实调仓 commit（建仓/加仓/减仓/转换/砍仓/校准/对账/赎回/卖出），过滤掉
#   调参/规则（估值改/拍板/卡片/fallback/docs/spec/注释/备注/同步 CSV/框架/适配）
#
# 为什么这样过滤（liubo 2026-10-17 反馈"调仓纪录，不要写调仓规则的纪录"）：
# - 调仓规则 commit = 规则调整/估值方法改动/卡片 UI 改动等，不算实际调仓动作
# - 真实调仓 = 实际发生的加仓/减仓/换基/建仓/转换/成本校准
# - 用户看"调仓纪录"是为了知道"我实际调过什么"，不是"调仓规则改了什么"
#
# 为什么这么改（liubo 2026-10-16 反馈）：
# - cost_basis.py 已经有 52 只基金的累计买入总金额（604143.47 + 校准 +1661 = 605804.47）
# - 用户拍板"我的成本和每次的调仓你不是都知道吗" → 数据来源 = cost_basis + git log
# - 不依赖 akshare 拉实时市值（有时 akshare 不稳或基金当天净值没更新）


def _build_cost_basis_section() -> list[dict[str, object]]:
    """Section 1 fallback — 持仓成本表 + 最近调仓记录（spec liubo 2026-10-16）。

    数据来源：
    - cost_basis.py（COST_BASIS_BY_CODE + SUBCLASS_BY_CODE）→ 每只基金的累计成本 + 子类
    - git log（subprocess）→ 最近 5 条 commit 摘要

    列：分类 / 基金数 / 累计成本(¥) / 占比
    按 SwensenClass 枚举顺序展示 11 个子类（cost_basis 只覆盖有数据的子类；
    空的子类不渲染 — 跟 holdings 表"含 count=0"展示不同，原因是 cost 视角更聚焦"已建仓"）。

    注意：004137 货币基金按现金处理，不进 cost_basis（CASH_LIKE_CODES），
    所以 CASH 子类 cost=0 → 不渲染。
    """
    from global_allocation.portfolio.breakdown import DISPLAY_NAME, SUBCLASS_BY_CODE
    from global_allocation.portfolio.cost_basis import COST_BASIS_BY_CODE

    # 按子类聚合
    by_sub: dict[SwensenClass, tuple[int, Decimal]] = {}
    for code, cost in COST_BASIS_BY_CODE.items():
        sub = SUBCLASS_BY_CODE.get(code)
        if sub is None:
            continue
        cnt, total = by_sub.get(sub, (0, Decimal("0")))
        by_sub[sub] = (cnt + 1, total + cost)

    total_cost = sum((c for _, c in by_sub.values()), Decimal("0"))
    rows: list[dict[str, object]] = []
    for sub in SwensenClass:
        if sub not in by_sub:
            continue
        cnt, cost = by_sub[sub]
        weight_pct = float(cost / total_cost * 100) if total_cost > 0 else 0.0
        rows.append(
            {
                "class": DISPLAY_NAME[sub],
                "count": f"{cnt}",
                "cost": f"{float(cost):,.2f}",
                "weight": f"{weight_pct:.2f}%",
            }
        )

    cost_table: dict[str, object] = {
        "columns": [
            {"name": "class", "display_name": "分类", "data_type": "text", "width": "auto"},
            {"name": "count", "display_name": "基金数", "data_type": "text", "width": "auto"},
            {"name": "cost", "display_name": "累计成本(¥)", "data_type": "text", "width": "auto"},
            {"name": "weight", "display_name": "成本占比", "data_type": "text", "width": "auto"},
        ],
        "rows": rows,
    }

    # 跟 DEFAULT_POSITION_ALLOCATION 比对，标出欠配/超配
    # 第三十轮（liubo 2026-10-18 反馈）：摘要补「欠配/超配」一行。
    # 第三十一轮（liubo 2026-10-18 反馈）：单位改「仓」（1 仓 = 1 万 CNY），按偏差大小排序。
    # 阈值 0.5 仓：|dev| < 0.5 不标（避免噪音；0.5 仓 = 5000 CNY）。
    # CASH 不参与 — CASH 不在 cost_basis。
    # 排序：欠配从最欠配到最轻欠配，超配从最超配到最轻超配（绝对值降序）。
    unit_size = float(DEFAULT_POSITION_ALLOCATION.unit_size)  # 10000 CNY
    under_alloc: list[tuple[float, str]] = []  # (dev_cang, label) — dev 为负，按 dev 升序（最负在前）
    over_alloc: list[tuple[float, str]] = []   # (dev_cang, label) — dev 为正，按 dev 降序（最正在前）
    for sub in SwensenClass:
        if sub == SwensenClass.CASH:
            continue
        if sub not in by_sub:
            continue
        cost = by_sub[sub][1]
        actual_cang = float(cost) / unit_size if unit_size > 0 else 0.0
        target_cang = float(DEFAULT_POSITION_ALLOCATION.target_position(sub))
        dev_cang = actual_cang - target_cang
        if dev_cang < -0.5:
            under_alloc.append((dev_cang, f"{DISPLAY_NAME[sub]} {dev_cang:+.1f} 仓"))
        elif dev_cang > 0.5:
            over_alloc.append((dev_cang, f"{DISPLAY_NAME[sub]} {dev_cang:+.1f} 仓"))

    # 排序：欠配 dev 越小越靠前（最负）；超配 dev 越大越靠前（最正）
    under_alloc.sort(key=lambda t: t[0])
    over_alloc.sort(key=lambda t: t[0], reverse=True)

    # summary div：生成时间 + 总成本 + 大类资产数 + 欠配/超配
    # 第二十九轮（liubo 2026-10-17 反馈）：改"基金数"为"大类资产数"——
    # 用户视角下"52 只"太细节，关心的是"我覆盖了几大类资产"（10 类 = A 股/港股/美股/国外发达/
    # 新兴市场/国内 REITs/美国 REITs/国内利率债/美债/商品；现金 004137 不算）。
    n_classes = len(by_sub)  # 有基金进 COST_BASIS_BY_CODE 的大类数（004137 在 CASH_LIKE_CODES 不进）
    summary_text = (
        f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}  \n"
        f"**总成本**：{float(total_cost):,.2f} CNY  \n"
        f"**大类资产数**：{n_classes} 类  \n"
        f"**欠配**：{' / '.join(label for _, label in under_alloc) if under_alloc else '—'}  \n"
        f"**超配**：{' / '.join(label for _, label in over_alloc) if over_alloc else '—'}"
    )

    elements: list[dict[str, object]] = [
        {"tag": "note", "elements": [{"tag": "plain_text", "content": "持仓成本"}]},
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": summary_text},
        },
        {"tag": "hr"},
        {"tag": "table", **cost_table},
    ]

    return elements


# ─── 第二十三轮（liubo 2026-10-17 拍板）：per-fund 持仓成本 + 估值 + 调仓建议 ───
#
# 用户原话（飞书 2026-10-17 反馈）：
# "第二部分呢，我是需要具体到每一只基金，然后这只基金现在是成本是多少？
#  然后这个然后这个就是它的估值是什么情况？然后我调仓的建议是什么？
#  这个就是帮我理解说我现在是不是需要进行一些调仓的操作？"
#
# 4 列：基金 / 持仓成本 / 估值 / 调仓建议
# 数据源：
# - 持仓成本：COST_BASIS_BY_CODE（52 只全列；cost=0 显示 "—"，不写 0.00）
# - 估值 + 调仓建议：weekly_rebalance_plan() 的 RebalanceAction
#   - 19 只 + 3 watchlist 在 FUND_INDEX_MAP（股票/REITs/黄金），有 metric
#   - 剩下 30 只左右（中短债/美债/现金）→ 估值 "—"，调仓建议 "不动"
#   - NDX 5 只（NDX 指数估值数据缺失）→ metric_value=None → 估值 "—"，advice 标 "跳过（数据缺失）"
# 排序：SwensenClass 枚举顺序（A 股 → 港股 → 美股 → 国外发达 → 新兴市场 → 国内 REITs → 美国 REITs → 国内利率债 → 美债 → 商品 → 现金）+ fund_code
# 不在 COST_BASIS_BY_CODE 但在 actions 里的（watchlist 的 3 个指数 HSI/N225/FCHI）→ 不渲染（无成本可显示）


def _build_per_fund_section(region: SwensenClass | None = None) -> list[dict[str, object]]:
    """Section 2: per-fund 持仓成本 + 估值 + 调仓建议（liubo 2026-10-17 拍板）。

    列：基金 | ETF 指数 | 持仓成本(¥) | 估值 | 调仓建议

    第三十五轮（liubo 2026-10-18）：传 region = SwensenClass.CN_EQUITY 时只显示 A 股行。
    - 持仓基金（COST_BASIS）：SUBCLASS_BY_CODE[code] == region 才入选
    - watchlist 基金（INDEX_WATCHLIST）：INDEX_WATCHLIST entry 的 subclass == region 才入选
    - 合并组（如 NDX 5 只 = 美股）：5 只都判 US_EQUITY，整组入选美股卡
    - region=None（默认）：全部 55 行
    """
    from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE
    from global_allocation.portfolio.cost_basis import COST_BASIS_BY_CODE
    from global_allocation.portfolio.pe_rebalance import (
        INDEX_DISPLAY_NAME,
        METRIC_DISPLAY_NAME,
        PESignal,
        RebalanceAction,
        ValuationMetric,
        weekly_rebalance_plan,
    )

    actions: list[RebalanceAction] = weekly_rebalance_plan()
    # 个别 code → action（处理 merge_by_etf 把 3 只 A500 合并成 "022434+022424+022448"）
    by_code: dict[str, RebalanceAction] = {}
    # watchlist 虚拟基金（fund_code 是 "(HSI)" 这种）→ 通过 etf_index_code 索引
    by_index_code: dict[str, RebalanceAction] = {}
    for a in actions:
        if "+" in a.fund_code:
            # 合并组 — 每只组内基金都映射到同一个 action
            for sub_code in a.fund_code.split("+"):
                by_code[sub_code] = a
        elif a.fund_code.startswith("(") and a.fund_code.endswith(")"):
            # watchlist 虚拟基金（fund_code="(HSI)"）→ 用 etf_index_code 索引
            by_index_code[a.etf_index_code] = a
        else:
            by_code[a.fund_code] = a

    # 11 个 SwensenClass 按枚举顺序排（DISPLAY_NAME 里 "A 股股票" / "港股" / ... 跟用户认知一致）
    subclass_order = {s: i for i, s in enumerate(SwensenClass)}

    # 按 (subclass_order, etf_index_code, fund_code_or_watchlist_marker) 排序
    # 第三十三轮（liubo 2026-10-18 反馈）：
    # - 大类顺序：A股 → 港股 → 美股 → ...
    # - 同 ETF 指数聚一起（如 NDX 5 只挨着）
    # - INDEX_WATCHLIST 里的指数（HSI/N225/FCHI）也列出，持仓 0
    from global_allocation.portfolio.pe_rebalance import INDEX_WATCHLIST

    items: list[tuple[int, str, str, Decimal, str]] = []
    # sort_key, etf_index_code_for_sort, code, cost, source
    # source: "holdings" 或 "watchlist"
    holdings_codes: set[str] = set()
    # 第三十六轮（liubo 2026-10-19）：dedupe — 持仓基金对应的 etf_index_code 已经在 holdings 行展示了，
    # 不再加 watchlist 行（避免 A 股卡 8 个指数里 7 个出现 2 行）。只有没持仓的指数（目前 A100 / 000903）走 watchlist 行。
    held_index_codes: set[str] = set()

    # 先加持仓基金（COST_BASIS_BY_CODE）
    for code, cost in COST_BASIS_BY_CODE.items():
        sub = SUBCLASS_BY_CODE.get(code)
        if sub is None:
            continue
        # 第三十五轮：region 过滤 — 只入选该 region 的基金
        if region is not None and sub != region:
            continue
        # 拿基金对应的 ETF 指数 code（从 by_code 反查；可能没在 FUND_INDEX_MAP）
        action = by_code.get(code)
        index_code_for_sort = action.etf_index_code if action else ""
        if action is not None:
            held_index_codes.add(action.etf_index_code)
        items.append((subclass_order[sub], index_code_for_sort, code, cost, "holdings"))
        holdings_codes.add(code)

    # 加 INDEX_WATCHLIST 里的指数（持仓 0）。但已被持仓覆盖的 index 跳过。
    for sub, index_code, display_name, _metric, _value, _pct in INDEX_WATCHLIST:
        if region is not None and sub != region:
            continue
        if index_code in held_index_codes:
            continue  # dedupe：持仓行已展示该 index
        items.append((subclass_order[sub], index_code, f"({index_code})", Decimal("0"), "watchlist"))

    items.sort(key=lambda t: (t[0], t[1], t[2]))

    # 信号 → 中文显示
    signal_display = {
        PESignal.ADD: "加仓",
        PESignal.REDUCE: "减仓",
        PESignal.BUILD: "建仓",
        PESignal.HOLD: "不动",
        PESignal.SKIP: "跳过",
    }

    n_add = n_reduce = n_build = n_hold = n_skip = 0

    rows: list[dict[str, object]] = []
    for _, _index_sort, code, cost, source in items:
        is_watchlist = source == "watchlist"
        # watchlist 行：fund 显示指数中文名（不带 code）；holdings 行：name (code)
        if is_watchlist:
            index_code_inner = code.strip("()")  # "(HSI)" → "HSI"
            display_name = INDEX_DISPLAY_NAME.get(index_code_inner, index_code_inner)
            fund_label = f"{display_name}（无持仓）"
            cost_str = "0.00"  # watchlist 显式 0，不写 "—"
            action = by_index_code.get(index_code_inner)
        else:
            fund_name = get_fund_name(code) or f"基金 {code}"
            fund_label = f"{fund_name} ({code})"
            cost_str = f"{float(cost):,.2f}" if cost > 0 else "—"
            action = by_code.get(code)

        if action is None:
            # 没在 FUND_INDEX_MAP（大多是债/现金）— 没估值指标，调仓建议按"不动"
            valuation = "—"
            advice = "不动"
            n_hold += 1
        else:
            # 估值列
            metric_name = METRIC_DISPLAY_NAME.get(action.metric, action.metric.value)
            if action.metric_value is None:
                # 数据源没给绝对值（NDX 螺丝钉只给分位）— 但 percentile 仍可显示
                if action.metric_percentile is not None:
                    pct = float(action.metric_percentile) * 100
                    valuation = f"{metric_name} 分位 {pct:.2f}%"
                else:
                    valuation = "—"
            else:
                if action.metric == ValuationMetric.GOLD_HISTORICAL_PCT:
                    # 金价：CNY/g 格式
                    val_str = f"{float(action.metric_value):.2f}"
                    valuation = f"{metric_name} {val_str} CNY/g"
                elif action.metric == ValuationMetric.GOLD_REAL_YIELD:
                    # 实际利率：fraction → percent
                    val_str = f"{float(action.metric_value) * 100:.2f}%"
                    valuation = f"{metric_name} {val_str}"
                elif action.metric == ValuationMetric.DY:
                    # DY：percent number
                    val_str = f"{float(action.metric_value):.2f}%"
                    valuation = f"{metric_name} {val_str}"
                elif action.metric == ValuationMetric.P_NAV:
                    val_str = f"{float(action.metric_value):.2f}x"
                    valuation = f"{metric_name} {val_str}"
                else:
                    # PE-TTM / P/FFO / PS-TTM
                    val_str = f"{float(action.metric_value):.2f}"
                    valuation = f"{metric_name} {val_str}"
                if action.metric_percentile is not None:
                    pct = float(action.metric_percentile) * 100
                    valuation = f"{valuation} / 分位 {pct:.2f}%"

            # 调仓建议列
            sig_text = signal_display.get(action.signal, action.signal.value)
            if action.signal == PESignal.SKIP:
                # 跳过：列 "跳过（数据缺失）" / "跳过（无估值指标）" / "跳过（冷却期）"
                # reason 里通常是 reason 全文 — 取第一个括号或句号前的部分做简短显示
                short_reason = action.reason.split("（")[0].split("。")[0].strip()
                if len(short_reason) > 14:
                    short_reason = short_reason[:14] + "…"
                advice = f"跳过（{short_reason}）" if short_reason else "跳过"
                n_skip += 1
            else:
                # 加仓/减仓/建仓/不动 — 列信号 + 简短 reason
                # reason 太长截前 30 字
                short_reason = action.reason
                if len(short_reason) > 30:
                    short_reason = short_reason[:30] + "…"
                advice = f"{sig_text}（{short_reason}）" if short_reason else sig_text
                if action.signal == PESignal.ADD:
                    n_add += 1
                elif action.signal == PESignal.REDUCE:
                    n_reduce += 1
                elif action.signal == PESignal.BUILD:
                    n_build += 1
                else:
                    n_hold += 1

        # ETF 指数列：watchlist 直接拿指数 code；holdings 从 action 拿
        if is_watchlist:
            index_code_inner = code.strip("()")
            etf_index_label = INDEX_DISPLAY_NAME.get(index_code_inner, "—")
        else:
            etf_index_label = INDEX_DISPLAY_NAME.get(action.etf_index_code, "—") if action else "—"

        rows.append({
            "fund": fund_label,
            "etf_index": etf_index_label,
            "cost": cost_str,
            "valuation": valuation,
            "advice": advice,
        })

    # summary div — 速读本周动作（第二十九轮 liubo 2026-10-17 反馈：
    # 删掉「总成本」行 — Section 1 已经展示总成本了，Section 2 不重复。
    # 保留「基金数」+「本周操作」（加仓 N / 建仓 N / 减仓 N）
    # 第三十五轮（liubo 2026-10-18）：region card 时改用「本 region 基金数」以明示筛选
    # 第三十六轮（liubo 2026-10-19）：region card 时扩成 5 行 — 本大类资产的基金数 + 跟踪的etf数量
    #   + 本大类资产的占比 + 超配/欠配 + 超/欠的仓位
    if region is not None:
        # ─── region card：5 行扩展摘要 ───
        from global_allocation.portfolio.cost_basis import (
            COST_BASIS_BY_CODE,
            TOTAL_COST_CNY,
        )

        # 1) 本大类资产的基金数（仅 holdings，不含 watchlist）
        n_region_funds = sum(
            1 for code, _ in COST_BASIS_BY_CODE.items()
            if SUBCLASS_BY_CODE.get(code) == region
        )

        # 2) 跟踪的 etf 数量 = holdings 唯一 index + watchlist 唯一 index（都已 dedupe，
        #    实际就是 items 里 etf_index_code 唯一集合，holdings + watchlist 全部 source）
        tracked_etf_codes: set[str] = set()
        for _, idx_code, _code, _cost, source in items:
            if source == "holdings" and action is not None:
                # 用 action 反查 (不直接用 items 里的 idx_code 因为可能为空)
                pass
            # 直接从 items 拿 index_code（holdings 行已填，watchlist 行也填了 index_code）
            if idx_code:
                tracked_etf_codes.add(idx_code)
        n_tracked_etfs = len(tracked_etf_codes)

        # 3) 本大类资产的占比 = region holdings cost / TOTAL_COST_CNY
        region_cost = sum(
            (cost for code, cost in COST_BASIS_BY_CODE.items()
             if SUBCLASS_BY_CODE.get(code) == region),
            Decimal("0"),
        )
        region_weight = (region_cost / TOTAL_COST_CNY) if TOTAL_COST_CNY > 0 else Decimal("0")

        # 4) 超配/欠配：实际仓（cost / unit_size）vs 目标仓（DEFAULT_POSITION_ALLOCATION.target_position）
        # region 跟 SwensenClass 1:1 对应（4 张卡的 region 都是单 subclass）
        unit_size = float(DEFAULT_POSITION_ALLOCATION.unit_size)  # 10000 CNY
        actual_cang = float(region_cost) / unit_size if unit_size > 0 else 0.0
        target_cang = float(DEFAULT_POSITION_ALLOCATION.target_position(region))
        dev_cang = actual_cang - target_cang
        if abs(dev_cang) < 0.05:
            alloc_status = "区间内"
        elif dev_cang < 0:
            alloc_status = "欠配"
        else:
            alloc_status = "超配"

        summary_parts = [
            f"**本大类资产的基金数**：{n_region_funds} 只  ",
            f"**跟踪的 ETF 数量**：{n_tracked_etfs} 个  ",
            f"**本大类资产的占比**：{float(region_weight) * 100:.2f}%  ",
            f"**超配/欠配**：{alloc_status}  ",
            f"**超/欠的仓位**：{dev_cang:+.2f} 仓（实际 {actual_cang:.2f} / 目标 {target_cang:.0f}）",
        ]
        summary_text = "  \n".join(summary_parts)
    else:
        # ─── 单卡（向后兼容）：基金数 + 本周操作 ───
        summary_parts = [
            f"**基金数**：{len(rows)} 只  ",
        ]
        op_parts = []
        if n_add:
            op_parts.append(f"加仓 {n_add}")
        if n_reduce:
            op_parts.append(f"减仓 {n_reduce}")
        if n_build:
            op_parts.append(f"建仓 {n_build}")
        if op_parts:
            summary_parts.append(f"**本周操作**：{' / '.join(op_parts)}")
        elif n_skip or n_hold:
            summary_parts.append(f"**本周**：{' / '.join(filter(None, ['跳过 ' + str(n_skip) if n_skip else '', '不动 ' + str(n_hold) if n_hold else '']))}")
        summary_text = "  \n".join(summary_parts)

    per_fund_table: dict[str, object] = {
        "columns": [
            {"name": "fund", "display_name": "基金", "data_type": "text", "width": "auto"},
            {"name": "etf_index", "display_name": "ETF 指数", "data_type": "text", "width": "auto"},
            {"name": "cost", "display_name": "持仓成本(¥)", "data_type": "text", "width": "auto"},
            {"name": "valuation", "display_name": "估值", "data_type": "text", "width": "auto"},
            {"name": "advice", "display_name": "调仓建议", "data_type": "text", "width": "auto"},
        ],
        "rows": rows,
    }

    return [
        {"tag": "hr"},
        {"tag": "note", "elements": [{"tag": "plain_text", "content": "持仓与调仓建议"}]},
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": summary_text},
        },
        {"tag": "table", **per_fund_table},
    ]


# ─── 第二十二轮（liubo 2026-10-17）：调仓纪录 section（独立，1-2 之间）───
#
# 关键设计：解析 commit body 而不是 subject（liubo 2026-10-17 反馈）：
# - 基金名：FUND_NAME_BY_CODE 查 code → 中文全称（不再显示 "013127"）
# - 多基金拆分：commit body 每条 `- 0xxxxx ...` 行 → 单独一行（不再把 3 笔放一起）
# - 金额：commit body 里 liubo 写了具体金额（如 "10000 元" / "已买 ¥40" / "+1661"）→ 提取
# - 无 "—" 占位符：每只基金一定显示名（fallback = "基金 <code>"，不编造）
#
# 关键词白名单：识别真实调仓 commit（subject 含明确动作词）
# - "校准/对账/转换/砍/新建仓/赎回/卖出/清仓/加仓" 等
# - "加 0xxxxx" / "扩 N 只" / "调整" + 6 位基金代码
# - 不在白名单 = 调参/规则/卡片/docs，过滤掉

_REBALANCE_KEYWORDS: tuple[str, ...] = (
    # 关键词带空格避免误命中（"赎回费" 属于调仓规则，不是真实调仓）
    "校准", "对账", "转换", "砍", "新建仓", "建仓", "赎回 ", "卖出 ", "清仓",
    "加仓 ", "+1 仓", "−1 仓", "-1 仓",
)


def _is_rebalance_subject(subject: str) -> bool:
    """判断 commit subject 是否是真实调仓（白名单匹配）。"""
    for marker in _REBALANCE_KEYWORDS:
        if marker in subject:
            return True
    if re.search(r"加\s*?\d{6}", subject):
        return True
    if re.search(r"[扩加]\s*?\d+\s*?只", subject):
        return True
    if "调整" in subject and re.search(r"\d{6}", subject):
        return True
    return False


@dataclass(frozen=True, slots=True)
class RebalanceRow:
    """调仓纪录里的一行。

    Fields:
    - date: commit 日期（YYYY-MM-DD）
    - fund: 基金显示文本（1 只 = name；建仓+→+2 codes 只显示 target；转换 = 转出/转入拆分）
    - action: 中文动作标签（建仓 / 加仓 / 减仓 / 转换 / 砍仓 / 赎回 / 卖出 / 校准 / 对账 / 扩仓）
      — 注意：不显示「换基」；"建仓 N 仓 + → + 2 code" 是给 target 加仓
    - amount: 金额显示（"10000 元" / "+40" / "已买 ¥40" / "建仓 1 仓 / 10000 元" / "+1661 CNY" / ...）
    - fund_codes: 这行涉及的基金代码（用于按 region 过滤；转换行有 2 个，普通行 1 个）
      第三十五轮（liubo 2026-10-18）：拆 4 张卡时按 region filter rebalance/dca 行用
    """
    date: str
    fund: str
    action: str
    amount: str
    fund_codes: tuple[str, ...] = ()


def _resolve_fund_display(line: str) -> str:
    r"""把 `- 0xxxxx 基金名 ... → 0yyyyy 目标基金名 ...` 行解析成基金显示。

    模式识别：
    - 1 个 code + 名称 + 描述：显示 name
    - 2 个 codes（A → B）+"建仓 N 仓"：只显示 target (B) ——
      liubo 2026-10-17 反馈：A 只是从跟踪列表里删掉（成本 0 占位），不是 swap
    - 2 个 codes（A → B）"转换 NNNN 份"：不通过这里（_parse_conversion_rows 单独处理）
    - 2 个 codes（A → B）其他：显示 "<from_name> → <to_name>"（少见；转换行的兜底）
    - 3+ codes: 取前两个
    - 0 codes：fallback "基金 —"（理论上不会发生 — _parse_rebalance_line 提前过滤）

    数字用 \d{6} 6 位匹配（不强制 0 前缀，覆盖 519981/110017 等非 0 开头 code），
    lookbehind (?<!\d) 避免日期 2026-10 之类误命中。
    """
    codes = re.findall(r"(?<!\d)\d{6}", line)
    if not codes:
        return "基金 —"
    if len(codes) == 1:
        return get_fund_name(codes[0])
    # "建仓 N 仓" + "→" + 2 codes → 只显示 target（liubo 2026-10-17 反馈）
    if re.search(r"建仓\s*\d+\s*仓", line):
        return get_fund_name(codes[1])
    from_name = get_fund_name(codes[0])
    to_name = get_fund_name(codes[1])
    return f"{from_name} → {to_name}"


def _resolve_action(line: str, subject: str) -> str:
    """从 `- ` 行 + commit subject 推断动作标签。

    优先级（行内信号更具体）：
    1. 行内 "（... 转换 NNNN 份 ...）" → 转换（_parse_conversion_rows 单独处理）
    2. 行内 "建仓 N 仓" + ≥2 code → 加仓（liubo 2026-10-17 反馈：
       "建仓 N 仓 + → + 2 code" 不是 swap，是给 target 加仓；旧基金只是
       从跟踪列表里删掉（成本 0 占位），不显示 换基）
    3. 行内 "建仓 N 仓" 单 code → 建仓
    4. 行内 "+N 仓" → 加仓（要在 校准 前面 — `- 013127 ... +1 仓：25000 → 35000` 是加仓，不是校准）
    5. 行内 "−N 仓" / "-N 仓" → 减仓
    6. 行内 ":N → M" → 校准（_parse_rebalance_line 已过滤纯校准，这里只是 fallback）
    7. 行内以 "- 加 0xxxxx" 开头 → 加仓
    8. 行内以 "- 卖出 " 开头 → 卖出
    9. 行内以 "- 砍 " 开头 → 砍仓
    10. 行内 "赎回 " → 赎回
    11. 兜底：subject 关键字
    """
    # 行级优先
    if "转换" in line and re.search(r"\d+\s*份", line):
        return "转换"
    if re.search(r"建仓\s*\d+\s*仓", line):
        if "→" in line and len(re.findall(r"(?<!\d)\d{6}", line)) >= 2:
            # liubo 2026-10-17：这是给 target 加仓，不是换基
            # （旧基金 A 成本 0 占位，从跟踪列表里删掉就算处理掉了）
            return "加仓"
        return "建仓"
    # 加仓 / 减仓：先于 校准 检测（line 含 "+1 仓" 时是加仓，不是校准）
    if re.search(r"\+\s*\d+\s*仓", line):
        return "加仓"
    if re.search(r"[−-]\s*\d+\s*仓", line):
        return "减仓"
    # 校准：支持 ASCII : 和中文 ：（liubo 在 commit 里两种都用）
    if re.search(r"[:：]\s*\d[\d,]*\s*→\s*\d[\d,]*", line):
        return "校准"
    # liubo 拍板 "- 加 0xxxxx 基金名" 系列 → 加仓（常见于加仓 / 建仓混用的 commit）
    if re.search(r"-\s+加\s+(?:\d{6}|基金)", line):
        return "加仓"
    # 行内以 "- 卖出 " 开头 → 卖出
    if re.search(r"-\s+卖出\s", line):
        return "卖出"
    if re.search(r"-\s+砍\s", line):
        return "砍仓"
    if "赎回 " in line:
        return "赎回"

    # subject 兜底
    for kw, label in (
        ("实盘对账", "对账"),
        ("成本校准", "校准"),
        ("校准", "校准"),
        ("对账", "对账"),
        ("新建仓", "新建仓"),
        ("建仓", "建仓"),
        ("转换", "转换"),
        ("赎回 ", "赎回"),
        ("卖出 ", "卖出"),
        ("加仓 ", "加仓"),
        ("+1 仓", "加仓"),
        ("−1 仓", "减仓"),
        ("-1 仓", "减仓"),
        ("砍", "砍仓"),
        ("换 ", "换基"),
        ("加 0", "加仓"),
        ("加 ", "加仓"),
    ):
        if kw in subject:
            return label
    return "调仓"


def _format_cang(n: float) -> str:
    """格式化仓值：四舍五入到 1 位小数，整数不带 .0。

    liubo 2026-10-18 反馈 — 0.499939 仓太长了，0.5 仓 / 1 仓 / 1.5 仓就够用。

    规则：
    - 整数（round 后 == int）→ "1" / "2" / "10"（无小数）
    - 1 位小数 → "0.5" / "0.8" / "1.5"
    """
    rounded = round(n, 1)
    if rounded == int(rounded):
        return f"{int(rounded)}"
    return f"{rounded:.1f}"


def _format_amount_with_units(amount: str) -> str:
    """把 {N 仓} 或 {X 元} 表达标准化为 "N 仓 / X 元" 双值。

    liubo 2026-10-17 反馈「我都想要」 — 1 万 = 1 仓，要么都显示，不能只一个。
    "你不要只显示金额，也不要只显示仓位。都想"。

    公式：1 仓 = 1 万 CNY = 10,000 元（spec 022/097 整数仓位模型）。

    规则：
    - "—" / 空 → 保持
    - DCA 3-value 格式（"10 元 / 每天 / 累计 1800"）→ 保持（频率+目标，不在 仓 范畴）
    - "已买 ¥X" → 保持（DCA 兜底，¥X 是当前累计进度）
    - "净 +N 元" cost 校准（liubo 标的记账差）→ 保持（不是仓位概念）
    - "建仓 N 仓 / X 元"（已含两者）→ 标准化为 "N 仓 / X 元"（去 "建仓" + 换 CNY/¥ 为 元）
    - "±N 仓"（纯 仓）→ "N 仓 / X 元"（方向走 action 列，amount 不带 ±）
    - "X 元 / CNY / 万 / ¥X"（纯 元）→ "N 仓 / X 元"（前面补 仓数）

    仓值显示：四舍五入到 1 位小数（liubo 2026-10-18 反馈 — 0.499939 仓太长了）。
    整数仓值保留 "1 仓" 不带 ".0"。
    """
    if not amount or amount == "—":
        return amount
    # DCA 3-value 格式（含 " / 累计 "） → 不动
    if " / 累计 " in amount:
        return amount
    # 已买 ¥X（DCA 兜底）→ 不动
    if "已买" in amount:
        return amount
    # 净 +N 元 / 净 -N 元 (cost 校准，不是仓位) → 不动
    if "净" in amount and "仓" not in amount and ("元" in amount or "CNY" in amount or "¥" in amount):
        return amount
    # 已含两者（"建仓 1 仓 / 10000 CNY" / "0.5 仓 / 5000 元"）→ 标准化
    if "仓" in amount and re.search(r"\d", amount) and (
        re.search(r"\d[\d,.]*\s*(元|CNY|¥|￥|万)", amount) is not None
    ):
        # 标准化：去 "建仓" 词 + CNY 换成 "元"，数字加千分位逗号
        cleaned = re.sub(r"建仓\s*", "", amount)
        # 替换 "N\s*CNY" → "N,NNN 元"（千分位）
        def _add_thousands(m: re.Match[str]) -> str:
            n = float(m.group(1))
            if n == int(n):
                return f"{int(n):,} 元"
            return f"{n:,} 元"
        cleaned = re.sub(r"(\d+(?:\.\d+)?)\s*CNY", _add_thousands, cleaned)
        # 也对纯 "N 元" 加千分位（如果 N >= 1000）
        def _add_thousands_yuan(m: re.Match[str]) -> str:
            n = float(m.group(1))
            if n >= 1000 and n == int(n):
                return f"{int(n):,} 元"
            return m.group(0)
        cleaned = re.sub(r"(\d+)\s*元", _add_thousands_yuan, cleaned)
        # 仓值四舍五入到 1 位小数（liubo 2026-10-18 反馈 — 0.499939 仓太长了）
        def _round_cang_in_text(m: re.Match[str]) -> str:
            n = float(m.group(1))
            return f"{_format_cang(n)} 仓"
        cleaned = re.sub(r"(\d+(?:\.\d+)?)\s*仓", _round_cang_in_text, cleaned)
        # 压缩多余空格但保留单空格
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned
    # 纯 ±N 仓（"+1 仓" / "-1 仓" / "0.5 仓"）
    仓_match = re.match(r"^([+\-−])?\s*(\d+(?:\.\d+)?)\s*仓\s*$", amount)
    if 仓_match:
        n = float(仓_match.group(2))
        元 = n * 10000
        if 元 == int(元):
            元_str = f"{int(元):,}"
        else:
            元_str = f"{元:,.2f}"
        return f"{_format_cang(n)} 仓 / {元_str} 元"
    # 纯 X 元 / CNY / 万（数字在前）
    元_match = re.search(r"(\d+(?:\.\d+)?)\s*(元|万|CNY)", amount)
    if 元_match:
        n = float(元_match.group(1))
        unit = 元_match.group(2)
        if unit == "万":
            n = n * 10000
        仓 = n / 10000
        # 标准化原 amount：CNY → 元，千分位逗号对齐
        # 把 "10000" / "10,000" 规范成 "10,000"（带千分位）
        normalized_num = f"{int(n):,}" if n == int(n) else f"{n:,}"
        if unit == "万":
            normalized = f"{normalized_num} 元"  # 万 → 转成元
        else:
            normalized = f"{normalized_num} 元"
        return f"{_format_cang(仓)} 仓 / {normalized}"
    # 纯 ¥X / ￥X（数字在后）
    符号_match = re.search(r"[¥￥]\s*(\d+(?:\.\d+)?)", amount)
    if 符号_match:
        n = float(符号_match.group(1))
        仓 = n / 10000
        return f"{_format_cang(仓)} 仓 / ¥{int(n) if n == int(n) else n:g}"
    # 其他（含 "成本 20 收回现金" / "净 +N 元" / "净+N" 等特殊表达）→ 保持
    return amount


def _resolve_amount(line: str) -> str:
    """从 `- ` 行提取金额显示，最后过一道 _format_amount_with_units 标准化为 "N 仓 / X 元"。

    优先级：
    1. 括号里的金额描述（"已买 ¥60" / "+40" / "10000 元，转换 8379 份" / "成本 20 收回现金"）
       — 直接用括号片段（截断 30 字符避免列过宽）
    2. 行内的 "建仓 N 仓 = X CNY" / "±N 仓" / "¥X" / "X CNY" / "X 元"

    解析不到 → "—"（不编造）。
    """
    # 优先级 1：括号片段（liubo 总是把关键金额放在括号里）
    # 用 Chinese fullwidth parens
    paren_matches = re.findall(r"（([^（）]+)）", line)
    for seg in paren_matches:
        # 跳过 "（PE 19.69% < 20% 触发深价值）" 这种纯描述段
        is_pure_desc = any(kw in seg for kw in ("PE", "分位", "触发", "占位", "收回现金"))
        # 「成本 20 收回现金」算数据（"20" + "收回现金"）— 优先显示
        has_cost_kw = "成本" in seg and "收回" in seg
        has_money_signal = (
            re.search(r"[+\-−]?\s*\d", seg) is not None
            and any(kw in seg for kw in ("元", "¥", "￥", "CNY", "+", "-", "−", "已买", "份", "仓", "%", "成本", "净"))
        )
        if is_pure_desc and not has_cost_kw:
            continue
        if not has_money_signal:
            continue
        # 跳过 "（赚 N%）" 这种纯百分比描述
        if re.fullmatch(r"\s*[+\-−]?\s*\d[\d,.]*\s*%\s*", seg):
            continue
        cleaned = seg.strip()
        # 截断 30 字符（飞书 table 列宽有限）
        return _format_amount_with_units(cleaned[:30])

    # 优先级 2：行内 "建仓 N 仓 = X CNY" 格式
    build_match = re.search(
        r"建仓\s*(\d+)\s*仓(?:\s*=\s*([+\-−]?\s*\d[\d,]*(?:\.\d+)?\s*(?:元|万|CNY|¥|￥)?))?",
        line,
    )
    if build_match and build_match.group(2):
        return _format_amount_with_units(
            f"建仓 {build_match.group(1)} 仓 / {build_match.group(2).strip()}"
        )

    # 优先级 3：行内 ±N 仓（加减仓）
    posn_match = re.search(r"([+\-−])\s*(\d+)\s*仓", line)
    if posn_match:
        sign = "+" if posn_match.group(1) == "+" else "-"
        return _format_amount_with_units(f"{sign}{posn_match.group(2)} 仓")

    # 优先级 4：行内 ¥/CNY/元 数字
    money_match = re.search(r"[¥￥]\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:元|万|CNY))?", line)
    if money_match:
        return _format_amount_with_units(money_match.group(0).strip())
    cny_match = re.search(r"\d[\d,]*(?:\.\d+)?\s*CNY", line)
    if cny_match:
        return _format_amount_with_units(cny_match.group(0).strip())

    return "—"


def _parse_rebalance_line(line: str, subject: str) -> RebalanceRow | None:
    """单条 `- 0xxxxx ...` 行 → RebalanceRow。无 code 也不在白名单 → None。

    行必须以 `- ` 开头 + 紧跟 6 位基金代码（不一定以 0 开头 — 519981/110017/160422 等也合法），
    否则视为注释/标题行（跳过）。

    文档行过滤（commit body 描述代码改动时也用 `- ` 格式）：
    - 行首不是 6 位 code（如 `- cost_basis.py：...`）→ 描述代码改动，跳过
    - 代码被引号包裹（如 `"000698"` / `'399673'`）→ 描述上下文，跳过
    - 代码后跟着 Chinese fund name（"平安中短债"）→ 真实调仓
    - 代码后跟着 ` → date(...)` 或变量名 → 文档上下文，跳过
    """
    stripped = line.strip()
    if not stripped.startswith("- "):
        return None
    # 6 位数字基金代码 — 不用前缀 0（519981/110017/160422 等非 0 开头的也合法）
    code_positions = [m.start() for m in re.finditer(r"(?<!\d)\d{6}", stripped)]
    if not code_positions:
        return None
    # 行首必须是 6 位 code（"- 0xxxxx 基金名 ..."）— 排除 "- cost_basis.py：..." 这种代码改动描述
    if code_positions[0] != 2:  # "- " 占 2 字符
        return None
    # 行内 ≥2 个 code 时，第二个 code 必须也在 FUND_NAME_BY_CODE — 排除 "fund → index" 描述
    # 例：`- 011612 华夏科创50ETF联接A → 000688 科创 50`（000688 是指数代码，非基金）
    if len(code_positions) >= 2:
        codes_in_line = [m.group() for m in re.finditer(r"(?<!\d)\d{6}", stripped)]
        if codes_in_line[1] not in FUND_NAME_BY_CODE:
            return None
    # 过滤：被引号包裹的 6 位数字（描述上下文，不是真实调仓）
    for pos in code_positions:
        before = stripped[max(0, pos - 1):pos]
        after_char = stripped[pos + 6:pos + 7]
        if before in ('"', "'", "`") or after_char in ('"', "'", "`"):
            return None
    # 过滤：代码后没 Chinese 字符 + 没另一个 6 位数字（不像基金描述）
    has_fund_name = False
    for pos in code_positions:
        # 检查 code 后 ~20 字符内是否有 Chinese 字符
        tail = stripped[pos + 6:pos + 30]
        if re.search(r"[\u4e00-\u9fff]", tail):
            has_fund_name = True
            break
        # 如果有另一个 6 位数字（→ 后跟另一只基金），也算 fund context
        if re.search(r"(?<!\d)\d{6}", tail):
            has_fund_name = True
            break
    if not has_fund_name:
        return None

    # 过滤：所有 6 位数字都不在 FUND_NAME_BY_CODE — 是指数代码/版本号/日期等描述上下文
    # 例：`补 test_... "000698" / "399673" → date(2026, 10, 9)` 这种行
    # 000698/399673 是 ETF 跟踪的指数代码（不在 fund 表里），不是真实调仓
    actual_fund_codes = [m.group() for m in re.finditer(r"(?<!\d)\d{6}", stripped)
                         if m.group() in FUND_NAME_BY_CODE]
    if not actual_fund_codes:
        return None

    # 过滤：纯成本校准行（"004827: 22000 → 2000（-20000）" / "378006 ...：3910 → 5310（+1400）"）
    # 不计入调仓 — liubo 2026-10-17 反馈 — 这是"原来记错了，我纠正你了"，
    # 属于记账对账，不是调仓。
    #
    # 区分 DCA（保留）和 校准（跳过）的关键：
    # - DCA:  `016452 南方纳指 100：20 → 60（已买 ¥60）` — paren 里写 "已买 ¥X"
    # - 校准: `378006 摩根全球新兴市场：3910 → 5310（+1400）` — paren 里只写 ±N 元
    # - 校准: `004827: 22000 → 2000（-20000，分给 019857/023414 各 10000）` — ASCII : 风格 ledger
    #
    # 统一判别：行匹配 "code + Chinese 名称（可选）+ N → M（±X 元）" 且无 "已买" / "建仓 N 仓"
    if re.search(r"\d{6}\s*[:：]?\s*[\u4e00-\u9fff][^：:]*[:：]\s*\d[\d,]*\s*→\s*\d[\d,]*\s*（", stripped) \
            and "已买" not in stripped and "仓" not in stripped:
        return None
    # 9e70495 风格 ledger: code 直接接 ASCII 冒号（无 Chinese fund name）
    if re.match(r"^-\s+\d{6}\s*:\s*\d", stripped):
        return None

    # 第三十轮（liubo 2026-10-18 反馈）：过滤 commit body 描述行（不是真调仓）
    # 3 类描述行被误判为调仓：
    # 1. "X 保留（占位 0）" — 描述"保留占位"，不是新建仓
    #    例：- 011612 华夏科创50ETF联接A 保留（占位 0）
    #    subject 含 "+1 仓" 关键词 → _is_rebalance_subject 通过，但 body 这行只是描述
    # 2. "X 漏在...清单外" — fix commit 描述漏加，subject 含"加 X" 触发误判
    #    例：- 014673 富国中证港股通互联网ETF发起式联接A 漏在大类资产配置清单外，
    # 3. "X / Y / Z / W 这 N 只..." — 描述性列表（不是单笔调仓）
    #    例：- 022448 / 007997 / 023917 / 025682 这 4 只是单独的"红利策略组合"，
    if (
        ("保留" in stripped and "占位" in stripped)
        or re.search(r"占位\s*0", stripped) is not None
        or re.search(r"漏在|漏到|清单外|清单里", stripped) is not None
        or (
            re.search(r"这\s*\d+\s*只", stripped) is not None
            and len(re.findall(r"(?<!\d)\d{6}", stripped)) >= 2
        )
    ):
        return None

    # DCA (定投) 检测 — 行里有 "已买 ¥X" → 标 "定投"（不是 "校准"）
    # liubo 2026-10-17 反馈 — 南方纳100 / 摩根纳指100 / 万家纳指100 是 DCA，不是校准
    line_codes = re.findall(r"(?<!\d)\d{6}", stripped)
    if "已买" in stripped:
        # 转换拆 2 行（liubo 2026-10-17 反馈 — 转出一笔 + 转入一笔分开）
        if _is_conversion_line(stripped):
            return _parse_conversion_rows(stripped)
        action = "定投"
        fund = _resolve_fund_display(stripped)
        # liubo 2026-10-17 反馈 — 金额列显示 3 个值：定投额 / 频率 / 总目标，
        # 不写「已买 ¥X」（已经买入了多少他不关心）。
        # 数据从 _DCA_SCHEDULE_BY_CODE 拿（cost_basis.py 注释里的 DCA 设定）。
        if line_codes and line_codes[0] in _DCA_SCHEDULE_BY_CODE:
            amount = _format_dca_amount(_DCA_SCHEDULE_BY_CODE[line_codes[0]])
        else:
            # 兜底：基金不在 DCA mapping → 用 commit body 的金额（避免信息丢失）
            amount = _resolve_amount(stripped)
        return RebalanceRow(
            date="",
            fund=fund,
            action=action,
            amount=amount,
            fund_codes=tuple(line_codes),
        )

    # 转换也拆 2 行（DCA 的转换行也走这里）
    if _is_conversion_line(stripped):
        return _parse_conversion_rows(stripped)

    fund = _resolve_fund_display(stripped)
    action = _resolve_action(stripped, subject)
    amount = _resolve_amount(stripped)
    return RebalanceRow(
        date="",  # 由 caller 填
        fund=fund,
        action=action,
        amount=amount,
        fund_codes=tuple(line_codes),
    )


def _is_conversion_line(stripped: str) -> bool:
    """判断是否是 转换 行（"004827 平安中短债 → 019857 博时...（10000 元，转换 8379 份）"）。

    转换信号：
    - 行内 "转换 NNNN 份" 在 parens 里
    - 行内 ≥2 个 fund code + "→" 隔开
    """
    if "转换" in stripped and re.search(r"\d+\s*份", stripped):
        return True
    if "→" in stripped and len(re.findall(r"(?<!\d)\d{6}", stripped)) >= 2 and "转换" in stripped:
        return True
    return False


def _parse_conversion_rows(stripped: str) -> list[RebalanceRow]:
    """把 转换 行拆成 转出 + 转入 两行。

    例：`- 004827 平安中短债 → 019857 博时上证科创板100ETF联接A（10000 元，转换 8379 份）`
    → [
        RebalanceRow(fund="平安中短债", action="转出", amount="10000 元"),
        RebalanceRow(fund="博时上证科创板100ETF联接A", action="转入", amount="10000 元"),
    ]

    金额从 parens 里取 "10000 元" 部分（截断在 "，" / "转换" 之前）。
    """
    codes = re.findall(r"(?<!\d)\d{6}", stripped)
    if len(codes) < 2:
        # 不应该发生 — _is_conversion_line 已经确保 ≥2 个 code
        from_name = get_fund_name(codes[0]) if codes else "基金 —"
        to_name = "基金 —"
    else:
        from_name = get_fund_name(codes[0])
        to_name = get_fund_name(codes[1])

    # 金额：paren 里的 "10000 元" 部分
    amount = "—"
    paren_match = re.search(r"（([^（）]+)）", stripped)
    if paren_match:
        seg = paren_match.group(1)
        # 优先取 "10000 元" 这种金额描述
        money_match = re.search(r"\d[\d,]*(?:\.\d+)?\s*(?:元|万|CNY|¥|￥)", seg)
        if money_match:
            # 转换行也要走标准化 — liubo 2026-10-17 反馈「我都想要」：
            # 转出 10000 元 → "1 仓 / 10000 元"（方向走 action 列 "转出"）
            amount = _format_amount_with_units(money_match.group(0).strip())

    return [
        RebalanceRow(date="", fund=from_name, action="转出", amount=amount, fund_codes=(codes[0],) if codes else ()),
        RebalanceRow(date="", fund=to_name, action="转入", amount=amount, fund_codes=(codes[1],) if len(codes) >= 2 else ()),
    ]


def _collect_rebalance_rows(limit: int = 20) -> list[RebalanceRow]:
    """从 git log 抓所有调仓/定投行 — 调仓记录 + 定投记录 共用。

    liubo 2026-10-17 拍板 — 调仓和定投分开 2 张表，但数据源都是 git log，
    共用一次 git log 调用（节省 ~50ms + 避免时序不一致）。

    返回：list[RebalanceRow]，按 action 自动分布：
    - action != "定投" → 调仓记录表
    - action == "定投" → 定投记录表

    为什么用 `%x00|<hash>|%x00END%x00` 包裹每条 commit：
    git log 的 `%b` 在 body 末尾加换行但不加分隔符，所以用纯 `\x00` 拆 4-field
    结构会偏移（因为 body 内部可能含 `\n`）。用 START/END 包裹后 split('\x00')
    切片能精确还原每个 commit 的字段。
    """
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parents[3]),
             "log", "--pretty=format:%x00REC%x00%H%x00%ai%x00%s%x00%b%x00END%x00",
             f"-{limit * 4}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return []

    commits: list[dict[str, str]] = []
    for block in proc.stdout.split("\x00REC\x00"):
        # block 形如 "<hash>\0<date>\0<subject>\0<body>\0END\0\n"
        # 第一个 block 是空字符串（最前面有 \x00REC），跳过
        if not block:
            continue
        parts = block.split("\x00")
        # 期望结构: hash, date, subject, body, "END", trailing
        if len(parts) < 5:
            continue
        if parts[4] != "END":
            # 没找到 END 标记 → 不是合法 commit block（可能截断）
            continue
        commits.append({
            "hash": parts[0],
            "date": parts[1],
            "subject": parts[2],
            "body": parts[3],
        })
        if len(commits) >= limit * 4:
            break

    if not commits:
        return []

    rows: list[RebalanceRow] = []
    for commit in commits:
        subject = commit["subject"]
        if not _is_rebalance_subject(subject):
            continue
        date_short = commit["date"][:10]
        body = commit["body"]

        # 每条 `- 0xxxxx ...` 行 → 1 row
        # 跨组合过滤（按行）：spec 022 card 只展示 global-allocation 调仓；ETF 轮动 /
        # 红利策略 / 其他组合的行跳过（liubo 2026-10-17 反馈 — 三个组合分开管理）
        body_rows: list[RebalanceRow] = []
        for line in body.splitlines():
            parsed = _parse_rebalance_line(line, subject)
            if parsed is None:
                continue
            # 转换行拆 2 行（返回 list）vs 普通行返回 1 个 RebalanceRow
            rows_from_line = parsed if isinstance(parsed, list) else [parsed]
            # 跨组合过滤 — 按首个 fund code 判定
            line_codes = re.findall(r"(?<!\d)\d{6}", line)
            if line_codes and line_codes[0] in _CROSS_PORTFOLIO_EXCLUDE_CODES:
                continue
            body_rows.extend(rows_from_line)

        # 没有任何 `- ` 行 → fallback 用 subject 解析出 1 row（避免漏掉 subject-only commit）
        if not body_rows:
            fallback_fund, fallback_action, fallback_amount = _parse_subject_fallback(subject)
            # fallback 也找不到具体基金 code（subject 是描述/fix 而非真调仓）→ 跳过
            # 例：`fix(022): 补 2026-10-09 新建仓 2 个 ETF ...` — 描述历史动作的 fix，不是新调仓
            if not re.search(r"(?<!\d)\d{6}", fallback_fund):
                continue
            body_rows.append(
                RebalanceRow(
                    date="",
                    fund=fallback_fund,
                    action=fallback_action,
                    amount=fallback_amount,
                    fund_codes=tuple(re.findall(r"(?<!\d)\d{6}", fallback_fund)),
                )
            )

        # 同一 commit 多行 → 补日期 + 加进 rows
        for rb in body_rows:
            # 校准 + amount="—" 是 noise（self-referential commit body 描述实现）—
            # 真实校准一定有金额差值（"+21" / "+1400" 等），无金额 = 误判
            if rb.action == "校准" and rb.amount in ("—", ""):
                continue
            rb_with_date = RebalanceRow(
                date=date_short,
                fund=rb.fund,
                action=rb.action,
                amount=rb.amount,
                fund_codes=rb.fund_codes,
            )
            rows.append(rb_with_date)
            if len(rows) >= limit:
                break
        if len(rows) >= limit:
            break

    # 去重：同一 (date, fund, amount) 出现「转入」+「加仓」两条时，保留「转入」删「加仓」。
    # 背景（liubo 2026-10-18 反馈）：2026-10-09 的 019857/013127/023414 各被记了 2 次 ——
    # 「转入」来自 spec 022 conversion ledger（006829 → 019857 等）
    # 「加仓」来自高层 decision commit（"建仓 1 仓 = 10000" / "+1 仓: 25000→35000"）
    # 描述同一笔实际调仓。优先保留「转入」（带 source，更完整）。
    transfer_keys: set[tuple[str, str, str]] = {
        (r.date, r.fund, r.amount) for r in rows if r.action == "转入"
    }
    rows = [
        r
        for r in rows
        if not (r.action == "加仓" and (r.date, r.fund, r.amount) in transfer_keys)
    ]

    return rows


def _build_rebalance_records_section(
    rows: list[RebalanceRow],
    region: SwensenClass | None = None,
) -> list[dict[str, object]]:
    """Section 3 — 调仓纪录表格版（liubo 2026-10-17 改：4 列 = 日期/基金/动作/金额）。

    liubo 2026-10-17 第二轮反馈 — 调仓是调仓，定投是定投，分开 2 张表。
    本函数只渲染"调仓"行（action != "定投"），定投行由 _build_dca_records_section 渲染。

    第三十五轮（liubo 2026-10-18）：region != None 时按 fund_codes 过滤 —
    - 行涉及的任意 fund_code 在 SUBCLASS_BY_CODE 里属于 region，则入选
    - 转换行的 转出/转入 可能属于不同 region；只要 source 或 target 之一属于 region 就入选
    """
    from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE

    def _in_region(r: RebalanceRow) -> bool:
        if region is None:
            return True
        if not r.fund_codes:
            return False  # 没解析到 code → 默认排除（保守）
        return any(SUBCLASS_BY_CODE.get(c) == region for c in r.fund_codes)

    rebalance_rows = [r for r in rows if r.action != "定投" and _in_region(r)]
    if not rebalance_rows:
        return []

    rows_dict = [
        {"date": r.date, "fund": r.fund, "action": r.action, "amount": r.amount}
        for r in rebalance_rows
    ]
    table: dict[str, object] = {
        "columns": [
            {"name": "date", "display_name": "日期", "data_type": "text", "width": "auto"},
            {"name": "fund", "display_name": "基金", "data_type": "text", "width": "auto"},
            {"name": "action", "display_name": "动作", "data_type": "text", "width": "auto"},
            {"name": "amount", "display_name": "金额", "data_type": "text", "width": "auto"},
        ],
        "rows": rows_dict,
    }

    return [
        {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": "调仓纪录"}],
        },
        {"tag": "hr"},
        {
            "tag": "table",
            **table,
        },
    ]


def _build_dca_records_section(
    rows: list[RebalanceRow],
    region: SwensenClass | None = None,
) -> list[dict[str, object]]:
    """Section 4 — 定投纪录表格版（liubo 2026-10-17 第二轮反馈 — 跟调仓分开 2 张表）。

    5 列：日期 / 基金 / 动作 / 金额 / 状态
    - 动作固定 "定投"
    - 金额 = "X 元 / Y / 累计 Z" 3 段（per / cycle / total），从 _DCA_SCHEDULE_BY_CODE 拿
    - 状态 = "进行中" / "到期" / "终止"（liubo 2026-10-18 拍板加状态字段）
    - 始终展示 _DCA_SCHEDULE_BY_CODE 全部 5 只基金（liubo 2026-10-17 第二十五轮反馈 — 不管
      git log 是否有 "定投" commit，黄金 + DAX 也要展示），日期从 _DCA_PLAN_START_BY_CODE 拿
    - `rows` 参数保留以备未来用（目前没有 git log 行需合并到 DCA 表，因为 DCA 不走 commit body）

    第三十五轮（liubo 2026-10-18）：region != None 时按 code 的 SUBCLASS 过滤
    - 5 只 DCA 基金里，黄金 (000216) 是 COMMODITY；A 股 1 只 014532，DAX (000614) 是 FOREIGN_DM
    - A 股 card 应只有 014532 美股 card 应只有 NDX DCA 3 只 (016452/019172/019441)
    - 港股 card 无 DCA（恒生科技 013127 不在 _DCA_SCHEDULE 里）

    为什么不依赖 git log：
    - git log 只在 校准 事件里手动加了 "已买 ¥X" 行（numpy column 当时想统计"已买多少"）
    - 但 DCA 的本体不是「买」而是「自动定投计划」，天天基金后台每天扣款，commit 注释跟 DCA 无关
    - 真实数据源是 liubo 在 2026-10-17 提供的天天基金 DCA 计划详情截图
    - 因此 DCA 表是「持仓里所有 DCA 计划的快照」，跟调仓表（git log 历史事件）语义不同
    """
    from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE

    if not _DCA_SCHEDULE_BY_CODE:
        return []

    rows_dict: list[dict[str, str]] = []
    # 按 code 排序，输出稳定（016452 → 000614 → 000216 → 019172 → 019441）
    for code in sorted(_DCA_SCHEDULE_BY_CODE.keys()):
        if region is not None and SUBCLASS_BY_CODE.get(code) != region:
            continue
        schedule = _DCA_SCHEDULE_BY_CODE[code]
        fund_name = get_fund_name(code)
        date = _DCA_PLAN_START_BY_CODE.get(code, "—")
        rows_dict.append({
            "date": date,
            "fund": fund_name,
            "action": "定投",
            "amount": _format_dca_amount(schedule),
            "status": _compute_dca_status(code, schedule),
        })

    table: dict[str, object] = {
        "columns": [
            {"name": "date", "display_name": "日期", "data_type": "text", "width": "auto"},
            {"name": "fund", "display_name": "基金", "data_type": "text", "width": "auto"},
            {"name": "action", "display_name": "动作", "data_type": "text", "width": "auto"},
            {"name": "amount", "display_name": "金额", "data_type": "text", "width": "auto"},
            {"name": "status", "display_name": "状态", "data_type": "text", "width": "auto"},
        ],
        "rows": rows_dict,
    }

    return [
        {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": "定投纪录"}],
        },
        {"tag": "hr"},
        {
            "tag": "table",
            **table,
        },
    ]


def _parse_subject_fallback(subject: str) -> tuple[str, str, str]:
    """commit body 没有 `- ` 行时的 fallback — 直接从 subject 解析。

    与旧版 _parse_rebalance_row 等价，但用 fund_name 而非 code。保留向后兼容：
    - subject-only commit（如很老的 commit）也能渲染
    - subject 含 "校准 14 只基金" 但没具体 code → 显示 fallback
    """
    codes = re.findall(r"(?<!\d)\d{6}", subject)
    if codes:
        fund = " / ".join(get_fund_name(c) for c in codes)
    else:
        # 没 code 时显示 subject 摘要的前 30 字
        fund = subject[:30] + ("..." if len(subject) > 30 else "")
    action = "调仓"  # 没有足够信号时用通用 "调仓"
    action_patterns = [
        ("新建仓", "新建仓"), ("成本校准", "成本校准"), ("实盘对账", "实盘对账"),
        ("校准", "校准"), ("对账", "对账"), ("转换", "转换"), ("建仓", "建仓"),
        ("加仓 ", "加仓"), ("+1 仓", "加仓"),
        ("−1 仓", "减仓"), ("-1 仓", "减仓"),
        ("赎回 ", "赎回"), ("卖出 ", "卖出"), ("砍", "砍仓"),
        ("加 0", "加仓"), ("加 ", "加仓"), ("换 ", "换基"),
    ]
    for pat, label in action_patterns:
        if pat in subject:
            action = label
            break

    # 金额
    amount_match = re.search(r"[¥￥]\s?\d[\d,]*(?:\.\d+)?\s?[元万]?", subject)
    amount = amount_match.group(0).strip() if amount_match else "—"

    return fund, action, amount


# ─── Per-fund 估值指标策略（spec 098 第二十七轮）───
#
# 不同指数的估值方法不一样（liubo 2026-09-19 讨论后确定）：
# - dividend（红利类）：看股息率加权 PE 分位（来源：银行螺丝钉），不用普通 PE 分位
# - broad（宽基）：看普通 PE 分位
# - growth（成长/小盘）：看普通 PE 分位 + ROE 同比（盈利是否在涨）
#
# 为什么不用统一阈值：
# - 红利低波 100：PE 常年 8-12，普通 PE 分位 80% 是"它常态"，要看股息率加权 PE
# - 科创创业 50：PE 天然 40+，股息率永远 0.5-1%（两个指标都失真），用 ROE 同比补
# - 中证 1000：同 科创创业50，PE 偏高股息率偏低
# - 中证 A50 / A500：宽基，PE 分位有效

INDEX_VERDICT_STRATEGY: dict[str, str] = {
    # A 股指数（spec 098 第二十七轮）
    "930050": "broad",     # 中证 A50
    "000510": "broad",     # 中证 A500
    "931643": "growth",    # 科创创业 50
    "000852": "growth",    # 中证 1000
    # liubo 2026-10-09 新增 3 个科创/创业类（跟 931643/000852 同档 growth）
    "000688": "growth",    # 科创 50
    "000698": "growth",    # 科创 100
    "399673": "growth",    # 创业板 50
    "930955": "dividend",  # 红利低波 100
    # 港股指数（spec 098.2 — liubo 2026-09-19）
    "HSSCHKY": "dividend",  # 恒生港股通高股息率（看股息率 + 股息率加权 PE 分位）
    "930792": "dividend",    # HK 银行（高分红，看股息率）— 理杏仁 CSV 数字代码
    "HSTECH": "growth",      # 恒生科技（成长股，看 PE + ROE 同比）
    # 美股指数（spec 098.3 — liubo 2026-09-20）
    # CSV 里理杏仁没给 NDX 估值（PE/股息率全空），所以 NDX 实际不会触发 —
    # 仍保留映射以备以后理杏仁补数据；当前只 INX/GSPC/OEX 真正命中。
    "INX":  "growth",      # 标普 500（美股基准 = 总市值加权，跟 A 股 broad 接近）
    "GSPC": "growth",      # 标普 500（lixinger 备用 code，跟 INX 等价）
    "OEX":  "dividend",    # 标普 100（大蓝筹，高分红）
    "NDX":  "growth",      # 纳指 100（成长股）— 当前 CSV 无数据，保留占位
}


def _compute_roe_yoy(fv: FundValuation) -> Decimal | None:
    """ROE 同比 = (ROE 最新 - ROE 去年同期) / ROE 去年同期。

    返回 fraction（如 +0.10 = 同比 +10%）。ROE 任一缺失或去年为 0 → None。
    """
    if fv.roe_latest is None or fv.roe_year_ago is None:
        return None
    if fv.roe_year_ago == 0:
        return None
    return (fv.roe_latest - fv.roe_year_ago) / fv.roe_year_ago


def _per_fund_verdict(fv: FundValuation, strategy: str) -> str:
    """单只 A 股基金的估值判断（5 档打分制 — liubo 2026-09-19 反馈"分位 1-5 打分"）。

    按指数类型选不同的估值信号：
    - dividend（红利类）：股息率加权 PE 分位，fallback 到普通 PE 分位
    - broad（宽基）：普通 PE 分位
    - growth（成长/小盘）：普通 PE 分位 + ROE 同比修正

    返回："<label> <score>"，如 "极低估 1" / "低估 2" / "正常 3" / "高估 4" / "极高估 5" / "数据缺失"

    阈值（与 4 指标估值表 PE 分位的 5 档一致）：
    - PE 分位 < 10%  → 极低估 (1)
    - 10%-30%       → 低估 (2)
    - 30%-70%       → 正常 (3)
    - 70%-90%       → 高估 (4)
    - ≥90%          → 极高估 (5)

    growth 策略额外调整（ROE 同比）：
    - ROE 同比 ≤ -10% 且 score ≥ 4 → 升到 "极高估 5"（盈利下滑 + PE 中高位 = 真贵）
    - ROE 同比 ≥ +10% 且 score == 5 → 降到 "高估 4"（盈利大涨，PE 高但合理）
    """
    # 选主信号 PE 分位
    if strategy == "dividend":
        pct = fv.pe_percentile_dy_weighted if fv.pe_percentile_dy_weighted is not None else fv.pe_percentile
    else:
        pct = fv.pe_percentile
    if pct is None:
        return "数据缺失"

    # 基础 5 档判断（PE 分位阈值，跟 4 指标表 PE 分位阈值一致）
    if pct < Decimal("0.10"):
        label, score = "极低估", 1
    elif pct < Decimal("0.30"):
        label, score = "低估", 2
    elif pct < Decimal("0.70"):
        label, score = "正常", 3
    elif pct < Decimal("0.90"):
        label, score = "高估", 4
    else:
        label, score = "极高估", 5

    # growth 策略：ROE 同比辅助
    if strategy == "growth":
        roe_yoy = _compute_roe_yoy(fv)
        if roe_yoy is not None and roe_yoy <= Decimal("-0.10") and score >= 4:
            # 盈利下滑 + PE 中高位 → 真贵
            return "极高估 5"
        if roe_yoy is not None and roe_yoy >= Decimal("0.10") and score == 5:
            # 盈利大涨 + 极高 PE → 降到高估（不是极高估）
            return "高估 4"

    return f"{label} {score}"


def _per_fund_recommendation(per_fund_verdict: str) -> str:
    """根据单只基金的估值判断给具体动作建议（spec 098 第二十八轮 — liubo 反馈"建议不要看整体超配"）。

    决策：
    - score 1-2 (极低估 / 低估) → 买入
    - score 3 (正常) → 持有
    - score 4-5 (高估 / 极高估) → 止盈
    - 数据缺失 → "—"

    注意：跟整体 A 股策略无关。每只基金独立判断。仓位纪律由独立的"A 股占比 vs 目标"note 提示。
    """
    if per_fund_verdict == "数据缺失":
        return "—"
    # 解析 "label score" 格式
    parts = per_fund_verdict.split()
    if len(parts) < 2:
        return "—"
    try:
        score = int(parts[-1])
    except ValueError:
        return "—"
    if score <= 2:
        return "买入"
    if score == 3:
        return "持有"
    return "止盈"  # 4 or 5


def _build_fund_valuation_section(
    journal: PortfolioJournal,
    over_target: bool = False,
    fund_subclasses: list[SwensenClass] | None = None,
) -> list[dict[str, object]]:
    """单个 region 的基金估值 + 仓位管理合并表（spec 098 第二十七轮 + 第二十九轮 — 两表合并）。

    spec 098.2 扩展：参数化 fund_subclasses 让 A 股 / 港股能复用同一个 per-fund 表逻辑。
    默认值 [CN_EQUITY] 保持向后兼容（旧调用方 + 测试）。

    列设计（10 列）：
    - 基金 / 指数 / PE / PE分位 / 股息率 / ROE同比 / 评估 / 建议 / 当前仓位 / 加减仓建议
    - "PE分位" 列按 strategy 切换：dividend → 股息率加权 PE 分位，broad/growth → 总市值加权 PE 分位
    - "股息率" 和 "ROE同比" 按 strategy 显示：
      - dividend：股息率有值，ROE同比 = "—"
      - broad：股息率有值，ROE同比 = "—"
      - growth：股息率 = "—"，ROE同比有值
    - "当前仓位" 列 = 市值 / 1 仓（1 仓 = 1w CNY），格式 "X.Y仓"
    - "加减仓建议" 列 = "+1仓" / "−1仓" / "—"
      - 买入（低估 1-2）：
        - 整体超配 → "—"（仓位纪律优先）
        - 当前仓位 >= 6 仓（已达上限）→ "—"（已达仓位上限）
        - 否则 → "+1仓"
      - 止盈（高估 4-5）：
        - 当前仓位 <= 1 仓（只剩底仓）→ "—"（底仓不动）
        - 否则 → "−1仓"
      - 持有 / 数据缺失 → "—"

    数据来源：
    - 基金列表 = journal.compute_holdings() 里 fund_subclasses 命中的基金
    - 估值数据 = journal.db.list_latest_fund_valuations_for_codes([...])

    渲染规则：
    - 该 region 没有任何基金持仓 → 不渲染整段（避免空 table）
    - 用户有持仓但全部缺估值 → 仍渲染（行 = "数据缺失"），让用户知道 CSV 还没灌
    """
    target_subclasses = fund_subclasses if fund_subclasses is not None else [SwensenClass.CN_EQUITY]
    target_subclass_set = set(target_subclasses)
    holdings = journal.compute_holdings()
    region_funds = [
        h for h in holdings
        if (sub := get_subclass(h.fund.code)) is not None and sub in target_subclass_set
    ]
    if not region_funds:
        # 第二十二轮（liubo 2026-10-17）fallback：holdings 为空时，
        # 直接从 fund_valuations 表 + SUBCLASS_BY_CODE 拿有估值的基金渲染 per-fund 表
        # 数据源：fund_valuations（PE/PE分位/股息率/ROE 同比）+ breakdown.SUBCLASS_BY_CODE（子类）
        # 当前仓位列 = "数据缺失"（无 holdings 数据）→ adjust 列 = "—"
        return _build_fund_valuation_section_fallback(
            journal, target_subclass_set,
        )

    fund_codes = [h.fund.code for h in region_funds]
    fvs_by_code = journal.db.list_latest_fund_valuations_for_codes(fund_codes)

    rows: list[dict[str, object]] = []
    for h in region_funds:
        code = h.fund.code
        fv = fvs_by_code.get(code)
        # 没估值数据 → fund_index_code 未知，按 broad 走 fallback
        idx_code = fv.index_code if fv is not None else ""
        strategy = INDEX_VERDICT_STRATEGY.get(idx_code, "broad")

        # 当前仓位（市值 / 1 仓）
        mv = h.market_value
        position = (mv / POSITION_UNIT) if mv is not None else None
        position_str = f"{position:.1f}仓" if position is not None else "数据缺失"

        if fv is None:
            rows.append(_empty_fund_row(code, h.fund.name, "—", position_str, "—"))
            continue

        verdict = _per_fund_verdict(fv, strategy)
        advice = _per_fund_recommendation(verdict)
        # 加减仓建议（spec 098 第三十轮 — 加底仓 + 仓位上限约束）
        adjust_str = _compute_adjust_str(advice, position, over_target)
        rows.append(_format_fund_row(
            code, h.fund.name, fv, strategy, verdict, advice, position_str, adjust_str,
        ))

    columns = _fund_columns()
    table: dict[str, object] = {
        "columns": columns,
        "rows": rows,
    }

    return [
        {"tag": "hr"},
        {"tag": "table", **table},
    ]


def _build_fund_valuation_section_fallback(
    journal: PortfolioJournal,
    target_subclass_set: set[SwensenClass],
) -> list[dict[str, object]]:
    """holdings 为空时，从 fund_valuations 表直接读 + SUBCLASS_BY_CODE 分类（liubo 2026-10-17）。

    用 list_latest_fund_valuations_for_codes 传所有 cost_basis.py 已知的 52 只基金 code，
    拿到 fund_code → FundValuation 映射，再按 SUBCLASS_BY_CODE 过滤出该 region 的基金。

    fund_name 没存到 fund_valuations 表，用 fund_code 当显示（用户能认就行）。

    返回：[hr, table] 或 []（无 fund_valuations 数据时）。
    """
    from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE
    from global_allocation.portfolio.cost_basis import COST_BASIS_BY_CODE

    # 拿所有 cost_basis 已知基金的最新估值
    all_codes = list(COST_BASIS_BY_CODE.keys())
    fvs_by_code = journal.db.list_latest_fund_valuations_for_codes(all_codes)

    # 按 SUBCLASS_BY_CODE 过滤出该 region 的基金
    region_fvs: list[FundValuation] = []
    for code, fv in fvs_by_code.items():
        sub = SUBCLASS_BY_CODE.get(code)
        if sub is not None and sub in target_subclass_set:
            region_fvs.append(fv)

    if not region_fvs:
        return []

    # 按 fund_code 排序（稳定显示顺序）
    region_fvs.sort(key=lambda fv: fv.fund_code)

    rows: list[dict[str, object]] = []
    for fv in region_fvs:
        idx_code = fv.index_code or ""
        strategy = INDEX_VERDICT_STRATEGY.get(idx_code, "broad")
        verdict = _per_fund_verdict(fv, strategy)
        advice = _per_fund_recommendation(verdict)
        # fallback 没市值数据 → position="数据缺失"，adjust="—"
        rows.append(_format_fund_row(
            fv.fund_code, fv.fund_code,  # name 字段没存到表，用 code 顶上
            fv, strategy, verdict, advice, "数据缺失", "—",
        ))

    columns = _fund_columns()
    table: dict[str, object] = {
        "columns": columns,
        "rows": rows,
    }
    return [
        {"tag": "hr"},
        {"tag": "table", **table},
    ]


# ─── 仓位单位 + 仓位上限（spec 098 第二十八轮 + 第三十轮 — liubo 反馈"1w = 1 仓，底仓 1 仓 + 最多加 5 仓"）───

POSITION_UNIT = Decimal("10000")  # 1 仓 = 1w CNY
POSITION_BASE = Decimal("1")  # 每只基金底仓 1 仓（不动）
POSITION_MAX_ABOVE_BASE = Decimal("5")  # 底仓之上最多加 5 仓
POSITION_MAX = POSITION_BASE + POSITION_MAX_ABOVE_BASE  # 总仓上限 = 6 仓


def _compute_adjust_str(
    advice: str,
    position: Decimal | None,
    over_target: bool,
) -> str:
    """根据 per-fund 建议 + 当前仓位 + 整体超配状态 计算"加减仓建议"列。

    spec 098 第三十轮 — liubo 反馈：
    - 每只基金有 1 仓底仓（不动）
    - 底仓之上最多加 5 仓（总仓上限 6 仓）
    - 买入 + 整体超配 → "—"（仓位纪律优先）
    - 买入 + 已达上限 → "—"（仓位已满）
    - 买入 + 还有空间 → "+1仓"
    - 止盈 + 已到仅底仓 → "—"（底仓不动）
    - 止盈 + 还有空间 → "−1仓"
    - 持有 / 数据缺失 → "—"
    """
    if advice == "买入":
        if over_target:
            return "—"
        if position is None:
            return "—"  # 无市值数据 → 不给加减仓建议
        if position >= POSITION_MAX:
            return "—"  # 已达上限 6 仓
        return "+1仓"
    if advice == "止盈":
        if position is None:
            return "—"
        if position <= POSITION_BASE:
            return "—"  # 只剩底仓，不卖底仓
        return "−1仓"
    return "—"


def _empty_fund_row(
    fund_code: str,
    fund_name: str,
    index_code: str,
    position_str: str,
    adjust_str: str,
) -> dict[str, object]:
    """DB 里没估值数据时的占位行（所有列都填"数据缺失"或"—"）。"""
    return {
        "fund": f"{fund_code}\n{fund_name}",
        "index": index_code,
        "pe": "数据缺失",
        "pe_pct": "数据缺失",
        "dy": "数据缺失",
        "roe_yoy": "数据缺失",
        "verdict": "数据缺失",
        "advice": "—",
        "position": position_str,
        "adjust": adjust_str,
    }


def _format_fund_row(
    fund_code: str,
    fund_name: str,
    fv: FundValuation,
    strategy: str,
    verdict: str,
    advice: str,
    position_str: str,
    adjust_str: str,
) -> dict[str, object]:
    """格式化一只基金的估值行。按 strategy 选择不同指标填"PE分位"/"股息率"/"ROE同比"列。

    dividend 策略的 PE 分位列优先用股息率加权分位（如银行螺丝钉手动填），
    没有时 fallback 到普通 PE 分位（lixinger 总市值加权）— 跟 verdict 逻辑保持一致。
    """
    pe_str = f"{float(fv.pe_ttm):.2f}" if fv.pe_ttm is not None else "n/a"
    dy_str = f"{float(fv.dividend_yield) * 100:.2f}%" if fv.dividend_yield is not None else "n/a"
    pct_dyw_str = (
        f"{float(fv.pe_percentile_dy_weighted) * 100:.1f}%"
        if fv.pe_percentile_dy_weighted is not None
        else None  # fallback 到 pe_percentile（让 verdict 和渲染一致）
    )
    pct_str = f"{float(fv.pe_percentile) * 100:.1f}%" if fv.pe_percentile is not None else "n/a"
    roe_yoy = _compute_roe_yoy(fv)
    roe_yoy_str = f"{float(roe_yoy) * 100:+.1f}%" if roe_yoy is not None else "n/a"

    # 按 strategy 填列：
    # - dividend: PE分位=股息率加权（fallback 总市值加权）；股息率=实际值；ROE同比=—
    # - broad: PE分位=总市值加权；股息率=实际值；ROE同比=—
    # - growth: PE分位=总市值加权；股息率=—（成长股息率永远低，没参考价值）；ROE同比=实际值
    if strategy == "dividend":
        pe_pct_str = pct_dyw_str or pct_str  # 没有 dy_weighted 时用 pe_percentile
        dy_col = dy_str
        roe_col = "—"
    elif strategy == "growth":
        pe_pct_str = pct_str
        dy_col = "—"
        roe_col = roe_yoy_str
    else:  # broad
        pe_pct_str = pct_str
        dy_col = dy_str
        roe_col = "—"

    return {
        "fund": f"{fund_code}\n{fund_name}",
        "index": fv.index_code,
        "pe": pe_str,
        "pe_pct": pe_pct_str,
        "dy": dy_col,
        "roe_yoy": roe_col,
        "verdict": verdict,
        "advice": advice,
        "position": position_str,
        "adjust": adjust_str,
    }


def _fund_columns() -> list[dict[str, object]]:
    """A 股基金估值 + 仓位合并表的列定义（10 列，跨 strategy 通用）。"""
    return [
        {"name": "fund", "display_name": "基金", "data_type": "text", "width": "auto"},
        {"name": "index", "display_name": "指数", "data_type": "text", "width": "auto"},
        {"name": "pe", "display_name": "PE", "data_type": "text", "width": "auto"},
        # "PE分位" 列按 strategy 切换数据：dividend 用股息率加权，broad/growth 用总市值加权
        {"name": "pe_pct", "display_name": "PE分位", "data_type": "text", "width": "auto"},
        {"name": "dy", "display_name": "股息率", "data_type": "text", "width": "auto"},
        {"name": "roe_yoy", "display_name": "ROE同比", "data_type": "text", "width": "auto"},
        {"name": "verdict", "display_name": "评估", "data_type": "text", "width": "auto"},
        {"name": "advice", "display_name": "建议", "data_type": "text", "width": "auto"},
        {"name": "position", "display_name": "当前仓位", "data_type": "text", "width": "auto"},
        {"name": "adjust", "display_name": "加减仓建议", "data_type": "text", "width": "auto"},
    ]


def _build_valuation_section(journal: PortfolioJournal) -> list[dict[str, object]]:
    """Section 3: 大类资产估值（spec 098 — A 股 + 港股 + 美股）。

    spec 098.2（liubo 2026-09-19 确认方案 A）：港股估值跟 A 股平行展示。
    spec 098.3（liubo 2026-09-20 拍板）：美股估值再起一个 region，跟 A 股 / 港股平行。
    spec 098.4（liubo 2026-09-20 拍板）：合并 3 region 表 — 飞书卡片 ≤5 table 总数限制
        （ErrCode 11310），3 个 region 指标表 + 3 个 per-fund 表 = 6 张表，叠上持仓聚合 + 策略
        对比 = 8 张表超出限制。改为 combined 模式：1 张 combined 指标表（12 指标 + 3 综合 =
        15 行 × 5 列）+ 1 张 combined per-fund 表（18 只基金 × 11 列），加上原有 2 张
        （持仓聚合 + 策略对比）= 4 张表，在限制内。

    渲染结构：
    - note header "估值与操作"
    - 1 张 combined 指标表（3 个 region 的 4 指标 + 综合分行一并展示，行首带"区域"列）
    - 3 个 strategy note div（每个 region 一个，仍按 region 维度给占比 + 估值判断）
    - 1 张 combined per-fund 表（3 个 region 的基金按区域前缀排序展示）

    数据缺失处理：
    - 任何一个 region 完全空 → 该 region 行不出现在 combined 表（不渲染空行）
    - 完全没有 region 数据 → 返回 []
    """
    a_share_data = _collect_region_valuation_data(
        journal, SwensenClass.CN_EQUITY, "A 股",
        A_SHARE_INDICATOR_CODES, A_SHARE_INDICATOR_NAMES, A_SHARE_INDICATOR_SHORT_NAMES,
    )
    hk_data = _collect_region_valuation_data(
        journal, SwensenClass.HK_EQUITY, "港股",
        HK_INDICATOR_CODES, HK_INDICATOR_NAMES, HK_INDICATOR_SHORT_NAMES,
    )
    us_data = _collect_region_valuation_data(
        journal, SwensenClass.US_EQUITY, "美股",
        US_INDICATOR_CODES, US_INDICATOR_NAMES, US_INDICATOR_SHORT_NAMES,
    )

    region_datas = [d for d in (a_share_data, hk_data, us_data) if d.has_data]
    if not region_datas:
        return []

    elements: list[dict[str, object]] = [
        {"tag": "note", "elements": [{"tag": "plain_text", "content": "估值与操作"}]},
        {"tag": "hr"},
        {"tag": "table", **_build_combined_indicator_table(region_datas)},
    ]

    # 3 个 region 各一个 strategy_note div（顺序：A 股 → 港股 → 美股）
    # 即使该 region 在 combined 表里有数据但没持仓（strategy_note = None）也跳过
    for d in (a_share_data, hk_data, us_data):
        if d.strategy_note is not None:
            elements.append(d.strategy_note)

    # combined per-fund 表（按 region 顺序：A 股 → 港股 → 美股）
    per_fund_table = _build_combined_per_fund_table(region_datas)
    if per_fund_table is not None:
        elements.append({"tag": "hr"})
        elements.append({"tag": "table", **per_fund_table})

    return elements


@dataclass(frozen=True, slots=True)
class _RegionValuationData:
    """单个 region 的估值数据（spec 098.4 — combined table 用）。

    shared 数据：
    - region_label: "A 股" / "港股" / "美股"
    - indicator_rows: 4 指标行 + 1 综合分行（无估值时为 []）
    - strategy_note: 策略 div 元素（无持仓或无估值时为 None）
    - over_target: 区域占比 > 目标（per-fund 表的"加减仓建议"列用）
    - per_fund_rows: per-fund 行（无持仓时不渲染空表，跟 _build_fund_valuation_section 一致）
    - has_data: 该 region 是否有任何数据（indicator_rows 非空 或 per_fund_rows 非空）
    """

    region_label: str
    indicator_rows: list[dict[str, object]]
    strategy_note: dict[str, object] | None
    over_target: bool
    per_fund_rows: list[dict[str, object]]
    has_data: bool


def _collect_region_valuation_data(
    journal: PortfolioJournal,
    subclass: SwensenClass,
    region_label: str,
    codes: list[ValuationIndicatorCode],
    names: dict[ValuationIndicatorCode, str],
    short_names: dict[ValuationIndicatorCode, str],
) -> _RegionValuationData:
    """采集单个 region 的估值数据（spec 098.4 — 共享 helper）。

    之前的 _build_a_share_valuation_section / _build_hk_valuation_section / _build_us_valuation_section
    各自做了相同的"读 DB → 建表 → 算 strategy → 建 per-fund"流程；现在提取共享，
    让 combined table 能直接用各 region 的数据。

    Returns:
        _RegionValuationData，含 4 指标 + 综合分行、strategy_note、over_target、per-fund 行
    """
    # spec 098.5：估值指标改用"最新可用"日期，而不是强制今天 —
    # 工作日 17 点后/周末/节假日，今天没数据很正常，回退到 DB 里最新一天的
    # 数据，per-fund 表同理（list_latest_fund_valuations_for_codes）。
    today = date.today()
    today_indicators = journal.db.list_valuation_indicators_for_date(today)
    if today_indicators:
        indicators = today_indicators
        valuation_date = today
    else:
        indicators = journal.db.list_latest_valuation_indicators()
        # 取最新日期（最新一批 indicators 的 record_date 都一样）
        valuation_date = indicators[0].record_date if indicators else today
    region_indicators = [i for i in indicators if i.indicator_code in set(codes)]

    indicator_rows: list[dict[str, object]] = []
    strategy_note: dict[str, object] | None = None
    over_target = False
    if region_indicators:
        by_code: dict[ValuationIndicatorCode, ValuationIndicator] = {
            i.indicator_code: i for i in region_indicators
        }
        rows, _scores, composite_verdict_str = _build_indicator_table(
            by_code, codes, names, short_names,
        )
        indicator_rows = rows
        strategy_note, over_target = _build_region_strategy_note(
            journal, subclass, region_label, _scores, composite_verdict_str,
        )

    # per-fund 行：从 _build_fund_valuation_section 复用（仍走 hr + table 渲染）
    # 这里只取 table 行（避免重复渲染 hr）
    per_fund_rows: list[dict[str, object]] = []
    fund_elements = _build_fund_valuation_section(
        journal, fund_subclasses=[subclass], over_target=over_target,
    )
    if fund_elements:
        # fund_elements = [hr, table] — 跳过 hr，取 table 的 rows
        for el in fund_elements:
            if el.get("tag") == "table":
                per_fund_rows = el.get("rows", [])  # type: ignore[assignment]
                break

    has_data = bool(indicator_rows) or bool(per_fund_rows)
    return _RegionValuationData(
        region_label=region_label,
        indicator_rows=indicator_rows,
        strategy_note=strategy_note,
        over_target=over_target,
        per_fund_rows=per_fund_rows,
        has_data=has_data,
    )


def _build_combined_indicator_table(region_datas: list[_RegionValuationData]) -> dict[str, object]:
    """3 region 的估值指标 + 综合分合并成 1 张表（spec 098.4 — 飞书 ≤5 table 限制）。

    列：区域 / 指标 / 当前 / 评估 / 阈值（5 列）
    行：每个 region 的 4 指标 + 1 综合分（无综合分就 4 行）
        例：3 region × (4 指标 + 1 综合) = 15 行

    区域列前缀让用户一眼看清每行属于哪个 region；综合分行 region 列写"—"
    （综合分是 region 内部聚合，不属于单一指标）。
    """
    rows: list[dict[str, object]] = []
    for d in region_datas:
        for indicator_row in d.indicator_rows:
            indicator_name = indicator_row.get("indicator", "")
            # 综合分行用区域"—"（聚合行不是单一指标），其他指标行用区域前缀
            if indicator_name == "综合分":
                region_cell = "—"
            else:
                region_cell = d.region_label
            rows.append(
                {
                    "region": region_cell,
                    "indicator": indicator_name,
                    "value": indicator_row.get("value", ""),
                    "verdict": indicator_row.get("verdict", ""),
                    "threshold": indicator_row.get("threshold", ""),
                }
            )

    return {
        "columns": [
            {"name": "region", "display_name": "区域", "data_type": "text", "width": "auto"},
            {"name": "indicator", "display_name": "指标", "data_type": "text", "width": "auto"},
            {"name": "value", "display_name": "当前", "data_type": "text", "width": "auto"},
            {"name": "verdict", "display_name": "评估", "data_type": "text", "width": "auto"},
            {"name": "threshold", "display_name": "阈值", "data_type": "text", "width": "auto"},
        ],
        "rows": rows,
    }


def _build_combined_per_fund_table(region_datas: list[_RegionValuationData]) -> dict[str, object] | None:
    """3 region 的 per-fund 行合并成 1 张表（spec 098.4 — 飞书 ≤5 table 限制）。

    列：区域 / 基金 / 指数 / PE / PE分位 / 股息率 / ROE同比 / 评估 / 建议 / 当前仓位 / 加减仓建议
        （11 列，比 per-region 多 1 列"区域"前缀）

    行：所有 region 的 per-fund 行，按 region 顺序串联（A 股 → 港股 → 美股）
        没有任何 region 有持仓 → 返回 None（不渲染空表）

    数据缺失：某个 region 完全无持仓 → 跳过该 region；某个 region 有持仓但 fund 缺估值
        → 仍渲染该行（行内 "数据缺失"，跟之前一致）。
    """
    has_any_row = any(bool(d.per_fund_rows) for d in region_datas)
    if not has_any_row:
        return None

    rows: list[dict[str, object]] = []
    for d in region_datas:
        for fund_row in d.per_fund_rows:
            rows.append(
                {
                    "region": d.region_label,
                    "fund": fund_row.get("fund", ""),
                    "index": fund_row.get("index", ""),
                    "pe": fund_row.get("pe", ""),
                    "pe_pct": fund_row.get("pe_pct", ""),
                    "dy": fund_row.get("dy", ""),
                    "roe_yoy": fund_row.get("roe_yoy", ""),
                    "verdict": fund_row.get("verdict", ""),
                    "advice": fund_row.get("advice", ""),
                    "position": fund_row.get("position", ""),
                    "adjust": fund_row.get("adjust", ""),
                }
            )

    return {
        "columns": [
            {"name": "region", "display_name": "区域", "data_type": "text", "width": "auto"},
            {"name": "fund", "display_name": "基金", "data_type": "text", "width": "auto"},
            {"name": "index", "display_name": "指数", "data_type": "text", "width": "auto"},
            {"name": "pe", "display_name": "PE", "data_type": "text", "width": "auto"},
            {"name": "pe_pct", "display_name": "PE分位", "data_type": "text", "width": "auto"},
            {"name": "dy", "display_name": "股息率", "data_type": "text", "width": "auto"},
            {"name": "roe_yoy", "display_name": "ROE同比", "data_type": "text", "width": "auto"},
            {"name": "verdict", "display_name": "评估", "data_type": "text", "width": "auto"},
            {"name": "advice", "display_name": "建议", "data_type": "text", "width": "auto"},
            {"name": "position", "display_name": "当前仓位", "data_type": "text", "width": "auto"},
            {"name": "adjust", "display_name": "加减仓建议", "data_type": "text", "width": "auto"},
        ],
        "rows": rows,
    }


# ─── A 股 + 港股 region 估值 section 共享的常量 ───

# A 股 4 指标 + 中文名 + 简名（spec 098 第二十一轮）
A_SHARE_INDICATOR_CODES: list[ValuationIndicatorCode] = [
    ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
    ValuationIndicatorCode.PE_PERCENTILE,
    ValuationIndicatorCode.BUFFETT_INDICATOR,
    ValuationIndicatorCode.DIVIDEND_YIELD,
]
A_SHARE_INDICATOR_NAMES: dict[ValuationIndicatorCode, str] = {
    ValuationIndicatorCode.EQUITY_RISK_PREMIUM: "股债利差",
    ValuationIndicatorCode.PE_PERCENTILE: "PE 分位",
    ValuationIndicatorCode.BUFFETT_INDICATOR: "巴菲特指标",
    ValuationIndicatorCode.DIVIDEND_YIELD: "股息率",
}
A_SHARE_INDICATOR_SHORT_NAMES: dict[ValuationIndicatorCode, str] = {
    ValuationIndicatorCode.EQUITY_RISK_PREMIUM: "股债",
    ValuationIndicatorCode.PE_PERCENTILE: "PE",
    ValuationIndicatorCode.BUFFETT_INDICATOR: "巴菲特",
    ValuationIndicatorCode.DIVIDEND_YIELD: "股息",
}

# 港股 4 指标 + 中文名 + 简名（spec 098.2 — liubo 2026-09-19 方案 A）
HK_INDICATOR_CODES: list[ValuationIndicatorCode] = [
    ValuationIndicatorCode.HK_PE_PERCENTILE,
    ValuationIndicatorCode.HK_DIVIDEND_YIELD,
    ValuationIndicatorCode.HK_AH_PREMIUM,
    ValuationIndicatorCode.HK_BUFFETT_INDICATOR,
]
HK_INDICATOR_NAMES: dict[ValuationIndicatorCode, str] = {
    ValuationIndicatorCode.HK_PE_PERCENTILE: "PE 分位",
    ValuationIndicatorCode.HK_DIVIDEND_YIELD: "股息率",
    ValuationIndicatorCode.HK_AH_PREMIUM: "AH 溢价",
    ValuationIndicatorCode.HK_BUFFETT_INDICATOR: "港股巴菲特",
}
HK_INDICATOR_SHORT_NAMES: dict[ValuationIndicatorCode, str] = {
    ValuationIndicatorCode.HK_PE_PERCENTILE: "PE",
    ValuationIndicatorCode.HK_DIVIDEND_YIELD: "股息",
    ValuationIndicatorCode.HK_AH_PREMIUM: "AH",
    ValuationIndicatorCode.HK_BUFFETT_INDICATOR: "巴菲特",
}

# 美股 4 指标 + 中文名 + 简名（spec 098.3 — liubo 2026-09-20）
# 顺序对应卡片的"指标"列，跟 HK region 平行。
US_INDICATOR_CODES: list[ValuationIndicatorCode] = [
    ValuationIndicatorCode.US_PE_PERCENTILE,
    ValuationIndicatorCode.US_DIVIDEND_YIELD,
    ValuationIndicatorCode.US_BUFFETT_INDICATOR,
    ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM,
]
US_INDICATOR_NAMES: dict[ValuationIndicatorCode, str] = {
    ValuationIndicatorCode.US_PE_PERCENTILE: "PE 分位",
    ValuationIndicatorCode.US_DIVIDEND_YIELD: "股息率",
    ValuationIndicatorCode.US_BUFFETT_INDICATOR: "美股巴菲特",
    ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM: "股债利差",
}
US_INDICATOR_SHORT_NAMES: dict[ValuationIndicatorCode, str] = {
    ValuationIndicatorCode.US_PE_PERCENTILE: "PE",
    ValuationIndicatorCode.US_DIVIDEND_YIELD: "股息",
    ValuationIndicatorCode.US_BUFFETT_INDICATOR: "巴菲特",
    ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM: "股债",
}


def _format_indicator_value(code: ValuationIndicatorCode, value: Decimal) -> str:
    """单个估值指标当前值的格式化字符串（spec 098 第 106~110 行 + 098.2/098.3 扩展）。

    12 个指标的格式差异：
    - 股债利差 / 美股股债利差：保留正负号 + 2 位小数（"+" / "-" 头）
    - A 股 PE 分位 / 股息率 / 巴菲特：2 位小数百分比
    - 港股 PE 分位 / 股息率：2 位小数百分比（跟 A 股一致）
    - 美股 PE 分位 / 股息率：2 位小数百分比（跟 A 股一致）
    - AH 溢价 / 港股巴菲特 / 美股巴菲特：整数百分比（值通常 > 1，显示成 "150%" / "950%"）
    """
    pct = float(value) * 100
    if code == ValuationIndicatorCode.EQUITY_RISK_PREMIUM:
        # 股债利差：保留符号（正 = 股票相对债券有溢价）+2 位小数
        return f"{pct:+.2f}%"
    if code == ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM:
        # 美股股债利差：跟 A 股一致保留正负号 + 2 位小数
        return f"{pct:+.2f}%"
    if code in (
        ValuationIndicatorCode.HK_AH_PREMIUM,
        ValuationIndicatorCode.HK_BUFFETT_INDICATOR,
        ValuationIndicatorCode.US_BUFFETT_INDICATOR,
    ):
        # 港股/美股巴菲特指标：值通常 > 1（如 950% / 180%），整数即可
        return f"{pct:.0f}%"
    # 其他 7 个指标：2 位小数百分比
    return f"{pct:.2f}%"


def _build_indicator_table(
    by_code: dict[ValuationIndicatorCode, ValuationIndicator],
    indicator_codes: list[ValuationIndicatorCode],
    indicator_names: dict[ValuationIndicatorCode, str],
    indicator_short_names: dict[ValuationIndicatorCode, str],
) -> tuple[list[dict[str, object]], dict[ValuationIndicatorCode, int], str]:
    """构建 4 指标 + 1 综合分的 5 行 4 列估值表。

    返回 (rows, scores, composite_verdict_str)：
    - rows: 5 行表格数据（4 指标 + 1 综合分；综合分仅当有 ≥1 个分数时存在）
    - scores: 每个指标的 1-5 分（缺失指标不在 dict 里）
    - composite_verdict_str: 综合分解读文案（"极低"/"低估"/"正常"/"偏高估"/"极高估"）
      无分数时为空字符串（让 caller 知道不能用 verdict 做决策）

    复用：A 股 + 港股两个 section 都调它。
    """
    scores: dict[ValuationIndicatorCode, int] = {}
    rows: list[dict[str, object]] = []

    for code in indicator_codes:
        name = indicator_names[code]
        threshold = format_5band_threshold(DEFAULT_SCORE_BANDS[code])
        if code in by_code:
            ind = by_code[code]
            current_str = _format_indicator_value(code, ind.value)
            verdict_str = compute_verdict(code, ind.value)
            try:
                scores[code] = score_indicator(code, ind.value)
            except ValueError:
                # 数据异常（落入区间外）— 当作缺失，不计分
                pass
            # 评估列 = 评估文字 + 1-5 分（liubo 2026-09-19 反馈"评估要加得分"）
            # 例："偏低估 1" / "正常 3" / "偏高估 4"
            if code in scores:
                verdict_str = f"{verdict_str} {scores[code]}"
        else:
            current_str = "数据缺失"
            verdict_str = "n/a"
        rows.append(
            {
                "indicator": name,
                "value": current_str,
                "verdict": verdict_str,
                "threshold": threshold,
            }
        )

    composite_verdict_str = ""
    if scores:
        score_list = [scores[c] for c in indicator_codes if c in scores]
        composite = compute_composite_score(score_list)
        composite_verdict_str = interpret_composite_score(composite)
        # value 列：分数简明展示（"[PE:4 股债:2 巴菲特:3 股息:3]"）
        parts = [f"{indicator_short_names[c]}:{scores[c]}" for c in indicator_codes if c in scores]
        if len(parts) < len(indicator_codes):
            # 部分缺失时，缺失的位置标 "-"（而不是完全省掉，避免顺序错位）
            parts_full = []
            for c in indicator_codes:
                if c in scores:
                    parts_full.append(f"{indicator_short_names[c]}:{scores[c]}")
                else:
                    parts_full.append(f"{indicator_short_names[c]}:-")
            parts = parts_full
        composite_value = "[" + " ".join(parts) + "]"
        rows.append(
            {
                "indicator": "综合分",
                "value": composite_value,
                "verdict": f"{float(composite):.1f} {composite_verdict_str}",
                "threshold": "1=极低估 5=极高估",
            }
        )

    return rows, scores, composite_verdict_str


def _decide_a_share_strategy(
    over_target: bool,
    composite_verdict: str,
) -> tuple[str, str]:
    """根据 A 股占比 vs 目标 + 估值综合分，决定策略（spec 098 第二十五轮）。

    Args:
        over_target: True → 当前占比已超过动态目标
        composite_verdict: 综合分解读文案，interpret_composite_score() 输出：
                          "极低" / "低估" / "正常" / "偏高估" / "极高估"

    Returns:
        (策略文案, 判定依据)
        策略文案：用于卡片"评估"列（用户一眼看到的动作）
        判定依据：用于卡片"阈值"列（让用户知道为什么这样建议）

    决策树：
        1) over_target → ("不再投入", "占比 > 目标")
           仓位纪律优先：即使估值偏低也不买，避免进一步超配
        2) over_target=False + 偏高估/极高估 → ("分批止盈", "估值 偏高估")
        3) over_target=False + 低估/极低 → ("分批买入", "估值 偏低估")
        4) over_target=False + 正常 → ("正常持有", "估值 正常 — 不卖不买")
    """
    if over_target:
        return "不再投入", "占比 > 目标"
    if composite_verdict in ("偏高估", "极高估"):
        return "分批止盈", "估值 偏高估"
    if composite_verdict in ("低估", "极低"):
        return "分批买入", "估值 偏低估"
    # 正常（含 "正常" 和兜底其他情况）
    return "正常持有", "估值 正常 — 不卖不买"


def _decide_hk_strategy(
    over_target: bool,
    composite_verdict: str,
) -> tuple[str, str]:
    """根据港股占比 vs 目标 + 估值综合分，决定策略（spec 098.2）。

    跟 _decide_a_share_strategy 完全平行 — 同样 4 档决策树，仓位规则一致。
    """
    if over_target:
        return "不再投入", "占比 > 目标"
    if composite_verdict in ("偏高估", "极高估"):
        return "分批止盈", "估值 偏高估"
    if composite_verdict in ("低估", "极低"):
        return "分批买入", "估值 偏低估"
    return "正常持有", "估值 正常 — 不卖不买"


def _decide_us_strategy(
    over_target: bool,
    composite_verdict: str,
) -> tuple[str, str]:
    """根据美股占比 vs 目标 + 估值综合分，决定策略（spec 098.3）。

    跟 _decide_a_share_strategy / _decide_hk_strategy 完全平行 — 同样 4 档决策树，
    仓位规则一致。
    """
    if over_target:
        return "不再投入", "占比 > 目标"
    if composite_verdict in ("偏高估", "极高估"):
        return "分批止盈", "估值 偏高估"
    if composite_verdict in ("低估", "极低"):
        return "分批买入", "估值 偏低估"
    return "正常持有", "估值 正常 — 不卖不买"


def _build_region_strategy_note(
    journal: PortfolioJournal,
    subclass: SwensenClass,
    region_label: str,  # "A 股" / "港股"
    scores: dict[ValuationIndicatorCode, int],
    composite_verdict_str: str,
) -> tuple[dict[str, object] | None, bool]:
    """构建某个 region 的策略 note + 返回 over_target（per-fund 表要用）。

    Returns:
        (strategy_note dict or None, over_target bool)
        - strategy_note: 含占比/目标/策略/理由的 div，None 表示无持仓
        - over_target: 占比 > 目标（per-fund 表的"加减仓建议"列需要）

    数据缺失处理（spec 098 第二十六轮 — liubo 反馈"用文字说，别放表里"）：
    - 占比 > 目标 → "不再投入"（仓位纪律优先）
    - 占比 ≤ 目标 + 有估值 → 偏低估/正常/偏高估三档
    - 占比 ≤ 目标 + 无估值 → "占比 ≤ 目标（等估值）"
    - 持仓数据缺失（journal.compute_holdings()=空）→ "持仓数据缺失"（第二十二轮 liubo 2026-10-16）
      此时跳过策略判断，只给估值综合分的解读

    spec 097 第二十一轮（liubo 2026-09-24）：region_target 改用
    DEFAULT_POSITION_ALLOCATION.target_weight(subclass)，不再走动态缩放公式。
    """
    breakdown = compute_breakdown(journal)
    region_row = next((b for b in breakdown if b["subclass"] == subclass), None)
    if region_row is None:
        return None, False

    region_weight = float(region_row["weight"]) * 100
    region_target_pct = float(DEFAULT_POSITION_ALLOCATION.target_weight(subclass)) * 100
    holdings_empty = not journal.compute_holdings()

    # 第二十二轮（liubo 2026-10-16）：持仓数据缺失时只显示估值综合分，
    # 不展示占比/目标/策略（避免"占比 0%"误导）
    if holdings_empty:
        if scores:
            text = (
                f"**📌 {region_label}估值综合分 {composite_verdict_str}。"
                f"（持仓数据缺失，等录入后展示占比/目标）**"
            )
        else:
            text = f"**📌 {region_label}持仓数据缺失，等录入后展示。**"
        strategy_note: dict[str, object] = {
            "tag": "div",
            "text": {"tag": "lark_md", "content": text},
        }
        return strategy_note, False

    over_target = region_weight > region_target_pct

    if scores:
        if subclass == SwensenClass.CN_EQUITY:
            strategy, reason = _decide_a_share_strategy(over_target, composite_verdict_str)
        elif subclass == SwensenClass.HK_EQUITY:
            strategy, reason = _decide_hk_strategy(over_target, composite_verdict_str)
        elif subclass == SwensenClass.US_EQUITY:
            strategy, reason = _decide_us_strategy(over_target, composite_verdict_str)
        else:
            # 兜底：跟 _decide_a_share_strategy 一致（不认识的子类按 A 股走）
            strategy, reason = _decide_a_share_strategy(over_target, composite_verdict_str)
    else:
        # 无估值数据 → 不能给买卖建议
        strategy = "占比 ≤ 目标（等估值）" if not over_target else "不再投入"
        reason = "无估值数据"

    text = f"**📌 {region_label}占比 {region_weight:.1f}%，目标 {region_target_pct:.1f}%。{strategy}（{reason}）**"
    strategy_note = {
        "tag": "div",
        "text": {"tag": "lark_md", "content": text},
    }
    return strategy_note, over_target


def _build_a_share_valuation_section(journal: PortfolioJournal) -> list[dict[str, object]]:
    """A 股估值 section（spec 098 — 含 4 指标 + 综合分 + 策略 + per-fund 表）。

    spec 098.2：保留 A 股 section，作为跟港股并列的第一个估值大类。
    spec 098.4：内部改用 _collect_region_valuation_data 共享 helper（避免跟港股/美股 builder
    重复；_build_valuation_section 走 combined 模式，不再用 per-region builder）。

    DB 完全空（今天没拉过 A 股估值）→ 返回 []（不渲染整段）。
    """
    data = _collect_region_valuation_data(
        journal, SwensenClass.CN_EQUITY, "A 股",
        A_SHARE_INDICATOR_CODES, A_SHARE_INDICATOR_NAMES, A_SHARE_INDICATOR_SHORT_NAMES,
    )
    return _render_region_valuation_section(data, "A 股估值与操作")


def _build_hk_valuation_section(journal: PortfolioJournal) -> list[dict[str, object]]:
    """港股估值 section（spec 098.2 — liubo 2026-09-19 方案 A）。

    跟 A 股 section 完全平行：4 港股指标 + 综合分 + 策略 + per-fund 表。

    spec 098.4：内部改用 _collect_region_valuation_data 共享 helper。

    DB 完全空 → 返回 []（不渲染）。
    """
    data = _collect_region_valuation_data(
        journal, SwensenClass.HK_EQUITY, "港股",
        HK_INDICATOR_CODES, HK_INDICATOR_NAMES, HK_INDICATOR_SHORT_NAMES,
    )
    return _render_region_valuation_section(data, "港股估值与操作")


def _build_us_valuation_section(journal: PortfolioJournal) -> list[dict[str, object]]:
    """美股估值 section（spec 098.3 — liubo 2026-09-20）。

    跟 A 股 / 港股 section 完全平行：4 美股指标 + 综合分 + 策略 + per-fund 表。

    数据来源差异（liubo 2026-09-20 确认）：
    - PE / 股息率：lixinger CSV（标普 500 = INX）。
      纳指 100 (NDX) 在 lixinger CSV 里没数据（理杏仁未提供），per-fund 表里
      4 只 NDX ETF + 3 只全球主题会用 INX 的估值（默认 fallback），不报错。
      加仓时按"纳指偏高估 → 减"做减仓判断（保守）— 后续 lixinger 补 NDX 数据后
      会自动按 index_code 路由。
    - 美股巴菲特：硬编码 50T USD 市值 + akshare 没现成接口 → 用 us_buffett_indicator
    - 美股股债利差：1/PE - 美 10Y 国债（akshare bond_zh_us_rate）

    spec 098.4：内部改用 _collect_region_valuation_data 共享 helper。

    DB 完全空 → 返回 []（不渲染）。
    """
    data = _collect_region_valuation_data(
        journal, SwensenClass.US_EQUITY, "美股",
        US_INDICATOR_CODES, US_INDICATOR_NAMES, US_INDICATOR_SHORT_NAMES,
    )
    return _render_region_valuation_section(data, "美股估值与操作")


def _render_region_valuation_section(
    data: _RegionValuationData,
    header_text: str,
) -> list[dict[str, object]]:
    """渲染单个 region 的 section 元素（spec 098.4 — 共享渲染逻辑）。

    结构：
    - note header（"A 股估值与操作" / "港股估值与操作" / "美股估值与操作"）
    - hr
    - indicator table（4 指标 + 1 综合分）— data.has_data 且有 indicator_rows 时
    - (optional) strategy_note div
    - hr + per-fund table — 有 per-fund 行时

    无数据 → []。

    只供 _build_*_valuation_section 三个 per-region builder 用（直接测试仍然验证）
    ；卡片最终走 _build_valuation_section 的 combined 模式。
    """
    if not data.has_data:
        return []

    elements: list[dict[str, object]] = []
    if data.indicator_rows:
        elements.extend([
            {"tag": "note", "elements": [{"tag": "plain_text", "content": header_text}]},
            {"tag": "hr"},
            {
                "tag": "table",
                "columns": [
                    {"name": "indicator", "display_name": "指标", "data_type": "text", "width": "auto"},
                    {"name": "value", "display_name": "当前", "data_type": "text", "width": "auto"},
                    {"name": "verdict", "display_name": "评估", "data_type": "text", "width": "auto"},
                    {"name": "threshold", "display_name": "阈值", "data_type": "text", "width": "auto"},
                ],
                "rows": data.indicator_rows,
            },
        ])
        if data.strategy_note is not None:
            elements.append(data.strategy_note)

    if data.per_fund_rows:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "table",
            "columns": _fund_columns(),
            "rows": data.per_fund_rows,
        })

    return elements


def build_portfolio_card(
    journal: PortfolioJournal,
    title: str | None = None,
) -> dict[str, object]:
    """构造实盘账本的飞书交互卡片（单卡片版本，向后兼容）。

    第三十五轮（liubo 2026-10-18）：周报改为发 4 张卡（overview + 3 region cards）。
    本函数仍按旧行为生成单卡：全部 section 拼到一张卡里（per-fund 是 55 行全列）。
    多卡流程请用 build_portfolio_cards() → [overview, A 股, 港股, 美股]。

    卡片结构：
    - Section 1（持仓成本 by 大类）：note + div + hr + table
    - Section 2（per-fund 55 行）：hr + note + div + table
    - Section 3（调仓记录）：hr + note + hr + table
    - Section 4（定投记录）：hr + note + hr + table
    """
    actual_title = title or "实盘周报"
    elements = _build_cost_basis_section()
    elements.extend(_build_per_fund_section())  # region=None → 全部 55 行
    all_rebalance_rows = _collect_rebalance_rows(limit=20)
    rebalance_section = _build_rebalance_records_section(all_rebalance_rows)  # region=None
    if rebalance_section:
        elements.append({"tag": "hr"})
        elements.extend(rebalance_section)
    dca_section = _build_dca_records_section(all_rebalance_rows)  # region=None
    if dca_section:
        elements.append({"tag": "hr"})
        elements.extend(dca_section)
    return _wrap_card(actual_title, elements)


def build_portfolio_cards(
    journal: PortfolioJournal,
    base_title: str = "实盘周报",
) -> list[dict[str, object]]:
    """构造 4 张飞书卡片：overview + 3 region cards（第三十五轮 liubo 2026-10-18）。

    返回顺序：
    - cards[0] = 总览（持仓成本 by 大类）
    - cards[1] = A 股（per-fund + 调仓 + 定投，filter 到 CN_EQUITY）
    - cards[2] = 港股（per-fund + 调仓 + 定投，filter 到 HK_EQUITY）
    - cards[3] = 美股（per-fund + 调仓 + 定投，filter 到 US_EQUITY）

    每张卡用各自 title（"{base_title}（总览/A 股/港股/美股）"）。
    """
    overview = build_overview_card(journal, title=f"{base_title}（总览）")
    cn = build_region_card(journal, SwensenClass.CN_EQUITY, title=f"{base_title}（A 股）")
    hk = build_region_card(journal, SwensenClass.HK_EQUITY, title=f"{base_title}（港股）")
    us = build_region_card(journal, SwensenClass.US_EQUITY, title=f"{base_title}（美股）")
    return [overview, cn, hk, us]


def build_overview_card(
    journal: PortfolioJournal,
    title: str = "实盘周报（总览）",
) -> dict[str, object]:
    """Card 1（总览）：持仓成本 by 大类（liubo 2026-10-18 拍板）。

    数据源：COST_BASIS_BY_CODE + breakdown.py（不依赖 journal.compute_holdings()）。
    第三十四轮记忆：市值/盈亏不显示（券商 app 数据不准）；用累计成本。
    """
    elements = _build_cost_basis_section()
    return _wrap_card(title, elements)


def build_region_card(
    journal: PortfolioJournal,
    region: SwensenClass,
    title: str | None = None,
) -> dict[str, object]:
    """Card 2/3/4（区域）：某 region 的 per-fund + 调仓 + 定投。

    第三十五轮（liubo 2026-10-18）：
    - 标题默认 "{region 中文名} 周报"，例："A 股 周报"
    - per-fund：只显示该 region 的持仓 + watchlist 行
    - 调仓纪录：按 fund_codes 过滤（source 或 target 之一属于 region 则入选）
    - 定投纪录：按 DCA 基金的 SUBCLASS 过滤
    """
    from global_allocation.portfolio.breakdown import DISPLAY_NAME

    actual_title = title or f"{DISPLAY_NAME[region].split('股票')[0]} 周报"
    # 收集一次 rebalance rows，3 个 section 共用
    all_rebalance_rows = _collect_rebalance_rows(limit=20)

    elements: list[dict[str, object]] = []

    # Section 2: per-fund（filter 到 region）
    per_fund = _build_per_fund_section(region=region)
    elements.extend(per_fund)

    # Section 3: 调仓记录（filter 到 region）
    rebalance_section = _build_rebalance_records_section(all_rebalance_rows, region=region)
    if rebalance_section:
        elements.append({"tag": "hr"})
        elements.extend(rebalance_section)

    # Section 4: 定投记录（filter 到 region）
    dca_section = _build_dca_records_section(all_rebalance_rows, region=region)
    if dca_section:
        elements.append({"tag": "hr"})
        elements.extend(dca_section)

    # 如果 region 完全空（无持仓/无调仓/无定投）→ 仍返回 1 个 note 提示
    if len(elements) <= len(per_fund):
        elements.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "本 region 无调仓 / 定投记录"},
            }
        )

    return _wrap_card(actual_title, elements)


def _wrap_card(title: str, elements: list[dict[str, object]]) -> dict[str, object]:
    """通用卡片外壳（header + elements + footer）。"""
    return {
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
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


def card_to_json(card: dict[str, object]) -> str:
    """序列化（ensure_ascii=False 保中文）。"""
    return json.dumps(card, ensure_ascii=False)


__all__ = ["build_portfolio_card", "build_portfolio_cards", "build_overview_card", "build_region_card", "card_to_json"]
