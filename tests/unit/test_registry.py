"""测试 src/global_allocation/strategies/registry.py。"""

from __future__ import annotations

import pytest

from global_allocation.strategies.base import StrategyBase
from global_allocation.strategies.builtin import (
    SixtyForty,
)
from global_allocation.strategies.registry import (
    STRATEGY_REGISTRY,
    get_strategy,
    list_strategies,
)


class TestStrategyRegistry:
    def test_all_four_builtin_registered(self) -> None:
        assert set(STRATEGY_REGISTRY.keys()) == {
            "60_40",
            "permanent_portfolio",
            "all_weather",
            "risk_parity",
        }

    def test_get_strategy_returns_class(self) -> None:
        cls = get_strategy("60_40")
        assert cls is SixtyForty

    def test_get_strategy_instantiates(self) -> None:
        s = get_strategy("60_40")()
        assert isinstance(s, StrategyBase)
        assert s.id == "60_40"

    def test_get_unknown_strategy_raises(self) -> None:
        with pytest.raises(KeyError, match="未知策略"):
            get_strategy("nonexistent_strategy")

    def test_list_strategies_returns_sorted_ids(self) -> None:
        ids = list_strategies()
        assert ids == sorted(ids)
        assert "60_40" in ids
        assert "permanent_portfolio" in ids
        assert "all_weather" in ids
        assert "risk_parity" in ids

    def test_each_registered_class_validates(self) -> None:
        for cls in STRATEGY_REGISTRY.values():
            cls().validate()

    def test_registry_strategies_are_strategy_subclasses(self) -> None:
        for cls in STRATEGY_REGISTRY.values():
            assert issubclass(cls, StrategyBase)
