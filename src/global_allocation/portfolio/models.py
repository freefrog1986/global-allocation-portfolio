"""实盘持仓账本模型。

参照 specs/090-portfolio-journal.md + specs/098-valuation-section.md。

Fund / Transaction / Holding / WeeklySnapshot / ValuationIndicator。
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


class ValuationIndicatorCode(str, Enum):
    """估值指标代码（spec 098 — A 股 4 个 + 港股 4 个，共 8 个）。

    str-mixin 让 JSON 序列化直接用 .value（不用 EnumJsonEncoder）。
    DB UNIQUE (record_date, indicator_code) 索引依赖这些字符串值，不能随便改。

    A 股 4 指标（spec 098 第一期）：
    - EQUITY_RISK_PREMIUM：股债利差 = 1/PE - 10Y 国债收益率
    - PE_PERCENTILE：PE 在过去 10 年序列里的百分位
    - BUFFETT_INDICATOR：A 股总市值 / 中国 GDP
    - DIVIDEND_YIELD：中证全A 分红总额 / 总市值

    港股 4 指标（spec 098.2 — liubo 2026-09-19 确认方案 A）：
    - HK_PE_PERCENTILE：恒生指数 PE 分位（10 年窗口）
    - HK_DIVIDEND_YIELD：恒生指数股息率
    - HK_AH_PREMIUM：AH 溢价指数（A 股价格 / H 股价格 ×100 — 值越大 = H 股越便宜）
    - HK_BUFFETT_INDICATOR：港股总市值 / 香港 GDP

    数据源：lixinger CSV（per-fund） + akshare 或手动（4 整体指标）。
    """

    EQUITY_RISK_PREMIUM = "equity_risk_premium"  # 股债利差 = 1/PE - 10Y 国债收益率
    PE_PERCENTILE = "pe_percentile"  # PE 在过去 10 年序列里的百分位
    BUFFETT_INDICATOR = "buffett_indicator"  # A 股总市值 / 中国 GDP
    DIVIDEND_YIELD = "dividend_yield"  # 中证全A 分红总额 / 总市值

    HK_PE_PERCENTILE = "hk_pe_percentile"  # 恒生 PE 分位
    HK_DIVIDEND_YIELD = "hk_dividend_yield"  # 恒生股息率
    HK_AH_PREMIUM = "hk_ah_premium"  # AH 溢价（值大=H 便宜）
    HK_BUFFETT_INDICATOR = "hk_buffett_indicator"  # 港股市值 / 香港GDP

    US_PE_PERCENTILE = "us_pe_percentile"  # 标普 500 / 纳指 100 PE 分位
    US_DIVIDEND_YIELD = "us_dividend_yield"  # 标普 500 股息率
    US_BUFFETT_INDICATOR = "us_buffett_indicator"  # US 总市值 / US GDP
    US_EQUITY_RISK_PREMIUM = "us_equity_risk_premium"  # 美股股债利差 = 1/PE - 美 10Y 国债


class ValuationIndicator(_FrozenModel):
    """单日单个估值指标快照（spec 098）。

    同一天同一指标的多次拉取靠 DB 的 UNIQUE (record_date, indicator_code) 兜底。
    Decimal 存 text（精度无损，跟 transactions / snapshots 一致）。
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )

    record_date: date
    indicator_code: ValuationIndicatorCode
    value: Decimal
    source: str  # "akshare:stock_zh_index_value_dbj_b" 等


class FundValuation(_FrozenModel):
    """单只 A 股基金的单日估值快照（spec 098 第二十七轮 — liubo 2026-09-19 反馈"要按基金给估值"）。

    用 fund_code 而不是 index_code 做主键的一部分，是因为：
    - 多个基金可能跟踪同一个指数（中证A500 + 增强A500 + 中证A500ETF 全部跟踪 000510）
    - 卡片要按"我持有的基金"展示，而不是按"指数"
    - 估值通过 fund_code 关联到 holdings 里的具体基金

    字段说明（按指标分）：
    - index_code：基金跟踪的指数代码（中证全A / 中证A50 / 中证A500 等）
      同一只基金可能跟踪不同指数（如 008114 跟踪红利低波100）。
      存进 DB 是为了：
      1. 卡片显示"跟踪指数"列
      2. 后续能从 CSV 的指数行反查映射回基金
    - pe_ttm：PE-TTM 实数（如 15.85）
    - pe_percentile：PE 在过去 10 年序列里的百分位（fraction：0.4638 = 46.38%）
    - dividend_yield：股息率（fraction：0.0251 = 2.51%）
    - pe_percentile_dy_weighted：股息率加权的 PE 分位（可选 — liubo 2026-09-19
      反馈红利低波要按这个看，来源是银行螺丝钉每日推送的「按股息率加权 PE 分位」；
      其他指数不需要，所以默认 None）
    - roe_latest：最新报告期 ROE（净资产收益率，fraction：0.0834 = 8.34%）；
      用作成长股估值辅助指标 — liubo 反馈"看 PE + 最近 4 季度净利润同比"，
      但理杏仁无净利润同比 API，用 ROE 同比替代（盈利是否在涨）
    - roe_year_ago：去年同期 ROE（同口径）
    - source："lixinger_csv:..." 来源标识
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )

    record_date: date
    fund_code: str = Field(min_length=1, max_length=32)
    index_code: str = Field(min_length=1, max_length=32)
    pe_ttm: Decimal | None = None
    pe_percentile: Decimal | None = None
    dividend_yield: Decimal | None = None
    pe_percentile_dy_weighted: Decimal | None = None
    roe_latest: Decimal | None = None
    roe_year_ago: Decimal | None = None
    source: str


__all__ = [
    "Fund",
    "Transaction",
    "TransactionSide",
    "Holding",
    "WeeklySnapshot",
    "ValuationIndicator",
    "ValuationIndicatorCode",
    "FundValuation",
]
