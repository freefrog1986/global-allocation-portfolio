"""估值信号触发判断。

参照 specs/091-strategy-config.md §估值信号。

MVP 仅支持 manual data_source（外部传入 current_percentiles 字典）。
后续可接 akshare / yfinance 自动拉数据。

compute_signal_actions：基于 entry/exit threshold 触发 buy/sell actions。
- entry_signal：percentile < entry_threshold → buy（前提：未持有）
- exit_signal：percentile > exit_threshold → sell（前提：已持有）
- 在 band 内 [entry, exit] → hold
- 持有但不在 target 列表 → sell
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from global_allocation.portfolio.models import Holding
from global_allocation.strategy.models import (
    PlanSleeve,
    PlanTarget,
    RebalanceAction,
    RebalanceSuggestion,
    SelectionConfig,
    Strategy,
    StrategyVersion,
)

_QUANT = Decimal("0.01")


def _flatten_target_codes(
    sleeves: list[PlanSleeve],
    targets_by_sleeve: dict[int, list[PlanTarget]],
) -> dict[str, str]:
    """fund_code → sleeve_code。"""
    result: dict[str, str] = {}
    for s in sleeves:
        sub = targets_by_sleeve.get(s.id, []) if s.id is not None else []
        for t in sub:
            result.setdefault(t.fund_code, s.code)
    return result


def compute_signal_actions(
    *,
    strategy: Strategy,
    version: StrategyVersion,
    sleeves: list[PlanSleeve],
    targets_by_sleeve: dict[int, list[PlanTarget]],
    current_percentiles: dict[str, Decimal],
    current_holdings: list[Holding],
    current_prices: dict[str, Decimal],
    as_of: date,
) -> RebalanceSuggestion:
    """基于估值信号生成 per-fund actions。

    只支持 SelectionConfig 策略；allocation 策略返回空 actions。
    """
    config = version.config
    if not isinstance(config, SelectionConfig):
        return RebalanceSuggestion(
            strategy_id=strategy.id,
            version=version.version,
            as_of=as_of,
            total_value=Decimal("0"),
            actions=[],
            summary="非 selection 策略，不适用估值信号",
        )

    total_value = sum(
        (h.market_value or Decimal("0") for h in current_holdings),
        Decimal("0"),
    )
    held_codes = {h.fund.code for h in current_holdings}
    target_sleeve_map = _flatten_target_codes(sleeves, targets_by_sleeve)

    actions: list[RebalanceAction] = []

    # 1. Entry signal：percentile < entry → buy（未持有才触发）
    if config.entry_signal is not None:
        entry_th = config.entry_signal.entry_threshold
        for fund_code, sleeve_code in target_sleeve_map.items():
            if fund_code not in current_percentiles:
                continue
            if fund_code in held_codes:
                continue  # 已持有，不重复买
            if fund_code not in current_prices:
                continue
            pct = current_percentiles[fund_code]
            if pct < entry_th:
                price = current_prices[fund_code]
                # 目标金额 = total_value × sleeve.target_weight × target.weight
                # 简化：先 sleeve × target 找整体目标权重
                sleeve_target_w = next(
                    (s.target_weight for s in sleeves if s.code == sleeve_code),
                    Decimal("0"),
                )
                target_w = next(
                    (
                        t.weight
                        for s in sleeves
                        if s.code == sleeve_code
                        for t in targets_by_sleeve.get(s.id or -1, [])
                        if t.fund_code == fund_code
                    ),
                    Decimal("1"),
                )
                overall_target = sleeve_target_w * target_w
                target_value = total_value * overall_target
                target_shares = (target_value / price).quantize(_QUANT)
                delta_shares = target_shares
                est_value = (delta_shares * price).__abs__()
                actions.append(
                    RebalanceAction(
                        fund_code=fund_code,
                        sleeve_code=sleeve_code,
                        action="buy",
                        current_weight=Decimal("0"),
                        target_weight=overall_target,
                        drift=-overall_target,
                        current_shares=Decimal("0"),
                        target_shares=target_shares,
                        delta_shares=delta_shares,
                        est_value=est_value,
                        note=f"entry signal：percentile {pct} < {entry_th}",
                    )
                )

    # 2. Exit signal：percentile > exit → sell（已持有才触发）
    if config.exit_signal is not None:
        exit_th = config.exit_signal.exit_threshold
        for h in current_holdings:
            code = h.fund.code
            if code not in current_percentiles:
                continue
            if code not in current_prices:
                continue
            pct = current_percentiles[code]
            if pct > exit_th:
                price = current_prices[code]
                mv = h.market_value if h.market_value is not None else Decimal("0")
                current_weight = mv / total_value if total_value > 0 else Decimal("0")
                actions.append(
                    RebalanceAction(
                        fund_code=code,
                        sleeve_code=target_sleeve_map.get(code, "(none)"),
                        action="sell",
                        current_weight=current_weight,
                        target_weight=Decimal("0"),
                        drift=current_weight,
                        current_shares=h.shares,
                        target_shares=Decimal("0"),
                        delta_shares=-h.shares,
                        est_value=h.shares * price,
                        note=f"exit signal：percentile {pct} > {exit_th}",
                    )
                )

    # 3. 持有但不在 target → sell
    for h in current_holdings:
        code = h.fund.code
        if code in target_sleeve_map:
            continue
        if any(a.fund_code == code for a in actions):
            continue  # exit signal 已经处理过
        if code not in current_prices:
            continue
        price = current_prices[code]
        mv = h.market_value if h.market_value is not None else Decimal("0")
        current_weight = mv / total_value if total_value > 0 else Decimal("0")
        actions.append(
            RebalanceAction(
                fund_code=code,
                sleeve_code="(none)",
                action="sell",
                current_weight=current_weight,
                target_weight=Decimal("0"),
                drift=current_weight,
                current_shares=h.shares,
                target_shares=Decimal("0"),
                delta_shares=-h.shares,
                est_value=h.shares * price,
                note="不在当前 target 列表中",
            )
        )

    summary = f"{len(actions)} 只基金触发信号"
    return RebalanceSuggestion(
        strategy_id=strategy.id,
        version=version.version,
        as_of=as_of,
        total_value=total_value,
        actions=actions,
        summary=summary,
    )


__all__ = ["compute_signal_actions"]
