"""大类资产配置策略（spec 097）。

策略是双层结构：
- **Layer 1（细粒度上限）**：14 个 SwensenClass 子类各设上限（不设目标，只设上限）—— 防止单一资产过度集中
- **Layer 2（粗粒度目标）**：5 个超类（股票/债券/REITs/商品/现金）设目标比例 —— 反映整体风险偏好

数字依据：
- Layer 1 上限：基于 David Swensen《Unconventional Success》（非凡的成功）+ Yale endowment 模型的子类比例，结合中国市场分散需求调整
- Layer 2 目标：liubo 70% 股票激进版（spec 097 第十四轮），剩余 30% 按 Swensen 学术建议分给债券/REITs/商品/现金

后续演进（liubo 2026-09-18 反馈）：
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

    EQUITY = "equity"  # 股票（7 子类）
    BOND = "bond"  # 债券（3 子类）
    REIT = "reit"  # REITs（2 子类）
    COMMODITY = "commodity"  # 商品（1 子类）
    CASH = "cash"  # 现金（1 子类）


SUPER_CATEGORY_DISPLAY_NAME: dict[SuperCategory, str] = {
    SuperCategory.EQUITY: "股票",
    SuperCategory.BOND: "债券",
    SuperCategory.REIT: "REITs",
    SuperCategory.COMMODITY: "商品",
    SuperCategory.CASH: "现金",
}


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
    """5 个超类的目标比例。

    target 是 0~1 的小数。展示时 ×100。
    """

    category: SuperCategory
    target: Decimal


@dataclass(frozen=True, slots=True)
class AllocationStrategy:
    """完整大类资产配置策略（双层结构）。

    - subclass_limits: 14 个子类的上限（防风险集中）
    - super_category_targets: 5 个超类的目标（整体风险偏好）
    """

    subclass_limits: tuple[SubclassLimit, ...]
    super_category_targets: tuple[SuperCategoryTarget, ...]

    def subclass_upper(self, subclass: SwensenClass) -> Decimal | None:
        """查某子类的上限（无 = None）。"""
        for limit in self.subclass_limits:
            if limit.subclass == subclass:
                return limit.upper
        return None

    def super_category_target(self, category: SuperCategory) -> Decimal | None:
        """查某超类的目标（无 = None）。"""
        for t in self.super_category_targets:
            if t.category == category:
                return t.target
        return None

    @property
    def total_super_target(self) -> Decimal:
        """5 个超类目标之和（应 = 1.0）。"""
        return sum((t.target for t in self.super_category_targets), Decimal("0"))


# 默认策略：Swensen/Yale endowment + liubo 70% 股激进版
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
# - REITs/商品/现金：上限 10-20%，辅助类配置
#
# Layer 2 目标依据（liubo 第十四轮反馈 "股票 70% 吧，是吧？... 肯定是以股票为主"）：
# - 股票 70%：liubo 明确选择激进版（高收益偏好，主要配置）
# - 债券 15%：Swensen 学术建议总债券 30%，激进版股票仓位挤出部分债券 → 折半到 15%
# - REITs 8%：Swensen 建议 15%，激进版 REITs 作为辅助类 → 折半到 8%
# - 商品 4%：黄金/能源作为通胀对冲，非 Swensen 明确推荐但常见配置
# - 现金 3%：流动性储备（Swensen 学术建议 0%，但中国市场实际需求 3% 应急）
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
        SubclassLimit(SwensenClass.CASH, Decimal("0.20")),
    ),
    super_category_targets=(
        SuperCategoryTarget(SuperCategory.EQUITY, Decimal("0.70")),
        SuperCategoryTarget(SuperCategory.BOND, Decimal("0.15")),
        SuperCategoryTarget(SuperCategory.REIT, Decimal("0.08")),
        SuperCategoryTarget(SuperCategory.COMMODITY, Decimal("0.04")),
        SuperCategoryTarget(SuperCategory.CASH, Decimal("0.03")),
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
    "SUBCLASS_TO_SUPER",
    "SubclassLimit",
    "SuperCategoryTarget",
    "AllocationStrategy",
    "DEFAULT_STRATEGY",
    "compute_super_category_breakdown",
]
