"""策略配置管理数据模型。

参照 specs/091-strategy-config.md。

4 层结构：Strategy → StrategyVersion → PlanSleeve → PlanTarget
+ 两种 type-specific config：AllocationConfig | SelectionConfig
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from global_allocation.models import Currency, _FrozenModel

# ────────────────────────────────────────────────────────────────────
# Enums
# ────────────────────────────────────────────────────────────────────


class StrategyType(str, Enum):
    """策略类型。"""

    ALLOCATION = "allocation"  # 全球配置 — 固定权重 + 再平衡
    SELECTION = "selection"  # A 股红利 — 选股 + 估值信号


class StrategyStatus(str, Enum):
    """版本状态。"""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


# ────────────────────────────────────────────────────────────────────
# Strategy 顶层
# ────────────────────────────────────────────────────────────────────


_StrategyId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$"),
]


class Strategy(_FrozenModel):
    """顶层策略记录。"""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )

    id: _StrategyId
    name: str = Field(min_length=1, max_length=128)
    type: StrategyType
    description: str = ""
    active_version: int | None = None
    created_at: datetime
    updated_at: datetime


# ────────────────────────────────────────────────────────────────────
# AllocationConfig + RebalanceTrigger
# ────────────────────────────────────────────────────────────────────


_RebalanceCalendar = Literal["none", "monthly", "quarterly", "yearly"]


class RebalanceTrigger(_FrozenModel):
    """再平衡触发器。三种可叠加。"""

    calendar: _RebalanceCalendar = "none"
    threshold: Decimal | None = None  # drift 超过 N% 触发
    cashflow: bool = False  # 新进的钱优先加到低配 sleeve

    @field_validator("threshold", mode="before")
    @classmethod
    def _coerce_threshold(cls, v: Any) -> Any:
        if v is None or isinstance(v, Decimal):
            return v
        if isinstance(v, (int, float, str)):
            return Decimal(str(v))
        raise ValueError(f"Cannot convert {type(v)} to Decimal")

    @model_validator(mode="after")
    def _validate_threshold_range(self) -> RebalanceTrigger:
        if self.threshold is not None and not (
            Decimal("0") < self.threshold < Decimal("1")
        ):
            raise ValueError(
                f"threshold must be in (0, 1), got {self.threshold}"
            )
        return self


class AllocationConfig(_FrozenModel):
    """全球配置类型策略的 config。"""

    base_currency: Currency = Currency.CNY
    rebalance_trigger: RebalanceTrigger = Field(default_factory=RebalanceTrigger)


# ────────────────────────────────────────────────────────────────────
# SelectionConfig + ValuationSignal
# ────────────────────────────────────────────────────────────────────


_ValuationMetric = Literal["pe_percentile", "pb_percentile", "dividend_yield_percentile"]
_SignalSource = Literal["manual", "akshare", "yfinance"]


class ValuationSignal(_FrozenModel):
    """估值信号。分位数 < entry 触发买入，> exit 触发卖出。"""

    metric: _ValuationMetric = "pe_percentile"
    entry_threshold: Decimal = Decimal("30")
    exit_threshold: Decimal = Decimal("70")
    data_source: _SignalSource = "manual"  # MVP 只支持 manual

    @field_validator("entry_threshold", "exit_threshold", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        if isinstance(v, Decimal):
            return v
        if isinstance(v, (int, float, str)):
            return Decimal(str(v))
        raise ValueError(f"Cannot convert {type(v)} to Decimal")

    @model_validator(mode="after")
    def _validate_thresholds(self) -> ValuationSignal:
        if not (Decimal("0") <= self.entry_threshold <= Decimal("100")):
            raise ValueError(
                f"entry_threshold must be in [0, 100], got {self.entry_threshold}"
            )
        if not (Decimal("0") <= self.exit_threshold <= Decimal("100")):
            raise ValueError(
                f"exit_threshold must be in [0, 100], got {self.exit_threshold}"
            )
        if self.entry_threshold >= self.exit_threshold:
            raise ValueError(
                f"entry_threshold ({self.entry_threshold}) must be < "
                f"exit_threshold ({self.exit_threshold})"
            )
        return self


class PositionSizing(_FrozenModel):
    """单只基金 / 单 sleeve 的仓位限制。"""

    max_single: Decimal = Decimal("0.10")
    max_sleeve: Decimal = Decimal("0.30")
    min_cash_reserve: Decimal = Decimal("0.05")

    @field_validator("max_single", "max_sleeve", "min_cash_reserve", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        if isinstance(v, Decimal):
            return v
        if isinstance(v, (int, float, str)):
            return Decimal(str(v))
        raise ValueError(f"Cannot convert {type(v)} to Decimal")

    @model_validator(mode="after")
    def _validate_ranges(self) -> PositionSizing:
        for name in ("max_single", "max_sleeve", "min_cash_reserve"):
            v = getattr(self, name)
            if not (Decimal("0") <= v <= Decimal("1")):
                raise ValueError(f"{name} must be in [0, 1], got {v}")
        return self


class SelectionConfig(_FrozenModel):
    """A 股红利的 config。"""

    selection_criteria: list[dict[str, Any]] = Field(default_factory=list)
    entry_signal: ValuationSignal | None = None
    exit_signal: ValuationSignal | None = None
    position_sizing: PositionSizing = Field(default_factory=PositionSizing)


# ────────────────────────────────────────────────────────────────────
# StrategyVersion
# ────────────────────────────────────────────────────────────────────


class StrategyVersion(_FrozenModel):
    """一条策略的某个版本。config 字段按 type 走 AllocationConfig | SelectionConfig。"""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )

    id: int | None = None
    strategy_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    status: StrategyStatus
    notes: str = ""
    config: AllocationConfig | SelectionConfig
    created_at: datetime

    @field_validator("config", mode="before")
    @classmethod
    def _discriminate_config(cls, v: Any) -> Any:
        """根据 strategy_type 自动选 AllocationConfig / SelectionConfig。

        但这层 model 不知道 strategy type，所以用 duck typing：
        - 有 rebalance_trigger 字段 → AllocationConfig
        - 其他 → SelectionConfig
        """
        if isinstance(v, (AllocationConfig, SelectionConfig)):
            return v
        if isinstance(v, dict):
            if "rebalance_trigger" in v or "base_currency" in v:
                return AllocationConfig.model_validate(v)
            return SelectionConfig.model_validate(v)
        return v


# ────────────────────────────────────────────────────────────────────
# PlanSleeve + PlanTarget
# ────────────────────────────────────────────────────────────────────


def _coerce_weight(v: Any) -> Any:
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float, str)):
        return Decimal(str(v))
    raise ValueError(f"Cannot convert {type(v)} to Decimal")


class PlanSleeve(_FrozenModel):
    """sleeve = 策略的子分类。"""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )

    id: int | None = None
    version_id: int = Field(ge=1)
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    target_weight: Decimal
    min_weight: Decimal
    max_weight: Decimal
    tags: list[str] = Field(default_factory=list)
    position: int = Field(default=0, ge=0)

    @field_validator("target_weight", "min_weight", "max_weight", mode="before")
    @classmethod
    def _coerce_weights(cls, v: Any) -> Any:
        return _coerce_weight(v)

    @model_validator(mode="after")
    def _validate_band(self) -> PlanSleeve:
        if not (Decimal("0") <= self.min_weight <= Decimal("1")):
            raise ValueError(f"min_weight out of [0,1]: {self.min_weight}")
        if not (Decimal("0") <= self.max_weight <= Decimal("1")):
            raise ValueError(f"max_weight out of [0,1]: {self.max_weight}")
        if self.min_weight >= self.max_weight:
            raise ValueError(
                f"min_weight ({self.min_weight}) must be < max_weight ({self.max_weight})"
            )
        if not (self.min_weight < self.target_weight < self.max_weight):
            raise ValueError(
                f"target_weight ({self.target_weight}) must be in "
                f"({self.min_weight}, {self.max_weight})"
            )
        return self


class PlanTarget(_FrozenModel):
    """sleeve 下的单只基金目标权重。"""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )

    id: int | None = None
    sleeve_id: int = Field(ge=1)
    fund_code: str = Field(min_length=1, max_length=32)
    weight: Decimal
    min_weight: Decimal
    max_weight: Decimal
    position: int = Field(default=0, ge=0)

    @field_validator("weight", "min_weight", "max_weight", mode="before")
    @classmethod
    def _coerce_weights(cls, v: Any) -> Any:
        return _coerce_weight(v)

    @model_validator(mode="after")
    def _validate_band(self) -> PlanTarget:
        if not (Decimal("0") <= self.min_weight <= Decimal("1")):
            raise ValueError(f"min_weight out of [0,1]: {self.min_weight}")
        if not (Decimal("0") <= self.max_weight <= Decimal("1")):
            raise ValueError(f"max_weight out of [0,1]: {self.max_weight}")
        if self.min_weight >= self.max_weight:
            raise ValueError(
                f"min_weight ({self.min_weight}) must be < max_weight ({self.max_weight})"
            )
        if not (self.min_weight < self.weight < self.max_weight):
            raise ValueError(
                f"weight ({self.weight}) must be in "
                f"({self.min_weight}, {self.max_weight})"
            )
        return self


# ────────────────────────────────────────────────────────────────────
# RebalanceAction + RebalanceSuggestion（计算结果，不存表）
# ────────────────────────────────────────────────────────────────────


_RebalanceActionLiteral = Literal["buy", "sell", "hold"]


class RebalanceAction(_FrozenModel):
    """单只基金的再平衡动作。"""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )

    fund_code: str
    sleeve_code: str
    action: _RebalanceActionLiteral
    current_weight: Decimal
    target_weight: Decimal
    drift: Decimal  # current - target
    current_shares: Decimal
    target_shares: Decimal
    delta_shares: Decimal  # 正数=买，负数=卖
    est_value: Decimal  # |delta_shares × price|
    note: str = ""

    @field_validator(
        "current_weight", "target_weight", "drift",
        "current_shares", "target_shares", "delta_shares", "est_value",
        mode="before",
    )
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        return _coerce_weight(v)


class RebalanceSuggestion(_FrozenModel):
    """一次再平衡扫描的结果。"""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )

    strategy_id: str
    version: int = Field(ge=1)
    as_of: date
    total_value: Decimal
    actions: list[RebalanceAction] = Field(default_factory=list)
    summary: str = ""

    @field_validator("total_value", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        return _coerce_weight(v)


__all__ = [
    "StrategyType",
    "StrategyStatus",
    "Strategy",
    "StrategyVersion",
    "RebalanceTrigger",
    "AllocationConfig",
    "ValuationSignal",
    "PositionSizing",
    "SelectionConfig",
    "PlanSleeve",
    "PlanTarget",
    "RebalanceAction",
    "RebalanceSuggestion",
]
