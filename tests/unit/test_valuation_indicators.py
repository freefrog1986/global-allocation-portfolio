"""测试 src/global_allocation/portfolio/valuation_indicators.py。

spec 098：4 个 compute 函数 + 阈值判断。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from global_allocation.portfolio.models import ValuationIndicatorCode
from global_allocation.portfolio.valuation_indicators import (
    DEFAULT_THRESHOLDS,
    compute_buffett_indicator,
    compute_equity_risk_premium,
    compute_pe_percentile,
    compute_verdict,
)


class TestEquityRiskPremium:
    def test_basic(self) -> None:
        # PE=20, 国债=3% → 5% − 3% = 2%
        erp = compute_equity_risk_premium(
            pe_ttm=Decimal("20"),
            treasury_yield=Decimal("0.03"),
        )
        assert erp == Decimal("0.02")

    def test_low_pe_high_yield(self) -> None:
        # PE=15, 国债=3% → 6.67% − 3% = 3.67%
        erp = compute_equity_risk_premium(
            pe_ttm=Decimal("15"),
            treasury_yield=Decimal("0.03"),
        )
        assert abs(erp - Decimal("0.03666666666666666666666666667")) < Decimal("1e-10")

    def test_decimal_precision(self) -> None:
        """Decimal 全程精度无损（不转 float）。"""
        erp = compute_equity_risk_premium(
            pe_ttm=Decimal("19.56"),
            treasury_yield=Decimal("0.0285"),
        )
        # 1/19.56 - 0.0285
        expected = Decimal("1") / Decimal("19.56") - Decimal("0.0285")
        assert erp == expected

    def test_negative_pe_rejected(self) -> None:
        with pytest.raises(ValueError, match="PE-TTM 必须为正数"):
            compute_equity_risk_premium(
                pe_ttm=Decimal("-1"),
                treasury_yield=Decimal("0.03"),
            )

    def test_zero_pe_rejected(self) -> None:
        with pytest.raises(ValueError, match="PE-TTM 必须为正数"):
            compute_equity_risk_premium(
                pe_ttm=Decimal("0"),
                treasury_yield=Decimal("0.03"),
            )


class TestPePercentile:
    def test_at_min(self) -> None:
        """PE 等于历史最小 → 分位 = 0。"""
        pct = compute_pe_percentile(
            current_pe=Decimal("10"),
            pe_history=[Decimal("10"), Decimal("15"), Decimal("20")],
        )
        assert pct == Decimal("0")

    def test_at_max(self) -> None:
        """PE 等于历史最大 → 分位 = 1。"""
        pct = compute_pe_percentile(
            current_pe=Decimal("20"),
            pe_history=[Decimal("10"), Decimal("15"), Decimal("20")],
        )
        assert pct == Decimal("1")

    def test_at_middle(self) -> None:
        """PE 在正中间 → 分位 = 0.5。"""
        pct = compute_pe_percentile(
            current_pe=Decimal("15"),
            pe_history=[Decimal("10"), Decimal("15"), Decimal("20")],
        )
        assert pct == Decimal("0.5")

    def test_clamps_below_min(self) -> None:
        """PE 比历史最小还低 → 夹紧到 0。"""
        pct = compute_pe_percentile(
            current_pe=Decimal("5"),
            pe_history=[Decimal("10"), Decimal("15"), Decimal("20")],
        )
        assert pct == Decimal("0")

    def test_clamps_above_max(self) -> None:
        """PE 比历史最大还高 → 夹紧到 1。"""
        pct = compute_pe_percentile(
            current_pe=Decimal("25"),
            pe_history=[Decimal("10"), Decimal("15"), Decimal("20")],
        )
        assert pct == Decimal("1")

    def test_flat_history_returns_half(self) -> None:
        """PE 一直不变 → 取中位数 0.5。"""
        pct = compute_pe_percentile(
            current_pe=Decimal("20"),
            pe_history=[Decimal("20")] * 12,
        )
        assert pct == Decimal("0.5")

    def test_empty_history_rejected(self) -> None:
        with pytest.raises(ValueError, match="PE 历史序列为空"):
            compute_pe_percentile(
                current_pe=Decimal("15"),
                pe_history=[],
            )


class TestBuffettIndicator:
    def test_basic(self) -> None:
        # 总市值 = 650 万亿, GDP = 1000 万亿 → 0.65
        result = compute_buffett_indicator(
            market_cap=Decimal("650") * Decimal("1E12"),
            gdp=Decimal("1000") * Decimal("1E12"),
        )
        assert result == Decimal("0.65")

    def test_above_one_means_bubble(self) -> None:
        """总市值 > GDP → 泡沫（结果 > 1）。"""
        result = compute_buffett_indicator(
            market_cap=Decimal("1200") * Decimal("1E12"),
            gdp=Decimal("1000") * Decimal("1E12"),
        )
        assert result == Decimal("1.2")

    def test_zero_gdp_rejected(self) -> None:
        with pytest.raises(ValueError, match="GDP 必须为正数"):
            compute_buffett_indicator(
                market_cap=Decimal("650") * Decimal("1E12"),
                gdp=Decimal("0"),
            )


class TestVerdict:
    """测试阈值判断（spec 098 第 49~52 行：股债利差/PE分位/巴菲特/股息率 4 个阈值）。"""

    def test_equity_risk_premium_low(self) -> None:
        """ERP = 6% → > 5% 阈值 → 偏低估。"""
        assert (
            compute_verdict(ValuationIndicatorCode.EQUITY_RISK_PREMIUM, Decimal("0.06"))
            == "偏低估"
        )

    def test_equity_risk_premium_normal(self) -> None:
        """ERP = 3% → 在 2%~5% 之间 → 正常。"""
        assert (
            compute_verdict(ValuationIndicatorCode.EQUITY_RISK_PREMIUM, Decimal("0.03"))
            == "正常"
        )

    def test_equity_risk_premium_high(self) -> None:
        """ERP = 1% → < 2% 阈值 → 偏高估。"""
        assert (
            compute_verdict(ValuationIndicatorCode.EQUITY_RISK_PREMIUM, Decimal("0.01"))
            == "偏高估"
        )

    def test_pe_percentile_low(self) -> None:
        """PE 分位 = 20% → < 30% → 偏低估。"""
        assert (
            compute_verdict(ValuationIndicatorCode.PE_PERCENTILE, Decimal("0.20"))
            == "偏低估"
        )

    def test_pe_percentile_normal(self) -> None:
        """PE 分位 = 50% → 30%~70% → 正常。"""
        assert (
            compute_verdict(ValuationIndicatorCode.PE_PERCENTILE, Decimal("0.50"))
            == "正常"
        )

    def test_pe_percentile_high(self) -> None:
        """PE 分位 = 80% → > 70% → 偏高估。"""
        assert (
            compute_verdict(ValuationIndicatorCode.PE_PERCENTILE, Decimal("0.80"))
            == "偏高估"
        )

    def test_buffett_low(self) -> None:
        """巴菲特 = 40% → < 50% → 偏低估。"""
        assert (
            compute_verdict(ValuationIndicatorCode.BUFFETT_INDICATOR, Decimal("0.40"))
            == "偏低估"
        )

    def test_buffett_normal(self) -> None:
        """巴菲特 = 65% → 50%~80% → 正常。"""
        assert (
            compute_verdict(ValuationIndicatorCode.BUFFETT_INDICATOR, Decimal("0.65"))
            == "正常"
        )

    def test_buffett_high(self) -> None:
        """巴菲特 = 90% → > 80% → 偏高估。"""
        assert (
            compute_verdict(ValuationIndicatorCode.BUFFETT_INDICATOR, Decimal("0.90"))
            == "偏高估"
        )

    def test_dividend_yield_low(self) -> None:
        """股息率 = 3.5% → > 3% → 偏低估。"""
        assert (
            compute_verdict(ValuationIndicatorCode.DIVIDEND_YIELD, Decimal("0.035"))
            == "偏低估"
        )

    def test_dividend_yield_normal(self) -> None:
        """股息率 = 2% → 1%~3% → 正常。"""
        assert (
            compute_verdict(ValuationIndicatorCode.DIVIDEND_YIELD, Decimal("0.02"))
            == "正常"
        )

    def test_dividend_yield_high(self) -> None:
        """股息率 = 0.5% → < 1% → 偏高估。"""
        assert (
            compute_verdict(ValuationIndicatorCode.DIVIDEND_YIELD, Decimal("0.005"))
            == "偏高估"
        )


class TestVerdictBoundaries:
    """测试阈值边界（精确等于 low_max / high_min 应该归到哪边）。"""

    def test_erp_exactly_at_low_boundary(self) -> None:
        """ERP = 5% （= low_max） → 应该归"偏低估"（direction='low' 用 >=）。"""
        assert (
            compute_verdict(ValuationIndicatorCode.EQUITY_RISK_PREMIUM, Decimal("0.05"))
            == "偏低估"
        )

    def test_erp_just_above_low_boundary(self) -> None:
        """ERP = 5.01% → 偏低估。"""
        assert (
            compute_verdict(ValuationIndicatorCode.EQUITY_RISK_PREMIUM, Decimal("0.0501"))
            == "偏低估"
        )

    def test_pe_exactly_at_high_boundary(self) -> None:
        """PE 分位 = 70% （= high_min） → 应该归"偏高估"（direction='high' 用 >=）。"""
        assert (
            compute_verdict(ValuationIndicatorCode.PE_PERCENTILE, Decimal("0.70"))
            == "偏高估"
        )


class TestDefaultThresholds:
    """确保 DEFAULT_THRESHOLDS 跟 spec 一致。"""

    def test_all_four_codes_covered(self) -> None:
        assert set(DEFAULT_THRESHOLDS.keys()) == set(ValuationIndicatorCode)

    def test_threshold_validity(self) -> None:
        """阈值要合法：direction='low' → low_max > high_min；direction='high' → low_max < high_min。"""
        for code, t in DEFAULT_THRESHOLDS.items():
            if t.direction == "low":
                # 股债利差 / 股息率：值大=低估 → 低估区间 = [low_max, +∞)，高估 = (-∞, high_min]
                assert t.low_max > t.high_min, f"{code}: low_max={t.low_max} <= high_min={t.high_min}"
            else:
                # PE 分位 / 巴菲特：值大=高估 → 低估区间 = (-∞, low_max]，高估 = [high_min, +∞)
                assert t.low_max < t.high_min, f"{code}: low_max={t.low_max} >= high_min={t.high_min}"

    def test_direction_matches_code(self) -> None:
        """direction 必须匹配指标语义：
        - 股债利差 / 股息率：值大 = 低估 → 'low'
        - PE 分位 / 巴菲特：值大 = 高估 → 'high'
        """
        assert DEFAULT_THRESHOLDS[ValuationIndicatorCode.EQUITY_RISK_PREMIUM].direction == "low"
        assert DEFAULT_THRESHOLDS[ValuationIndicatorCode.PE_PERCENTILE].direction == "high"
        assert DEFAULT_THRESHOLDS[ValuationIndicatorCode.BUFFETT_INDICATOR].direction == "high"
        assert DEFAULT_THRESHOLDS[ValuationIndicatorCode.DIVIDEND_YIELD].direction == "low"
