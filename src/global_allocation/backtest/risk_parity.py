"""风险平价权重计算。

参照 specs/050-backtest-engine.md。

风险平价（Risk Parity）目标：每种资产对组合总波动率的边际贡献相等。
实现：用历史协方差矩阵 + 数值求解。

简化版（Naive Risk Parity）：
    weight_i ∝ 1 / σ_i
    其中 σ_i = 历史日收益年化波动率。

这等于"按波动率倒数加权"——常见近似，权重不一定严格满足
∂(σ_p)/∂w_i = const，但足够直观、稳定、不需要 scipy 优化。
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd


def compute_risk_parity_weights(prices: pd.DataFrame) -> dict[str, Decimal]:
    """计算风险平价权重（倒数波动率加权）。

    Args:
        prices: 宽表 DataFrame，columns=asset symbol，values=Adj Close。

    Returns:
        字典 {symbol: weight}，权重和 = 1.0。

    算法：
        1. 算日收益：ret = prices.pct_change().dropna()
        2. 算每列年化波动率 σ_i
        3. 原始权重 w_i_raw = 1 / σ_i
        4. 归一化：w_i = w_i_raw / sum(w_i_raw)

    Edge cases:
        - 任一 σ_i = 0 → 该资产分母为 inf → 设为 0 权重（视为无风险）
        - 全部 σ = 0 → 等权 fallback
    """
    if prices.empty or len(prices.columns) == 0:
        raise ValueError("prices 不能为空")

    symbols: list[str] = list(prices.columns)

    # 1. 日收益
    returns = prices.pct_change().dropna()
    if returns.empty:
        # 不足 2 个交易日 → 等权
        n = len(symbols)
        return {s: Decimal("1") / Decimal(n) for s in symbols}

    # 2. 年化波动率（用 252 交易日）
    vols = returns.std() * np.sqrt(252)

    # 3. 倒数权重
    raw = pd.Series(
        {sym: (0.0 if v == 0 or pd.isna(v) else 1.0 / v) for sym, v in vols.items()}
    )

    # 4. 归一化
    total = raw.sum()
    if total == 0:
        # 全部 0 波动率（不太可能）→ 等权
        n = len(symbols)
        return {s: Decimal("1") / Decimal(n) for s in symbols}

    normalized = raw / total

    # 转 Decimal（4 位小数）
    return {
        s: Decimal(str(round(float(normalized[s]), 6))) for s in symbols
    }


__all__ = ["compute_risk_parity_weights"]
