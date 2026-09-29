"""PE-TTM 周调仓策略（spec 022，liubo 2026-09-24 拍板）。

设计原则：
- 每周五评估 PE-TTM 估值分位（10 年窗口），按规则生成加/减仓动作。
- 同一 ETF 的不同基金合并计算仓位（中证 A500 共 3 只 → 当 1 仓评估）。
- 1 仓位 = 1 万 CNY（跟 PositionAllocation 对齐）。

调仓规则（liubo 2026-09-24 拍板 + 2026-09-24 二次细化）：
- 仓位 = 0：
  - 分位 < 50% → BUILD +1 仓（建仓信号）
  - 分位 >= 50% → HOLD
- 仓位 > 0 且 < 1 仓（欠配）：
  - 分位 < 50% → ADD +1 仓（凑到 1 仓目标）
  - 50% <= 分位 <= 80% → HOLD
  - 分位 > 80% → REDUCE -1 仓
- 仓位 >= 1 仓（已配足）：
  - 分位 < 20% → ADD +1 仓（深价值例外）
  - 20% <= 分位 <= 80% → HOLD
  - 分位 > 80% → REDUCE -1 仓
- 数据缺失 → SKIP

月度冷却期（liubo 2026-09-25 拍板）：
- 调仓频率：按月，同一 ETF 距上次调仓 < COOLDOWN_DAYS (30) 天 → 把 ADD/REDUCE/BUILD 转 HOLD
- 实际调仓后手动更新 LAST_REBALANCE_BY_ETF 对应 ETF 的日期

逻辑要点：分位 < 50% + 仓位 < 1 是「欠配 + 便宜」的组合信号——既低于价值中点、仓位又没到目标，应该补到 1 仓；仓位已 ≥ 1 后，只在深价值（< 20%）才继续加，避免单一子类过度向 1 仓以上累积。

估值指标（liubo 2026-09-24 扩展）：
- A 股 / 美股 / 港股 / 国外发达 / 新兴市场：用 PE-TTM（PE-TTM 是默认）。
- US REIT：用 P/FFO（MSCI/NAREIT 标准；REITs 用 PE 会被折旧扭曲）。
- 中证 REITs：用 P/NAV（招商/中金/华泰国内券商惯例）。
- 商品（黄金）：用 PE 不适用，标 PE 数据缺失 → SKIP。

子类 / watchlist 处理：
- 持仓基金走 FUND_INDEX_MAP（PE_SNAPSHOT_BY_INDEX 取指标）。
- 无持仓但想跟踪的指数走 INDEX_WATCHLIST（如 HSI 恒生指数）—— 0 仓位也会触发
  BUILD（提醒建仓）或 HOLD（等便宜），跟跑分数显示「无持仓」标签。

数据来源（snapshot 写死在 PE_SNAPSHOT_BY_INDEX + INDEX_WATCHLIST）：
- A 股 PE：理杏仁 CSV（2026-09-24 总市值加权 10 年窗口）。
- 美股 PE：理杏仁 INX + WebSearch NDX/DAX/CAC40/日经。
- 新兴市场 PE：worldperatio / siblisresearch（2026-09-04）。
- US REIT P/FFO：MSCI factsheet + NAREIT（2026-08-31）。
- 中证 REITs P/NAV：招商证券 REITs 月报（2026-08-31）。
- HSI PE：hsi.com.hk / lixinger / 百分位网（2026-09）。
- 后续接 akshare fetcher 自动拉。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Iterable

from global_allocation.portfolio.breakdown import SwensenClass
from global_allocation.portfolio.cost_basis import COST_BASIS_BY_CODE


# ─── 类型 ──────────────────────────────────────────


class ValuationMetric(str, Enum):
    """估值指标类型（liubo 2026-09-24 扩展，支持 REITs 用 P/FFO / P/NAV）。"""

    PE_TTM = "pe_ttm"  # 股票类（PE-TTM）
    P_FFO = "p_ffo"    # US REIT（MSCI/NAREIT 标准）
    P_NAV = "p_nav"    # 中证 REITs（国内券商惯例）


# 指标在卡片 / 报告里的人类可读名
METRIC_DISPLAY_NAME: dict[ValuationMetric, str] = {
    ValuationMetric.PE_TTM: "PE-TTM",
    ValuationMetric.P_FFO: "P/FFO",
    ValuationMetric.P_NAV: "P/NAV",
}


class PESignal(str, Enum):
    """估值分位调仓信号（liubo 2026-09-24 拍板的 5 个）。"""

    ADD = "add"          # 加仓 1 仓位（有仓位 + 分位 < 20%）
    REDUCE = "reduce"    # 减仓 1 仓位（有仓位 + 分位 > 80%）
    BUILD = "build"      # 建仓 1 仓位（无仓位 + 分位 < 50%）
    HOLD = "hold"        # 不动（仓位 OK + 分位 20-80% 或仓位=0 分位>=50%）
    SKIP = "skip"        # 数据缺失 / 不适用


@dataclass(frozen=True, slots=True)
class FundPEvaluation:
    """单只基金（或合并后的 ETF / watchlist 指数）的评估输入。

    字段：
    - fund_code: 基金代码（合并组用 "+" 连接；watchlist 用 "(INDEX_CODE)" 标记）。
    - fund_name: 基金中文名（飞书表格展示用）。
    - subclass: SwensenClass 子类。
    - etf_index_code: 跟踪指数的代码（如 "000510" 中证 A500、".INX" 标普 500、"HSI" 恒生）。
    - etf_index_name: 指数中文名。
    - metric: 估值指标（PE-TTM / P-FFO / P-NAV）。
    - metric_value: 估值倍数（None = 数据缺失；PE 15.89 / P/FFO 19.7 / P/NAV 1.03）。
    - metric_percentile: 10 年分位（None = 数据缺失；fraction, 0.1694 = 16.94%）。
    - current_position: 当前仓位（单位 = 1 万 CNY；cost / 10000）。
    """

    fund_code: str
    fund_name: str
    subclass: SwensenClass
    etf_index_code: str
    etf_index_name: str
    metric: ValuationMetric
    metric_value: Decimal | None
    metric_percentile: Decimal | None
    current_position: Decimal


@dataclass(frozen=True, slots=True)
class RebalanceAction:
    """单只基金（或合并 ETF / watchlist 指数）的调仓建议。"""

    fund_code: str
    fund_name: str
    subclass: SwensenClass
    etf_index_code: str
    etf_index_name: str
    metric: ValuationMetric
    signal: PESignal
    change: Decimal        # +1 = 加 1 万, -1 = 减 1 万, 0 = 不变
    reason: str            # 中文说明（飞书表格展示用）
    metric_value: Decimal | None
    metric_percentile: Decimal | None
    current_position: Decimal


# ─── 硬编码估值 snapshot (2026-09-24 liubo 灌的数据 + WebSearch) ────────


# PE_SNAPSHOT_BY_INDEX: 指数代码 → (估值指标, 倍数, 10年分位)
# 注意：分位值是 fraction（0.1694 = 16.94%），不是整数百分比。
#
# 数据来源（2026-09-28 末）：
# - A 股: 理杏仁 CSV "frog 勿删_总市值加权_10年_20260928_222805.csv"
# - 美股 INX/OEX: 同上 CSV（OEX 用 INX 代理）
# - 美股 NDX: WebSearch 2026-09-14 PE 28.68, 分位 ~60% (lixinger 09-28 没数据，仍用旧值)
# - 国外发达 (N225/GDAXI/FCHI): WebSearch 最新值
# - 新兴市场: worldperatio + siblisresearch 2026-09-04（PE 14.3 / 分位 60%）
# - US REIT: MSCI factsheet + NAREIT 2026-08-31（P/FFO 19.7 / 分位 60%）
# - 中证 REITs: 招商证券 2026-08-31（P/NAV 1.03 / 分位 34% — 指数只有 5 年历史）
# - HSI: hsi.com.hk / 百分位网 2026-09-28（PE 10.8557 / 分位 58.06%）
# - 000903 中证 A100: liubo 2026-09-28 新加进 snapshot（PE 16.2030 / 分位 89.65%）
PE_SNAPSHOT_BY_INDEX: dict[str, tuple[ValuationMetric, Decimal, Decimal]] = {
    # ── A 股（PE-TTM）──
    "000510": (ValuationMetric.PE_TTM, Decimal("15.4577"), Decimal("0.4303")),  # 中证 A500
    "000852": (ValuationMetric.PE_TTM, Decimal("41.9057"), Decimal("0.6781")),  # 中证 1000
    "000903": (ValuationMetric.PE_TTM, Decimal("16.2030"), Decimal("0.8965")),  # 中证 A100（liubo 2026-09-28 加）
    "930050": (ValuationMetric.PE_TTM, Decimal("15.5418"), Decimal("0.1342")),  # 中证 A50
    "931643": (ValuationMetric.PE_TTM, Decimal("47.7959"), Decimal("0.6187")),  # 科创创业 50
    # ── 美股（PE-TTM）──
    ".INX": (ValuationMetric.PE_TTM, Decimal("26.1363"), Decimal("0.6208")),    # 标普 500
    ".NDX": (ValuationMetric.PE_TTM, Decimal("28.68"), Decimal("0.60")),        # 纳斯达克 100（09-28 CSV 空）
    ".OEX": (ValuationMetric.PE_TTM, Decimal("26.1363"), Decimal("0.6208")),    # 标普 100（INX 代理）
    # ── 国外发达（PE-TTM）──
    ".N225": (ValuationMetric.PE_TTM, Decimal("22.0"), Decimal("0.65")),        # 日经 225
    ".GDAXI": (ValuationMetric.PE_TTM, Decimal("17.91"), Decimal("0.53")),     # 德国 DAX
    ".FCHI": (ValuationMetric.PE_TTM, Decimal("17.31"), Decimal("0.82")),      # 法国 CAC 40
    # ── 新兴市场（PE-TTM）──
    ".MSCI_EM": (ValuationMetric.PE_TTM, Decimal("14.30"), Decimal("0.60")),   # MSCI Emerging Markets
    # ── 美国 REITs（P/FFO，MSCI/NAREIT 标准）──
    ".MSCI_US_REIT": (ValuationMetric.P_FFO, Decimal("19.70"), Decimal("0.60")),  # MSCI US REIT
    # ── 国内 REITs（P/NAV，招商/中金标准）──
    "932006": (ValuationMetric.P_NAV, Decimal("1.03"), Decimal("0.34")),       # 中证 REITs 指数
}


# 基金 → 跟踪指数代码 + 中文名映射
FUND_INDEX_MAP: dict[str, tuple[SwensenClass, str, str]] = {
    # fund_code → (SwensenClass, index_code, fund_name)
    # A 股股票 (6 只 → 4 个 ETF)
    "013310": (SwensenClass.CN_EQUITY, "931643", "华夏科创创业 50"),
    "022434": (SwensenClass.CN_EQUITY, "000510", "南方中证 A500"),
    "017644": (SwensenClass.CN_EQUITY, "000852", "博道中证 1000 增强"),
    "022424": (SwensenClass.CN_EQUITY, "000510", "广发中证 A500"),
    "014532": (SwensenClass.CN_EQUITY, "930050", "易方达 MSCI 中国 A50"),
    "022448": (SwensenClass.CN_EQUITY, "000510", "国泰中证 A500 联接"),
    # 美股股票 (7 只 → 3 个 ETF)
    # 第二十轮（liubo 2026-09-29）：加 019172 摩根纳斯达克100指数(QDII)人民币A → .NDX
    # 直接 QDII（不是联接），替代 4 只联接作为加仓渠道。NDX 现在合并 5 只。
    "519981": (SwensenClass.US_EQUITY, ".OEX", "长信标普 100"),
    "018966": (SwensenClass.US_EQUITY, ".NDX", "汇添富纳指 100"),
    "539001": (SwensenClass.US_EQUITY, ".NDX", "建信纳指 100"),
    "017641": (SwensenClass.US_EQUITY, ".INX", "摩根标普 500"),
    "016452": (SwensenClass.US_EQUITY, ".NDX", "南方纳指 100"),
    "019524": (SwensenClass.US_EQUITY, ".NDX", "华泰柏瑞纳指 100"),
    "019172": (SwensenClass.US_EQUITY, ".NDX", "摩根纳指 100 QDII 人民币A"),
    # 新兴市场 (1 只 → MSCI EM)
    "378006": (SwensenClass.EM_EQUITY, ".MSCI_EM", "摩根全球新兴市场"),
    # REITs / 商品 — 用各自 REITs 指标，不再 SKIP
    "028277": (SwensenClass.CN_REIT, "932006", "华夏中证 REITs"),
    "160140": (SwensenClass.US_REIT, ".MSCI_US_REIT", "南方道琼斯美国 REIT"),
    # 商品 — 黄金 PE 不适用，仍标 None 让 evaluate_fund 返回 SKIP
    "000216": (SwensenClass.COMMODITY, "GOLD_NO_METRIC", "华安黄金 ETF 联接"),
}

# Watchlist：没持仓但想跟踪的指数（liubo 2026-09-24 加 HSI 恒生指数）
# 列表项：(subclass, index_code, display_name, metric, value, percentile)
# 0 仓位也会触发 BUILD（提醒建仓）或 HOLD（等便宜）。
INDEX_WATCHLIST: list[tuple[SwensenClass, str, str, ValuationMetric, Decimal, Decimal]] = [
    (
        SwensenClass.HK_EQUITY,
        "HSI",
        "恒生指数",
        ValuationMetric.PE_TTM,
        Decimal("10.8557"),  # hsi.com.hk + 百分位网 2026-09-28
        Decimal("0.5806"),
    ),
]

# 月度冷却期（liubo 2026-09-25 拍板）：同 ETF 调仓间隔 ≥ COOLDOWN_DAYS 天。
# 实际调仓后手动更新这个表（每次执行转换 / 加减仓，更新对应 ETF 的日期）。
LAST_REBALANCE_BY_ETF: dict[str, date] = {
    "930050": date(2026, 9, 24),  # 中证 A50 — liubo 2026-09-24 转 008505 → 014532
    "932006": date(2026, 9, 24),  # 中证 REITs — liubo 2026-09-24 转 008505 → 028277
}

COOLDOWN_DAYS: int = 30  # 月度调仓最短间隔

# 指数代码 → 指数中文名
INDEX_DISPLAY_NAME: dict[str, str] = {
    "000510": "中证 A500",
    "000852": "中证 1000",
    "000903": "中证 A100",
    "930050": "中证 A50",
    "931643": "科创创业 50",
    ".INX": "标普 500",
    ".NDX": "纳斯达克 100",
    ".OEX": "标普 100",
    ".N225": "日经 225",
    ".GDAXI": "德国 DAX",
    ".FCHI": "法国 CAC 40",
    ".MSCI_EM": "MSCI 新兴市场",
    ".MSCI_US_REIT": "MSCI US REIT",
    "932006": "中证 REITs",
    "HSI": "恒生指数",
    "GOLD_NO_METRIC": "黄金 (无估值指标)",
}


# ─── 核心逻辑 ──────────────────────────────────────────


def evaluate_fund(
    eval: FundPEvaluation,
    today: date | None = None,
) -> RebalanceAction:
    """根据估值分位评估单只基金（或合并 ETF / watchlist 指数）的调仓建议。

    规则（liubo 2026-09-24 拍板 + 二次细化）：
    - 数据缺失 → SKIP
    - 仓位 = 0 + 分位 < 50% → BUILD +1
    - 仓位 = 0 + 分位 >= 50% → HOLD
    - 仓位 in (0, 1) + 分位 < 50% → ADD +1（欠配 + 便宜 → 凑 1 仓）
    - 仓位 in (0, 1) + 50% <= 分位 <= 80% → HOLD
    - 仓位 in (0, 1) + 分位 > 80% → REDUCE -1
    - 仓位 >= 1 + 分位 < 20% → ADD +1（深价值例外）
    - 仓位 >= 1 + 20% <= 分位 <= 80% → HOLD
    - 仓位 >= 1 + 分位 > 80% → REDUCE -1
    - 月度冷却期（liubo 2026-09-25 拍板）：同 ETF 距上次调仓 < 30 天 → 转 HOLD

    不变量：
    - signal == HOLD 时 change == 0
    - signal == SKIP 时 change == 0
    - signal in (ADD, BUILD) 时 change == +1
    - signal == REDUCE 时 change == -1

    Args:
        eval: 评估输入（含仓位 / 估值指标 / 分位）
        today: 评估日期；None = date.today()（测试用固定日期）
    """
    if today is None:
        today = date.today()
    action = _evaluate_signal(eval)
    return _apply_cooldown(action, today)


def _evaluate_signal(eval: FundPEvaluation) -> RebalanceAction:
    """纯信号评估（不应用冷却期）。测试 + 内部用。"""
    # 数据缺失
    if eval.metric_percentile is None:
        return RebalanceAction(
            fund_code=eval.fund_code,
            fund_name=eval.fund_name,
            subclass=eval.subclass,
            etf_index_code=eval.etf_index_code,
            etf_index_name=eval.etf_index_name,
            metric=eval.metric,
            signal=PESignal.SKIP,
            change=Decimal("0"),
            reason="估值数据缺失" if eval.metric_value is None else "分位缺失",
            metric_value=eval.metric_value,
            metric_percentile=eval.metric_percentile,
            current_position=eval.current_position,
        )

    pct = eval.metric_percentile
    pos = eval.current_position
    metric_name = METRIC_DISPLAY_NAME[eval.metric]

    # 仓位 = 0
    if pos == Decimal("0"):
        if pct < Decimal("0.50"):
            return RebalanceAction(
                fund_code=eval.fund_code,
                fund_name=eval.fund_name,
                subclass=eval.subclass,
                etf_index_code=eval.etf_index_code,
                etf_index_name=eval.etf_index_name,
                metric=eval.metric,
                signal=PESignal.BUILD,
                change=Decimal("1"),
                reason=f"空仓 + {metric_name} 分位 {float(pct) * 100:.2f}% < 50% → 建仓",
                metric_value=eval.metric_value,
                metric_percentile=pct,
                current_position=pos,
            )
        return RebalanceAction(
            fund_code=eval.fund_code,
            fund_name=eval.fund_name,
            subclass=eval.subclass,
            etf_index_code=eval.etf_index_code,
            etf_index_name=eval.etf_index_name,
            metric=eval.metric,
            signal=PESignal.HOLD,
            change=Decimal("0"),
            reason=f"空仓 + {metric_name} 分位 {float(pct) * 100:.2f}% >= 50% → 等便宜",
            metric_value=eval.metric_value,
            metric_percentile=pct,
            current_position=pos,
        )

    # 仓位 in (0, 1) — 「欠配」状态：分位 < 50% 一律 ADD（凑到 1 仓）
    if pos < Decimal("1"):
        if pct < Decimal("0.50"):
            return RebalanceAction(
                fund_code=eval.fund_code,
                fund_name=eval.fund_name,
                subclass=eval.subclass,
                etf_index_code=eval.etf_index_code,
                etf_index_name=eval.etf_index_name,
                metric=eval.metric,
                signal=PESignal.ADD,
                change=Decimal("1"),
                reason=f"欠配 + {metric_name} 分位 {float(pct) * 100:.2f}% < 50% → 加 1 仓凑目标",
                metric_value=eval.metric_value,
                metric_percentile=pct,
                current_position=pos,
            )
        if pct > Decimal("0.80"):
            return RebalanceAction(
                fund_code=eval.fund_code,
                fund_name=eval.fund_name,
                subclass=eval.subclass,
                etf_index_code=eval.etf_index_code,
                etf_index_name=eval.etf_index_name,
                metric=eval.metric,
                signal=PESignal.REDUCE,
                change=Decimal("-1"),
                reason=f"{metric_name} 分位 {float(pct) * 100:.2f}% > 80% → 减仓",
                metric_value=eval.metric_value,
                metric_percentile=pct,
                current_position=pos,
            )
        return RebalanceAction(
            fund_code=eval.fund_code,
            fund_name=eval.fund_name,
            subclass=eval.subclass,
            etf_index_code=eval.etf_index_code,
            etf_index_name=eval.etf_index_name,
            metric=eval.metric,
            signal=PESignal.HOLD,
            change=Decimal("0"),
            reason=f"欠配 + {metric_name} 分位 {float(pct) * 100:.2f}% (50-80%) → 区间内不动",
            metric_value=eval.metric_value,
            metric_percentile=pct,
            current_position=pos,
        )

    # 仓位 >= 1 — 已配足：只有深价值（< 20%）才继续加，避免单一子类过度累积
    if pct < Decimal("0.20"):
        return RebalanceAction(
            fund_code=eval.fund_code,
            fund_name=eval.fund_name,
            subclass=eval.subclass,
            etf_index_code=eval.etf_index_code,
            etf_index_name=eval.etf_index_name,
            metric=eval.metric,
            signal=PESignal.ADD,
            change=Decimal("1"),
            reason=f"已配足 + {metric_name} 分位 {float(pct) * 100:.2f}% < 20% → 深价值加仓",
            metric_value=eval.metric_value,
            metric_percentile=pct,
            current_position=pos,
        )
    if pct > Decimal("0.80"):
        return RebalanceAction(
            fund_code=eval.fund_code,
            fund_name=eval.fund_name,
            subclass=eval.subclass,
            etf_index_code=eval.etf_index_code,
            etf_index_name=eval.etf_index_name,
            metric=eval.metric,
            signal=PESignal.REDUCE,
            change=Decimal("-1"),
            reason=f"{metric_name} 分位 {float(pct) * 100:.2f}% > 80% → 减仓",
            metric_value=eval.metric_value,
            metric_percentile=pct,
            current_position=pos,
        )
    return RebalanceAction(
        fund_code=eval.fund_code,
        fund_name=eval.fund_name,
        subclass=eval.subclass,
        etf_index_code=eval.etf_index_code,
        etf_index_name=eval.etf_index_name,
        metric=eval.metric,
        signal=PESignal.HOLD,
        change=Decimal("0"),
        reason=f"{metric_name} 分位 {float(pct) * 100:.2f}% (20-80%) → 区间内不动",
        metric_value=eval.metric_value,
        metric_percentile=pct,
        current_position=pos,
    )


def _apply_cooldown(action: RebalanceAction, today: date) -> RebalanceAction:
    """月度冷却期检查（liubo 2026-09-25 拍板）。

    同 ETF 距上次调仓 < COOLDOWN_DAYS 天 → 把 ADD/REDUCE/BUILD 转 HOLD。
    HOLD / SKIP 不变（HOLD 本来就不动；SKIP 是估值数据问题，不是调仓时机）。
    """
    if action.signal not in (PESignal.ADD, PESignal.REDUCE, PESignal.BUILD):
        return action
    last = LAST_REBALANCE_BY_ETF.get(action.etf_index_code)
    if last is None:
        return action
    days_since = (today - last).days
    if days_since >= COOLDOWN_DAYS:
        return action
    # 冷却期内：转 HOLD，但保留 reason 提示
    return RebalanceAction(
        fund_code=action.fund_code,
        fund_name=action.fund_name,
        subclass=action.subclass,
        etf_index_code=action.etf_index_code,
        etf_index_name=action.etf_index_name,
        metric=action.metric,
        signal=PESignal.HOLD,
        change=Decimal("0"),
        reason=(
            f"冷却期：上次调仓 {last.isoformat()} 距今 {days_since} 天 "
            f"< {COOLDOWN_DAYS} 天（{action.signal.value.upper()} 信号被冻结）"
        ),
        metric_value=action.metric_value,
        metric_percentile=action.metric_percentile,
        current_position=action.current_position,
    )


def get_current_position(fund_code: str) -> Decimal:
    """从 COST_BASIS_BY_CODE 算当前仓位（cost / 10000, 单位 = 1 万 CNY）。

    liubo 2026-09-24 拍板：成本 = 仓位单位（"成本 20000 就是 2 个仓位"）。
    0.65 仓位 = 6500 元。

    未知基金 / 现金类（004137）→ 返回 0。
    """
    cost = COST_BASIS_BY_CODE.get(fund_code)
    if cost is None:
        return Decimal("0")
    return cost / Decimal("10000")


def build_evaluations() -> list[FundPEvaluation]:
    """从 FUND_INDEX_MAP + PE_SNAPSHOT_BY_INDEX + COST_BASIS_BY_CODE 构建评估列表。

    每只基金 1 个 FundPEvaluation（不合并 — 由 merge_by_etf 后续处理）。
    snapshot 缺数据的（如 000216 商品用 GOLD_NO_METRIC）→ PE=None → evaluate_fund 返回 SKIP。
    """
    out: list[FundPEvaluation] = []
    for fund_code, (subclass, index_code, fund_name) in FUND_INDEX_MAP.items():
        snapshot = PE_SNAPSHOT_BY_INDEX.get(index_code)
        if snapshot is None:
            metric, value, pct = ValuationMetric.PE_TTM, None, None
        else:
            metric, value, pct = snapshot
        out.append(
            FundPEvaluation(
                fund_code=fund_code,
                fund_name=fund_name,
                subclass=subclass,
                etf_index_code=index_code,
                etf_index_name=INDEX_DISPLAY_NAME.get(index_code, index_code),
                metric=metric,
                metric_value=value,
                metric_percentile=pct,
                current_position=get_current_position(fund_code),
            )
        )
    return out


def build_watchlist_evaluations() -> list[FundPEvaluation]:
    """从 INDEX_WATCHLIST 构建 watchlist 评估列表（0 仓位，但也会触发 BUILD/HOLD）。

    liubo 2026-09-24 加 HSI 恒生指数 — 当前没持仓，但想看估值等买入时机。
    评估规则跟持仓基金一致：仓位 0 + 分位 < 50% → BUILD，分位 >= 50% → HOLD。
    """
    out: list[FundPEvaluation] = []
    for subclass, index_code, display_name, metric, value, pct in INDEX_WATCHLIST:
        out.append(
            FundPEvaluation(
                fund_code=f"({index_code})",   # 标记为 watchlist，不是真实基金代码
                fund_name=f"{display_name}（无持仓）",
                subclass=subclass,
                etf_index_code=index_code,
                etf_index_name=display_name,
                metric=metric,
                metric_value=value,
                metric_percentile=pct,
                current_position=Decimal("0"),
            )
        )
    return out


def merge_by_etf(evaluations: Iterable[FundPEvaluation]) -> list[FundPEvaluation]:
    """合并跟踪同一 ETF 的基金（中证 A500 共 3 只 → 1 个评估）。

    合并规则：
    - group by etf_index_code
    - current_position 相加
    - fund_code 用 "+" 连接（如 "022434+022424+022448"）
    - fund_name 用首只基金的 name + "(合并 N 只)" 提示
    - 估值倍数 / 分位取第一只基金的（同一指数值都相同）
    - 指标类型必须一致（不一致不合并；理论上同一指数只会有同一 metric）

    Watchlist 项（fund_code 以 "(" 开头）独自成组，不合并。
    """
    groups: dict[str, list[FundPEvaluation]] = defaultdict(list)
    for ev in evaluations:
        groups[ev.etf_index_code].append(ev)

    out: list[FundPEvaluation] = []
    for idx_code, group in groups.items():
        if len(group) == 1:
            out.append(group[0])
            continue

        # 合并多只同 ETF 的基金
        total_position = sum((e.current_position for e in group), Decimal("0"))
        first = group[0]
        merged_name = f"{first.fund_name} (合并 {len(group)} 只)"
        out.append(
            FundPEvaluation(
                fund_code="+".join(e.fund_code for e in group),
                fund_name=merged_name,
                subclass=first.subclass,
                etf_index_code=first.etf_index_code,
                etf_index_name=first.etf_index_name,
                metric=first.metric,
                metric_value=first.metric_value,
                metric_percentile=first.metric_percentile,
                current_position=total_position,
            )
        )
    return out


def weekly_rebalance_plan(today: date | None = None) -> list[RebalanceAction]:
    """生成本周调仓计划。

    流程：
    1. build_evaluations() → 所有持仓基金独立评估输入
    2. build_watchlist_evaluations() → watchlist 指数（0 仓位）评估输入
    3. merge_by_etf() → 合并同 ETF（watchlist 项不合并）
    4. evaluate_fund() → 每组生成调仓信号（应用月度冷却期）

    Args:
        today: 评估日期；None = date.today()（测试用固定日期）

    Returns:
        list[RebalanceAction], 按 SwensenClass 枚举顺序 + fund_code 排序。
    """
    if today is None:
        today = date.today()
    evaluations = merge_by_etf(build_evaluations() + build_watchlist_evaluations())
    actions = [evaluate_fund(ev, today=today) for ev in evaluations]

    # 按子类枚举顺序排，再按 fund_code 排（稳定排序）
    subclass_order = {s: i for i, s in enumerate(SwensenClass)}
    actions.sort(key=lambda a: (subclass_order[a.subclass], a.fund_code))
    return actions


# ─── 报告格式化 ──────────────────────────────────────────


# 子类显示名（跟 breakdown.DISPLAY_NAME 一致，但本地化短名）
SUBCLASS_DISPLAY_NAME: dict[SwensenClass, str] = {
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


def format_weekly_report(actions: list[RebalanceAction], today: date | None = None) -> str:
    """生成飞书文本格式的周调仓报告。

    分组按 SwensenClass，每组下面列出该子类下所有评估。
    操作类（ADD/REDUCE/BUILD）放在顶部 summary，HOLD/SKIP 折叠到子类下。
    估值指标用 METRIC_DISPLAY_NAME 显示（PE-TTM / P/FFO / P/NAV）。

    Args:
        actions: 调仓动作列表（一般来自 weekly_rebalance_plan()）
        today: 报告日期；None = date.today()。跟 weekly_rebalance_plan 的 today 对齐，
               方便测试用固定日期。
    """
    if today is None:
        today = date.today()
    # 按子类分组（保持子类枚举顺序）
    by_class: dict[SwensenClass, list[RebalanceAction]] = defaultdict(list)
    for a in actions:
        by_class[a.subclass].append(a)

    summary_ops: list[RebalanceAction] = [
        a for a in actions
        if a.signal in (PESignal.ADD, PESignal.REDUCE, PESignal.BUILD)
    ]

    lines: list[str] = []
    lines.append(f"📊 估值分位 周调仓报告 ({today.isoformat()})")
    lines.append("")

    # ── Summary: 本周需要操作的 ──
    if summary_ops:
        lines.append(f"**本周操作（{len(summary_ops)} 笔）**")
        for a in summary_ops:
            signal_text = {
                PESignal.ADD: "加仓",
                PESignal.REDUCE: "减仓",
                PESignal.BUILD: "建仓",
            }[a.signal]
            sign = "+" if a.change > 0 else ""
            lines.append(
                f"- {signal_text} **{a.fund_name}** ({a.fund_code}) "
                f"{sign}{float(a.change):.0f} 仓 = {sign}{float(a.change) * 1:.1f} 万 CNY"
            )
            lines.append(f"  - {a.reason}")
        lines.append("")
    else:
        lines.append("**本周无操作**：所有触发估值的基金都在 20-80% 区间内。")
        lines.append("")

    # ── 子类分组详情 ──
    lines.append("**各子类详情**")
    for subclass in SwensenClass:
        if subclass not in by_class:
            continue
        class_name = SUBCLASS_DISPLAY_NAME[subclass]
        ops = by_class[subclass]
        lines.append("")
        lines.append(f"**{class_name}**")
        for a in ops:
            signal_text = {
                PESignal.ADD: "加仓",
                PESignal.REDUCE: "减仓",
                PESignal.BUILD: "建仓",
                PESignal.HOLD: "不动",
                PESignal.SKIP: "跳过",
            }[a.signal]
            metric_name = METRIC_DISPLAY_NAME[a.metric]
            if a.metric_value is not None and a.metric_percentile is not None:
                val_str = (
                    f"{float(a.metric_value):.2f}"
                    if a.metric in (ValuationMetric.PE_TTM, ValuationMetric.P_FFO)
                    else f"{float(a.metric_value):.2f}x"
                )
                val_text = (
                    f"{metric_name} {val_str} / 分位 {float(a.metric_percentile) * 100:.2f}%"
                )
            else:
                val_text = "数据缺失"
            lines.append(
                f"- {a.fund_name} ({a.fund_code}): "
                f"{signal_text} · {val_text} · 当前 {float(a.current_position):.2f} 仓"
            )
            if a.signal != PESignal.SKIP:
                lines.append(f"  - {a.reason}")

    return "\n".join(lines)


# ─── 模块导出 ──────────────────────────────────────────


__all__ = [
    "PESignal",
    "ValuationMetric",
    "METRIC_DISPLAY_NAME",
    "FundPEvaluation",
    "RebalanceAction",
    "PE_SNAPSHOT_BY_INDEX",
    "FUND_INDEX_MAP",
    "INDEX_DISPLAY_NAME",
    "INDEX_WATCHLIST",
    "LAST_REBALANCE_BY_ETF",
    "COOLDOWN_DAYS",
    "SUBCLASS_DISPLAY_NAME",
    "evaluate_fund",
    "_evaluate_signal",
    "_apply_cooldown",
    "get_current_position",
    "build_evaluations",
    "build_watchlist_evaluations",
    "merge_by_etf",
    "weekly_rebalance_plan",
    "format_weekly_report",
]