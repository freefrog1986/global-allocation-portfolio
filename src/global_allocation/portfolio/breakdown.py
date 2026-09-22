"""持仓按 Swensen 大类资产分类（spec 097 第十八轮：股权只留宽基，47→36）。

参照大卫·史文森《Unconventional Success》的"独立回报来源"框架：
- 不按"股票/债券/现金"二分，而是按真正的 risk premium 拆开
- 中国投资者适配版：港股从「国外发达市场」里单独拆出来
- 第十七轮精简到 11 个子类（删了欧洲发达/亚洲发达/全球主题/国内信用债，
  合并出「国外发达市场股票」，把国内信用债的基金并到国内利率债）
- **第十八轮（2026-09-22 liubo 拍板）砍 11 只非宽基股权类**：
  - 港股 5（004098/013127/006809/014673/016495）— 不投港股
  - 美股 3 QDII 主题（017730/016664/006373）— 全球产业升级 / 全球高端制造 /
    全球科技互联都是行业/主题基金，不算宽基
  - A 股 3 红利低波（005561/007605/008114）— 红利低波不算宽基指数
  - 保留国外发达 1（457001 跟 MSCI AC Asia ex Japan）+ 新兴市场 1（378006 跟
    MSCI Emerging Markets），liubo 确认这俩是宽基

  A 股 / 美股 / 国外发达 / 新兴市场              ← 股票 4 子类（砍了港股）
  国内 REITs / 美国 REITs                        ← REITs 2 子类
  国内利率债 / 美债                              ← 债券 2 子类
  商品 / 现金                                    ← 商品 + 现金 各 1 子类

MVP 用 hardcoded mapping 标 36 只已知基金；后续 spec 092 标的库可以把子类存到 fund_universe。
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


# 已知 36 只基金的 mapping（MVP hardcode；后续 spec 092 移到 fund_universe 表）
# 2026-09-22 liubo 拍板股权类只用宽基 ETF（spec 098 第十八轮）：
# - A 股宽基 5：A500 / 科创创业50 / A50 / 1000增强
# - 美股宽基 6：标普500 / 标普100 / 纳100（4 只跟踪同一指数）
# - 国外发达 1：MSCI AC Asia ex Japan
# - 新兴市场 1：MSCI Emerging Markets
# 砍 11 只非宽基（详见 breakdown 47→36 docstring）
SUBCLASS_BY_CODE: dict[str, SwensenClass] = {
    # A 股股票 (5 宽基)
    "013310": SwensenClass.CN_EQUITY,  # 华夏科创创业50
    "022434": SwensenClass.CN_EQUITY,  # 南方中证A500
    "017644": SwensenClass.CN_EQUITY,  # 博道中证1000指数增强
    "022424": SwensenClass.CN_EQUITY,  # 广发中证A500
    "014532": SwensenClass.CN_EQUITY,  # 易方达MSCI中国A50
    # 港股 (0) - liubo 2026-09-22 砍：不投港股
    # 美股股票 (6 宽基) - 51781 标普100 + 017641 标普500 + 4 只纳100
    "519981": SwensenClass.US_EQUITY,  # 长信标普100
    "018966": SwensenClass.US_EQUITY,  # 汇添富纳100
    "539001": SwensenClass.US_EQUITY,  # 建信纳100
    "017641": SwensenClass.US_EQUITY,  # 摩根标普500
    "016452": SwensenClass.US_EQUITY,  # 南方纳100
    "019524": SwensenClass.US_EQUITY,  # 华泰柏瑞纳100
    # 国外发达市场股票 (1) - MSCI AC Asia ex Japan 净总收益
    "457001": SwensenClass.FOREIGN_DM_EQUITY,  # 国富亚洲机会
    # 新兴市场股票 (1) - MSCI Emerging Markets 总回报
    "378006": SwensenClass.EM_EQUITY,  # 摩根全球新兴市场
    # REITs (2) — lixinger 无指数估值数据，per-fund 表显示"数据缺失"
    "028277": SwensenClass.CN_REIT,  # 华夏中证REITs全收益
    "160140": SwensenClass.US_REIT,  # 南方道琼斯美国精选REIT
    # 国内利率债 (16) - 含原国内信用债 4 只 + liubo 2026-09-22 截图新增 12 只
    # （含混合债基 010742 按 liubo 确认归 CN_GOV_BOND）
    "008505": SwensenClass.CN_GOV_BOND,  # 浙商中短债A（原信用债）
    "004827": SwensenClass.CN_GOV_BOND,  # 平安中短债（原信用债）
    "003547": SwensenClass.CN_GOV_BOND,  # 鹏华丰禄（原信用债）
    "000931": SwensenClass.CN_GOV_BOND,  # 国寿安保尊益信用纯债（原信用债）
    "007520": SwensenClass.CN_GOV_BOND,  # 富安达富利纯债A（liubo 2026-09-22）
    "006829": SwensenClass.CN_GOV_BOND,  # 鹏扬利沣短债A（liubo 2026-09-22）
    "008333": SwensenClass.CN_GOV_BOND,  # 景顺长城弘利39个月定开债（liubo 2026-09-22）
    "015736": SwensenClass.CN_GOV_BOND,  # 长盛盛裕纯债D（liubo 2026-09-22）
    "017837": SwensenClass.CN_GOV_BOND,  # 博时中债7-10年政金债指数A（liubo 2026-09-22）
    "001512": SwensenClass.CN_GOV_BOND,  # 易方达中债3-5年期国债指数（liubo 2026-09-22）
    "485119": SwensenClass.CN_GOV_BOND,  # 工银信用纯债债券A（liubo 2026-09-22）
    "010653": SwensenClass.CN_GOV_BOND,  # 农银汇理金玉债券（liubo 2026-09-22，9-24 分红）
    "202103": SwensenClass.CN_GOV_BOND,  # 南方多利增强债券A（liubo 2026-09-22）
    "519753": SwensenClass.CN_GOV_BOND,  # 交银安心收益债券A（liubo 2026-09-22）
    "002490": SwensenClass.CN_GOV_BOND,  # 金鹰元祺债券A（liubo 2026-09-22）
    "010742": SwensenClass.CN_GOV_BOND,  # 南方宁悦一年持有期混合A（liubo 2026-09-22 确认归利率债）
    # 美债 (6) - 全 USD 计价 + liubo 2026-09-22 截图新增 3 只 QDII 全球债
    "100050": SwensenClass.US_BOND,  # 富国全球债券（实际 100% 美国国债）
    "007360": SwensenClass.US_BOND,  # 易方达中短期美元债
    "003385": SwensenClass.US_BOND,  # 工银全球美元债
    "002286": SwensenClass.US_BOND,  # 中银美元债债券(QDII)人民币A（liubo 2026-09-22 确认归美债）
    "000290": SwensenClass.US_BOND,  # 鹏华全球高收益债(QDII)（liubo 2026-09-22）
    "501300": SwensenClass.US_BOND,  # 海富通全球收益债券人民币（liubo 2026-09-22）
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
