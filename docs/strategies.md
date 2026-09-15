# 内置策略详解

> 4 个内置策略的设计思路、标的、再平衡规则、参考阅读。

## 总览

| ID | 名字 | 标的数 | 再平衡 | 思路 |
| --- | --- | --- | --- | --- |
| `60_40` | 60/40 经典股债 | 2 | yearly | Brinson-Hood-Beebower (1986) |
| `permanent_portfolio` | 永久组合 | 4 | yearly | Browne (1999) Fail-Safe Investing |
| `all_weather` | 桥水全天候（简化版） | 5 | yearly | Dalio (2005) |
| `risk_parity` | 风险平价 | 4 | monthly + threshold | Qian (2005) |

所有策略默认 `base_currency: usd`，inception 是底层标的里最晚成立的 ETF 的上市日（更早没数据，回测会自动跳过 NaN）。

---

## 60/40 经典股债

```yaml
# VT 60% + BND 40%
target_weights:
  VT: 0.60
  BND: 0.40
rebalance: yearly
inception: 2007-09-26   # BND 上市
```

**标的：**

- `VT` — Vanguard Total World Stock ETF（全球股票，含发达市场 + 新兴市场）
- `BND` — Vanguard Total Bond Market ETF（美国投资级债券总览）

**为什么这俩：**

- VT 一只覆盖全球股票，省去自己配美股/欧股/新兴市场的麻烦
- BND 是美国债的"宽基"，到期分散在 1-30 年
- 60/40 是个老古董（Brinson-Hood-Beebower 1986），但几十年来一直是退休金默认配置，因为：长期股票收益 ≈ 7-10%，长期债券收益 ≈ 3-5%，两者负相关或低相关，"免费的午餐"

**回测特征（参考）：**

- 长期 CAGR ≈ 6-8%
- 最大回撤在股灾年（如 2008、2022）会到 -30% 上下
- 夏普 0.4-0.6

---

## 永久组合（Harry Browne）

```yaml
# VTI 25% + AGG 25% + GLD 25% + SHY 25%
target_weights:
  VTI: 0.25
  AGG: 0.25
  GLD: 0.25
  SHY: 0.25
rebalance: yearly
inception: 2004-11-18   # GLD 上市
```

**标的：**

- `VTI` — Vanguard Total Stock Market ETF（美国全市场股票）
- `AGG` — iShares Core US Aggregate Bond ETF（美国投资级债券）
- `GLD` — SPDR Gold Shares（实物黄金）
- `SHY` — iShares 1-3 Year Treasury Bond ETF（短期国债，近似现金）

**为什么是这四样：**

Browne 在 *Fail-Safe Investing*（1999）里说，经济只有四种状态——

| 状态 | 涨 | 跌 |
| --- | --- | --- |
| 繁荣（inflation ↑, growth ↑） | 股票 | 债券 |
| 衰退（inflation ↓, growth ↓） | 债券 | 股票 |
| 通胀（inflation ↑, growth ↓） | 黄金 | 股票 |
| 通缩（inflation ↓, growth ↑） | 现金 | 黄金 |

四等分，每种状态至少有一种资产在涨。

**回测特征：**

- CAGR ≈ 5-8%（略低于 60/40，因为 SHY 拖后腿）
- **最大回撤小很多**（通常 -10% 到 -15%），这是它的卖点
- 夏普通常高于 60/40（因为回撤小）

**坑：**

- 短期国债（SHY）长期收益低，是组合的"成本"。Browne 自己用的是真现金（货币基金），不是 SHY。但 SHY 流动性好、能用 ETF 回测。

---

## 桥水全天候（简化版）

```yaml
# VTI 30% + TLT 40% + IEF 15% + GLD 7.5% + DJP 7.5%
target_weights:
  VTI: 0.30
  TLT: 0.40
  IEF: 0.15
  GLD: 0.075
  DJP: 0.075
rebalance: yearly
inception: 2002-07-26   # TLT 上市
```

**标的：**

- `VTI` — 美国全市场股票
- `TLT` — 20 年以上长期国债（对通胀敏感，衰退时涨）
- `IEF` — 7-10 年中期国债（久期短一些）
- `GLD` — 黄金
- `DJP` — iPath Bloomberg Commodity Index ETN（大宗商品总览）

**为什么这组合：**

Dalio 的全天候原版（1996 年起）很复杂——用杠杆、衍生品、通胀挂钩债券。零售版简化成 5 只 ETF，思路仍然是：

| 象限 | 配置 |
| --- | --- |
| 增长 + 通胀 | 股票 (VTI) + 商品 (DJP) |
| 增长 + 通缩 | 中期国债 (IEF) |
| 衰退 + 通胀 | 黄金 (GLD) + 长期国债 (TLT) |
| 衰退 + 通缩 | 长期国债 (TLT) + 中期国债 (IEF) |

股票 30%、债（TLT + IEF）55%、商品（DJP + GLD）15%。股票故意少配，因为股票的"风险贡献"远大于债（同样的钱波动更大）。

**回测特征：**

- CAGR 跟 60/40 差不多或略低（4-7%）
- 最大回撤通常 **比 60/40 小**（因为债占比高）
- 2022 年这种"股债双杀 + 通胀"的场景表现会糟糕（TLT -30%+、VTI -20%+、GLD 也跌）

**坑：**

- DJP 是 ETN（不是 ETF），有发行方信用风险。零售版没更好的选择；想升级可以换成 `PDBC` 或 `GSG`
- TLT 对利率极敏感，2022 通胀冲击是组合最大黑天鹅

---

## 风险平价（Risk Parity）

```yaml
# VTI / AGG / GLD / VNQ 各 25%（fallback）
# 真实权重 = 1/volatility（按历史波动率倒数加权）
target_weights:
  VTI: 0.25
  AGG: 0.25
  GLD: 0.25
  VNQ: 0.25
rebalance: monthly + threshold 10%
inception: 2004-09-23   # GLD ETF 上市参考
```

**标的：**

- `VTI` — 美国全市场股票
- `AGG` — 美国投资级债券
- `GLD` — 黄金
- `VNQ` — Vanguard Real Estate ETF（REITs）

**跟 60/40 的关键区别：**

60/40 是**金额**配（股票占 60% 的钱），风险平价是**风险贡献**配（每种资产对组合总风险的贡献相等）。

实现方式：每种资产的权重 ∝ 1 / 它的历史波动率。股票波动大 → 权重小；债券波动小 → 权重大（往往配到 60-70%）。回测引擎（`src/global_allocation/backtest/risk_parity.py`）用过去所有日收益的标准差估算 vol。

为什么这样做：因为 60/40 的真实风险主要来自股票（钱占 60% 但风险占 90%），调整后组合的风险更分散。

**再平衡规则：**

- **monthly**：每月再平衡
- **threshold 10%**：任一权重偏离 target 超过 10% 立即触发

**回测特征：**

- CAGR 通常 ≈ 5-7%
- 最大回撤比 60/40 小（因为债被加杠杆式加重）
- 夏普通常更高（0.6-0.8）

**坑：**

- 这是**简化版**风险平价（用 inverse-volatility），不是 Bridgewater 的 ERC（Equal Risk Contribution）。后者需要解非线性方程组
- 没有杠杆，所以风险平价"配到债 70%"的想法在零售版实现不出来
- 真实场景下需要用 margin / 衍生品加杠杆到 vol target（一般 10-15%）

---

## 怎么选

| 你的偏好 | 推荐策略 |
| --- | --- |
| 简单、可解释 | `60_40` |
| 回撤小、心理舒服 | `permanent_portfolio` |
| 想分散到商品、通胀对冲 | `all_weather` |
| 想"风险贡献均衡" | `risk_parity` |

或者照抄组合：50% 60/40 + 25% 永久 + 25% 全天候 = "懒人全天候"。

## 参考阅读

- Brinson, Hood, Beebower (1986) — *Determinants of Portfolio Performance* — 60/40 的学术基础
- Browne (1999) — *Fail-Safe Investing* — 永久组合原书
- Dalio (2005) — *Principles: Ray Dalio* — 全天候思路
- Qian (2005) — *Risk Parity Portfolios: Efficient Portfolios Through True Diversification* — 风险平价公式化
- Faber (2013) — *Global Asset Allocation: A Survey of the World's Top Investment Strategies* — 跨策略对比
