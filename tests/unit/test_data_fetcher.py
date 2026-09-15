"""测试 src/global_allocation/data/fetcher.py（高层 API）。

测试 fetch_ohlcv / fetch_many 的缓存路由、use_cache、refresh、inner/outer join 等。
fetcher 子类通过 mock 避免网络请求。
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from global_allocation.data.cache import Cache
from global_allocation.data.fetcher import (
    fetch_many,
    fetch_ohlcv,
    get_default_cache,
)
from global_allocation.models import Asset, AssetClass, Currency, DataSource, Region


def _make_yf_asset(symbol: str = "AAPL") -> Asset:
    return Asset(
        symbol=symbol,
        name=f"Test {symbol}",
        asset_class=AssetClass.EQUITY,
        region=Region.US,
        currency=Currency.USD,
        data_source=DataSource.YFINANCE,
    )


def _make_ak_asset(symbol: str = "510300.SH") -> Asset:
    return Asset(
        symbol=symbol,
        name=f"Test {symbol}",
        asset_class=AssetClass.EQUITY,
        region=Region.CN,
        currency=Currency.CNY,
        data_source=DataSource.AKSHARE,
    )


def _sample_ohlcv(start: str = "2024-01-02", n: int = 5) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="D")
    return pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(n)],
            "High": [101.0 + i for i in range(n)],
            "Low": [99.0 + i for i in range(n)],
            "Close": [100.5 + i for i in range(n)],
            "Adj Close": [100.5 + i for i in range(n)],
            "Volume": [1000000] * n,
        },
        index=idx,
    )


@pytest.fixture
def cache(tmp_path) -> Cache:
    return Cache(tmp_path / "cache.db")


@pytest.fixture
def patched_yf():
    """替换 _FETCHERS 里的 YFinanceFetcher 实例，模拟网络返回。"""
    from global_allocation.data import fetcher as fetcher_mod

    original = fetcher_mod._FETCHERS.copy()
    mock_instance = MagicMock()
    mock_instance.fetch.return_value = _sample_ohlcv()
    fetcher_mod._FETCHERS[DataSource.YFINANCE] = mock_instance
    try:
        yield mock_instance
    finally:
        fetcher_mod._FETCHERS.clear()
        fetcher_mod._FETCHERS.update(original)


@pytest.fixture
def patched_ak():
    """替换 _FETCHERS 里的 AkshareFetcher 实例，模拟网络返回。"""
    from global_allocation.data import fetcher as fetcher_mod

    original = fetcher_mod._FETCHERS.copy()
    mock_instance = MagicMock()
    mock_instance.fetch.return_value = _sample_ohlcv(start="2024-01-01", n=6)
    fetcher_mod._FETCHERS[DataSource.AKSHARE] = mock_instance
    try:
        yield mock_instance
    finally:
        fetcher_mod._FETCHERS.clear()
        fetcher_mod._FETCHERS.update(original)


class TestGetDefaultCache:
    def test_returns_cache_instance(self) -> None:
        cache = get_default_cache()
        assert isinstance(cache, Cache)

    def test_singleton_returns_same_instance(self) -> None:
        cache1 = get_default_cache()
        cache2 = get_default_cache()
        assert cache1 is cache2

    def test_uses_xdg_data_home(self, monkeypatch, tmp_path) -> None:
        # Reset module-level singleton
        import global_allocation.data.fetcher as fetcher_mod

        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        fetcher_mod._default_cache = None
        cache = get_default_cache()
        assert str(tmp_path / "xdg") in str(cache.db_path)


class TestFetchOhlcv:
    def test_end_before_start_raises(self, cache: Cache) -> None:
        with pytest.raises(ValueError, match="must be >="):
            fetch_ohlcv(
                _make_yf_asset(),
                start=date(2024, 6, 1),
                end=date(2024, 1, 1),
                cache=cache,
            )

    def test_first_call_hits_network(
        self, cache: Cache, patched_yf: MagicMock
    ) -> None:
        # Act
        result = fetch_ohlcv(
            _make_yf_asset("AAPL"),
            date(2024, 1, 1),
            date(2024, 1, 31),
            cache=cache,
        )

        # Assert
        assert len(result) == 5
        patched_yf.fetch.assert_called_once()

    def test_second_call_hits_cache(
        self, cache: Cache, patched_yf: MagicMock
    ) -> None:
        # Arrange —— 先调一次填充缓存
        fetch_ohlcv(
            _make_yf_asset("AAPL"),
            date(2024, 1, 1),
            date(2024, 1, 31),
            cache=cache,
        )

        # Act —— 再调一次
        fetch_ohlcv(
            _make_yf_asset("AAPL"),
            date(2024, 1, 1),
            date(2024, 1, 31),
            cache=cache,
        )

        # Assert —— 网络只调用一次
        assert patched_yf.fetch.call_count == 1

    def test_use_cache_false_bypasses_cache(
        self, cache: Cache, patched_yf: MagicMock
    ) -> None:
        # Arrange —— 先填充缓存
        fetch_ohlcv(
            _make_yf_asset("AAPL"),
            date(2024, 1, 1),
            date(2024, 1, 31),
            cache=cache,
        )

        # Act —— use_cache=False
        fetch_ohlcv(
            _make_yf_asset("AAPL"),
            date(2024, 1, 1),
            date(2024, 1, 31),
            use_cache=False,
            cache=cache,
        )

        # Assert —— 两次都走网络
        assert patched_yf.fetch.call_count == 2

    def test_refresh_forces_refetch(
        self, cache: Cache, patched_yf: MagicMock
    ) -> None:
        # Arrange
        fetch_ohlcv(
            _make_yf_asset("AAPL"),
            date(2024, 1, 1),
            date(2024, 1, 31),
            cache=cache,
        )

        # Act —— refresh=True
        fetch_ohlcv(
            _make_yf_asset("AAPL"),
            date(2024, 1, 1),
            date(2024, 1, 31),
            refresh=True,
            cache=cache,
        )

        # Assert —— 强制重新拉
        assert patched_yf.fetch.call_count == 2

    def test_cached_data_is_clipped_to_range(
        self, cache: Cache, patched_yf: MagicMock
    ) -> None:
        # Arrange —— 缓存里有 5 天的数据（2024-01-02 ~ 2024-01-06）
        fetch_ohlcv(
            _make_yf_asset("AAPL"),
            date(2024, 1, 1),
            date(2024, 1, 10),
            cache=cache,
        )

        # Act —— 请求一个更窄的子区间
        result = fetch_ohlcv(
            _make_yf_asset("AAPL"),
            date(2024, 1, 3),
            date(2024, 1, 4),
            cache=cache,
        )

        # Assert —— 只返回这两天
        assert len(result) == 2
        assert result.index[0].date() == date(2024, 1, 3)
        assert result.index[-1].date() == date(2024, 1, 4)
        # 不走网络
        patched_yf.fetch.assert_called_once()

    def test_unsupported_source_raises(self, cache: Cache) -> None:
        # 构造一个假数据源
        from global_allocation.data.fetcher import _FETCHERS

        original = _FETCHERS.copy()
        # 移除 YFINANCE 触发 error 分支
        _FETCHERS.pop(DataSource.YFINANCE, None)
        try:
            with pytest.raises(ValueError, match="不支持"):
                fetch_ohlcv(
                    _make_yf_asset(),
                    date(2024, 1, 1),
                    date(2024, 1, 5),
                    cache=cache,
                )
        finally:
            _FETCHERS.update(original)

    def test_different_assets_use_different_sources(
        self, cache: Cache, patched_yf: MagicMock, patched_ak: MagicMock
    ) -> None:
        # Act
        fetch_ohlcv(
            _make_yf_asset("AAPL"),
            date(2024, 1, 1),
            date(2024, 1, 5),
            cache=cache,
        )
        fetch_ohlcv(
            _make_ak_asset("510300.SH"),
            date(2024, 1, 1),
            date(2024, 1, 5),
            cache=cache,
        )

        # Assert
        patched_yf.fetch.assert_called_once()
        patched_ak.fetch.assert_called_once()
        # 缓存里应该都有
        assert cache.get("AAPL", "yfinance") is not None
        assert cache.get("510300.SH", "akshare") is not None


class TestFetchMany:
    def test_empty_assets_raises(self, cache: Cache) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            fetch_many([], date(2024, 1, 1), date(2024, 1, 31), cache=cache)

    def test_inner_join_default(
        self, cache: Cache, patched_yf: MagicMock, patched_ak: MagicMock
    ) -> None:
        # Arrange —— yfinance 返回 5 天（2024-01-02~06），akshare 返回 6 天（01-01~06）
        aapl = _make_yf_asset("AAPL")
        hs300 = _make_ak_asset("510300.SH")

        # Act
        result = fetch_many(
            [aapl, hs300],
            date(2024, 1, 1),
            date(2024, 1, 10),
            cache=cache,
        )

        # Assert
        assert list(result.columns) == ["AAPL", "510300.SH"]
        # inner join 应该是 5 天（aapl 的范围）
        assert len(result) == 5
        # index 是 DatetimeIndex
        assert isinstance(result.index, pd.DatetimeIndex)

    def test_outer_join(
        self, cache: Cache, patched_yf: MagicMock, patched_ak: MagicMock
    ) -> None:
        # Act
        result = fetch_many(
            [_make_yf_asset("AAPL"), _make_ak_asset("510300.SH")],
            date(2024, 1, 1),
            date(2024, 1, 10),
            how="outer",
            cache=cache,
        )

        # Assert —— outer join 取并集
        assert len(result) == 6  # akshare 的范围更大

    def test_invalid_how_raises(self, cache: Cache) -> None:
        with pytest.raises(ValueError, match="how 必须是"):
            fetch_many(
                [_make_yf_asset()],
                date(2024, 1, 1),
                date(2024, 1, 5),
                how="left",  # type: ignore[arg-type]
                cache=cache,
            )

    def test_uses_adj_close_column(
        self, cache: Cache, patched_yf: MagicMock
    ) -> None:
        # Arrange —— 确保 fetch_many 取的是 Adj Close 而不是 Close
        result = fetch_many(
            [_make_yf_asset("AAPL")],
            date(2024, 1, 1),
            date(2024, 1, 5),
            cache=cache,
        )

        # sample data 中 Adj Close = [100.5, 101.5, 102.5, 103.5, 104.5]
        assert list(result["AAPL"]) == [100.5, 101.5, 102.5, 103.5, 104.5]
