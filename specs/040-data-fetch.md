# Spec 040: 历史数据获取

> 状态：Draft
> 最后更新：2026-09-15

## 目标

从 yfinance（美股/全球）和 akshare（A股/港股）拉历史 OHLCV 数据，本地 SQLite 缓存，支持增量更新。

## 不在范围内

- 实时 tick 数据
- 财报、新闻、情绪面数据
- 期权/期货/外汇衍生品

## API 概览

```python
from datetime import date
from global_allocation.data import fetch_ohlcv
from global_allocation.models import Asset

aapl = Asset(
    symbol="AAPL",
    name="Apple Inc.",
    asset_class=AssetClass.EQUITY,
    region=Region.US,
    currency=Currency.USD,
    data_source=DataSource.YFINANCE,
)

df = fetch_ohlcv(
    asset=aapl,
    start=date(2020, 1, 1),
    end=date(2025, 1, 1),
    use_cache=True,       # 默认 True
    refresh=False,        # True 强制重新拉
)

# df 字段：index=Date, columns=[Open, High, Low, Close, Adj Close, Volume]
# 数据源：yfinance 直接给 Adj Close；akshare 给的是前复权，等价
```

### 多标的批量

```python
from global_allocation.data import fetch_many

assets = [aapl, msft, ...]
prices = fetch_many(assets, start=..., end=..., how="inner")  # how: inner/outer
# 返回 wide-format DataFrame：columns=MultiIndex (asset, field)
# 或简化的 Close-only：columns=asset.symbol
```

## 数据契约

### 返回 DataFrame

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| Open | float | 开盘价 |
| High | float | 最高价 |
| Low | float | 最低价 |
| Close | float | 收盘价 |
| Adj Close | float | 复权收盘价（yfinance）；akshare 转前复权后用 Close |
| Volume | int | 成交量 |

### 缓存表

```sql
CREATE TABLE IF NOT EXISTS ohlcv_cache (
    symbol    TEXT NOT NULL,
    source    TEXT NOT NULL,        -- 'yfinance' / 'akshare'
    date      DATE NOT NULL,
    open      REAL NOT NULL,
    high      REAL NOT NULL,
    low       REAL NOT NULL,
    close     REAL NOT NULL,
    adj_close REAL NOT NULL,
    volume    INTEGER NOT NULL,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, source, date)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_date ON ohlcv_cache (symbol, date);
```

## 边界情况

1. **股票已退市** —— yfinance 返回历史但不再更新；用最后一天的 `fetched_at` 判断 staleness，超过 7 天提示
2. **数据源临时不可用** —— 报错 + 提示用户检查网络（不自动切换源）
3. **请求区间在 IPO 之前** —— 取实际可得的日期范围（不要假装填 0 或 NaN）
4. **缓存里有，但 freshness 不够** —— 默认走缓存（性能优先），`refresh=True` 才重新拉
5. **跨市场交易时间不一致**（A 股 vs 美股） —— 按本地日历，不做时区对齐（按交易日，不做 tick 级）
6. **币种换算** —— MVP 不做，假设所有资产按其本地币种计价（spec 110 处理）

## 验收标准

- [ ] `fetch_ohlcv()` 单标的能跑通（mock 测试 + 1 个 integration test 真实拉 AAPL）
- [ ] `fetch_many()` 多标的返回正确 wide-format
- [ ] SQLite 缓存读写正确，第二次请求走缓存（用 mock 验证不调用 network）
- [ ] 失败有清晰错误信息（包含 asset symbol + source）
- [ ] `tests/unit/test_data_fetch.py` + `tests/integration/test_data_fetch_live.py`（标记 `integration`）
- [ ] 覆盖率 ≥ 80%（unit + integration 合并）

## 依赖

- `specs/010-data-models.md`
- yfinance
- akshare
- sqlite3（stdlib）
- pandas

## 备注

- **API 限流**：yfinance 没官方限流文档，但社区建议每次请求间隔 1 秒。fetch_many 里加 0.5s sleep
- **akshare 稳定性**：akshare 接口偶尔会变版本，要锁版本（pyproject.toml 里 `akshare>=1.13,<2`）
- **数据准确性**：MVP 不做数据质量检查（如 outlier detection），靠数据源本身
- **增量更新**：MVP 缓存所有请求的日期，不做"只拉最新一天"。后续 spec 045 加 smart refresh
