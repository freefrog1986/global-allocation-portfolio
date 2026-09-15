"""测试 src/global_allocation/data/akshare_source.py。

akshare 通过 mock ak.stock_zh_a_hist 来测试。
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from global_allocation.data.akshare_source import (
    AkshareFetcher,
    _to_akshare_symbol,
)
from global_allocation.models import Asset, AssetClass, Currency, DataSource, Region


def _make_asset(symbol: str = "510300.SH") -> Asset:
    return Asset(
        symbol=symbol,
        name="沪深300ETF",
        asset_class=AssetClass.EQUITY,
        region=Region.CN,
        currency=Currency.CNY,
        data_source=DataSource.AKSHARE,
    )


def _sample_ak_df() -> pd.DataFrame:
    """构造 akshare stock_zh_a_hist 返回的中文列名 DataFrame。"""
    return pd.DataFrame(
        {
            "日期": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "开盘": [3.5, 3.55, 3.6],
            "收盘": [3.52, 3.57, 3.62],
            "最高": [3.55, 3.6, 3.65],
            "最低": [3.48, 3.53, 3.58],
            "成交量": [10000000, 11000000, 12000000],
            "成交额": [35000000, 39000000, 43000000],
        }
    )


class TestToAkshareSymbol:
    def test_shanghai_suffix_stripped(self) -> None:
        asset = _make_asset("510300.SH")
        assert _to_akshare_symbol(asset) == "510300"

    def test_shenzhen_suffix_stripped(self) -> None:
        asset = _make_asset("000300.SZ")
        assert _to_akshare_symbol(asset) == "000300"

    def test_hk_suffix_stripped(self) -> None:
        asset = _make_asset("00700.HK")
        assert _to_akshare_symbol(asset) == "00700"

    def test_plain_symbol_unchanged(self) -> None:
        asset = _make_asset("510300")
        assert _to_akshare_symbol(asset) == "510300"


class TestAkshareFetcher:
    def test_wrong_source_raises(self) -> None:
        asset = Asset(
            symbol="AAPL",
            name="Apple",
            asset_class=AssetClass.EQUITY,
            region=Region.US,
            currency=Currency.USD,
            data_source=DataSource.YFINANCE,
        )
        fetcher = AkshareFetcher()
        with pytest.raises(ValueError, match="不支持"):
            fetcher.fetch(asset, date(2024, 1, 1), date(2024, 1, 31))

    @patch("akshare.stock_zh_a_hist")
    def test_fetch_returns_normalized_df(self, mock_hist: MagicMock) -> None:
        # Arrange
        mock_hist.return_value = _sample_ak_df()

        fetcher = AkshareFetcher()
        asset = _make_asset("510300.SH")

        # Act
        result = fetcher.fetch(asset, date(2024, 1, 1), date(2024, 1, 5))

        # Assert
        assert list(result.columns) == [
            "Open",
            "High",
            "Low",
            "Close",
            "Adj Close",
            "Volume",
        ]
        assert len(result) == 3
        # Adj Close 应该是 Close 的 copy
        pd.testing.assert_series_equal(
            result["Adj Close"], result["Close"], check_names=False
        )
        # index 应该是 DatetimeIndex 且按日期升序
        assert isinstance(result.index, pd.DatetimeIndex)
        assert result.index.is_monotonic_increasing

        # 验证传给 akshare 的参数
        mock_hist.assert_called_once()
        call_kwargs = mock_hist.call_args.kwargs
        assert call_kwargs["symbol"] == "510300"
        assert call_kwargs["period"] == "daily"
        assert call_kwargs["start_date"] == "20240101"
        assert call_kwargs["end_date"] == "20240105"
        assert call_kwargs["adjust"] == "qfq"

    @patch("akshare.stock_zh_a_hist")
    def test_fetch_empty_df_raises(self, mock_hist: MagicMock) -> None:
        mock_hist.return_value = pd.DataFrame()

        fetcher = AkshareFetcher()
        with pytest.raises(RuntimeError, match="空数据"):
            fetcher.fetch(_make_asset(), date(2024, 1, 1), date(2024, 1, 5))

    @patch("akshare.stock_zh_a_hist")
    def test_fetch_missing_column_raises(self, mock_hist: MagicMock) -> None:
        df = _sample_ak_df().drop(columns=["收盘"])
        mock_hist.return_value = df

        fetcher = AkshareFetcher()
        with pytest.raises(RuntimeError, match="缺列"):
            fetcher.fetch(_make_asset(), date(2024, 1, 1), date(2024, 1, 5))

    @patch("akshare.stock_zh_a_hist")
    @patch("time.sleep")
    def test_fetch_retries_on_failure(
        self, mock_sleep: MagicMock, mock_hist: MagicMock
    ) -> None:
        mock_hist.side_effect = [
            ConnectionError("net 1"),
            ConnectionError("net 2"),
            _sample_ak_df(),
        ]

        fetcher = AkshareFetcher()
        result = fetcher.fetch(_make_asset(), date(2024, 1, 1), date(2024, 1, 5))

        assert len(result) == 3
        assert mock_hist.call_count == 3

    @patch("akshare.stock_zh_a_hist")
    @patch("time.sleep")
    def test_fetch_raises_after_3_failures(
        self, mock_sleep: MagicMock, mock_hist: MagicMock
    ) -> None:
        mock_hist.side_effect = ConnectionError("always fails")

        fetcher = AkshareFetcher()
        with pytest.raises(RuntimeError, match="已重试 3 次"):
            fetcher.fetch(_make_asset(), date(2024, 1, 1), date(2024, 1, 5))
        assert mock_hist.call_count == 3

    @patch("akshare.stock_zh_a_hist")
    def test_hk_symbol_works(self, mock_hist: MagicMock) -> None:
        # Arrange —— 测试港股代码也会被剥后缀
        mock_hist.return_value = _sample_ak_df()

        fetcher = AkshareFetcher()
        asset = _make_asset("00700.HK")

        # Act
        fetcher.fetch(asset, date(2024, 1, 1), date(2024, 1, 5))

        # Assert
        call_kwargs = mock_hist.call_args.kwargs
        assert call_kwargs["symbol"] == "00700"
