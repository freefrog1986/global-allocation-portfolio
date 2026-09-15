"""数据获取抽象基类。

参照 specs/040-data-fetch.md。

每个数据源（yfinance, akshare）实现一个 Fetcher 子类，
提供 fetch(asset, start, end) -> DataFrame。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd

from global_allocation.models import Asset


class Fetcher(ABC):
    """数据源抽象接口。"""

    @abstractmethod
    def fetch(self, asset: Asset, start: date, end: date) -> pd.DataFrame:
        """拉取单个标的的历史 OHLCV 数据。

        Args:
            asset: 资产对象（含 symbol 和 source）
            start: 起始日期（含）
            end: 结束日期（含）

        Returns:
            DataFrame，index=DatetimeIndex，columns 至少含
            ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume']
            数据是按日期升序。

        Raises:
            RuntimeError: 数据源不可用 / 网络错误（已重试后）
            ValueError: 资产不被该 fetcher 支持
        """


__all__ = ["Fetcher"]
