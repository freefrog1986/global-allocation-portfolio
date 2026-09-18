"""飞书 chart card for portfolio journal。

参照 specs/090-portfolio-journal.md + specs/096-portfolio-card-redesign.md。

设计原则（spec 096 第二轮）：
- 只回答用户三个问题：现在整体怎么样 / 大类资产怎么分布 / 每个大类具体占多少
- 3 段元素：summary div + 大类资产柱状图 + 按大类聚合的持仓表
- 柱状图 + 表都按 Swensen 框架顺序排（不是市值倒序）
- 表不再下钻到单只基金，只到"大类 / 市值 / 占比"
- 14 个子类全部展示（含 count=0 的——这样能看出框架里哪些没覆盖到）
- 不再包含 pie / line chart / 单只基金明细 / 交易流水表
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from global_allocation.portfolio.breakdown import compute_breakdown
from global_allocation.portfolio.journal import PortfolioJournal


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
    """各大类资产占比柱状图（vertical bar，X 轴 = 类名，Y 轴 = 占比 %）。

    第三轮反馈：纵坐标改成占比（用户更关心配置比例而非绝对金额）。
    weight 原始值是 0~1 的 Decimal，乘以 100 转成百分比数字交给 VChart（Y 轴
    会显示成 0/5/10/.../30，标题"各大类资产占比（%）"明示单位）。

    按 SwensenClass 枚举自然顺序展示全部 14 个子类（含 count=0 的——这样能直观看到
    哪些子类没覆盖到，是配置漏洞）。空子类 weight=0 在柱状图上不画柱子、保留 X 轴标签。

    飞书 VChart 柱状图 = simple 格式：type="bar" + data.values + xField/yField
    （column 是 VChart 内部名，飞书对外只认 "bar"；不加 direction 默认就是垂直柱状图）
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
        # 第十一轮最终决定（liubo 2026-09-18）：不加 % 后缀。
        # 飞书 VChart 卡片组件不支持 JS 函数（formatMethod 不能用），
        # formatter 模板字符串的 {value} 替换在飞书内置的 VChart 版本
        # （1.10.1 / 1.12.3）下不工作，显示成字面 "%Y6%"。
        # axes label formatMethod 同样飞书不解析。title 已经写了"占比（%）"
        # 明示单位，柱子顶上 VChart 默认显示 yField 数值（29.9 这种）。
    }


def _build_holdings_table(journal: PortfolioJournal) -> dict[str, object]:
    """按 Swensen 大类聚合的持仓表：分类（含 # 前缀）+ 市值 + 占比。

    spec 096：
    - 第二轮反馈：用户不要"细致到具体基金"，表只回答"我每个大类持了多少"
    - 第三轮反馈：表加序号信息
    - 第四轮反馈：# 列太宽了
    - 第五轮反馈（实测后的折中）：Feishu table 列 width 只接受 "auto"，其它值（short/
      medium/long/数字）API 都拒。所以把 # 信息嵌进分类名前缀（"1. A 股股票"），
      干掉单独 # 列——既保留序号信息，又没有多余宽列

    按 SwensenClass 枚举顺序展示全部 14 个子类（含 count=0 的——空子类显示 0 元 / 0.00%，
    这样能直观看到 Swensen 框架里哪些子类没覆盖到）。

    Feishu 表格 row 必须是 dict（按列名取）；所有列 data_type=text（value/weight 是预格式化的字符串）。
    """
    breakdown = compute_breakdown(journal)  # 已经是 SwensenClass 枚举顺序

    rows: list[dict[str, object]] = [
        {
            "class": f"{idx}. {b['display_name']}",  # 序号嵌进分类名前缀
            "value": f"{float(b['value']):,.2f}",
            "weight": f"{float(b['weight']) * 100:.2f}%",
        }
        for idx, b in enumerate(breakdown, start=1)
    ]

    return {
        "columns": [
            # 全部 width="auto"（实测 Feishu table 只接受 "auto"；short/medium/long/数字都拒）
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
