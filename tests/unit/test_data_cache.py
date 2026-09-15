"""测试 src/global_allocation/data/cache.py。

参照 specs/040-data-fetch.md（缓存层）。
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from global_allocation.data.cache import Cache


@pytest.fixture
def cache(tmp_path) -> Cache:
    db_path = tmp_path / "test_cache.db"
    return Cache(db_path)


def _sample_df() -> pd.DataFrame:
    """构造一个最小的 OHLCV DataFrame 用于测试。"""
    idx = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    return pd.DataFrame(
        {
            "Open": [Decimal("100"), Decimal("101"), Decimal("102")],
            "High": [Decimal("101"), Decimal("102"), Decimal("103")],
            "Low": [Decimal("99"), Decimal("100"), Decimal("101")],
            "Close": [Decimal("100.5"), Decimal("101.5"), Decimal("102.5")],
            "Adj Close": [Decimal("100.5"), Decimal("101.5"), Decimal("102.5")],
            "Volume": [1000000, 1100000, 1200000],
        },
        index=idx,
    )


class TestCache:
    def test_db_file_created(self, tmp_path) -> None:
        # Arrange
        db_path = tmp_path / "new.db"
        # Act
        Cache(db_path)
        # Assert
        assert db_path.exists()

    def test_put_and_get_roundtrip(self, cache: Cache) -> None:
        # Arrange
        df = _sample_df()

        # Act
        cache.put(
            symbol="AAPL",
            source="yfinance",
            df=df,
        )
        result = cache.get(symbol="AAPL", source="yfinance")

        # Assert
        assert result is not None
        assert len(result) == 3
        assert "Adj Close" in result.columns

    def test_get_missing_returns_none(self, cache: Cache) -> None:
        result = cache.get(symbol="NOPE", source="yfinance")
        assert result is None

    def test_overwrite_replaces_data(self, cache: Cache) -> None:
        # Arrange
        cache.put("AAPL", "yfinance", _sample_df())
        smaller_df = _sample_df().iloc[:1]
        # Act
        cache.put("AAPL", "yfinance", smaller_df)
        result = cache.get("AAPL", "yfinance")

        # Assert
        assert result is not None
        assert len(result) == 1

    def test_different_sources_isolated(self, cache: Cache) -> None:
        # Arrange
        df = _sample_df()
        # Act
        cache.put("AAPL", "yfinance", df)
        cache.put("AAPL", "akshare", df.iloc[:2])
        # Assert
        yf = cache.get("AAPL", "yfinance")
        akshare = cache.get("AAPL", "akshare")
        assert yf is not None and len(yf) == 3
        assert akshare is not None and len(akshare) == 2

    def test_different_symbols_isolated(self, cache: Cache) -> None:
        # Arrange
        cache.put("AAPL", "yfinance", _sample_df())
        cache.put("MSFT", "yfinance", _sample_df().iloc[:1])
        # Assert
        aapl = cache.get("AAPL", "yfinance")
        msft = cache.get("MSFT", "yfinance")
        assert aapl is not None and len(aapl) == 3
        assert msft is not None and len(msft) == 1

    def test_clear_specific_symbol(self, cache: Cache) -> None:
        # Arrange
        cache.put("AAPL", "yfinance", _sample_df())
        cache.put("MSFT", "yfinance", _sample_df())
        # Act
        cache.clear(symbol="AAPL", source="yfinance")
        # Assert
        assert cache.get("AAPL", "yfinance") is None
        assert cache.get("MSFT", "yfinance") is not None

    def test_clear_all(self, cache: Cache) -> None:
        # Arrange
        cache.put("AAPL", "yfinance", _sample_df())
        cache.put("MSFT", "yfinance", _sample_df())
        # Act
        cache.clear_all()
        # Assert
        assert cache.get("AAPL", "yfinance") is None
        assert cache.get("MSFT", "yfinance") is None

    def test_list_symbols(self, cache: Cache) -> None:
        # Arrange
        cache.put("AAPL", "yfinance", _sample_df())
        cache.put("MSFT", "yfinance", _sample_df())
        cache.put("510300.SH", "akshare", _sample_df())
        # Act
        pairs = cache.list_symbols()
        # Assert
        symbols = {sym for sym, _src in pairs}
        assert {"AAPL", "MSFT", "510300.SH"}.issubset(symbols)
        # sources 也得对
        sources = {src for _sym, src in pairs}
        assert {"yfinance", "akshare"}.issubset(sources)

    def test_persists_across_instances(self, tmp_path) -> None:
        # Arrange
        db_path = tmp_path / "persist.db"
        c1 = Cache(db_path)
        c1.put("AAPL", "yfinance", _sample_df())

        # Act —— 新实例连同一文件
        c2 = Cache(db_path)

        # Assert
        result = c2.get("AAPL", "yfinance")
        assert result is not None
        assert len(result) == 3
