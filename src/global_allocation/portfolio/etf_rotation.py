"""ETF 轮动策略组合（独立模块，liubo 2026-09-22 建，2026-09-28 扩到 9 只）。

跟 Swensen 大类资产配置 + 红利策略**都不混在一起**，单独一个模块。

按策略标签分 4 类（liubo 2026-09-28 拍板）：

港股科技互联网（4 只，QDII/港股通 ETF 联接，跟踪指数基金）— 合计 78,000
- 006327 易方达中证海外互联网50ETF联接(QDII)A → 中证海外互联网 50 指数
- 013127 汇添富恒生科技ETF联接发起式(QDII)A → 恒生科技指数
- 014673 富国中证港股通互联网ETF发起式联接A → 中证港股通互联网指数
- 016495 景顺长城中证港股通科技ETF发起联接A → 中证港股通科技指数

港股银行（1 只，QDII）— 1,010
- 006809 泰康香港银行指数A → 港股银行指数（HSSI/HK银行）

亚太除日本（1 只，主动管理 QDII）— 4,640
- 457001 国富亚洲机会股票(QDII)A → 业绩基准 = MSCI AC Asia ex Japan

全球科技主动（3 只，QDII 主动管理主题基金）— 合计 7,770
- 016664 天弘全球高端制造混合(QDII)A → 全球高端制造主题
- 006373 国富全球科技互联混合(QDII)人民币A → 全球科技互联主题
- 017730 嘉实全球产业升级股票发起式(QDII)A → 全球产业升级主题

合计 9 只 / 91,420 CNY（2026-09-28 liubo 给齐所有准确成本）。

成本历史：
- 457001 = 4,640（旧 module 值；liubo 2026-09-28 确认）
- 013127/016495（liubo 2026-09-28 第二轮确认）
- 其余 6 只 = 截图「资产 - 持仓收益」估算（精度 ±10 元）→ 2026-09-28 全部由 liubo 校准

P&L 口径（重要，liubo 2026-09-28 拍板）：
- **真实 P&L = 当前总资产 - 累计成本**
- 不要用券商 app「持仓收益」 — app 算的是「组合成立日之后的盈亏」，漏算
  成立前的累计买入（liubo 在建「ETF 轮动组合」分组前就已经买入了多只）。
- 2026-09-28 snapshot：总资产 85,460.41 - 累计成本 91,420 = -5,959.59 CNY (-6.52%)
- 见 memory/project_brokerage_pnl.md

注意：模块名虽然叫"ETF 轮动"，但 4 只是 QDII 主动管理基金（亚太 + 全球科技 3 只）；
"轮动"指按动量/估值信号轮换持有标的；标的本身不限于纯 ETF。

后续这块有自己的成本/估值/再平衡规则就加在这里。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal


# 已知基金 + 策略标签（MVP hardcode；后续可挪到独立数据库）
# 9 只基金，按上面 4 类分组；007 等留空间新增。
ETF_ROTATION_BY_CODE: dict[str, str] = {
    # ── 港股科技互联网（4）──
    "006327": "港股科技互联网",   # 易方达中证海外互联网50ETF联接（QDII，海外中概）
    "013127": "港股科技互联网",   # 汇添富恒生科技ETF联接（QDII）
    "014673": "港股科技互联网",   # 富国中证港股通互联网ETF联接
    "016495": "港股科技互联网",   # 景顺长城中证港股通科技ETF联接
    # ── 港股银行（1）──
    "006809": "港股银行",         # 泰康香港银行指数（QDII）
    # ── 亚太除日本（1）──
    "457001": "亚太除日本",       # 国富亚洲机会（QDII，跟踪 MSCI AC Asia ex Japan）
    # ── 全球科技主动（3）──
    "016664": "全球科技主动",     # 天弘全球高端制造混合（QDII 主题）
    "006373": "全球科技主动",     # 国富全球科技互联混合（QDII 主题）
    "017730": "全球科技主动",     # 嘉实全球产业升级股票（QDII 主题）
}


# 各基金累计买入总金额（CNY）— 跟 cost_basis.py 同样的口径
# 457001 = 4640（liubo 2026-09-28 确认）；其他 8 只是 2026-09-28 截图估算
# （资产 - 持仓收益），精度 ±10 元；待 liubo 给准确成本后校准。
COST_BASIS_BY_CODE: dict[str, Decimal] = {
    # ── 港股科技互联网（4）──
    "006327": Decimal("10000"),     # liubo 2026-09-28 确认
    "013127": Decimal("25000"),     # liubo 2026-09-28 确认
    "014673": Decimal("23000"),     # liubo 2026-09-28 确认
    "016495": Decimal("20000"),     # liubo 2026-09-28 确认
    # ── 港股银行（1）──
    "006809": Decimal("1010"),      # liubo 2026-09-28 确认
    # ── 亚太除日本（1）──
    "457001": Decimal("4640"),      # liubo 2026-09-28 确认
    # ── 全球科技主动（3）──
    "016664": Decimal("2370"),      # liubo 2026-09-28 确认
    "006373": Decimal("100"),       # liubo 2026-09-28 确认
    "017730": Decimal("5300"),      # liubo 2026-09-28 确认
}


# 4 类分类常量（飞书顶部卡片 / 报告输出用）
STRATEGY_CATEGORIES: dict[str, list[str]] = {
    "港股科技互联网": ["006327", "013127", "014673", "016495"],
    "港股银行": ["006809"],
    "亚太除日本": ["457001"],
    "全球科技主动": ["016664", "006373", "017730"],
}


# 合计：91,420 元（9 只 ETF 轮动组合基金；2026-09-28 liubo 全部校准完成）
# - 港股科技互联网 4：78,000（006327=10k + 013127=25k + 014673=23k + 016495=20k）
# - 港股银行 1：1,010
# - 亚太除日本 1：4,640
# - 全球科技主动 3：7,770（016664=2,370 + 006373=100 + 017730=5,300）
TOTAL_COST_CNY: Decimal = sum(COST_BASIS_BY_CODE.values(), Decimal("0"))


# ── 当前市值 + 真实 P&L snapshot（2026-09-28 liubo 截图）───────────
# 真实 P&L = 当前总资产 - 累计成本；不用券商 app 的「持仓收益」（口径不准）。
# 资产数据来自 liubo 2026-09-28 截图，每只基金的「资产」列加总；每日更新需手动。
CURRENT_SNAPSHOT_DATE: date = date(2026, 9, 28)
CURRENT_TOTAL_ASSETS_CNY: Decimal = Decimal("85460.41")
CURRENT_TOTAL_PNL_CNY: Decimal = CURRENT_TOTAL_ASSETS_CNY - TOTAL_COST_CNY  # -5,959.59
# 收益率 = P&L / 成本；负数表示亏损
CURRENT_TOTAL_RETURN_PCT: Decimal = CURRENT_TOTAL_PNL_CNY / TOTAL_COST_CNY  # -0.0652 (-6.52%)


# 显示名（飞书卡片 / 报告输出用）
DISPLAY_NAME: dict[str, str] = {
    code: f"{label}（{code}）"
    for code, label in ETF_ROTATION_BY_CODE.items()
}


def get_strategy(fund_code: str) -> str | None:
    """查某只基金的策略标签（4 类之一）。未知返回 None。"""
    return ETF_ROTATION_BY_CODE.get(fund_code)


def get_cost_basis(fund_code: str) -> Decimal | None:
    """查某只基金的累计买入总金额（CNY）。未知返回 None。"""
    return COST_BASIS_BY_CODE.get(fund_code)


def get_total_assets_cny() -> Decimal:
    """当前总资产（CNY；最近一次 snapshot 的 9 只资产合计）。

    注：来自 2026-09-28 liubo 截图，每日更新需手动。后续如果接入行情 API
    可以改成动态算（fetch 最新净值 × 持仓份额）。
    """
    return CURRENT_TOTAL_ASSETS_CNY


def get_total_pnl_cny() -> Decimal:
    """真实总盈亏（CNY）= 当前总资产 - 累计成本。

    liubo 2026-09-28 拍板：不用券商 app 的「持仓收益」（口径不准 — app 漏算
    组合成立前的买入成本）。
    """
    return CURRENT_TOTAL_PNL_CNY


def get_total_return_pct() -> Decimal:
    """总收益率（小数形式；-0.0652 = -6.52%）。"""
    return CURRENT_TOTAL_RETURN_PCT


__all__ = [
    "ETF_ROTATION_BY_CODE",
    "COST_BASIS_BY_CODE",
    "STRATEGY_CATEGORIES",
    "TOTAL_COST_CNY",
    "CURRENT_SNAPSHOT_DATE",
    "CURRENT_TOTAL_ASSETS_CNY",
    "CURRENT_TOTAL_PNL_CNY",
    "CURRENT_TOTAL_RETURN_PCT",
    "DISPLAY_NAME",
    "get_strategy",
    "get_cost_basis",
    "get_total_assets_cny",
    "get_total_pnl_cny",
    "get_total_return_pct",
]