"""大类资产配置策略（spec 097 — 第十五轮：现金区间策略）。

策略三层结构：
- **Layer 1（细粒度上限）**：14 个 SwensenClass 子类各设上限 —— 防止单一资产过度集中
- **Layer 2a（投资类目标）**：4 个投资超类（股票/债券/REITs/商品）目标比例 —— 反映风险偏好
- **Layer 2b（现金区间）**：现金是"子弹/弹药"，允许在 [20%, 50%] 区间波动 —— 经济危机时抄底

为什么现金是区间不是目标（liubo 2026-09-18 第十五轮反馈）：
- 股票/债券/REITs/商品属于"投资类"，追求收益
- 现金属于"子弹/弹药"，用于市场大跌时抄底 + 应对突发流动性需求
- 区间策略：常态下子弹不要打光（不低于 20%），但也不要囤太多（不高于 50%，资金有机会成本）

数字依据：
- Layer 1 上限：基于 David Swensen《Unconventional Success》（非凡的成功）+ Yale endowment 模型的子类比例
- Layer 2a 目标：liubo 70% 股票激进版（spec 097 第十四轮），剩余按 Swensen 学术建议分给债券/REITs/商品（4 类合计 ~97%，剩 ~3% 平均现金下限的弹性）
- Layer 2b 区间：liubo 第十五轮明确指定 [20%, 50%]（不设固定目标，留弹性）

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

    EQUITY = "equity"  # 股票（7 子类）— 投资类
    BOND = "bond"  # 债券（3 子类）— 投资类
    REIT = "reit"  # REITs（2 子类）— 投资类
    COMMODITY = "commodity"  # 商品（1 子类）— 投资类
    CASH = "cash"  # 现金（1 子类）— 子弹，走区间策略不走固定目标


SUPER_CATEGORY_DISPLAY_NAME: dict[SuperCategory, str] = {
    SuperCategory.EQUITY: "股票",
    SuperCategory.BOND: "债券",
    SuperCategory.REIT: "REITs",
    SuperCategory.COMMODITY: "商品",
    SuperCategory.CASH: "现金",
}


# 投资类超类（4 个，不含现金 — 现金走区间策略）
INVESTMENT_CATEGORIES: tuple[SuperCategory, ...] = (
    SuperCategory.EQUITY,
    SuperCategory.BOND,
    SuperCategory.REIT,
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
class SuperCategoryTarget:
    """投资类超类的目标比例（不含现金，现金走 CashRange 区间策略）。

    target 是 0~1 的小数。展示时 ×100。
    """

    category: SuperCategory
    target: Decimal


@dataclass(frozen=True, slots=True)
class CashRange:
    """现金仓位区间策略（"子弹"区间，spec 097 第十五轮新增）。

    现金是子弹/弹药，不是投资：
    - min_weight：子弹下限（市场大跌时有钱抄底，不低于 20%）
    - max_weight：子弹上限（不要囤太多，资金有机会成本，不高于 50%）
    - 当前 cash 占比应在 [min, max] 区间内，超出区间 → 调仓信号

    数字依据（liubo 2026-09-18 第十五轮反馈 "现金不低于 20%，不高于 50%"）：
    - 不低于 20%：常态下子弹不要打光（应对下一次全球性危机）
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
        """卡片展示用的区间字符串（例 "[20%, 50%]"）。"""
        return f"[{int(self.min_weight * 100)}%, {int(self.max_weight * 100)}%]"


@dataclass(frozen=True, slots=True)
class AllocationStrategy:
    """完整大类资产配置策略（三层结构）。

    - subclass_limits: 14 个子类的上限（防风险集中）
    - investment_targets: 4 个投资类超类的目标（不含现金）
    - cash_range: 现金仓位区间（子弹策略）

    注意：投资类目标之和通常 < 1.0（剩余部分由现金区间填充，不强制 = 100%）。
    例如默认策略 70% + 15% + 8% + 4% = 97%，剩余 3% 弹性来自现金区间下限之上的空间。
    """

    subclass_limits: tuple[SubclassLimit, ...]
    investment_targets: tuple[SuperCategoryTarget, ...]
    cash_range: CashRange

    def subclass_upper(self, subclass: SwensenClass) -> Decimal | None:
        """查某子类的上限（无 = None）。"""
        for limit in self.subclass_limits:
            if limit.subclass == subclass:
                return limit.upper
        return None

    def investment_target(self, category: SuperCategory) -> Decimal | None:
        """查某投资类超类的目标（无 = None；现金不在内，现金走 cash_range）。"""
        for t in self.investment_targets:
            if t.category == category:
                return t.target
        return None

    @property
    def total_investment_target(self) -> Decimal:
        """4 个投资类超类目标之和（应 < 1.0，剩余为现金区间填充）。"""
        return sum((t.target for t in self.investment_targets), Decimal("0"))


# 默认策略：Swensen/Yale endowment + liubo 70% 股激进版 + 现金区间 [20%, 50%]
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
# - CASH 子类上限 50%（第十五轮：跟随现金区间上限，作为硬约束）
#
# Layer 2a 目标依据（liubo 第十四轮反馈 "股票 70% 吧，是吧？... 肯定是以股票为主"）：
# - 股票 70%：liubo 明确选择激进版（高收益偏好，主要配置）
# - 债券 15%：Swensen 学术建议总债券 30%，激进版股票仓位挤出部分债券 → 折半到 15%
# - REITs 8%：Swensen 建议 15%，激进版 REITs 作为辅助类 → 折半到 8%
# - 商品 4%：黄金/能源作为通胀对冲，非 Swensen 明确推荐但常见配置
# 4 类合计 97%，剩余 3% 弹性来自现金区间下限之上的空间（不是硬目标）
#
# Layer 2b 现金区间依据（liubo 第十五轮反馈 "现金不低于 20%，不高于 50%"）：
# - 子弹下限 20%：常态下子弹不要打光（应对下一次全球性危机）
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
    investment_targets=(
        SuperCategoryTarget(SuperCategory.EQUITY, Decimal("0.70")),
        SuperCategoryTarget(SuperCategory.BOND, Decimal("0.15")),
        SuperCategoryTarget(SuperCategory.REIT, Decimal("0.08")),
        SuperCategoryTarget(SuperCategory.COMMODITY, Decimal("0.04")),
    ),
    cash_range=CashRange(
        min_weight=Decimal("0.20"),  # 子弹下限
        max_weight=Decimal("0.50"),  # 子弹上限
    ),
)


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
    "SuperCategoryTarget",
    "CashRange",
    "AllocationStrategy",
    "DEFAULT_STRATEGY",
    "compute_super_category_breakdown",
]
