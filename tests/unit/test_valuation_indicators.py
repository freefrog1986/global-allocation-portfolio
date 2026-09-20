"""测试 src/global_allocation/portfolio/valuation_indicators.py。

spec 098：4 个 compute 函数 + 阈值判断 + 1-5 分打分（第二十一轮加）。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from global_allocation.portfolio.models import ValuationIndicatorCode
from global_allocation.portfolio.valuation_indicators import (
    DEFAULT_THRESHOLDS,
    compute_buffett_indicator,
    compute_composite_score,
    compute_equity_risk_premium,
    compute_pe_percentile,
    compute_verdict,
    interpret_composite_score,
    score_indicator,
    format_5band_threshold,
    DEFAULT_SCORE_BANDS,
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


# ─── 1-5 分打分（spec 098 第二十一轮新增）───


class TestScoreIndicator:
    """4 个指标各 5 档分数（1=极低估，5=极高估）。"""

    # 股债利差 (direction='low'，值大=低估，分数随值减小)
    def test_erp_very_cheap(self) -> None:
        """ERP=6% → 1（极低估，明显加仓机会）。"""
        assert score_indicator(ValuationIndicatorCode.EQUITY_RISK_PREMIUM, Decimal("0.06")) == 1

    def test_erp_cheap(self) -> None:
        """ERP=4.5% → 2（低估）。"""
        assert score_indicator(ValuationIndicatorCode.EQUITY_RISK_PREMIUM, Decimal("0.045")) == 2

    def test_erp_normal(self) -> None:
        """ERP=3% → 3（正常）。"""
        assert score_indicator(ValuationIndicatorCode.EQUITY_RISK_PREMIUM, Decimal("0.03")) == 3

    def test_erp_expensive(self) -> None:
        """ERP=1% → 4（偏高估）。"""
        assert score_indicator(ValuationIndicatorCode.EQUITY_RISK_PREMIUM, Decimal("0.01")) == 4

    def test_erp_very_expensive(self) -> None:
        """ERP=-1%（股票相对债券折价） → 5（极高估）。"""
        assert score_indicator(ValuationIndicatorCode.EQUITY_RISK_PREMIUM, Decimal("-0.01")) == 5

    def test_erp_boundary_at_5pct_is_1(self) -> None:
        """ERP=5%（=极低估边界）→ 1（边界用 >= low 包含）。"""
        assert score_indicator(ValuationIndicatorCode.EQUITY_RISK_PREMIUM, Decimal("0.05")) == 1

    # PE 分位 (direction='high'，值大=高估)
    def test_pe_very_cheap(self) -> None:
        """PE 分位=5% → 1（极低估）。"""
        assert score_indicator(ValuationIndicatorCode.PE_PERCENTILE, Decimal("0.05")) == 1

    def test_pe_cheap(self) -> None:
        """PE 分位=20% → 2。"""
        assert score_indicator(ValuationIndicatorCode.PE_PERCENTILE, Decimal("0.20")) == 2

    def test_pe_normal(self) -> None:
        """PE 分位=50% → 3。"""
        assert score_indicator(ValuationIndicatorCode.PE_PERCENTILE, Decimal("0.50")) == 3

    def test_pe_expensive(self) -> None:
        """PE 分位=80% → 4。"""
        assert score_indicator(ValuationIndicatorCode.PE_PERCENTILE, Decimal("0.80")) == 4

    def test_pe_very_expensive(self) -> None:
        """PE 分位=95% → 5。"""
        assert score_indicator(ValuationIndicatorCode.PE_PERCENTILE, Decimal("0.95")) == 5

    # 巴菲特指标 (direction='high'，值大=高估)
    def test_buffett_very_cheap(self) -> None:
        """巴菲特=30% → 1（明显低估）。"""
        assert score_indicator(ValuationIndicatorCode.BUFFETT_INDICATOR, Decimal("0.30")) == 1

    def test_buffett_cheap(self) -> None:
        """巴菲特=45% → 2。"""
        assert score_indicator(ValuationIndicatorCode.BUFFETT_INDICATOR, Decimal("0.45")) == 2

    def test_buffett_normal(self) -> None:
        """巴菲特=65% → 3。"""
        assert score_indicator(ValuationIndicatorCode.BUFFETT_INDICATOR, Decimal("0.65")) == 3

    def test_buffett_expensive(self) -> None:
        """巴菲特=90% → 4。"""
        assert score_indicator(ValuationIndicatorCode.BUFFETT_INDICATOR, Decimal("0.90")) == 4

    def test_buffett_very_expensive(self) -> None:
        """巴菲特=110%（总市值超过 GDP） → 5（泡沫）。"""
        assert score_indicator(ValuationIndicatorCode.BUFFETT_INDICATOR, Decimal("1.10")) == 5

    # 股息率 (direction='low'，值大=低估)
    def test_dy_very_cheap(self) -> None:
        """股息率=5% → 1（明显低估，高分红）。"""
        assert score_indicator(ValuationIndicatorCode.DIVIDEND_YIELD, Decimal("0.05")) == 1

    def test_dy_cheap(self) -> None:
        """股息率=3.5% → 2。"""
        assert score_indicator(ValuationIndicatorCode.DIVIDEND_YIELD, Decimal("0.035")) == 2

    def test_dy_normal(self) -> None:
        """股息率=2% → 3。"""
        assert score_indicator(ValuationIndicatorCode.DIVIDEND_YIELD, Decimal("0.02")) == 3

    def test_dy_expensive(self) -> None:
        """股息率=0.7% → 4。"""
        assert score_indicator(ValuationIndicatorCode.DIVIDEND_YIELD, Decimal("0.007")) == 4

    def test_dy_very_expensive(self) -> None:
        """股息率=0.3% → 5。"""
        assert score_indicator(ValuationIndicatorCode.DIVIDEND_YIELD, Decimal("0.003")) == 5


class TestComputeCompositeScore:
    """综合分 = 4 个分数的简单平均（不加权）。"""

    def test_basic(self) -> None:
        """1,2,3,4 → 平均 2.5 → 保留 1 位小数 = 2.5。"""
        assert compute_composite_score([1, 2, 3, 4]) == Decimal("2.5")

    def test_single_score(self) -> None:
        """1 个分数 = 自身。"""
        assert compute_composite_score([3]) == Decimal("3.0")

    def test_all_ones(self) -> None:
        """全 1 → 1.0（极低估）。"""
        assert compute_composite_score([1, 1, 1, 1]) == Decimal("1.0")

    def test_all_fives(self) -> None:
        """全 5 → 5.0（极高估）。"""
        assert compute_composite_score([5, 5, 5, 5]) == Decimal("5.0")

    def test_quantizes_to_one_decimal(self) -> None:
        """[1,2,3,4] 平均 = 2.5，[1,2,3,3] 平均 = 2.25 → quantize 到 2.3（round up）。

        用 ROUND_HALF_UP（Decimal 默认）：2.25 → 2.3。
        """
        assert compute_composite_score([1, 2, 3, 3]) == Decimal("2.3")

    def test_empty_list_rejected(self) -> None:
        with pytest.raises(ValueError, match="至少 1 个分数"):
            compute_composite_score([])

    def test_invalid_score_rejected(self) -> None:
        with pytest.raises(ValueError, match="分数必须在 1-5"):
            compute_composite_score([1, 2, 3, 6])

    def test_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="分数必须在 1-5"):
            compute_composite_score([1, 2, 3, 0])

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="分数必须在 1-5"):
            compute_composite_score([1, -1, 3, 4])


class TestInterpretCompositeScore:
    """综合分解读（5 档文案）。"""

    def test_very_low(self) -> None:
        """1.0-1.5 → 极低。"""
        assert interpret_composite_score(Decimal("1.0")) == "极低"
        assert interpret_composite_score(Decimal("1.4")) == "极低"

    def test_low(self) -> None:
        """1.5-2.5 → 低估。"""
        assert interpret_composite_score(Decimal("1.5")) == "低估"
        assert interpret_composite_score(Decimal("2.0")) == "低估"
        assert interpret_composite_score(Decimal("2.4")) == "低估"

    def test_normal(self) -> None:
        """2.5-3.5 → 正常。"""
        assert interpret_composite_score(Decimal("2.5")) == "正常"
        assert interpret_composite_score(Decimal("3.0")) == "正常"
        assert interpret_composite_score(Decimal("3.4")) == "正常"

    def test_high(self) -> None:
        """3.5-4.5 → 偏高估。"""
        assert interpret_composite_score(Decimal("3.5")) == "偏高估"
        assert interpret_composite_score(Decimal("4.0")) == "偏高估"
        assert interpret_composite_score(Decimal("4.4")) == "偏高估"

    def test_very_high(self) -> None:
        """4.5-5.0 → 极高估。"""
        assert interpret_composite_score(Decimal("4.5")) == "极高估"
        assert interpret_composite_score(Decimal("5.0")) == "极高估"


class TestScoreBandCoverage:
    """确保每个指标的 5 个打分区间覆盖所有可能值（不漏不重）。"""

    def test_each_indicator_has_exactly_5_bands(self) -> None:
        from global_allocation.portfolio.valuation_indicators import DEFAULT_SCORE_BANDS

        for code in ValuationIndicatorCode:
            assert len(DEFAULT_SCORE_BANDS[code]) == 5, f"{code} 应该有 5 个分数段"

    def test_each_band_score_in_range_1_to_5(self) -> None:
        from global_allocation.portfolio.valuation_indicators import DEFAULT_SCORE_BANDS

        for code, bands in DEFAULT_SCORE_BANDS.items():
            scores = [b.score for b in bands]
            assert sorted(scores) == [1, 2, 3, 4, 5], f"{code} 分数应覆盖 1-5，实际 {scores}"

    def test_low_extreme_is_minus_infinity_proxy(self) -> None:
        """direction='low' 时最低段 low=-1e10（实际是 -∞ 替身）— 兜底所有负值。"""
        from global_allocation.portfolio.valuation_indicators import DEFAULT_SCORE_BANDS

        # 股债利差 5 分段最低分 = 5，极端值 = -0.05 → 应落入 5 段
        erp_bands = DEFAULT_SCORE_BANDS[ValuationIndicatorCode.EQUITY_RISK_PREMIUM]
        assert score_indicator(ValuationIndicatorCode.EQUITY_RISK_PREMIUM, Decimal("-0.05")) == 5

        # 股息率 5 分段最低分 = 5，极端值 = -0.01 → 应落入 5 段
        dy_bands = DEFAULT_SCORE_BANDS[ValuationIndicatorCode.DIVIDEND_YIELD]
        assert score_indicator(ValuationIndicatorCode.DIVIDEND_YIELD, Decimal("-0.01")) == 5


class TestFormat5BandThreshold:
    """format_5band_threshold 把 5 个 ScoreBand 渲染成卡片阈值文案。

    spec 098 第二十四轮 — liubo 反馈"阈值写太复杂了，简化"。

    卡片文案格式（飞书 cell 用 \\n 换行）：
        行 1：5 个分界点    "≥5%/4%/2%/0%/<0%"
        行 2：5 个分数对应  "1/2/3/4/5"
    - direction='low'（值大=低估）：高分在前（按 ScoreBand 列表顺序）
    - direction='high'（值大=高估）：低分在前
    """

    def test_low_direction_equity_risk_premium(self) -> None:
        """股债利差 direction='low'：高分在前（≥5% = 1 分）。"""
        from global_allocation.portfolio.valuation_indicators import DEFAULT_SCORE_BANDS, format_5band_threshold

        result = format_5band_threshold(DEFAULT_SCORE_BANDS[ValuationIndicatorCode.EQUITY_RISK_PREMIUM])
        assert result == "≥5%/4%/2%/0%/<0%\n1/2/3/4/5"

    def test_high_direction_pe_percentile(self) -> None:
        """PE 分位 direction='high'：低分在前（<10% = 1 分）。"""
        from global_allocation.portfolio.valuation_indicators import DEFAULT_SCORE_BANDS, format_5band_threshold

        result = format_5band_threshold(DEFAULT_SCORE_BANDS[ValuationIndicatorCode.PE_PERCENTILE])
        assert result == "<10%/30%/70%/90%/≥90%\n1/2/3/4/5"

    def test_high_direction_buffett(self) -> None:
        """巴菲特指标：<40% / 50% / 80% / 100% + 极高端 ≥100%。"""
        from global_allocation.portfolio.valuation_indicators import DEFAULT_SCORE_BANDS, format_5band_threshold

        result = format_5band_threshold(DEFAULT_SCORE_BANDS[ValuationIndicatorCode.BUFFETT_INDICATOR])
        assert result == "<40%/50%/80%/100%/≥100%\n1/2/3/4/5"

    def test_low_direction_dividend_yield(self) -> None:
        """股息率：≥4% 起 → 1，每降 1% +1 分（>3=2, >1=3, >0.5=4, <0.5=5）。"""
        from global_allocation.portfolio.valuation_indicators import DEFAULT_SCORE_BANDS, format_5band_threshold

        result = format_5band_threshold(DEFAULT_SCORE_BANDS[ValuationIndicatorCode.DIVIDEND_YIELD])
        assert result == "≥4%/3%/1%/0.5%/<0.5%\n1/2/3/4/5"

    def test_empty_bands_returns_empty_string(self) -> None:
        """空列表 → 空字符串（防御性）。"""
        from global_allocation.portfolio.valuation_indicators import format_5band_threshold

        assert format_5band_threshold([]) == ""


# ─── 港股 4 指标（spec 098.2 — liubo 2026-09-19 方案 A）───


class TestHKValuationThresholds:
    """港股 4 指标的阈值判断（PE 分位 / 股息率 / AH 溢价 / 港股巴菲特）。"""

    def test_hk_pe_low(self) -> None:
        """HK PE 分位 = 20% → < 30% → 偏低估。"""
        assert (
            compute_verdict(ValuationIndicatorCode.HK_PE_PERCENTILE, Decimal("0.20"))
            == "偏低估"
        )

    def test_hk_pe_normal(self) -> None:
        """HK PE 分位 = 50% → 30-70% → 正常。"""
        assert (
            compute_verdict(ValuationIndicatorCode.HK_PE_PERCENTILE, Decimal("0.50"))
            == "正常"
        )

    def test_hk_pe_high(self) -> None:
        """HK PE 分位 = 80% → > 70% → 偏高估。"""
        assert (
            compute_verdict(ValuationIndicatorCode.HK_PE_PERCENTILE, Decimal("0.80"))
            == "偏高估"
        )

    def test_hk_dividend_yield_low(self) -> None:
        """HK 股息率 = 4% → > 3% → 偏低估（H 股高分红 = 便宜）。"""
        assert (
            compute_verdict(ValuationIndicatorCode.HK_DIVIDEND_YIELD, Decimal("0.04"))
            == "偏低估"
        )

    def test_hk_dividend_yield_high(self) -> None:
        """HK 股息率 = 0.5% → < 1% → 偏高估。"""
        assert (
            compute_verdict(ValuationIndicatorCode.HK_DIVIDEND_YIELD, Decimal("0.005"))
            == "偏高估"
        )

    def test_hk_ah_premium_low(self) -> None:
        """AH 溢价 = 1.60（160%）→ H 股巨便宜 → 偏低估（direction='low'）。"""
        assert (
            compute_verdict(ValuationIndicatorCode.HK_AH_PREMIUM, Decimal("1.60"))
            == "偏低估"
        )

    def test_hk_ah_premium_normal(self) -> None:
        """AH 溢价 = 1.15（115%）→ 历史均值附近 → 正常。"""
        assert (
            compute_verdict(ValuationIndicatorCode.HK_AH_PREMIUM, Decimal("1.15"))
            == "正常"
        )

    def test_hk_ah_premium_high(self) -> None:
        """AH 溢价 = 0.30（30%，H 股反而贵）→ 偏高估（≤ high_min=0.40）。"""
        assert (
            compute_verdict(ValuationIndicatorCode.HK_AH_PREMIUM, Decimal("0.30"))
            == "偏高估"
        )

    def test_hk_buffett_low(self) -> None:
        """港股巴菲特 = 6（600%，市值 < 6 倍 GDP）→ 偏低估。"""
        assert (
            compute_verdict(ValuationIndicatorCode.HK_BUFFETT_INDICATOR, Decimal("6"))
            == "偏低估"
        )

    def test_hk_buffett_normal(self) -> None:
        """港股巴菲特 = 10（1000%）→ 正常。"""
        assert (
            compute_verdict(ValuationIndicatorCode.HK_BUFFETT_INDICATOR, Decimal("10"))
            == "正常"
        )

    def test_hk_buffett_high(self) -> None:
        """港股巴菲特 = 14（1400%）→ 偏高估。"""
        assert (
            compute_verdict(ValuationIndicatorCode.HK_BUFFETT_INDICATOR, Decimal("14"))
            == "偏高估"
        )


class TestHKScoring:
    """港股 4 指标的 1-5 分打分。"""

    def test_hk_pe_very_cheap(self) -> None:
        """HK PE 分位 = 5% → 1。"""
        assert score_indicator(ValuationIndicatorCode.HK_PE_PERCENTILE, Decimal("0.05")) == 1

    def test_hk_pe_normal(self) -> None:
        """HK PE 分位 = 50% → 3。"""
        assert score_indicator(ValuationIndicatorCode.HK_PE_PERCENTILE, Decimal("0.50")) == 3

    def test_hk_pe_very_expensive(self) -> None:
        """HK PE 分位 = 95% → 5。"""
        assert score_indicator(ValuationIndicatorCode.HK_PE_PERCENTILE, Decimal("0.95")) == 5

    def test_hk_dy_normal(self) -> None:
        """HK 股息率 = 2% → 3。"""
        assert score_indicator(ValuationIndicatorCode.HK_DIVIDEND_YIELD, Decimal("0.02")) == 3

    def test_hk_ah_very_cheap(self) -> None:
        """AH 溢价 = 1.80（180%，H 巨便宜）→ 1。"""
        assert score_indicator(ValuationIndicatorCode.HK_AH_PREMIUM, Decimal("1.80")) == 1

    def test_hk_ah_very_expensive(self) -> None:
        """AH 溢价 = 0.50（50%，H 反而贵）→ 5。"""
        assert score_indicator(ValuationIndicatorCode.HK_AH_PREMIUM, Decimal("0.50")) == 5

    def test_hk_buffett_very_cheap(self) -> None:
        """港股巴菲特 = 4 → 1（市值 < 4 倍 GDP）。"""
        assert score_indicator(ValuationIndicatorCode.HK_BUFFETT_INDICATOR, Decimal("4")) == 1

    def test_hk_buffett_very_expensive(self) -> None:
        """港股巴菲特 = 16 → 5。"""
        assert score_indicator(ValuationIndicatorCode.HK_BUFFETT_INDICATOR, Decimal("16")) == 5


class TestHKFormat5BandThreshold:
    """港股 4 指标的阈值文案渲染。"""

    def test_hk_pe(self) -> None:
        """HK PE 分位跟 A 股 PE 分位阈值一致：<10%/30%/70%/90%/≥90%。"""
        result = format_5band_threshold(DEFAULT_SCORE_BANDS[ValuationIndicatorCode.HK_PE_PERCENTILE])
        assert result == "<10%/30%/70%/90%/≥90%\n1/2/3/4/5"

    def test_hk_dividend_yield(self) -> None:
        """HK 股息率跟 A 股股息率阈值一致：≥4%/3%/1%/0.5%/<0.5%。"""
        result = format_5band_threshold(DEFAULT_SCORE_BANDS[ValuationIndicatorCode.HK_DIVIDEND_YIELD])
        assert result == "≥4%/3%/1%/0.5%/<0.5%\n1/2/3/4/5"

    def test_hk_ah_premium(self) -> None:
        """AH 溢价：值大=低估 → direction='low'，按 score 1→5 升序排列。
        ScoreBand 1=≥1.60, 2=1.30-1.60, 3=1.00-1.30, 4=0.80-1.00, 5=<0.80
        渲染：≥160%/130%/100%/80%/<80%（format × 100 显示成百分比）
        """
        result = format_5band_threshold(DEFAULT_SCORE_BANDS[ValuationIndicatorCode.HK_AH_PREMIUM])
        assert result == "≥160%/130%/100%/80%/<80%\n1/2/3/4/5"

    def test_hk_buffett(self) -> None:
        """港股巴菲特：值大=高估 → direction='high'，按 score 1→5 升序排列。
        ScoreBand 1=<5, 2=5-8, 3=8-12, 4=12-15, 5=≥15
        渲染：<500%/800%/1200%/1500%/≥1500%（format × 100 显示成百分比 — 15 倍 GDP = 1500%）
        """
        result = format_5band_threshold(DEFAULT_SCORE_BANDS[ValuationIndicatorCode.HK_BUFFETT_INDICATOR])
        assert result == "<500%/800%/1200%/1500%/≥1500%\n1/2/3/4/5"


class TestHKThresholdValidity:
    """港股指标在 DEFAULT_THRESHOLDS 里的合法性。"""

    def test_all_hk_codes_in_thresholds(self) -> None:
        """4 个 HK codes 都有阈值定义。"""
        hk_codes = {
            ValuationIndicatorCode.HK_PE_PERCENTILE,
            ValuationIndicatorCode.HK_DIVIDEND_YIELD,
            ValuationIndicatorCode.HK_AH_PREMIUM,
            ValuationIndicatorCode.HK_BUFFETT_INDICATOR,
        }
        for code in hk_codes:
            assert code in DEFAULT_THRESHOLDS, f"{code} 缺阈值"

    def test_hk_ah_direction_is_low(self) -> None:
        """AH 溢价 direction='low'（值大=H 便宜=低估，跟 股债利差 / 股息率 一致语义）。"""
        assert DEFAULT_THRESHOLDS[ValuationIndicatorCode.HK_AH_PREMIUM].direction == "low"

    def test_hk_dividend_direction_is_low(self) -> None:
        """HK 股息率 direction='low'（值大=H 便宜=低估）。"""
        assert DEFAULT_THRESHOLDS[ValuationIndicatorCode.HK_DIVIDEND_YIELD].direction == "low"

    def test_hk_buffett_direction_is_high(self) -> None:
        """港股巴菲特 direction='high'（值大=贵）。"""
        assert DEFAULT_THRESHOLDS[ValuationIndicatorCode.HK_BUFFETT_INDICATOR].direction == "high"
