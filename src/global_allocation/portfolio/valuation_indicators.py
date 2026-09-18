"""估值指标计算层（spec 098）。

数据从 ValuationSource 拉过来，本模块负责 4 个计算：
- 股债利差 = 1/PE − 10Y 国债收益率
- PE 分位 = (当前 − 历史 min) / (历史 max − min)
- 巴菲特指标 = 总市值 / GDP
- 评估文本 = 按阈值返回"偏低估"/"正常"/"偏高估"

阈值常量（DEFAULT_THRESHOLDS）写死在模块顶部，方便后续调整（不动 compute 函数）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from global_allocation.portfolio.models import ValuationIndicatorCode


@dataclass(frozen=True)
class IndicatorThreshold:
    """一个指标的阈值定义。

    边界值用 Decimal（不是 float）— 避免 0.05 → 0.05000000000000001 这种比较坑。
    `direction='low'` 表示值越大越低估（股债利差、股息率）；
    `direction='high'` 表示值越大越高估（PE 分位、巴菲特指标）。
    """

    low_max: Decimal  # ≤ 这个值 = "偏低估"
    high_min: Decimal  # ≥ 这个值 = "偏高估"
    direction: str  # "low"（值大=低估） 或 "high"（值大=高估）


# ─── 默认阈值（spec 098 第 49~52 行）───
# 后续用户反馈调整阈值改这里即可，不动 compute 函数。
DEFAULT_THRESHOLDS: dict[ValuationIndicatorCode, IndicatorThreshold] = {
    # 股债利差 > 5% 低估（股票相对债券很有吸引力），< 2% 高估
    # direction='low' — 越大越低估
    ValuationIndicatorCode.EQUITY_RISK_PREMIUM: IndicatorThreshold(
        low_max=Decimal("0.05"),
        high_min=Decimal("0.02"),
        direction="low",
    ),
    # PE 分位 < 30% 低估，> 70% 高估
    # direction='high' — 越大越高估
    ValuationIndicatorCode.PE_PERCENTILE: IndicatorThreshold(
        low_max=Decimal("0.30"),
        high_min=Decimal("0.70"),
        direction="high",
    ),
    # 巴菲特指标 < 50% 低估，> 80% 高估
    ValuationIndicatorCode.BUFFETT_INDICATOR: IndicatorThreshold(
        low_max=Decimal("0.50"),
        high_min=Decimal("0.80"),
        direction="high",
    ),
    # 股息率 > 3% 低估（高分红 = 市场便宜），< 1% 高估
    # direction='low' — 越大越低估
    ValuationIndicatorCode.DIVIDEND_YIELD: IndicatorThreshold(
        low_max=Decimal("0.03"),
        high_min=Decimal("0.01"),
        direction="low",
    ),
}


# ─── 4 个 compute 函数 ───


def compute_equity_risk_premium(
    pe_ttm: Decimal,
    treasury_yield: Decimal,
) -> Decimal:
    """股债利差 = 1/PE − 10Y 国债收益率。

    返回 0~1 范围的小数（不是百分数）：
    - PE=20, 国债=3% → 1/20 − 0.03 = 0.05 − 0.03 = 0.02 (= 2%)
    - PE=15, 国债=3% → 1/15 − 0.03 ≈ 0.0367 (= 3.67%)

    边界：
    - PE ≤ 0 → 抛 ValueError（数据异常，spec 098 第 174 行）
    """
    if pe_ttm <= Decimal("0"):
        raise ValueError(f"PE-TTM 必须为正数，实际 {pe_ttm}")
    return Decimal("1") / pe_ttm - treasury_yield


def compute_pe_percentile(
    current_pe: Decimal,
    pe_history: list[Decimal],
) -> Decimal:
    """PE 分位 = (current − min) / (max − min)。

    返回 0~1 的小数：
    - 0 = 历史最低
    - 1 = 历史最高
    - 0.5 = 历史中位数附近

    边界：
    - history 为空 → 抛 ValueError（spec 098 第 173 行：历史不足 → 视为数据异常）
    - max == min（PE 一直不变）→ 返回 0.5（中位数）— 跟"位置无法判断"语义一致
    - current_pe 超出范围 → 返回 0（< min）或 1（> max），夹紧
    """
    if not pe_history:
        raise ValueError("PE 历史序列为空，无法计算分位")

    pe_min = min(pe_history)
    pe_max = max(pe_history)

    if pe_max == pe_min:
        # PE 一直不变 → 没法判断位置，取中位数
        return Decimal("0.5")

    # 夹紧：超出范围返回 0 或 1
    if current_pe <= pe_min:
        return Decimal("0")
    if current_pe >= pe_max:
        return Decimal("1")

    return (current_pe - pe_min) / (pe_max - pe_min)


def compute_buffett_indicator(
    market_cap: Decimal,
    gdp: Decimal,
) -> Decimal:
    """巴菲特指标 = A 股总市值 / 中国 GDP。

    返回 0~N 范围的倍数：
    - 0.65 = 总市值占 GDP 的 65%
    - 1.20 = 总市值超过 GDP（泡沫）

    边界：
    - gdp <= 0 → 抛 ValueError（GDP 数据异常）
    """
    if gdp <= Decimal("0"):
        raise ValueError(f"GDP 必须为正数，实际 {gdp}")
    return market_cap / gdp


def compute_verdict(
    code: ValuationIndicatorCode,
    value: Decimal,
    thresholds: dict[ValuationIndicatorCode, IndicatorThreshold] | None = None,
) -> str:
    """根据阈值返回评估文本。

    返回值（spec 098 第 106~110 行）：
    - "偏低估" — 值落在"低估"区间
    - "正常" — 值落在中间
    - "偏高估" — 值落在"高估"区间
    - "n/a" — 未知指标 code（不在 thresholds 里）

    阈值默认用 DEFAULT_THRESHOLDS；测试或定制场景可传自己的 dict。
    """
    rules = thresholds or DEFAULT_THRESHOLDS
    if code not in rules:
        return "n/a"

    threshold = rules[code]
    if threshold.direction == "low":
        # 值越大越低估（股债利差、股息率）
        if value >= threshold.low_max:
            return "偏低估"
        if value <= threshold.high_min:
            return "偏高估"
        return "正常"
    else:
        # 值越大越高估（PE 分位、巴菲特指标）
        if value <= threshold.low_max:
            return "偏低估"
        if value >= threshold.high_min:
            return "偏高估"
        return "正常"


__all__ = [
    "IndicatorThreshold",
    "DEFAULT_THRESHOLDS",
    "compute_equity_risk_premium",
    "compute_pe_percentile",
    "compute_buffett_indicator",
    "compute_verdict",
]
