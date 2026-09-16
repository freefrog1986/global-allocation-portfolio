"""实盘账本价格源抽象。

接口：get_price(code, on) -> Decimal | None

实现：
  - AkshareFundPriceSource：akshare 公开基金净值（生产用）
  - ManualPriceSource：CLI 手动指定
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol


class PriceSource(Protocol):
    """价格源接口。"""

    def get_price(self, code: str, on: date) -> Decimal | None:
        ...


class ManualPriceSource:
    """固定价格（CLI 传入）。"""

    def __init__(self, prices: dict[str, Decimal]) -> None:
        self._prices = prices

    def get_price(self, code: str, on: date) -> Decimal | None:
        return self._prices.get(code)


class AkshareFundPriceSource:
    """akshare 公开基金净值（生产用）。"""

    def get_price(self, code: str, on: date) -> Decimal | None:
        try:
            import akshare as ak
        except ImportError:
            return None

        try:
            df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            if df is None or df.empty:
                return None
            # 找 on 当天或之前最近一行
            import pandas as pd

            df["净值日期"] = pd.to_datetime(df["净值日期"]).dt.date
            df = df[df["净值日期"] <= on].sort_values("净值日期", ascending=False)
            if df.empty:
                return None
            return Decimal(str(df.iloc[0]["单位净值"]))
        except Exception:
            # 货币基金接口（fund_open_fund_info_em 会 ReferenceError）→ NAV=1.0
            return self._try_money_market_fallback(code, on)

    def _try_money_market_fallback(self, code: str, on: date) -> Decimal | None:
        """货币基金 NAV 恒为 1.0。"""
        try:
            import akshare as ak
        except ImportError:
            return None
        try:
            df = ak.fund_money_fund_daily_em()
            if df is not None and not df.empty and "基金代码" in df.columns:
                if (df["基金代码"] == code).any():
                    return Decimal("1.0000")
        except Exception:
            pass
        return None


__all__ = ["PriceSource", "ManualPriceSource", "AkshareFundPriceSource"]
