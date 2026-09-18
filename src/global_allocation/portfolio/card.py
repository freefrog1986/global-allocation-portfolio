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

from global_allocation.portfolio.breakdown import DISPLAY_NAME, SwensenClass, compute_breakdown
from global_allocation.portfolio.journal import PortfolioJournal
from global_allocation.portfolio.strategy import (
    DEFAULT_STRATEGY,
    SUPER_CATEGORY_DISPLAY_NAME,
    AllocationStrategy,
    SuperCategory,
    compute_actual_target,
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


def _build_strategy_section(
    journal: PortfolioJournal,
    strategy: AllocationStrategy = DEFAULT_STRATEGY,
) -> list[dict[str, object]]:
    """Section 2: 大类资产策略 — 4 投资类内部权重 + 现金区间 + 当前实际 + 偏离。

    spec 097 第十七轮（liubo 2026-09-19）：去掉了文字摘要 div，只留一张表。

    liubo 反馈：
    - "具体策略" 标题不准确 — 应该叫 "大类资产策略"（策略本身就是关于大类资产的）
    - 文字摘要冗余 — 表里已经覆盖了所有必要信息
    - 现金区间已经在表里（最后一行），4 投资类内部权重也在（前 4 行 target 列）
    - 子类上限不进表（是"硬约束"语义，混在 target 里会让用户混淆"目标"和"上限"）

    表格 5 行 × 4 列：
    - 投资类 4 行：target = internal_weight × (1 − 当前现金占比) — 动态计算
    - 现金 1 行：target = "[15%, 50%]"，delta = "区间内/低于下限/高于上限"

    为什么 target 是动态计算：
    - 现金区间 [15%, 50%] 不固定 → 投资部分 = 100% − 现金%（变量）
    - 投资部分按内部权重分配（股票 70% / REITs 15% / 债券 10% / 商品 5%）
    - 现金越多，投资部分越少 → 各投资类实际目标越小
    - 现金越少，投资部分越多 → 各投资类实际目标越大
    """
    breakdown = compute_breakdown(journal)
    current_by_super = compute_super_category_breakdown(breakdown)
    cash_range = strategy.cash_range
    current_cash = current_by_super[SuperCategory.CASH]

    # table: 5 行（4 投资类 + 1 现金）× 4 列
    rows: list[dict[str, object]] = []
    # 按 SuperCategory 枚举顺序遍历（EQUITY → BOND → REIT → COMMODITY → CASH）
    for cat in SuperCategory:
        current_pct = float(current_by_super[cat]) * 100
        current_str = f"{current_pct:.1f}%"

        if cat == SuperCategory.CASH:
            # 现金走区间策略：target 列显示区间，delta 列显示状态文本
            target_str = cash_range.display_range
            delta_str = cash_range.status(current_by_super[cat])
        else:
            # 投资类走内部权重：target = 内部权重 × (1 − 当前现金占比)
            # 动态公式 — 现金变时目标自动缩放
            actual_target = compute_actual_target(strategy, cat, current_cash)
            assert actual_target is not None  # 4 投资类都有权重
            target_pct = float(actual_target) * 100
            target_str = f"{target_pct:.0f}%"
            delta_pct = current_pct - target_pct
            sign = "+" if delta_pct >= 0 else ""
            delta_str = f"{sign}{delta_pct:.1f}pp"  # pp = percentage points

        rows.append(
            {
                "category": SUPER_CATEGORY_DISPLAY_NAME[cat],
                "target": target_str,
                "current": current_str,
                "delta": delta_str,
            }
        )

    table: dict[str, object] = {
        "columns": [
            {"name": "category", "display_name": "超类", "data_type": "text", "width": "auto"},
            {"name": "target", "display_name": "目标", "data_type": "text", "width": "auto"},
            {"name": "current", "display_name": "当前", "data_type": "text", "width": "auto"},
            {"name": "delta", "display_name": "偏离", "data_type": "text", "width": "auto"},
        ],
        "rows": rows,
    }

    return [
        {"tag": "note", "elements": [{"tag": "plain_text", "content": "大类资产策略"}]},
        {"tag": "hr"},
        {"tag": "table", **table},
    ]


def _build_subclass_section(
    journal: PortfolioJournal,
    strategy: AllocationStrategy = DEFAULT_STRATEGY,
) -> list[dict[str, object]]:
    """Section 3: 大类资产明细 — 10 个子类的目标 / 当前 / 偏离。

    spec 097 第十七轮（liubo 2026-09-18）：Layer 3 子类内部权重落地展示。

    表格 10 行 × 4 列（不含现金 — 现金已在 Section 2）：
    - 股票 5 行（A股 / 美股 / 港股 / 国外发达 / 新兴市场）
    - REITs 2 行（国内 / 美国）
    - 债券 2 行（国内利率债 / 美债）
    - 商品 1 行（只有一个子类）

    target = subclass_internal_weight × super_investment_weight × (1 − 当前现金%)
    例（默认策略，现金 30%）：
    - A 股目标 = 40% × 70% × 70% = 19.6%
    - 美股目标 = 20% × 70% × 70% = 9.8%
    - 港股目标 = 15% × 70% × 70% = 7.35%

    delta 列：
    - 偏离 = 当前 − 目标（pp 后缀，跟 Section 2 风格一致）
    - 投资子类都走百分比，所以 delta 全是 pp

    为什么单独 Section（不是 Section 2 的延伸）：
    - Section 2 看的是 4 大类整体（股票 49% / REITs 10.5% / 债券 7% / 商品 3.5%）
    - Section 3 看的是每个具体子类的目标（A股 19.6% / 美股 9.8% / ...）
    - 用户需要分别知道"大类偏离"和"具体子项偏离"，粒度不同
    """
    breakdown = compute_breakdown(journal)
    current_by_super = compute_super_category_breakdown(breakdown)
    current_cash = current_by_super[SuperCategory.CASH]

    # 按 SwensenClass 枚举顺序遍历（11 个子类，跳过 CASH）
    rows: list[dict[str, object]] = []
    for sub in SwensenClass:
        if sub == SwensenClass.CASH:
            continue  # 现金不在 Section 3（已在 Section 2 展示子弹区间）

        # 当前占比：从 breakdown 查 weight
        current_weight = next(
            (row["weight"] for row in breakdown if row["subclass"] == sub),
            Decimal("0"),
        )
        current_pct = float(current_weight) * 100
        current_str = f"{current_pct:.1f}%"

        # 目标占比：子类内部权重 × 超类内部权重 × (1 − 现金%)
        actual_target = compute_subclass_actual_target(strategy, sub, current_cash)
        assert actual_target is not None  # 10 个非现金子类都有权重
        target_pct = float(actual_target) * 100
        target_str = f"{target_pct:.1f}%"

        # 偏离
        delta_pct = current_pct - target_pct
        sign = "+" if delta_pct >= 0 else ""
        delta_str = f"{sign}{delta_pct:.1f}pp"

        rows.append(
            {
                "subclass": DISPLAY_NAME[sub],
                "target": target_str,
                "current": current_str,
                "delta": delta_str,
            }
        )

    table: dict[str, object] = {
        "columns": [
            {"name": "subclass", "display_name": "大类资产", "data_type": "text", "width": "auto"},
            {"name": "target", "display_name": "目标", "data_type": "text", "width": "auto"},
            {"name": "current", "display_name": "当前", "data_type": "text", "width": "auto"},
            {"name": "delta", "display_name": "偏离", "data_type": "text", "width": "auto"},
        ],
        "rows": rows,
    }

    return [
        {"tag": "note", "elements": [{"tag": "plain_text", "content": "大类资产明细"}]},
        {"tag": "hr"},
        {"tag": "table", **table},
    ]


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

    按 SwensenClass 枚举顺序展示全部 11 个子类（含 count=0 的——空子类显示 0 元 / 0.00%，
    这样能直观看到 Swensen 框架里哪些子类没覆盖到；spec 097 第十七轮精简到 11）。

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

    卡片结构（spec 096 + spec 097 第十七轮 — 3 个 section）：
    - header.title: "实盘周报"
    - Section 1（实盘持仓）：
      - note header "实盘持仓"
      - summary div（生成时间 / 总市值 / 总成本 / 浮动盈亏 / 周涨跌 / 累计涨跌）
      - hr 分隔
      - 大类资产柱状图（vertical bar，11 个子类）
      - hr 分隔
      - 持仓聚合表（11 行 × 3 列：分类 / 市值 / 占比）
    - Section 2（大类资产策略 — 第十七轮：去文字摘要，只留 1 张表）：
      - hr 分隔（跨 section）
      - note header "大类资产策略"
      - hr 分隔
      - 策略对比表（5 行 × 4 列：超类 / 目标 / 当前 / 偏离）
        - 投资类 4 行：target = 内部权重 × (1 − 当前现金占比)（动态）
        - 现金 1 行：target = "[15%, 50%]"，delta = "区间内/低于下限/高于上限"
      - 第十七轮改动：之前这里有 div 文字摘要（现金区间 + 4 投资类内部权重 +
        Layer 1 摘要 + 子类内部权重索引），liubo 反馈文字摘要冗余（表里已经覆盖了
        所有信息），删掉
    - Section 3（大类资产明细 — 第十七轮新增：Layer 3 子类内部权重落地）：
      - hr 分隔（跨 section）
      - note header "大类资产明细"
      - hr 分隔
      - 子类对比表（10 行 × 4 列：大类资产 / 目标 / 当前 / 偏离；不含现金）
        - target = subclass_weight × super_weight × (1 − 当前现金占比)（动态）

    未来扩展（spec 096 第十三轮预留）：Section 4+ 按 SwensenClass 划分（每个大类资产一个 section）。
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
            # Section 1: 实盘持仓 — note 做 section header（浅灰背景块）
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
            # Section 2: 具体策略（spec 097 第十六轮 + 第十七轮）
            {"tag": "hr"},
            *_build_strategy_section(journal),
            # Section 3: 大类资产明细（spec 097 第十七轮新增 — Layer 3）
            {"tag": "hr"},
            *_build_subclass_section(journal),
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
