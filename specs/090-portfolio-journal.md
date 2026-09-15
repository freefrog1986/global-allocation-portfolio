# Spec 090: 实盘持仓账本（Portfolio Journal）

> 状态：Draft
> 最后更新：2026-09-15
> 作者：liubo
> 实现 PR（如果有）：TBD

## 目标

让用户通过 CLI 录入真实的基金持仓和买卖交易，自动维护一份「实盘账本」，并能：
- 看当前持仓、成本、市值、浮动盈亏
- 查历史每笔交易（含当时策略 / 标签 / 手续费）
- 每周自动跑一张快照 + 发飞书卡片，看周涨跌 / 累计收益 / 持仓占比

## 不在范围内

- 券商 API 自动同步（以后再说，先做手动）
- 多组合 / 多账户（MVP 只支持单个组合）
- 多币种（MVP 只支持人民币，按用户需求）
- 税务计算（盈亏口径是「浮动 + 已实现」简单算，不算税）
- 自动再平衡建议（跟回测引擎那条路分开）

## 用法概览

```bash
# 初始化（一次）
gap portfolio init --name "我的组合"

# 加基金
gap portfolio fund add 163406 --name "兴全合润"
gap portfolio fund add 510300  --name "华泰柏瑞沪深300"

# 记买入（用户通过飞书告诉我，我执行这条命令）
gap portfolio buy 163406 \
  --date 2026-09-10 \
  --shares 1000 \
  --price 2.350 \
  --fee 1.20 \
  --strategy "月度定投扣款" \
  --tags dca monthly

# 记卖出
gap portfolio sell 163406 \
  --date 2026-09-12 \
  --shares 200 \
  --price 2.450 \
  --fee 1.00 \
  --strategy "止盈减仓" \
  --tags profit-take

# 看现在
gap portfolio show               # 当前持仓 + 浮动盈亏
gap portfolio tx list            # 所有交易
gap portfolio tx list --fund 163406 --tag dca

# 打快照 + 周报
gap portfolio snapshot           # 现在拍一张（带最新 NAV）
gap portfolio report --weekly    # 算本周涨跌 + 累计
gap portfolio publish --weekly   # 发飞书卡片（每周自动跑这个）
```

### 飞书录入口径

用户从飞书发我，标准格式：

```
buy 163406 1000 2.35 策略:月度定投扣款 费:1.2
sell 163406 200 2.45 策略:止盈减仓 费:1.0
```

或者更随意：

```
我今天买了 163406 一千份 2.35 块，定投扣款
```

我负责解析 + 转成 CLI 命令跑。

### 周报自动跑

`gap portfolio publish --weekly` 设计成幂等可重入：每次跑都拍新快照（不覆盖历史）+ 发飞书。用户配 cron：

```bash
# 每周五 17:00 收盘后跑一次
0 17 * * 5  cd ~/projects/global-allocation-portfolio && source .venv/bin/activate && gap portfolio publish --weekly >> ~/.gap/cron.log 2>&1
```

或者 GitHub Actions weekly cron 也行。

## 数据契约

### Fund

```python
class Fund(BaseModel):
    code: str                    # 6 位基金代码（如 "163406"）
    name: str                    # 显示名
    asset_class: AssetClass      # equity / bond / commodity / reit / mixed
    data_source: DataSource      # akshare（公开基金净值）
    currency: Currency = CNY     # MVP 固定
```

### Transaction

```python
class TransactionSide(str, Enum):
    BUY = "buy"
    SELL = "sell"

class Transaction(BaseModel):
    id: int | None               # DB 自增
    fund_code: str
    side: TransactionSide
    date: date
    shares: Decimal              # > 0
    price: Decimal               # > 0，单位 CNY
    fee: Decimal = Decimal("0")  # >= 0
    strategy: str | None = None  # 自由文本：「跌破 90 日均线加仓」「止盈减仓」
    tags: list[str] = []         # 标签列表：dca / profit-take / rebalance / ad-hoc
    note: str | None = None      # 额外备注
    created_at: datetime         # 录入时间（非交易日期）
```

### Holding（计算字段，不存表）

实时计算 = 所有 BUY - 所有 SELL，按 fund_code 聚合：

```python
@dataclass
class Holding:
    fund: Fund
    shares: Decimal              # 当前持仓
    avg_cost: Decimal            # 加权平均成本（含费）
    market_price: Decimal | None # 最新净值（拉 akshare）
    market_value: Decimal | None # shares * market_price
    cost_basis: Decimal          # shares * avg_cost
    unrealized_pnl: Decimal      # market_value - cost_basis
    unrealized_pnl_pct: Decimal  # pnl / cost_basis
```

### WeeklySnapshot

```python
class WeeklySnapshot(BaseModel):
    week_end_date: date          # 周五日期（或最后交易日）
    total_value: Decimal
    week_return: Decimal         # 本周涨跌幅（vs 上一个快照）
    cumulative_return: Decimal   # 累计涨跌幅（vs 首次有完整持仓的快照）
    holdings_json: str           # 序列化：[{fund_code, shares, market_value, weight, pnl_pct}, ...]
    created_at: datetime
```

`holdings_json` 存 JSON 字符串，因为 holdings 是组合维度的派生数据 + 列表结构。

### Portfolio

单组合设计，不存 ID（直接 hard-code `default`）。如果以后要多组合，加 `Portfolio` 表。

## 存储

单独 SQLite：`$XDG_DATA_HOME/gap/portfolio.db`（默认 `~/.local/share/gap/portfolio.db`）。

不复用回测的 `prices.db`，因为：
1. 实盘账本是「写入为主」，回测缓存是「读取为主」
2. 备份 / 清理生命周期不同
3. 用户可能想从不同设备访问实盘账本（将来）

表结构：

```sql
CREATE TABLE funds (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    data_source TEXT NOT NULL,
    currency    TEXT NOT NULL DEFAULT 'CNY'
);

CREATE TABLE transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_code   TEXT NOT NULL,
    side        TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    date        TEXT NOT NULL,                -- ISO date
    shares      TEXT NOT NULL,                -- Decimal as text
    price       TEXT NOT NULL,                -- Decimal as text
    fee         TEXT NOT NULL DEFAULT '0',
    strategy    TEXT,
    tags        TEXT,                         -- JSON array
    note        TEXT,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (fund_code) REFERENCES funds(code)
);

CREATE INDEX idx_tx_fund_date ON transactions(fund_code, date);

CREATE TABLE weekly_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    week_end_date       TEXT NOT NULL UNIQUE,
    total_value         TEXT NOT NULL,
    week_return         TEXT NOT NULL DEFAULT '0',
    cumulative_return   TEXT NOT NULL DEFAULT '0',
    holdings_json       TEXT NOT NULL,
    created_at          TEXT NOT NULL
);
```

Decimal 全存 text（精度无损，跟回测缓存一致）。

## 模块划分

```
src/global_allocation/
  portfolio/
    __init__.py
    models.py         # Fund / Transaction / Holding / WeeklySnapshot pydantic
    db.py             # SQLite 读写
    valuation.py      # 拉最新价格、计算当前持仓 + P&L
    journal.py        # 业务逻辑：add_fund / record_tx / compute_holdings / take_snapshot / compute_report
    cli.py            # Typer 子命令
  cli.py              # 加 `gap portfolio` 把上面挂上去
```

`feishu/card.py` 加 `_build_portfolio_card(result)`：当前持仓饼图 + 周涨跌 line + 累计收益曲线 + 最近交易表。

## 估值（最新价格）

用 akshare 拉公开基金净值：

- `ak.fund_open_fund_info_em(symbol="163406", indicator="单位净值")` → 最近净值
- 频率：每天拉一次够用（盘后才更新）
- 缓存：reuse `prices.db` 的 SQLite 缓存（key=fund_code）还是新建？**新建** `prices_funds.db`，避免跟股票/回测混

估算 NAV = SUM(shares_i * nav_i) for all funds。

## 周快照生成算法

```python
def take_snapshot(week_end_date: date) -> WeeklySnapshot:
    holdings = compute_holdings()              # 当前所有持仓 + market_value
    total = sum(h.market_value for h in holdings)
    
    # 拿上周快照算周涨跌
    prev = get_latest_snapshot_before(week_end_date)
    week_return = (total / prev.total_value - 1) if prev else Decimal("0")
    
    # 累计：从最早一个 total_value 算起
    first = get_first_snapshot_with_value()
    cumulative = (total / first.total_value - 1) if first else Decimal("0")
    
    holdings_json = json.dumps([
        {"fund_code": h.fund.code, "shares": str(h.shares), "market_value": str(h.market_value), "weight": str(h.market_value/total), "pnl_pct": str(h.unrealized_pnl_pct)}
        for h in holdings
    ])
    
    return WeeklySnapshot(week_end_date=week_end_date, total_value=total, week_return=week_return, cumulative_return=cumulative, holdings_json=holdings_json, created_at=now())
```

幂等：`INSERT OR IGNORE` 凭 `week_end_date UNIQUE`，重复跑不会覆盖历史。

## 边界情况

- **边界 1**：用户卖出多于持仓的份额 → 抛 ValueError「持仓不足」
- **边界 2**：基金代码未登记就交易 → 抛 ValueError「基金未登记，请先 `fund add`」
- **边界 3**：拉不到基金最新净值 → 标记 `market_price=None`，snapshot 抛错让用户重试
- **边界 4**：同一基金同一日多次买入 → 简单累加（不在合并，BUY 是离散事件）
- **边界 5**：周快照重复跑 → UNIQUE 约束 + `INSERT OR IGNORE` 跳过
- **边界 6**：第一次 snapshot 没有 prev → week_return = 0
- **边界 7**：CLI --date 给未来日期 → 接受（用户可以事后录）
- **边界 8**：负 fee / 负 price → 校验拒绝

## 验收标准

- [ ] `gap portfolio init` 创建空 portfolio
- [ ] `gap portfolio fund add` 写入 funds 表
- [ ] `gap portfolio buy/sell` 写入 transactions 表
- [ ] `gap portfolio show` 显示当前持仓 + P&L（用 mock 价格避免网络）
- [ ] `gap portfolio tx list --tag dca` 正确过滤
- [ ] `gap portfolio snapshot` 落 weekly_snapshots 一行
- [ ] `gap portfolio report --weekly` 计算 week_return / cumulative_return 正确
- [ ] `gap portfolio publish --weekly --dry-run` 生成飞书卡片 JSON
- [ ] 单元测试 ≥ 85% 覆盖（journal + db + models）
- [ ] mypy strict + ruff 干净
- [ ] docs/journal-guide.md（用户文档）写完

## 风险

1. akshare 公开基金数据接口稳定性 — 抓不到就退化（用户可以手动传价）
2. Decimal 序列化到 SQLite text 会有点繁琐，但避免 float 精度问题
3. 持仓的「成本基础」算法选 weighted average（简单），未来可能需要 FIFO / LIFO（税务计算用）