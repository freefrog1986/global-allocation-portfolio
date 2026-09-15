"""高层数据获取 API。

参照 specs/040-data-fetch.md。

提供：
  - fetch_ohlcv(asset, start, end, use_cache=True, refresh=False)
  - fetch_many(assets, start, end, how='inner')
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from global_allocation.data.akshare_source import AkshareFetcher
from global_allocation.data.base import Fetcher
from global_allocation.data.cache import Cache
from global_allocation.data.yfinance_source import YFinanceFetcher
from global_allocation.models import Asset, DataSource


# 单例 fetcher registry（按 data_source 选 fetcher）
_FETCHERS: dict[DataSource, Fetcher] = {
    DataSource.YFINANCE: YFinanceFetcher(),
    DataSource.AKSHARE: AkshareFetcher(),
}


def _get_cache_path() -> Path:
    """获取缓存数据库路径。XDG_DATA_HOME 兼容。"""
    import os

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".local" / "share"
    return base / "gap" / "cache.db"


# 全局 cache 单例（每个进程一个）
_default_cache: Cache | None = None


def get_default_cache() -> Cache:
    global _default_cache
    if _default_cache is None:
        _default_cache = Cache(_get_cache_path())
    return _default_cache


def _get_fetcher(asset: Asset) -> Fetcher:
    """根据 asset.data_source 选 fetcher。"""
    fetcher = _FETCHERS.get(asset.data_source)
    if fetcher is None:
        raise ValueError(f"不支持的数据源: {asset.data_source}")
    return fetcher


def fetch_ohlcv(
    asset: Asset,
    start: date,
    end: date,
    use_cache: bool = True,
    refresh: bool = False,
    cache: Cache | None = None,
) -> pd.DataFrame:
    """拉取单个资产的历史 OHLCV 数据。

    Args:
        asset: 资产
        start: 起始日期
        end: 结束日期
        use_cache: 是否走缓存（默认 True）
        refresh: 是否强制重新拉（覆盖缓存，默认 False）
        cache: 自定义缓存实例（默认用全局单例）

    Returns:
        DataFrame（index=DatetimeIndex, columns=OHLCV）

    Raises:
        RuntimeError: 数据源失败（已重试 3 次）
    """
    if end < start:
        raise ValueError(f"end ({end}) must be >= start ({start})")

    cache = cache or get_default_cache()

    # 缓存路径
    if use_cache and not refresh:
        cached = cache.get(asset.symbol, asset.data_source.value)
        if cached is not None:
            # 裁剪到请求区间
            idx_dates = cached.index.date  # type: ignore[attr-defined]
            mask = (idx_dates >= start) & (idx_dates <= end)
            filtered: pd.DataFrame = cached.loc[mask]
            if not filtered.empty:
                return filtered

    # 走网络
    fetcher = _get_fetcher(asset)
    df: pd.DataFrame = fetcher.fetch(asset, start, end)

    # 写缓存
    if use_cache:
        cache.put(asset.symbol, asset.data_source.value, df)

    return df


def fetch_many(
    assets: list[Asset],
    start: date,
    end: date,
    how: Literal["inner", "outer"] = "inner",
    use_cache: bool = True,
    refresh: bool = False,
    cache: Cache | None = None,
) -> pd.DataFrame:
    """批量拉取多个资产的 Adj Close 价，按日期对齐。

    Args:
        assets: 资产列表
        start, end: 日期区间
        how: 'inner' = 取交集（所有资产都有的日期）；'outer' = 并集，缺失填 NaN
        use_cache, refresh, cache: 同 fetch_ohlcv

    Returns:
        wide-format DataFrame，columns=asset.symbol，values=Adj Close
    """
    if not assets:
        raise ValueError("assets 不能为空")

    series_list: list[pd.Series[Any]] = []
    for asset in assets:
        df = fetch_ohlcv(asset, start, end, use_cache=use_cache, refresh=refresh, cache=cache)
        s: pd.Series[Any] = df["Adj Close"].rename(asset.symbol)
        series_list.append(s)

    if how == "inner":
        result = pd.concat(series_list, axis=1, join="inner")
    elif how == "outer":
        result = pd.concat(series_list, axis=1, join="outer", sort=True)
    else:
        raise ValueError(f"how 必须是 'inner' 或 'outer'，收到 {how}")

    return result


__all__ = [
    "Fetcher",
    "Cache",
    "YFinanceFetcher",
    "AkshareFetcher",
    "fetch_ohlcv",
    "fetch_many",
    "get_default_cache",
]
