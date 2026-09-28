"""红利策略组合 — 2D 分类矩阵（市场 × 策略）。

liubo 2026-09-26 拍板：
- 第一轴 = 市场（A 股 / 港股 / A+H）
- 第二轴 = 策略（红利 / 红利+低波 / 红利+价值 / 自由现金流 / 高股息 / 主动基金）

每个 (市场, 策略) cell 是正交独立、可单独查询的；跟螺丝钉 21 只指数 universe + 11 只
当前红利基金一一映射。主动基金 022164 不在矩阵里，单独列示。

与 Swensen 大类资产配置（`breakdown.SUBCLASS_BY_CODE`）完全独立 ——
不同组合的代码、仓位、模块不混。
"""

from __future__ import annotations

from enum import Enum


class Market(str, Enum):
    """红利策略组合 — 市场维度（第一轴）。"""

    A_STOCK = "A 股"
    HK = "港股"
    A_H = "A+H"


class Strategy(str, Enum):
    """红利策略组合 — 策略维度（第二轴）。"""

    DIVIDEND = "红利"            # 纯红利，不带低波约束
    DIVIDEND_LOW_VOL = "红利+低波"  # 红利 + 低波动双重约束
    DIVIDEND_VALUE = "红利+价值"   # 红利 + 价值/基本面
    FREE_CASH_FLOW = "自由现金流"   # FCF 因子策略
    HIGH_DIVIDEND = "高股息"      # 高股息率策略（中证智选等）


# 2D 矩阵：MARKET_STRATEGY_CELLS[market][strategy] = {
#     "indices": 螺丝钉 universe 里的指数 display_name 列表
#     "fund_codes": 跟踪该 cell 的当前基金代码列表（空 list = 没持仓）
# }
#
# liubo 2026-09-26 拍板 v6：
# - 9 个有效 cell（3 市场 × 5 策略，去掉无指数/无基金的 cell）
# - 16 只螺丝钉指数（5 宽基 + 3 非红利消费被排除在外，不归类）
# - 10 只被动基金映射到 6 个 cell（022164 主动基金独立，不在矩阵里）
MARKET_STRATEGY_CELLS: dict[Market, dict[Strategy, dict[str, list[str]]]] = {
    Market.A_STOCK: {
        Strategy.DIVIDEND: {
            "indices": ["中证红利", "上证红利", "龙头红利", "红利机会"],
            "fund_codes": [],
        },
        Strategy.DIVIDEND_LOW_VOL: {
            "indices": ["中证红利低波动", "红利低波100", "沪深300红利低波动", "标普中国A股大盘红利低波50"],
            "fund_codes": ["008163", "005561", "007605", "008114"],
        },
        Strategy.DIVIDEND_VALUE: {
            "indices": ["基本面50", "300价值"],
            "fund_codes": [],
        },
        Strategy.FREE_CASH_FLOW: {
            "indices": ["自由现金流", "中证全指自由现金流"],
            "fund_codes": ["023917", "025958"],
        },
        Strategy.HIGH_DIVIDEND: {
            "indices": ["中证智选高股息策略"],
            "fund_codes": ["025682"],
        },
    },
    Market.HK: {
        Strategy.DIVIDEND: {
            "indices": ["港股红利"],
            "fund_codes": ["004098"],
        },
        Strategy.DIVIDEND_LOW_VOL: {
            "indices": ["恒生红利低波动"],
            "fund_codes": ["021457"],
        },
    },
    Market.A_H: {
        Strategy.DIVIDEND_LOW_VOL: {
            "indices": ["沪港深红利低波"],
            "fund_codes": ["007751"],
        },
    },
}


# 主动基金（不在 2D 矩阵里；业绩基准 ≠ 跟踪指数，PE 分位调仓不适用）
# liubo 2026-09-26 确认：022164 西部利得央企优选股票 A 是唯一主动
ACTIVE_FUND_CODES: frozenset[str] = frozenset({
    "022164",  # 业绩基准 = 中证中央企业综合指数（非跟踪）
})


def get_cell(market: Market, strategy: Strategy) -> dict[str, list[str]] | None:
    """查 (市场, 策略) cell 内容。cell 不存在返回 None。"""
    return MARKET_STRATEGY_CELLS.get(market, {}).get(strategy)


def get_cell_by_fund(fund_code: str) -> tuple[Market, Strategy] | None:
    """查某只基金所在的 (市场, 策略) cell。被动基金返回 cell；主动 / 未知返回 None。"""
    for market, strategies in MARKET_STRATEGY_CELLS.items():
        for strategy, cell in strategies.items():
            if fund_code in cell["fund_codes"]:
                return (market, strategy)
    return None


def get_cell_by_index(index_display_name: str) -> tuple[Market, Strategy] | None:
    """查某只螺丝钉指数所在的 (市场, 策略) cell。"""
    for market, strategies in MARKET_STRATEGY_CELLS.items():
        for strategy, cell in strategies.items():
            if index_display_name in cell["indices"]:
                return (market, strategy)
    return None


def all_cells() -> list[tuple[Market, Strategy]]:
    """列出所有有效 (市场, 策略) cell（按枚举顺序）。"""
    cells: list[tuple[Market, Strategy]] = []
    for market in Market:
        for strategy in Strategy:
            if get_cell(market, strategy) is not None:
                cells.append((market, strategy))
    return cells


def all_indices() -> list[str]:
    """列出矩阵里所有螺丝钉指数 display_name（按 cell 顺序）。"""
    out: list[str] = []
    for market in Market:
        for strategy in Strategy:
            cell = get_cell(market, strategy)
            if cell:
                out.extend(cell["indices"])
    return out


def all_fund_codes() -> list[str]:
    """列出矩阵里所有被动基金的代码（按 cell 顺序）。"""
    out: list[str] = []
    for market in Market:
        for strategy in Strategy:
            cell = get_cell(market, strategy)
            if cell:
                out.extend(cell["fund_codes"])
    return out


def is_active_fund(fund_code: str) -> bool:
    """判断是否主动基金（不在 2D 矩阵，PE 分位调仓不适用）。"""
    return fund_code in ACTIVE_FUND_CODES


__all__ = [
    "Market",
    "Strategy",
    "MARKET_STRATEGY_CELLS",
    "ACTIVE_FUND_CODES",
    "get_cell",
    "get_cell_by_fund",
    "get_cell_by_index",
    "all_cells",
    "all_indices",
    "all_fund_codes",
    "is_active_fund",
]