"""估值指标计算层（spec 098）。

数据从 ValuationSource 拉过来，本模块负责：
- 4 个计算：股债利差 / PE 分位 / 巴菲特 / 股息率
- 评估文本：按阈值返回"偏低估"/"正常"/"偏高估"
- 1-5 分打分：每个指标独立打分，简单平均得综合分（spec 098 第二十一轮加）

阈值常量（DEFAULT_THRESHOLDS / DEFAULT_SCORE_BANDS）写死在模块顶部，
方便后续调整（不动 compute 函数）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

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
    # ── 港股 4 指标（spec 098.2 — liubo 2026-09-19 方案 A）───
    # 港股 PE 分位：跟 A 股 PE 分位阈值一致（< 30% 低估, > 70% 高估）
    ValuationIndicatorCode.HK_PE_PERCENTILE: IndicatorThreshold(
        low_max=Decimal("0.30"),
        high_min=Decimal("0.70"),
        direction="high",
    ),
    # 港股股息率：跟 A 股股息率阈值一致（> 3% 低估, < 1% 高估）
    ValuationIndicatorCode.HK_DIVIDEND_YIELD: IndicatorThreshold(
        low_max=Decimal("0.03"),
        high_min=Decimal("0.01"),
        direction="low",
    ),
    # AH 溢价（A 股价格 / H 股价格）— 值越大 = H 股相对越便宜
    # 历史均值 ~125%；> 150% H 股显著便宜（低估），< 40% H 股不便宜（高估）
    # direction='low' — 越大越低估（H 股相对便宜，跟 股债利差 / 股息率 一致语义）
    ValuationIndicatorCode.HK_AH_PREMIUM: IndicatorThreshold(
        low_max=Decimal("1.50"),  # ≥ 1.50（150%） → 偏低估
        high_min=Decimal("0.40"),  # ≤ 0.40（40%） → 偏高估
        direction="low",
    ),
    # 港股巴菲特（港股总市值 / 香港 GDP）
    # 港股因中概股回归，市值远大于本地 GDP：实际值通常 8-15（800%~1500%）
    # 阈值参考 A 股 5 档语义但整体上移到 5/8/12/15 倍
    # direction='high' — 越大越高估
    ValuationIndicatorCode.HK_BUFFETT_INDICATOR: IndicatorThreshold(
        low_max=Decimal("8"),
        high_min=Decimal("12"),
        direction="high",
    ),
    # ── 美股 4 指标（spec 098.3 — liubo 2026-09-20 拍板）───
    # 美股 PE 分位：跟 A 股 / 港股 阈值一致
    ValuationIndicatorCode.US_PE_PERCENTILE: IndicatorThreshold(
        low_max=Decimal("0.30"),
        high_min=Decimal("0.70"),
        direction="high",
    ),
    # 美股股息率：跟 A 股一致（> 3% 低估, < 1% 高估）
    # 注：S&P 500 历史股息率 1.5-2%，多数时间落"正常"区间
    ValuationIndicatorCode.US_DIVIDEND_YIELD: IndicatorThreshold(
        low_max=Decimal("0.03"),
        high_min=Decimal("0.01"),
        direction="low",
    ),
    # 美股巴菲特（US 总市值 / US GDP）
    # 美股历史比 A 股高：2024 约 180-200%（2020 后泡沫期），长期均值 130%
    # 阈值上移到 80/130/180（>180% 偏高估，<80% 偏低估）
    # direction='high' — 越大越高估
    ValuationIndicatorCode.US_BUFFETT_INDICATOR: IndicatorThreshold(
        low_max=Decimal("0.80"),
        high_min=Decimal("1.80"),
        direction="high",
    ),
    # 美股股债利差 = 1/PE - 美 10Y 国债收益率
    # 跟 A 股一致阈值（>5% 低估，<2% 高估）
    # direction='low' — 越大越低估
    ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM: IndicatorThreshold(
        low_max=Decimal("0.05"),
        high_min=Decimal("0.02"),
        direction="low",
    ),
}


# ─── 1-5 分打分（spec 098 第二十一轮新增 — liubo 2026-09-19 反馈）───
#
# 设计意图：
# - 每个指标独立打 1-5 分：1=极度低估（加仓好时机），5=极度高估（减仓回避）
# - 综合分 = 4 个分数的算术平均（不加权，简单投票）
# - 卡片展示 "综合 3.0 [PE:4 股债:2 巴菲特:3 股息:3]" — 一眼看出哪项拖后腿
#
# 为什么简单平均：
# - 4 个指标没有可靠的优先级权重（不同时期相关性不同）
# - 用户投票 = 每个指标一票，谁都不是主导，符合 Swensen 多元化精神
#
# 打分带宽度（5 个区间 = 5 个分）：
# direction='low'（值大=低估）：值从高到低对应分从低到高（5→1）
# direction='high'（值大=高估）：值从低到高对应分从低到高（1→5）
# 边界用 [a, b) 半开区间 — a 是上界，b 是下界；落点 = 第一个匹配的区间


@dataclass(frozen=True)
class ScoreBand:
    """一个打分区间。

    对应 1-5 分中的一个：
    - 1 = 极低估（加仓良机）
    - 2 = 低估（可加仓）
    - 3 = 正常（持有）
    - 4 = 偏高估（注意）
    - 5 = 极高估（减仓回避）

    `low` 是分数对应的下限值（含），`high` 是上限值（不含，None 表示 +∞）。
    direction 跟 IndicatorThreshold 一致：'low' = 值大=低估，分数随值减小；
    'high' = 值大=高估，分数随值增大。
    """

    low: Decimal  # 值 ≥ low 才落入本区间
    high: Decimal | None  # 值 < high 才落入本区间（None 表示到 +∞）
    score: int  # 本区间对应的分数（1-5）
    direction: str  # "low" 或 "high"


# 5 个分数 + 每个指标 5 个区间（共 4 × 5 = 20 个 ScoreBand）
# liubo 2026-09-19：阈值参考 DEFAULT_THRESHOLDS 的 3 段边界，向两端延伸各 1 段

DEFAULT_SCORE_BANDS: dict[ValuationIndicatorCode, list[ScoreBand]] = {
    # 股债利差（direction='low'：越大越低估）
    #   极低估(1):   value ≥ 5%  （股债利差大 = 股票相对债券溢价高 = 吸引力大）
    #   低估(2):     4% ≤ value < 5%
    #   正常(3):     2% ≤ value < 4%
    #   偏高估(4):   0% ≤ value < 2%
    #   极高估(5):   value < 0%
    ValuationIndicatorCode.EQUITY_RISK_PREMIUM: [
        ScoreBand(Decimal("0.05"), None, 1, "low"),
        ScoreBand(Decimal("0.04"), Decimal("0.05"), 2, "low"),
        ScoreBand(Decimal("0.02"), Decimal("0.04"), 3, "low"),
        ScoreBand(Decimal("0.00"), Decimal("0.02"), 4, "low"),
        ScoreBand(Decimal("-1e10"), Decimal("0.00"), 5, "low"),
    ],
    # PE 分位（direction='high'：越大越高估）
    #   极低估(1):   value < 10%
    #   低估(2):     10% ≤ value < 30%
    #   正常(3):     30% ≤ value < 70%
    #   偏高估(4):   70% ≤ value < 90%
    #   极高估(5):   value ≥ 90%
    ValuationIndicatorCode.PE_PERCENTILE: [
        ScoreBand(Decimal("-1e10"), Decimal("0.10"), 1, "high"),
        ScoreBand(Decimal("0.10"), Decimal("0.30"), 2, "high"),
        ScoreBand(Decimal("0.30"), Decimal("0.70"), 3, "high"),
        ScoreBand(Decimal("0.70"), Decimal("0.90"), 4, "high"),
        ScoreBand(Decimal("0.90"), None, 5, "high"),
    ],
    # 巴菲特指标（direction='high'：越大越高估）
    #   极低估(1):   value < 40%
    #   低估(2):     40% ≤ value < 50%
    #   正常(3):     50% ≤ value < 80%
    #   偏高估(4):   80% ≤ value < 100%
    #   极高估(5):   value ≥ 100%
    ValuationIndicatorCode.BUFFETT_INDICATOR: [
        ScoreBand(Decimal("-1e10"), Decimal("0.40"), 1, "high"),
        ScoreBand(Decimal("0.40"), Decimal("0.50"), 2, "high"),
        ScoreBand(Decimal("0.50"), Decimal("0.80"), 3, "high"),
        ScoreBand(Decimal("0.80"), Decimal("1.00"), 4, "high"),
        ScoreBand(Decimal("1.00"), None, 5, "high"),
    ],
    # 股息率（direction='low'：越大越低估）
    #   极低估(1):   value ≥ 4%
    #   低估(2):     3% ≤ value < 4%
    #   正常(3):     1% ≤ value < 3%
    #   偏高估(4):   0.5% ≤ value < 1%
    #   极高估(5):   value < 0.5%
    ValuationIndicatorCode.DIVIDEND_YIELD: [
        ScoreBand(Decimal("0.04"), None, 1, "low"),
        ScoreBand(Decimal("0.03"), Decimal("0.04"), 2, "low"),
        ScoreBand(Decimal("0.01"), Decimal("0.03"), 3, "low"),
        ScoreBand(Decimal("0.005"), Decimal("0.01"), 4, "low"),
        ScoreBand(Decimal("-1e10"), Decimal("0.005"), 5, "low"),
    ],
    # ── 港股 4 指标 5 档（spec 098.2 — liubo 2026-09-19 方案 A）───
    # 港股 PE 分位：跟 A 股 PE 分位阈值完全一致
    ValuationIndicatorCode.HK_PE_PERCENTILE: [
        ScoreBand(Decimal("-1e10"), Decimal("0.10"), 1, "high"),
        ScoreBand(Decimal("0.10"), Decimal("0.30"), 2, "high"),
        ScoreBand(Decimal("0.30"), Decimal("0.70"), 3, "high"),
        ScoreBand(Decimal("0.70"), Decimal("0.90"), 4, "high"),
        ScoreBand(Decimal("0.90"), None, 5, "high"),
    ],
    # 港股股息率：跟 A 股股息率阈值完全一致
    ValuationIndicatorCode.HK_DIVIDEND_YIELD: [
        ScoreBand(Decimal("0.04"), None, 1, "low"),
        ScoreBand(Decimal("0.03"), Decimal("0.04"), 2, "low"),
        ScoreBand(Decimal("0.01"), Decimal("0.03"), 3, "low"),
        ScoreBand(Decimal("0.005"), Decimal("0.01"), 4, "low"),
        ScoreBand(Decimal("-1e10"), Decimal("0.005"), 5, "low"),
    ],
    # AH 溢价（direction='low'：值大=H 便宜=低估）
    #   极低估(1):   value ≥ 1.60  （AH ≥160%，H 巨便宜）
    #   低估(2):     1.30 ≤ value < 1.60
    #   正常(3):     1.00 ≤ value < 1.30
    #   偏高估(4):   0.80 ≤ value < 1.00
    #   极高估(5):   value < 0.80   （AH < 80%，H 不便宜或反而贵）
    ValuationIndicatorCode.HK_AH_PREMIUM: [
        ScoreBand(Decimal("1.60"), None, 1, "low"),
        ScoreBand(Decimal("1.30"), Decimal("1.60"), 2, "low"),
        ScoreBand(Decimal("1.00"), Decimal("1.30"), 3, "low"),
        ScoreBand(Decimal("0.80"), Decimal("1.00"), 4, "low"),
        ScoreBand(Decimal("-1e10"), Decimal("0.80"), 5, "low"),
    ],
    # 港股巴菲特（direction='high'：越大越高估）
    #   极低估(1):   value < 5   （市值 < 5 倍 GDP）
    #   低估(2):     5 ≤ value < 8
    #   正常(3):     8 ≤ value < 12
    #   偏高估(4):   12 ≤ value < 15
    #   极高估(5):   value ≥ 15
    ValuationIndicatorCode.HK_BUFFETT_INDICATOR: [
        ScoreBand(Decimal("-1e10"), Decimal("5"), 1, "high"),
        ScoreBand(Decimal("5"), Decimal("8"), 2, "high"),
        ScoreBand(Decimal("8"), Decimal("12"), 3, "high"),
        ScoreBand(Decimal("12"), Decimal("15"), 4, "high"),
        ScoreBand(Decimal("15"), None, 5, "high"),
    ],
    # ── 美股 4 指标 5 档（spec 098.3 — liubo 2026-09-20）───
    # 美股 PE 分位：跟 A 股 / 港股 完全一致
    ValuationIndicatorCode.US_PE_PERCENTILE: [
        ScoreBand(Decimal("-1e10"), Decimal("0.10"), 1, "high"),
        ScoreBand(Decimal("0.10"), Decimal("0.30"), 2, "high"),
        ScoreBand(Decimal("0.30"), Decimal("0.70"), 3, "high"),
        ScoreBand(Decimal("0.70"), Decimal("0.90"), 4, "high"),
        ScoreBand(Decimal("0.90"), None, 5, "high"),
    ],
    # 美股股息率：跟 A 股 / 港股 完全一致
    ValuationIndicatorCode.US_DIVIDEND_YIELD: [
        ScoreBand(Decimal("0.04"), None, 1, "low"),
        ScoreBand(Decimal("0.03"), Decimal("0.04"), 2, "low"),
        ScoreBand(Decimal("0.01"), Decimal("0.03"), 3, "low"),
        ScoreBand(Decimal("0.005"), Decimal("0.01"), 4, "low"),
        ScoreBand(Decimal("-1e10"), Decimal("0.005"), 5, "low"),
    ],
    # 美股巴菲特（direction='high'：越大越高估）
    #   极低估(1):   value < 80%
    #   低估(2):     80% ≤ value < 130%
    #   正常(3):     130% ≤ value < 180%
    #   偏高估(4):   180% ≤ value < 220%
    #   极高估(5):   value ≥ 220%
    ValuationIndicatorCode.US_BUFFETT_INDICATOR: [
        ScoreBand(Decimal("-1e10"), Decimal("0.80"), 1, "high"),
        ScoreBand(Decimal("0.80"), Decimal("1.30"), 2, "high"),
        ScoreBand(Decimal("1.30"), Decimal("1.80"), 3, "high"),
        ScoreBand(Decimal("1.80"), Decimal("2.20"), 4, "high"),
        ScoreBand(Decimal("2.20"), None, 5, "high"),
    ],
    # 美股股债利差（direction='low'：越大越低估）
    # 跟 A 股一致阈值：>5% 极低估，<0% 极高估
    ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM: [
        ScoreBand(Decimal("0.05"), None, 1, "low"),
        ScoreBand(Decimal("0.03"), Decimal("0.05"), 2, "low"),
        ScoreBand(Decimal("0.01"), Decimal("0.03"), 3, "low"),
        ScoreBand(Decimal("0"), Decimal("0.01"), 4, "low"),
        ScoreBand(Decimal("-1e10"), Decimal("0"), 5, "low"),
    ],
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


def score_indicator(
    code: ValuationIndicatorCode,
    value: Decimal,
    score_bands: dict[ValuationIndicatorCode, list[ScoreBand]] | None = None,
) -> int:
    """根据 1-5 分打分带返回单个指标的分数。

    返回值：1-5 的整数（1=极低估，5=极高估）。
    超出范围 / 不在列表里 → 抛 ValueError（让 caller 知道是数据问题）。

    算法：找到第一个 value 落入 [low, high) 的区间（high=None 表示 +∞）。
    """
    bands = score_bands or DEFAULT_SCORE_BANDS
    if code not in bands:
        raise ValueError(f"未知指标 {code}，无法打分")
    for band in bands[code]:
        if value >= band.low and (band.high is None or value < band.high):
            return band.score
    # 5 个区间都该覆盖整个值域，到这里说明边界值有 bug
    raise ValueError(f"指标 {code} 值 {value} 落入任何打分区间（5 个区间必有 1 个匹配）")


def compute_composite_score(scores: list[int]) -> Decimal:
    """综合分 = 简单平均（4 个分数 / 4），保留 1 位小数。

    spec 098 第二十一轮：liubo 反馈"不要加权，让他们投票"。

    边界：
    - 空列表 → 抛 ValueError（4 个指标缺一不可，综合分没意义）
    - 任意分数不在 1-5 → 抛 ValueError（数据异常）
    """
    if not scores:
        raise ValueError("综合分需要至少 1 个分数")
    if any(s < 1 or s > 5 for s in scores):
        raise ValueError(f"分数必须在 1-5 之间，实际 {scores}")
    total = sum(scores)
    avg = Decimal(total) / Decimal(len(scores))
    # 保留 1 位小数（4 票平均后是 0.25 步长）。
    # 用 ROUND_HALF_UP（不是 Decimal 默认的 ROUND_HALF_EVEN）— 财务展示
    # 通常期待 "四舍五入"，banker's rounding 对用户反直觉。
    return avg.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def interpret_composite_score(composite: Decimal) -> str:
    """综合分解读文案（5 档）。

    - 1.0-1.5：极低（明显低估，加仓良机）
    - 1.5-2.5：低估（可加仓）
    - 2.5-3.5：正常（持有）
    - 3.5-4.5：偏高估（注意风险）
    - 4.5-5.0：极高估（减仓回避）

    边界用 [a, b) 半开区间。
    """
    if composite < Decimal("1.5"):
        return "极低"
    if composite < Decimal("2.5"):
        return "低估"
    if composite < Decimal("3.5"):
        return "正常"
    if composite < Decimal("4.5"):
        return "偏高估"
    return "极高估"


def format_5band_threshold(bands: list[ScoreBand]) -> str:
    """把 1 个指标的 5 个 ScoreBand 格式化成卡片阈值文案（spec 098 第二十四轮）。

    格式：2 行（用 \\n 分隔，飞书 table cell 支持换行）：
        行 1：5 个分界点    "≥5%/4%/2%/0%/<0%"
        行 2：5 个分数对应  "1/2/3/4/5"

    渲染规则（按 score 1→5 排序）：

    正常情况（两端都不在 ±∞ 哨兵）：
        [score1.low, score2.low, score3.low, score4.low, score5.high]
        ["≥"+l1,    l2,       l3,       l4,       "<"+h5]

    score1 碰到 -∞ 哨兵（direction='high' 首段，score1.low=-1e9）：
        用 score1.high 替代 score1.low 作为左端哨兵：
        ["<"+score1.high, score2.high, score3.high, score4.high, score5.low]
        例（PE）：["<10%", "30%", "70%", "90%", "≥90%"]
        注意：score2.low = score1.high，所以要从 score2.high 开始，跳过重复。

    score5 碰到 +∞ 哨兵（direction='low' 末段，score5.high=None）：
        用 score5.low 替代 score5.high 作为右端哨兵：
        ["≥"+score1.low, score2.low, score3.low, score4.low, "≥"+score5.low]
        但 score5.low = score4.high = 普通段末的 high，所以仍要保持顺序。
        实际 direction='low' 末段（score5）是 low=-1e9，high=某个有限值 — 这不是
        +∞ 哨兵，是普通末段。"≥"+score5.low 那一边哨兵要在 score4 之后。

    总结：score1 用 low 还是 high，score5 用 low 还是 high，**对称**：
        - score1.low != -∞ → 用 score1.low（"≥"）
        - score1.low == -∞ → 用 score1.high（"<"）
        - score5.high != None → 用 score5.high（"<"）
        - score5.high == None → 用 score5.low（"≥"）

    中间 3 个边界：根据 score1 用了 low 还是 high 来决定：
        - 若 score1 用 low（"≥"），则中间按 score2/3/4.low 显示（值依次递增）
        - 若 score1 用 high（"<"），则中间按 score2/3/4.high 显示（score2.low ==
          score1.high 已显示过，避免重复）

    例：
        股债利差（direction='low'，score1.low=5% 正常）：
            → "≥5%/4%/2%/0%/<0%"
        PE 分位（direction='high'，score1.low=-1e9）：
            → "<10%/30%/70%/90%/≥90%"
        股息率（direction='low'，score1.low=4% 正常）：
            → "≥4%/3%/1%/0.5%/<0.5%"
    """
    if not bands:
        return ""
    sorted_bands = sorted(bands, key=lambda b: b.score)
    if len(sorted_bands) != 5:
        return ""  # 兜底：只支持 5 档
    s1, _, _, _, s5 = sorted_bands
    points: list[str] = []
    if s1.low <= Decimal("-1e9"):
        # score1 哨兵：用 score1.high 作左端（"<X"），中间按 score2/3/4.high 排
        v = float(s1.high) * 100
        points.append(f"<{v:g}%")
        for i in (1, 2, 3):
            v = float(sorted_bands[i].high) * 100
            points.append(f"{v:g}%")
    else:
        # score1 正常：用 score1.low 作左端（"≥X"），中间按 score2/3/4.low 排
        v = float(s1.low) * 100
        points.append(f"≥{v:g}%")
        for i in (1, 2, 3):
            v = float(sorted_bands[i].low) * 100
            points.append(f"{v:g}%")
    # 右端
    if s5.high is None:
        # 哨兵：用 score5.low 作右端（"≥X"）
        v = float(s5.low) * 100
        points.append(f"≥{v:g}%")
    else:
        # 正常：用 score5.high 作右端（"<X"）
        v = float(s5.high) * 100
        points.append(f"<{v:g}%")
    scores = [str(b.score) for b in sorted_bands]
    return "/".join(points) + "\n" + "/".join(scores)


__all__ = [
    "IndicatorThreshold",
    "ScoreBand",
    "DEFAULT_THRESHOLDS",
    "DEFAULT_SCORE_BANDS",
    "compute_equity_risk_premium",
    "compute_pe_percentile",
    "compute_buffett_indicator",
    "compute_verdict",
    "score_indicator",
    "compute_composite_score",
    "interpret_composite_score",
    "format_5band_threshold",
]
