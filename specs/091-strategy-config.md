# Spec 091: 策略配置管理（Strategy Config）

> 状态：Draft
> 最后更新：2026-09-18
> 作者：liubo
> 实现 PR（如果有）：TBD

## 目标

让用户通过 CLI 管理自己的多策略投资组合配置：
- 维护多条策略（A 股红利、全球配置、以后新加的），每条独立版本化
- 每条策略可拆 sleeve（子分类）+ 单基金，定义目标权重 + 容忍带
- 一键算出当前持仓 vs 目标的 drift 和再平衡建议（**按单只基金粒度**）
- 跑历史回测 + 多策略横评，对比策略表现

跟现有的 `strategies/` 模块（回测模板 60/40、全天候）完全解耦：spec 091 管**用户自己的实盘配置**，spec 030 管**回测用的预设模板**。

## 不在范围内

- 标的库 / Universe 管理（基金规模、PE 上下限等单基金风控 — 用户确认先不做，spec 092 再说）
- 券商 API 自动同步（跟 portfolio_journal 一样先手动）
- 自动信号接入（估值分位、动量 — Selection 策略先支持手动配置 + 后续可接数据源）
- 跨策略 sleeve 共享（用户确认不做，各策略独立管理）
- 实时监控 / drift 报警飞书推送（feature 6 暂缓；feature 5 算就完事）

## 用法概览

```bash
# 初始化一条策略
gap strategy init a-share-dividend \
  --name "我的 A 股红利" \
  --type selection

gap strategy init global-alloc \
  --name "全球资产配置" \
  --type allocation

# 加 sleeve（每个策略可以多个）
gap strategy sleeve add a-share-dividend financial \
  --name "金融红利" \
  --weight 0.40 --min 0.30 --max 0.50 \
  --tags "equity,dividend"

gap strategy sleeve add global-alloc equity \
  --name "股票" --weight 0.60 --min 0.50 --max 0.70

# 给 sleeve 加基金目标权重
gap strategy target add a-share-dividend financial 510300 \
  --weight 0.50 --min 0.40 --max 0.60

gap strategy target add a-share-dividend financial 008114 \
  --weight 0.50 --min 0.40 --max 0.60

# 配置 selection 策略的信号
gap strategy signal a-share-dividend set \
  --entry "valuation_percentile<30" \
  --exit "valuation_percentile>70" \
  --max-single 0.10

# 一切就绪，校验 + 列出
gap strategy check a-share-dividend
gap strategy list
gap strategy show a-share-dividend

# 切版本（v1 → v2）
gap strategy version new a-share-dividend --notes "加了消费红利 sleeve"
gap strategy activate a-share-dividend --version 2

# 算再平衡建议
gap strategy next-rebalance a-share-dividend
gap strategy rebalance a-share-dividend --dry-run   # 看清单
gap strategy rebalance a-share-dividend             # 发飞书卡片

# 回测
gap strategy backtest a-share-dividend --start 2020-01-01
gap strategy backtest global-alloc --start 2020-01-01

# 多策略横评
gap strategy compare a-share-dividend global-alloc --start 2020-01-01
```

## 数据契约

### 4 层结构

```
Strategy (顶层)
└── StrategyVersion (v1, v2, v3, ... status: draft/active/archived)
    ├── config: AllocationConfig | SelectionConfig   ← type-specific
    └── sleeves: list[PlanSleeve]
                  ├── target_weight, min_weight, max_weight
                  └── targets: list[PlanTarget]
                                ├── fund_code
                                └── weight, min_weight, max_weight
```

### Strategy（顶层）

```python
class StrategyType(str, Enum):
    ALLOCATION = "allocation"   # 全球配置这种 — 固定权重 + 再平衡
    SELECTION = "selection"    # A 股红利这种 — 选股 + 估值信号

class StrategyStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"

class Strategy(BaseModel):
    id: str                       # slug: "a-share-dividend"
    name: str                     # "我的 A 股红利"
    type: StrategyType
    description: str = ""
    active_version: int | None    # 当前生效版本号
    created_at: datetime
    updated_at: datetime
```

### StrategyVersion

```python
class StrategyVersion(BaseModel):
    id: int | None
    strategy_id: str
    version: int                  # 1, 2, 3 ...
    status: StrategyStatus
    notes: str = ""               # "2024-08 加了消费红利 sleeve"
    config: AllocationConfig | SelectionConfig   ← type-specific
    created_at: datetime
```

`config` 字段在 DB 里存 JSON 字符串（不同 type schema 不同）。

### AllocationConfig（全球配置用）

```python
class RebalanceTrigger(BaseModel):
    """再平衡触发器。三种可叠加。"""
    calendar: Literal["none", "monthly", "quarterly", "yearly"] = "none"
    threshold: Decimal | None = None     # drift 超过 N% 触发（每个 sleeve 单独判断）
    cashflow: bool = False              # 新进的钱是否优先加到低配 sleeve

class AllocationConfig(BaseModel):
    base_currency: Currency = Currency.CNY
    rebalance_trigger: RebalanceTrigger = RebalanceTrigger()
```

### SelectionConfig（A 股红利用）

```python
class ValuationSignal(BaseModel):
    """估值信号：分位数 < entry 触发买入，> exit 触发卖出。"""
    metric: Literal["pe_percentile", "pb_percentile", "dividend_yield_percentile"]
    entry_threshold: Decimal         # < 30 买
    exit_threshold: Decimal          # > 70 卖
    data_source: Literal["manual", "akshare", "yfinance"] = "manual"   # MVP 先 manual

class PositionSizing(BaseModel):
    max_single: Decimal = Decimal("0.10")    # 单只基金上限
    max_sleeve: Decimal = Decimal("0.30")    # 单 sleeve 上限
    min_cash_reserve: Decimal = Decimal("0.05")   # 现金最低保留比例

class SelectionConfig(BaseModel):
    selection_criteria: list[dict[str, Any]] = []   # 评分卡（MVP 留空，只用 sleeve 列表）
    entry_signal: ValuationSignal | None = None
    exit_signal: ValuationSignal | None = None
    position_sizing: PositionSizing = PositionSizing()
```

### PlanSleeve + PlanTarget

```python
class PlanSleeve(BaseModel):
    id: int | None
    version_id: int
    code: str                  # sleeve 标识（在 version 内唯一）: "financial"
    name: str                  # "金融红利"
    target_weight: Decimal     # 0.40
    min_weight: Decimal        # 0.30
    max_weight: Decimal        # 0.50
    tags: list[str] = []       # ["equity", "dividend"]
    position: int              # 排序

class PlanTarget(BaseModel):
    id: int | None
    sleeve_id: int
    fund_code: str             # "510300"
    weight: Decimal            # 0.50（sleeve 内占比；最终 = 0.40 × 0.50 = 0.20）
    min_weight: Decimal
    max_weight: Decimal
    position: int
```

### RebalanceSuggestion（计算结果，不存表）

```python
class RebalanceAction(BaseModel):
    fund_code: str
    sleeve_code: str
    action: Literal["buy", "sell", "hold"]
    current_weight: Decimal
    target_weight: Decimal
    drift: Decimal             # current - target
    current_shares: Decimal
    target_shares: Decimal
    delta_shares: Decimal      # 正数=买，负数=卖
    est_value: Decimal         # delta_shares × current_price
    note: str = ""             # "sleeve 整体超 band" / "fund 单独超 band" 等

class RebalanceSuggestion(BaseModel):
    strategy_id: str
    version: int
    as_of: date
    total_value: Decimal
    actions: list[RebalanceAction]
    summary: str              # 人读摘要
```

## 存储

单独 SQLite：`$XDG_DATA_HOME/gap/strategy.db`（默认 `~/.local/share/gap/strategy.db`）。

不复用 `portfolio.db`，因为：
1. strategy 是「写少读多 + 频繁比较」型，portfolio 是「写多读多」型
2. 备份 / 清理生命周期不同（strategy 改版会留历史，portfolio 的旧交易不删但极少回看）
3. 跨模块只通过 fund_code 关联，DB 解耦更清晰

表结构：

```sql
CREATE TABLE strategies (
    id              TEXT PRIMARY KEY,            -- slug
    name            TEXT NOT NULL,
    type            TEXT NOT NULL,                -- allocation | selection
    description     TEXT NOT NULL DEFAULT '',
    active_version  INTEGER,                      -- NULL 表示没有 active version
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE strategy_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     TEXT NOT NULL,
    version         INTEGER NOT NULL,
    status          TEXT NOT NULL,                -- draft | active | archived
    notes           TEXT NOT NULL DEFAULT '',
    config_json     TEXT NOT NULL,                -- 序列化 AllocationConfig | SelectionConfig
    created_at      TEXT NOT NULL,
    UNIQUE(strategy_id, version),
    FOREIGN KEY (strategy_id) REFERENCES strategies(id) ON DELETE CASCADE
);

CREATE INDEX idx_versions_strategy ON strategy_versions(strategy_id, version DESC);

CREATE TABLE plan_sleeves (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id      INTEGER NOT NULL,
    code            TEXT NOT NULL,
    name            TEXT NOT NULL,
    target_weight   TEXT NOT NULL,                -- Decimal as text
    min_weight      TEXT NOT NULL,
    max_weight      TEXT NOT NULL,
    tags            TEXT NOT NULL DEFAULT '[]',   -- JSON array
    position        INTEGER NOT NULL DEFAULT 0,
    UNIQUE(version_id, code),
    FOREIGN KEY (version_id) REFERENCES strategy_versions(id) ON DELETE CASCADE
);

CREATE TABLE plan_targets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sleeve_id       INTEGER NOT NULL,
    fund_code       TEXT NOT NULL,
    weight          TEXT NOT NULL,                -- Decimal as text
    min_weight      TEXT NOT NULL,
    max_weight      TEXT NOT NULL,
    position        INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (sleeve_id) REFERENCES plan_sleeves(id) ON DELETE CASCADE
);

CREATE INDEX idx_targets_sleeve ON plan_targets(sleeve_id);
```

Decimal 全部存 text（跟 portfolio_journal 一致）。

## 模块划分

```
src/global_allocation/
  strategy/
    __init__.py
    models.py          # Strategy / StrategyVersion / PlanSleeve / PlanTarget / RebalanceSuggestion + Allocation/SelectionConfig
    db.py              # SQLite 读写
    repo.py            # 高层业务方法：create_strategy / add_sleeve / add_target / activate_version / check
    signals.py         # 估值信号解析 + 触发判断（MVP 简单实现）
    rebalance.py       # 算 RebalanceSuggestion：current vs target，按 fund 粒度
    cli.py             # Typer 子命令
  cli.py               # 加 `gap strategy` 顶层命令
  backtest/
    engine.py          # 加 `run_strategy_backtest(plan, ...)` — 复用现有回测引擎
  feishu/
    card.py            # 加 `build_strategy_rebalance_card(suggestion)` — 飞书卡片
    publisher.py       # 加 `publish_strategy_rebalance(...)` — 复用 reply API
```

## 算法

### 校验（`gap strategy check`）

```python
def check_version(version: StrategyVersion) -> list[str]:
    errs = []
    
    # 1. Sleeve 权重和 = 1.0（容差 1e-4）
    total = sum(s.target_weight for s in version.sleeves)
    if abs(total - Decimal("1.0")) > Decimal("0.0001"):
        errs.append(f"sleeve 权重和 = {total}, 必须 = 1.0")
    
    # 2. 每个 sleeve：min < target < max
    for s in version.sleeves:
        if not (s.min_weight < s.target_weight < s.max_weight):
            errs.append(f"sleeve {s.code}: min<target<max 校验失败")
        if not (s.min_weight >= 0 and s.max_weight <= 1):
            errs.append(f"sleeve {s.code}: band 越界 [0, 1]")
    
    # 3. 每个 sleeve 内：target 权重和 = 1.0
    for s in version.sleeves:
        sub_total = sum(t.weight for t in s.targets)
        if abs(sub_total - Decimal("1.0")) > Decimal("0.0001"):
            errs.append(f"sleeve {s.code} 内 target 权重和 = {sub_total}, 必须 = 1.0")
        
        # 4. 每个 target：min < weight < max
        for t in s.targets:
            if not (t.min_weight < t.weight < t.max_weight):
                errs.append(f"target {t.fund_code}: min<weight<max 校验失败")
    
    # 5. Selection 策略必须有 entry/exit signal
    if version.config_type == "selection":
        if not version.config.entry_signal or not version.config.exit_signal:
            errs.append("selection 策略必须配置 entry_signal 和 exit_signal")
    
    return errs
```

### 激活版本（`gap strategy activate --version N`）

```python
def activate_version(strategy_id: str, version: int) -> None:
    # 1. 校验 version 存在
    # 2. 校验通过 check()
    # 3. 把该 strategy 所有 version 标 archived
    # 4. 把目标 version 标 active
    # 5. 更新 strategies.active_version
    # 6. 算 drift（如果当前 portfolio 跟新 version 差距大，给出 warning，但不阻塞）
```

事务保证。

### 再平衡建议（per-fund 粒度）

```python
def compute_rebalance_suggestion(
    strategy: Strategy,
    version: StrategyVersion,
    current_holdings: list[Holding],   # 从 portfolio_journal 拿
    current_prices: dict[str, Decimal], # 从 akshare 拿
    as_of: date,
) -> RebalanceSuggestion:
    total_value = sum(h.market_value for h in current_holdings)
    
    # 1. 展平 target：每个 fund 的整体目标权重 = sleeve.target × fund.target
    fund_targets: dict[str, dict] = {}  # fund_code -> {sleeve_code, target_weight, band_min, band_max, current_weight}
    for sleeve in version.sleeves:
        for target in sleeve.targets:
            overall_target = sleeve.target_weight * target.weight
            band_min = sleeve.min_weight * target.min_weight
            band_max = sleeve.max_weight * target.max_weight
            fund_targets[target.fund_code] = {
                "sleeve_code": sleeve.code,
                "target_weight": overall_target,
                "band_min": band_min,
                "band_max": band_max,
            }
    
    # 2. 算 current_weight per fund
    current_weight_per_fund = {
        h.fund.code: h.market_value / total_value
        for h in current_holdings
    }
    
    # 3. 生成 actions — 按单只基金粒度
    actions = []
    
    # 3a. 所有 target funds 都需要评估
    for fund_code, info in fund_targets.items():
        current_shares = next(
            (h.shares for h in current_holdings if h.fund.code == fund_code),
            Decimal("0"),
        )
        current_weight = current_weight_per_fund.get(fund_code, Decimal("0"))
        target_weight = info["target_weight"]
        drift = current_weight - target_weight
        price = current_prices[fund_code]
        
        # 触发判断：超出 band 就触发
        if current_weight < info["band_min"] or current_weight > info["band_max"]:
            target_value = total_value * target_weight
            target_shares = (target_value / price).quantize(Decimal("0.01"))
            delta_shares = target_shares - current_shares
            action = "buy" if delta_shares > 0 else "sell"
            actions.append(RebalanceAction(
                fund_code=fund_code,
                sleeve_code=info["sleeve_code"],
                action=action,
                current_weight=current_weight,
                target_weight=target_weight,
                drift=drift,
                current_shares=current_shares,
                target_shares=target_shares,
                delta_shares=delta_shares,
                est_value=abs(delta_shares * price),
                note=f"超出 band [{info['band_min']:.2%}, {info['band_max']:.2%}]",
            ))
    
    # 3b. 当前持有但不在 target 里的 fund → 建议卖出
    for fund_code, current_weight in current_weight_per_fund.items():
        if fund_code not in fund_targets:
            current_shares = next(h.shares for h in current_holdings if h.fund.code == fund_code)
            price = current_prices[fund_code]
            actions.append(RebalanceAction(
                fund_code=fund_code,
                sleeve_code="(none)",
                action="sell",
                current_weight=current_weight,
                target_weight=Decimal("0"),
                drift=current_weight,
                current_shares=current_shares,
                target_shares=Decimal("0"),
                delta_shares=-current_shares,
                est_value=current_shares * price,
                note="不在当前 target 列表中",
            ))
    
    return RebalanceSuggestion(
        strategy_id=strategy.id,
        version=version.version,
        as_of=as_of,
        total_value=total_value,
        actions=actions,
        summary=f"{len(actions)} 只基金需要调整",
    )
```

### 触发时机（`gap strategy next-rebalance`）

```python
def next_rebalance_date(
    strategy: Strategy,
    version: StrategyVersion,
    last_rebalance: date | None,
) -> tuple[date | None, str]:
    config = version.config  # AllocationConfig | SelectionConfig
    if not isinstance(config, AllocationConfig):
        return None, "selection 策略不适用日历再平衡，请用估值信号"
    
    trigger = config.rebalance_trigger
    today = date.today()
    
    # 1. Calendar
    if trigger.calendar != "none" and last_rebalance:
        interval = {"monthly": 30, "quarterly": 90, "yearly": 365}[trigger.calendar]
        next_cal = last_rebalance + timedelta(days=interval)
        reason = f"calendar ({trigger.calendar})"
    else:
        next_cal, reason = None, ""
    
    # 2. Threshold：每个 sleeve 算 drift，超 threshold 立即触发
    suggestion = compute_rebalance_suggestion(...)  # 拿当前 drift
    max_drift = max((abs(a.drift) for a in suggestion.actions), default=0)
    if trigger.threshold and max_drift > trigger.threshold:
        return today, f"threshold 触发：max drift = {max_drift:.2%} > {trigger.threshold:.2%}"
    
    return next_cal, reason
```

### 回测（`gap strategy backtest`）

复用 `backtest/engine.py`，新加 `run_strategy_backtest(plan, start, end)`：
- 用 plan 的 sleeve/target 在每个 rebalance 日调整持仓
- NAV 曲线 + 指标（年化、最大回撤、夏普）跟现有回测一致
- 限制：MVP 只支持 `allocation` 类型回测（selection 类型因为没有固定 target，跳过）

### 多策略横评（`gap strategy compare`）

- 同时跑 2+ strategies 的 backtest
- 对比表：年化、波动、最大回撤、夏普
- 净值曲线叠加（matplotlib 出 PNG，发飞书或落本地）
- 相关性矩阵（如果策略间 sleeve 有重叠）

## 边界情况

- **边界 1**：未通过 check() 的 version 不允许 activate
- **边界 2**：activate 时如果当前 portfolio 跟新 version drift > 20%，只 warn 不阻塞（用户明确选）
- **边界 3**：删除 strategy 时所有 versions/sleeves/targets 级联删除（ON DELETE CASCADE）
- **边界 4**：fund_code 在 journal 里没登记 → rebalance 时给 warning 但不抛错（用户可能准备新增）
- **边界 5**：rebalance 算不出 drift（current_holdings 为空）→ 返回空 actions + summary = "无持仓，跳过"
- **边界 6**：拉不到价格 → 抛错让用户重试（复用 akshare fallback 到 manual）
- **边界 7**：sleeve/target 数量为 0 → check 失败
- **边界 8**：negative / > 1 权重 → 校验拒绝
- **边界 9**：version 号必须严格递增（不能 v1 → v3 跳号）
- **边界 10**：compare 至少 2 个 strategy，否则提示用户

## 验收标准

- [ ] `gap strategy init` 创建 strategy 记录（type 必填）
- [ ] `gap strategy sleeve add` 写入 plan_sleeves
- [ ] `gap strategy target add` 写入 plan_targets
- [ ] `gap strategy check` 校验权重和 + band，列出所有 err
- [ ] `gap strategy version new` 创建新 version（status=draft，config 复制自上一个 active version）
- [ ] `gap strategy activate --version N` 切到新版本，事务保证
- [ ] `gap strategy next-rebalance` 算出下次再平衡日期 + 触发原因
- [ ] `gap strategy rebalance --dry-run` 打印 RebalanceSuggestion 表格
- [ ] `gap strategy rebalance` 发飞书卡片（per-fund 清单）
- [ ] `gap strategy backtest` 跑历史回测，输出 CAGR/最大回撤/夏普
- [ ] `gap strategy compare a b` 同时跑两个回测，输出对比表
- [ ] Selection 策略支持 entry/exit signal 配置
- [ ] Allocation 策略支持 calendar/threshold/cashflow trigger
- [ ] 单元测试 ≥ 85% 覆盖（models + repo + rebalance + check）
- [ ] mypy strict + ruff 干净
- [ ] docs/strategy-guide.md（用户文档）写完

## 风险

1. **估值信号 MVP 阶段只能 manual** — 用户要手动录入 PE 分位；后续接 akshare/yfinance 自动拉（先不做）
2. **回测引擎复用风险** — 现有 engine 是为 60/40 这种简单权重设计的，多 sleeve 嵌套可能需要扩展（评估：先看 engine 现状再决定）
3. **per-fund 粒度可能产生很多小交易** — 后续加 minimum trade size / transaction cost 约束（MVP 不做）
4. **fund_code 跨模块一致性** — strategy 用 code，portfolio_journal 也用 code，将来 Universe 模块用 symbol（不一致风险，但 MVP 不引入新概念）

## 后续工作（不在本 spec）

- spec 092: 标的库 / Universe 管理（基金规模、PE 上下限、相关性等风控）
- spec 093: Drift 监控 + 飞书自动报警
- spec 094: 估值信号自动接入（akshare / yfinance）
- spec 095: 跨策略 sleeve 共享（如果真出现重叠需求）
