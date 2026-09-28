"""红利策略组合 — 螺丝钉估值调仓（liubo 2026-09-26 拍板 v6）。

跟 Swensen 大类资产配置组合的 pe_rebalance.py 完全独立：
- 数据源：螺丝钉周估值表（偏低 / 适中 / 偏高），不用 PE-TTM 分位
- 调仓单位：5k CNY（= 0.5 unit in pe_rebalance.py；不同组合不同粒度）
- 单只上限：8 lots = 40k = 10% of 400k 总规模
- 单类策略上限：28 lots = 140k = 35%（soft cap：A 股·红利+低波 暂超）
- 卖信号：螺丝钉新进 偏高 → REDUCE -1 lot (5k)
- 主动基金 022164 → SKIP（业绩基准 ≠ 跟踪指数）
- 不应用月度冷却期（螺丝钉每周更新；周节奏足够慢）

v6 规则映射（liubo 2026-09-26）：
- 偏低 + position = 0 → BUILD +1 lot (5k CNY 建仓)
- 偏低 + position > 0 → ADD +1 lot (5k CNY 加仓)
- 适中 → HOLD
- 偏高 + position > 0 → REDUCE -1 lot (5k CNY 减仓)
- 偏高 + position = 0 → HOLD
- 螺丝钉 universe 里没的指数（5 只 EXTRAS） → SKIP，等接 parent ETF PE 后补
- 主动基金（022164） → SKIP
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from global_allocation.portfolio.category_matrix import (
    ACTIVE_FUND_CODES,
    Market,
    Strategy,
    get_cell_by_index,
)
from global_allocation.portfolio.luosiding_universe import LUOSIDING_UNIVERSE, LuosidingIndex


# 螺丝钉估值的 3 个分类（跟 luosiding_universe.LuosidingIndex.valuation 对齐）
class DividendValuation(str, Enum):
    """螺丝钉估值分类（liubo 2026-09-25 截图数据）。"""

    LOW = "偏低"
    MEDIUM = "适中"
    HIGH = "偏高"


class DividendSignal(str, Enum):
    """红利策略组合 — 调仓信号（基于螺丝钉估值 + 仓位）。"""

    BUILD = "build"        # 建仓：空仓 + 偏低
    ADD = "add"            # 加仓：有仓位 + 偏低
    REDUCE = "reduce"      # 减仓：有仓位 + 偏高
    HOLD = "hold"          # 持有：适中 / 偏高+空仓
    SKIP = "skip"          # 跳过：主动基金 / 螺丝钉 universe 没该指数


# 红利策略组合的总规模 & 仓位常量
TOTAL_PORTFOLIO_CNY: Decimal = Decimal("400000")           # 40 万总规模（liubo 2026-09-26 拍板）
SINGLE_CAP_PCT: Decimal = Decimal("0.10")                  # 单只 10% 上限
CATEGORY_CAP_PCT: Decimal = Decimal("0.35")                # 单类 35% 上限（soft cap）
CASH_BUFFER_PCT: Decimal = Decimal("0.10")                 # 现金 10%
LOT_SIZE_CNY: Decimal = Decimal("5000")                    # 1 lot = 5k CNY

SINGLE_CAP_LOTS: Decimal = SINGLE_CAP_PCT * TOTAL_PORTFOLIO_CNY / LOT_SIZE_CNY  # 8 lots = 40k
CATEGORY_CAP_LOTS: Decimal = CATEGORY_CAP_PCT * TOTAL_PORTFOLIO_CNY / LOT_SIZE_CNY  # 28 lots = 140k
CASH_BUFFER_LOTS: Decimal = CASH_BUFFER_PCT * TOTAL_PORTFOLIO_CNY / LOT_SIZE_CNY  # 8 lots = 40k


@dataclass(frozen=True, slots=True)
class DividendRebalanceAction:
    """单只基金（或指数）的红利策略调仓建议。"""

    fund_code: str
    fund_name: str
    market: Market | None           # 2D 分类 — 市场维度
    strategy: Strategy | None       # 2D 分类 — 策略维度
    index_display_name: str | None  # 螺丝钉指数显示名（None = 不在 universe 里）
    valuation: DividendValuation | None
    signal: DividendSignal
    change_lots: Decimal            # +1 = 加 5k, -1 = 减 5k, 0 = 不变
    reason: str                     # 中文说明（飞书表格展示用）
    current_position_lots: Decimal  # 当前仓位（单位 = 5k CNY）


def _index_by_display_name(display_name: str) -> LuosidingIndex | None:
    """从螺丝钉 universe 查指数对象。"""
    for idx in LUOSIDING_UNIVERSE:
        if idx.display_name == display_name:
            return idx
    return None


def evaluate_dividend_fund(
    fund_code: str,
    fund_name: str,
    index_display_name: str | None,
    current_position_lots: Decimal,
) -> DividendRebalanceAction:
    """根据螺丝钉估值 + 当前仓位生成红利策略调仓动作。

    Args:
        fund_code: 基金代码（红利策略 11 只之一）
        fund_name: 基金中文名（飞书展示用）
        index_display_name: 螺丝钉指数 display_name（None = 该基金跟踪指数不在螺丝钉表）
        current_position_lots: 当前仓位（单位 = 5k CNY，cost / 5000）
    """
    # 1. 主动基金 → SKIP
    if fund_code in ACTIVE_FUND_CODES:
        return DividendRebalanceAction(
            fund_code=fund_code,
            fund_name=fund_name,
            market=None,
            strategy=None,
            index_display_name=index_display_name,
            valuation=None,
            signal=DividendSignal.SKIP,
            change_lots=Decimal("0"),
            reason="主动管理基金（业绩基准 ≠ 跟踪指数），跳过 PE 分位调仓",
            current_position_lots=current_position_lots,
        )

    # 2. 螺丝钉 universe 没该指数 → SKIP
    if index_display_name is None:
        return DividendRebalanceAction(
            fund_code=fund_code,
            fund_name=fund_name,
            market=None,
            strategy=None,
            index_display_name=None,
            valuation=None,
            signal=DividendSignal.SKIP,
            change_lots=Decimal("0"),
            reason="跟踪指数不在螺丝钉 universe，等接 parent ETF PE 后补信号",
            current_position_lots=current_position_lots,
        )

    idx = _index_by_display_name(index_display_name)
    if idx is None:
        return DividendRebalanceAction(
            fund_code=fund_code,
            fund_name=fund_name,
            market=None,
            strategy=None,
            index_display_name=index_display_name,
            valuation=None,
            signal=DividendSignal.SKIP,
            change_lots=Decimal("0"),
            reason=f"螺丝钉 universe 找不到 {index_display_name}",
            current_position_lots=current_position_lots,
        )

    # 3. 查 (市场, 策略) cell
    cell = get_cell_by_index(index_display_name)
    market: Market | None = cell[0] if cell else None
    strategy: Strategy | None = cell[1] if cell else None

    # 4. 螺丝钉 valuation → signal
    val_str = idx.valuation
    if val_str == DividendValuation.LOW.value:
        valuation_enum = DividendValuation.LOW
    elif val_str == DividendValuation.HIGH.value:
        valuation_enum = DividendValuation.HIGH
    elif val_str == DividendValuation.MEDIUM.value:
        valuation_enum = DividendValuation.MEDIUM
    else:
        return DividendRebalanceAction(
            fund_code=fund_code,
            fund_name=fund_name,
            market=market,
            strategy=strategy,
            index_display_name=index_display_name,
            valuation=None,
            signal=DividendSignal.SKIP,
            change_lots=Decimal("0"),
            reason=f"螺丝钉估值 '{val_str}' 不在 偏低/适中/偏高 三档，跳过",
            current_position_lots=current_position_lots,
        )

    # 5. 单只上限检查
    pos = current_position_lots
    cap = SINGLE_CAP_LOTS

    if valuation_enum == DividendValuation.LOW:
        if pos >= cap:
            return DividendRebalanceAction(
                fund_code=fund_code,
                fund_name=fund_name,
                market=market,
                strategy=strategy,
                index_display_name=index_display_name,
                valuation=valuation_enum,
                signal=DividendSignal.HOLD,
                change_lots=Decimal("0"),
                reason=f"偏低 + 仓位 {float(pos):.1f} 仓已达单只上限 {float(cap):.0f} 仓 → 不再加",
                current_position_lots=pos,
            )
        if pos == Decimal("0"):
            return DividendRebalanceAction(
                fund_code=fund_code,
                fund_name=fund_name,
                market=market,
                strategy=strategy,
                index_display_name=index_display_name,
                valuation=valuation_enum,
                signal=DividendSignal.BUILD,
                change_lots=Decimal("1"),
                reason=f"偏低 + 空仓 → 建仓 1 仓 (5k CNY)",
                current_position_lots=pos,
            )
        return DividendRebalanceAction(
            fund_code=fund_code,
            fund_name=fund_name,
            market=market,
            strategy=strategy,
            index_display_name=index_display_name,
            valuation=valuation_enum,
            signal=DividendSignal.ADD,
            change_lots=Decimal("1"),
            reason=f"偏低 + 已持仓 {float(pos):.1f} 仓 → 加仓 1 仓 (5k CNY)",
            current_position_lots=pos,
        )

    if valuation_enum == DividendValuation.MEDIUM:
        return DividendRebalanceAction(
            fund_code=fund_code,
            fund_name=fund_name,
            market=market,
            strategy=strategy,
            index_display_name=index_display_name,
            valuation=valuation_enum,
            signal=DividendSignal.HOLD,
            change_lots=Decimal("0"),
            reason=f"适中 + 仓位 {float(pos):.1f} 仓 → 持有不动",
            current_position_lots=pos,
        )

    # HIGH
    if pos == Decimal("0"):
        return DividendRebalanceAction(
            fund_code=fund_code,
            fund_name=fund_name,
            market=market,
            strategy=strategy,
            index_display_name=index_display_name,
            valuation=valuation_enum,
            signal=DividendSignal.HOLD,
            change_lots=Decimal("0"),
            reason=f"偏高 + 空仓 → 不开新仓",
            current_position_lots=pos,
        )
    return DividendRebalanceAction(
        fund_code=fund_code,
        fund_name=fund_name,
        market=market,
        strategy=strategy,
        index_display_name=index_display_name,
        valuation=valuation_enum,
        signal=DividendSignal.REDUCE,
        change_lots=Decimal("-1"),
        reason=f"偏高 + 已持仓 {float(pos):.1f} 仓 → 减仓 1 仓 (5k CNY)；浮盈/浮亏用户手动确认",
        current_position_lots=pos,
    )


__all__ = [
    "DividendValuation",
    "DividendSignal",
    "DividendRebalanceAction",
    "TOTAL_PORTFOLIO_CNY",
    "SINGLE_CAP_PCT",
    "CATEGORY_CAP_PCT",
    "CASH_BUFFER_PCT",
    "LOT_SIZE_CNY",
    "SINGLE_CAP_LOTS",
    "CATEGORY_CAP_LOTS",
    "CASH_BUFFER_LOTS",
    "evaluate_dividend_fund",
]