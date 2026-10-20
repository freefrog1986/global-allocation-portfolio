"""左侧布局策略组合（独立模块，liubo 2026-09-22 建「ETF 轮动组合」，2026-10-19 改名「左侧布局」）。

跟 Swensen 大类资产配置 + 红利策略**都不混在一起**，单独一个模块。

按策略标签分 4 类（liubo 2026-09-28 拍板）：

港股科技互联网（3 只，QDII/港股通 ETF 联接，跟踪指数基金）— 合计 53,000
- 006327 易方达中证海外互联网50ETF联接(QDII)A → 中证海外互联网 50 指数
- 014673 富国中证港股通互联网ETF发起式联接A → 中证港股通互联网指数
- 016495 景顺长城中证港股通科技ETF发起联接A → 中证港股通科技指数

港股银行（1 只，QDII）— 1,010
- 006809 泰康香港银行指数A → 港股银行指数（HSSI/HK银行）

亚太除日本（1 只，主动管理 QDII）— 4,640
- 457001 国富亚洲机会股票(QDII)A → 业绩基准 = MSCI AC Asia ex Japan

全球科技主动（6 只，QDII 主动管理主题基金 + 全球科技主题 ETF 联接）— 合计 7,770
- 016664 天弘全球高端制造混合(QDII)A → 全球高端制造主题
- 006373 国富全球科技互联混合(QDII)人民币A → 全球科技互联主题
- 017730 嘉实全球产业升级股票发起式(QDII)A → 全球产业升级主题
- 501226 长程全球新能源汽车股票(QDII) → 2026-10-20 加，成本 0（未买入）
- 019331 华泰柏瑞中证沪港深云计算产业ETF联接C → 2026-10-20 加，成本 0（未买入）
- 017436 华宝纳斯达克精选股票(QDII)A → 2026-10-20 加，成本 0（未买入）

房地产（1 只，A 股行业 ETF 联接 E 类）— 5,000
- 010989 南方中证房地产 ETF 发起联接 E → 中证房地产指数（liubo 2026-09-30 新加 1 仓）

合计 12 只 / 71,420 CNY（2026-09-28 8 只全部校准 + 2026-09-29 减 013127 -25000
+ 2026-09-30 加 010989 +5000 + 2026-10-20 加 501226/019331/017436 3 只成本 0）。

成本历史：
- 457001 = 4,640（旧 module 值；liubo 2026-09-28 确认）
- 014673/016495（liubo 2026-09-28 第二轮确认）
- 2026-09-29 liubo 把 013127 转到大类资产配置（HK 港股子类）
- 2026-09-30 liubo 加 010989 南方中证房地产 ETF 发起联接 E（新类别「房地产」），5000 CNY = 1 仓
- 2026-10-20 liubo 加 501226/019331/017436 3 只（并入「全球科技主动」，**暂定未买入 cost=0**）
- 其余 6 只 = 截图「资产 - 持仓收益」估算（精度 ±10 元）→ 2026-09-28 全部由 liubo 校准

P&L 口径（重要，liubo 2026-09-28 拍板）：
- **真实 P&L = 当前总资产 - 累计成本**
- 不要用券商 app「持仓收益」 — app 算的是「组合成立日之后的盈亏」，漏算
  成立前的累计买入（liubo 在建「左侧布局组合」分组前就已经买入了多只）。
- 2026-09-30 snapshot（含 010989 新买 5000）：总资产 67,090.41 - 累计成本 71,420 = -4,329.59 CNY (-6.06%)
  - 8 只老基金总资产 = 62,090.41（沿用 09-29 数据，等下一次手动 snapshot 更新）
  - 010989 新买入 5000 CNY（pending confirmation 10-08，按成本计入）
- 见 memory/project_brokerage_pnl.md

策略规则（liubo 2026-09-29 拍板 — 主观轮动 + 严格仓位管理；2026-10-19 拍板 cap 从 30% 降到 15%）：
- **标的（完全主观）**：liubo 看哪只顺眼买哪只，觉得不好就卖；**不**用算法信号
- **单笔规模**：每次买/卖 = 1 仓（不超买不超卖）
- **1 仓 = 5,000 CNY**（独立于大类资产配置的 1 万）
- **类别上限**：单类累计成本 ≤ 总成本 × 15%（CAP=15%；liubo 2026-10-19 拍板从 30% 降到 15%）
- **老仓位豁免**：当前已超限的类别，新买仍允许（不阻拦），只卡新买时的额外加仓
- **同基金冷却期**：30 天（liubo 2026-10-19 拍板：从 7 天延长到 30 天，避免月度内频繁交易同一只基金）
- **无止盈止损**：纯主观，无自动规则

注意：模块名「左侧布局」是 2026-10-19 改名（原 ETF 轮动组合）；其中 4 只是 QDII 主动管理基金
（亚太 + 全球科技 3 只）；"布局"指按主观判断 / 估值信号挑标的；标的本身不限于纯 ETF。

后续这块有自己的成本/估值/再平衡规则就加在这里。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal


# 已知基金 + 策略标签（MVP hardcode；后续可挪到独立数据库）
# 12 只基金（2026-10-20 liubo 加 501226/019331/017436 3 只，并入「全球科技主动」），按上面 5 类分组。
# 之前 9 只，2026-10-20 加 3。
LEFT_LAYOUT_BY_CODE: dict[str, str] = {
    # ── 港股科技互联网（3）──  2026-09-29 liubo 把 013127 转走
    "006327": "港股科技互联网",   # 易方达中证海外互联网50ETF联接（QDII，海外中概）
    "014673": "港股科技互联网",   # 富国中证港股通互联网ETF联接
    "016495": "港股科技互联网",   # 景顺长城中证港股通科技ETF联接
    # ── 港股银行（1）──
    "006809": "港股银行",         # 泰康香港银行指数（QDII）
    # ── 亚太除日本（1）──
    "457001": "亚太除日本",       # 国富亚洲机会（QDII，跟踪 MSCI AC Asia ex Japan）
    # ── 全球科技主动（6）──  2026-10-20 加 3 只（暂定未买入）
    "016664": "全球科技主动",     # 天弘全球高端制造混合（QDII 主题）
    "006373": "全球科技主动",     # 国富全球科技互联混合（QDII 主题）
    "017730": "全球科技主动",     # 嘉实全球产业升级股票（QDII 主题）
    "501226": "全球科技主动",     # 长程全球新能源汽车股票（QDII 主题，2026-10-20 加）
    "019331": "全球科技主动",     # 华泰柏瑞中证沪港深云计算产业ETF联接C（2026-10-20 加）
    "017436": "全球科技主动",     # 华宝纳斯达克精选股票（QDII）A（2026-10-20 加）
    # ── 房地产（1）──  2026-09-30 liubo 新加
    "010989": "房地产",           # 南方中证房地产 ETF 发起联接 E（中证房地产指数）
}


# 各基金累计买入总金额（CNY）— 跟 cost_basis.py 同样的口径
# 457001 = 4640（liubo 2026-09-28 确认）；其他 7 只是 2026-09-28 截图估算
# （资产 - 持仓收益），精度 ±10 元；待 liubo 给准确成本后校准。
# 2026-09-29 liubo 把 013127 转到大类资产配置，删 1 只。
# 2026-09-30 liubo 加 010989 南方中证房地产 ETF 发起联接 E（新类别「房地产」），5000 CNY = 1 仓
COST_BASIS_BY_CODE: dict[str, Decimal] = {
    # ── 港股科技互联网（3）──  2026-09-29 删 013127
    "006327": Decimal("10000"),     # liubo 2026-09-28 确认
    "014673": Decimal("23000"),     # liubo 2026-09-28 确认
    "016495": Decimal("20000"),     # liubo 2026-09-28 确认
    # ── 港股银行（1）──
    "006809": Decimal("1010"),      # liubo 2026-09-28 确认
    # ── 亚太除日本（1）──
    "457001": Decimal("4640"),      # liubo 2026-09-28 确认
    # ── 全球科技主动（6）──
    "016664": Decimal("2370"),      # liubo 2026-09-28 确认
    "006373": Decimal("100"),       # liubo 2026-09-28 确认
    "017730": Decimal("5300"),      # liubo 2026-09-28 确认
    # 2026-10-20 liubo 暂定要买、**尚未买入** → cost = 0（不影响总成本/占比）
    "501226": Decimal("0"),         # 长程全球新能源汽车股票
    "019331": Decimal("0"),         # 华泰柏瑞中证沪港深云计算产业ETF联接C
    "017436": Decimal("0"),         # 华宝纳斯达克精选股票(QDII)A
    # ── 房地产（1）──  2026-09-30 加
    "010989": Decimal("5000"),      # 南方中证房地产 ETF 发起联接 E（新 1 仓）
}


# 5 类分类常量（飞书顶部卡片 / 报告输出用）
STRATEGY_CATEGORIES: dict[str, list[str]] = {
    "港股科技互联网": ["006327", "014673", "016495"],  # 2026-09-29 删 013127
    "港股银行": ["006809"],
    "亚太除日本": ["457001"],
    "全球科技主动": ["016664", "006373", "017730", "501226", "019331", "017436"],
    "房地产": ["010989"],  # 2026-09-30 liubo 新加第 5 类
}


# 各类别用的估值指数（liubo 2026-10-19 拍板 Section 3 加「估值」字段用）。
# 格式：{类别名: [指数代码, ...]}，跟理杏仁对得上；空 list = 无统一基准。
# liubo 选指数时挑**权威指数**作为该类别估值参照；
# 2026-10-20 liubo 拍板：全球科技主动要**同时参考两个**指数（纳指100 + 费城半导体），
# 所以值改成 list，卡片里用「 / 」并列展示。QDII 主动主题类别 → 空 list 不估值。
INDEX_BY_CATEGORY: dict[str, list[str]] = {
    # 港股科技互联网 3 只：006327 / 014673 / 016495
    # 006327 跟踪中证海外互联网 50，014673 跟踪中证港股通互联网，016495 跟踪中证港股通科技
    # 3 只主题近似但跟踪指数不同；先选 016495 的港股通科技（liubo 2026-10-19 拍板理杏仁示例）
    "港股科技互联网": ["987008.sz"],  # 港股通科技指数（016495 跟踪）
    "港股银行": ["930792.csi"],        # liubo 2026-10-19 拍板：HK 银行（理杏仁）
    "亚太除日本": ["MSCI_AC_AXJ"],     # MSCI AC Asia ex Japan（数据未填）
    # 全球科技主动：liubo 2026-10-20 拍板**两个都要**，不删纳指 100
    "全球科技主动": [".NDX", ".SOX"],
    "房地产": ["931775.csi"],          # liubo 2026-10-19 拍板：房地产指数（理杏仁）
}


# 指数代码 → 显示名称（liubo 2026-10-19 拍板 Section 3 「估值」首字段）。
INDEX_DISPLAY_NAME_BY_CODE: dict[str, str] = {
    "987008.sz": "港股通科技",
    "930792.csi": "HK银行",
    "MSCI_AC_AXJ": "MSCI AC Asia ex Japan",
    ".NDX": "纳斯达克100",
    "931775.csi": "房地产",
    ".SOX": "费城半导体",            # liubo 2026-10-20 加（全球科技主动估值基准）
}


# 指数代码 → 估值 dict（liubo 手动维护，飞书卡片「估值」字段用）。
# liubo 2026-10-19 拍板 R10：每个类别只展示 1 个主打估值指标（简化版）。
# 字段集：
#   primary_label: 主指标名（如 "PE-TTM" / "PB"）
#   primary_value: 主指标的值（如 "23.10@13.14%" — 值 @ 当前值分位点%）
# 注意：R9 schema 全 11 字段已简化为这 2 个；未来如需多指标，扩展 primary_label 为 list。
INDEX_VALUATION_BY_CODE: dict[str, dict[str, str]] = {
    # 港股通科技 (987008.sz) — PE-TTM 23.10 @13.14%（liubo 2026-10-19 拍板 R10）
    "987008.sz": {
        "primary_label": "PE-TTM",
        "primary_value": "23.10@13.14%",
    },
    # HK 银行 (930792.csi) — PE-TTM 7.32 @90.71%（liubo 2026-10-19 给数据）
    "930792.csi": {
        "primary_label": "PE-TTM",
        "primary_value": "7.32@90.71%",
    },
    # 房地产 (931775.csi) — PB 0.760 @13.27%（liubo 2026-10-19 给数据）
    # 注意：房地产的主指标是 PB 不是 PE-TTM（行业习惯用 PB 看估值）
    "931775.csi": {
        "primary_label": "PB",
        "primary_value": "0.760@13.27%",
    },
    # 纳斯达克100 (.NDX) — PB 9.08（liubo 2026-10-19 给；行业习惯 PB 而非 PE-TTM）
    # 注意：分位点 liubo 暂未给 → value 只填数字不带 @分位
    ".NDX": {
        "primary_label": "PB",
        "primary_value": "9.08",
    },
    # 费城半导体 (.SOX) — liubo 2026-10-20 指定为「全球科技主动」估值基准，数据未填
    # 待 liubo 补主指标（PE-TTM 或 PB）+ 分位点后填下面两行：
    # ".SOX": {
    #     "primary_label": "PE-TTM",
    #     "primary_value": "XX.XX@XX.XX%",
    # },
    # 亚太除日本 (MSCI_AC_AXJ) — 数据未填
}


def get_index_name(index_code: str) -> str:
    """查指数显示名（liubo 2026-10-19 拍板 Section 3 用）。未知 → 原 code。"""
    return INDEX_DISPLAY_NAME_BY_CODE.get(index_code, index_code or "无统一基准")


def get_index_primary_metric(index_code: str) -> tuple[str, str]:
    """查指数的主打估值指标（liubo 2026-10-19 拍板 R10：1 个指标简版）。

    Returns:
        (label, value) 元组：
        - label = "PE-TTM" 等指标名，未填 → ""
        - value = "23.10@13.14%" 等值 + 分位点%，未填 → "—"

    例：get_index_primary_metric("987008.sz") → ("PE-TTM", "23.10@13.14%")
        get_index_primary_metric("") → ("", "—")
        get_index_primary_metric("HSSI.HK") → ("PE-TTM", "—")  # 字段缺
    """
    if not index_code:
        return ("", "—")
    data = INDEX_VALUATION_BY_CODE.get(index_code, {})
    return (data.get("primary_label", "PE-TTM"), data.get("primary_value", "—"))


# 合计：71,420 元（9 只左侧布局组合基金；2026-09-28 liubo 全部校准完成 + 2026-09-29 减 013127 -25000 + 2026-09-30 加 010989 +5000）
# - 港股科技互联网 3：53,000（006327=10k + 014673=23k + 016495=20k）
# - 港股银行 1：1,010
# - 亚太除日本 1：4,640
# - 全球科技主动 3：7,770（016664=2,370 + 006373=100 + 017730=5,300）
# - 房地产 1：5,000（010989）
TOTAL_COST_CNY: Decimal = sum(COST_BASIS_BY_CODE.values(), Decimal("0"))


# ── 当前市值 + 真实 P&L snapshot（2026-09-30 含 010989 新买 5000）───────────
# 真实 P&L = 当前总资产 - 累计成本；不用券商 app 的「持仓收益」（口径不准）。
# 资产数据来自 liubo 2026-09-28 截图 + 2026-09-30 加 010989 5000 CNY。
# 010989 是新买入（pending confirmation 10-08），按成本计入总资产。
CURRENT_SNAPSHOT_DATE: date = date(2026, 9, 30)  # 2026-09-30 加 010989
CURRENT_TOTAL_ASSETS_CNY: Decimal = Decimal("67090.41")  # 62090.41 + 010989 新买 5000
CURRENT_TOTAL_PNL_CNY: Decimal = CURRENT_TOTAL_ASSETS_CNY - TOTAL_COST_CNY  # 67,090.41 - 71,420 = -4,329.59
# 收益率 = P&L / 成本；负数表示亏损
CURRENT_TOTAL_RETURN_PCT: Decimal = CURRENT_TOTAL_PNL_CNY / TOTAL_COST_CNY  # -0.0606


# 显示名（飞书卡片 / 报告输出用）
DISPLAY_NAME: dict[str, str] = {
    code: f"{label}（{code}）"
    for code, label in LEFT_LAYOUT_BY_CODE.items()
}


# 基金中文全名（飞书卡片 Section 3 第一列用，liubo 2026-10-19 拍板从
# 「类别（code）」拆出 → 基金全名 + 类别各一列）
# 来源：天天基金 / 各家基金公司官网，2026-09-28 liubo 校准。
FUND_FULL_NAME_BY_CODE: dict[str, str] = {
    "006327": "易方达中证海外互联网50ETF联接(QDII)A",
    "014673": "富国中证港股通互联网ETF发起式联接A",
    "016495": "景顺长城中证港股通科技ETF发起联接A",
    "006809": "泰康香港银行指数A",
    "457001": "国富亚洲机会股票(QDII)A",
    "016664": "天弘全球高端制造混合(QDII)A",
    "006373": "国富全球科技互联混合(QDII)人民币A",
    "017730": "嘉实全球产业升级股票发起式(QDII)A",
    "501226": "长程全球新能源汽车股票(QDII)A",
    "019331": "华泰柏瑞中证沪港深云计算产业ETF联接C",
    "017436": "华宝纳斯达克精选股票(QDII)A",
    "010989": "南方中证房地产ETF发起联接E",
}


def get_strategy(fund_code: str) -> str | None:
    """查某只基金的策略标签（4 类之一）。未知返回 None。"""
    return LEFT_LAYOUT_BY_CODE.get(fund_code)


def get_cost_basis(fund_code: str) -> Decimal | None:
    """查某只基金的累计买入总金额（CNY）。未知返回 None。"""
    return COST_BASIS_BY_CODE.get(fund_code)


# 各基金估值（liubo 手动维护，飞书卡片 Section 3 「估值」列用）。
# 左侧布局是「主观选标的」，不接算法信号；估值是 liubo 每周看 PE-TTM / PB
# 分位后自己填的，给参考。空 key → 卡片显示「—」。
# 格式自由：PE-TTM 分位 65% / PB 0.85 / 等等，看 liubo 怎么填方便。
VALUATION_BY_CODE: dict[str, str] = {
    # "006327": "海外互联网 PE-TTM 分位 28%",
    # "010989": "中证房地产 PB 分位 12%",
    # 其余 7 只待 liubo 填
}


def get_valuation(fund_code: str) -> str:
    """查某只基金的估值字符串。未知/未填 → "—"。"""
    return VALUATION_BY_CODE.get(fund_code, "—")


def get_category_indices(category: str) -> list[str]:
    """查某类别用的估值指数代码列表（liubo 2026-10-19 拍板 Section 3 用）。

    2026-10-20 liubo 拍板全球科技主动要同时参考 2 个指数 → 改成返回 list。
    未知类别 → 空 list。
    """
    return INDEX_BY_CATEGORY.get(category, [])


def get_category_index_name(category: str) -> str:
    """查某类别用的估值指数显示名（多指数用「 / 」并列）。未知 → 「无统一基准」。"""
    codes = get_category_indices(category)
    if not codes:
        return "无统一基准"
    return " / ".join(INDEX_DISPLAY_NAME_BY_CODE.get(c, c) for c in codes)


def decide_category_action(category: str) -> dict[str, str]:
    """给某类别决定调仓动作（liubo 2026-10-19 拍板 Section 3 R10：4 选 1 + 仓数单位）。

    4 种动作：
    - **减仓** — 类别累计 > 15% 上限（老仓位豁免，但动作还是减仓以提示集中度风险）
    - **持有** — 接近上限（>80% × 15% = 12% 占比）→ 不加不减
    - **加仓** — 距离上限有空间 → 还有加仓余地
    - **建仓** — 该类别还没有任何子基金 → 主观选标的

    R10 加 unit 字段：单笔 = 1 仓（POSITION_UNIT_CNY = 5000）。
    - 减仓 → 「减 1 仓」
    - 加仓 → 「加 1 仓」
    - 建仓 → 「建 1 仓」
    - 持有 → 「持有」（无 unit）

    Args:
        category: 类别名。

    Returns:
        dict 含:
          - action: 4 选 1（"减仓" / "持有" / "加仓" / "建仓"）
          - action_with_unit: 卡片里显示的动作字符串（"减 1 仓" 等）
          - reason: 人类可读的具体原因（含具体数字）
          - emoji: 在卡片里用的 emoji 前缀
    """
    codes = STRATEGY_CATEGORIES.get(category, [])
    if not codes:
        # 该类别下还没有任何子基金 → 建仓
        return {
            "action": "建仓",
            "action_with_unit": "建 1 仓",
            "reason": "暂无持仓",
            "emoji": "🆕",
        }

    status = get_category_cap_status(category)
    pct = float(status["pct_of_total"]) * 100
    cap_pct = float(CATEGORY_CAP_PCT) * 100  # 15%
    near_threshold = cap_pct * 0.8  # 接近上限 = 12%

    if status["is_over"]:
        return {
            "action": "减仓",
            "action_with_unit": "减 1 仓",
            "reason": "仓位超 15% 上限",
            "emoji": "⚠️",
        }
    if pct >= near_threshold:
        return {
            "action": "持有",
            "action_with_unit": "持有",
            "reason": f"仓位 {pct:.1f}%，接近 12% 阈值，不加不减",
            "emoji": "✋",
        }
    return {
        "action": "加仓",
        "action_with_unit": "加 1 仓",
        "reason": (
            f"仓位 {pct:.1f}%，离 15% 上限还有 "
            f"{float(status['remaining_cny']):,.0f} CNY"
        ),
        "emoji": "➕",
    }


def get_rebalance_suggestion(fund_code: str, today: date | None = None) -> str:
    """某只基金的调仓建议（自动派生，飞书卡片 Section 3 「调仓建议」列用）。

    优先级（从高到低）：
    1. 同基金冷却中 → 「冷却中，剩 X 天」
    2. 类别超限 → 「⚠️ 类别超限，减仓」
    3. 否则 → 「主观持有」（liubo 自己决定）

    左侧布局不接算法信号（liubo 2026-09-29 拍板）；只能给「类别集中度风险」这个
    系统性提醒，其他按主观。
    """
    if today is None:
        today = date.today()

    cooldown_ok, cooldown_reason = check_cooldown(fund_code, today)
    if not cooldown_ok:
        # cooldown_reason 类似 "上次交易 2026-09-19（5 天前），冷却期还剩 25 天"
        # 提取「剩 X 天」
        if "，" in cooldown_reason:
            tail = cooldown_reason.split("，")[-1]
            return f"⏸ 冷却中（{tail}）"
        return "⏸ 冷却中"

    category = LEFT_LAYOUT_BY_CODE.get(fund_code)
    if category is None:
        return "—"  # 未知基金
    cap_status = get_category_cap_status(category)
    if cap_status["is_over"]:
        pct = float(cap_status["pct_of_total"]) * 100
        return f"⚠️ 类别超限 {pct:.1f}%，主观决定"

    return "主观持有"


# ── 仓位管理（liubo 2026-09-29 拍板）─────────────────────────────────

# 1 仓 = 5,000 CNY（独立于大类资产配置的 1 万；不混）
POSITION_UNIT_CNY: Decimal = Decimal("5000")

# 类别上限：单类累计成本 ≤ 总成本 × 15%（老仓位豁免，只卡新买）
# liubo 2026-10-19 拍板从 30% 降到 15%（更严格，单类集中度风险低）
CATEGORY_CAP_PCT: Decimal = Decimal("0.15")

# 同基金冷却期：30 天（liubo 2026-10-19 拍板从 7 天延长到 30 天）
COOLDOWN_DAYS: int = 30

# 同基金最后交易日（liubo 手动维护；初始为空 dict，每次 record_trade 后更新）
# 格式：{fund_code: date}
# liubo 2026-09-29 拍板：同基金冷却；2026-10-19 拍板 7 天 → 30 天
LAST_TRADE_BY_FUND: dict[str, date] = {}


def get_position(fund_code: str) -> Decimal:
    """查某只基金的当前仓位（仓数；小数 = cost / 5000）。

    未知基金 → 0。
    例：cost=4640 → 0.928 仓；cost=23000 → 4.6 仓。
    """
    cost = COST_BASIS_BY_CODE.get(fund_code)
    if cost is None:
        return Decimal("0")
    return cost / POSITION_UNIT_CNY


def get_category_total_cost(category: str) -> Decimal:
    """算某类别所有基金的累计成本总和（CNY）。

    未知类别 → 0。
    """
    codes = STRATEGY_CATEGORIES.get(category, [])
    return sum(
        (COST_BASIS_BY_CODE.get(c, Decimal("0")) for c in codes),
        Decimal("0"),
    )


def get_category_cap_status(category: str) -> dict[str, Decimal | bool]:
    """算某类别的上限状态。

    Returns:
        dict 含:
          - cap_cny: 上限 CNY（= 当前总成本 × 15%）
          - current_cny: 当前类别累计成本
          - remaining_cny: 还能加仓多少 CNY（负数表示已超限）
          - pct_of_total: 当前类别占组合总成本的比例（current / total_cost；liubo 2026-10-19 拍板用实际占比，不用「使用率」）
          - is_over: 是否已超限（老仓位豁免不影响这个字段，只是提醒）
    """
    cap_cny = TOTAL_COST_CNY * CATEGORY_CAP_PCT
    current_cny = get_category_total_cost(category)
    remaining_cny = cap_cny - current_cny
    pct_of_total = (
        (current_cny / TOTAL_COST_CNY) if TOTAL_COST_CNY > 0 else Decimal("0")
    )
    return {
        "cap_cny": cap_cny,
        "current_cny": current_cny,
        "remaining_cny": remaining_cny,
        "pct_of_total": pct_of_total,
        "is_over": current_cny > cap_cny,
    }


def check_category_cap_for_buy(
    fund_code: str, add_cost: Decimal
) -> tuple[bool, str]:
    """检查新买入是否会让该基金所在类别超过 15% 上限。

    老仓位豁免：当前已超限时，仍然允许新买（不阻拦），但 reason 里有"已达上限，新买仍允许"提示。

    Args:
        fund_code: 基金代码。
        add_cost: 计划新增成本（CNY）。

    Returns:
        (ok: bool, reason: str)
        ok=True 表示可以买；ok=False 表示拒绝（虽然现在因为老仓位豁免，永远 ok=True）。
        reason 给出人类可读的状态。
    """
    category = get_strategy(fund_code)
    if category is None:
        return False, f"未知基金 {fund_code} 不在左侧布局组合"
    status = get_category_cap_status(category)
    new_total = status["current_cny"] + add_cost
    cap_cny = status["cap_cny"]
    new_pct_of_total = (
        (new_total / TOTAL_COST_CNY) if TOTAL_COST_CNY > 0 else Decimal("0")
    )

    if status["is_over"]:
        # 老仓位超限 — 豁免；提示但不阻拦
        return True, (
            f"⚠️ {category} 已超 15% 上限（当前 {float(status['pct_of_total']) * 100:.1f}%，"
            f"新买后会变 {float(new_pct_of_total) * 100:.1f}%），老仓位豁免，仍允许"
        )
    if new_total > cap_cny:
        return False, (
            f"✗ {category} 类别上限 15% ({float(cap_cny):.0f} CNY)，"
            f"新买后累计 {float(new_total):.0f} CNY = {float(new_pct_of_total) * 100:.1f}%，超限"
        )
    return True, (
        f"✓ {category} 累计 {float(new_total):.0f} / {float(cap_cny):.0f} CNY "
        f"({float(new_pct_of_total) * 100:.1f}%)，未超 15% 上限"
    )


def check_cooldown(fund_code: str, today: date | None = None) -> tuple[bool, str]:
    """检查同基金冷却期（liubo 2026-09-29 拍板：7 天；2026-10-19 拍板：30 天）。

    Args:
        fund_code: 基金代码。
        today: 评估日期；None = date.today()。

    Returns:
        (ok: bool, reason: str)
        ok=True 表示可交易；ok=False 表示冷却中。
        reason 给出人类可读的状态。
    """
    if today is None:
        today = date.today()
    last = LAST_TRADE_BY_FUND.get(fund_code)
    if last is None:
        return True, "首次交易，无冷却期记录"
    days_since = (today - last).days
    if days_since >= COOLDOWN_DAYS:
        return True, f"上次交易 {last.isoformat()}（{days_since} 天前），已过冷却期"
    remaining = COOLDOWN_DAYS - days_since
    return False, (
        f"上次交易 {last.isoformat()}（{days_since} 天前），"
        f"冷却期还剩 {remaining} 天"
    )


def record_trade(fund_code: str, trade_date: date) -> None:
    """记录某基金的最后交易日（用于冷却期检查）。

    实际 BUY/SELL 用 journal.record_buy / record_sell；这里只更新冷却期表。
    liubo 在每次手动交易后调一下，或者 botmux 帮我记。
    """
    LAST_TRADE_BY_FUND[fund_code] = trade_date


def format_weekly_snapshot_text(today: date | None = None) -> str:
    """生成每周快照的纯文本格式（用于 dry-run / 日志 / CLI 输出）。

    飞书卡片版见 left_layout_card.py。
    """
    if today is None:
        today = date.today()
    lines: list[str] = []
    lines.append(f"📊 左侧布局 周快照 ({today.isoformat()})")
    lines.append("")
    lines.append(f"组合总成本：{float(TOTAL_COST_CNY):,.0f} CNY")
    lines.append(f"当前总资产：{float(CURRENT_TOTAL_ASSETS_CNY):,.2f} CNY")
    lines.append(f"总盈亏：{float(CURRENT_TOTAL_PNL_CNY):+,.2f} CNY "
                 f"({float(CURRENT_TOTAL_RETURN_PCT) * 100:+.2f}%)")
    lines.append("")

    # 按类别分组
    for category in STRATEGY_CATEGORIES:
        status = get_category_cap_status(category)
        cap_pct = float(status["pct_of_total"]) * 100
        over_mark = " ⚠️超限（老仓位豁免）" if status["is_over"] else ""
        lines.append(
            f"**{category}**  类别累计 {float(status['current_cny']):,.0f} / "
            f"上限 {float(status['cap_cny']):,.0f} CNY "
            f"(占总成本 {cap_pct:.1f}%){over_mark}"
        )
        for code in STRATEGY_CATEGORIES[category]:
            cost = COST_BASIS_BY_CODE.get(code, Decimal("0"))
            pos = get_position(code)
            cooldown_ok, cooldown_reason = check_cooldown(code, today)
            cd_mark = "" if cooldown_ok else f"  [{cooldown_reason}]"
            lines.append(
                f"  · {code} {DISPLAY_NAME[code]}：{float(cost):,.0f} CNY = "
                f"{float(pos):.2f} 仓{cd_mark}"
            )
        lines.append("")

    return "\n".join(lines)


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
    "LEFT_LAYOUT_BY_CODE",
    "COST_BASIS_BY_CODE",
    "STRATEGY_CATEGORIES",
    "TOTAL_COST_CNY",
    "CURRENT_SNAPSHOT_DATE",
    "CURRENT_TOTAL_ASSETS_CNY",
    "CURRENT_TOTAL_PNL_CNY",
    "CURRENT_TOTAL_RETURN_PCT",
    "DISPLAY_NAME",
    "FUND_FULL_NAME_BY_CODE",
    "POSITION_UNIT_CNY",
    "CATEGORY_CAP_PCT",
    "COOLDOWN_DAYS",
    "LAST_TRADE_BY_FUND",
    "VALUATION_BY_CODE",
    "INDEX_BY_CATEGORY",
    "INDEX_DISPLAY_NAME_BY_CODE",
    "INDEX_VALUATION_BY_CODE",
    "get_strategy",
    "get_cost_basis",
    "get_position",
    "get_valuation",
    "get_category_indices",
    "get_category_index_name",
    "get_index_name",
    "get_index_primary_metric",
    "decide_category_action",
    "get_rebalance_suggestion",
    "get_category_total_cost",
    "get_category_cap_status",
    "check_category_cap_for_buy",
    "check_cooldown",
    "record_trade",
    "format_weekly_snapshot_text",
    "get_total_assets_cny",
    "get_total_pnl_cny",
    "get_total_return_pct",
]