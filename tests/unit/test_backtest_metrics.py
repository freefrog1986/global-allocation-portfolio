"""测试 src/global_allocation/backtest/metrics.py。

参照 specs/060-performance-metrics.md。
"""

from __future__ import annotations

import math
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from global_allocation.backtest.metrics import compute_metrics


def _make_equity_curve(nav_values: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=len(nav_values), freq="D")
    return pd.DataFrame({"nav": nav_values}, index=dates)


class TestComputeMetrics:
    def test_monotonic_up_positive_cagr(self) -> None:
        # 单调上涨 1% 每天 → 252 天后 = (1.01)^252 ≈ 12.07 倍
        n = 252
        nav = [100.0 * (1.01**i) for i in range(n + 1)]
        equity = _make_equity_curve(nav)

        m = compute_metrics(equity)
        # CAGR 应该 ≈ 0.01 / day * 252 = 年化约 1084%（纯几何等效）
        # 实际 (1.01)^252 - 1 ≈ 11.07
        assert m.cagr > Decimal("10")
        assert m.total_return > Decimal("11")

    def test_monotonic_down_negative_cagr(self) -> None:
        # 单调下跌 0.5% 每天
        n = 252
        nav = [100.0 * (0.995**i) for i in range(n + 1)]
        equity = _make_equity_curve(nav)

        m = compute_metrics(equity)
        assert m.cagr < Decimal("0")
        assert m.max_drawdown < Decimal("0")  # 必为负

    def test_max_drawdown_always_non_positive(self) -> None:
        # 任何 equity curve → max_drawdown ≤ 0
        np.random.seed(7)
        returns = np.random.normal(0.0005, 0.01, 200)
        nav = [100.0]
        for r in returns:
            nav.append(nav[-1] * (1.0 + r))
        equity = _make_equity_curve(nav)

        m = compute_metrics(equity)
        assert m.max_drawdown <= Decimal("0")

    def test_zero_volatility_sharpe_is_inf(self) -> None:
        # NAV 完全不变 → std = 0 → sharpe = inf
        equity = _make_equity_curve([100.0] * 30)

        m = compute_metrics(equity)
        # inf 在 Decimal 里转成字符串
        assert math.isinf(float(m.sharpe))
        assert float(m.sharpe) > 0

    def test_volatility_annualized(self) -> None:
        # 已知：daily std = 0.01 → 年化 ≈ 0.01 * sqrt(252) ≈ 0.1587
        np.random.seed(42)
        returns = np.random.normal(0, 0.01, 500)
        nav = [100.0]
        for r in returns:
            nav.append(nav[-1] * (1.0 + r))
        equity = _make_equity_curve(nav)

        m = compute_metrics(equity)
        expected = 0.01 * math.sqrt(252)
        assert abs(float(m.volatility) - expected) < 0.02

    def test_sharpe_manual_calculation(self) -> None:
        # 构造已知收益序列
        nav = [100.0, 101.0, 100.5, 102.0, 101.0, 103.0]  # 5 个收益
        equity = _make_equity_curve(nav)

        m = compute_metrics(
            equity, risk_free_rate=Decimal("0"), trading_days_per_year=252
        )

        rets = pd.Series([0.01, -0.00495, 0.01493, -0.0098, 0.0198])
        expected_sharpe = (
            rets.mean() / rets.std() * math.sqrt(252)
        )
        assert abs(float(m.sharpe) - expected_sharpe) < 0.5

    def test_correlation_diagonal_is_one(self) -> None:
        # 多资产 equity_curve
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        np.random.seed(0)
        data = {
            "A": 100 + np.cumsum(np.random.normal(0, 1, n)),
            "B": 100 + np.cumsum(np.random.normal(0, 1, n)),
            "nav": 100 + np.cumsum(np.random.normal(0, 1, n)),
        }
        equity = pd.DataFrame(data, index=dates)

        m = compute_metrics(equity)
        # 相关矩阵存在
        assert isinstance(m.correlation, pd.DataFrame)
        # 对角线 = 1.0
        for sym in m.correlation.columns:
            assert abs(m.correlation.loc[sym, sym] - 1.0) < 1e-9

    def test_win_rate_in_range(self) -> None:
        np.random.seed(3)
        n = 100
        returns = np.random.normal(0.001, 0.01, n)
        nav = [100.0]
        for r in returns:
            nav.append(nav[-1] * (1.0 + r))
        equity = _make_equity_curve(nav)

        m = compute_metrics(equity)
        assert Decimal("0") <= m.win_rate <= Decimal("1")

    def test_best_worst_day(self) -> None:
        # 第一天 +5%，最后一天 -3%，其他不变
        nav = [100.0, 105.0, 105.0, 105.0, 101.85]
        equity = _make_equity_curve(nav)

        m = compute_metrics(equity)
        assert abs(float(m.best_day) - 0.05) < 1e-6
        assert abs(float(m.worst_day) - (-0.03)) < 1e-6

    def test_empty_equity_raises(self) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            compute_metrics(pd.DataFrame(columns=["nav"]))

    def test_missing_nav_column_raises(self) -> None:
        equity = pd.DataFrame({"foo": [1, 2, 3]})
        with pytest.raises(ValueError, match="nav"):
            compute_metrics(equity)

    def test_short_equity_returns_zero_metrics(self) -> None:
        # 1 个数据点 → 不足算收益
        equity = _make_equity_curve([100.0])
        m = compute_metrics(equity)
        # 不报错，指标都是 0
        assert m.cagr == Decimal("0")
        assert m.total_return == Decimal("0")
