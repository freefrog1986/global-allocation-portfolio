"""持仓按 Swensen 大类资产分类（spec 097 第十九轮：大类资产配置 47 只）。

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
  - 保留新兴市场 1（378006 跟 MSCI Emerging Markets），liubo 确认是宽基
  - 457001 国富亚洲机会（MSCI AC Asia ex Japan）：liubo 2026-09-22 移到 ETF 轮动组合，
    不再算大类资产配置（实际持仓是发达+新兴混合，不属于"国外发达市场股票"子类）

- **第十九轮（2026-09-22 liubo）扩展大类资产配置**：
  - 加 022448 国泰中证A500ETF发起联接A + 007997 易方达年年恒秋一年定开债A
    （从红利策略组合移过来）
  - 解锁 008505 浙商中短债A + 004827 平安中短债（不再按现金）
  - 加 7 只新中债：004534/110017/009625/005690/400030/010942/008420
  - 补回 016452 南方纳100（第十八轮漏了）

  A 股 / 港股 / 美股 / 国外发达 / 新兴市场        ← 股票 5 子类（第二十二轮加港股 013127）
  国内 REITs / 美国 REITs                        ← REITs 2 子类
  国内利率债 / 美债                              ← 债券 2 子类（利率债扩到 23 只）
  商品 / 现金                                    ← 商品 + 现金 各 1 子类

MVP 用 hardcoded mapping 标 49 只已知基金（含 1 只按现金 004137）；
后续 spec 092 标的库可以把子类存到 fund_universe。
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


# 已知 48 只基金的 mapping（MVP hardcode；后续 spec 092 移到 fund_universe 表）
# 2026-09-22 liubo 拍板股权类只用宽基 ETF（spec 098 第十八轮）：
# - A 股宽基 5：A500 / 科创创业50 / A50 / 1000增强
# - 美股宽基 6：标普500 / 标普100 / 纳100（4 只跟踪同一指数）
# - 国外发达 1：MSCI AC Asia ex Japan
# - 新兴市场 1：MSCI Emerging Markets
# 第十九轮扩展（liubo 2026-09-22）：加 022448 A500 联接 + 7 只中债 +
# 解锁 008505/004827 + 移来 007997 + 补回 016452 = 36 → 47 只
# 第二十二轮（liubo 2026-09-29）：把 013127 汇添富恒生科技 ETF 联接发起式(QDII)A
# 从 ETF 轮动组合转过来，港股子类从 0 → 1（之前砍的 5 只港股去了红利/ETF 轮动组合）
SUBCLASS_BY_CODE: dict[str, SwensenClass] = {
    # A 股股票 (9 宽基) - 第十九轮加 022448 国泰A500 联接；2026-10-09 加 011612/019857/023414 3 只科创/创业宽基
    "013310": SwensenClass.CN_EQUITY,  # 华夏科创创业50
    "022434": SwensenClass.CN_EQUITY,  # 南方中证A500
    "017644": SwensenClass.CN_EQUITY,  # 博道中证1000指数增强
    "022424": SwensenClass.CN_EQUITY,  # 广发中证A500
    "014532": SwensenClass.CN_EQUITY,  # 易方达MSCI中国A50
    "022448": SwensenClass.CN_EQUITY,  # 国泰中证A500ETF发起联接A（第十九轮从红利策略移过来）
    # A 股宽基扩展 3 只（liubo 2026-10-09 拍板科创/创业类改 PS 估值）
    "011612": SwensenClass.CN_EQUITY,  # 华夏科创50ETF联接A
    "019857": SwensenClass.CN_EQUITY,  # 博时上证科创板100ETF联接A（2026-10-09 拍板换掉 020291，建仓 1 仓）
    "023414": SwensenClass.CN_EQUITY,  # 工银创业板50ETF联接A（2026-10-09 拍板换掉 160422，建仓 1 仓）
    # 港股 (1) - 2026-09-29 liubo 把 013127 从 ETF 轮动组合转过来，跟踪恒生科技 HSTECH
    "013127": SwensenClass.HK_EQUITY,  # 汇添富恒生科技 ETF 联接发起式(QDII)A（第二十二轮从 ETF 轮动组合转过来）
    # 美股股票 (7 宽基) - 519981 标普100 + 017641 标普500 + 5 只纳100
    # 2026-09-29 liubo 卖出 019524（华泰柏瑞纳 100 联接），8 → 7
    # 2026-09-29 liubo 确认 539001 是直接 QDII 场外（不是场内 ETF，天天基金可买）
    # 第二十轮（liubo 2026-09-29）：加 019172 摩根纳斯达克100指数(QDII)人民币A
    # 直接 QDII（非联接），替代联接作为新加仓渠道；DCA ¥10/天 → 1 仓目标
    # 第二十一轮（liubo 2026-09-29）：加 019441 万家纳斯达克100指数发起式(QDII)A — 双只备份
    "519981": SwensenClass.US_EQUITY,  # 长信标普100
    "018966": SwensenClass.US_EQUITY,  # 汇添富纳100（联接基金）
    "539001": SwensenClass.US_EQUITY,  # 建信纳斯达克100指数(QDII)A人民币（直接 QDII 场外）
    "017641": SwensenClass.US_EQUITY,  # 摩根标普500
    "016452": SwensenClass.US_EQUITY,  # 南方纳100（第十九轮补回）
    "019172": SwensenClass.US_EQUITY,  # 摩根纳斯达克100指数(QDII)人民币A（第二十轮加）
    "019441": SwensenClass.US_EQUITY,  # 万家纳斯达克100指数发起式(QDII)A（第二十一轮加）
    # 国外发达市场股票 (1) - liubo 2026-09-22 移 457001 到 ETF 轮动组合后为 0，
    # 2026-09-29 第二十四轮 liubo 把 000614 华安 DAX 联接 A 加回来（支付宝慧定投 250-1000 元/周，
    # 平均 500，目标累计 10000 CNY = 1 仓自动暂停，跟踪法兰克福 DAX 指数 = .GDAXI），
    # PE 16.90 / 分位 47.1% < 50% → BUILD
    "000614": SwensenClass.FOREIGN_DM_EQUITY,  # 华安德国(DAX)联接(QDII)A（第二十四轮加，慧定投凑 1 仓）
    # 新兴市场股票 (1) - MSCI Emerging Markets 总回报
    "378006": SwensenClass.EM_EQUITY,  # 摩根全球新兴市场
    # REITs (2) — lixinger 无指数估值数据，per-fund 表显示"数据缺失"
    "028277": SwensenClass.CN_REIT,  # 华夏中证REITs全收益
    "160140": SwensenClass.US_REIT,  # 南方道琼斯美国精选REIT
    # 国内利率债 (23) - 含原国内信用债 2 只（解锁）+ 第十九轮新 7 只 + 移来 1 只（007997）
    # 第十九轮解锁（不再按现金）：008505 / 004827
    "008505": SwensenClass.CN_GOV_BOND,  # 浙商中短债A（第十九轮解锁，不再按现金）
    "004827": SwensenClass.CN_GOV_BOND,  # 平安中短债（第十九轮解锁，不再按现金）
    "003547": SwensenClass.CN_GOV_BOND,  # 鹏华丰禄
    "000931": SwensenClass.CN_GOV_BOND,  # 国寿安保尊益信用纯债
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
    # 第十九轮从红利策略组合移过来
    "007997": SwensenClass.CN_GOV_BOND,  # 易方达年年恒秋一年定开债A
    # 第十九轮新增 7 只中债
    "004534": SwensenClass.CN_GOV_BOND,  # 汇添富双盈回报一年持有债A
    "110017": SwensenClass.CN_GOV_BOND,  # 易方达增强回报债券A
    "009625": SwensenClass.CN_GOV_BOND,  # 天弘中债3-5年政策性金融债指数发起A
    "005690": SwensenClass.CN_GOV_BOND,  # 中银安享债券A
    "400030": SwensenClass.CN_GOV_BOND,  # 东方添益债券
    "010942": SwensenClass.CN_GOV_BOND,  # 招商瑞乐6个月持有期混合A
    "008420": SwensenClass.CN_GOV_BOND,  # 广发招泰A
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
