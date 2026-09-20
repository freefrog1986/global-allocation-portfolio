"""红利策略组合（与全球大类资产配置分开的独立组合，liubo 2026-09-20）。

跟 Swensen 大类资产配置框架**不混在一起**，所以单独一个模块。
4 只基金覆盖：A500 宽基 + 短债 + 自由现金流 + 高股息。

后续这块有自己的成本/估值/再平衡规则就加在这里。
"""

from __future__ import annotations


# 4 只已知基金的策略标签（MVP hardcode；后续可挪到独立数据库）
DIVIDEND_STRATEGY_BY_CODE: dict[str, str] = {
    "022448": "A500宽基",        # 国泰中证A500ETF发起联接A
    "007997": "短债",            # 易方达年年恒秋一年定开债A
    "023917": "自由现金流",       # 华夏国证自由现金流ETF发起式联接A
    "025682": "高股息",          # 广发高股息ETF联接A
}


# 显示名（飞书卡片 / 报告输出用）
DISPLAY_NAME: dict[str, str] = {
    code: f"{label}（{code}）"
    for code, label in DIVIDEND_STRATEGY_BY_CODE.items()
}


def get_strategy(fund_code: str) -> str | None:
    """查某只基金的策略标签（"A500宽基" / "短债" / "自由现金流" / "高股息"）。未知返回 None。"""
    return DIVIDEND_STRATEGY_BY_CODE.get(fund_code)


__all__ = [
    "DIVIDEND_STRATEGY_BY_CODE",
    "DISPLAY_NAME",
    "get_strategy",
]
