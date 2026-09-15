"""实盘持仓账本模型。

参照 specs/090-portfolio-journal.md。

Fund / Transaction / Holding / WeeklySnapshot。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import ConfigDict, Field, field_validator

from global_allocation.models import (
    AssetClass,
    Currency,
    DataSource,
    _FrozenModel,
)


class TransactionSide(str, Enum):
    """买卖方向。"""

    BUY = "buy"
    SELL = "sell"


class Fund(_FrozenModel):
    """一个基金 / ETF / 股票。MVP 只支持单基金（人民币）。"""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )

    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1)
    asset_class: AssetClass
    data_source: DataSource = DataSource.AKSHARE
    currency: Currency = Currency.CNY

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Fund):
            return NotImplemented
        return self.code == other.code

    def __hash__(self) -> int:
        return hash(self.code)


class Transaction(_FrozenModel):
    """一笔交易（买入或卖出）。"""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )

    id: int | None = None
    fund_code: str = Field(min_length=1, max_length=32)
    side: TransactionSide
    date: date
    shares: Decimal = Field(gt=Decimal("0"))
    price: Decimal = Field(gt=Decimal("0"))
    fee: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    strategy: str | None = None
    tags: list[str] = Field(default_factory=list)
    note: str | None = None
    created_at: datetime | None = None

    @field_validator("shares", "price", "fee", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Any:
        """允许 float / int / str 输入，自动转 Decimal。"""
        if isinstance(v, Decimal):
            return v
        if isinstance(v, (int, float, str)):
            return Decimal(str(v))
        raise ValueError(f"Cannot convert {type(v)} to Decimal")


class Holding:
    """当前持仓 = BUY - SELL 聚合后的视图。

    非 pydantic model（不存表，按需计算）。
    """

    def __init__(
        self,
        fund: Fund,
        shares: Decimal,
        avg_cost: Decimal,
        market_price: Decimal | None,
    ) -> None:
        self.fund = fund
        self.shares = shares
        self.avg_cost = avg_cost
        self.market_price = market_price

    @property
    def market_value(self) -> Decimal | None:
        if self.market_price is None:
            return None
        return self.shares * self.market_price

    @property
    def cost_basis(self) -> Decimal:
        return self.shares * self.avg_cost

    @property
    def unrealized_pnl(self) -> Decimal | None:
        mv = self.market_value
        if mv is None:
            return None
        return mv - self.cost_basis

    @property
    def unrealized_pnl_pct(self) -> Decimal | None:
        cb = self.cost_basis
        if cb == 0 or self.market_price is None:
            return None
        pnl = self.unrealized_pnl
        if pnl is None:
            return None
        return pnl / cb


class WeeklySnapshot(_FrozenModel):
    """一周的组合快照。"""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )

    id: int | None = None
    week_end_date: date
    total_value: Decimal = Field(ge=Decimal("0"))
    week_return: Decimal = Decimal("0")
    cumulative_return: Decimal = Decimal("0")
    holdings_json: str
    created_at: datetime | None = None


__all__ = [
    "Fund",
    "Transaction",
    "TransactionSide",
    "Holding",
    "WeeklySnapshot",
]
