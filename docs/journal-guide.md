# 实盘持仓账本使用指南

> 这本账本用来记录你**真实买**的基金持仓、每笔买卖、当时为什么买（策略）、花了多少手续费。
> 回测跑的是历史假设，这本账本跑的是「你」的真实仓位。两者互相对照，能看清回测偏差。

## 跟回测的关系

| 维度 | 回测（`gap backtest`） | 实盘账本（`gap portfolio`） |
| --- | --- | --- |
| 数据源 | 历史价（yfinance + akshare） | 真实成交价（你告诉我） |
| 交易 | 按再平衡规则自动 | 你手动 `buy` / `sell` |
| 跟踪 | CAGR / Sharpe / 最大回撤 | 浮动盈亏 / 周涨跌 / 累计涨跌 |
| 输出 | 性能指标报告 | 飞书周报卡片 |
| 持久化 | 不写 SQLite | 写 `$XDG_DATA_HOME/gap/portfolio.db` |

> 一句话：**回测看「该不该这么配」，账本看「我实际配成什么样」**。

## 一次性初始化

```bash
gap portfolio init --name "我的组合"
```

会在 `$XDG_DATA_HOME/gap/portfolio.db`（通常是 `~/.local/share/gap/portfolio.db`）建一个空 SQLite。

## 登记基金

```bash
gap portfolio fund add 163406 --name "兴全合润分级"  --asset-class mixed
gap portfolio fund add 510300 --name "华泰柏瑞沪深300" --asset-class equity
gap portfolio fund add 161725 --name "招商中证白酒"   --asset-class equity
```

`--asset-class` 可选：`equity` / `bond` / `commodity` / `reit` / `cash` / `mixed`。
可以随时 `gap portfolio fund list` 看已经登记的基金。

## 记买入

告诉我（飞书）你今天买了什么，我帮你敲。比如「今天定投了 1000 块 163406，单价 2.35」：

```bash
gap portfolio buy 163406 \
  --date 2026-09-10 \
  --shares 1000 \
  --price 2.350 \
  --fee 1.20 \
  --strategy "月度定投扣款" \
  --tags dca monthly
```

- `--date` 交易日（默认今天）
- `--shares` 实际成交份数
- `--price` 成交**单价**（净值，不是钱）
- `--fee` 手续费
- `--strategy` 当时为什么买（写给自己看的，半年后回看很有用）
- `--tags` 标签，可多个：比如 `dca` / `monthly` / `profit-take` / `rebalance`

## 记卖出

同上但 `sell`：

```bash
gap portfolio sell 163406 \
  --date 2026-09-12 \
  --shares 200 \
  --price 2.450 \
  --fee 1.00 \
  --strategy "止盈减仓到 80%" \
  --tags profit-take
```

> 卖出前会自动检查「你有没有这么多份」。比如你总共 1000 份，要卖 1500 会拒绝。

## 看当前持仓

```bash
gap portfolio show
```

会出一张表：

```
                        当前持仓
┌────────────┬────────┬──────────┬────────┬─────────┬──────┬──────────┬────────┐
│ 基金       │ 份额   │ 成本均价 │ 最新价 │ 市值    │ 占比 │ 浮动盈亏 │ 盈亏 % │
├────────────┼────────┼──────────┼────────┼─────────┼──────┼──────────┼────────┤
│ 163406     │  800.00│   2.3800 │ 2.5000 │ 2,000.00│ 80%  │ +96.00   │ +5.04% │
│ 510300     │  200.00│   3.8500 │ 4.0000 │   800.00│ 20%  │ +30.00   │ +3.90% │
└────────────┴────────┴──────────┴────────┴─────────┴──────┴──────────┴────────┘
总市值：2,800.00 CNY
```

## 看交易流水

```bash
gap portfolio tx list                      # 全部
gap portfolio tx list --fund 163406        # 只看一个基金
gap portfolio tx list --tag dca            # 只看定投的
gap portfolio tx list --strategy "月度定投" # 按策略搜
```

## 拍周快照

```bash
gap portfolio snapshot --date 2026-09-13
```

快照会记录当时的**总市值**、**上周涨跌**（跟上周对比）、**累计涨跌**（跟第一张快照对比）。
同样的日期拍第二次会覆盖（idempotent）。

## 看报告

```bash
gap portfolio report                # 现在的总成本 / 总市值 / 浮动盈亏
gap portfolio report --weekly       # 加上周 / 累计涨跌
```

## 发飞书周报

```bash
gap portfolio publish --weekly --dry-run    # 先生成卡片 JSON，看一眼
gap portfolio publish --weekly               # 真发到默认群
gap portfolio publish --title "9月第2周"     # 改卡片标题
gap portfolio publish --chat oc_xxx          # 发到别的群
```

卡片里有什么：

1. **header**：标题 + 蓝色主题
2. **summary**：总市值 / 总成本 / 浮动盈亏 / 上周涨跌 / 累计涨跌
3. **pie chart**：当前持仓占比
4. **line chart**：NAV 曲线（每个快照 + 现在）
5. **table**：最近 10 笔交易（买卖用颜色区分）
6. **footer**：生成时间

第一次发的时候 `--dry-run` 看一眼卡片长啥样，满意了去掉 `--dry-run` 真发。

凭证配置见 [`docs/feishu-setup.md`](./feishu-setup.md)。

## 常见操作剧本

### 场景 1：今天定投了 3 只基金

```bash
gap portfolio buy 163406 --date 2026-09-15 --shares 500  --price 2.40 --fee 1 --strategy "周一扣款" --tags dca
gap portfolio buy 510300 --date 2026-09-15 --shares 200  --price 3.90 --fee 1 --strategy "周一扣款" --tags dca
gap portfolio buy 161725 --date 2026-09-15 --shares 1000 --price 1.10 --fee 1 --strategy "周一扣款" --tags dca
gap portfolio publish --title "9月第3周定投" --dry-run
```

### 场景 2：周五收盘前看一眼周报

```bash
gap portfolio publish --weekly --title "9月W2周报"
```

会自动先拍今天快照，再发卡片。

### 场景 3：止盈卖出

```bash
gap portfolio sell 163406 --date 2026-09-15 --shares 200 --price 2.65 --fee 1 \
  --strategy "净值新高，止盈 25%" --tags profit-take
gap portfolio publish --title "止盈后持仓"
```

### 场景 4：手动改价（akshare 抓不到时）

目前生产价源是 akshare 的 `fund_open_fund_info_em`。如果当天接口挂了，
最简单的兜底是暂时改 `src/global_allocation/portfolio/valuation.py` 的 `AkshareFundPriceSource`
加 fallback，或者直接用 `ManualPriceSource` 临时跑命令。
后续会加 `--price-override` 临时覆盖接口价。

## 数据存储位置

- 主表 `funds` / `transactions` / `weekly_snapshots` 都在 `$XDG_DATA_HOME/gap/portfolio.db`
- 想备份直接 `cp` 这个文件即可
- 想换电脑：把 db 拷过去 + 配置飞书凭证 + 重装 gap 即可

## 跟 Claude 协作的姿势

我（CC-LEGION-002）就是你的录入助手。你只要在飞书告诉我：

- 「今天买了 1000 块 163406，单价 2.35」
- 「510300 卖了 200 份，2.45 卖的」
- 「帮我拍个周报发群里」

我会转成上面的命令，**让你确认**后再执行。不会偷偷动你的账本。

## 接下来

- 把 specs/090 里所有验收项勾完（剩下的：akshare fallback、临时改价、CLI 自动补全）
- 周报自动化（cron / launchd 每周自动 `gap portfolio publish --weekly`）
- 多组合支持（先把单组合跑顺）
