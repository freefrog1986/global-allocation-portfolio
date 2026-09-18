# 用户策略配置指南

> spec 091 — 管理你自己的多策略投资组合配置：sleeve 分层、版本化、再平衡建议、飞书推送。
> 跟内置策略（`gap strategy` 下的 60/40、全天候等回测模板）完全解耦。

## 这是什么

`gap plan` 子命令让你管**自己实盘在用的策略**，而不是用来回测的内置模板：

- 维护多条策略（A 股红利、全球配置、子女教育金…），每条独立版本化
- 每条策略拆 sleeve（子分类，比如「金融红利」「消费红利」），每个 sleeve 下面是单只基金
- 每只基金定义目标权重 + 容忍带（band），算出当前持仓的 drift
- 一键生成再平衡建议（**按单只基金粒度**：告诉你具体哪只买/卖多少股）
- 把建议卡片发到飞书，丢给券商 App 跟单

跟 `gap strategy` 的区别：

| 命令 | 用途 | 数据形态 |
| --- | --- | --- |
| `gap strategy` | 内置回测模板（60/40、永久组合等） | YAML/Python 文件，只读 |
| `gap plan` | 你自己的实盘策略配置 | SQLite 读写，版本化 |

---

## 30 秒上手

```bash
# 1. 创建一条策略（v1 默认 draft）
gap plan init global-alloc \
  --name "全球资产配置" \
  --type allocation

# 2. 加 sleeve
gap plan sleeve-add global-alloc \
  --version 1 \
  --code equity --name "股票" \
  --target 0.60 --min 0.50 --max 0.70

gap plan sleeve-add global-alloc \
  --version 1 \
  --code bond --name "债券" \
  --target 0.40 --min 0.30 --max 0.50

# 3. 给 sleeve 加基金目标
gap plan target-add global-alloc \
  --version 1 --sleeve equity \
  --fund 510300 \
  --weight 0.50 --min 0.40 --max 0.60

gap plan target-add global-alloc \
  --version 1 --sleeve bond \
  --fund 008114 \
  --weight 1.00 --min 0.95 --max 1.00

# 4. 校验 + 激活
gap plan check global-alloc --version 1
gap plan activate global-alloc --version 1

# 5. 看一眼 + 算下次再平衡
gap plan show global-alloc
gap plan next-rebalance global-alloc

# 6. 算再平衡建议（不真发飞书）
gap plan rebalance global-alloc --dry-run

# 7. 真发飞书卡片
gap plan rebalance global-alloc --send
```

---

## 数据模型

4 层嵌套：

```
Strategy (顶层)             ← gap plan init 创建
└── StrategyVersion (v1/v2) ← gap plan version-new 创建；activate 切换
    ├── config              ← AllocationConfig 或 SelectionConfig
    └── sleeves             ← gap plan sleeve-add 创建
        ├── target_weight   ← sleeve 在策略里的权重
        └── targets         ← gap plan target-add 创建（sleeve 内的基金）
            ├── weight      ← 基金在 sleeve 内的权重
            └── 最终权重    = sleeve.target × target.weight
```

举例：sleeve `equity` 目标 0.60，里面 `510300` 权重 0.50 → `510300` 在策略里的最终权重 = `0.60 × 0.50 = 0.30`。

两种策略类型：

- **allocation**（默认）：固定权重 + 再平衡，适合全球配置这种「按目标比例持有」
- **selection**：选股 + 估值信号触发，适合 A 股红利这种「低于分位就买、高于分位就卖」

---

## 命令参考

### init

```bash
gap plan init <strategy_id> --name "<显示名>" --type <allocation|selection>
```

- `strategy_id`：kebab-case slug，全局唯一
- 必填 `--type`：决定 v1 默认 config（allocation 用 AllocationConfig，selection 用 SelectionConfig）
- v1 状态 = `draft`，必须 `check` + `activate` 才会生效

### list

```bash
gap plan list
```

表格列出所有策略：ID / 名称 / 类型 / 当前 active version / 创建时间。

### show

```bash
gap plan show <strategy_id>
```

显示：
1. 策略元信息（类型、active_version、创建时间）
2. 所有 versions（标 active/archived/draft）
3. 当前 active version 的 sleeves + targets 表

### sleeve-add

```bash
gap plan sleeve-add <strategy_id> --version N \
  --code <sleeve_code> --name "<sleeve 名>" \
  --target 0.60 --min 0.50 --max 0.70
```

sleeve_code 在 version 内必须唯一；权重和必须 = 1.0（`check` 校验）。

### target-add

```bash
gap plan target-add <strategy_id> --version N \
  --sleeve <sleeve_code> \
  --fund <fund_code> \
  --weight 0.50 --min 0.40 --max 0.60
```

fund_code 是 sleeve 内的基金代码（A 股 6 位数字）。每个 sleeve 内所有 target 的 weight 之和必须 = 1.0。

### check

```bash
gap plan check <strategy_id> --version N
```

校验规则（任何一条挂都返回非零 exit code）：

1. sleeve 的 `target_weight` 总和 = 1.0（容差 `1e-4`）
2. 每个 sleeve：`min_weight < target_weight < max_weight`，且 `[min, max] ⊆ [0, 1]`
3. 每个 sleeve 内 target 的 `weight` 总和 = 1.0
4. 每个 target：`min_weight < weight < max_weight`
5. selection 类型必须配齐 `entry_signal` + `exit_signal`

### version-new

```bash
gap plan version-new <strategy_id> --notes "<备注>"
```

从当前最新 version 复制 config 创建 `draft v+1`。改 sleeve / target 用 `--version N` 指到新 version 即可。

### activate

```bash
gap plan activate <strategy_id> --version N
```

原子操作：
1. 先 `check`（不过则报错退出）
2. 当前 active 标 `archived`
3. 目标 version 标 `active`
4. 更新 `strategies.active_version`

旧 version 不会删，留作历史。

### next-rebalance

```bash
gap plan next-rebalance <strategy_id> [--last YYYY-MM-DD]
```

算下次该再平衡的日期 + 触发原因。仅 allocation 策略。

- 有 `--last`：算 `last + interval`（monthly=30d / quarterly=90d / yearly=365d）
- 配置了 `threshold`：取当前最大 drift，超过阈值则建议「今天就再平衡」

### rebalance

```bash
gap plan rebalance <strategy_id> [--dry-run | --send]
```

MVP：不接真实 portfolio 持仓数据，返回「无持仓数据」的占位卡片。

- `--dry-run`（默认）：打印 plan 表格 + 卡片 JSON 到终端
- `--send`：通过 lark-oapi 真发飞书（重试 3 次）

等 spec 092/093 完成后，这个命令会接 portfolio 持仓算真实 drift。

---

## SQLite 存储

位置：`$XDG_DATA_HOME/gap/strategy.db`，默认 `~/.local/share/gap/strategy.db`。

4 张表：

- `strategies`：策略顶层
- `strategy_versions`：version + JSON config
- `plan_sleeves`：sleeve（含 band）
- `plan_targets`：sleeve 内的基金（含 band）

Decimal 全部存 text（精确）。`ON DELETE CASCADE`：删 strategy → 所有 version → 所有 sleeve → 所有 target 全清。

备份策略：

```bash
# 备份
sqlite3 ~/.local/share/gap/strategy.db ".backup '$HOME/backups/strategy-$(date +%F).db'"

# 导出 SQL（git diff 友好）
sqlite3 ~/.local/share/gap/strategy.db .dump > strategy-$(date +%F).sql
```

---

## 再平衡建议（per-fund 粒度）

`compute_rebalance_suggestion`（`src/global_allocation/strategy/rebalance.py`）按单只基金给操作建议：

每个基金有 `band_min = sleeve.min × target.min`、`band_max = sleeve.max × target.max`。

| 当前持仓位置 | 动作 |
| --- | --- |
| 在 band 内 | `hold`（不报） |
| 低于 band_min | `buy`（补到 target_weight） |
| 高于 band_max | `sell`（减到 target_weight） |
| 不在 target 列表里 | `sell` 清仓（sleeve_code="(none)"） |

输出 `RebalanceSuggestion`，含：

- `actions`：每个需要调整的基金一条（fund_code、sleeve_code、动作、当前/目标 weight、Δ股数、估算金额、原因）
- `summary`：人读摘要
- `total_value`、`as_of`

---

## 估值信号（Selection 策略）

MVP 阶段支持手动配置 `entry_signal` / `exit_signal`，判断逻辑：

| 当前分位 + 持仓状态 | 动作 |
| --- | --- |
| 分位 < entry_threshold，未持仓 | `buy` |
| 分位 > exit_threshold，已持仓 | `sell` |
| 在 band `[entry, exit]` 内 | `hold` |

信号接入：

```python
from global_allocation.strategy.signals import compute_signal_actions

suggestion = compute_signal_actions(
    strategy=strategy,
    version=version,
    sleeves=sleeves,
    targets_by_sleeve=targets_map,
    current_holdings=[...],   # list[Holding] from portfolio_journal
    valuation_percentiles={   # fund_code -> percentile (0~100)
        "510300": Decimal("25"),
        "008114": Decimal("75"),
    },
    current_prices={...},
)
```

`valuation_percentiles` 暂时手动传；spec 094 会接 akshare/yfinance 自动拉。

---

## 回测（library API）

CLI 还没接，可以直接调：

```python
from decimal import Decimal
import pandas as pd

from global_allocation.strategy.backtest import run_strategy_backtest

prices = pd.DataFrame(...)  # columns=fund_code, index=Date, values=Adj Close

result = run_strategy_backtest(
    strategy_id="global-alloc",
    strategy_name="全球配置",
    version=version,
    sleeves=sleeves,
    targets_by_sleeve=targets_map,
    prices=prices,
    initial_capital=Decimal("100000"),
)

print(result.metrics.cagr, result.metrics.sharpe, result.metrics.max_drawdown)
```

限制：MVP 只支持 `allocation` 策略；`selection` 因为没有固定 target 跳不过去（spec 094 自动信号接入后再扩展）。

回测引擎复用现有 `backtest/engine.py`，输出 BacktestResult（资金曲线 + 指标），跟内置策略回测的输出完全一致，可以发同一个飞书卡片。

---

## 飞书卡片

`build_strategy_rebalance_card(suggestion)`（`src/global_allocation/feishu/card.py`）构造的卡片：

1. 标题 + as_of + 总市值 + 摘要
2. 表格：基金 / sleeve / 动作（🟢买 🔴卖 ⚪持）/ 当前→目标 / Δ股数 / 估算金额 / 原因
3. 无 actions 时显示「✅ 无需调整：当前持仓在所有 band 内」

发送复用现有 lark-oapi 通道（凭证配置见 [`feishu-setup.md`](feishu-setup.md)）：

```bash
gap plan rebalance global-alloc --send
```

---

## 边界与陷阱

- **activate 不会自动算 drift**：当前 portfolio 跟新 version 差距大时只 warn 不阻塞；如果你想自动算，等 spec 093。
- **`fund_code` 不做有效性校验**：DB 里允许任何字符串，rebalance 时再去 journal 找。漏配会得到「不在 target 列表里 → 卖」的误报。
- **sleeve 数量无上限**：但 `check` 会把每个 sleeve 都过一遍，目标过多（>10）时人工维护会累。
- **band 是严格 `<`**：min=0.40, max=0.60 时 target 不能取 0.40 或 0.60（极小数要落在开区间内）。
- **version 号必须严格递增**：不能 v1 → v3 跳号，`version-new` 总是 v+1。
- **删 strategy 是级联**：DB 没软删，删了就没了，先 export SQL 备份。

---

## 不在本 spec 范围

明确不做（spec 091 范围外）：

- 标的库 / Universe 管理（基金规模、PE 上下限等单基金风控）— spec 092
- 实时监控 + drift 报警飞书推送 — spec 093
- 估值信号自动接入（akshare/yfinance） — spec 094
- 跨策略 sleeve 共享 — spec 095（如果真出现重叠需求再做）
- 多策略横评 CLI（`gap plan compare`） — 暂缓，先用 Python 调 `run_strategy_backtest` 对比结果
- 券商 API 自动同步 — 跟 portfolio_journal 一样手动

---

## 文件位置速查

| 用途 | 路径 |
| --- | --- |
| Pydantic 模型 | `src/global_allocation/strategy/models.py` |
| SQLite CRUD | `src/global_allocation/strategy/db.py` |
| 业务方法 + check | `src/global_allocation/strategy/repo.py` |
| 再平衡算法 | `src/global_allocation/strategy/rebalance.py` |
| 估值信号 | `src/global_allocation/strategy/signals.py` |
| 回测 adapter | `src/global_allocation/strategy/backtest.py` |
| CLI | `src/global_allocation/strategy/cli.py` |
| 飞书卡片 | `src/global_allocation/feishu/card.py` |
| 飞书发送 | `src/global_allocation/feishu/publisher.py` |
| 单元测试 | `tests/unit/test_strategy_*.py` |
| Spec | `specs/091-strategy-config.md` |
