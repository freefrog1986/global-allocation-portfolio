"""yfinance 数据源实现。"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import yfinance

from global_allocation.data.base import Fetcher
from global_allocation.models import Asset, DataSource


def _to_yfinance_symbol(asset: Asset) -> str:
    """把我们的 Asset 转成 yfinance ticker。

    例：
      - 'AAPL'           -> 'AAPL'
      - 'VTI'            -> 'VTI'
      - '510300.SH'      -> '510300.SS'   （上交所）
      - '000300.SZ'      -> '000300.SZ'   （深交所）
      - '0700.HK'        -> '0700.HK'     （港股）
    """
    sym = asset.symbol
    if sym.endswith(".SH"):
        return sym[:-3] + ".SS"
    return sym


class YFinanceFetcher(Fetcher):
    """Yahoo Finance 数据源。"""

    def fetch(self, asset: Asset, start: date, end: date) -> pd.DataFrame:
        if asset.data_source != DataSource.YFINANCE:
            raise ValueError(
                f"YFinanceFetcher 不支持 source={asset.data_source}"
            )

        ticker = _to_yfinance_symbol(asset)
        # yfinance end 是 exclusive，加 1 天
        end_exclusive = end + timedelta(days=1)

        # 简单重试 3 次（指数退避）
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                t = yfinance.Ticker(ticker)
                df = t.history(
                    start=start.isoformat(),
                    end=end_exclusive.isoformat(),
                    auto_adjust=False,  # 我们要原始 Adj Close
                    actions=False,
                )
                if df.empty:
                    raise RuntimeError(
                        f"yfinance 返回空数据：{asset.symbol} "
                        f"({ticker}) {start} ~ {end}"
                    )
                # 统一列名（小写开头 → 大写开头）
                df = df.rename(
                    columns={
                        "Open": "Open",
                        "High": "High",
                        "Low": "Low",
                        "Close": "Close",
                        "Adj Close": "Adj Close",
                        "Volume": "Volume",
                    }
                )
                # 确保列都在
                for col in ("Open", "High", "Low", "Close", "Adj Close", "Volume"):
                    if col not in df.columns:
                        raise RuntimeError(
                            f"yfinance 返回缺列 {col}：{asset.symbol}"
                        )
                # 丢弃时区（spec 040 MVP 不处理时区对齐）
                if df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                result: pd.DataFrame = df[
                    ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
                ]
                return result
            except Exception as e:
                last_error = e
                if attempt < 2:
                    import time

                    time.sleep(0.5 * (2**attempt))
                else:
                    break

        raise RuntimeError(
            f"yfinance 拉取失败（已重试 3 次）：{asset.symbol} {start} ~ {end}：{last_error}"
        )


__all__ = ["YFinanceFetcher"]
