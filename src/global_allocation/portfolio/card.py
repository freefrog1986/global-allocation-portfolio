"""飞书 chart card for portfolio journal。

参照 specs/090-portfolio-journal.md + specs/096-portfolio-card-redesign.md
+ specs/097-strategy-spike.md。

设计原则（spec 097 第十九轮 — 单 section 整合）：
- 只回答用户三个问题：现在整体怎么样 / 大类资产怎么分布 / 每个大类具体占多少
- 1 段元素：summary div + 大类资产柱状图 + 5 列聚合持仓表（含 target/delta）
- 柱状图带顶部数值标签（label.visible + position="top"）— 第十九轮加
- 表不再下钻到单只基金，只到"分类 / 市值 / 占比 / 目标 / 偏离"
- 11 个子类全部展示（含 count=0 的——这样能看出框架里哪些没覆盖到）
- 不再包含 pie / line chart / 单只基金明细 / 交易流水表
- 不再包含独立的"大类资产策略"和"大类资产明细" section（合并到持仓表）
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from global_allocation.portfolio.breakdown import SwensenClass, compute_breakdown
from global_allocation.portfolio.journal import PortfolioJournal
from global_allocation.portfolio.strategy import (
    DEFAULT_STRATEGY,
    AllocationStrategy,
    SuperCategory,
    compute_subclass_actual_target,
    compute_super_category_breakdown,
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
    strategy: AllocationStrategy = DEFAULT_STRATEGY,
) -> dict[str, object]:
    """11 子类聚合表：分类 / 市值 / 占比 / 目标 / 偏离。

    spec 097 第十九轮（liubo 2026-09-19）：合并 Section 2 + Section 3 到这张表。

    历史（第十四~第十八轮）：卡片分 3 个 section
    - Section 1（实盘持仓）：本表（3 列 — 分类/市值/占比）
    - Section 2（大类资产策略）：超类对比表（5 行 × 4 列 — category/target/current/delta）
    - Section 3（大类资产明细）：子类对比表（10 行 × 4 列 — subclass/target/current/delta）

    liubo 第十九轮反馈：
    > "第一部分持仓里边其实每一个大类的占比都有，干脆就把策略里边的目标和偏离
    > 放在这张表里了，然后那上边后边就不用再重复"

    解读：Section 1 表里已经有每个子类的占比，那把目标/偏离也放到同一张表，下面的
    Section 2/3 就不用重复展示了。本函数把表从 3 列扩到 5 列（加 target + delta），
    build_portfolio_card 同时删掉 Section 2/3 的引用。

    5 列含义：
    - 分类（含 #. 前缀，例 "1. A 股股票"）：子类中文名
    - 市值(¥)：当前市值（CNY，千分位逗号）
    - 占比：当前权重（%，2 位小数）
    - 目标：动态目标
      - 投资子类（10 个）：subclass_internal_weight × super_investment_weight × (1 − 当前现金%)
      - 现金（1 个）："[15%, 50%]"，区间字符串（不是百分比）
    - 偏离：当前 − 目标（pp 后缀）
      - 投资子类（10 个）："±X.Xpp"（正 = 超配，负 = 低配）
      - 现金（1 个）："区间内" / "低于下限" / "高于上限"（状态文本，跟 Section 2 风格一致）

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
    current_by_super = compute_super_category_breakdown(breakdown)
    current_cash = current_by_super[SuperCategory.CASH]
    cash_range = strategy.cash_range

    rows: list[dict[str, object]] = []
    for idx, b in enumerate(breakdown, start=1):
        sub = b["subclass"]
        weight_pct = float(b["weight"]) * 100

        if sub == SwensenClass.CASH:
            # 现金行：target 是区间字符串，delta 是状态文本（区间内/低于下限/高于上限）
            target_str = cash_range.display_range
            delta_str = cash_range.status(b["weight"])
        else:
            # 投资子类：target = subclass × super × (1 − 现金%)（动态公式）
            actual_target = compute_subclass_actual_target(strategy, sub, current_cash)
            assert actual_target is not None  # 10 个非现金子类都有权重
            target_pct = float(actual_target) * 100
            target_str = f"{target_pct:.1f}%"
            # delta = 当前 - 目标（pp 后缀，正 = 超配，负 = 低配）
            delta_pct = weight_pct - target_pct
            sign = "+" if delta_pct >= 0 else ""
            delta_str = f"{sign}{delta_pct:.1f}pp"

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


def build_portfolio_card(
    journal: PortfolioJournal,
    title: str | None = None,
) -> dict[str, object]:
    """构造实盘账本的飞书交互卡片。

    卡片结构（spec 097 第十九轮 — 1 个 section 整合持仓 + 策略对比）：
    - header.title: "实盘周报"
    - 实盘持仓 section（卡片唯一 section）：
      - note header "实盘持仓"（浅灰背景块，作为 section 标题）
      - summary div（生成时间 / 总市值 / 总成本 / 浮动盈亏 / 周涨跌 / 累计涨跌）
      - hr 分隔
      - 大类资产柱状图（vertical bar，11 个子类，柱子顶部带数值标签 — 第十九轮加）
      - hr 分隔
      - 持仓聚合表（11 行 × 5 列：分类 / 市值 / 占比 / 目标 / 偏离）

    第十九轮（liubo 2026-09-19）：
    - 把 Section 2（大类资产策略）+ Section 3（大类资产明细）的策略对比表
      合并到 Section 1 持仓表（加 目标 / 偏离 列）
    - liubo 反馈："第一部分持仓里边其实每一个大类的占比都有，干脆就把
      策略里边的目标和偏离放在这张表里了，然后那上边后边就不用再重复"
    - 结果：卡片从 3 个 section 简化为 1 个 section，信息密度不变（target/delta
      不再重复展示两次）
    - 柱状图加 data labels（label.visible + position="top"）— 之前默认不显示
      数值，liubo 反馈"股票和港股的上边那个数字怎么没在上面了，希望它在上面"

    元素总数：1 note + 1 div + 2 hr + 1 chart + 1 table = 6 + footer。
    """
    actual_title = title or "实盘周报"
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
            # 实盘持仓 section（第十九轮：卡片唯一 section，note 仍作浅灰标题块）
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "实盘持仓",
                    }
                ],
            },
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
