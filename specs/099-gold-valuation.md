# Spec 099: 黄金估值方法（A+B 综合分）

> 状态：Draft
> 最后更新：2026-10-08
> 作者：liubo
> 实现 PR（如果有）：待
> 前置：spec 098（A 股/港股/美股估值指标框架）
>
> **变更记录**
> - v1 → v2 (2026-10-08 liubo 拍板, commit 6e679b3 / 6dff326): 指标 B 从 1/r 倒数改实际利率本身（direction='low'）；金价分位窗口 10 年 → 5 年。本 doc 已按现行版对齐。

## 目标

给项目里的黄金仓位（000216 华安黄金 ETF 联接A，COMMODITY 子类，目标 2 仓 / 2 万 CNY）一个估值方法，让 pe_rebalance 调仓框架能对黄金给出真实的 ADD / HOLD / REDUCE 建议（现状是直接 SKIP，调仓表里黄金永远不动）。

回答的问题：
- 现在黄金估值偏高还是偏低？
- 跟过去 5 年比在什么位置？（v2 拍板用 5 年不用 10 年）
- 相对实际利率看是否有泡沫？
- 该加仓、持有、还是减仓？

## 不在范围内

- **不做白银 / 能源 / 工业金属**（第一期只做黄金；其他商品 spec 099.x 单独做）
- **不接自动数据源**（FRED CSV + akshare SGE 都是手动 weekly hardcode，跟现状 pe_rebalance.py 一致）
- **不做黄金收益率 / 波动率 / VaR 风险指标**（spec 099 只做估值；风险归 spec 060）
- **不改 Strategy 整数仓位模型**（2 仓目标不动；spec 099 只决定调仓时点）
- **不接券商 API 自动同步金价持仓**（沿用 cost_basis.py 手工录入）

## 选定的 2 个估值指标

### 指标 A：金价 5 年分位（GOLD_HISTORICAL_PCT）

- 公式：当前金价在过去 5 年金价序列里的百分位
- 数据源：akshare `spot_golden_benchmark_sge`（SGE Au99.99 上海黄金交易所基准价，日频）
- 阈值（direction='high' — 越大越高估）：
  - 极低估 (1):  < 30% 分位
  - 低估 (2):    30% ≤ value < 50%
  - 正常 (3):    50% ≤ value < 70%
  - 偏高估 (4):  70% ≤ value < 90%
  - 极高估 (5):  ≥ 90% 分位
- 优点：跟 A 股 PE 分位方法完全一致，复用 compute_pe_percentile 函数（金价当 PE 用）
- 缺点：金价趋势性强（2020 起飞到现在），分位可能长期贴边；需要配合 B 指标交叉验证
- **窗口选择（liubo 2026-10-08 拍板用 5 年不是 10 年）**：
  - 5 年 = 1 轮完整黄金牛熊（2020 起飞 → 2024 高点 → 2025 整理），更能反映"近 1 轮"估值
  - 10 年多算 2016-2020 横盘段（350-450 USD/oz 低位），拉低分位、钝化
  - 当前实测 5 年分位 61.58% vs 10 年分位 65.54%（差 4 个百分点）

### 指标 B：实际利率法（GOLD_REAL_YIELD）

- 金融学逻辑：黄金 = 无息资产 + 抗通胀 + 美元替代；机会成本 = 美国 10Y 实际利率（TIPS 收益率）
- 公式：**直接用实际利率本身打分**（v2 liubo 2026-10-08 拍板；v1 用 1/r 倒数被判定逻辑反，commit 6e679b3 删掉）
  - direction='low' — 实际利率 ↑ → 黄金吸引力 ↓ → 黄金低估（越值高越便宜）
  - 跟股债利差 / 股息率同款 direction='low' 心智
- 阈值直接用实际利率打 5 档（见下方），v1 的 1/r 反向打分已废弃
- 数据源：FRED CSV `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10`（10-Year Treasury TIPS，日频）
  - FRED 国内直连可达（已测 2026-10-08 curl 通）
  - 不需要 API key（CSV 公共接口）
  - fallback：手动每周 hardcode DFII10 当前值
- **为什么用 TIPS 实际利率而不是 DGS10 名义利率（liubo 2026-10-08 疑问澄清）**：
  - DGS10 = 10Y 名义国债收益率（5.27% 2026-10-06）
  - DFII10 = 10Y TIPS 实际利率（2.91% 2026-10-06）
  - 三角恒等：DGS10 - DFII10 = T10YIE 通胀预期（2.36%）
  - 黄金 = 抗通胀资产，天然吃回通胀补偿（2.36%），所以持有黄金的"真实机会成本"是实际利率 2.91%（不是名义 5.27%）
  - 教科书 / 央行黄金研究 / Dalio《债务危机》都按 TIPS 实际利率估值
  - 用 DGS10 名义利率会高估持有成本（5.27% 里 2.36% 是通胀补偿，黄金天然抗）
- 阈值（direction='low' — 越大越低估；v2 liubo 2026-10-08 拍板改用「实际利率本身」打分，删掉 1/r 倒数换算）：
  - 极低估 (1):  实际利率 ≥ 3%
  - 低估 (2):    2% ≤ 实际利率 < 3%
  - 正常 (3):    1% ≤ 实际利率 < 2%
  - 偏高估 (4):  0% ≤ 实际利率 < 1%
  - 极高估 (5):  实际利率 < 0%（实际负利率，黄金泡沫）

### 为什么 A+B 综合

- 单看 A：金价分位会钝化（趋势期长期贴边），单独信号不稳
- 单看 B：实际利率是宏观因子，对黄金估值有"延迟"（金价反应预期而非当下实际利率）
- 两者一起：分位回答"价格历史位置"，利率回答"持有贵不贵"，互补
- 综合分 = 简单平均（spec 098 第二十一轮 liubo 拍板的"投票"机制）

### 为什么 2 个而不是更多

- 黄金不像股票有 4 个独立维度（盈利、股息、宏观、相对）
- 黄金的 2 个维度 = 价格分位 + 实际利率，已经覆盖"绝对水平 + 相对机会成本"
- 跟 A 股 4 指标不同市场不要硬套 — 2 指标简洁，添加更多反而过度工程

## API 概览

```python
# v2 不再需要 compute_gold_real_yield 函数（commit 6e679b3 删掉 1/r 换算）
# 指标 B 直接用 FRED DFII10 实际利率本身喂给 score_indicator() 打 5 档
# 复用 spec 098 已有的 compute + score 函数
from global_allocation.portfolio.valuation_indicators import (
    compute_pe_percentile,   # 指标 A：复用，金价当 PE 用
    score_indicator,         # 5 档打分（已存在，spec 098 加 GOLD_REAL_YIELD band 后直接用）
    compute_composite_score,  # 综合分（已存在）
    interpret_composite_score,  # 5 档解读（已存在）
)
```

## 数据契约

### 指标代码枚举（扩展现有 enum）

```python
class ValuationIndicatorCode(str, Enum):
    # ... spec 098 已有 12 个 ...
    # spec 099 新增 2 个
    GOLD_HISTORICAL_PCT = "gold_historical_pct"  # SGE Au99.99 5 年分位（v2 liubo 2026-10-08 拍板用 5 年不用 10 年）
    GOLD_REAL_YIELD = "gold_real_yield"          # FRED DFII10 实际利率本身（v2 liubo 2026-10-08 拍板，direction='low'）
```

### pe_rebalance 数据流

```python
# 黄金不放在 PE_SNAPSHOT_BY_INDEX（那是 PE/分位单一指标快照）
# 黄金用专用结构 GOLD_SNAPSHOT
GOLD_SNAPSHOT: dict[str, tuple[Decimal, Decimal, Decimal, Decimal]] = {
    # fund_code → (gold_price, gold_pct_5y, real_yield_dfii10, gold_real_yield_value)
    "000216": (
        Decimal("907.50"),  # SGE Au99.99 当前价 CNY/g（akshare 2026-09-29）
        Decimal("0.6158"),  # 5 年分位 61.58%（liubo 2026-10-08 拍板 5 年）
        Decimal("0.0291"),  # FRED DFII10 当前 2.91%（2026-10-06，TIPS 实际利率）
        Decimal("34.36"),   # 1/0.0291 = 34.36（compute 自动算）
    ),
}

# INDEX_DISPLAY_NAME 改名
"GOLD_NO_METRIC": "黄金 (无估值指标)",  # 旧
"GOLD": "黄金",                        # 新
```

### 调仓规则

复用 spec 022 股票规则（liubo 2026-10-08 拍板）：

| 仓位状态 | 综合分 | 信号 |
| --- | --- | --- |
| 仓位 = 0 | < 3.0（正常/低估） | BUILD +1 |
| 仓位 = 0 | >= 3.0（偏高估） | HOLD |
| 仓位 in (0, 1) | < 3.0 | ADD +1 |
| 仓位 in (0, 1) | 3.0 - 4.0 | HOLD |
| 仓位 in (0, 1) | > 4.0 | REDUCE -1 |
| 仓位 >= 1 | < 2.0（深低估） | ADD +1 |
| 仓位 >= 1 | 2.0 - 4.0 | HOLD |
| 仓位 >= 1 | > 4.0 | REDUCE -1 |
| 数据缺失 | — | SKIP |

月度冷却期（COOLDOWN_DAYS = 30）也复用。

## 边界情况

- 边界 1：实际利率 < 0% → 直接打 5 分（极高估，正常情况罕见但金融史上有过 2011-2020 长负利率段）
- 边界 2：金价历史序列不足 5 年（akshare SGE Au99.99 数据 2002 起，足够；但若 akshare 返回空 → SKIP）
- 边界 3：FRED CSV 拉取失败 → fallback 到 hardcoded snapshot（pe_rebalance.py 已有的容错模式）
- 边界 4：黄金仓位已达上限（2 仓 + bound 2 = 4 仓）→ 仍按规则评估，可能 REDUCE
- 边界 5：综合分边界值（1.5 / 2.5 / 3.5 / 4.5）— 复用 spec 098 半开区间 [a, b)

## 验收标准

- [ ] `ValuationIndicatorCode` 加 2 个新值（GOLD_HISTORICAL_PCT + GOLD_REAL_YIELD）
- [ ] `DEFAULT_THRESHOLDS` 加 2 个黄金指标阈值
- [ ] `DEFAULT_SCORE_BANDS` 加 2 个黄金指标 5 档（v2 GOLD_REAL_YIELD band 用「实际利率本身」打分，删 compute_gold_real_yield）
- [ ] pe_rebalance.py 黄金从 SKIP 改走 2 指标综合分
- [ ] GOLD_SNAPSHOT hardcode 当前值
- [ ] 测试：黄金仓位 = 0 综合分 < 3.0 → BUILD +1
- [ ] 测试：黄金仓位 >= 1 综合分 > 4.0 → REDUCE -1
- [ ] 测试：实际利率 < 0%（负利率）→ 5 分（极高估）
- [ ] 测试覆盖率 ≥ 80%
- [ ] 调仓预览：跑一次 pe_rebalance，黄金给出真实信号（不再 SKIP）
- [ ] 文档：spec 099 标 "Implemented"

## 依赖

- spec 022（PE-TTM 周调仓策略 — 复用规则）
- spec 098（估值指标框架 — 复用 compute_pe_percentile / compute_composite_score）
- spec 097（整数仓位模型 — 黄金目标 2 仓，区间 [0, 4]）
- akshare ≥ 1.13（`spot_golden_benchmark_sge` 已在）
- FRED CSV 公共接口（国内直连可达，无 API key）

## 备注

- 当前 SGE Au99.99 价格 / FRED DFII10 / 5 年分位 都需要在 GOLD_SNAPSHOT 里 hardcode（v2 5 年不用 10 年）
  - 跟 spec 098 / spec 022 一致：手工 weekly 录入，OK 接受
  - 后续接 akshare fetcher 自动拉（spec 098 末尾已留 hook）
- 实际利率数据源切到 FRED 而不是 akshare 估算（10Y - CPI 同比）— 因为：
  - akshare 没直接的 TIPS/real yield 接口
  - FRED CSV 国内可达，已实测
  - 真正 TIPS 收益率 vs 估算的差异可能 ±0.5-1%，对估值判断影响大
- 黄金 2 指标体系（不是 4 指标）— 因为黄金不像股票有"盈利/分红/宏观"等独立维度
- 调仓规则完全复用股票 — 跟 liubo 2026-10-08 拍板一致，"一套心智不分裂"
- 黄金目标 2 仓，当前 cost 2510（占目标 12.5%）→ 当前 position < 1 + 任意"低估/正常" → ADD +1
- 实际当前金价 5 年分位 61.58% + 实际利率 DFII10 2.91%（commit 6e679b3 现行快照）：
  - 指标 A 61.58% → 3 分（正常区间 [50%, 70%)）
  - 指标 B 2.91% → 2 分（实际利率本身 [2%, 3%) 低估区间）
  - 综合 (3+2)/2 = 2.5 → "正常"（interpret_composite_score 半开区间 [2.5, 3.5)）
  - 当前 0.25 仓 + 综合 2.5 < 3.0 → **ADD +1**（凑到 1 仓）
