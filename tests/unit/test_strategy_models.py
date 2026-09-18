"""测试 src/global_allocation/strategy/models.py。

参照 specs/091-strategy-config.md。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from global_allocation.models import Currency
from global_allocation.strategy.models import (
    AllocationConfig,
    PlanSleeve,
    PlanTarget,
    PositionSizing,
    RebalanceAction,
    RebalanceSuggestion,
    RebalanceTrigger,
    SelectionConfig,
    Strategy,
    StrategyStatus,
    StrategyType,
    StrategyVersion,
    ValuationSignal,
)

# ─── Enum ─────────────────────────────────────────────────────


class TestEnums:
    def test_strategy_type_values(self) -> None:
        assert StrategyType.ALLOCATION.value == "allocation"
        assert StrategyType.SELECTION.value == "selection"

    def test_strategy_status_values(self) -> None:
        assert StrategyStatus.DRAFT.value == "draft"
        assert StrategyStatus.ACTIVE.value == "active"
        assert StrategyStatus.ARCHIVED.value == "archived"


# ─── AllocationConfig + RebalanceTrigger ─────────────────────


class TestRebalanceTrigger:
    def test_defaults(self) -> None:
        t = RebalanceTrigger()
        assert t.calendar == "none"
        assert t.threshold is None
        assert t.cashflow is False

    def test_threshold_must_be_in_open_interval(self) -> None:
        with pytest.raises(ValidationError):
            RebalanceTrigger(threshold=Decimal("0"))
        with pytest.raises(ValidationError):
            RebalanceTrigger(threshold=Decimal("1"))
        with pytest.raises(ValidationError):
            RebalanceTrigger(threshold=Decimal("-0.1"))
        with pytest.raises(ValidationError):
            RebalanceTrigger(threshold=Decimal("1.5"))

    def test_threshold_accepted(self) -> None:
        t = RebalanceTrigger(threshold=Decimal("0.05"))
        assert t.threshold == Decimal("0.05")

    def test_calendar_values(self) -> None:
        for c in ("none", "monthly", "quarterly", "yearly"):
            t = RebalanceTrigger(calendar=c)  # type: ignore[arg-type]
            assert t.calendar == c
        with pytest.raises(ValidationError):
            RebalanceTrigger(calendar="daily")  # type: ignore[arg-type]


class TestAllocationConfig:
    def test_defaults(self) -> None:
        c = AllocationConfig()
        assert c.base_currency == Currency.CNY
        assert c.rebalance_trigger.calendar == "none"

    def test_with_trigger(self) -> None:
        c = AllocationConfig(
            base_currency=Currency.USD,
            rebalance_trigger=RebalanceTrigger(
                calendar="quarterly", threshold=Decimal("0.05"), cashflow=True
            ),
        )
        assert c.base_currency == Currency.USD
        assert c.rebalance_trigger.cashflow is True


# ─── SelectionConfig + ValuationSignal ──────────────────────


class TestValuationSignal:
    def test_defaults(self) -> None:
        s = ValuationSignal()
        assert s.metric == "pe_percentile"
        assert s.entry_threshold == Decimal("30")
        assert s.exit_threshold == Decimal("70")
        assert s.data_source == "manual"

    def test_entry_lt_exit_required(self) -> None:
        with pytest.raises(ValidationError, match="entry"):
            ValuationSignal(entry_threshold=Decimal("80"), exit_threshold=Decimal("70"))

    def test_thresholds_in_range(self) -> None:
        with pytest.raises(ValidationError):
            ValuationSignal(entry_threshold=Decimal("-5"))
        with pytest.raises(ValidationError):
            ValuationSignal(entry_threshold=Decimal("105"))


class TestPositionSizing:
    def test_defaults(self) -> None:
        s = PositionSizing()
        assert s.max_single == Decimal("0.10")
        assert s.max_sleeve == Decimal("0.30")
        assert s.min_cash_reserve == Decimal("0.05")

    def test_values_in_range(self) -> None:
        with pytest.raises(ValidationError):
            PositionSizing(max_single=Decimal("1.5"))
        with pytest.raises(ValidationError):
            PositionSizing(max_sleeve=Decimal("1.01"))
        with pytest.raises(ValidationError):
            PositionSizing(min_cash_reserve=Decimal("-0.01"))


class TestSelectionConfig:
    def test_with_signals(self) -> None:
        c = SelectionConfig(
            entry_signal=ValuationSignal(
                entry_threshold=Decimal("30"), exit_threshold=Decimal("70")
            ),
            exit_signal=ValuationSignal(
                entry_threshold=Decimal("30"), exit_threshold=Decimal("70")
            ),
        )
        assert c.entry_signal is not None
        assert c.exit_signal is not None

    def test_optional_signals(self) -> None:
        c = SelectionConfig()
        assert c.entry_signal is None
        assert c.exit_signal is None


# ─── Strategy + StrategyVersion ─────────────────────────────


class TestStrategy:
    def test_basic(self) -> None:
        s = Strategy(
            id="a-share-dividend",
            name="我的 A 股红利",
            type=StrategyType.SELECTION,
            created_at=datetime(2026, 9, 18),
            updated_at=datetime(2026, 9, 18),
        )
        assert s.id == "a-share-dividend"
        assert s.type == StrategyType.SELECTION
        assert s.active_version is None
        assert s.description == ""

    def test_id_slug_format(self) -> None:
        # 必须 kebab-case
        with pytest.raises(ValidationError):
            Strategy(
                id="A Share Dividend",  # 空格
                name="x",
                type=StrategyType.ALLOCATION,
                created_at=datetime(2026, 9, 18),
                updated_at=datetime(2026, 9, 18),
            )
        with pytest.raises(ValidationError):
            Strategy(
                id="",
                name="x",
                type=StrategyType.ALLOCATION,
                created_at=datetime(2026, 9, 18),
                updated_at=datetime(2026, 9, 18),
            )

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            Strategy(
                id="x",
                name="x",
                type=StrategyType.ALLOCATION,
                extra_field="not allowed",  # type: ignore[call-arg]
                created_at=datetime(2026, 9, 18),
                updated_at=datetime(2026, 9, 18),
            )


class TestStrategyVersion:
    def test_allocation(self) -> None:
        v = StrategyVersion(
            strategy_id="a-share-dividend",
            version=1,
            status=StrategyStatus.ACTIVE,
            config=AllocationConfig(),
            created_at=datetime(2026, 9, 18),
        )
        assert v.version == 1
        assert v.notes == ""

    def test_version_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            StrategyVersion(
                strategy_id="x",
                version=0,
                status=StrategyStatus.DRAFT,
                config=AllocationConfig(),
                created_at=datetime(2026, 9, 18),
            )


# ─── PlanSleeve + PlanTarget ────────────────────────────────


class TestPlanSleeve:
    def test_basic(self) -> None:
        s = PlanSleeve(
            version_id=1,
            code="financial",
            name="金融红利",
            target_weight=Decimal("0.40"),
            min_weight=Decimal("0.30"),
            max_weight=Decimal("0.50"),
            tags=["equity", "dividend"],
            position=0,
        )
        assert s.code == "financial"
        assert s.target_weight == Decimal("0.40")

    def test_target_in_band(self) -> None:
        # target 必须在 (min, max) 区间内
        with pytest.raises(ValidationError, match="target"):
            PlanSleeve(
                version_id=1,
                code="x",
                name="x",
                target_weight=Decimal("0.60"),  # 超 max
                min_weight=Decimal("0.30"),
                max_weight=Decimal("0.50"),
                position=0,
            )
        with pytest.raises(ValidationError, match="target"):
            PlanSleeve(
                version_id=1,
                code="x",
                name="x",
                target_weight=Decimal("0.20"),  # 低于 min
                min_weight=Decimal("0.30"),
                max_weight=Decimal("0.50"),
                position=0,
            )

    def test_band_must_be_valid(self) -> None:
        with pytest.raises(ValidationError):
            PlanSleeve(
                version_id=1,
                code="x",
                name="x",
                target_weight=Decimal("0.40"),
                min_weight=Decimal("0.50"),  # min > max
                max_weight=Decimal("0.30"),
                position=0,
            )
        with pytest.raises(ValidationError):
            PlanSleeve(
                version_id=1,
                code="x",
                name="x",
                target_weight=Decimal("0.40"),
                min_weight=Decimal("-0.1"),
                max_weight=Decimal("0.50"),
                position=0,
            )


class TestPlanTarget:
    def test_basic(self) -> None:
        t = PlanTarget(
            sleeve_id=1,
            fund_code="510300",
            weight=Decimal("0.50"),
            min_weight=Decimal("0.40"),
            max_weight=Decimal("0.60"),
            position=0,
        )
        assert t.fund_code == "510300"

    def test_weight_in_band(self) -> None:
        with pytest.raises(ValidationError):
            PlanTarget(
                sleeve_id=1,
                fund_code="x",
                weight=Decimal("0.70"),  # 超 max
                min_weight=Decimal("0.40"),
                max_weight=Decimal("0.60"),
                position=0,
            )


# ─── RebalanceAction + RebalanceSuggestion ──────────────────


class TestRebalanceAction:
    def test_basic(self) -> None:
        a = RebalanceAction(
            fund_code="510300",
            sleeve_code="equity",
            action="buy",
            current_weight=Decimal("0.10"),
            target_weight=Decimal("0.15"),
            drift=Decimal("-0.05"),
            current_shares=Decimal("1000"),
            target_shares=Decimal("1500"),
            delta_shares=Decimal("500"),
            est_value=Decimal("5000"),
            note="超出 band",
        )
        assert a.action == "buy"
        assert a.delta_shares == Decimal("500")

    def test_action_literal(self) -> None:
        with pytest.raises(ValidationError):
            RebalanceAction(
                fund_code="x",
                sleeve_code="x",
                action="bogus",  # type: ignore[arg-type]
                current_weight=Decimal("0"),
                target_weight=Decimal("0"),
                drift=Decimal("0"),
                current_shares=Decimal("0"),
                target_shares=Decimal("0"),
                delta_shares=Decimal("0"),
                est_value=Decimal("0"),
            )


class TestRebalanceSuggestion:
    def test_basic(self) -> None:
        s = RebalanceSuggestion(
            strategy_id="a-share-dividend",
            version=1,
            as_of=date(2026, 9, 18),
            total_value=Decimal("100000"),
            actions=[],
            summary="无变化",
        )
        assert s.total_value == Decimal("100000")
        assert s.actions == []


# ─── Immutability ────────────────────────────────────────────


class TestImmutability:
    def test_strategy_frozen(self) -> None:
        s = Strategy(
            id="x",
            name="x",
            type=StrategyType.ALLOCATION,
            created_at=datetime(2026, 9, 18),
            updated_at=datetime(2026, 9, 18),
        )
        with pytest.raises(ValidationError, match="frozen"):
            s.name = "new"  # type: ignore[misc]

    def test_target_weight_frozen(self) -> None:
        t = PlanTarget(
            sleeve_id=1,
            fund_code="x",
            weight=Decimal("0.5"),
            min_weight=Decimal("0.4"),
            max_weight=Decimal("0.6"),
            position=0,
        )
        with pytest.raises(ValidationError, match="frozen"):
            t.weight = Decimal("0.7")  # type: ignore[misc]
