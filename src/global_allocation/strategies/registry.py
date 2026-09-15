"""策略注册表。

参照 specs/030-built-in-strategies.md。
"""

from __future__ import annotations

from global_allocation.strategies.base import StrategyBase
from global_allocation.strategies.builtin import (
    AllWeather,
    PermanentPortfolio,
    RiskParity,
    SixtyForty,
)

STRATEGY_REGISTRY: dict[str, type[StrategyBase]] = {
    "60_40": SixtyForty,
    "permanent_portfolio": PermanentPortfolio,
    "all_weather": AllWeather,
    "risk_parity": RiskParity,
}


def get_strategy(strategy_id: str) -> type[StrategyBase]:
    """按 ID 获取策略类。

    Args:
        strategy_id: 策略 ID（如 '60_40'）

    Returns:
        策略类（不是实例）

    Raises:
        KeyError: 未知策略 ID
    """
    if strategy_id not in STRATEGY_REGISTRY:
        available = ", ".join(sorted(STRATEGY_REGISTRY.keys()))
        raise KeyError(
            f"未知策略 '{strategy_id}'。可用策略: {available}"
        )
    return STRATEGY_REGISTRY[strategy_id]


def list_strategies() -> list[str]:
    """列出所有内置策略 ID（按字母排序）。"""
    return sorted(STRATEGY_REGISTRY.keys())


__all__ = ["STRATEGY_REGISTRY", "get_strategy", "list_strategies"]
