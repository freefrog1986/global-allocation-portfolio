"""大类资产配置策略（spec 097 — 第十六轮：内部权重 + 现金区间 [15%, 50%]）。

策略三层结构：
- **Layer 1（细粒度上限）**：14 个 SwensenClass 子类各设上限 —— 防止单一资产过度集中
- **Layer 2a（投资类内部权重）**：4 个投资超类（股票/REITs/债券/商品）按收益率排序的相对权重 —— 反映风险偏好
- **Layer 2b（现金区间）**：现金是"子弹/弹药"，允许在 [15%, 50%] 区间波动 —— 经济危机时抄底

关键设计变更（第十六轮 vs 第十五轮）：
- 之前：投资类是"绝对目标"（股票 70% / REITs 8% / 债券 15% / 商品 4%，合计 97%）
- 现在：投资类是"内部权重"（股票 70% / REITs 15% / 债券 10% / 商品 5%，合计 100% 投资部分）
- 实际目标 = 内部权重 × (1 − 现金占比)
- 这样现金仓位变化时，投资类自动等比例缩放，不需要重新算

为什么现金走区间不是目标（liubo 2026-09-18 第十五轮反馈）：
- 股票/REITs/债券/商品属于"投资类"，追求收益
- 现金属于"子弹/弹药"，用于市场大跌时抄底 + 应对突发流动性需求
- 区间策略：常态下子弹保留 [15%, 50%]，市场大跌时抄底后子弹变少也没事（允许到 15%）

为什么按"收益率"排序内部权重（liubo 2026-09-18 第十六轮反馈）：
- 股票长期收益最高，"占大头"
- REITs 介于股债之间（收益 + 抗通胀）
- 债券中等收益
- 商品不算投资资产，"长期没那么值钱"
- 排序：股票 > REITs > 债券 > 商品

数字依据：
- Layer 1 上限：基于 David Swensen《Unconventional Success》+ Yale endowment 模型
- Layer 2a 内部权重：liubo 第十六轮明确 "股票占大头" → 70/15/10/5
- Layer 2b 区间：liubo 第十五轮 [20%, 50%] → 第十六轮放宽到 [15%, 50%]（"特别好的投资机会可以到 15%")

后续演进：
- 策略数字存本模块的 `DEFAULT_STRATEGY` 常量（frozen dataclass，后续可改）
- 不存数据库（"在那个持仓策略那个模块里边，去把这个策略写清楚呀，策略后续肯定是要变的"）
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from global_allocation.portfolio.breakdown import BreakdownRow, SwensenClass


class SuperCategory(str, Enum):
    """5 个超类（聚合 SwensenClass 14 子类）。

    命名跟 display_name 分开：
    - enum value 用英文小写（序列化 / dict key 友好）
    - display_name 用中文（飞书卡片展示用）
    """

    EQUITY = "equity"  # 股票（7 子类）— 投资类，按收益排序 #1
    BOND = "bond"  # 债券（3 子类）— 投资类，按收益排序 #3
    REIT = "reit"  # REITs（2 子类）— 投资类，按收益排序 #2
    COMMODITY = "commodity"  # 商品（1 子类）— 投资类，按收益排序 #4
    CASH = "cash"  # 现金（1 子类）— 子弹，走区间策略不走权重


SUPER_CATEGORY_DISPLAY_NAME: dict[SuperCategory, str] = {
    SuperCategory.EQUITY: "股票",
    SuperCategory.BOND: "债券",
    SuperCategory.REIT: "REITs",
    SuperCategory.COMMODITY: "商品",
    SuperCategory.CASH: "现金",
}


# 投资类超类（4 个，不含现金 — 现金走区间策略）
# 注意：这里按"收益率排序"（spec 097 第十六轮）：股票 > REITs > 债券 > 商品
INVESTMENT_CATEGORIES: tuple[SuperCategory, ...] = (
    SuperCategory.EQUITY,
    SuperCategory.REIT,
    SuperCategory.BOND,
    SuperCategory.COMMODITY,
)


# SwensenClass → SuperCategory 映射（14 子类分到 5 超类）
SUBCLASS_TO_SUPER: dict[SwensenClass, SuperCategory] = {
    # 股票（7 子类）
    SwensenClass.CN_EQUITY: SuperCategory.EQUITY,
    SwensenClass.HK_EQUITY: SuperCategory.EQUITY,
    SwensenClass.US_EQUITY: SuperCategory.EQUITY,
    SwensenClass.EU_EQUITY: SuperCategory.EQUITY,
    SwensenClass.ASIA_DM_EQUITY: SuperCategory.EQUITY,
    SwensenClass.EM_EQUITY: SuperCategory.EQUITY,
    SwensenClass.GLOBAL_THEMED_EQUITY: SuperCategory.EQUITY,
    # REITs（2 子类）
    SwensenClass.CN_REIT: SuperCategory.REIT,
    SwensenClass.US_REIT: SuperCategory.REIT,
    # 债券（3 子类）
    SwensenClass.CN_GOV_BOND: SuperCategory.BOND,
    SwensenClass.CN_CREDIT_BOND: SuperCategory.BOND,
    SwensenClass.US_BOND: SuperCategory.BOND,
    # 商品（1 子类）
    SwensenClass.COMMODITY: SuperCategory.COMMODITY,
    # 现金（1 子类）
    SwensenClass.CASH: SuperCategory.CASH,
}


@dataclass(frozen=True, slots=True)
class SubclassLimit:
    """单个 SwensenClass 子类的上限（不设目标，只设上限）。

    upper 是 0~1 的小数（不是百分比数字）。展示时 ×100。
    """

    subclass: SwensenClass
    upper: Decimal


@dataclass(frozen=True, slots=True)
class InvestmentWeight:
    """投资类超类的内部权重（spec 097 第十六轮新增，替代 SuperCategoryTarget）。

    weight 是 0~1 的小数，4 类合计 = 1.0（投资部分）。
    实际目标 = weight × (1 − cash_weight)。

    为什么用"内部权重"而不是"绝对目标"：
    - 现金仓位区间 [15%, 50%]，不固定
    - 当现金 = 30% 时，投资部分 = 70%，各投资类按内部权重分配这 70%
    - 当现金 = 50% 时，投资部分 = 50%，各投资类按内部权重分配这 50%
    - 内部权重是不变的相对比例，绝对值随现金占比自动缩放
    """

    category: SuperCategory
    weight: Decimal


@dataclass(frozen=True, slots=True)
class CashRange:
    """现金仓位区间策略（"子弹"区间，spec 097 第十五轮新增）。

    现金是子弹/弹药，不是投资：
    - min_weight：子弹下限（市场大跌时有钱抄底）
    - max_weight：子弹上限（不要囤太多，资金有机会成本）
    - 当前 cash 占比应在 [min, max] 区间内，超出区间 → 调仓信号

    数字依据（liubo 2026-09-18 反馈）：
    - 不低于 15%（第十六轮从 20% 放宽到 15%）："如果有特别好的投资机会，15% 也行"
    - 不高于 50%：囤太多资金会有机会成本（投资收益跑赢现金）
    """

    min_weight: Decimal  # 子弹下限（0~1）
    max_weight: Decimal  # 子弹上限（0~1）

    def is_in_range(self, current: Decimal) -> bool:
        """当前现金占比是否在区间内。"""
        return self.min_weight <= current <= self.max_weight

    def status(self, current: Decimal) -> str:
        """当前现金占比的状态文本（卡片展示用）。

        - "区间内"：在 [min, max] 区间里
        - "低于下限"：子弹打光了，需要警惕
        - "高于上限"：子弹囤太多，需要加仓投资类
        """
        if current < self.min_weight:
            return "低于下限"
        if current > self.max_weight:
            return "高于上限"
        return "区间内"

    @property
    def display_range(self) -> str:
        """卡片展示用的区间字符串（例 "[15%, 50%]"）。"""
        return f"[{int(self.min_weight * 100)}%, {int(self.max_weight * 100)}%]"


@dataclass(frozen=True, slots=True)
class AllocationStrategy:
    """完整大类资产配置策略（三层结构，第十六轮）。

    - subclass_limits: 14 个子类的上限（防风险集中）
    - investment_weights: 4 个投资类超类的内部权重（不含现金，合计 = 1.0）
    - cash_range: 现金仓位区间（子弹策略）

    注意：投资类内部权重之和 = 1.0（投资部分），实际目标由
    compute_actual_target() 按当前现金占比动态计算。
    """

    subclass_limits: tuple[SubclassLimit, ...]
    investment_weights: tuple[InvestmentWeight, ...]
    cash_range: CashRange

    def subclass_upper(self, subclass: SwensenClass) -> Decimal | None:
        """查某子类的上限（无 = None）。"""
        for limit in self.subclass_limits:
            if limit.subclass == subclass:
                return limit.upper
        return None

    def investment_weight(self, category: SuperCategory) -> Decimal | None:
        """查某投资类超类的内部权重（无 = None；现金不在内，现金走 cash_range）。"""
        for w in self.investment_weights:
            if w.category == category:
                return w.weight
        return None

    @property
    def total_investment_weight(self) -> Decimal:
        """4 个投资类内部权重之和（应 = 1.0）。"""
        return sum((w.weight for w in self.investment_weights), Decimal("0"))


# 默认策略：Swensen/Yale endowment + liubo 70% 股激进版 + 现金区间 [15%, 50%]
#
# Layer 1 上限依据（基于 Swensen《Unconventional Success》/ Yale 模型 + 中国市场分散调整）：
# - 股票子类（A/HK/US/EU/ASIA_DM/EM/GLOBAL_THEMED）：单市场上限 15-40%，避免单一地区风险过度集中
#   - CN_EQUITY 40% — 主场可适度集中（home bias）
#   - HK_EQUITY 25% — 大中华海外配置
#   - US_EQUITY 25% — Swensen 国际发达市场拆分
#   - 其他股票子类 10-15%
# - 债券子类（CN_GOV/CN_CREDIT/US_BOND）：上限 15-25%，债券本身波动小可以适度集中
#   - CN_GOV_BOND 25% — 利率债波动小
#   - CN_CREDIT_BOND 20% — 信用债有信用风险
#   - US_BOND 15%
# - REITs/商品：上限 10-15%，辅助类配置
# - CASH 子类上限 50%（跟随现金区间上限，作为硬约束）
#
# Layer 2a 内部权重依据（liubo 第十六轮反馈 "股票占大头，按收益率排序"）：
# - 股票 70%：长期收益最高，"占大头"（liubo 明确）
# - REITs 15%：介于股债之间（收益 + 抗通胀），排第二
# - 债券 10%：中等收益，排第三
# - 商品 5%：不算投资资产，"长期没那么值钱"，排最后
# 4 类合计 100%（投资部分），实际目标 = 内部权重 × (1 − 现金占比)
#
# Layer 2b 现金区间依据（liubo 第十六轮反馈 [15%, 50%]）：
# - 子弹下限 15%（第十六轮从 20% 放宽到 15%）："如果有特别好的投资机会，15% 也行"
# - 子弹上限 50%：囤太多资金会有机会成本（投资收益跑赢现金）
DEFAULT_STRATEGY = AllocationStrategy(
    subclass_limits=(
        SubclassLimit(SwensenClass.CN_EQUITY, Decimal("0.40")),
        SubclassLimit(SwensenClass.HK_EQUITY, Decimal("0.25")),
        SubclassLimit(SwensenClass.US_EQUITY, Decimal("0.25")),
        SubclassLimit(SwensenClass.EU_EQUITY, Decimal("0.15")),
        SubclassLimit(SwensenClass.ASIA_DM_EQUITY, Decimal("0.15")),
        SubclassLimit(SwensenClass.EM_EQUITY, Decimal("0.15")),
        SubclassLimit(SwensenClass.GLOBAL_THEMED_EQUITY, Decimal("0.10")),
        SubclassLimit(SwensenClass.CN_REIT, Decimal("0.15")),
        SubclassLimit(SwensenClass.US_REIT, Decimal("0.15")),
        SubclassLimit(SwensenClass.CN_GOV_BOND, Decimal("0.25")),
        SubclassLimit(SwensenClass.CN_CREDIT_BOND, Decimal("0.20")),
        SubclassLimit(SwensenClass.US_BOND, Decimal("0.15")),
        SubclassLimit(SwensenClass.COMMODITY, Decimal("0.10")),
        SubclassLimit(SwensenClass.CASH, Decimal("0.50")),
    ),
    investment_weights=(
        InvestmentWeight(SuperCategory.EQUITY, Decimal("0.70")),
        InvestmentWeight(SuperCategory.REIT, Decimal("0.15")),
        InvestmentWeight(SuperCategory.BOND, Decimal("0.10")),
        InvestmentWeight(SuperCategory.COMMODITY, Decimal("0.05")),
    ),
    cash_range=CashRange(
        min_weight=Decimal("0.15"),  # 子弹下限（第十六轮：放宽到 15%）
        max_weight=Decimal("0.50"),  # 子弹上限
    ),
)


def compute_actual_target(
    strategy: AllocationStrategy,
    category: SuperCategory,
    current_cash_weight: Decimal,
) -> Decimal | None:
    """给定当前现金占比，计算某投资类超类的实际目标。

    公式：actual_target = internal_weight × (1 − current_cash_weight)

    Args:
        strategy: 完整策略（含内部权重）
        category: 投资类超类（不含现金）
        current_cash_weight: 当前现金占比（0~1）

    Returns:
        实际目标占比（0~1）；现金类返回 None（现金走 cash_range，不走权重）

    为什么是动态公式：
    - 现金区间 [15%, 50%] 不固定，投资部分 = 100% − 现金%
    - 投资部分按内部权重分配（股票 70% / REITs 15% / 债券 10% / 商品 5%）
    - 现金越多，投资部分越少；现金越少，投资部分越多
    - 这样用户改现金时不用重新设目标，内部权重自动缩放
    """
    if category == SuperCategory.CASH:
        return None
    weight = strategy.investment_weight(category)
    if weight is None:
        return None
    investment_total = Decimal("1") - current_cash_weight
    return weight * investment_total


def compute_super_category_breakdown(
    breakdown: list[BreakdownRow],
) -> dict[SuperCategory, Decimal]:
    """聚合 breakdown 各子类权重到超类。

    Args:
        breakdown: compute_breakdown() 的输出（14 子类）

    Returns:
        dict[SuperCategory, Decimal]，每个 value 是该超类下所有子类权重之和（0~1）
    """
    result: dict[SuperCategory, Decimal] = {c: Decimal("0") for c in SuperCategory}
    for row in breakdown:
        cat = SUBCLASS_TO_SUPER[row["subclass"]]
        result[cat] += row["weight"]
    return result


__all__ = [
    "SuperCategory",
    "SUPER_CATEGORY_DISPLAY_NAME",
    "INVESTMENT_CATEGORIES",
    "SUBCLASS_TO_SUPER",
    "SubclassLimit",
    "InvestmentWeight",
    "CashRange",
    "AllocationStrategy",
    "DEFAULT_STRATEGY",
    "compute_actual_target",
    "compute_super_category_breakdown",
]
