"""策略再平衡建议生成器。

参照 specs/091-strategy-config.md §再平衡建议。

两个公开函数：
- compute_rebalance_suggestion：per-fund 粒度，比对 current vs target，触发 band 外的 actions
- compute_next_rebalance_date：allocation 策略的下次再平衡日期（calendar / threshold）
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from global_allocation.portfolio.models import Holding
from global_allocation.strategy.models import (
    AllocationConfig,
    PlanSleeve,
    PlanTarget,
    RebalanceAction,
    RebalanceSuggestion,
    Strategy,
    StrategyVersion,
)

_QUANT = Decimal("0.01")

# calendar → 天数
_CALENDAR_DAYS: dict[str, int] = {
    "monthly": 30,
    "quarterly": 90,
    "yearly": 365,
}


def _flatten_targets(
    sleeves: list[PlanSleeve],
    targets_by_sleeve: dict[int, list[PlanTarget]],
) -> dict[str, dict[str, Decimal | str]]:
    """把 sleeve × target 展平成 fund_code → 整体 target 信息。

    同 fund_code 出现多次时取第一个（spec 默认）。
    """
    fund_targets: dict[str, dict[str, Decimal | str]] = {}
    for s in sleeves:
        sub_targets = (
            targets_by_sleeve.get(s.id, []) if s.id is not None else []
        )
        for t in sub_targets:
            overall_target = s.target_weight * t.weight
            band_min = s.min_weight * t.min_weight
            band_max = s.max_weight * t.max_weight
            fund_targets.setdefault(
                t.fund_code,
                {
                    "sleeve_code": s.code,
                    "target_weight": overall_target,
                    "band_min": band_min,
                    "band_max": band_max,
                },
            )
    return fund_targets


def compute_rebalance_suggestion(
    *,
    strategy: Strategy,
    version: StrategyVersion,
    sleeves: list[PlanSleeve],
    targets_by_sleeve: dict[int, list[PlanTarget]],
    current_holdings: list[Holding],
    current_prices: dict[str, Decimal],
    as_of: date,
) -> RebalanceSuggestion:
    """算 per-fund 再平衡建议。

    触发条件：current_weight 超出 [band_min, band_max] 才生成 action。
    band 计算：sleeve.min/max × target.min/max。
    """
    # 1. total_value
    total_value = sum(
        (h.market_value or Decimal("0") for h in current_holdings),
        Decimal("0"),
    )

    # 2. 展平 fund targets
    fund_targets = _flatten_targets(sleeves, targets_by_sleeve)

    # 3. current_weight / current_shares per fund
    current_weight_per_fund: dict[str, Decimal] = {}
    current_shares_per_fund: dict[str, Decimal] = {}
    for h in current_holdings:
        if total_value == 0:
            w = Decimal("0")
        else:
            mv = h.market_value if h.market_value is not None else Decimal("0")
            w = mv / total_value
        current_weight_per_fund[h.fund.code] = w
        current_shares_per_fund[h.fund.code] = h.shares

    # 4. 遍历所有 target funds，判断是否超出 band
    actions: list[RebalanceAction] = []
    for fund_code, info in fund_targets.items():
        target_weight = info["target_weight"]
        band_min = info["band_min"]
        band_max = info["band_max"]
        sleeve_code = info["sleeve_code"]

        assert isinstance(target_weight, Decimal)
        assert isinstance(band_min, Decimal)
        assert isinstance(band_max, Decimal)
        assert isinstance(sleeve_code, str)

        current_weight = current_weight_per_fund.get(fund_code, Decimal("0"))
        current_shares = current_shares_per_fund.get(fund_code, Decimal("0"))
        drift = current_weight - target_weight

        # 价格缺失 → warning 但不抛错（spec 边界 4：fund 未登记）
        if fund_code not in current_prices:
            continue

        # 在 band 内 → 不需要 action
        if band_min <= current_weight <= band_max:
            continue

        # 触发：算 delta
        target_value = total_value * target_weight
        price = current_prices[fund_code]
        target_shares = (target_value / price).quantize(_QUANT)
        delta_shares = target_shares - current_shares
        action_str: Literal["buy", "sell"] = "buy" if delta_shares > 0 else "sell"
        est_value = (delta_shares * price).__abs__()

        actions.append(
            RebalanceAction(
                fund_code=fund_code,
                sleeve_code=sleeve_code,
                action=action_str,
                current_weight=current_weight,
                target_weight=target_weight,
                drift=drift,
                current_shares=current_shares,
                target_shares=target_shares,
                delta_shares=delta_shares,
                est_value=est_value,
                note=f"超出 band [{band_min:.2%}, {band_max:.2%}]",
            )
        )

    # 5. 当前持有但不在 target → 卖出
    for fund_code, current_weight in current_weight_per_fund.items():
        if fund_code in fund_targets:
            continue
        if fund_code not in current_prices:
            continue
        current_shares = current_shares_per_fund[fund_code]
        price = current_prices[fund_code]
        actions.append(
            RebalanceAction(
                fund_code=fund_code,
                sleeve_code="(none)",
                action="sell",
                current_weight=current_weight,
                target_weight=Decimal("0"),
                drift=current_weight,
                current_shares=current_shares,
                target_shares=Decimal("0"),
                delta_shares=-current_shares,
                est_value=current_shares * price,
                note="不在当前 target 列表中",
            )
        )

    # 6. summary
    if total_value == 0:
        summary = "无持仓，跳过"
    else:
        summary = f"{len(actions)} 只基金需要调整"

    return RebalanceSuggestion(
        strategy_id=strategy.id,
        version=version.version,
        as_of=as_of,
        total_value=total_value,
        actions=actions,
        summary=summary,
    )


def compute_next_rebalance_date(
    *,
    strategy: Strategy,
    version: StrategyVersion,
    sleeves: list[PlanSleeve],
    targets_by_sleeve: dict[int, list[PlanTarget]],
    last_rebalance: date | None,
    current_holdings: list[Holding] | None = None,
    current_prices: dict[str, Decimal] | None = None,
    as_of: date | None = None,
) -> tuple[date | None, str]:
    """算下次再平衡日期 + 原因。

    - selection 策略：不适用日历再平衡
    - allocation + calendar：last_rebalance + interval
    - allocation + threshold：当前 drift 超 threshold 立即触发（返回 today）
    - 两者都配：threshold 优先（先返回 today）
    """
    config = version.config
    if not isinstance(config, AllocationConfig):
        return None, "selection 策略不适用日历再平衡，请用估值信号"

    trigger = config.rebalance_trigger
    today = as_of or date.today()

    # 1. Threshold 检查（需要 holdings + prices 才能算 drift）
    if (
        trigger.threshold is not None
        and current_holdings is not None
        and current_prices is not None
    ):
        suggestion = compute_rebalance_suggestion(
            strategy=strategy,
            version=version,
            sleeves=sleeves,
            targets_by_sleeve=targets_by_sleeve,
            current_holdings=current_holdings,
            current_prices=current_prices,
            as_of=today,
        )
        max_drift = max(
            (abs(a.drift) for a in suggestion.actions), default=Decimal("0")
        )
        if max_drift > trigger.threshold:
            return today, (
                f"threshold 触发：max drift = {max_drift:.2%} > "
                f"{trigger.threshold:.2%}"
            )

    # 2. Calendar 检查
    if trigger.calendar != "none" and last_rebalance is not None:
        interval = _CALENDAR_DAYS[trigger.calendar]
        next_cal = last_rebalance + timedelta(days=interval)
        return next_cal, f"calendar ({trigger.calendar})"

    return None, ""


__all__ = ["compute_rebalance_suggestion", "compute_next_rebalance_date"]
