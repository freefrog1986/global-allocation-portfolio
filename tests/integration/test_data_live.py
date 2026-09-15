"""集成测试：实际网络数据拉取。

参照 specs/040-data-fetch.md。

默认 skip。要跑：
    GAP_RUN_INTEGRATION=1 pytest -m integration tests/integration/
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from global_allocation.data.cache import Cache
from global_allocation.data.fetcher import fetch_many, fetch_ohlcv
from global_allocation.models import Asset, AssetClass, Currency, DataSource, Region

pytestmark = pytest.mark.integration


def _us_equity() -> Asset:
    return Asset(
        symbol="AAPL",
        name="Apple Inc.",
        asset_class=AssetClass.EQUITY,
        region=Region.US,
        currency=Currency.USD,
        data_source=DataSource.YFINANCE,
    )


def _cn_equity() -> Asset:
    return Asset(
        symbol="510300.SH",
        name="沪深300ETF",
        asset_class=AssetClass.EQUITY,
        region=Region.CN,
        currency=Currency.CNY,
        data_source=DataSource.AKSHARE,
    )


@pytest.fixture
def fresh_cache(tmp_path) -> Cache:
    """每次 integration test 都用干净的缓存。"""
    return Cache(tmp_path / "integration_cache.db")


class TestYFinanceLive:
    def test_aapl_one_month(self, fresh_cache: Cache) -> None:
        end = date.today()
        start = end - timedelta(days=31)
        df = fetch_ohlcv(
            _us_equity(), start, end, refresh=True, cache=fresh_cache
        )

        # 基本形状
        assert isinstance(df.index, pd.DatetimeIndex)
        assert list(df.columns) == [
            "Open",
            "High",
            "Low",
            "Close",
            "Adj Close",
            "Volume",
        ]
        assert len(df) > 15  # 一个月至少有 ~21 个交易日
        # 数值合理
        assert df["Close"].min() > 0
        assert df["Volume"].min() >= 0

    def test_cache_reuse(self, fresh_cache: Cache) -> None:
        # 第一次拉
        df1 = fetch_ohlcv(
            _us_equity(),
            date(2024, 1, 1),
            date(2024, 3, 31),
            refresh=True,
            cache=fresh_cache,
        )
        # 第二次（应该走缓存，瞬间返回）
        df2 = fetch_ohlcv(
            _us_equity(),
            date(2024, 1, 1),
            date(2024, 3, 31),
            cache=fresh_cache,
        )
        pd.testing.assert_frame_equal(df1, df2)


class TestAkshareLive:
    def test_hs300_one_month(self, fresh_cache: Cache) -> None:
        end = date.today()
        start = end - timedelta(days=31)
        df = fetch_ohlcv(
            _cn_equity(), start, end, refresh=True, cache=fresh_cache
        )

        assert isinstance(df.index, pd.DatetimeIndex)
        assert len(df) > 15
        assert df["Close"].min() > 0


class TestFetchManyLive:
    def test_yf_and_akshare_aligned(self, fresh_cache: Cache) -> None:
        end = date.today()
        start = end - timedelta(days=31)

        result = fetch_many(
            [_us_equity(), _cn_equity()],
            start,
            end,
            how="inner",
            refresh=True,
            cache=fresh_cache,
        )

        # columns 必须是 asset symbol
        assert list(result.columns) == ["AAPL", "510300.SH"]
        # inner join 取交集
        assert len(result) > 0
        # 没有 NaN（inner join）
        assert not result.isna().any().any()
