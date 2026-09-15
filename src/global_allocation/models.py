"""核心数据模型。

参照 specs/010-data-models.md。所有 model 用 Pydantic v2 BaseModel，
frozen=True 以保证不变性（不变量：金融数据不可原地修改）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ────────────────────────────────────────────────────────────────────
# Enums
# ────────────────────────────────────────────────────────────────────


class AssetClass(str, Enum):
    """资产类别。"""

    EQUITY = "equity"
    BOND = "bond"
    COMMODITY = "commodity"
    REIT = "reit"
    CASH = "cash"
    MIXED = "mixed"


class Region(str, Enum):
    """地域。"""

    CN = "cn"
    HK = "hk"
    US = "us"
    EU = "eu"
    JP = "jp"
    GLOBAL = "global"


class Currency(str, Enum):
    """计价币种。"""

    CNY = "cny"
    USD = "usd"
    HKD = "hkd"
    EUR = "eur"
    JPY = "jpy"


class DataSource(str, Enum):
    """数据源。"""

    YFINANCE = "yfinance"
    AKSHARE = "akshare"


# ────────────────────────────────────────────────────────────────────
# 基础 config：frozen=True 强制不变性
# ────────────────────────────────────────────────────────────────────


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )


# ────────────────────────────────────────────────────────────────────
# Asset
# ────────────────────────────────────────────────────────────────────


class Asset(_FrozenModel):
    """一个可投资标的（如 AAPL、510300.SH、000300.SZ）。"""

    symbol: str = Field(min_length=1, max_length=32)
    name: str
    asset_class: AssetClass
    region: Region
    currency: Currency
    data_source: DataSource

    def __eq__(self, other: object) -> bool:
        """两个 Asset 如果 symbol 相同就算相等（同一标的）。"""
        if not isinstance(other, Asset):
            return NotImplemented
        return self.symbol == other.symbol

    def __hash__(self) -> int:
        return hash(self.symbol)


# ────────────────────────────────────────────────────────────────────
# TargetWeight + RebalanceRule
# ────────────────────────────────────────────────────────────────────


class TargetWeight(_FrozenModel):
    """策略对单一资产的目标权重。"""

    asset: Asset
    weight: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))

    @field_validator("weight", mode="before")
    @classmethod
    def _coerce_to_decimal(cls, v: Any) -> Any:
        """允许 float/int 输入，自动转 Decimal。"""
        if isinstance(v, Decimal):
            return v
        if isinstance(v, (int, float, str)):
            return Decimal(str(v))
        raise ValueError(f"Cannot convert {type(v)} to Decimal")


class RebalanceRule(_FrozenModel):
    """再平衡规则。"""

    frequency: Literal["monthly", "quarterly", "yearly", "none"]
    threshold: Decimal | None = None

    @model_validator(mode="after")
    def _validate_threshold(self) -> RebalanceRule:
        if self.threshold is not None:
            # 开区间 (0, 1)
            if not (Decimal("0") < self.threshold < Decimal("1")):
                raise ValueError(f"threshold must be in (0, 1), got {self.threshold}")
        return self


# ────────────────────────────────────────────────────────────────────
# Strategy
# ────────────────────────────────────────────────────────────────────


class Strategy(_FrozenModel):
    """完整策略定义：metadata + 目标权重 + 再平衡规则。"""

    id: str = Field(min_length=1)
    name: str
    description: str
    target_weights: list[TargetWeight] = Field(min_length=1)
    rebalance: RebalanceRule
    base_currency: Currency
    inception: date
    metadata: dict[str, Any] = Field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────
# Trade / RebalanceEvent / PortfolioSnapshot
# ────────────────────────────────────────────────────────────────────


class Trade(_FrozenModel):
    """单笔交易。"""

    symbol: str
    side: Literal["buy", "sell"]
    shares: Decimal = Field(ge=Decimal("0"))
    price: Decimal = Field(ge=Decimal("0"))
    fee: Decimal = Field(ge=Decimal("0"))


class RebalanceEvent(_FrozenModel):
    """一次再平衡事件。"""

    date: date
    triggered_by: Literal["schedule", "threshold"]
    trades: list[Trade]
    cost_bps: Decimal = Field(ge=Decimal("0"))


class PortfolioSnapshot(_FrozenModel):
    """某一天的持仓快照。"""

    date: date
    total_value: Decimal = Field(ge=Decimal("0"))
    positions: dict[str, Decimal]
    weights: dict[str, Decimal]
    cash: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))


# ────────────────────────────────────────────────────────────────────
# PerformanceMetrics + BacktestResult
# ────────────────────────────────────────────────────────────────────


class PerformanceMetrics(_FrozenModel):
    """性能指标。"""

    cagr: Decimal = Field(allow_inf_nan=True)
    sharpe: Decimal = Field(allow_inf_nan=True)
    max_drawdown: Decimal  # 负数
    volatility: Decimal = Field(allow_inf_nan=True)
    total_return: Decimal
    annual_return: Decimal = Field(allow_inf_nan=True)
    # correlation: pd.DataFrame 在 Pydantic 序列化上有限制，
    # 这里用 Any 字段存，类型由 spec 060 保证
    correlation: Any
    best_day: Decimal
    worst_day: Decimal
    win_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))


class BacktestResult(_FrozenModel):
    """一次回测的完整输出。"""

    strategy_id: str
    start_date: date
    end_date: date
    initial_capital: Decimal = Field(gt=Decimal("0"))
    final_value: Decimal = Field(ge=Decimal("0"))
    equity_curve: Any  # pd.DataFrame
    snapshots: list[PortfolioSnapshot]
    metrics: PerformanceMetrics
    rebalance_events: list[RebalanceEvent]

    @model_validator(mode="after")
    def _validate_dates(self) -> BacktestResult:
        if self.end_date < self.start_date:
            raise ValueError(f"end_date ({self.end_date}) must be >= start_date ({self.start_date})")
        return self


__all__ = [
    "AssetClass",
    "Region",
    "Currency",
    "DataSource",
    "Asset",
    "TargetWeight",
    "RebalanceRule",
    "Strategy",
    "Trade",
    "RebalanceEvent",
    "PortfolioSnapshot",
    "PerformanceMetrics",
    "BacktestResult",
]


# 强制 pandas 不被 unused import 警告
_ = pd.DataFrame
