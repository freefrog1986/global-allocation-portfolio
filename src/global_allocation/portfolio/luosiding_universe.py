"""螺丝钉估值表 universe（liubo 2026-09-26 拍板只关注 21 只指数）。

红利策略组合只关注螺丝钉估值表里的 21 只指数作为参考 universe：
- 每只指数算「红利策略组合」的仓位（cost / 10000）
- 大类资产 / ETF 轮动组合分开管理，仓位不串
- 不在螺丝钉表的 5 只红利基金 + 1 只主动基金单独列 EXTRAS

liubo 后续会把 EXTRAS 里 5 只基金的跟踪指数查清楚填进去。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from global_allocation.portfolio.dividend_strategy import DIVIDEND_COST_BASIS_BY_CODE


# 螺丝钉截图日期（liubo 2026-09-26 提供截图，估值数据为前一日 2026-09-25）
LUOSIDING_SNAPSHOT_DATE: str = "2026-09-25"


@dataclass(frozen=True, slots=True)
class LuosidingIndex:
    """螺丝钉估值表里的单只指数。"""

    display_name: str            # 螺丝钉显示名（"中证A500" / "沪港深红利低波" 等）
    valuation: str               # "偏低" / "适中" / "偏高"
    pe_ttm: Decimal | None       # PE-TTM（None = 螺丝钉没显示盈利收益率）
    pb: Decimal | None           # 市净率
    earnings_yield: Decimal | None  # 盈利收益率 = 1 / PE
    dividend_yield: Decimal | None  # 股息率


# 21 只螺丝钉指数（按截图顺序录入；估值数据来自 liubo 2026-09-25 截图）
LUOSIDING_UNIVERSE: list[LuosidingIndex] = [
    # ── 截图 1（10 只；前 4 只只显示 PE/PB，后 6 只还显示盈利收益率）──
    LuosidingIndex(
        display_name="中证A500", valuation="适中",
        pe_ttm=Decimal("19.22"), pb=Decimal("2.05"),
        earnings_yield=None, dividend_yield=Decimal("0.0196"),
    ),
    LuosidingIndex(
        display_name="标普500", valuation="适中",
        pe_ttm=Decimal("24.61"), pb=Decimal("5.63"),
        earnings_yield=None, dividend_yield=None,
    ),
    LuosidingIndex(
        display_name="中证1000", valuation="适中",
        pe_ttm=Decimal("45.07"), pb=Decimal("2.44"),
        earnings_yield=None, dividend_yield=Decimal("0.0098"),
    ),
    LuosidingIndex(
        display_name="纳斯达克100", valuation="适中",
        pe_ttm=Decimal("29.65"), pb=Decimal("9.67"),
        earnings_yield=None, dividend_yield=None,
    ),
    LuosidingIndex(
        display_name="上证红利", valuation="适中",
        pe_ttm=Decimal("10.67"), pb=Decimal("1.03"),
        earnings_yield=Decimal("0.0937"), dividend_yield=Decimal("0.0429"),
    ),
    LuosidingIndex(
        display_name="龙头红利", valuation="适中",
        pe_ttm=Decimal("11.57"), pb=Decimal("1.51"),
        earnings_yield=Decimal("0.0864"), dividend_yield=Decimal("0.0408"),
    ),
    LuosidingIndex(
        display_name="港股红利", valuation="适中",
        pe_ttm=Decimal("10.63"), pb=Decimal("0.98"),
        earnings_yield=Decimal("0.0941"), dividend_yield=Decimal("0.0510"),
    ),
    LuosidingIndex(
        display_name="红利机会", valuation="适中",
        pe_ttm=Decimal("13.83"), pb=Decimal("1.74"),
        earnings_yield=None, dividend_yield=Decimal("0.0468"),
    ),
    LuosidingIndex(
        display_name="基本面50", valuation="适中",
        pe_ttm=Decimal("11.27"), pb=Decimal("0.87"),
        earnings_yield=Decimal("0.0887"), dividend_yield=Decimal("0.0440"),
    ),
    LuosidingIndex(
        display_name="科创50", valuation="偏高",
        pe_ttm=Decimal("107.37"), pb=Decimal("8.14"),
        earnings_yield=None, dividend_yield=Decimal("0.0023"),
    ),
    # ── 截图 2（11 只；全部偏低；多为红利策略跟踪指数）──
    LuosidingIndex(
        display_name="沪港深红利低波", valuation="偏低",
        pe_ttm=Decimal("9.38"), pb=Decimal("0.89"),
        earnings_yield=Decimal("0.1066"), dividend_yield=Decimal("0.0427"),
    ),
    LuosidingIndex(
        display_name="自由现金流", valuation="偏低",
        pe_ttm=Decimal("10.84"), pb=Decimal("1.35"),
        earnings_yield=None, dividend_yield=Decimal("0.0422"),
    ),
    LuosidingIndex(
        display_name="中证红利低波动", valuation="偏低",
        pe_ttm=Decimal("8.57"), pb=Decimal("0.77"),
        earnings_yield=Decimal("0.1167"), dividend_yield=Decimal("0.0489"),
    ),
    LuosidingIndex(
        display_name="红利低波100", valuation="偏低",
        pe_ttm=Decimal("9.7"), pb=Decimal("0.88"),
        earnings_yield=Decimal("0.1031"), dividend_yield=Decimal("0.0461"),
    ),
    LuosidingIndex(
        display_name="恒生红利低波动", valuation="偏低",
        pe_ttm=Decimal("8.77"), pb=Decimal("0.65"),
        earnings_yield=Decimal("0.1140"), dividend_yield=Decimal("0.0464"),
    ),
    LuosidingIndex(
        display_name="中证红利", valuation="偏低",
        pe_ttm=Decimal("9.54"), pb=Decimal("1.03"),
        earnings_yield=Decimal("0.1048"), dividend_yield=Decimal("0.0439"),
    ),
    LuosidingIndex(
        display_name="消费红利", valuation="偏低",
        pe_ttm=Decimal("21.73"), pb=Decimal("2.23"),
        earnings_yield=None, dividend_yield=Decimal("0.0382"),
    ),
    LuosidingIndex(
        display_name="300价值", valuation="偏低",
        pe_ttm=Decimal("9.95"), pb=Decimal("0.93"),
        earnings_yield=Decimal("0.1005"), dividend_yield=Decimal("0.0432"),
    ),
    LuosidingIndex(
        display_name="中证消费", valuation="偏低",
        pe_ttm=Decimal("23.02"), pb=Decimal("2.56"),
        earnings_yield=None, dividend_yield=Decimal("0.0411"),
    ),
    LuosidingIndex(
        display_name="中证白酒", valuation="偏低",
        pe_ttm=Decimal("20.08"), pb=Decimal("2.36"),
        earnings_yield=None, dividend_yield=Decimal("0.0515"),
    ),
    LuosidingIndex(
        display_name="恒生消费", valuation="偏低",
        pe_ttm=Decimal("13.87"), pb=Decimal("1.97"),
        earnings_yield=None, dividend_yield=Decimal("0.0449"),
    ),
]


# 螺丝钉指数名 → 红利策略基金代码列表
# 5 只红利基金对得上 5 只偏低指数；其余 16 只螺丝钉指数在红利策略组合里都是 0 仓。
LUOSIDING_TO_DIVIDEND_FUND_CODES: dict[str, list[str]] = {
    "中证红利低波动": ["005561"],    # 创金合信中证红利低波动指数A
    "红利低波100": ["008114"],       # 天弘中证红利低波动100联接A
    "沪港深红利低波": ["007751"],   # 景顺长城沪港深红利成长低波指数A
    "自由现金流": ["023917"],        # 华夏国证自由现金流ETF发起式联接A
    "恒生红利低波动": ["021457"],   # 易方达港股通红利低波ETF联接A
}


@dataclass(frozen=True, slots=True)
class LuosidingUniverseRow:
    """单只螺丝钉指数 + 红利策略仓位。"""

    index: LuosidingIndex
    fund_codes: list[str]            # 红利策略组合里跟踪该指数的基金（空 list = 0 仓）
    total_cost_cny: Decimal          # 这些基金的累计买入总成本
    total_position_lots: Decimal     # cost / 10000（1 仓 = 1 万 CNY）


def compute_luosiding_positions() -> list[LuosidingUniverseRow]:
    """对 21 只螺丝钉指数算红利策略组合的仓位。

    没持仓的指数 fund_codes = []，total_position_lots = 0。
    """
    rows: list[LuosidingUniverseRow] = []
    for idx in LUOSIDING_UNIVERSE:
        codes = LUOSIDING_TO_DIVIDEND_FUND_CODES.get(idx.display_name, [])
        cost = sum(
            (DIVIDEND_COST_BASIS_BY_CODE[c] for c in codes),
            Decimal("0"),
        )
        lots = cost / Decimal("10000") if cost > 0 else Decimal("0")
        rows.append(LuosidingUniverseRow(
            index=idx,
            fund_codes=list(codes),
            total_cost_cny=cost,
            total_position_lots=lots,
        ))
    return rows


# ── EXTRAS：不在螺丝钉表的 5 只红利基金 + 1 只主动基金 ──


class ExtraReason(str, Enum):
    """EXTRAS 基金列入的原因。"""

    PENDING_INDEX = "pending_index"  # 等 liubo 查跟踪指数（底层指数不在螺丝钉表里）
    ACTIVE_FUND = "active_fund"      # 主动管理（业绩基准 ≠ 跟踪指数）


@dataclass(frozen=True, slots=True)
class ExtraHolding:
    """EXTRAS 里的一只基金 — 不在螺丝钉表里。"""

    fund_code: str
    fund_name: str
    tracking_index: str | None      # 跟踪的底层指数（None = 主动管理，按业绩基准看）
    parent_etf_code: str | None     # 联接基金对应的母 ETF 代码（None = 主动 / 非联接）
    cost_cny: Decimal
    position_lots: Decimal
    reason: ExtraReason


# 6 只不在螺丝钉表的基金（liubo 2026-09-26 拍板先列出来）
# liubo 2026-09-26 查证完成：5 只 PENDING_INDEX 的跟踪指数 + 母 ETF 都已填好；
# 004098（前海开源港股通股息率50强）是主动管理基金，业绩基准 ≠ 跟踪指数，
# 仍列 PENDING_INDEX 但 parent_etf_code = None。
EXTRA_HOLDINGS: list[ExtraHolding] = [
    # 5 只跟踪指数不在螺丝钉自选表里的联接基金（已查证）
    ExtraHolding(
        fund_code="008163", fund_name="南方标普中国A股大盘红利低波50ETF联接A",
        tracking_index="标普中国A股大盘红利低波50指数",  # 螺丝钉未列此指数
        parent_etf_code="515450",   # 南方标普中国A股大盘红利低波50ETF
        cost_cny=Decimal("60000"), position_lots=Decimal("6"),
        reason=ExtraReason.PENDING_INDEX,
    ),
    ExtraHolding(
        fund_code="007605", fund_name="嘉实沪深300红利低波动ETF联接A",
        tracking_index="沪深300红利低波动指数",          # 螺丝钉未列此指数
        parent_etf_code="515300",   # 嘉实沪深300红利低波动ETF
        cost_cny=Decimal("50000"), position_lots=Decimal("5"),
        reason=ExtraReason.PENDING_INDEX,
    ),
    ExtraHolding(
        fund_code="004098", fund_name="前海开源港股通股息率50强",
        tracking_index="中证港股通高股息投资指数",       # 螺丝钉"港股红利"是不同指数
        parent_etf_code=None,        # 主动管理基金（前海开源），无母 ETF
        cost_cny=Decimal("50000"), position_lots=Decimal("5"),
        reason=ExtraReason.PENDING_INDEX,
    ),
    ExtraHolding(
        fund_code="025958", fund_name="南方中证全指自由现金流ETF联接A",
        tracking_index="中证全指自由现金流指数",          # 螺丝钉只列国证那只（不同指数）
        parent_etf_code="159232",   # 南方中证全指自由现金流ETF
        cost_cny=Decimal("1000"), position_lots=Decimal("0.1"),
        reason=ExtraReason.PENDING_INDEX,
    ),
    ExtraHolding(
        fund_code="025682", fund_name="广发高股息ETF联接A",
        tracking_index="中证智选高股息策略指数",          # 螺丝钉未列此指数
        parent_etf_code="159207",   # 广发中证智选高股息策略ETF
        cost_cny=Decimal("5000"), position_lots=Decimal("0.5"),
        reason=ExtraReason.PENDING_INDEX,
    ),
    # 1 只主动基金（业绩基准，不是跟踪）
    ExtraHolding(
        fund_code="022164", fund_name="西部利得央企优选股票A",
        tracking_index=None,        # 主动基金；业绩基准 = 中证中央企业综合指数，不算跟踪
        parent_etf_code=None,       # 主动基金，无母 ETF
        cost_cny=Decimal("1000"), position_lots=Decimal("0.1"),
        reason=ExtraReason.ACTIVE_FUND,
    ),
]


__all__ = [
    "LUOSIDING_SNAPSHOT_DATE",
    "LuosidingIndex",
    "LUOSIDING_UNIVERSE",
    "LUOSIDING_TO_DIVIDEND_FUND_CODES",
    "LuosidingUniverseRow",
    "compute_luosiding_positions",
    "ExtraReason",
    "ExtraHolding",
    "EXTRA_HOLDINGS",
]