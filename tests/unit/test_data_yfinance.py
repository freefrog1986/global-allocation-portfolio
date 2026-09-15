"""测试 src/global_allocation/data/yfinance_source.py。

yfinance 通过 mock yfinance.Ticker 来测试（避免实际网络请求）。
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from global_allocation.data.yfinance_source import (
    YFinanceFetcher,
    _to_yfinance_symbol,
)
from global_allocation.models import Asset, AssetClass, Currency, DataSource, Region


def _make_asset(symbol: str = "AAPL") -> Asset:
    return Asset(
        symbol=symbol,
        name="Apple Inc.",
        asset_class=AssetClass.EQUITY,
        region=Region.US,
        currency=Currency.USD,
        data_source=DataSource.YFINANCE,
    )


def _sample_yf_df() -> pd.DataFrame:
    """构造一个 yfinance 风格返回的 DataFrame（带 tz-aware index）。"""
    idx = pd.to_datetime(
        ["2024-01-02", "2024-01-03", "2024-01-04"], utc=True
    )
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [101.0, 102.0, 103.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [100.5, 101.5, 102.5],
            "Adj Close": [100.5, 101.5, 102.5],
            "Volume": [1000000, 1100000, 1200000],
        },
        index=idx,
    )


class TestToYfinanceSymbol:
    def test_us_symbol_unchanged(self) -> None:
        asset = _make_asset("AAPL")
        assert _to_yfinance_symbol(asset) == "AAPL"

    def test_etf_unchanged(self) -> None:
        asset = _make_asset("VTI")
        assert _to_yfinance_symbol(asset) == "VTI"

    def test_shanghai_suffix_converted_to_ss(self) -> None:
        asset = _make_asset("510300.SH")
        assert _to_yfinance_symbol(asset) == "510300.SS"

    def test_shenzhen_suffix_unchanged(self) -> None:
        asset = _make_asset("000300.SZ")
        assert _to_yfinance_symbol(asset) == "000300.SZ"

    def test_hk_suffix_unchanged(self) -> None:
        asset = _make_asset("0700.HK")
        assert _to_yfinance_symbol(asset) == "0700.HK"


class TestYFinanceFetcher:
    def test_wrong_source_raises(self) -> None:
        asset = Asset(
            symbol="510300.SH",
            name="沪深300ETF",
            asset_class=AssetClass.EQUITY,
            region=Region.CN,
            currency=Currency.CNY,
            data_source=DataSource.AKSHARE,  # 不匹配
        )
        fetcher = YFinanceFetcher()
        with pytest.raises(ValueError, match="不支持"):
            fetcher.fetch(asset, date(2024, 1, 1), date(2024, 1, 31))

    @patch("yfinance.Ticker")
    def test_fetch_returns_normalized_df(self, mock_ticker_cls: MagicMock) -> None:
        # Arrange
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _sample_yf_df()
        mock_ticker_cls.return_value = mock_ticker

        fetcher = YFinanceFetcher()
        asset = _make_asset("AAPL")

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
        # yfinance end 是 exclusive，确认传入 end+1
        mock_ticker.history.assert_called_once()
        call_kwargs = mock_ticker.history.call_args.kwargs
        assert call_kwargs["end"] == "2024-01-06"  # end=2024-01-05 + 1 day
        assert call_kwargs["start"] == "2024-01-01"
        assert call_kwargs["auto_adjust"] is False

    @patch("yfinance.Ticker")
    def test_fetch_strips_timezone(self, mock_ticker_cls: MagicMock) -> None:
        # Arrange —— yfinance 返回带 tz 的 index
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _sample_yf_df()
        mock_ticker_cls.return_value = mock_ticker

        fetcher = YFinanceFetcher()

        # Act
        result = fetcher.fetch(_make_asset(), date(2024, 1, 1), date(2024, 1, 5))

        # Assert
        assert result.index.tz is None  # tz 已被 strip

    @patch("yfinance.Ticker")
    def test_fetch_empty_df_raises(self, mock_ticker_cls: MagicMock) -> None:
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        mock_ticker_cls.return_value = mock_ticker

        fetcher = YFinanceFetcher()
        with pytest.raises(RuntimeError, match="空数据"):
            fetcher.fetch(_make_asset(), date(2024, 1, 1), date(2024, 1, 5))

    @patch("yfinance.Ticker")
    def test_fetch_missing_column_raises(self, mock_ticker_cls: MagicMock) -> None:
        # Arrange —— 缺 Adj Close
        df = _sample_yf_df().drop(columns=["Adj Close"])
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = df
        mock_ticker_cls.return_value = mock_ticker

        fetcher = YFinanceFetcher()
        with pytest.raises(RuntimeError, match="缺列"):
            fetcher.fetch(_make_asset(), date(2024, 1, 1), date(2024, 1, 5))

    @patch("yfinance.Ticker")
    @patch("time.sleep")
    def test_fetch_retries_on_failure(
        self, mock_sleep: MagicMock, mock_ticker_cls: MagicMock
    ) -> None:
        # Arrange —— 前两次失败，第三次成功
        mock_ticker = MagicMock()
        mock_ticker.history.side_effect = [
            ConnectionError("net 1"),
            ConnectionError("net 2"),
            _sample_yf_df(),
        ]
        mock_ticker_cls.return_value = mock_ticker

        fetcher = YFinanceFetcher()

        # Act
        result = fetcher.fetch(_make_asset(), date(2024, 1, 1), date(2024, 1, 5))

        # Assert
        assert len(result) == 3
        assert mock_ticker.history.call_count == 3
        assert mock_sleep.call_count == 2  # 重试 2 次前 sleep

    @patch("yfinance.Ticker")
    @patch("time.sleep")
    def test_fetch_raises_after_3_failures(
        self, mock_sleep: MagicMock, mock_ticker_cls: MagicMock
    ) -> None:
        # Arrange
        mock_ticker = MagicMock()
        mock_ticker.history.side_effect = ConnectionError("always fails")
        mock_ticker_cls.return_value = mock_ticker

        fetcher = YFinanceFetcher()
        with pytest.raises(RuntimeError, match="已重试 3 次"):
            fetcher.fetch(_make_asset(), date(2024, 1, 1), date(2024, 1, 5))
        assert mock_ticker.history.call_count == 3

    @patch("yfinance.Ticker")
    def test_shanghai_symbol_uses_ss_ticker(
        self, mock_ticker_cls: MagicMock
    ) -> None:
        # Arrange
        asset = _make_asset("510300.SH")
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _sample_yf_df()
        mock_ticker_cls.return_value = mock_ticker

        fetcher = YFinanceFetcher()

        # Act
        fetcher.fetch(asset, date(2024, 1, 1), date(2024, 1, 5))

        # Assert
        mock_ticker_cls.assert_called_once_with("510300.SS")
