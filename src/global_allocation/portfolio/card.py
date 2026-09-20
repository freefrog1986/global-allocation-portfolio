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
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from global_allocation.portfolio.breakdown import SwensenClass, compute_breakdown, get_subclass
from global_allocation.portfolio.journal import PortfolioJournal
from global_allocation.portfolio.models import (
    FundValuation,
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
        return []

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
    today = date.today()
    indicators = journal.db.list_valuation_indicators_for_date(today)
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
    """
    breakdown = compute_breakdown(journal)
    region_row = next((b for b in breakdown if b["subclass"] == subclass), None)
    if region_row is None:
        return None, False

    region_weight = float(region_row["weight"]) * 100
    current_by_super = compute_super_category_breakdown(breakdown)
    current_cash = current_by_super[SuperCategory.CASH]
    region_target = compute_subclass_actual_target(DEFAULT_STRATEGY, subclass, current_cash)
    assert region_target is not None
    region_target_pct = float(region_target) * 100
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
    strategy_note: dict[str, object] = {
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
    - Section 3（A 股资产估值 — spec 098 第二十一轮新增，第二十二轮加综合分）：
      - hr 分隔（跨 section）
      - note header "A 股资产估值"
      - hr 分隔
      - 估值表（5 行 × 4 列：指标 / 当前 / 评估 / 阈值）
        - 4 个 A 股指标：股债利差 / PE 分位 / 巴菲特指标 / 股息率
        - 1 个综合分行：value = "[PE:4 股债:2 巴菲特:3 股息:3]"，
          verdict = "3.0 正常"，threshold = "1=极低估 5=极高估"
        - 数据缺失显示"数据缺失"（akshare 接口失败时）
        - DB 完全空时整段不渲染（publish 时 CLI 已自动 update）

    第十九轮（liubo 2026-09-19）：合并 Section 2 + 3 到 Section 1 持仓表（加 target/delta）
    第二十轮（liubo 2026-09-19）：恢复 Section 2，保留 Section 3 删除状态
    第二十一轮（spec 098）：新增 Section 3（A 股估值）— 跟持仓/Section 2 完全不同维度
    第二十二轮（liubo 2026-09-19）：改标题 "大类资产估值" → "A 股资产估值"（明确只覆盖 A 股）；
        加 1-5 分综合分行（4 票简单平均，不加权）
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
