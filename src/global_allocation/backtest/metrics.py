"""性能指标计算。

参照 specs/060-performance-metrics.md。
"""

from __future__ import annotations

import math
from decimal import Decimal

import pandas as pd

from global_allocation.models import PerformanceMetrics


def compute_metrics(
    equity_curve: pd.DataFrame,
    risk_free_rate: Decimal = Decimal("0.02"),
    trading_days_per_year: int = 252,
) -> PerformanceMetrics:
    """从 equity curve 计算标准业绩指标。

    Args:
        equity_curve: DataFrame，含 'nav' 列（一个元素的 Series 即可），
                      index 必须是 DatetimeIndex。
        risk_free_rate: 年化无风险利率（默认 0.02 = 2%）。
        trading_days_per_year: 年化用交易日数（默认 252）。

    Returns:
        PerformanceMetrics 对象，所有 Decimal 字段都保留 6 位小数。

    算法（全部基于 daily NAV 的 pct_change）：
        - CAGR: (NAV_end / NAV_start) ** (252 / trading_days) - 1
        - total_return: NAV_end / NAV_start - 1
        - annual_return: total_return / years
        - volatility: daily_ret.std() * sqrt(252)
        - sharpe: (daily_ret.mean() - rf/252) / daily_ret.std() * sqrt(252)
        - max_drawdown: (NAV - cummax(NAV)) / cummax(NAV) 的最小值（负数）
        - correlation: 列之间（assets）的日收益相关矩阵
        - best_day / worst_day: 单日最大/最小收益
        - win_rate: 正收益天数占比

    Edge cases:
        - equity_curve < 2 行 → 大多数指标 NaN/0
        - std = 0 → sharpe = inf
        - 列缺失 → 抛错
    """
    if equity_curve.empty:
        raise ValueError("equity_curve 不能为空")
    if "nav" not in equity_curve.columns:
        raise ValueError("equity_curve 必须包含 'nav' 列")

    nav: pd.Series[float] = equity_curve["nav"].astype(float)
    n_days = len(nav)

    # 总收益
    nav_start = float(nav.iloc[0])
    nav_end = float(nav.iloc[-1])
    if nav_start <= 0:
        raise ValueError(f"NAV start 必须 > 0，实际 {nav_start}")

    total_return = nav_end / nav_start - 1.0

    # CAGR（years 是日历年的近似 = trading_days / 252）
    years = n_days / trading_days_per_year
    cagr = (nav_end / nav_start) ** (1.0 / years) - 1.0 if years > 0 else 0.0

    # 日收益
    daily_ret = nav.pct_change().dropna()
    n_ret_days = len(daily_ret)

    if n_ret_days == 0:
        # 不足 2 个交易日
        return PerformanceMetrics(
            cagr=Decimal("0"),
            sharpe=Decimal("0"),
            max_drawdown=Decimal("0"),
            volatility=Decimal("0"),
            total_return=Decimal(str(round(total_return, 6))),
            annual_return=Decimal("0"),
            correlation=pd.DataFrame(),
            best_day=Decimal("0"),
            worst_day=Decimal("0"),
            win_rate=Decimal("0"),
        )

    # 波动率 / Sharpe
    daily_std = float(daily_ret.std())
    volatility = daily_std * math.sqrt(trading_days_per_year)
    if daily_std > 0:
        rf_daily = float(risk_free_rate) / trading_days_per_year
        sharpe = (
            (float(daily_ret.mean()) - rf_daily) / daily_std * math.sqrt(trading_days_per_year)
        )
    else:
        # std = 0 → sharpe 视为 +inf（spec 060 边界 case 1）
        sharpe = math.inf

    # 最大回撤
    peak = nav.cummax()
    drawdown = (nav - peak) / peak
    max_dd = float(drawdown.min())  # 永远 ≤ 0

    # best/worst day
    best_day = float(daily_ret.max())
    worst_day = float(daily_ret.min())

    # win rate
    win_rate = (daily_ret > 0).sum() / n_ret_days

    # 年化收益（线性，不复利）
    annual_return = total_return / years if years > 0 else 0.0

    # 相关矩阵（基于 asset prices 的日收益，而不是 NAV）
    # 这里需要 prices 数据——但 equity_curve 里只有 nav
    # 所以从 equity_curve 的其他列取（如果有）
    asset_cols = [c for c in equity_curve.columns if c != "nav"]
    if asset_cols:
        correlation = equity_curve[asset_cols].pct_change().dropna().corr()
    else:
        # 没有 asset 价格列，返回空的 1x1（带 nav）
        correlation = pd.DataFrame({"nav": [1.0]}, index=["nav"])

    return PerformanceMetrics(
        cagr=Decimal(str(round(cagr, 6))),
        sharpe=Decimal(str(round(sharpe, 6))),
        max_drawdown=Decimal(str(round(max_dd, 6))),
        volatility=Decimal(str(round(volatility, 6))),
        total_return=Decimal(str(round(total_return, 6))),
        annual_return=Decimal(str(round(annual_return, 6))),
        correlation=correlation,
        best_day=Decimal(str(round(best_day, 6))),
        worst_day=Decimal(str(round(worst_day, 6))),
        win_rate=Decimal(str(round(win_rate, 6))),
    )


__all__ = ["compute_metrics"]
