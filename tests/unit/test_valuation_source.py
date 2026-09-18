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
         patch("akshare.bond_china_yield") as mock_yield, \
         patch("akshare.stock_zh_a_spot_em") as mock_spot, \
         patch("akshare.stock_sse_summary") as mock_sse, \
         patch("akshare.stock_szse_summary") as mock_szse, \
         patch("akshare.macro_china_stock_market_cap") as mock_cap, \
         patch("akshare.macro_china_gdp") as mock_gdp:
        yield {
            "csindex": mock_csindex,
            "ttm": mock_ttm,
            "yield": mock_yield,
            "spot": mock_spot,
            "sse": mock_sse,
            "szse": mock_szse,
            "cap": mock_cap,
            "gdp": mock_gdp,
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
    def test_filters_to_gov_curve(self, mock_akshare: dict[str, Any]) -> None:
        """只取"中债国债收益率曲线"，跳过信用债 / 商业银行债曲线。"""
        mock_akshare["yield"].return_value = _make_df([
            {"曲线名称": "中债中短期票据收益率曲线(AAA)", "日期": "2026-09-17", "10年": 3.5},
            {"曲线名称": "中债国债收益率曲线", "日期": "2026-09-17", "10年": 2.85},
            {"曲线名称": "中债商业银行普通债收益率曲线(AAA)", "日期": "2026-09-17", "10年": 3.2},
        ])
        src = AkshareValuationSource()
        y = src.get_10y_treasury_yield(date(2026, 9, 17))
        # 应该取国债 2.85 → 0.0285
        assert y == Decimal("0.0285")

    def test_returns_none_when_no_gov_curve(
        self, mock_akshare: dict[str, Any]
    ) -> None:
        mock_akshare["yield"].return_value = _make_df([
            {"曲线名称": "中债中短期票据收益率曲线(AAA)", "日期": "2026-09-17", "10年": 3.5},
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
