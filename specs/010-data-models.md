# Spec 010: 核心数据模型

> 状态：Draft
> 最后更新：2026-09-15

## 目标

定义整个系统的"名词表"：资产、组合、策略、回测结果。所有的数据流转都基于这些类型。

## 不在范围内

- 数据库 schema（spec 040 单独定义 SQLite 表结构）
- API endpoint（spec 070 CLI 单独定义）

## 数据契约

所有 model 用 Pydantic v2 BaseModel 实现，方便序列化 + 校验。

### Asset

代表一个可投资的标的（如 `AAPL`、`510300`、`000300`）。

```python
class Asset(BaseModel):
    symbol: str                 # ticker：'AAPL' / '510300.SH' / '000300.SZ'
    name: str                   # 显示名：'Apple Inc.' / '沪深300ETF'
    asset_class: AssetClass     # EQUITY / BOND / COMMODITY / REIT / CASH
    region: Region              # CN / HK / US / EU / JP / GLOBAL
    currency: Currency          # CNY / USD / HKD / EUR / JPY
    data_source: DataSource     # YFINANCE / AKSHARE
```

### AssetClass

```python
class AssetClass(str, Enum):
    EQUITY = "equity"
    BOND = "bond"
    COMMODITY = "commodity"
    REIT = "reit"
    CASH = "cash"
```

### Region / Currency / DataSource

```python
class Region(str, Enum):
    CN = "cn"
    HK = "hk"
    US = "us"
    EU = "eu"
    JP = "jp"
    GLOBAL = "global"

class Currency(str, Enum):
    CNY = "cny"
    USD = "usd"
    HKD = "hkd"
    EUR = "eur"
    JPY = "jpy"

class DataSource(str, Enum):
    YFINANCE = "yfinance"
    AKSHARE = "akshare"
```

### TargetWeight

策略对单一资产的目标权重。

```python
class TargetWeight(BaseModel):
    asset: Asset
    weight: Decimal             # 0.0 ~ 1.0，4 位小数
    # 注意：所有策略内 TargetWeight 的 weight 之和必须 = 1.0（在 strategy 层校验）
```

### RebalanceRule

再平衡规则。

```python
class RebalanceRule(BaseModel):
    frequency: Literal["monthly", "quarterly", "yearly", "none"]
    threshold: Decimal | None   # 0.05 表示偏离超过 5% 才再平衡（可选）

    @model_validator(mode="after")
    def _validate(self) -> "RebalanceRule":
        if self.threshold is not None and not (0 < self.threshold < 1):
            raise ValueError("threshold must be in (0, 1)")
        return self
```

### Strategy

策略由 metadata + target weights + rebalance rule 组成。

```python
class Strategy(BaseModel):
    id: str                     # '60_40' / 'permanent_portfolio'
    name: str                   # 显示名：'60/40 经典股债'
    description: str            # 一段话说清策略
    target_weights: list[TargetWeight]
    rebalance: RebalanceRule
    base_currency: Currency     # 净值用什么币种计价（默认 USD）
    inception: date             # 策略适用的最早日期
    metadata: dict[str, Any]    # 自由字段（标签、备注）
```

### Portfolio（运行时状态）

回测过程中每一期的持仓快照（不在策略文件里，由回测引擎产出）。

```python
class PortfolioSnapshot(BaseModel):
    date: date
    total_value: Decimal        # 用 base_currency 计价
    positions: dict[str, Decimal]   # symbol → 当前市值
    weights: dict[str, Decimal]     # symbol → 当前权重
    cash: Decimal
```

### BacktestResult

一次回测的完整输出。

```python
class BacktestResult(BaseModel):
    strategy_id: str
    start_date: date
    end_date: date
    initial_capital: Decimal
    final_value: Decimal
    equity_curve: pd.DataFrame  # index=date, columns=[total_value, pnl]
    snapshots: list[PortfolioSnapshot]
    metrics: PerformanceMetrics  # 见 spec 060
    rebalance_events: list[RebalanceEvent]
```

### RebalanceEvent

```python
class RebalanceEvent(BaseModel):
    date: date
    triggered_by: Literal["schedule", "threshold"]
    trades: list[Trade]
    cost_bps: Decimal           # 交易成本（基点）
```

### Trade

```python
class Trade(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    shares: Decimal
    price: Decimal
    fee: Decimal
```

## 验收标准

- [ ] 所有 model 用 Pydantic v2，校验失败给清晰错误
- [ ] `Decimal` 用于所有金额/权重，不允许 float 混入
- [ ] `tests/unit/test_models.py` 覆盖每个 model 的边界
- [ ] 覆盖率 ≥ 90%（纯数据 model，必须高）

## 依赖

- pydantic >= 2.7
- pandas（用于 equity_curve DataFrame）

## 备注

- Decimal vs float：金融场景必须用 Decimal 避免二进制浮点误差。但 DataFrame 里存 float 性能更好 → 在序列化时统一 cast
- 货币换算：本期 MVP 假设所有资产用同一币种（USD）计价，不做汇率换算。后续 spec 110 单独处理
