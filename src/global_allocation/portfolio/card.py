"""飞书 chart card for portfolio journal。

参照 specs/090-portfolio-journal.md + specs/096-portfolio-card-redesign.md
+ specs/097-strategy-spike.md + specs/098-valuation-section.md。

设计原则（spec 097 第十九轮 — 单 section 整合）：
- 只回答用户三个问题：现在整体怎么样 / 大类资产怎么分布 / 每个大类具体占多少
- 1 段元素：summary div + 大类资产柱状图 + 5 列聚合持仓表（含 target/delta）
- 柱状图带顶部数值标签（label.visible + position="top"）— 第十九轮加
- 表不再下钻到单只基金，只到"分类 / 市值 / 占比 / 目标 / 偏离"
- 11 个子类全部展示（含 count=0 的——这样能看出框架里哪些没覆盖到）
- 不再包含 pie / line chart / 单只基金明细 / 交易流水表
- 不再包含独立的"大类资产策略"和"大类资产明细" section（合并到持仓表）

第二十轮：恢复 Section 2（大类资产策略），保留 Section 3 删除状态。
第二十一轮（spec 098）：新增 Section 3 — 大类资产估值（A 股 4 个指标）。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from global_allocation.portfolio.breakdown import SwensenClass, compute_breakdown
from global_allocation.portfolio.journal import PortfolioJournal
from global_allocation.portfolio.models import (
    ValuationIndicator,
    ValuationIndicatorCode,
)
from global_allocation.portfolio.strategy import (
    DEFAULT_STRATEGY,
    SUPER_CATEGORY_DISPLAY_NAME,
    AllocationStrategy,
    SuperCategory,
    compute_actual_target,
    compute_subclass_actual_target,
    compute_super_category_breakdown,
)
from global_allocation.portfolio.valuation_indicators import compute_verdict


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
    """Section 2: 大类资产策略 — 5 行 × 4 列超类对比表（股票/REITs/债券/商品/现金）。

    spec 097 第二十轮（liubo 2026-09-19）：第十九轮删了 Section 2/3 后用户反馈
    "我还是要看一下超类的，因为我担心没控制好这个超类的比例了" — 重新加回来。

    跟第十九轮之前对比：
    - 之前 Section 2 + Section 3 都展示 target/current/delta
    - 第十九轮合并 Section 2 + 3 到 Section 1（持仓表加 target/delta 列）
    - 第二十轮恢复 Section 2（但 Section 3 仍然删除 — 子类粒度的 target/delta 已在持仓表）

    跟持仓表的"区别"在哪（不重复在哪）：
    - 持仓表（Section 1）：子类粒度，11 行（A股/港股/.../现金）
      target = subclass × super × (1 - 现金%)
    - Section 2：超类粒度，5 行（股票/REITs/债券/商品/现金）
      target = super 内部权重 × (1 - 现金%)
    - 两者的 target 不严格相等（持仓表按子类累加 vs Section 2 直接按超类公式）
      但都在 1% 误差内，反映同一意图
    - 用户担心的"超类比例失控"主要看 Section 2（一眼看出股票 49%、REITs 10.5%）

    表格 5 行 × 4 列：
    - 4 投资类（股票/REITs/债券/商品）：target = internal_weight × (1 − 当前现金占比)
      delta = current − target（pp 后缀）
    - 1 现金：target = "[15%, 50]%"，delta = "区间内/低于下限/高于上限"

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
            delta_str = f"{sign}{delta_pct:.1f}pp"

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


def _build_valuation_section(journal: PortfolioJournal) -> list[dict[str, object]]:
    """Section 3: 大类资产估值（spec 098 — 第一期仅 A 股）。

    卡片结构：note header + hr + 4 行 × 4 列表格。
    4 行：股债利差 / PE 分位 / 巴菲特指标 / 股息率（按 ValuationIndicatorCode enum 顺序）。
    4 列：指标 / 当前 / 评估 / 阈值。

    数据缺失处理：DB 缺哪个指标就显示"数据缺失"（spec 098 第 170 行）。
    DB 完全空（今天没拉过估值）→ Section 3 整段不渲染（避免空表格）。
    """
    today = date.today()
    indicators = journal.db.list_valuation_indicators_for_date(today)
    if not indicators:
        # DB 里今天没有任何指标 → 不渲染 Section 3（spec 098 卡片方案）
        # publish 时 CLI 已自动调 update，所以正常情况不会到这里。
        return []

    by_code: dict[ValuationIndicatorCode, ValuationIndicator] = {
        i.indicator_code: i for i in indicators
    }

    # 4 个指标的中文名 + 阈值文案（spec 098 第 99~110 行）
    labels: dict[ValuationIndicatorCode, tuple[str, str]] = {
        ValuationIndicatorCode.EQUITY_RISK_PREMIUM: ("股债利差", ">5% 低 / <2% 高"),
        ValuationIndicatorCode.PE_PERCENTILE: ("PE 分位", "<30% 低 / >70% 高"),
        ValuationIndicatorCode.BUFFETT_INDICATOR: ("巴菲特指标", "<50% 低 / >80% 高"),
        ValuationIndicatorCode.DIVIDEND_YIELD: ("股息率", ">3% 低 / <1% 高"),
    }

    def _format_value(code: ValuationIndicatorCode, value: Decimal) -> str:
        """指标当前值的格式化字符串（spec 098 第 106~110 行）。"""
        pct = float(value) * 100
        if code == ValuationIndicatorCode.EQUITY_RISK_PREMIUM:
            # 股债利差：保留符号（正 = 股票相对债券有溢价）+2 位小数
            return f"{pct:+.2f}%"
        # 其他 3 个指标：2 位小数（百分比）
        return f"{pct:.2f}%"

    rows: list[dict[str, object]] = []
    for code in ValuationIndicatorCode:
        name, threshold = labels[code]
        if code in by_code:
            ind = by_code[code]
            current_str = _format_value(code, ind.value)
            verdict_str = compute_verdict(code, ind.value)
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

    table: dict[str, object] = {
        "columns": [
            {"name": "indicator", "display_name": "指标", "data_type": "text", "width": "auto"},
            {"name": "value", "display_name": "当前", "data_type": "text", "width": "auto"},
            {"name": "verdict", "display_name": "评估", "data_type": "text", "width": "auto"},
            {"name": "threshold", "display_name": "阈值", "data_type": "text", "width": "auto"},
        ],
        "rows": rows,
    }

    return [
        {"tag": "note", "elements": [{"tag": "plain_text", "content": "大类资产估值"}]},
        {"tag": "hr"},
        {"tag": "table", **table},
    ]


def build_portfolio_card(
    journal: PortfolioJournal,
    title: str | None = None,
) -> dict[str, object]:
    """构造实盘账本的飞书交互卡片。

    卡片结构（spec 098 第二十一轮 — 3 个 section：持仓 + 超类策略 + 估值）：
    - header.title: "实盘周报"
    - Section 1（实盘持仓）：
      - note header "实盘持仓"
      - summary div（生成时间 / 总市值 / 总成本 / 浮动盈亏 / 周涨跌 / 累计涨跌）
      - hr 分隔
      - 大类资产柱状图（vertical bar，11 个子类，柱子顶部带数值标签 — 第十九轮加）
      - hr 分隔
      - 持仓聚合表（11 行 × 5 列：分类 / 市值 / 占比 / 目标 / 偏离 — 第十九轮扩列）
    - Section 2（大类资产策略 — spec 097 第二十轮恢复）：
      - hr 分隔（跨 section）
      - note header "大类资产策略"
      - hr 分隔
      - 策略对比表（5 行 × 4 列：超类 / 目标 / 当前 / 偏离）
        - 4 投资类：target = 内部权重 × (1 − 当前现金占比)（动态）
        - 现金：target = "[15%, 50%]"，delta = "区间内/低于下限/高于上限"
    - Section 3（大类资产估值 — spec 098 第二十一轮新增）：
      - hr 分隔（跨 section）
      - note header "大类资产估值"
      - hr 分隔
      - 估值表（4 行 × 4 列：指标 / 当前 / 评估 / 阈值）
        - 4 个 A 股指标：股债利差 / PE 分位 / 巴菲特指标 / 股息率
        - 数据缺失显示"数据缺失"（akshare 接口失败时）
        - DB 完全空时整段不渲染（publish 时 CLI 已自动 update）

    第十九轮（liubo 2026-09-19）：合并 Section 2 + 3 到 Section 1 持仓表（加 target/delta）
    第二十轮（liubo 2026-09-19）：恢复 Section 2，保留 Section 3 删除状态
    第二十一轮（spec 098）：新增 Section 3（A 股估值）— 跟持仓/Section 2 完全不同维度
    - Section 1/2 是用户持仓的"账面"信息（"我有什么 / 偏离目标多少"）
    - Section 3 是"市场给当前大类的报价"（"现在加仓合不合适"）
    - 用户场景：看完持仓 → 想知道"现在该不该加仓" → 看 Section 3 评估

    元素总数（无估值）：2 note + 1 div + 3 hr + 1 chart + 2 table = 9 + footer。
    元素总数（有估值）：3 note + 1 div + 4 hr + 1 chart + 3 table = 12 + footer。
    """
    actual_title = title or "实盘周报"
    holdings = journal.compute_holdings()
    if not holdings:
        raise ValueError("没有持仓，无法生成卡片")

    elements: list[dict[str, object]] = [
        # Section 1: 实盘持仓
        {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": "实盘持仓"}],
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": _build_summary(journal, actual_title),
            },
        },
        {"tag": "hr"},
        {"tag": "chart", "chart_spec": _build_breakdown_bar(journal)},
        {"tag": "hr"},
        {"tag": "table", **_build_holdings_table(journal)},
        # Section 2: 大类资产策略（spec 097 第二十轮恢复）
        {"tag": "hr"},
        *_build_strategy_section(journal),
    ]

    # Section 3: 大类资产估值（spec 098 第二十一轮新增）
    valuation_section = _build_valuation_section(journal)
    if valuation_section:
        elements.append({"tag": "hr"})
        elements.extend(valuation_section)

    card: dict[str, object] = {
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": actual_title,
            },
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
    return card


def card_to_json(card: dict[str, object]) -> str:
    """序列化（ensure_ascii=False 保中文）。"""
    return json.dumps(card, ensure_ascii=False)


__all__ = ["build_portfolio_card", "card_to_json"]
