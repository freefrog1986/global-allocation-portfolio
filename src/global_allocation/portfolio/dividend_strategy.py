"""红利策略组合（与全球大类资产配置分开的独立组合，liubo 2026-09-20 建）。

跟 Swensen 大类资产配置框架**不混在一起**，所以单独一个模块。

2026-09-26 第二次扩展：从 2 只扩到 11 只，liubo 录入成本合计 343,000 CNY。
按底层指数族 / 策略主题分 6 类：

A 股红利低波 (4 只, 220,000 CNY) — 全部跟踪 A 股红利低波动系列指数
- 008163 南方标普中国A股大盘红利低波50ETF联接A → 标普中国A股大盘红利低波50指数
- 005561 创金合信中证红利低波动指数A → 中证红利低波动指数
- 007605 嘉实沪深300红利低波动ETF联接A → 沪深300红利低波动指数
- 008114 天弘中证红利低波动100联接A → 中证红利低波动100指数

A+H 红利成长低波 (1 只, 60,000 CNY) — 含港股通的跨市场
- 007751 景顺长城沪港深红利成长低波指数A → 中证沪港深红利成长低波动指数

港股高股息 (2 只, 51,000 CNY)
- 004098 前海开源港股通股息率50强 → 中证港股通高股息投资指数
- 021457 易方达港股通红利低波ETF联接A → 恒生港股通高股息低波动指数

自由现金流 (2 只, 6,000 CNY)
- 023917 华夏国证自由现金流ETF发起式联接A → 国证自由现金流指数
- 025958 南方中证全指自由现金流ETF联接A → 中证全指自由现金流指数

高股息策略 (1 只, 5,000 CNY)
- 025682 广发高股息ETF联接A → 中证智选高股息策略指数

主动-央企-量化 (1 只, 1,000 CNY) — **唯一主动管理基金**，盛丰衍量化选股
- 022164 西部利得央企优选股票A → 业绩基准 = 中证中央企业综合指数（非跟踪）

合计 11 只 / 343,000 CNY。后续 PE 分位调仓时，主动基金 022164 跳过（不能用业绩基准分位当信号）。

后续如果要接券商 API 自动同步流水，可以把这个文件替换成从 transactions 表聚合出来的总买入金额。但 MVP 阶段先 hardcode。
"""

from __future__ import annotations

from decimal import Decimal

from global_allocation.portfolio.category_matrix import Market, Strategy, is_active_fund


# 11 只基金 + 策略标签（MVP hardcode；后续可挪到独立数据库）
DIVIDEND_STRATEGY_BY_CODE: dict[str, str] = {
    # A 股红利低波 (4)
    "008163": "A 股红利低波",   # 标普中国A股大盘红利低波50
    "005561": "A 股红利低波",   # 中证红利低波动
    "007605": "A 股红利低波",   # 沪深300红利低波动
    "008114": "A 股红利低波",   # 中证红利低波动100
    # A+H 红利成长低波 (1)
    "007751": "A+H 红利成长低波",  # 中证沪港深红利成长低波动
    # 港股高股息 (2)
    "004098": "港股高股息",     # 中证港股通高股息投资
    "021457": "港股高股息",     # 恒生港股通高股息低波动
    # 自由现金流 (2)
    "023917": "自由现金流",     # 国证自由现金流
    "025958": "自由现金流",     # 中证全指自由现金流
    # 高股息策略 (1)
    "025682": "高股息策略",     # 中证智选高股息策略
    # 主动-央企-量化 (1) — 唯一主动基金，跳过 PE 分位调仓
    "022164": "主动-央企-量化",
}


# 跟踪的底层指数 / ETF（PE 分位调仓时用作指数代码查询）
TRACKING_INDEX_BY_CODE: dict[str, str] = {
    "008163": "标普中国A股大盘红利低波50指数",
    "005561": "中证红利低波动指数",
    "007605": "沪深300红利低波动指数",
    "008114": "中证红利低波动100指数",
    "007751": "中证沪港深红利成长低波动指数",
    "004098": "中证港股通高股息投资指数",
    "021457": "恒生港股通高股息低波动指数",
    "023917": "国证自由现金流指数",
    "025958": "中证全指自由现金流指数",
    "025682": "中证智选高股息策略指数",
    "022164": "中证中央企业综合指数",  # 业绩基准；非跟踪
}


# 主动管理基金清单（红利策略组合里 11 只只有 1 只主动，022164）
# 主动基金不能用业绩基准分位当 PE 分位调仓信号，必须跳过。
# 注：004098（前海开源港股通股息率50强）虽然不是联接基金而是主动管理，但保留在
# "港股高股息"分类下作为"参考 业绩基准 = 中证港股通高股息投资指数"使用，
# 实际调仓信号仍按指数 PE 分位跑（基金本身不严格跟踪指数）。
ACTIVE_FUND_CODES: frozenset[str] = frozenset({
    "022164",  # 西部利得央企优选股票 A — 盛丰衍量化选股
})


# 市场维度映射（liubo 2026-09-26 拍板的 2D 分类第一轴）
# 11 只里 10 只被动有市场归属；022164 主动基金 → None
MARKET_BY_CODE: dict[str, Market] = {
    "008163": Market.A_STOCK,
    "005561": Market.A_STOCK,
    "007605": Market.A_STOCK,
    "008114": Market.A_STOCK,
    "007751": Market.A_H,        # A+H 跨市场红利低波
    "004098": Market.HK,         # 港股高股息
    "021457": Market.HK,         # 港股红利低波
    "023917": Market.A_STOCK,    # 自由现金流（FCF）
    "025958": Market.A_STOCK,    # 自由现金流（中证全指）
    "025682": Market.A_STOCK,    # 高股息策略
    "022164": Market.A_STOCK,    # 主动-央企-量化（业绩基准 = 中证中央企业综合指数）
}


# 策略维度映射（liubo 2026-09-26 拍板的 2D 分类第二轴）
STRATEGY_BY_CODE: dict[str, Strategy] = {
    "008163": Strategy.DIVIDEND_LOW_VOL,
    "005561": Strategy.DIVIDEND_LOW_VOL,
    "007605": Strategy.DIVIDEND_LOW_VOL,
    "008114": Strategy.DIVIDEND_LOW_VOL,
    "007751": Strategy.DIVIDEND_LOW_VOL,
    "004098": Strategy.DIVIDEND,
    "021457": Strategy.DIVIDEND_LOW_VOL,
    "023917": Strategy.FREE_CASH_FLOW,
    "025958": Strategy.FREE_CASH_FLOW,
    "025682": Strategy.HIGH_DIVIDEND,
    # 022164 主动基金不在策略维度里（业绩基准 ≠ 跟踪指数）
}


def get_market(fund_code: str) -> Market | None:
    """查某只基金的市场归属（2D 分类第一轴）。未知 / 主动返回 None。"""
    return MARKET_BY_CODE.get(fund_code)


def get_strategy_enum(fund_code: str) -> Strategy | None:
    """查某只基金的策略归属（2D 分类第二轴）。未知 / 主动返回 None。"""
    return STRATEGY_BY_CODE.get(fund_code)


# 联接基金对应的母 ETF 代码（场内 ETF；非联接 / 主动基金不在此列）
# liubo 2026-09-26 查证：
# - 008163 → 515450  南方标普中国A股大盘红利低波50ETF
# - 007605 → 515300  嘉实沪深300红利低波动ETF
# - 025958 → 159232  南方中证全指自由现金流ETF
# - 025682 → 159207  广发中证智选高股息策略ETF
# - 004098 不收录：主动基金，无母 ETF
# 后续做"基金持仓 = 母 ETF 一级市场申赎套利"分析时可以用。
PARENT_EFFOCODE_BY_CODE: dict[str, str] = {
    "008163": "515450",   # 南方标普中国A股大盘红利低波50ETF
    "007605": "515300",   # 嘉实沪深300红利低波动ETF
    "025958": "159232",   # 南方中证全指自由现金流ETF
    "025682": "159207",   # 广发中证智选高股息策略ETF
}


# 11 只基金的累计买入总金额（CNY）— liubo 2026-09-26 截图 + 手工录入
DIVIDEND_COST_BASIS_BY_CODE: dict[str, Decimal] = {
    # A 股红利低波 (4) — 220,000
    "008163": Decimal("60000"),  # 南方标普红利低波 50 ETF 联接 A
    "005561": Decimal("60000"),  # 创金合信中证红利低波动指数 A
    "007605": Decimal("50000"),  # 嘉实沪深 300 红利低波动 ETF 联接 A
    "008114": Decimal("50000"),  # 天弘中证红利低波动 100 联接 A
    # A+H 红利成长低波 (1) — 60,000
    "007751": Decimal("60000"),  # 景顺长城沪港深红利成长低波指数 A
    # 港股高股息 (2) — 51,000
    "004098": Decimal("50000"),  # 前海开源港股通股息率 50 强
    "021457": Decimal("1000"),   # 易方达港股通红利低波 ETF 联接 A
    # 自由现金流 (2) — 6,000
    "023917": Decimal("5000"),   # 华夏国证自由现金流 ETF 发起式联接 A
    "025958": Decimal("1000"),   # 南方中证全指自由现金流 ETF 联接 A
    # 高股息策略 (1) — 5,000
    "025682": Decimal("5000"),   # 广发高股息 ETF 联接 A
    # 主动-央企-量化 (1) — 1,000
    "022164": Decimal("1000"),   # 西部利得央企优选股票 A（唯一主动基金）
}


# 合计：343,000 CNY（11 只红利策略组合基金；liubo 2026-09-26 录入）
# - A 股红利低波 4：220,000
# - A+H 红利成长低波 1：60,000
# - 港股高股息 2：51,000
# - 自由现金流 2：6,000
# - 高股息策略 1：5,000
# - 主动-央企-量化 1：1,000
DIVIDEND_TOTAL_COST_CNY: Decimal = sum(DIVIDEND_COST_BASIS_BY_CODE.values(), Decimal("0"))


# 显示名（飞书卡片 / 报告输出用）
DISPLAY_NAME: dict[str, str] = {
    code: f"{label}（{code}）"
    for code, label in DIVIDEND_STRATEGY_BY_CODE.items()
}


def get_strategy(fund_code: str) -> str | None:
    """查某只基金的策略标签。未知返回 None。"""
    return DIVIDEND_STRATEGY_BY_CODE.get(fund_code)


def get_tracking_index(fund_code: str) -> str | None:
    """查某只基金跟踪的底层指数。未知返回 None。"""
    return TRACKING_INDEX_BY_CODE.get(fund_code)


def get_cost_basis(fund_code: str) -> Decimal | None:
    """查某只基金的累计买入总金额（CNY）。未知返回 None。"""
    return DIVIDEND_COST_BASIS_BY_CODE.get(fund_code)


def is_active_fund(fund_code: str) -> bool:
    """判断是否主动管理基金（不能用业绩基准分位当 PE 分位调仓信号）。"""
    return fund_code in ACTIVE_FUND_CODES


def get_parent_etf_code(fund_code: str) -> str | None:
    """查联接基金对应的母 ETF 代码（场内代码）。主动基金 / 非联接返回 None。"""
    return PARENT_EFFOCODE_BY_CODE.get(fund_code)


__all__ = [
    "DIVIDEND_STRATEGY_BY_CODE",
    "TRACKING_INDEX_BY_CODE",
    "ACTIVE_FUND_CODES",
    "PARENT_EFFOCODE_BY_CODE",
    "MARKET_BY_CODE",
    "STRATEGY_BY_CODE",
    "DIVIDEND_COST_BASIS_BY_CODE",
    "DIVIDEND_TOTAL_COST_CNY",
    "DISPLAY_NAME",
    "get_strategy",
    "get_tracking_index",
    "get_cost_basis",
    "is_active_fund",
    "get_parent_etf_code",
    "get_market",
    "get_strategy_enum",
]