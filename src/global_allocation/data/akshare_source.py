"""akshare 数据源实现（A 股 / 港股）。"""

from __future__ import annotations

from datetime import date

import pandas as pd

from global_allocation.data.base import Fetcher
from global_allocation.models import Asset, DataSource


def _to_akshare_symbol(asset: Asset) -> str:
    """把 Asset 转成 akshare 接受的 6 位代码（不带后缀）。

    例：
      - '510300.SH' -> '510300'
      - '000300.SZ' -> '000300'
      - '00700.HK'  -> '00700'    （港股 ETF / 股票）
    """
    return asset.symbol.split(".")[0]


class AkshareFetcher(Fetcher):
    """akshare 数据源（A 股场内基金 / 股票 / 港股）。"""

    def fetch(self, asset: Asset, start: date, end: date) -> pd.DataFrame:
        if asset.data_source != DataSource.AKSHARE:
            raise ValueError(
                f"AkshareFetcher 不支持 source={asset.data_source}"
            )

        # 延迟导入（akshare 启动慢）
        import akshare as ak

        symbol = _to_akshare_symbol(asset)

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                # 用 stock_zh_a_hist 支持 A 股股票和场内基金（统一接口）
                # period='daily', adjust='qfq' 前复权
                df = ak.stock_zh_a_hist(
                    symbol=symbol,
                    period="daily",
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust="qfq",
                )
                if df.empty:
                    raise RuntimeError(
                        f"akshare 返回空数据：{asset.symbol} "
                        f"({symbol}) {start} ~ {end}"
                    )

                # akshare 列名：日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, ...
                df = df.rename(
                    columns={
                        "日期": "Date",
                        "开盘": "Open",
                        "收盘": "Close",
                        "最高": "High",
                        "最低": "Low",
                        "成交量": "Volume",
                    }
                )

                df.index = pd.to_datetime(df["Date"])
                df = df.drop(columns=["Date"])
                df = df.sort_index()

                # 确保必需列都在（adj_close 单独加）
                for col in ("Open", "High", "Low", "Close", "Volume"):
                    if col not in df.columns:
                        raise RuntimeError(
                            f"akshare 返回缺列 {col}：{asset.symbol}"
                        )
                # akshare 没有独立的 Adj Close，前复权后 Close 就是 Adj Close
                df["Adj Close"] = df["Close"]

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
            f"akshare 拉取失败（已重试 3 次）：{asset.symbol} {start} ~ {end}：{last_error}"
        )


__all__ = ["AkshareFetcher"]
