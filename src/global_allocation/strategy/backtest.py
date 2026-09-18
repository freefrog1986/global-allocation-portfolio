"""策略 version 接 backtest engine。

参照 specs/091-strategy-config.md §回测。

复用 src/global_allocation/backtest/engine.py，把 StrategyVersion 转成
backtest Strategy，再喂给 BacktestEngine.run。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from global_allocation.backtest.engine import BacktestEngine
from global_allocation.models import (
    Asset,
    AssetClass,
    BacktestResult,
    Currency,
    DataSource,
    RebalanceRule,
    Region,
    Strategy,
    TargetWeight,
)
from global_allocation.strategy.models import (
    AllocationConfig,
    PlanSleeve,
    PlanTarget,
    StrategyVersion,
)

_CALENDAR_FREQ = {
    "monthly": "monthly",
    "quarterly": "quarterly",
    "yearly": "yearly",
    "none": "none",
}


def _flatten_fund_weights(
    sleeves: list[PlanSleeve],
    targets_by_sleeve: dict[int, list[PlanTarget]],
) -> list[tuple[str, Decimal]]:
    """把 sleeve × target 展平成 (fund_code, overall_weight) 列表。"""
    out: list[tuple[str, Decimal]] = []
    for s in sleeves:
        sub = targets_by_sleeve.get(s.id, []) if s.id is not None else []
        for t in sub:
            out.append((t.fund_code, s.target_weight * t.weight))
    return out


def build_backtest_strategy(
    *,
    strategy_id: str,
    strategy_name: str,
    version: StrategyVersion,
    sleeves: list[PlanSleeve],
    targets_by_sleeve: dict[int, list[PlanTarget]],
    inception: date,
) -> Strategy:
    """把 StrategyVersion 转成 backtest.Engine 用的 Strategy。"""
    config = version.config
    if not isinstance(config, AllocationConfig):
        raise ValueError(
            "MVP 只支持 allocation 策略回测；"
            f"version.config 是 {type(config).__name__}"
        )

    fund_weights = _flatten_fund_weights(sleeves, targets_by_sleeve)
    if not fund_weights:
        raise ValueError("version 没有 sleeve/target，无法回测")

    target_weights = [
        TargetWeight(
            asset=Asset(
                symbol=code,
                name=code,
                asset_class=AssetClass.EQUITY,
                region=Region.CN,
                currency=Currency.CNY,
                data_source=DataSource.AKSHARE,
            ),
            weight=w,
        )
        for code, w in fund_weights
    ]

    trigger = config.rebalance_trigger
    return Strategy(
        id=strategy_id,
        name=strategy_name,
        description=f"用户策略 {strategy_id} v{version.version}",
        target_weights=target_weights,
        rebalance=RebalanceRule(
            frequency=_CALENDAR_FREQ[trigger.calendar],  # type: ignore[arg-type]
            threshold=trigger.threshold,
        ),
        base_currency=config.base_currency,
        inception=inception,
    )


def run_strategy_backtest(
    *,
    strategy_id: str,
    strategy_name: str,
    version: StrategyVersion,
    sleeves: list[PlanSleeve],
    targets_by_sleeve: dict[int, list[PlanTarget]],
    prices: pd.DataFrame,
    initial_capital: Decimal = Decimal("100000"),
    cost_bps: Decimal = Decimal("10"),
) -> BacktestResult:
    """跑用户策略的回测。

    Args:
        strategy_id: 策略 ID（gap plan 的 id）。
        strategy_name: 显示名。
        version: StrategyVersion（必须 AllocationConfig）。
        sleeves, targets_by_sleeve: plan 展开。
        prices: 宽表 DataFrame，columns=fund_code，index=Date，values=Adj Close。
        initial_capital: 初始资金。
        cost_bps: 手续费 bps（双边）。

    Returns:
        BacktestResult。

    Raises:
        ValueError: 非 allocation / 无 plan / 数据列缺失。
    """
    inception = prices.index[0].date() if not prices.empty else date.today()
    bt_strategy = build_backtest_strategy(
        strategy_id=strategy_id,
        strategy_name=strategy_name,
        version=version,
        sleeves=sleeves,
        targets_by_sleeve=targets_by_sleeve,
        inception=inception,
    )
    engine = BacktestEngine(
        initial_capital=initial_capital,
        cost_bps=cost_bps,
    )
    return engine.run(bt_strategy, prices)


__all__ = ["build_backtest_strategy", "run_strategy_backtest"]
