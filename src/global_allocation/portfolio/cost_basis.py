"""大类资产配置组合 — 各基金买入总成本（CNY）。

来源：liubo 2026-09-20 / 2026-09-21 手工录入。
单位：人民币元（CNY）。
合计：307570 元（29 只有金额的）。

不在表里的基金（008505 / 004827 / 004137）：按现金 / 类现金处理，
不计入成本表（NAV ≈ 1，P&L 几乎为零）。

后续如果要接券商 API 自动同步流水，可以把这个文件替换成从
transactions 表聚合出来的总买入金额。但 MVP 阶段先 hardcode。
"""

from __future__ import annotations

from decimal import Decimal


# 29 只基金的累计买入总金额（CNY）— 2026-09-21 liubo 录入
COST_BASIS_BY_CODE: dict[str, Decimal] = {
    # A 股股票 (6) — 92500 元
    "013310": Decimal("20000"),  # 华夏科创创业50
    "022434": Decimal("10500"),  # 南方中证A500
    "008114": Decimal("50000"),  # 天弘中证红利低波动100
    "017644": Decimal("5000"),   # 博道中证1000指数增强
    "022424": Decimal("5000"),   # 广发中证A500
    "014532": Decimal("2000"),   # 易方达MSCI中国A50
    # 港股 (4) — 98010 元
    "004098": Decimal("50000"),  # 前海开源港股通股息率50强
    "013127": Decimal("25000"),  # 汇添富恒生科技
    "006809": Decimal("1010"),   # 泰康香港银行指数
    "014673": Decimal("22000"),  # 富国中证港股通互联网ETF发起式联接A
    # 美股股票 (9) — 14500 元
    "519981": Decimal("3620"),   # 长信标普100
    "018966": Decimal("2020"),   # 汇添富纳100
    "539001": Decimal("1000"),   # 建信纳100
    "017641": Decimal("50"),     # 摩根标普500
    "016452": Decimal("20"),     # 南方纳100
    "019524": Decimal("20"),     # 华泰柏瑞纳100
    "017730": Decimal("5300"),   # 嘉实全球产业升级
    "016664": Decimal("2370"),   # 天弘全球高端制造
    "006373": Decimal("100"),    # 国富全球科技互联
    # 国外发达市场股票 (1) — 4640 元
    "457001": Decimal("4640"),   # 国富亚洲机会
    # 新兴市场股票 (1) — 3910 元
    "378006": Decimal("3910"),   # 摩根全球新兴市场
    # 国内 REITs (1) — 5000 元
    "028277": Decimal("5000"),   # 华夏中证REITs全收益
    # 美国 REITs (1) — 5000 元
    "160140": Decimal("5000"),   # 南方道琼斯美国精选REIT
    # 国内利率债 (2 of 4) — 5500 元；008505/004827 当现金跳过
    "003547": Decimal("2000"),   # 鹏华丰禄
    "000931": Decimal("3500"),   # 国寿安保尊益信用纯债
    # 美债 (3) — 76000 元
    "100050": Decimal("6000"),   # 富国全球债券
    "007360": Decimal("55000"),  # 易方达中短期美元债
    "003385": Decimal("15000"),  # 工银全球美元债
    # 商品 (1) — 2510 元
    "000216": Decimal("2510"),   # 华安黄金ETF联接A
    # 现金 (1) — 004137 博时合惠货币B：当现金处理，不进 cost_basis 表
}


# 按现金 / 类现金处理的基金（不在 COST_BASIS_BY_CODE 里；NAV 接近 1，P&L 几乎为零）
CASH_LIKE_CODES: frozenset[str] = frozenset({
    "008505",  # 浙商中短债A — liubo 2026-09-21 确认当现金
    "004827",  # 平安中短债 — 同上
    "004137",  # 博时合惠货币B — 货币基金本来就是现金等价物
})


# 合计：307570 元（29 只有金额的）— 2026-09-21 liubo 录入时手算 + 自动验证
TOTAL_COST_CNY: Decimal = Decimal("307570")


def get_cost_basis(fund_code: str) -> Decimal | None:
    """查某只基金的累计买入总金额（CNY）。未知返回 None。"""
    return COST_BASIS_BY_CODE.get(fund_code)


def is_cash_like(fund_code: str) -> bool:
    """判断是否按现金 / 类现金处理（不进成本表，NAV≈1）。"""
    return fund_code in CASH_LIKE_CODES


__all__ = [
    "COST_BASIS_BY_CODE",
    "CASH_LIKE_CODES",
    "TOTAL_COST_CNY",
    "get_cost_basis",
    "is_cash_like",
]
