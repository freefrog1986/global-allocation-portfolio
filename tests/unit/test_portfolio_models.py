"""测试 src/global_allocation/portfolio/models.py。

参照 specs/090-portfolio-journal.md。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from global_allocation.models import AssetClass, Currency, DataSource
from global_allocation.portfolio.models import (
    Fund,
    FundValuation,
    Holding,
    Transaction,
    TransactionSide,
    ValuationIndicator,
    ValuationIndicatorCode,
    WeeklySnapshot,
)


class TestFund:
    def test_basic(self) -> None:
        f = Fund(code="163406", name="兴全合润", asset_class=AssetClass.MIXED)
        assert f.code == "163406"
        assert f.name == "兴全合润"
        assert f.asset_class == AssetClass.MIXED
        assert f.currency == Currency.CNY  # default
        assert f.data_source == DataSource.AKSHARE  # default for CN funds

    def test_explicit_currency(self) -> None:
        f = Fund(
            code="VT", name="Vanguard Total World", asset_class=AssetClass.EQUITY,
            currency=Currency.USD, data_source=DataSource.YFINANCE,
        )
        assert f.currency == Currency.USD
        assert f.data_source == DataSource.YFINANCE

    def test_code_required(self) -> None:
        with pytest.raises(ValidationError):
            Fund(name="x", asset_class=AssetClass.EQUITY)  # type: ignore[call-arg]

    def test_immutable(self) -> None:
        from dataclasses import FrozenInstanceError

        f = Fund(code="163406", name="x", asset_class=AssetClass.EQUITY)
        with pytest.raises((FrozenInstanceError, ValidationError)):
            f.name = "y"  # type: ignore[misc]


class TestTransaction:
    def test_buy(self) -> None:
        tx = Transaction(
            fund_code="163406",
            side=TransactionSide.BUY,
            date=date(2026, 9, 10),
            shares=Decimal("1000"),
            price=Decimal("2.350"),
            fee=Decimal("1.20"),
            strategy="月度定投扣款",
            tags=["dca", "monthly"],
        )
        assert tx.side == TransactionSide.BUY
        assert tx.fund_code == "163406"
        assert tx.shares == Decimal("1000")
        assert tx.fee == Decimal("1.20")
        assert tx.tags == ["dca", "monthly"]
        assert tx.strategy == "月度定投扣款"
        assert tx.note is None

    def test_sell(self) -> None:
        tx = Transaction(
            fund_code="163406",
            side=TransactionSide.SELL,
            date=date(2026, 9, 12),
            shares=Decimal("200"),
            price=Decimal("2.45"),
        )
        assert tx.side == TransactionSide.SELL

    def test_negative_shares_rejected(self) -> None:
        with pytest.raises(ValidationError, match="shares"):
            Transaction(
                fund_code="163406",
                side=TransactionSide.BUY,
                date=date(2026, 9, 10),
                shares=Decimal("-100"),
                price=Decimal("2.350"),
            )

    def test_negative_price_rejected(self) -> None:
        with pytest.raises(ValidationError, match="price"):
            Transaction(
                fund_code="163406",
                side=TransactionSide.BUY,
                date=date(2026, 9, 10),
                shares=Decimal("100"),
                price=Decimal("-2"),
            )

    def test_negative_fee_rejected(self) -> None:
        with pytest.raises(ValidationError, match="fee"):
            Transaction(
                fund_code="163406",
                side=TransactionSide.BUY,
                date=date(2026, 9, 10),
                shares=Decimal("100"),
                price=Decimal("2"),
                fee=Decimal("-1"),
            )

    def test_default_fee_zero(self) -> None:
        tx = Transaction(
            fund_code="163406",
            side=TransactionSide.BUY,
            date=date(2026, 9, 10),
            shares=Decimal("100"),
            price=Decimal("2"),
        )
        assert tx.fee == Decimal("0")

    def test_default_tags_empty(self) -> None:
        tx = Transaction(
            fund_code="163406",
            side=TransactionSide.BUY,
            date=date(2026, 9, 10),
            shares=Decimal("100"),
            price=Decimal("2"),
        )
        assert tx.tags == []


class TestHolding:
    def _make_fund(self) -> Fund:
        return Fund(code="163406", name="兴全合润", asset_class=AssetClass.MIXED)

    def test_with_market_price(self) -> None:
        h = Holding(
            fund=self._make_fund(),
            shares=Decimal("1000"),
            avg_cost=Decimal("2.30"),
            market_price=Decimal("2.50"),
        )
        assert h.market_value == Decimal("2500")
        assert h.cost_basis == Decimal("2300")
        assert h.unrealized_pnl == Decimal("200")
        # 200/2300 = 0.086956...
        assert abs(h.unrealized_pnl_pct - Decimal("0.08695652173913043478260869565")) < Decimal("1E-10")

    def test_without_market_price(self) -> None:
        h = Holding(
            fund=self._make_fund(),
            shares=Decimal("1000"),
            avg_cost=Decimal("2.30"),
            market_price=None,
        )
        assert h.market_value is None
        assert h.unrealized_pnl is None
        assert h.unrealized_pnl_pct is None


class TestWeeklySnapshot:
    def test_basic(self) -> None:
        holdings = [
            {"fund_code": "163406", "shares": "1000", "market_value": "2500", "weight": "0.5", "pnl_pct": "0.08"},
            {"fund_code": "510300", "shares": "500", "market_value": "2500", "weight": "0.5", "pnl_pct": "0.04"},
        ]
        snap = WeeklySnapshot(
            week_end_date=date(2026, 9, 11),
            total_value=Decimal("5000"),
            week_return=Decimal("0.012"),
            cumulative_return=Decimal("0.085"),
            holdings_json=json.dumps(holdings),
            created_at=datetime(2026, 9, 11, 17, 0, 0),
        )
        assert snap.total_value == Decimal("5000")
        assert snap.week_return == Decimal("0.012")
        assert snap.cumulative_return == Decimal("0.085")
        # holdings_json 可往返解析
        parsed = json.loads(snap.holdings_json)
        assert len(parsed) == 2
        assert parsed[0]["fund_code"] == "163406"

    def test_defaults(self) -> None:
        snap = WeeklySnapshot(
            week_end_date=date(2026, 9, 11),
            total_value=Decimal("5000"),
            holdings_json="[]",
        )
        assert snap.week_return == Decimal("0")
        assert snap.cumulative_return == Decimal("0")

    def test_negative_total_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WeeklySnapshot(
                week_end_date=date(2026, 9, 11),
                total_value=Decimal("-1"),
                holdings_json="[]",
            )


class TestTransactionSide:
    def test_values(self) -> None:
        assert TransactionSide.BUY.value == "buy"
        assert TransactionSide.SELL.value == "sell"


class TestValuationIndicatorCode:
    """spec 098 + 098.2：估值指标代码枚举。

    关键设计：str-mixin（继承 str），JSON 序列化直接用 .value。
    12 个指标对应 3 个大类的估值（A 股 4 + 港股 4 + 美股 4）。
    DB UNIQUE (record_date, indicator_code) 索引依赖 .value 字符串值，不能随便改。

    A 股 4 维度：股债利差 / PE 分位 / 巴菲特 / 股息率（spec 098 第一期）
    港股 4 维度：PE 分位 / 股息率 / AH 溢价 / 港股巴菲特（spec 098.2 — liubo 2026-09-19 方案 A）
    美股 4 维度：PE 分位 / 股息率 / 美股巴菲特 / 美股股债利差（spec 098.3 — liubo 2026-09-20 拍板）
    """

    def test_has_twelve_codes(self) -> None:
        """4 个 A 股 + 4 个港股 + 4 个美股 = 12 个。"""
        assert len(ValuationIndicatorCode) == 12

    def test_specific_values(self) -> None:
        """spec 098 + 098.2 + 098.3 写死的 12 个 code — DB UNIQUE 索引依赖这些字符串。"""
        # A 股 4
        assert ValuationIndicatorCode.EQUITY_RISK_PREMIUM.value == "equity_risk_premium"
        assert ValuationIndicatorCode.PE_PERCENTILE.value == "pe_percentile"
        assert ValuationIndicatorCode.BUFFETT_INDICATOR.value == "buffett_indicator"
        assert ValuationIndicatorCode.DIVIDEND_YIELD.value == "dividend_yield"
        # 港股 4
        assert ValuationIndicatorCode.HK_PE_PERCENTILE.value == "hk_pe_percentile"
        assert ValuationIndicatorCode.HK_DIVIDEND_YIELD.value == "hk_dividend_yield"
        assert ValuationIndicatorCode.HK_AH_PREMIUM.value == "hk_ah_premium"
        assert ValuationIndicatorCode.HK_BUFFETT_INDICATOR.value == "hk_buffett_indicator"
        # 美股 4
        assert ValuationIndicatorCode.US_PE_PERCENTILE.value == "us_pe_percentile"
        assert ValuationIndicatorCode.US_DIVIDEND_YIELD.value == "us_dividend_yield"
        assert ValuationIndicatorCode.US_BUFFETT_INDICATOR.value == "us_buffett_indicator"
        assert ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM.value == "us_equity_risk_premium"

    def test_is_str_mixin(self) -> None:
        """继承 str，所以可以直接当字符串用（序列化 / 比较）。"""
        code = ValuationIndicatorCode.PE_PERCENTILE
        assert isinstance(code, str)
        assert code == "pe_percentile"
        assert code.value == "pe_percentile"

    def test_lookup_by_value(self) -> None:
        """DB 读出来的字符串能反向查回 enum（_row_to_valuation_indicator 用）。"""
        assert ValuationIndicatorCode("equity_risk_premium") == ValuationIndicatorCode.EQUITY_RISK_PREMIUM


class TestValuationIndicator:
    """spec 098：单日单个估值指标快照。"""

    def test_basic(self) -> None:
        ind = ValuationIndicator(
            record_date=date(2026, 9, 19),
            indicator_code=ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
            value=Decimal("0.052"),
            source="akshare:stock_zh_index_value_dbj_b",
        )
        assert ind.record_date == date(2026, 9, 19)
        assert ind.indicator_code == ValuationIndicatorCode.EQUITY_RISK_PREMIUM
        assert ind.value == Decimal("0.052")
        assert ind.source == "akshare:stock_zh_index_value_dbj_b"

    def test_decimal_precision(self) -> None:
        """Decimal 高精度（PE 分位可能是 0.287351...）不应被 float 截断。"""
        ind = ValuationIndicator(
            record_date=date(2026, 9, 19),
            indicator_code=ValuationIndicatorCode.PE_PERCENTILE,
            value=Decimal("0.2873519234"),
            source="akshare:test",
        )
        assert ind.value == Decimal("0.2873519234")

    def test_immutable(self) -> None:
        from dataclasses import FrozenInstanceError

        ind = ValuationIndicator(
            record_date=date(2026, 9, 19),
            indicator_code=ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
            value=Decimal("0.05"),
            source="x",
        )
        with pytest.raises((FrozenInstanceError, ValidationError)):
            ind.value = Decimal("0.06")  # type: ignore[misc]

    def test_required_fields(self) -> None:
        """缺 record_date / code / value / source 都应 ValidationError。"""
        with pytest.raises(ValidationError):
            ValuationIndicator(  # type: ignore[call-arg]
                indicator_code=ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
                value=Decimal("0.05"),
                source="x",
            )
        with pytest.raises(ValidationError):
            ValuationIndicator(  # type: ignore[call-arg]
                record_date=date(2026, 9, 19),
                value=Decimal("0.05"),
                source="x",
            )

    def test_extra_fields_rejected(self) -> None:
        """extra='forbid'（跟其他模型一致） — 不接受未知字段。"""
        with pytest.raises(ValidationError):
            ValuationIndicator(
                record_date=date(2026, 9, 19),
                indicator_code=ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
                value=Decimal("0.05"),
                source="x",
                unknown_field="bad",  # type: ignore[call-arg]
            )


class TestFundValuation:
    """每只 A 股基金的估值快照（spec 098 第二十七轮）。"""

    def test_basic(self) -> None:
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="014532",
            index_code="930050",
            pe_ttm=Decimal("15.7851"),
            pe_percentile=Decimal("0.1626"),
            dividend_yield=Decimal("0.0298"),
            source="lixinger_csv:foo.csv",
        )
        assert fv.fund_code == "014532"
        assert fv.index_code == "930050"
        assert fv.pe_ttm == Decimal("15.7851")
        # 新增字段默认值
        assert fv.pe_percentile_dy_weighted is None
        assert fv.roe_latest is None
        assert fv.roe_year_ago is None

    def test_with_dividend_yielded_pe_pct(self) -> None:
        """红利低波带股息率加权 PE 分位（银行螺丝钉手动填）。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="008114",
            index_code="930955",
            pe_ttm=Decimal("8.85"),
            pe_percentile=Decimal("0.8180"),  # 普通 PE 分位 81.8%
            dividend_yield=Decimal("0.0448"),
            pe_percentile_dy_weighted=Decimal("0.20"),  # 股息率加权 PE 分位 20%
            source="lixinger_csv:foo.csv + 银行螺丝钉",
        )
        assert fv.pe_percentile_dy_weighted == Decimal("0.20")

    def test_with_growth_roe(self) -> None:
        """成长股带 ROE 最新 + 去年（用于算 ROE 同比）。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="013310",
            index_code="931643",
            pe_ttm=Decimal("51.20"),
            pe_percentile=Decimal("0.65"),
            dividend_yield=Decimal("0.006"),
            roe_latest=Decimal("0.0834"),  # 2026Q2
            roe_year_ago=Decimal("0.0543"),  # 2025Q2
            source="lixinger_csv:foo.csv",
        )
        assert fv.roe_latest == Decimal("0.0834")
        assert fv.roe_year_ago == Decimal("0.0543")

    def test_optional_fields_default_none(self) -> None:
        """所有可选字段默认 None。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="008114",
            index_code="930955",
            pe_ttm=None,
            pe_percentile=None,
            dividend_yield=None,
            source="manual",
        )
        assert fv.pe_ttm is None
        assert fv.pe_percentile is None
        assert fv.dividend_yield is None
        assert fv.pe_percentile_dy_weighted is None
        assert fv.roe_latest is None
        assert fv.roe_year_ago is None

    def test_immutable(self) -> None:
        from dataclasses import FrozenInstanceError

        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="014532",
            index_code="930050",
            pe_ttm=Decimal("15.78"),
            pe_percentile=Decimal("0.16"),
            dividend_yield=Decimal("0.03"),
            source="x",
        )
        with pytest.raises((FrozenInstanceError, ValidationError)):
            fv.fund_code = "999999"  # type: ignore[misc]

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FundValuation(
                record_date=date(2026, 9, 19),
                fund_code="014532",
                index_code="930050",
                pe_ttm=Decimal("15.78"),
                pe_percentile=Decimal("0.16"),
                dividend_yield=Decimal("0.03"),
                source="x",
                unknown="bad",  # type: ignore[call-arg]
            )
