# Spec 098: 周报 Section 3 — 大类资产估值

> 状态：Draft
> 最后更新：2026-09-19
> 作者：liubo
> 实现 PR（如果有）：待

## 目标

给 spec 096/097 周报卡片加 Section 3 — **大类资产估值**：在卡片上展示用户当前持有的大类资产（第一期只做 A 股股票）的估值指标，让用户一眼看出"现在是不是值得加仓"。

回答的问题：
- A 股现在估值偏高还是偏低？
- 跟过去 10 年比在什么位置？
- 相对债券吸引力如何？
- 相对 GDP 总量看是否有泡沫？

## 不在范围内

- **不做其他 super category 的估值**（第一期只做 A 股；美股 / 港股 / REITs / 债券 / 商品 后续 spec 098.x 单独做）
- **不做基于估值的自动调仓建议**（只展示指标 + 阈值判断，不说"该买/该卖"）
- **不做估值历史曲线图**（只展示当前点 + 分位数；曲线图未来 spec 098+）
- **不存数据库的估值历史**（估值数据每天变，长期历史由 akshare 兜底；本地只存最新一天的数据 + 最近 10 年的 PE 历史快照用于分位计算）

## 选定的 4 个估值指标（A 股专用）

| # | 指标 | 公式 | 阈值（低/正常/高） | 数据源（akshare） |
| --- | --- | --- | --- | --- |
| 1 | **股债利差**（核心） | 1 ÷ PE-TTM − 10年国债收益率 | > 5% 低 / 2%~5% 正常 / < 2% 高 | `stock_zh_index_value_dbj_b`（PE）+ `bond_china_yield_curve`（10Y） |
| 2 | **PE 分位** | 当前 PE 在过去 10 年 PE 序列里的百分位 | < 30% 低 / 30%~70% 正常 / > 70% 高 | `stock_zh_index_value_dbj_b`（历史 PE） |
| 3 | **巴菲特指标** | A股总市值 ÷ 中国 GDP | < 50% 低 / 50%~80% 正常 / > 80% 高 | `stock_zh_a_spot_em`（总市值）+ `macro_china_gdp` |
| 4 | **股息率** | 中证全A 分红总额 ÷ 总市值 | > 3% 低 / 1%~3% 正常 / < 1% 高 | `stock_zh_index_value_dbj_b` |

### 为什么这 4 个指标（liubo 2026-09-19 反馈确认）

- 股债利差：唯一一个"回答股债切换决策"的指标，符合 Swensen 框架的核心思想
- PE 分位：把绝对估值放到历史上下文中解读，比单看 PE 数字稳健
- 巴菲特指标：宏观锚 — 整个市场相对经济总量是否泡沫
- 股息率：侧面验证（公司愿意分钱 = 估值不会离谱）

### 为什么用 `000985` 中证全A

- 中证全A = 沪深两市所有 A 股，最能代表"A 股整体"
- akshare 的 `stock_zh_index_value_dbj_b(symbol="000985")` 直接返回 PE/PB/股息率
- 其他选择（沪深300 / 中证1000）是大盘或中小盘的代表，跟用户"整个 A 股"的直觉不一致

### 实现注记（2026-09-19 实装时发现 akshare 实际行为差异）

spec 表里的 `stock_zh_index_value_dbj_b` 函数在已装的 akshare 版本里**不存在**，且每个接口返回数据规模和口径差异大。实际实现替换为：

| 指标 | spec 原计划 | 实际实现 | 原因 |
| --- | --- | --- | --- |
| 股债利差 — 当前 PE | `stock_zh_index_value_dbj_b("000985")` | `stock_a_ttm_lyr().middlePETTM.iloc[-1]` | 全 A 中位数 PE，跟 history 同口径（percentile 计算需要 current/history 同口径） |
| PE 历史 | `stock_zh_index_value_dbj_b` | `stock_a_ttm_lyr().middlePETTM`（261 行月频） | dbj_b 只返回 20 天，10 年分位用不了；全 A 历史有 21 年月频 |
| 巴菲特 — 总市值 | `stock_zh_a_spot_em`（实时） | `stock_sse_summary + stock_szse_summary`（当日）→ fallback `macro_china_stock_market_cap`（月度）→ 最后 `stock_zh_a_spot_em` | eastmoney 代理不稳时实时接口失败；上交所+深交所当日数据更稳 |
| 巴菲特 — GDP | `macro_china_gdp`（最新季度） | `macro_china_gdp`（最新**年度**= 第1-4季度 行） | GDP 是流量，应该用年度而不是季度累计；H1=69.57 万亿是半年不是全年 |
| 股息率 | `stock_zh_index_value_dbj_b` | `stock_zh_index_value_csindex("000985")`（已装 akshare 的替代） | 同源 PE，股息率口径是"中证全指"（== "中证全A" 都是沪深所有 A 股，差异 < 5%） |

### 阈值依据

- 股债利差：> 5% / < 2% — 业内通用（中证全A 股债利差长期均值约 3.5%）
- PE 分位：< 30% / > 70% — 自己设的稳健阈值
- 巴菲特指标：< 50% / > 80% — 巴菲特原话（美国市场经验，A股直接借用）
- 股息率：> 3% — 中证全A 长期均值约 2%，3% 算偏高

## 数据契约

### 存储（SQLite 新表）

```sql
CREATE TABLE IF NOT EXISTS valuation_indicators (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    record_date     TEXT NOT NULL,          -- YYYY-MM-DD，估值快照日期
    indicator_code  TEXT NOT NULL,          -- 指标代码（见下）
    value           TEXT NOT NULL,          -- Decimal（精度无损）
    source          TEXT NOT NULL,          -- "akshare:stock_zh_index_value_dbj_b" 等
    UNIQUE (record_date, indicator_code)
);
```

### Pydantic Model

```python
class ValuationIndicator(_FrozenModel):
    record_date: date
    indicator_code: str  # "equity_risk_premium" / "pe_percentile" / "buffett_indicator" / "dividend_yield"
    value: Decimal
    source: str
```

### 指标代码枚举

```python
class ValuationIndicatorCode(str, Enum):
    EQUITY_RISK_PREMIUM = "equity_risk_premium"   # 股债利差
    PE_PERCENTILE = "pe_percentile"               # PE 分位
    BUFFETT_INDICATOR = "buffett_indicator"       # 巴菲特指标
    DIVIDEND_YIELD = "dividend_yield"             # 股息率
```

### 卡片 Section 3 数据结构

```python
{
    "tag": "note",
    "elements": [{"tag": "plain_text", "content": "大类资产估值"}],
},
{"tag": "hr"},
{
    "tag": "table",
    "columns": [
        {"name": "indicator", "display_name": "指标", ...},
        {"name": "value", "display_name": "当前", ...},
        {"name": "verdict", "display_name": "评估", ...},
        {"name": "threshold", "display_name": "阈值", ...},
    ],
    "rows": [
        {"indicator": "股债利差", "value": "+5.2%", "verdict": "偏低估", "threshold": ">5% 低 / <2% 高"},
        {"indicator": "PE 分位", "value": "28%", "verdict": "偏低估", "threshold": "<30% 低 / >70% 高"},
        {"indicator": "巴菲特指标", "value": "65%", "verdict": "正常", "threshold": "<50% 低 / >80% 高"},
        {"indicator": "股息率", "value": "2.5%", "verdict": "正常", "threshold": ">3% 低 / <1% 高"},
    ],
},
```

### 数据获取层

```python
# src/global_allocation/portfolio/valuation_source.py

class ValuationSource(Protocol):
    """估值数据源接口（akshare 适配）"""
    def get_pe_ttm(self, index_code: str = "000985", on: date) -> Decimal | None: ...
    def get_pe_history(self, index_code: str = "000985", years: int = 10) -> list[Decimal]: ...
    def get_dividend_yield(self, index_code: str = "000985", on: date) -> Decimal | None: ...
    def get_10y_treasury_yield(self, on: date) -> Decimal | None: ...
    def get_a_share_total_market_cap(self, on: date) -> Decimal | None: ...
    def get_china_gdp(self, on: date) -> Decimal | None: ...


class AkshareValuationSource:
    """akshare 数据源实现"""
    # 6 个方法分别对应上面接口的实现
    # 用 try/except 包好 akshare 调用，失败返回 None（不抛异常）
```

### 计算层

```python
# src/global_allocation/portfolio/valuation_indicators.py

def compute_equity_risk_premium(pe_ttm: Decimal, treasury_yield: Decimal) -> Decimal:
    """股债利差 = 1/PE - 国债收益率。返回 0~1 范围的小数。"""

def compute_pe_percentile(current_pe: Decimal, pe_history: list[Decimal]) -> Decimal:
    """PE 分位 = (当前 PE - 历史最小) / (历史最大 - 历史最小)。返回 0~1。"""

def compute_buffett_indicator(market_cap: Decimal, gdp: Decimal) -> Decimal:
    """巴菲特指标 = 总市值 / GDP。返回 0~N 范围的倍数。"""

def compute_verdict(code: ValuationIndicatorCode, value: Decimal) -> str:
    """根据阈值返回"偏低估"/"正常"/"偏高估"。"""
```

## CLI

```bash
# 拉最新估值入库（手动刷新）
gap valuation update

# 显示当前估值（数据库里最新一天的所有指标）
gap valuation show

# 发周报到飞书（自动检查 + 拉新估值）
gap portfolio publish --root <mid>
```

`publish` 行为：检查数据库里 `valuation_indicators.record_date == today` 是否齐全，不全自动调 `valuation update` 拉新。

## 边界情况

- **akshare 接口失败**：返回 None，compute 函数不调用（跳过该指标），卡片 Section 3 该行显示 "数据缺失"
- **10 年 PE 历史数据不足 10 年**：能取多少取多少，分位计算用 available 数据
- **GDP 数据是季度**：用最近一季度作为当前值，巴菲特指标每月更新一次
- **当天已拉过估值**：缓存命中，不重复拉
- **PE 是负数或异常大**：视为数据异常，跳过该指标
- **同一天多次拉取**：UNIQUE (record_date, indicator_code) 保证只有一条

## 验收标准

- [ ] spec 098 写完
- [ ] SQLite 新表 `valuation_indicators` + DB CRUD 方法
- [ ] Pydantic model `ValuationIndicator` + enum `ValuationIndicatorCode`
- [ ] `valuation_source.py` 6 个 akshare 适配函数（mock 测试）
- [ ] `valuation_indicators.py` 4 个 compute 函数（unit test 覆盖 100%）
- [ ] CLI `gap valuation update` + `gap valuation show`
- [ ] `gap portfolio publish` 自动拉新估值（如缺）
- [ ] card.py Section 3（4 行 × 4 列估值表）+ 集成
- [ ] 卡片结构测试通过
- [ ] 测试覆盖率 ≥ 80%
- [ ] ruff clean
- [ ] 实际跑 `gap valuation update` 拿到真实数据
- [ ] 发飞书视觉验证 Section 3 渲染正确（4 行指标 + 评估文案 + 阈值参考）

## 依赖

- spec 090（PortfolioDB — 已有 SQLite 基础设施）
- spec 097（Section 1/2 已交付，Section 3 是增量）
- akshare（已有依赖）

## 备注

### 为什么不直接算后塞进持仓表

持仓表（Section 1）是子类粒度（11 行），估值是 super category 粒度（1 行 / super category）。粒度不同，放在一起反而混淆。Section 3 独立一节更清晰。

### 为什么阈值写死 + 后续可调

第一期阈值用我建议的默认值，存常量。后续可以根据用户实际反馈在 `DEFAULT_THRESHOLDS` 里调整（不动 compute 函数）。

### 为什么不画估值历史曲线

曲线图在飞书卡片上支持有限（VChart label 限制），且每周发一次卡片，曲线意义不大（一年才 52 个点）。分位数 + 当前值 已经能传达"位置"信息。

### 后续 spec 098.x

- `spec 098.1` — 美股估值（Shiller PE / US 10Y ERP）
- `spec 098.2` — 港股估值（恒生 PE）
- `spec 098.3` — REITs 估值（P/FFO + 股息率）
- `spec 098.4` — 债券估值（10Y 国债 + 利差分析）
- `spec 098.5` — 商品估值（黄金 + 实际利率）

每个 sub-spec 沿用本 spec 的"4 个指标"框架，阈值按对应市场调整。