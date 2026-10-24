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
  - 457001 国富亚洲机会（MSCI AC Asia ex Japan）：liubo 2026-09-22 移到左侧布局组合，
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
# 从左侧布局组合转过来，港股子类从 0 → 1（之前砍的 5 只港股去了红利/左侧布局组合）
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
    # 港股 (2) - 2026-09-29 liubo 把 013127 从左侧布局组合转过来，跟踪恒生科技 HSTECH
    "013127": SwensenClass.HK_EQUITY,  # 汇添富恒生科技 ETF 联接发起式(QDII)A
    # 2026-10-25 liubo 转 4 只债基建仓 018200，跟踪恒生消费指数 HSCGSI
    "018200": SwensenClass.HK_EQUITY,  # 嘉实恒生消费ETF发起联接(QDII)A
    # 美股股票 (7 宽基) - 519981 标普100 + 017641 标普500 + 5 只纳100
    "519981": SwensenClass.US_EQUITY,  # 长信标普100
    "018966": SwensenClass.US_EQUITY,  # 汇添富纳100（联接基金）
    "539001": SwensenClass.US_EQUITY,  # 建信纳斯达克100指数(QDII)A人民币（直接 QDII 场外）
    "017641": SwensenClass.US_EQUITY,  # 摩根标普500
    "016452": SwensenClass.US_EQUITY,  # 南方纳100（第十九轮补回）
    "019172": SwensenClass.US_EQUITY,  # 摩根纳斯达克100指数(QDII)人民币A（第二十轮加）
    "019441": SwensenClass.US_EQUITY,  # 万家纳斯达克100指数发起式(QDII)A（第二十一轮加）
    # 国外发达市场股票 (1)
    "000614": SwensenClass.FOREIGN_DM_EQUITY,  # 华安德国(DAX)联接(QDII)A
    # 新兴市场股票 (1) - MSCI Emerging Markets
    "378006": SwensenClass.EM_EQUITY,  # 摩根全球新兴市场
    # REITs (2) — lixinger 无指数估值数据
    "028277": SwensenClass.CN_REIT,  # 华夏中证REITs全收益
    "160140": SwensenClass.US_REIT,  # 南方道琼斯美国精选REIT
    # 国内利率债 (23)
    "008505": SwensenClass.CN_GOV_BOND,  # 浙商中短债A
    "004827": SwensenClass.CN_GOV_BOND,  # 平安中短债
    "003547": SwensenClass.CN_GOV_BOND,  # 鹏华丰禄
    "000931": SwensenClass.CN_GOV_BOND,  # 国寿安保尊益信用纯债
    "007520": SwensenClass.CN_GOV_BOND,  # 富安达富利纯债A
    "006829": SwensenClass.CN_GOV_BOND,  # 鹏扬利沣短债A
    "008333": SwensenClass.CN_GOV_BOND,  # 景顺长城弘利39个月定开债
    "015736": SwensenClass.CN_GOV_BOND,  # 长盛盛裕纯债D
    "017837": SwensenClass.CN_GOV_BOND,  # 博时中债7-10年政金债指数A
    "001512": SwensenClass.CN_GOV_BOND,  # 易方达中债3-5年期国债指数
    "485119": SwensenClass.CN_GOV_BOND,  # 工银信用纯债债券A
    "010653": SwensenClass.CN_GOV_BOND,  # 农银汇理金玉债券
    "202103": SwensenClass.CN_GOV_BOND,  # 南方多利增强债券A
    "519753": SwensenClass.CN_GOV_BOND,  # 交银安心收益债券A
    "002490": SwensenClass.CN_GOV_BOND,  # 金鹰元祺债券A
    "010742": SwensenClass.CN_GOV_BOND,  # 南方宁悦一年持有期混合A
    "007997": SwensenClass.CN_GOV_BOND,  # 易方达年年恒秋一年定开债A
    "004534": SwensenClass.CN_GOV_BOND,  # 汇添富双盈回报一年持有债A
    "110017": SwensenClass.CN_GOV_BOND,  # 易方达增强回报债券A
    "009625": SwensenClass.CN_GOV_BOND,  # 天弘中债3-5年政策性金融债指数发起A
    "005690": SwensenClass.CN_GOV_BOND,  # 中银安享债券A
    "400030": SwensenClass.CN_GOV_BOND,  # 东方添益债券
    "010942": SwensenClass.CN_GOV_BOND,  # 招商瑞乐6个月持有期混合A
    "008420": SwensenClass.CN_GOV_BOND,  # 广发招泰A
    # 美债 (6)
    "100050": SwensenClass.US_BOND,  # 富国全球债券
    "007360": SwensenClass.US_BOND,  # 易方达中短期美元债
    "003385": SwensenClass.US_BOND,  # 工银全球美元债
    "002286": SwensenClass.US_BOND,  # 中银美元债债券(QDII)人民币A
    "000290": SwensenClass.US_BOND,  # 鹏华全球高收益债(QDII)
    "501300": SwensenClass.US_BOND,  # 海富通全球收益债券人民币
    # 商品 (1)
    "000216": SwensenClass.COMMODITY,  # 华安黄金ETF联接A
    # 现金 (1)
    "004137": SwensenClass.CASH,  # 博时合惠货币B
}


def get_subclass(fund_code: str) -> SwensenClass | None:
    """查某只基金属于哪个子类。未知返回 None（不影响其它基金的统计）。"""
    return SUBCLASS_BY_CODE.get(fund_code)


# ─── FUND_NAME_BY_CODE — 基金 code → 中文名（调仓纪录渲染 / 卡片基金列显示）───
#
# 第二十二轮新增（liubo 2026-10-17 反馈"基金列不要用 code"）：调仓纪录的"基金"列改用
# 中文名（"汇添富恒生科技 ETF 联接发起式(QDII)A" 而不是 "013127"）。
#
# 数据来源：
# - cost_basis.py 注释（最完整 — 含每只基金的简称/全称/历史转换）
# - breakdown.py 注释（验证一致）
# - 历史 commit body（liubo 写的 `- 0xxxxx 基金名` 行）—— 020291 / 160422 / 019524
#   已不在 cost_basis.py，但调仓纪录历史行需要显示名称 → 单独收录
#
# 命名约定：保留 liubo 在 commit body 写的中文全称（带括号修饰），
#           去掉首个空格、调整标点（避免过宽）
#
# 注意：历史 019524 已卖出，020291 / 160422 已替换 — 这 3 只不进 SUBCLASS_BY_CODE，
#       但 FUND_NAME_BY_CODE 仍保留以便历史调仓行能正确显示基金名。
FUND_NAME_BY_CODE: dict[str, str] = {
    # A 股股票 (9 宽基)
    "013310": "华夏科创创业50",
    "022434": "南方中证A500",
    "017644": "博道中证1000指数增强",
    "022424": "广发中证A500",
    "014532": "易方达MSCI中国A50",
    "022448": "国泰中证A500ETF发起联接A",
    "011612": "华夏科创50ETF联接A",
    "019857": "博时上证科创板100ETF联接A",
    "023414": "工银创业板50ETF联接A",
    # 港股 (2)
    "013127": "汇添富恒生科技 ETF 联接发起式(QDII)A",
    "018200": "嘉实恒生消费ETF发起联接(QDII)A",  # 2026-10-25 转 4 只债基建仓 1 仓
    # 美股股票 (7 宽基)
    "519981": "长信标普100",
    "018966": "汇添富纳100",
    "539001": "建信纳斯达克100指数(QDII)A人民币",
    "017641": "摩根标普500",
    "016452": "南方纳100",
    "019172": "摩根纳斯达克100指数(QDII)人民币A",
    "019441": "万家纳斯达克100指数发起式(QDII)A",
    # 国外发达 (1)
    "000614": "华安德国(DAX)联接(QDII)A",
    # 新兴市场 (1)
    "378006": "摩根全球新兴市场",
    # REITs (2)
    "028277": "华夏中证REITs全收益",
    "160140": "南方道琼斯美国精选REIT",
    # 国内利率债 (23)
    "008505": "浙商中短债A",
    "004827": "平安中短债",
    "003547": "鹏华丰禄",
    "000931": "国寿安保尊益信用纯债",
    "007520": "富安达富利纯债A",
    "006829": "鹏扬利沣短债A",
    "008333": "景顺长城弘利39个月定开债",
    "015736": "长盛盛裕纯债D",
    "017837": "博时中债7-10年政金债指数A",
    "001512": "易方达中债3-5年期国债指数",
    "485119": "工银信用纯债债券A",
    "010653": "农银汇理金玉债券",
    "202103": "南方多利增强债券A",
    "519753": "交银安心收益债券A",
    "002490": "金鹰元祺债券A",
    "010742": "南方宁悦一年持有期混合A",
    "007997": "易方达年年恒秋一年定开债A",
    "004534": "汇添富双盈回报一年持有债A",
    "110017": "易方达增强回报债券A",
    "009625": "天弘中债3-5年政策性金融债指数发起A",
    "005690": "中银安享债券A",
    "400030": "东方添益债券",
    "010942": "招商瑞乐6个月持有期混合A",
    "008420": "广发招泰A",
    # 美债 (6)
    "100050": "富国全球债券",
    "007360": "易方达中短期美元债",
    "003385": "工银全球美元债",
    "002286": "中银美元债债券(QDII)人民币A",
    "000290": "鹏华全球高收益债(QDII)",
    "501300": "海富通全球收益债券人民币",
    # 商品 (1)
    "000216": "华安黄金ETF联接A",
    # 现金 (1)
    "004137": "博时合惠货币B",
    # ─── 历史已替换/已卖出的基金（不在 SUBCLASS_BY_CODE，但调仓纪录历史行仍要显示名）───
    "019524": "华泰柏瑞纳 100 联接",        # 2026-09-29 卖出
    "020291": "华夏科创100ETF联接A",        # 2026-10-09 被 019857 替换
    "160422": "华安创业板50ETF联接A",       # 2026-10-09 被 023414 替换
    # ─── 跨组合（左侧布局组合里也出现过）───
    "010989": "南方中证房地产 ETF 联接 E",  # 2026-09-30 加到左侧布局组合（房地产类别）
    "004098": "港股 ETF 联接（已不在大类资产配置）",  # 第十八轮砍
    "006809": "港股 ETF 联接（已不在大类资产配置）",  # 第十八轮砍
    "014673": "港股 ETF 联接（已不在大类资产配置）",  # 第十八轮砍
    "005561": "红利低波（已不在大类资产配置）",         # 第十八轮砍
    "007605": "红利低波（已不在大类资产配置）",         # 第十八轮砍
    "008114": "红利低波（已不在大类资产配置）",         # 第十八轮砍
}


def get_fund_name(fund_code: str) -> str:
    """查某只基金的中文名。未知返回 fallback "code 0xxxxx"（每只基金一定有 code，不会是 "—"）。

    为什么 fallback 用 code 而不是 "—"（liubo 2026-10-17 反馈"每只基金都有 code，没有 -"）：
    - 解析 commit body 时偶尔遇到 SUBCLASS/FUND_NAME 都没覆盖到的 6 位 code
      （罕见：新买入但还没录入 breakdown 的基金）
    - fallback 用 code 让用户至少能定位基金；不编造中文名
    """
    if fund_code in FUND_NAME_BY_CODE:
        return FUND_NAME_BY_CODE[fund_code]
    return f"基金 {fund_code}"


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
    "FUND_NAME_BY_CODE",
    "BreakdownRow",
    "get_subclass",
    "get_fund_name",
    "compute_breakdown",
]
