"""测试 src/global_allocation/backtest/risk_parity.py。"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from global_allocation.backtest.risk_parity import compute_risk_parity_weights


def _make_prices(returns_dict: dict[str, list[float]]) -> pd.DataFrame:
    """从给定日收益构造价格序列（起价 100）。"""
    out: dict[str, list[float]] = {}
    for sym, rets in returns_dict.items():
        prices = [100.0]
        for r in rets:
            prices.append(prices[-1] * (1.0 + r))
        out[sym] = prices[1:]
    return pd.DataFrame(out)


class TestRiskParityWeights:
    def test_equal_volatility_gives_equal_weights(self) -> None:
        # 所有资产波动率相同 → 权重应该近似相等
        np.random.seed(42)
        rets = {
            "A": list(np.random.normal(0.001, 0.01, 100)),
            "B": list(np.random.normal(0.001, 0.01, 100)),
            "C": list(np.random.normal(0.001, 0.01, 100)),
        }
        prices = _make_prices(rets)
        w = compute_risk_parity_weights(prices)

        assert len(w) == 3
        # 每个 ≈ 1/3
        for sym in ["A", "B", "C"]:
            assert abs(w[sym] - Decimal("0.333333")) < Decimal("0.05")

    def test_higher_volatility_lower_weight(self) -> None:
        # A 低波动，B 高波动 → A 应该权重大
        rets = {
            "A": list(np.random.normal(0.001, 0.005, 200)),  # 低波动
            "B": list(np.random.normal(0.001, 0.020, 200)),  # 高波动
        }
        np.random.seed(1)
        prices = _make_prices(rets)
        w = compute_risk_parity_weights(prices)

        assert w["A"] > w["B"]

    def test_weights_sum_to_one(self) -> None:
        rets = {
            "A": list(np.random.normal(0, 0.01, 100)),
            "B": list(np.random.normal(0, 0.02, 100)),
            "C": list(np.random.normal(0, 0.005, 100)),
        }
        np.random.seed(2)
        prices = _make_prices(rets)
        w = compute_risk_parity_weights(prices)

        total = sum(w.values())
        assert abs(total - Decimal("1")) < Decimal("0.001")

    def test_empty_prices_raises(self) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            compute_risk_parity_weights(pd.DataFrame())

    def test_insufficient_data_equal_weight(self) -> None:
        # 只有 1 个交易日 → 不足算收益 → 等权 fallback
        prices = pd.DataFrame({"A": [100.0], "B": [200.0]})
        w = compute_risk_parity_weights(prices)
        assert w["A"] == Decimal("0.5")
        assert w["B"] == Decimal("0.5")

    def test_zero_volatility_asset_excluded(self) -> None:
        # A 完全无波动（每天价格不变）→ 应得 0 权重
        rets = {
            "A": [0.0] * 50,  # 零波动
            "B": list(np.random.normal(0, 0.01, 50)),
        }
        prices = _make_prices(rets)
        w = compute_risk_parity_weights(prices)

        # A 权重应该极小（接近 0）
        assert w["A"] < Decimal("0.01")
        # B 占主导
        assert w["B"] > Decimal("0.99")
