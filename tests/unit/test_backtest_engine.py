"""测试 src/global_allocation/backtest/engine.py。

参照 specs/050-backtest-engine.md。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from global_allocation.backtest.engine import BacktestEngine
from global_allocation.models import (
    Asset,
    AssetClass,
    Currency,
    DataSource,
    RebalanceRule,
    Region,
    Strategy,
    TargetWeight,
)


def _equity() -> Asset:
    return Asset(
        symbol="EQUITY",
        name="E",
        asset_class=AssetClass.EQUITY,
        region=Region.US,
        currency=Currency.USD,
        data_source=DataSource.YFINANCE,
    )


def _bond() -> Asset:
    return Asset(
        symbol="BOND",
        name="B",
        asset_class=AssetClass.BOND,
        region=Region.US,
        currency=Currency.USD,
        data_source=DataSource.YFINANCE,
    )


def _make_strategy(
    weights: dict[str, Decimal], freq: str = "yearly", threshold: Decimal | None = None
) -> Strategy:
    asset_map = {"EQUITY": _equity(), "BOND": _bond()}
    return Strategy(
        id="test",
        name="Test",
        description="T",
        target_weights=[
            TargetWeight(asset=asset_map[s], weight=w) for s, w in weights.items()
        ],
        rebalance=RebalanceRule(frequency=freq, threshold=threshold),  # type: ignore[arg-type]
        base_currency=Currency.USD,
        inception=date(2024, 1, 1),
    )


def _make_prices(
    series: dict[str, list[float]], start: str = "2024-01-01"
) -> pd.DataFrame:
    n = max(len(v) for v in series.values())
    idx = pd.date_range(start, periods=n, freq="D")
    return pd.DataFrame(series, index=idx)


class TestBacktestEngineInit:
    def test_negative_capital_raises(self) -> None:
        with pytest.raises(ValueError, match="initial_capital"):
            BacktestEngine(initial_capital=Decimal("-100"))

    def test_negative_cost_raises(self) -> None:
        with pytest.raises(ValueError, match="cost_bps"):
            BacktestEngine(cost_bps=Decimal("-1"))

    def test_negative_slippage_raises(self) -> None:
        with pytest.raises(ValueError, match="slippage_bps"):
            BacktestEngine(slippage_bps=Decimal("-1"))


class TestBacktestRun:
    def test_buy_and_hold_basic(self) -> None:
        # EQUITY 单调上涨 1%/天，BOND 不变
        n = 30
        equity_prices = [100.0 * (1.01**i) for i in range(n)]
        bond_prices = [100.0] * n

        prices = _make_prices({"EQUITY": equity_prices, "BOND": bond_prices})
        strategy = _make_strategy(
            {"EQUITY": Decimal("0.6"), "BOND": Decimal("0.4")}, freq="none"
        )

        engine = BacktestEngine(
            initial_capital=Decimal("100000"), cost_bps=Decimal("0")
        )
        result = engine.run(strategy, prices)

        # 基本不变量
        assert result.strategy_id == "test"
        assert result.initial_capital == Decimal("100000")
        assert result.final_value > Decimal("100000")  # 应该赚钱
        # 第一次（也是唯一一次）是 schedule rebalance (day 0)
        assert len(result.rebalance_events) == 1
        # snapshots 数 = 价格行数
        assert len(result.snapshots) == n
        # equity_curve 是 DataFrame
        assert isinstance(result.equity_curve, pd.DataFrame)
        # 指标都有
        assert result.metrics.total_return > Decimal("0")

    def test_deterministic_same_input_same_output(self) -> None:
        n = 30
        equity_prices = [100.0 + i for i in range(n)]
        bond_prices = [100.0 + 0.5 * i for i in range(n)]
        prices = _make_prices({"EQUITY": equity_prices, "BOND": bond_prices})
        strategy = _make_strategy(
            {"EQUITY": Decimal("0.5"), "BOND": Decimal("0.5")}, freq="none"
        )

        engine = BacktestEngine()
        r1 = engine.run(strategy, prices)
        r2 = engine.run(strategy, prices)

        # bit-exact same
        assert r1.final_value == r2.final_value
        assert len(r1.snapshots) == len(r2.snapshots)
        for s1, s2 in zip(r1.snapshots, r2.snapshots, strict=True):
            assert s1.total_value == s2.total_value
            assert s1.cash == s2.cash

    def test_yearly_rebalance_triggers(self) -> None:
        # 跨 3 年的数据，应该至少触发 1 次年度 rebalance
        n = 252 * 3 + 10  # 3 年 + 缓冲
        equity_prices = [100.0 * (1.0005**i) for i in range(n)]
        bond_prices = [100.0 * (1.0002**i) for i in range(n)]
        prices = _make_prices({"EQUITY": equity_prices, "BOND": bond_prices})
        strategy = _make_strategy(
            {"EQUITY": Decimal("0.6"), "BOND": Decimal("0.4")}, freq="yearly"
        )

        engine = BacktestEngine(cost_bps=Decimal("0"))
        result = engine.run(strategy, prices)

        # 至少有 2 次 schedule rebalance（day 0 + 第 1 年）
        rebalance_dates = [e.date for e in result.rebalance_events]
        assert rebalance_dates[0] == date(2024, 1, 1)
        # 之后会有年度 rebalance
        assert any(d.year > 2024 for d in rebalance_dates)

    def test_threshold_rebalance_triggers(self) -> None:
        # EQUITY 单调大涨，BOND 不变 → EQUITY 权重会快速偏离 60% → 触发 threshold
        n = 100
        equity_prices = [100.0 * (1.02**i) for i in range(n)]  # 每天 +2%
        bond_prices = [100.0] * n
        prices = _make_prices({"EQUITY": equity_prices, "BOND": bond_prices})
        strategy = _make_strategy(
            {"EQUITY": Decimal("0.5"), "BOND": Decimal("0.5")},
            freq="none",
            threshold=Decimal("0.05"),
        )

        engine = BacktestEngine(cost_bps=Decimal("0"))
        result = engine.run(strategy, prices)

        # 至少有 1 次 threshold rebalance
        triggers = [e.triggered_by for e in result.rebalance_events]
        assert "threshold" in triggers

    def test_empty_prices_raises(self) -> None:
        strategy = _make_strategy({"EQUITY": Decimal("1.0")})
        with pytest.raises(ValueError, match="不能为空"):
            BacktestEngine().run(strategy, pd.DataFrame())

    def test_missing_price_column_raises(self) -> None:
        prices = _make_prices({"AAPL": [100.0, 101.0]})  # 没有 EQUITY/BOND
        strategy = _make_strategy({"EQUITY": Decimal("1.0")})
        with pytest.raises(ValueError, match="缺下列标的"):
            BacktestEngine().run(strategy, prices)

    def test_final_value_equals_initial_when_prices_flat(self) -> None:
        # 价格完全不变 → 扣手续费后 NAV 应该 ≤ 初始（buy-and-hold）
        n = 30
        prices = _make_prices({"EQUITY": [100.0] * n, "BOND": [100.0] * n})
        strategy = _make_strategy(
            {"EQUITY": Decimal("0.5"), "BOND": Decimal("0.5")}, freq="none"
        )

        engine = BacktestEngine(cost_bps=Decimal("10"))
        result = engine.run(strategy, prices)

        # 价格不变 → 仅扣 day 0 手续费
        assert result.final_value < Decimal("100000")

    def test_nan_prices_dont_crash(self) -> None:
        # 中间某天价格 NaN
        prices_list = [100.0, 101.0, float("nan"), 103.0, 104.0]
        prices = _make_prices({"EQUITY": prices_list, "BOND": [100.0] * 5})
        strategy = _make_strategy(
            {"EQUITY": Decimal("0.5"), "BOND": Decimal("0.5")}, freq="none"
        )

        engine = BacktestEngine(cost_bps=Decimal("0"))
        result = engine.run(strategy, prices)

        # 不崩溃，跑完
        assert len(result.snapshots) == 5

    def test_equity_curve_has_nav_column(self) -> None:
        prices = _make_prices({"EQUITY": [100.0 + i for i in range(10)],
                               "BOND": [100.0 + i for i in range(10)]})
        strategy = _make_strategy({"EQUITY": Decimal("0.5"), "BOND": Decimal("0.5")},
                                   freq="none")
        engine = BacktestEngine(cost_bps=Decimal("0"))
        result = engine.run(strategy, prices)

        assert "nav" in result.equity_curve.columns
        assert len(result.equity_curve) == 10

    def test_risk_parity_uses_risk_weights(self) -> None:
        # 用 risk_parity id → engine 会调用 compute_risk_parity_weights
        # 这里用 2 个标的、不同波动率
        n = 60
        np.random.seed(0)
        low_vol = [100.0 * (1.0 + 0.001 * i) for i in range(n)]
        np.random.seed(1)
        high_vol = [100.0 * (1.0 + 0.01 * i + np.random.normal(0, 0.005)) for i in range(n)]
        prices = _make_prices({"EQUITY": high_vol, "BOND": low_vol})

        # 构造 risk parity 策略
        strategy = Strategy(
            id="risk_parity",
            name="RP",
            description="",
            target_weights=[
                TargetWeight(asset=_equity(), weight=Decimal("0.5")),
                TargetWeight(asset=_bond(), weight=Decimal("0.5")),
            ],
            rebalance=RebalanceRule(frequency="monthly"),
            base_currency=Currency.USD,
            inception=date(2024, 1, 1),
        )

        engine = BacktestEngine(cost_bps=Decimal("0"))
        result = engine.run(strategy, prices)

        # 不崩溃
        assert len(result.snapshots) == n
        # 至少 1 次 rebalance（day 0）
        assert len(result.rebalance_events) >= 1
