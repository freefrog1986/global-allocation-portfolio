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
- A 股 PE：理杏仁 CSV（2026-10-23 总市值加权 10 年窗口）。
- 美股 PE 分位：螺丝钉估值表第 2999 期（2026-10-23，liubo 2026-10-24 拍板「美股用这个」）；
  PE 倍数沿用理杏仁 CSV（螺丝钉只给分位和 ROE，不给倍数）。
- 国外发达 PE：Wind（估值日 2026-10-23，liubo 2026-10-25 查的）日经 225 / DAX / CAC40
  → 见 WIND_PE_SNAPSHOT_BY_INDEX，优先于自动拉的 DY。
- 国外发达 + 新兴市场 DY 兜底：yfinance ETF 代理自动拉（fetch_overseas_valuation.py，
  liubo 2026-10-24 拍板）→ OVERSEAS_SNAPSHOT_BY_INDEX。
- 新兴市场 PE：worldperatio / siblisresearch（2026-09-04）—— 仅作 DY 拉取失败时的兜底。
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
from global_allocation.portfolio.cost_basis import COST_BASIS_BY_CODE, get_position_unit
from global_allocation.portfolio.models import ValuationIndicatorCode
from global_allocation.portfolio.valuation_indicators import (
    compute_composite_score,
    score_indicator,
)


# ─── 类型 ──────────────────────────────────────────


class ValuationMetric(str, Enum):
    """估值指标类型（liubo 2026-09-24 扩展，支持 REITs 用 P/FFO / P/NAV）。

    spec 099 黄金：加 2 个黄金指标类型（GOLD_HISTORICAL_PCT + GOLD_REAL_YIELD），
    但黄金调仓走 composite_score（综合分 1-5）而不是单分位，所以 metric 字段只用于展示。

    spec REITs-DY（liubo 2026-10-12 拍板）：REITs 改用股息率 DY 替代 P/FFO / P/NAV
    （NAREIT 月报 + 招商月报难抓、6 周没更新；DY 数据易得且本质就是 REITs 估值核心）。
    DY 当前值用 percent number 存（3.76 = 3.76%），跟 akshare 输出一致。
    """

    PE_TTM = "pe_ttm"  # 股票类（PE-TTM）
    P_FFO = "p_ffo"    # US REIT（已废弃，liubo 2026-10-12 改 DY；保留以兼容旧 JSON）
    P_NAV = "p_nav"    # 中证 REITs（已废弃，liubo 2026-10-12 改 DY；保留以兼容旧 JSON）
    # ── 科创 / 创业类（liubo 2026-10-09 拍板：科创 50 / 100 / 创业板 50 / 科创创业 50 改 PS）───
    PS_TTM = "ps_ttm"  # PS-TTM（市销率，营收估值，适合未盈利或盈利波动的成长股）
    # ── 黄金 2 指标（spec 099 — liubo 2026-10-08 拍板 A+B 综合分）───
    GOLD_HISTORICAL_PCT = "gold_historical_pct"  # SGE Au99.99 5 年分位
    GOLD_REAL_YIELD = "gold_real_yield"          # FRED DFII10 实际利率（fraction 0.0291 = 2.91%）
    # ── REITs 股息率（spec REITs-DY — liubo 2026-10-12 拍板）───
    DY = "dy"  # 股息率，percent number（3.76 = 3.76%；存 Decimal 不带 % 号）


# 指标在卡片 / 报告里的人类可读名
METRIC_DISPLAY_NAME: dict[ValuationMetric, str] = {
    ValuationMetric.PE_TTM: "PE-TTM",
    ValuationMetric.P_FFO: "P/FFO",
    ValuationMetric.P_NAV: "P/NAV",
    ValuationMetric.PS_TTM: "PS-TTM",  # liubo 2026-10-09 拍板科创 / 创业类指数改 PS 估值
    ValuationMetric.GOLD_HISTORICAL_PCT: "金价分位",
    ValuationMetric.GOLD_REAL_YIELD: "实际利率",
    ValuationMetric.DY: "DY",  # 股息率（spec REITs-DY — liubo 2026-10-12）
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
    - etf_index_code: 跟踪指数的代码（如 "000510" 中证 A500、".INX" 标普 500、"HSI" 恒生、"GOLD" 黄金）。
    - etf_index_name: 指数中文名。
    - metric: 估值指标（PE-TTM / P-FFO / P-NAV / GOLD_HISTORICAL_PCT / GOLD_REAL_YIELD）。
    - metric_value: 估值倍数（None = 数据缺失；PE 15.89 / P/FFO 19.7 / P/NAV 1.03 / 金价 615.50 / 1/r 34.36）。
    - metric_percentile: 10 年分位（None = 数据缺失；fraction, 0.1694 = 16.94%）。
    - current_position: 当前仓位（单位 = 1 万 CNY；cost / 10000）。
    - composite_score: 综合分 1-5（spec 099 黄金专用；股票留 None 走分位路径）。
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
    composite_score: Decimal | None = None


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
    composite_score: Decimal | None = None  # spec 099 黄金专用


# ─── 硬编码估值 snapshot (2026-09-24 liubo 灌的数据 + WebSearch) ────────


# PE_SNAPSHOT_BY_INDEX: 指数代码 → (估值指标, 倍数, 10年分位)
# 注意：分位值是 fraction（0.1694 = 16.94%），不是整数百分比。
#
# 数据来源（最新一期 2026-10-23，liubo 给的理杏仁 CSV，已归档 data/lixinger/frog_20261023.csv；
# 上一期 2026-10-16 → data/lixinger/frog_20261016.csv）：
#   A 股: 理杏仁 CSV（000510/000852/000903/930050/931643 + 2026-10-09 新加 000688/000698/399673）
#   美股 INX/NDX/OEX: **分位走螺丝钉估值表第 2999 期（2026-10-23）** ——
#     liubo 2026-10-24 拍板「美股用这个」。螺丝钉只给近五年/近十年 PE 分位 + ROE，不给倍数，
#     所以倍数仍取理杏仁 CSV（.NDX 仍无倍数 → None）。本组合统一 10 年窗口。
#   国外发达 (N225/GDAXI/FCHI): guchacha.com 2026-09-22
#     - N225 用日本股市整体市场口径月频（不是 N225 指数本身）
#     - GDAXI 用 DAX 指数本身周频 2016 起
#     - FCHI 用法国股市整体市场口径月频
#   新兴市场: worldperatio + siblisresearch 2026-09-04（PE 14.3 / 分位 60%）
#   US REIT + 中证 REITs: 见 LIVE_REIT_SNAPSHOT_BY_INDEX（spec REITs-DY — liubo 2026-10-12 拍板，
#     每周五 cron 跑 fetch_reit_valuation.py 注入；不再用陈旧 P/FFO / P/NAV 硬编码）
#   HSI: hsi.com.hk / 百分位网 2026-09-28（PE 10.8557 / 分位 58.06%）
#   HSTECH: 理杏仁 2026-10-23（PE 20.6272 / 分位 9.42% → 跌回 <20% 深价值区；冷却期冻结至 2026-11-08）
#   baifenwei.com 2026-09-24 也提供 PE 分位（沪深300/科创50 等 A 股）作为交叉验证。
PE_SNAPSHOT_BY_INDEX: dict[str, tuple[ValuationMetric, Decimal | None, Decimal | None]] = {
    # ── A 股（PE-TTM，理杏仁 2026-10-23）──
    "000510": (ValuationMetric.PE_TTM, Decimal("14.9487"), Decimal("0.3665")),  # 中证 A500（< 50% → 欠配就 ADD）
    "000852": (ValuationMetric.PE_TTM, Decimal("38.3001"), Decimal("0.5309")),  # 中证 1000（50-80% → HOLD）
    "000903": (ValuationMetric.PE_TTM, Decimal("15.8502"), Decimal("0.8855")),  # 中证 A100（> 80% → REDUCE）
    "930050": (ValuationMetric.PE_TTM, Decimal("15.3410"), Decimal("0.1019")),  # 中证 A50（< 20% 深价值 → ADD，但有冷却期冻结）
    # ── A 股科创/创业类（PS-TTM，理杏仁 2026-10-23）───
    "931643": (ValuationMetric.PS_TTM, Decimal("5.5724"), Decimal("0.5715")),   # 科创创业 50
    "000688": (ValuationMetric.PS_TTM, Decimal("8.2313"), Decimal("0.6948")),   # 科创 50
    "000698": (ValuationMetric.PS_TTM, Decimal("5.8879"), Decimal("0.3969")),   # 科创 100（< 50% → 欠配就 ADD；冷却期冻结）
    "399673": (ValuationMetric.PS_TTM, Decimal("3.5209"), Decimal("0.1050")),   # 创业板 50（< 20% 深价值 → ADD；冷却期冻结）
    # ── 美股（PE-TTM 分位：螺丝钉估值表第 2999 期 2026-10-23；倍数：理杏仁 CSV 2026-10-23）──
    # liubo 2026-10-24 拍板「美股用这个」→ 美股分位统一走螺丝钉口径。
    # 螺丝钉只给「近五年 / 近十年市盈率分位数」+ ROE，不给 PE 倍数 → 倍数沿用理杏仁 CSV。
    # 本组合统一用 10 年窗口（跟理杏仁 CSV 同口径）。
    ".INX": (ValuationMetric.PE_TTM, Decimal("25.4957"), Decimal("0.7474")),    # 标普 500（螺丝钉 10 年分位 74.74% / 5 年 67.66%）
    # .NDX: 理杏仁 CSV 无数据 → 倍数 None；分位取螺丝钉
    ".NDX": (ValuationMetric.PE_TTM, None, Decimal("0.6455")),                  # 纳指 100（螺丝钉 10 年分位 64.55% / 5 年 52.03%）
    ".OEX": (ValuationMetric.PE_TTM, Decimal("25.4957"), Decimal("0.7474")),    # 标普 100（螺丝钉无此行 → INX 代理）
    # ── 港股（PE-TTM，理杏仁 2026-10-23）──
    "HSTECH": (ValuationMetric.PE_TTM, Decimal("20.6272"), Decimal("0.0942")),  # 恒生科技（9.42% < 20% → 深价值买入信号；冷却期冻结）
    # 恒生消费 HSCGSI：2026-10-19 先加进 INDEX_WATCHLIST（0 仓跟踪）；
    # 2026-10-25 liubo 转 4 只债基建仓 018200 嘉实恒生消费 ETF 发起联接(QDII)A
    # → 从 watchlist 移出，改走持仓基金路径（fund → index 映射在 FUND_INDEX_MAP）
    "HSCGSI": (ValuationMetric.PE_TTM, Decimal("13.3542"), Decimal("0.0028")),  # 恒生消费（0.28% < 20% → 深价值）
    # ── 国外发达（.N225 / .GDAXI / .FCHI）：见 WIND_PE_SNAPSHOT_BY_INDEX
    #    （liubo 2026-10-25 从 Wind 查的 PE-TTM，估值日 2026-10-23）
    # ── 新兴市场（PE-TTM 兜底；正常路径走 OVERSEAS_SNAPSHOT_BY_INDEX 的 live DY）──
    ".MSCI_EM": (ValuationMetric.PE_TTM, Decimal("14.30"), Decimal("0.60")),   # MSCI Emerging Markets
    # ── 美国 REITs（DY + 5年估值分位，spec REITs-DY — liubo 2026-10-12 拍板；见 LIVE_REIT_SNAPSHOT_BY_INDEX）──
    # ".MSCI_US_REIT": 见 LIVE_REIT_SNAPSHOT_BY_INDEX（fetch_reit_valuation.py 注入）
    # ── 国内 REITs（DY + 5年估值分位，spec REITs-DY — liubo 2026-10-12 拍板）──
    # "932006": 见 LIVE_REIT_SNAPSHOT_BY_INDEX
    # ── 黄金（spec 099 — liubo 2026-10-08）──
    # 主指标存金价分位（给卡片展示用），调仓信号走 composite_score
    # 综合分在 GOLD_SNAPSHOT 算好后 build_evaluations 填入 FundPEvaluation.composite_score
    # 2026-10-08 改：金价 907.50 + 5 年分位 61.58%（liubo 拍板用 5 年分位）
    "GOLD": (ValuationMetric.GOLD_HISTORICAL_PCT, Decimal("907.50"), Decimal("0.6158")),  # SGE Au99.99 5 年分位 2026-10-08
}


# WIND_PE_SNAPSHOT_BY_INDEX: 国外发达市场的权威 PE-TTM 快照（liubo 2026-10-25 从 Wind 查的）
#
# 数据（Wind 口径，估值日 2026-10-23）：
#   日经 225    N225.GI   PE-TTM 19.17   十年分位 52.60%
#   德国 DAX    GDAXI.GI  PE-TTM 17.10   十年分位 52.92%
#   法国 CAC 40 FCHI.PA   PE-TTM 16.74   十年分位 35.34%
#
# 为什么优先于自动拉的 DY（OVERSEAS_SNAPSHOT_BY_INDEX）：
#   1. 口径一致 —— 卡片上 A 股 / 港股 / 美股都是 PE-TTM，Wind 给的是指数本身的 PE-TTM，
#      跟组合统一口径；自动拉的是 ETF 代理的股息率，是另一个指标。
#   2. 更准 —— 专业终端的指数 PE-TTM > ETF 代理的分红拼算 DY。
# 分位窗口：10 年（跟组合统一，跟理杏仁 CSV / 螺丝钉同口径）。
#
# 兜底顺序：WIND_PE_SNAPSHOT_BY_INDEX > OVERSEAS_SNAPSHOT_BY_INDEX（自动拉 DY）> 静态 PE。
WIND_PE_SNAPSHOT_BY_INDEX: dict[str, tuple[ValuationMetric, Decimal | None, Decimal | None]] = {
    ".N225": (ValuationMetric.PE_TTM, Decimal("19.17"), Decimal("0.5260")),   # 日经 225（N225.GI）
    ".GDAXI": (ValuationMetric.PE_TTM, Decimal("17.10"), Decimal("0.5292")),  # 德国 DAX（GDAXI.GI）
    ".FCHI": (ValuationMetric.PE_TTM, Decimal("16.74"), Decimal("0.3534")),   # 法国 CAC 40（FCHI.PA）
}


# LIVE_REIT_SNAPSHOT_BY_INDEX: REITs 指数的实时估值（spec REITs-DY — liubo 2026-10-12 拍板）
# 来源：scripts/ 跑 fetch_reit_valuation.py → 写 JSON → load_latest_snapshot() 注入这里
# 字段：index_code → (metric, value, valuation_percentile_5y, data_date)
# - value: 股息率 percent number（3.76 = 3.76%，跟 akshare 输出一致）
# - valuation_percentile_5y: 估值分位 fraction（0.42 = 42%；低 = 便宜，跟 PE/PS 一致）。
#   原始 DY 分位（高 = 便宜）已翻转存进这个字段，JSON 里另存 dy_percentile_5y 给卡片展示用。
# - data_date: 数据日期（不是 fetch 日期）
#
# 如果 load_latest_snapshot() 没找到文件（首次跑、JSON 还没生成），这里就是空 dict。
# build_evaluations() 在这种情况下会让 REITs 走 SKIP 路径 + reason 提示跑 reit-fetch。
LIVE_REIT_SNAPSHOT_BY_INDEX: dict[
    str, tuple[ValuationMetric, Decimal | None, Decimal | None, "date"]
] = {}


# OVERSEAS_SNAPSHOT_BY_INDEX: 海外指数的实时估值（liubo 2026-10-24 拍板自动拉数）
# 来源：fetch_overseas_valuation.py → 写 JSON → load_live_overseas_snapshot() 注入这里
# 覆盖 .GDAXI（德国 DAX）/ .N225（日经 225）/ .FCHI（法国 CAC 40）/ .MSCI_EM（全球新兴市场）
# —— 这几个不在理杏仁 CSV 覆盖范围内，以前是 2026-09 的手抄静态值。
#
# ⚠️ 2026-10-25 起 .GDAXI / .N225 / .FCHI 优先用 WIND_PE_SNAPSHOT_BY_INDEX（liubo 从 Wind
#    查的 PE-TTM），这里的 live DY 只作兜底；.MSCI_EM 仍然走 live DY。
#
# 字段：index_code → (metric, value, valuation_percentile_10y, data_date)
# - metric: DY（股息率）—— PE-TTM 的 10 年分位没有免费可拉的数据源，
#   改用 ETF 代理的月度 DY-TTM 序列（跟 spec REITs-DY 同一套方法）
# - value: 股息率 percent number（2.16 = 2.16%）
# - valuation_percentile_10y: 估值分位 fraction（低 = 便宜，跟 PE/PS 一致）
# - data_date: 数据日期（不是 fetch 日期）
#
# 没有 live 数据时 → 回落 PE_SNAPSHOT_BY_INDEX 里的静态 PE 值（不 SKIP）。
OVERSEAS_SNAPSHOT_BY_INDEX: dict[
    str, tuple[ValuationMetric, Decimal | None, Decimal | None, "date"]
] = {}


# 基金 → 跟踪指数代码 + 中文名映射
FUND_INDEX_MAP: dict[str, tuple[SwensenClass, str, str]] = {
    # fund_code → (SwensenClass, index_code, fund_name)
    # A 股股票 (9 只 → 7 个 ETF；2026-10-09 加 011612/019857/023414 3 只科创/创业宽基)
    "013310": (SwensenClass.CN_EQUITY, "931643", "华夏科创创业 50"),
    "022434": (SwensenClass.CN_EQUITY, "000510", "南方中证 A500"),
    "017644": (SwensenClass.CN_EQUITY, "000852", "博道中证 1000 增强"),
    "022424": (SwensenClass.CN_EQUITY, "000510", "广发中证 A500"),
    "014532": (SwensenClass.CN_EQUITY, "930050", "易方达 MSCI 中国 A50"),
    "022448": (SwensenClass.CN_EQUITY, "000510", "国泰中证 A500 联接"),
    # A 股宽基扩展 3 只（liubo 2026-10-09 拍板科创/创业类改 PS 估值）
    # 2026-10-09 liubo 改换 2 只：020291/160422 → 019857/023414（费率/跟踪更好）
    "011612": (SwensenClass.CN_EQUITY, "000688", "华夏科创 50ETF 联接 A"),
    "019857": (SwensenClass.CN_EQUITY, "000698", "博时上证科创板 100ETF 联接 A"),
    "023414": (SwensenClass.CN_EQUITY, "399673", "工银创业板 50ETF 联接 A"),
    # 港股股票 (2 → HSTECH + HSCGSI)
    # 2026-09-29 liubo 把 013127 汇添富恒生科技 ETF 联接发起式(QDII)A
    # 从左侧布局组合转到大类资产配置（港股子类）
    "013127": (SwensenClass.HK_EQUITY, "HSTECH", "汇添富恒生科技 ETF 联接发起式"),
    # 2026-10-25 liubo 用 4 只债基超级转换建仓 018200 嘉实恒生消费 ETF 发起联接(QDII)A
    # （场内 ETF 520620，跟踪恒生消费指数 HSCGSI）—— 恒生消费从 watchlist 转为持仓基金
    "018200": (SwensenClass.HK_EQUITY, "HSCGSI", "嘉实恒生消费 ETF 联接"),
    # 美股股票 (7 只 → 4 个 ETF)
    # 2026-09-29 liubo 卖出 019524（华泰柏瑞纳 100 联接），8 → 7
    # 2026-09-29 liubo 确认 539001 是直接 QDII 场外（不是场内 ETF）
    # 第二十轮（liubo 2026-09-29）：加 019172 摩根纳斯达克100指数(QDII)人民币A → .NDX
    # 直接 QDII（不是联接），替代联接作为加仓渠道。
    # 第二十一轮（liubo 2026-09-29）：加 019441 万家纳斯达克100指数发起式(QDII)A → .NDX
    # 双只备份，分散 QDII 额度风险。NDX 现在合并 5 只（1 联接 + 4 直接 QDII 场外）。
    "519981": (SwensenClass.US_EQUITY, ".OEX", "长信标普 100"),
    "018966": (SwensenClass.US_EQUITY, ".NDX", "汇添富纳指 100 联接"),
    "539001": (SwensenClass.US_EQUITY, ".NDX", "建信纳指 100 QDII 场外"),
    "017641": (SwensenClass.US_EQUITY, ".INX", "摩根标普 500"),
    "016452": (SwensenClass.US_EQUITY, ".NDX", "南方纳指 100 发起式 QDII"),
    "019172": (SwensenClass.US_EQUITY, ".NDX", "摩根纳指 100 QDII 人民币A"),
    "019441": (SwensenClass.US_EQUITY, ".NDX", "万家纳指 100 发起式 QDII A"),
    # 新兴市场 (1 只 → MSCI EM)
    "378006": (SwensenClass.EM_EQUITY, ".MSCI_EM", "摩根全球新兴市场"),
    # 国外发达市场 (1 → .GDAXI，2026-09-29 第二十四轮加)
    # liubo 决定走场外基金（513030 场内 DAX ETF 有溢价），用支付宝慧定投：
    # 扣款日每周三，单次金额 250-1000 元/周（平均约 500），目标累计 10000 CNY = 1 仓（满额自动暂停）。
    # 综合 1.05%/年（A 类），限购 1000/天。
    # 当前 cost_basis = 0（DCA 未开始），仓位 0 + 分位 47.1% < 50% → BUILD。
    "000614": (SwensenClass.FOREIGN_DM_EQUITY, ".GDAXI", "华安德国 DAX 联接"),
    # REITs / 商品 — 用各自 REITs 指标，不再 SKIP
    "028277": (SwensenClass.CN_REIT, "932006", "华夏中证 REITs"),
    "160140": (SwensenClass.US_REIT, ".MSCI_US_REIT", "南方道琼斯美国 REIT"),
    # 商品 — 黄金 (spec 099 — liubo 2026-10-08 加 2 指标综合分)
    # 黄金走 GOLD 指数代码 → PE_SNAPSHOT_BY_INDEX 取主指标（分位），
    # composite_score 字段塞综合分 1-5 → _evaluate_signal 走 3.0/4.0 边界
    "000216": (SwensenClass.COMMODITY, "GOLD", "华安黄金 ETF 联接"),
}

# Watchlist：没持仓但想跟踪的指数（liubo 2026-09-24 加 HSI 恒生指数；
# 2026-09-29 加国外发达市场 2 个指数 N225/FCHI — 457001 已转左侧布局组合，
# .GDAXI 在 2026-09-29 第二十四轮加 000614 华安 DAX 联接 A 后从 watchlist 移到 FUND_INDEX_MAP）。
# 列表项：(subclass, index_code, display_name, metric, value, percentile)
# 0 仓位也会触发 BUILD（提醒建仓）或 HOLD（等便宜）/ REDUCE（不建仓）。
INDEX_WATCHLIST: list[tuple[SwensenClass, str, str, ValuationMetric, Decimal, Decimal]] = [
    # 第三十六轮（liubo 2026-10-19 拍板）：HSI 恒生指数 去掉，不跟踪了。
    # 之前 HSI 在这：
    #   (SwensenClass.HK_EQUITY, "HSI", "恒生指数", ValuationMetric.PE_TTM, Decimal("10.6327"), Decimal("0.5524")),
    # 港股剩下 3 个：HSCG（消费）/ HSIDI（创新药）/ HSTECH（科技；持仓 013127）
    # ── 国外发达市场（liubo 2026-09-29 加；457001 转走 0 持仓，跟踪 PE 等建仓时机；
    #    .GDAXI 在 2026-09-29 加 000614 后从 watchlist 移除（fund 替代 watchlist）。
    #    ⚠️ 下面这两个是**最后兜底值**；正常路径优先走 WIND_PE_SNAPSHOT_BY_INDEX
    #    （liubo 2026-10-25 从 Wind 查的 PE-TTM，估值日 2026-10-23），
    #    其次 OVERSEAS_SNAPSHOT_BY_INDEX 的 live DY（liubo 2026-10-24 拍板自动拉数
    #    → EWJ / CAC.PA 代理）。──
    (
        SwensenClass.FOREIGN_DM_EQUITY,
        ".N225",
        "日经 225",
        ValuationMetric.PE_TTM,
        Decimal("19.17"),    # 兜底：Wind 2026-10-23（N225.GI）
        Decimal("0.5260"),
    ),
    (
        SwensenClass.FOREIGN_DM_EQUITY,
        ".FCHI",
        "法国 CAC 40",
        ValuationMetric.PE_TTM,
        Decimal("16.74"),    # 兜底：Wind 2026-10-23（FCHI.PA）
        Decimal("0.3534"),
    ),
    # ── A 股 8 个 ETF 指数跟踪（liubo 2026-10-19 拍板：周报 A 股卡要覆盖全 8 个）──
    # 7 只已有持仓（014532/022434/022424/022448/017644/011612/019857/023414/013310），
    # 持仓行会通过 dedupe 覆盖 watchlist 行；A100 (000903) 没持仓，仍是 watchlist 唯一行。
    # 数据源：理杏仁 2026-10-23（PE-TTM）/ 科创+创业类用 PS-TTM（liubo 2026-10-09 改）
    (
        SwensenClass.CN_EQUITY,
        "930050",
        "中证 A50",
        ValuationMetric.PE_TTM,
        Decimal("15.3410"),  # 理杏仁 2026-10-23
        Decimal("0.1019"),
    ),
    (
        SwensenClass.CN_EQUITY,
        "000903",
        "中证 A100",
        ValuationMetric.PE_TTM,
        Decimal("15.8502"),  # 理杏仁 2026-10-23（> 80% → REDUCE 信号）
        Decimal("0.8855"),
    ),
    (
        SwensenClass.CN_EQUITY,
        "000510",
        "中证 A500",
        ValuationMetric.PE_TTM,
        Decimal("14.9487"),
        Decimal("0.3665"),
    ),
    (
        SwensenClass.CN_EQUITY,
        "000852",
        "中证 1000",
        ValuationMetric.PE_TTM,
        Decimal("38.3001"),  # 50-80% → HOLD
        Decimal("0.5309"),
    ),
    (
        SwensenClass.CN_EQUITY,
        "931643",
        "科创创业 50",
        ValuationMetric.PS_TTM,
        Decimal("5.5724"),
        Decimal("0.5715"),
    ),
    (
        SwensenClass.CN_EQUITY,
        "399673",
        "创业板 50",
        ValuationMetric.PS_TTM,
        Decimal("3.5209"),   # < 20% 深价值 → ADD
        Decimal("0.1050"),
    ),
    (
        SwensenClass.CN_EQUITY,
        "000688",
        "科创 50",
        ValuationMetric.PS_TTM,
        Decimal("8.2313"),
        Decimal("0.6948"),
    ),
    (
        SwensenClass.CN_EQUITY,
        "000698",
        "科创 100",
        ValuationMetric.PS_TTM,
        Decimal("5.8879"),
        Decimal("0.3969"),
    ),
    # ── 港股 恒生消费指数 HSCGSI ──
    # 2026-10-19 liubo 拍板加进 watchlist（0 仓跟踪，PE 13.3542 / 分位 0.28% → 深价值）；
    # 2026-10-25 liubo 转 4 只债基建仓 018200 嘉实恒生消费 ETF 发起联接(QDII)A
    # → 已从 watchlist 移出，改走持仓基金路径（PE 值见 PE_SNAPSHOT_BY_INDEX["HSCGSI"]）。
    # ── 港股 恒生创新药指数（liubo 2026-10-19 拍板：港股加跟踪）──
    # HSIDI = Hang Seng Innovative Drug Index。
    # URL: https://www.lixinger.com/analytics/index/detail/hk/HSIDI/2198
    # 数据源：data/lixinger/frog_20261023.csv（liubo 2026-10-23 提供）。
    # 估值指标：PS-TTM（liubo 2026-10-19 拍板 — 创新药公司大量亏损或微利，PE-TTM 失真；
    # 跟 A 股科创 50/100/创业板 50/科创创业 50 同口径，参考 pe_rebalance.py:84-85 注释）。
    # PS-TTM 2.7499 / 分位 64.32% → 区间内不动。
    (
        SwensenClass.HK_EQUITY,
        "HSIDI",
        "恒生创新药指数",
        ValuationMetric.PS_TTM,
        Decimal("2.7499"),    # 理杏仁 2026-10-23
        Decimal("0.6432"),    # 10 年分位 64.32% → 区间内
    ),
    # ── 港股 恒生港股通高股息率指数（liubo 2026-10-19 拍板：加 HSSCHKY 跟踪）──
    # HSSCHKY = Hang Seng SCHK High Dividend Yield Index（恒生港股通高股息率指数）。
    # URL: https://www.lixinger.com/analytics/index/detail/hk/HSSCHKY/2055
    # 50 只港股通高息股，覆盖 H 股 + A 股互通的中资高息股。
    # **关键**：跟踪 ETF = 513690 港股红利ETF博时（市面主流 HK 高股息 ETF，liubo 拍板 替换 HSHDYI 的原因）。
    # HSHDYI 没用：lixinger 页 1067 只样本数，跟踪 ETF 散乱（513060/159010 等小基金，
    # 没找到「明星产品」），liubo 拍板换 HSSCHKY。
    # 2026-10-23 起 lixinger frog_20261023.csv **已收录 HSSCHKY**，改用理杏仁 10 年分位口径，
    # 口径跟其他港股指数统一（此前 legulegu 2019-11~2026-08 月度加权分位偏高，已弃用）：
    # - PE-TTM 7.4298 / 10 年分位 82.55%（>80% REDUCE 区间）
    # 0 仓位 + 高分位 → 走「等便宜」 HOLD（_evaluate_signal: pos==0 且 pct>=0.50 → HOLD）。
    (
        SwensenClass.HK_EQUITY,
        "HSSCHKY",
        "恒生港股通高股息率指数",
        ValuationMetric.PE_TTM,
        Decimal("7.4298"),    # 理杏仁 2026-10-23
        Decimal("0.8255"),    # 10 年分位 82.55%（>80% REDUCE 区间）
    ),
]

# 月度冷却期（liubo 2026-09-25 拍板）：同 ETF 调仓间隔 ≥ COOLDOWN_DAYS 天。
# 实际调仓后手动更新这个表（每次执行转换 / 加减仓，更新对应 ETF 的日期）。
LAST_REBALANCE_BY_ETF: dict[str, date] = {
    "930050": date(2026, 9, 24),  # 中证 A50 — liubo 2026-09-24 转 008505 → 014532
    "932006": date(2026, 9, 24),  # 中证 REITs — liubo 2026-09-24 转 008505 → 028277
    # liubo 2026-10-09 新建仓（commit 664b6d6）：换 020291→019857（科创100）+ 160422→023414（创业板50）
    "000698": date(2026, 10, 9),  # 科创 100 — liubo 2026-10-09 新建仓 1 仓
    "399673": date(2026, 10, 9),  # 创业板 50 — liubo 2026-10-09 新建仓 1 仓
    "HSTECH": date(2026, 10, 9),  # 恒生科技 — liubo 2026-10-09 +1 仓 25000→35000（commit 664b6d6）
    "HSCGSI": date(2026, 10, 25),  # 恒生消费 — liubo 2026-10-25 转 4 只债基建仓 018200 嘉实恒生消费 1 仓
    # liubo 2026-09-29 加 019172/019441 纳指定投（commit 9e70495 之前的 5a5d3c0 等）
    ".NDX": date(2026, 9, 29),    # 纳指 100 — liubo 2026-09-29 加 2 只定投基金
}

COOLDOWN_DAYS: int = 30  # 月度调仓最短间隔

# 持有期约束（liubo 2026-10-14 拍板）
# REDUCE 信号必须等赎回费归零（最早购买日期 + 阈值天数 ≤ today）才能执行卖出。
# 阈值两类（数据源：akshare 天天基金 fund_fee_em，2026-10-14 拉）：
# 1. REDEMPTION_FEE_DAYS_BY_CODE：赎回费率表里首次出现 0% 的天数（如 730 = 2 年）
# 2. HOLDING_PERIOD_DAYS_BY_CODE：「持有期」基金（赎回费 0% 但期内不可赎回，等于锁定期）：
#    - 010742 南方宁悦一年持有期混合A → 365 天
#    - 004534 汇添富双盈回报一年持有债A → 365 天
#    - 010942 招商瑞乐6个月持有期混合A → 180 天
#    锁定期内整笔无法赎回（不只是费率问题），所以阈值取持有期天数。
# 3. PURCHASE_DATES_BY_CODE：每只基金的最早购买日期（liubo 后续录入）
#    缺失 = 未知 → 约束不生效（保留原 REDUCE 信号）
#    liubo 2026-10-14：「我这些资金都是什么时候买的，后边我会给你更新」
# 4. 货基 004137 按现金处理（不适用）
# 计算逻辑：days_held = today - earliest_purchase；threshold = max(REDEMPTION_FEE_DAYS, HOLDING_PERIOD_DAYS)
#           days_held >= threshold 才能 REDUCE，否则转 HOLD
REDEMPTION_FEE_DAYS_BY_CODE: dict[str, int] = {
    # A 股 (9 宽基)
    "013310": 30,   # 华夏科创创业50
    "022434": 7,    # 南方中证A500
    "017644": 365,  # 博道中证1000增强
    "022424": 7,    # 广发中证A500
    "014532": 180,  # 易方达MSCI中国A50
    "022448": 7,    # 国泰中证A500联接
    "011612": 30,   # 华夏科创50
    "019857": 7,    # 博时上证科创100
    "023414": 7,    # 工银创业板50
    # 港股
    "013127": 7,    # 汇添富恒生科技
    # 美股 (7)
    "519981": 5,    # 长信标普100
    "018966": 7,    # 汇添富纳100
    "539001": 90,   # 建信纳指100 QDII
    "017641": 730,  # 摩根标普500 ← 2 年
    "016452": 7,    # 南方纳100
    "019172": 7,    # 摩根纳100
    "019441": 30,   # 万家纳100
    # 国外发达 + 新兴市场
    "000614": 730,  # 华安德国DAX联接 ← 2 年
    "378006": 2,    # 摩根新兴市场
    # REITs (2)
    "028277": 7,    # 华夏中证REITs
    "160140": 730,  # 南方道琼斯美国REIT ← 2 年
    # 国内利率债 (21, 排除 3 只持有期基金 010742/004534/010942)
    "003547": 1,    # 鹏华丰禄
    "000931": 30,   # 国寿安保尊益信用纯债
    "007520": 30,   # 富安达富利纯债A
    "006829": 30,   # 鹏扬利沣短债A
    "008333": 1170, # 景顺长城弘利39个月定开债 ← 3.25 年
    "015736": 180,  # 长盛盛裕纯债D
    "017837": 7,    # 博时中债7-10年政金债
    "001512": 730,  # 易方达中债3-5年期国债指数
    "485119": 730,  # 工银信用纯债债券A
    "010653": 7,    # 农银汇理金玉债券
    "202103": 30,   # 南方多利增强债券A
    "519753": 730,  # 交银安心收益债券A
    "002490": 365,  # 金鹰元祺债券A
    "008505": 30,   # 浙商中短债A
    "004827": 30,   # 平安中短债
    "007997": 30,   # 易方达年年恒秋一年定开债A
    "110017": 730,  # 易方达增强回报债券A
    "009625": 7,    # 天弘中债3-5年政策性金融债
    "005690": 30,   # 中银安享债券A
    "400030": 730,  # 东方添益债券
    "008420": 180,  # 广发招泰A
    # 美债 (6)
    "100050": 90,   # 富国全球债券
    "007360": 90,   # 易方达中短期美元债
    "003385": 730,  # 工银全球美元债
    "002286": 730,  # 中银美元债
    "000290": 2,    # 鹏华全球高收益债
    "501300": 2,    # 海富通全球收益债券
    # 商品
    "000216": 365,  # 华安黄金ETF联接A
}

# 持有期基金（0% 赎回费但锁定期内不可赎回）
HOLDING_PERIOD_DAYS_BY_CODE: dict[str, int] = {
    "010742": 365,  # 南方宁悦一年持有期混合A
    "004534": 365,  # 汇添富双盈回报一年持有债A
    "010942": 180,  # 招商瑞乐6个月持有期混合A
}

# 基金最早购买日期（liubo 2026-10-14 起逐步录入；缺失 = 未知 = 约束不生效）
# 填充示例（等 liubo 给真实日期）：
#   "013310": date(2026, 9, 22),
#   "022434": date(2026, 9, 22),
PURCHASE_DATES_BY_CODE: dict[str, date] = {}

# 指数代码 → 指数中文名
INDEX_DISPLAY_NAME: dict[str, str] = {
    "000510": "中证 A500",
    "000852": "中证 1000",
    "000903": "中证 A100",
    "930050": "中证 A50",
    "931643": "科创创业 50",
    "000688": "科创 50",
    "000698": "科创 100",
    "399673": "创业板 50",
    ".INX": "标普 500",
    ".NDX": "纳斯达克 100",
    ".OEX": "标普 100",
    ".N225": "日经 225",
    ".GDAXI": "德国 DAX",
    ".FCHI": "法国 CAC 40",
    ".MSCI_EM": "MSCI 新兴市场",
    ".MSCI_US_REIT": "MSCI US REIT",
    "932006": "中证 REITs",
    "HSTECH": "恒生科技",   # 第三十六轮（liubo 2026-10-19）补：港股卡 013127 行 etf_index 列之前显示 "—"
    "HSCGSI": "恒生消费指数",  # 第三十六轮（liubo 2026-10-19 拍板）：港股也跟踪；理杏仁代码 HSCGSI
    "HSIDI": "恒生创新药指数",  # 第三十六轮（liubo 2026-10-19 拍板）：港股加跟踪
    "HSSCHKY": "恒生港股通高股息率指数",  # 第三十七轮（liubo 2026-10-19 拍板，从 HSHDYI 改）：513690 跟踪 ETF
    "GOLD": "黄金",
}


# ─── 黄金 2 指标 snapshot（spec 099 — liubo 2026-10-08 拍板）───
# 黄金不用 PE_SNAPSHOT_BY_INDEX 那套单一指标分位，而是用 2 指标综合分。
# GOLD_SNAPSHOT_BY_FUND 存黄金基金的 2 指标原始值：
#   - gold_price: 当前 SGE Au99.99 价格（CNY/g）
#   - gold_pct_10y: 10 年分位（fraction: 0.78 = 78%）
#   - real_yield_dfii10: FRED DFII10 当前值（fraction: 0.0291 = 2.91%）
#
# 综合分 = (score_gold_historical_pct + score_gold_real_yield) / 2
# 调仓规则跟股票一样（3.0/4.0 边界替代 0.5/0.8 边界）
#
# 数据源：
#   金价：akshare.spot_golden_benchmark_sge（日频）
#   实际利率：FRED CSV https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10（日频，国内可达）
#   5 年分位：手工 weekly 算（akshare 拉历史金价 → 5 年窗口算分位）
#
# 当前快照（2026-10-08 liubo 录入 — 5 年分位 + TIPS 实际利率 liubo 拍板）：
#   SGE Au99.99 = 907.50 CNY/g（akshare 2026-09-29 最新）
#   5 年分位 = 61.58%（2021-08-05 → 2026-09-29，最低 361.47，最高 1248.22）
#   FRED DFII10 = 2.91%（2026-10-06，TIPS 10Y 实际利率）
#   → 1/r = 1/0.0291 ≈ 34.36（正常区间，3 分）
#   → 综合分 (3+3)/2 = 3.0（"正常"）
GOLD_SNAPSHOT_BY_FUND: dict[str, tuple[Decimal, Decimal, Decimal]] = {
    "000216": (
        Decimal("907.50"),  # 金价 SGE Au99.99 CNY/g（akshare 2026-09-29）
        Decimal("0.6158"),  # 5 年分位 61.58%（liubo 2026-10-08 拍板用 5 年）
        Decimal("0.0291"),  # FRED DFII10 实际利率 2.91%（TIPS）
    ),
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
    action = _apply_cooldown(action, today)
    return _check_holding_period(action, today)


def _evaluate_signal(eval: FundPEvaluation) -> RebalanceAction:
    """纯信号评估（不应用冷却期）。测试 + 内部用。"""
    # 黄金走综合分路径（spec 099 — 2 指标简单平均 1-5）
    if eval.composite_score is not None:
        return _evaluate_gold_signal(eval)

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


def _evaluate_gold_signal(eval: FundPEvaluation) -> RebalanceAction:
    """黄金综合分调仓（spec 099 — liubo 2026-10-08 拍板 A+B 综合分）。

    跟股票规则同结构，但边界从 0.5/0.8 分位 改成 3.0/4.0 综合分：
    - 仓位 = 0 + 综合分 < 3.0 → BUILD +1
    - 仓位 = 0 + 综合分 >= 3.0 → HOLD
    - 仓位 in (0, 1) + 综合分 < 3.0 → ADD +1
    - 仓位 in (0, 1) + 3.0 <= 综合分 <= 4.0 → HOLD
    - 仓位 in (0, 1) + 综合分 > 4.0 → REDUCE -1
    - 仓位 >= 1 + 综合分 < 2.0 → ADD +1（深价值例外）
    - 仓位 >= 1 + 2.0 <= 综合分 <= 4.0 → HOLD
    - 仓位 >= 1 + 综合分 > 4.0 → REDUCE -1
    - composite_score 缺失 → SKIP
    """
    assert eval.composite_score is not None  # _evaluate_signal 已检查
    score = eval.composite_score
    pos = eval.current_position

    # 仓位 = 0
    if pos == Decimal("0"):
        if score < Decimal("3.0"):
            return RebalanceAction(
                fund_code=eval.fund_code,
                fund_name=eval.fund_name,
                subclass=eval.subclass,
                etf_index_code=eval.etf_index_code,
                etf_index_name=eval.etf_index_name,
                metric=eval.metric,
                signal=PESignal.BUILD,
                change=Decimal("1"),
                reason=f"空仓 + 综合分 {score} < 3.0 → 建仓",
                metric_value=eval.metric_value,
                metric_percentile=eval.metric_percentile,
                current_position=pos,
                composite_score=score,
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
            reason=f"空仓 + 综合分 {score} >= 3.0 → 等便宜",
            metric_value=eval.metric_value,
            metric_percentile=eval.metric_percentile,
            current_position=pos,
            composite_score=score,
        )

    # 仓位 in (0, 1) — 「欠配」状态
    if pos < Decimal("1"):
        if score < Decimal("3.0"):
            return RebalanceAction(
                fund_code=eval.fund_code,
                fund_name=eval.fund_name,
                subclass=eval.subclass,
                etf_index_code=eval.etf_index_code,
                etf_index_name=eval.etf_index_name,
                metric=eval.metric,
                signal=PESignal.ADD,
                change=Decimal("1"),
                reason=f"欠配 + 综合分 {score} < 3.0 → 加 1 仓凑目标",
                metric_value=eval.metric_value,
                metric_percentile=eval.metric_percentile,
                current_position=pos,
                composite_score=score,
            )
        if score > Decimal("4.0"):
            return RebalanceAction(
                fund_code=eval.fund_code,
                fund_name=eval.fund_name,
                subclass=eval.subclass,
                etf_index_code=eval.etf_index_code,
                etf_index_name=eval.etf_index_name,
                metric=eval.metric,
                signal=PESignal.REDUCE,
                change=Decimal("-1"),
                reason=f"综合分 {score} > 4.0 → 减仓",
                metric_value=eval.metric_value,
                metric_percentile=eval.metric_percentile,
                current_position=pos,
                composite_score=score,
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
            reason=f"欠配 + 综合分 {score} (3.0-4.0) → 区间内不动",
            metric_value=eval.metric_value,
            metric_percentile=eval.metric_percentile,
            current_position=pos,
            composite_score=score,
        )

    # 仓位 >= 1 — 已配足：只有深价值（< 2.0）才继续加
    if score < Decimal("2.0"):
        return RebalanceAction(
            fund_code=eval.fund_code,
            fund_name=eval.fund_name,
            subclass=eval.subclass,
            etf_index_code=eval.etf_index_code,
            etf_index_name=eval.etf_index_name,
            metric=eval.metric,
            signal=PESignal.ADD,
            change=Decimal("1"),
            reason=f"已配足 + 综合分 {score} < 2.0 → 深价值加仓",
            metric_value=eval.metric_value,
            metric_percentile=eval.metric_percentile,
            current_position=pos,
            composite_score=score,
        )
    if score > Decimal("4.0"):
        return RebalanceAction(
            fund_code=eval.fund_code,
            fund_name=eval.fund_name,
            subclass=eval.subclass,
            etf_index_code=eval.etf_index_code,
            etf_index_name=eval.etf_index_name,
            metric=eval.metric,
            signal=PESignal.REDUCE,
            change=Decimal("-1"),
            reason=f"综合分 {score} > 4.0 → 减仓",
            metric_value=eval.metric_value,
            metric_percentile=eval.metric_percentile,
            current_position=pos,
            composite_score=score,
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
        reason=f"综合分 {score} (2.0-4.0) → 区间内不动",
        metric_value=eval.metric_value,
        metric_percentile=eval.metric_percentile,
        current_position=pos,
        composite_score=score,
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


# ─── 持有期约束（liubo 2026-10-14 拍板）────────────────────


def _holding_period_threshold_days(fund_code: str) -> int | None:
    """单只基金的持有期阈值天数 = max(赎回费归零天数, 持有期天数)。

    返回 None = 这只基金没收录（不应该发生，但兜底返回 None 让约束不生效）。
    """
    redeem_days = REDEMPTION_FEE_DAYS_BY_CODE.get(fund_code)
    hold_days = HOLDING_PERIOD_DAYS_BY_CODE.get(fund_code)
    if redeem_days is None and hold_days is None:
        return None
    return max(x for x in (redeem_days, hold_days) if x is not None)


def _check_holding_period(action: RebalanceAction, today: date) -> RebalanceAction:
    """持有期约束：REDUCE 必须等赎回费归零 + 持有期解锁才能执行卖出。

    liubo 2026-10-14 拍板：
    - 只检查 REDUCE（卖出）。ADD / BUILD 是买入，不涉及赎回费。
    - 检查逻辑：days_held = today - earliest_purchase_date；threshold = 赎回费归零天数
      或持有期天数（持有期基金锁定期内不可赎回）
    - days_held < threshold → 转 HOLD（保留原 reason 提示）
    - PURCHASE_DATES_BY_CODE 缺失这只基金 → 约束不生效（保留原 REDUCE，等 liubo 录入）
    - 合并 ETF（fund_code 含 "+"）→ 每只都过才放行，任一没过都 HOLD

    不变量：
    - signal == HOLD 时 change == 0
    """
    if action.signal != PESignal.REDUCE:
        return action

    # 合并 ETF 拆开分别检查
    codes = action.fund_code.split("+")
    for code in codes:
        purchase = PURCHASE_DATES_BY_CODE.get(code)
        if purchase is None:
            # 缺失购买日期 = 未知 = 约束不生效（保留原 REDUCE，等 liubo 录入）
            continue
        threshold = _holding_period_threshold_days(code)
        if threshold is None:
            continue
        days_held = (today - purchase).days
        if days_held < threshold:
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
                    f"持有期约束：{code} 购买 {purchase.isoformat()} 距今 {days_held} 天 "
                    f"< {threshold} 天（{days_held}/{threshold} 天，REDUCE 冻结）"
                ),
                metric_value=action.metric_value,
                metric_percentile=action.metric_percentile,
                current_position=action.current_position,
            )
    return action


# ─── REITs 实时 snapshot 加载（spec REITs-DY — liubo 2026-10-12 拍板）───


def load_live_reit_snapshot() -> dict[str, tuple[ValuationMetric, Decimal | None, Decimal | None, date]]:
    """从 data/reit_valuation/ 最新 JSON 读 REITs 实时估值，注入 LIVE_REIT_SNAPSHOT_BY_INDEX。

    返回更新后的 LIVE_REIT_SNAPSHOT_BY_INDEX 引用（同时也写回 module-level 变量）。
    """
    from global_allocation.portfolio.fetch_reit_valuation import load_latest_snapshot

    result = load_latest_snapshot()
    if result is None:
        # 没有可用快照 — 保持空 dict，build_evaluations 会让 REITs SKIP
        return LIVE_REIT_SNAPSHOT_BY_INDEX

    _path, snapshots = result
    for code, s in snapshots.items():
        # error 字段非空 → 当数据缺失处理（value=None, percentile=None）
        # valuation_percentile_5y 是翻转后的估值分位（低 = 便宜，跟 PE/PS 一致），
        # 直接进规则不用再翻。卡片展示用 dy_percentile_5y（高 = DY 高 = 便宜）。
        if s.error and (s.value is None or s.valuation_percentile_5y is None):
            LIVE_REIT_SNAPSHOT_BY_INDEX[code] = (
                ValuationMetric.DY,
                None,
                None,
                s.data_date,
            )
            continue
        LIVE_REIT_SNAPSHOT_BY_INDEX[code] = (
            ValuationMetric.DY,
            s.value,
            s.valuation_percentile_5y,
            s.data_date,
        )
    return LIVE_REIT_SNAPSHOT_BY_INDEX


def get_live_reit_data_date() -> date | None:
    """LIVE_REIT_SNAPSHOT_BY_INDEX 里所有 REITs 的最大 data_date（卡片展示用）。"""
    if not LIVE_REIT_SNAPSHOT_BY_INDEX:
        return None
    return max(d for _, _, _, d in LIVE_REIT_SNAPSHOT_BY_INDEX.values())


def load_live_overseas_snapshot() -> dict[
    str, tuple[ValuationMetric, Decimal | None, Decimal | None, date]
]:
    """从 data/overseas_valuation/ 最新 JSON 读海外指数实时估值，注入 OVERSEAS_SNAPSHOT_BY_INDEX。

    liubo 2026-10-24 拍板「华安德国 DAX 联接、摩根全球新兴市场这几个你去自动获取拉数据」。

    没有可用快照 / 某个指数抓取失败 → 该指数不注入（回落静态 PE_SNAPSHOT 值）。
    """
    from global_allocation.portfolio.fetch_overseas_valuation import load_latest_snapshot

    result = load_latest_snapshot()
    if result is None:
        return OVERSEAS_SNAPSHOT_BY_INDEX

    _path, snapshots = result
    for code, s in snapshots.items():
        if s.error or s.value is None or s.valuation_percentile_10y is None:
            continue
        OVERSEAS_SNAPSHOT_BY_INDEX[code] = (
            ValuationMetric.DY,
            s.value,
            s.valuation_percentile_10y,
            s.data_date,
        )
    return OVERSEAS_SNAPSHOT_BY_INDEX


def load_live_gold_snapshot() -> dict[str, tuple[Decimal, Decimal, Decimal]]:
    """从 data/gold_valuation/ 最新 JSON 读黄金实时估值，注入 GOLD_SNAPSHOT_BY_FUND。

    返回更新后的 GOLD_SNAPSHOT_BY_FUND 引用（同时也写回 module-level 变量）。
    GOLD_SNAPSHOT_BY_FUND 的 value 是个 tuple (gold_price, gold_pct_5y, real_yield_dfii10)，
    跟 compute_composite_score 的入参匹配。

    失败 / 无 snapshot → 不修改 GOLD_SNAPSHOT_BY_FUND（保留静态 hardcode 数据）。
    """
    from global_allocation.portfolio.fetch_gold_valuation import load_latest_snapshot

    result = load_latest_snapshot()
    if result is None:
        return GOLD_SNAPSHOT_BY_FUND

    _path, snapshots = result
    for code, s in snapshots.items():
        # 任何一个字段为 None → 跳过（保留静态 hardcode）
        if (
            s.gold_price is None
            or s.gold_pct_5y is None
            or s.real_yield_dfii10 is None
            or s.error
        ):
            continue
        GOLD_SNAPSHOT_BY_FUND[code] = (
            s.gold_price,
            s.gold_pct_5y,
            s.real_yield_dfii10,
        )
    return GOLD_SNAPSHOT_BY_FUND


def get_live_gold_data_date() -> date | None:
    """GOLD_SNAPSHOT_BY_FUND 数据来源 data_date（注入的 JSON 里的 data_date 字段）。"""
    # GOLD_SNAPSHOT_BY_FUND 的 tuple 没存 data_date，只能从加载的 GoldValuation 拿
    from global_allocation.portfolio.fetch_gold_valuation import load_latest_snapshot
    result = load_latest_snapshot()
    if result is None:
        return None
    _path, snapshots = result
    if not snapshots:
        return None
    return max(s.data_date for s in snapshots.values())


def get_current_position(fund_code: str) -> Decimal:
    """从 COST_BASIS_BY_CODE 算当前仓位（cost / per-index 1 仓单位）。

    仓位单位（liubo 2026-10-17 第二十八轮反馈："跟踪同一 ETF 指数的仓位应该共享"）：
    - 全局默认：1 仓 = 10000 CNY（2026-09-24 拍板"成本 = 仓位单位"，0.65 仓 = 6500 元）
    - per-index 覆盖：跟踪同一 ETF 的基金共用同一个单位
      - GOLD 1 仓 = 7500（"为了凑一仓"——liubo 2026-10-17 第二十七轮反馈，仅 000216 1 只）
      - .NDX 1 仓 = 10000（5 只定投基金 018966/539001/016452/019172/019441 合并 = 0.882 仓）
      - .GDAXI 1 仓 = 10000（000614；跟全局一样）
    - 未知基金 / 现金类（004137）→ 返回 0
    """
    cost = COST_BASIS_BY_CODE.get(fund_code)
    if cost is None:
        return Decimal("0")
    index_info = FUND_INDEX_MAP.get(fund_code)
    index_code = index_info[1] if index_info is not None else ""
    unit = get_position_unit(index_code)
    return cost / unit


def build_evaluations() -> list[FundPEvaluation]:
    """从 FUND_INDEX_MAP + PE_SNAPSHOT_BY_INDEX + COST_BASIS_BY_CODE 构建评估列表。

    每只基金 1 个 FundPEvaluation（不合并 — 由 merge_by_etf 后续处理）。
    snapshot 缺数据的 → PE=None → evaluate_fund 返回 SKIP。

    数据来源优先级（liubo 2026-10-19 拍板要让卡片显示真实信号）：
    1. **黄金**（spec 099 — liubo 2026-10-08） → GOLD_SNAPSHOT_BY_FUND live（金价 + 5 年分位 + 实利率），
       不再读 PE_SNAPSHOT_BY_INDEX["GOLD"] 静态 hardcode。之前 composite_score 用 live 但
       metric_value/metric_percentile 还显示旧值（907.50 / 0.6158），卡片文字 misleading；
       现在统一用 live，飞书表格的「估值」列才会显示当周真实金价 + 真实分位。
    2. **REITs**（spec REITs-DY — liubo 2026-10-12） → LIVE_REIT_SNAPSHOT_BY_INDEX 的 DY 数据；
       空则 SKIP（reason 提示跑 `gap portfolio reit-fetch`）。
    3. **国外发达**（liubo 2026-10-25） → WIND_PE_SNAPSHOT_BY_INDEX 的 PE-TTM
       （.N225 / .GDAXI / .FCHI，Wind 估值日 2026-10-23）—— 优先于自动拉的 DY。
    4. **其它海外指数**（liubo 2026-10-24） → OVERSEAS_SNAPSHOT_BY_INDEX 的 DY 数据
       （.MSCI_EM，ETF 代理 + 月度 DY-TTM 10 年分位）；空则回落静态 PE_SNAPSHOT 值。
    5. 其它（股票 / 现金） → PE_SNAPSHOT_BY_INDEX 静态 or 缺失。
    """
    out: list[FundPEvaluation] = []
    for fund_code, (subclass, index_code, fund_name) in FUND_INDEX_MAP.items():
        # 数据源选择：黄金 > REITs > Wind 海外 PE > live DY 海外 > 静态 PE_SNAPSHOT
        if fund_code in GOLD_SNAPSHOT_BY_FUND:
            # 黄金 live：3 字段 = (gold_price, gold_pct_5y, real_yield_dfii10)
            # action 只取前 2 个（价格 + 分位）展示在卡片估值列；
            # 第 3 字段（实际利率）走 composite_score 路径，详见 _compute_gold_composite
            gold_price, gold_pct, _ry = GOLD_SNAPSHOT_BY_FUND[fund_code]
            metric = ValuationMetric.GOLD_HISTORICAL_PCT
            value = gold_price
            pct = gold_pct
        elif index_code in LIVE_REIT_SNAPSHOT_BY_INDEX:
            metric, value, pct, data_date = LIVE_REIT_SNAPSHOT_BY_INDEX[index_code]
        elif index_code in WIND_PE_SNAPSHOT_BY_INDEX:
            metric, value, pct = WIND_PE_SNAPSHOT_BY_INDEX[index_code]
        elif index_code in OVERSEAS_SNAPSHOT_BY_INDEX:
            metric, value, pct, data_date = OVERSEAS_SNAPSHOT_BY_INDEX[index_code]
        else:
            snapshot = PE_SNAPSHOT_BY_INDEX.get(index_code)
            if snapshot is None:
                metric, value, pct = ValuationMetric.PE_TTM, None, None
            else:
                metric, value, pct = snapshot
        # 黄金算综合分（spec 099 — 用 live pct + live ry）
        composite = _compute_gold_composite(fund_code, value, pct)
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
                composite_score=composite,
            )
        )
    return out


def _compute_gold_composite(
    fund_code: str,
    gold_price: Decimal | None,
    gold_pct: Decimal | None,
) -> Decimal | None:
    """算黄金 2 指标综合分（spec 099 — liubo 2026-10-08）。

    输入：fund_code（从 GOLD_SNAPSHOT_BY_FUND 取指标）
    输出：综合分 1-5（1=极低估，5=极高估）；None = 数据缺失

    步骤：
    1. 指标 A：金价 5 年分位 → score_indicator(1-5)
    2. 指标 B：实际利率本身（DFII10）→ score_indicator(1-5)
       方向：实际利率 ↑ → 黄金吸引力 ↓ → 黄金便宜（低估）
    3. 综合分 = (score_a + score_b) / 2（简单平均，spec 098 投票机制）

    失败 → 返回 None（让 evaluate_fund 走 SKIP 路径）
    """
    if fund_code not in GOLD_SNAPSHOT_BY_FUND:
        return None
    if gold_pct is None:
        return None
    _, _, real_yield = GOLD_SNAPSHOT_BY_FUND[fund_code]
    score_a = score_indicator(ValuationIndicatorCode.GOLD_HISTORICAL_PCT, gold_pct)
    score_b = score_indicator(ValuationIndicatorCode.GOLD_REAL_YIELD, real_yield)
    return compute_composite_score([score_a, score_b])


def build_watchlist_evaluations() -> list[FundPEvaluation]:
    """从 INDEX_WATCHLIST 构建 watchlist 评估列表（0 仓位，但也会触发 BUILD/HOLD）。

    liubo 2026-09-24 加 HSI 恒生指数 — 当前没持仓，但想看估值等买入时机。
    评估规则跟持仓基金一致：仓位 0 + 分位 < 50% → BUILD，分位 >= 50% → HOLD。

    海外指数（.N225 日经 225 等）：优先用 WIND_PE_SNAPSHOT_BY_INDEX（liubo 从 Wind 查的
    PE-TTM），其次 OVERSEAS_SNAPSHOT_BY_INDEX 的 live DY，最后 INDEX_WATCHLIST 里的静态值。
    """
    out: list[FundPEvaluation] = []
    for subclass, index_code, display_name, metric, value, pct in INDEX_WATCHLIST:
        if index_code in WIND_PE_SNAPSHOT_BY_INDEX:
            metric, value, pct = WIND_PE_SNAPSHOT_BY_INDEX[index_code]
        elif index_code in OVERSEAS_SNAPSHOT_BY_INDEX:
            metric, value, pct, _data_date = OVERSEAS_SNAPSHOT_BY_INDEX[index_code]
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
                if a.metric == ValuationMetric.PE_TTM:
                    val_str = f"{float(a.metric_value):.2f}"
                elif a.metric == ValuationMetric.P_FFO:
                    val_str = f"{float(a.metric_value):.2f}x"
                elif a.metric == ValuationMetric.P_NAV:
                    val_str = f"{float(a.metric_value):.2f}x"
                elif a.metric == ValuationMetric.DY:
                    # spec REITs-DY — liubo 2026-10-12 拍板
                    # value 已经是 percent number（3.76 = 3.76%）
                    val_str = f"{float(a.metric_value):.2f}%"
                elif a.metric == ValuationMetric.GOLD_HISTORICAL_PCT:
                    # 金价单位是 CNY/g，不是倍数
                    val_str = f"{float(a.metric_value):.2f} CNY/g"
                elif a.metric == ValuationMetric.GOLD_REAL_YIELD:
                    # 实际利率用百分号显示
                    val_str = f"{float(a.metric_value) * 100:.2f}%"
                else:
                    val_str = f"{float(a.metric_value):.2f}"
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
    "WIND_PE_SNAPSHOT_BY_INDEX",
    "LIVE_REIT_SNAPSHOT_BY_INDEX",
    "FUND_INDEX_MAP",
    "INDEX_DISPLAY_NAME",
    "INDEX_WATCHLIST",
    "LAST_REBALANCE_BY_ETF",
    "COOLDOWN_DAYS",
    "REDEMPTION_FEE_DAYS_BY_CODE",
    "HOLDING_PERIOD_DAYS_BY_CODE",
    "PURCHASE_DATES_BY_CODE",
    "SUBCLASS_DISPLAY_NAME",
    "evaluate_fund",
    "_evaluate_signal",
    "_apply_cooldown",
    "_check_holding_period",
    "_holding_period_threshold_days",
    "get_current_position",
    "load_live_reit_snapshot",
    "get_live_reit_data_date",
    "load_live_gold_snapshot",
    "get_live_gold_data_date",
    "build_evaluations",
    "build_watchlist_evaluations",
    "merge_by_etf",
    "weekly_rebalance_plan",
    "format_weekly_report",
]


# ─── 模块加载时自动注入 REITs + 黄金 实时 snapshot ─────────────────
# spec REITs-DY — liubo 2026-10-12 拍板
# spec 099 — liubo 2026-10-18 拍板黄金也走 fetch
# 没有 snapshot 文件 → 保持空（build_evaluations 走 SKIP）/ 保留静态 hardcode
try:
    load_live_reit_snapshot()
    load_live_gold_snapshot()
    load_live_overseas_snapshot()
except Exception:
    # 加载失败（首次跑、文件系统问题等）→ 保持空/静态，不影响其他指数评估
    pass