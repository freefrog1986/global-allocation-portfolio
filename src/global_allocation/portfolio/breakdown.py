"""持仓按 Swensen 大类资产分类（spec 097 第十七轮：11 子类）。

参照大卫·史文森《Unconventional Success》的"独立回报来源"框架：
- 不按"股票/债券/现金"二分，而是按真正的 risk premium 拆开
- 中国投资者适配版：港股从「国外发达市场」里单独拆出来
- 第十七轮精简到 11 个子类（删了欧洲发达/亚洲发达/全球主题/国内信用债，
  合并出「国外发达市场股票」，把国内信用债的基金并到国内利率债）

  A 股 / 港股 / 美股 / 国外发达 / 新兴市场  ← 股票 5 子类
  国内 REITs / 美国 REITs                    ← REITs 2 子类
  国内利率债 / 美债                          ← 债券 2 子类
  商品 / 现金                                ← 商品 + 现金 各 1 子类

MVP 用 hardcoded mapping 标 31 只已知基金；后续 spec 092 标的库可以把子类存到 fund_universe。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import TypedDict

from global_allocation.portfolio.journal import PortfolioJournal


@dataclass(frozen=True, slots=True)
class _ClassBucket:
    """单个子类在 compute_breakdown 里的中间聚合结构。"""

    count: int = 0
    value: Decimal = Decimal("0")


class BreakdownRow(TypedDict):
    """compute_breakdown 的每行结果字段（dict 形式以兼容 JSON / 飞书表格）。"""

    subclass: SwensenClass
    display_name: str
    count: int
    value: Decimal
    weight: Decimal


class SwensenClass(str, Enum):
    """11 个子类（Swensen 框架 + 中国版适配，spec 097 第十七轮精简）。

    第十七轮变更：
    - 删 GLOBAL_THEMED_EQUITY（3 只基金并入 US_EQUITY）
    - 删 EU_EQUITY + ASIA_DM_EQUITY，合并成 FOREIGN_DM_EQUITY
    - 删 CN_CREDIT_BOND（斯文森说信用债拿不到阿尔法，不投了；4 只基金并入 CN_GOV_BOND）
    """

    CN_EQUITY = "cn_equity"  # A 股股票
    HK_EQUITY = "hk_equity"  # 港股
    US_EQUITY = "us_equity"  # 美股股票（含原全球主题基金）
    FOREIGN_DM_EQUITY = "foreign_dm_equity"  # 国外发达市场股票（欧洲 + 日/台/韩等合并）
    EM_EQUITY = "em_equity"  # 新兴市场股票

    CN_REIT = "cn_reit"  # 国内 REITs
    US_REIT = "us_reit"  # 美国 REITs

    CN_GOV_BOND = "cn_gov_bond"  # 国内利率债（含原国内信用债基金）
    US_BOND = "us_bond"  # 美债（USD 计价债）

    COMMODITY = "commodity"  # 商品（黄金 / 能源）
    CASH = "cash"  # 现金（货基）


# 显示名（飞书表格用）
DISPLAY_NAME: dict[SwensenClass, str] = {
    SwensenClass.CN_EQUITY: "A 股股票",
    SwensenClass.HK_EQUITY: "港股",
    SwensenClass.US_EQUITY: "美股股票",
    SwensenClass.FOREIGN_DM_EQUITY: "国外发达市场股票",
    SwensenClass.EM_EQUITY: "新兴市场股票",
    SwensenClass.CN_REIT: "国内 REITs",
    SwensenClass.US_REIT: "美国 REITs",
    SwensenClass.CN_GOV_BOND: "国内利率债",
    SwensenClass.US_BOND: "美债",
    SwensenClass.COMMODITY: "商品",
    SwensenClass.CASH: "现金",
}


# 已知 31 只基金的 mapping（MVP hardcode；后续 spec 092 移到 fund_universe 表）
SUBCLASS_BY_CODE: dict[str, SwensenClass] = {
    # A 股股票 (6)
    "013310": SwensenClass.CN_EQUITY,  # 华夏科创创业50
    "022434": SwensenClass.CN_EQUITY,  # 南方中证A500
    "008114": SwensenClass.CN_EQUITY,  # 天弘中证红利低波动100
    "017644": SwensenClass.CN_EQUITY,  # 博道中证1000指数增强
    "022424": SwensenClass.CN_EQUITY,  # 广发中证A500
    "014532": SwensenClass.CN_EQUITY,  # 易方达MSCI中国A50
    # 港股 (3)
    "004098": SwensenClass.HK_EQUITY,  # 前海开源港股通股息率50强
    "013127": SwensenClass.HK_EQUITY,  # 汇添富恒生科技
    "006809": SwensenClass.HK_EQUITY,  # 泰康香港银行指数
    # 美股股票 (9) - 纯纳100/标普 + 原全球主题 3 只
    "519981": SwensenClass.US_EQUITY,  # 长信标普100
    "018966": SwensenClass.US_EQUITY,  # 汇添富纳100
    "539001": SwensenClass.US_EQUITY,  # 建信纳100
    "017641": SwensenClass.US_EQUITY,  # 摩根标普500
    "016452": SwensenClass.US_EQUITY,  # 南方纳100
    "019524": SwensenClass.US_EQUITY,  # 华泰柏瑞纳100
    "017730": SwensenClass.US_EQUITY,  # 嘉实全球产业升级（第十七轮：并入美股）
    "016664": SwensenClass.US_EQUITY,  # 天弘全球高端制造（第十七轮：并入美股）
    "006373": SwensenClass.US_EQUITY,  # 国富全球科技互联（第十七轮：并入美股）
    # 国外发达市场股票 (1) - 原亚洲发达市场，欧洲+日台韩合并
    "457001": SwensenClass.FOREIGN_DM_EQUITY,  # 国富亚洲机会（业绩比较基准：MSCI AC Asia ex Japan 净总收益）
    # 新兴市场股票 (1)
    "378006": SwensenClass.EM_EQUITY,  # 摩根全球新兴市场（业绩比较基准：MSCI Emerging Markets 总回报）
    # REITs (2) — lixinger 无指数估值数据，per-fund 表显示"数据缺失"
    "028277": SwensenClass.CN_REIT,  # 华夏中证REITs全收益
    "160140": SwensenClass.US_REIT,  # 南方道琼斯美国精选REIT
    # 国内利率债 (7) - 含原国内信用债 4 只（第十七轮合并，统计口径不变）
    "008505": SwensenClass.CN_GOV_BOND,  # 浙商中短债A（原信用债）
    "004827": SwensenClass.CN_GOV_BOND,  # 平安中短债（原信用债）
    "003547": SwensenClass.CN_GOV_BOND,  # 鹏华丰禄（原信用债）
    "000931": SwensenClass.CN_GOV_BOND,  # 国寿安保尊益信用纯债（原信用债）
    # 美债 (3) - 全 USD 计价
    "100050": SwensenClass.US_BOND,  # 富国全球债券（实际 100% 美国国债）
    "007360": SwensenClass.US_BOND,  # 易方达中短期美元债
    "003385": SwensenClass.US_BOND,  # 工银全球美元债
    # 商品 (1)
    "000216": SwensenClass.COMMODITY,  # 华安黄金ETF联接A
    # 现金 (1)
    "004137": SwensenClass.CASH,  # 博时合惠货币B
}


def get_subclass(fund_code: str) -> SwensenClass | None:
    """查某只基金属于哪个子类。未知返回 None（不影响其它基金的统计）。"""
    return SUBCLASS_BY_CODE.get(fund_code)


def compute_breakdown(
    journal: PortfolioJournal,
) -> list[BreakdownRow]:
    """算各大类资产的占比。

    Returns:
        list of BreakdownRow（TypedDict）, 每个 dict 含:
          - subclass: SwensenClass 枚举
          - display_name: str（中文显示名）
          - count: int（基金数）
          - value: Decimal（市值）
          - weight: Decimal（占总市值比例，0~1）
        按 SwensenClass 枚举顺序返回（11 类，空的也包含）。
    """
    holdings = journal.compute_holdings()
    total_value = sum(
        (h.market_value for h in holdings if h.market_value is not None),
        Decimal("0"),
    )

    by_class: dict[SwensenClass, _ClassBucket] = defaultdict(_ClassBucket)
    for h in holdings:
        sub = get_subclass(h.fund.code)
        if sub is None or h.market_value is None:
            continue
        bucket = by_class[sub]
        by_class[sub] = _ClassBucket(
            count=bucket.count + 1,
            value=bucket.value + h.market_value,
        )

    result: list[BreakdownRow] = []
    for sub in SwensenClass:
        bucket = by_class.get(sub, _ClassBucket())
        weight = (bucket.value / total_value) if total_value > 0 else Decimal("0")
        result.append(
            BreakdownRow(
                subclass=sub,
                display_name=DISPLAY_NAME[sub],
                count=bucket.count,
                value=bucket.value,
                weight=weight,
            )
        )
    return result


__all__ = [
    "SwensenClass",
    "DISPLAY_NAME",
    "SUBCLASS_BY_CODE",
    "BreakdownRow",
    "get_subclass",
    "compute_breakdown",
]
