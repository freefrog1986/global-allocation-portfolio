"""测试 src/global_allocation/portfolio/valuation_source.py。

akshare 调用全部 mock（避免网络依赖 + 速度）。
测试覆盖：6 个方法 + 失败返回 None + 容错（找不到精确日期用最新一条）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest

from global_allocation.portfolio.valuation_source import (
    AkshareValuationSource,
    ValuationSource,
)


def _make_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """构造模拟的 akshare 返回 DataFrame。"""
    return pd.DataFrame(rows)


# 假装 pandas 是 akshare 模块 — 用 patch 模拟所有 akshare API
@pytest.fixture
def mock_akshare() -> Any:
    """统一 mock 所有 akshare 接口（每个测试用 monkeypatch.setattr 自己覆盖）。"""
    with patch("akshare.stock_zh_index_value_csindex") as mock_csindex, \
         patch("akshare.stock_a_ttm_lyr") as mock_ttm, \
         patch("akshare.bond_zh_us_rate") as mock_yield, \
         patch("akshare.stock_zh_a_spot_em") as mock_spot, \
         patch("akshare.stock_sse_summary") as mock_sse, \
         patch("akshare.stock_szse_summary") as mock_szse, \
         patch("akshare.macro_china_stock_market_cap") as mock_cap, \
         patch("akshare.macro_china_gdp") as mock_gdp, \
         patch("akshare.stock_hk_index_daily_sina") as mock_hsi, \
         patch("akshare.macro_china_hk_gbp") as mock_hk_gbp:
        yield {
            "csindex": mock_csindex,
            "ttm": mock_ttm,
            "yield": mock_yield,
            "spot": mock_spot,
            "sse": mock_sse,
            "szse": mock_szse,
            "cap": mock_cap,
            "gdp": mock_gdp,
            "hsi": mock_hsi,
            "hk_gbp": mock_hk_gbp,
        }


class TestProtocolConformance:
    """AkshareValuationSource 必须满足 Protocol（duck-typed，不需要显式继承）。"""

    def test_satisfies_protocol(self) -> None:
        src: ValuationSource = AkshareValuationSource()
        assert hasattr(src, "get_pe_ttm")
        assert hasattr(src, "get_pe_history")
        assert hasattr(src, "get_dividend_yield")
        assert hasattr(src, "get_10y_treasury_yield")
        assert hasattr(src, "get_a_share_total_market_cap")
        assert hasattr(src, "get_china_gdp")


class TestGetPeTtm:
    """get_pe_ttm 现在用 stock_a_ttm_lyr（latest row middlePETTM）— 同口径配 history。"""

    def test_returns_decimal(self, mock_akshare: dict[str, Any]) -> None:
        mock_akshare["ttm"].return_value = _make_df([
            {"date": "2025-12-31", "middlePETTM": 35.0},
            {"date": "2026-09-17", "middlePETTM": 36.77},
        ])
        src = AkshareValuationSource()
        pe = src.get_pe_ttm(date(2026, 9, 17))
        # 返回最新一行 middlePETTM = 36.77
        assert pe == Decimal("36.77")

    def test_returns_latest_when_no_exact_match(
        self, mock_akshare: dict[str, Any]
    ) -> None:
        """on 参数不影响结果 — 接口返回的是最新一行（截面数据）。"""
        mock_akshare["ttm"].return_value = _make_df([
            {"date": "2026-09-17", "middlePETTM": 36.77},
        ])
        src = AkshareValuationSource()
        # 任意日期都返回 latest
        pe = src.get_pe_ttm(date(2026, 9, 19))
        assert pe == Decimal("36.77")

    def test_returns_none_on_empty(self, mock_akshare: dict[str, Any]) -> None:
        mock_akshare["ttm"].return_value = _make_df([])
        src = AkshareValuationSource()
        assert src.get_pe_ttm(date(2026, 9, 17)) is None

    def test_returns_none_on_exception(self, mock_akshare: dict[str, Any]) -> None:
        mock_akshare["ttm"].side_effect = RuntimeError("network error")
        src = AkshareValuationSource()
        assert src.get_pe_ttm(date(2026, 9, 17)) is None

    def test_returns_none_on_nan(self, mock_akshare: dict[str, Any]) -> None:
        import numpy as np
        mock_akshare["ttm"].return_value = _make_df([
            {"date": "2026-09-17", "middlePETTM": np.nan},
        ])
        src = AkshareValuationSource()
        assert src.get_pe_ttm(date(2026, 9, 17)) is None


class TestGetPeHistory:
    def test_returns_decimal_list(self, mock_akshare: dict[str, Any]) -> None:
        # 模拟 261 行月频 PE
        rows = [
            {"middlePETTM": 20 + i * 0.05}
            for i in range(261)
        ]
        mock_akshare["ttm"].return_value = _make_df(rows)
        src = AkshareValuationSource()
        history = src.get_pe_history(years=10)
        # 10 年月频 → 120 行
        assert len(history) == 120
        # 全部 Decimal
        assert all(isinstance(pe, Decimal) for pe in history)
        # 第一行是最早（10 年前），最后一行是最新
        assert history[0] < history[-1]

    def test_filters_nan(self, mock_akshare: dict[str, Any]) -> None:
        import numpy as np
        rows = [{"middlePETTM": 20.0}] * 100
        rows += [{"middlePETTM": np.nan}] * 10
        rows += [{"middlePETTM": 25.0}] * 100
        mock_akshare["ttm"].return_value = _make_df(rows)
        src = AkshareValuationSource()
        history = src.get_pe_history(years=10)
        # 210 个有效值（去掉 10 个 NaN）— 但 years=10 截到 120
        assert all(pe == 20.0 or pe == 25.0 for pe in history)
        assert None not in history  # 没有 NaN 漏出来

    def test_returns_empty_on_failure(self, mock_akshare: dict[str, Any]) -> None:
        mock_akshare["ttm"].side_effect = RuntimeError("network error")
        src = AkshareValuationSource()
        assert src.get_pe_history() == []

    def test_returns_empty_on_empty_df(self, mock_akshare: dict[str, Any]) -> None:
        mock_akshare["ttm"].return_value = _make_df([])
        src = AkshareValuationSource()
        assert src.get_pe_history() == []

    def test_years_zero_returns_all(self, mock_akshare: dict[str, Any]) -> None:
        """years=0 特殊含义：不截取，返回全部（spec 098 第 32 行容错）。"""
        rows = [{"middlePETTM": 20.0 + i} for i in range(50)]
        mock_akshare["ttm"].return_value = _make_df(rows)
        src = AkshareValuationSource()
        history = src.get_pe_history(years=0)
        assert len(history) == 50


class TestGetDividendYield:
    def test_returns_decimal_as_fraction(self, mock_akshare: dict[str, Any]) -> None:
        """股息率返回 0~1 小数（spec 098 第 32 行：'中证全A 分红总额 / 总市值'）。"""
        mock_akshare["csindex"].return_value = _make_df([
            {"日期": "2026-09-17", "市盈率1": 19.56, "股息率1": 2.5},  # 2.5%
        ])
        src = AkshareValuationSource()
        dy = src.get_dividend_yield(date(2026, 9, 17))
        assert dy == Decimal("0.025")  # 2.5% → 0.025

    def test_returns_none_on_failure(self, mock_akshare: dict[str, Any]) -> None:
        mock_akshare["csindex"].side_effect = RuntimeError("error")
        src = AkshareValuationSource()
        assert src.get_dividend_yield(date(2026, 9, 17)) is None


class TestGet10YTreasuryYield:
    def test_returns_10y_china_gov_yield(self, mock_akshare: dict[str, Any]) -> None:
        """数据源 = ak.bond_zh_us_rate()，列 = "中国国债收益率10年"。

        早期用 ak.bond_china_yield()（"中债国债收益率曲线"）但其数据卡在 2021。
        换 bond_zh_us_rate 后每天都有新数据。
        """
        mock_akshare["yield"].return_value = _make_df([
            {"日期": "2026-09-17", "中国国债收益率10年": 1.68},
        ])
        src = AkshareValuationSource()
        y = src.get_10y_treasury_yield(date(2026, 9, 17))
        # 1.68% → 0.0168
        assert y == Decimal("0.0168")

    def test_returns_latest_when_no_exact_match(
        self, mock_akshare: dict[str, Any]
    ) -> None:
        """节假日 / 周末 akshare 不更新时取最新一条。"""
        mock_akshare["yield"].return_value = _make_df([
            {"日期": "2026-09-17", "中国国债收益率10年": 1.68},
            {"日期": "2026-09-18", "中国国债收益率10年": 1.70},
        ])
        src = AkshareValuationSource()
        # on=9/19（周六，没数据）→ fallback 到 9/18 最新
        y = src.get_10y_treasury_yield(date(2026, 9, 19))
        assert y == Decimal("0.0170")

    def test_returns_none_when_10y_column_missing(
        self, mock_akshare: dict[str, Any]
    ) -> None:
        """中国国债收益率10年列缺失 → None（不是要查其他列）。"""
        mock_akshare["yield"].return_value = _make_df([
            {"日期": "2026-09-17", "中国国债收益率2年": 1.5},
        ])
        src = AkshareValuationSource()
        assert src.get_10y_treasury_yield(date(2026, 9, 17)) is None

    def test_returns_none_on_failure(self, mock_akshare: dict[str, Any]) -> None:
        mock_akshare["yield"].side_effect = RuntimeError("error")
        src = AkshareValuationSource()
        assert src.get_10y_treasury_yield(date(2026, 9, 17)) is None


class TestGetAShareTotalMarketCap:
    """主路径：上交所 + 深交所 summary（当日数据）。Fallback chain 见其他 test。"""

    def test_sums_sse_and_szse_total_market_caps(
        self, mock_akshare: dict[str, Any]
    ) -> None:
        """主路径：上交所"股票"总市值（亿元）+ 深交所"股票"总市值（元）。"""
        mock_akshare["sse"].return_value = _make_df([
            {"项目": "总市值", "股票": 700000.0},  # 上交所 700000 亿元
        ])
        mock_akshare["szse"].return_value = _make_df([
            {"证券类别": "股票", "总市值": 2.5e13},  # 深交所 25 万亿 = 2.5e13 元
        ])
        src = AkshareValuationSource()
        cap = src.get_a_share_total_market_cap(date(2026, 9, 17))
        # 700000 亿 + 25e5 亿 = 950000 亿 = 9.5e13 元
        assert cap == Decimal("950000") * Decimal("100000000")

    def test_returns_none_when_both_summaries_fail(
        self, mock_akshare: dict[str, Any]
    ) -> None:
        """SSE+SzSE 都失败 + macro fallback 失败 + spot 失败 → None。"""
        mock_akshare["sse"].side_effect = RuntimeError("error")
        mock_akshare["szse"].side_effect = RuntimeError("error")
        mock_akshare["cap"].side_effect = RuntimeError("error")
        mock_akshare["spot"].side_effect = RuntimeError("error")
        src = AkshareValuationSource()
        assert src.get_a_share_total_market_cap(date(2026, 9, 17)) is None

    def test_falls_back_to_macro_china_market_cap(
        self, mock_akshare: dict[str, Any]
    ) -> None:
        """SSE+SzSE 失败 → macro_china_stock_market_cap fallback。"""
        mock_akshare["sse"].side_effect = RuntimeError("error")
        mock_akshare["szse"].side_effect = RuntimeError("error")
        mock_akshare["cap"].return_value = _make_df([
            {"数据日期": "2026年08月份", "市价总值-上海": 706966.48, "市价总值-深圳": 450602.19},
        ])
        src = AkshareValuationSource()
        cap = src.get_a_share_total_market_cap(date(2026, 9, 17))
        # (706966.48 + 450602.19) * 1e8
        expected = (Decimal("706966.48") + Decimal("450602.19")) * Decimal("100000000")
        assert cap == expected


class TestGetChinaGdp:
    """返回最新完整年度 GDP（第1-4季度行），单位：元（×1e8）。"""

    def test_returns_most_recent_annual_gdp_in_yuan(
        self, mock_akshare: dict[str, Any]
    ) -> None:
        """跳过 H1/Q1（累计季度），取最近的"第1-4季度"行。"""
        mock_akshare["gdp"].return_value = _make_df([
            {"季度": "2026年第1-2季度", "国内生产总值-绝对值": 695704.0},
            {"季度": "2026年第1季度", "国内生产总值-绝对值": 334192.9},
            {"季度": "2025年第1-4季度", "国内生产总值-绝对值": 1401879.2},
            {"季度": "2025年第1-3季度", "国内生产总值-绝对值": 1013967.9},
        ])
        src = AkshareValuationSource()
        gdp = src.get_china_gdp(date(2026, 9, 17))
        # 1401879 亿元 = 1401879 × 1e8 元（2025 全年，匹配"第1-4季度"）
        assert gdp == Decimal("1401879.2") * Decimal("100000000")

    def test_returns_latest_quarter_when_no_annual_data(
        self, mock_akshare: dict[str, Any]
    ) -> None:
        """没有"第1-4季度"行（如数据残缺）→ fallback 到最新季度累计。"""
        mock_akshare["gdp"].return_value = _make_df([
            {"季度": "2026年第1-2季度", "国内生产总值-绝对值": 695704.0},
            {"季度": "2026年第1季度", "国内生产总值-绝对值": 334192.9},
        ])
        src = AkshareValuationSource()
        gdp = src.get_china_gdp(date(2026, 9, 17))
        # 没"第1-4季度" → fallback 到最新季度累计 695704
        assert gdp == Decimal("695704.0") * Decimal("100000000")

    def test_returns_none_on_failure(self, mock_akshare: dict[str, Any]) -> None:
        mock_akshare["gdp"].side_effect = RuntimeError("error")
        src = AkshareValuationSource()
        assert src.get_china_gdp(date(2026, 9, 17)) is None


class TestGetHkAhPremium:
    """spec 098.2 — AH 溢价（恒生沪深港通 AH 溢价指数 HSAHP / 100）。"""

    def test_returns_ratio_from_hsahp(self, mock_akshare: dict[str, Any]) -> None:
        """HSAHP=124.01 → ratio=1.2401（值大=H便宜=低估）。"""
        mock_akshare["hsi"].return_value = _make_df(
            [
                {"date": date(2026, 9, 17), "open": 124.0, "high": 125.0,
                 "low": 123.5, "close": 124.5, "volume": 0, "amount": 0},
                {"date": date(2026, 9, 18), "open": 124.5, "high": 124.7,
                 "low": 123.6, "close": 124.01, "volume": 0, "amount": 0},
            ]
        )
        src = AkshareValuationSource()
        ah = src.get_hk_ah_premium(date(2026, 9, 18))
        assert ah == Decimal("1.2401")

    def test_returns_latest_when_target_after_last(
        self, mock_akshare: dict[str, Any]
    ) -> None:
        """目标日期在数据最后一行之后 → 返回最后一行（容错）。"""
        mock_akshare["hsi"].return_value = _make_df(
            [{"date": date(2026, 9, 18), "open": 0, "high": 0,
              "low": 0, "close": 120.0, "volume": 0, "amount": 0}]
        )
        src = AkshareValuationSource()
        ah = src.get_hk_ah_premium(date(2030, 1, 1))
        assert ah == Decimal("1.20")

    def test_returns_none_on_empty(self, mock_akshare: dict[str, Any]) -> None:
        mock_akshare["hsi"].return_value = _make_df([])
        src = AkshareValuationSource()
        assert src.get_hk_ah_premium(date(2026, 9, 18)) is None

    def test_returns_none_on_exception(self, mock_akshare: dict[str, Any]) -> None:
        mock_akshare["hsi"].side_effect = RuntimeError("network error")
        src = AkshareValuationSource()
        assert src.get_hk_ah_premium(date(2026, 9, 18)) is None


class TestGetHkTotalMarketCap:
    """spec 098.2 — 港股总市值（硬编码 fallback，HKEX 月度统计）。

    注：akshare 没有直接接口拉 HKEX 总市值，先用硬编码值。
    TODO: 接 HKEX 官网 https://www.hkex.com.hk 改用自动抓取。
    """

    def test_returns_fallback_value_for_recent(self) -> None:
        """2025-12 之后 → fallback 硬编码 38.5 万亿 HKD。"""
        src = AkshareValuationSource()
        mcap = src.get_hk_total_market_cap(date(2026, 9, 18))
        assert mcap == Decimal("38500000000000")

    def test_returns_fallback_for_any_date(self) -> None:
        """当前实现不依赖外部 API → 任何日期都返回 fallback。"""
        src = AkshareValuationSource()
        assert src.get_hk_total_market_cap(date(2024, 1, 1)) == Decimal("38500000000000")


class TestGetHkGdp:
    """spec 098.2 — 香港 GDP（ak.macro_china_hk_gbp，季度累加 = 年度）。"""

    def test_returns_annual_sum_from_4_quarters(
        self, mock_akshare: dict[str, Any]
    ) -> None:
        """4 个季度现值累加 → 年度 GDP（HKD）。"""
        mock_akshare["hk_gbp"].return_value = _make_df(
            [
                {"时间": "2026第3季度", "前值": 800000, "现值": 820000.0,
                 "发布日期": "2026-11-15"},
                {"时间": "2026第2季度", "前值": 780000, "现值": 790000.0,
                 "发布日期": "2026-08-15"},
                {"时间": "2026第1季度", "前值": 760000, "现值": 770000.0,
                 "发布日期": "2026-05-15"},
                {"时间": "2025第4季度", "前值": 850000, "现值": 860000.0,
                 "发布日期": "2026-02-15"},
                {"时间": "2025第3季度", "前值": 830000, "现值": 840000.0,
                 "发布日期": "2025-11-15"},
                {"时间": "2025第2季度", "前值": 810000, "现值": 820000.0,
                 "发布日期": "2025-08-15"},
                {"时间": "2025第1季度", "前值": 790000, "现值": 800000.0,
                 "发布日期": "2025-05-15"},
            ]
        )
        src = AkshareValuationSource()
        gdp = src.get_hk_gdp(date(2026, 9, 18))
        # 2025 年 4 季度累加：860000+840000+820000+800000 = 3,320,000（百万 HKD）
        # 转 HKD：× 1,000,000 = 3,320,000,000,000
        assert gdp == Decimal("3320000000000")

    def test_returns_none_on_empty(self, mock_akshare: dict[str, Any]) -> None:
        mock_akshare["hk_gbp"].return_value = _make_df([])
        src = AkshareValuationSource()
        assert src.get_hk_gdp(date(2026, 9, 18)) is None

    def test_returns_none_on_failure(self, mock_akshare: dict[str, Any]) -> None:
        mock_akshare["hk_gbp"].side_effect = RuntimeError("error")
        src = AkshareValuationSource()
        assert src.get_hk_gdp(date(2026, 9, 18)) is None


class TestResilience:
    """akshare 失败时所有方法都应静默返回 None / []，不抛异常。"""

    def test_all_methods_handle_akshare_not_installed(self) -> None:
        """如果 akshare 没装，所有方法都应优雅降级。"""
        with patch.dict("sys.modules", {"akshare": None}):
            src = AkshareValuationSource()
            assert src.get_pe_ttm(date(2026, 9, 17)) is None
            assert src.get_pe_history() == []
            assert src.get_dividend_yield(date(2026, 9, 17)) is None
            assert src.get_10y_treasury_yield(date(2026, 9, 17)) is None
            assert src.get_a_share_total_market_cap(date(2026, 9, 17)) is None
            assert src.get_china_gdp(date(2026, 9, 17)) is None
            assert src.get_hk_ah_premium(date(2026, 9, 17)) is None
            # HK 总市值和 GDP fallback 不依赖 akshare → 不在 not-installed 范围
            assert src.get_hk_total_market_cap(date(2026, 9, 17)) is not None
            assert src.get_hk_gdp(date(2026, 9, 17)) is None  # akshare required
