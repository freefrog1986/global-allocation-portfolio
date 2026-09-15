# 用户指南

> 给 `gap` 用户的快速上手 + 常见场景。读完应该能自己写自定义策略、回测、对比、发飞书。

## 安装

要求 Python 3.11+。

```bash
git clone https://github.com/freefrog1986/global-allocation-portfolio.git
cd global-allocation-portfolio
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,backtest]'
```

可选 `backtest` extra 引入了 `vectorbt`（回测引擎的依赖）。如果你不想装这一坨，`gap backtest` 会用内置的纯 pandas 实现（也够用）。

## 30 秒上手

```bash
gap version                                 # 看版本
gap strategy list                           # 看内置 4 个策略
gap backtest 60_40 --period 5y              # 跑 60/40 回测
gap compare 60_40 permanent_portfolio --period 10y   # 对比两个策略
gap publish 60_40 --period 5y --dry-run     # 生成飞书卡片（不真发）
```

## 数据获取

```bash
# 拉单个标的（带 SQLite 缓存）
gap data fetch VT 5y
gap data fetch 510300.SH 3y               # A 股用 akshare

# 看缓存里都有啥
gap data list

# 清缓存
gap data clear --all
gap data clear --symbol VT
```

数据源选择规则：

- **yfinance**：标的是 `.` 分隔的美股代码（如 `VT`、`BND`），或者没有后缀的港股/欧股代码
- **akshare**：A 股 `510300.SH` / `510500.SH` 这种带后缀的
- 默认规则见 `src/global_allocation/data/fetcher.py`，可强制 `--source yfinance` 或 `--source akshare`

## 写自定义策略（YAML）

把策略写成 YAML，放进 git，能 diff 能 review。

### 模板

```yaml
id: my_60_40_custom            # 唯一 ID
name: 我的 60/40（自定义）     # 显示名
description: |
  60% 全球股票 ETF + 40% 美国债 ETF，按年再平衡。

target_weights:                 # 权重总和必须为 1.0
  - symbol: VT
    name: Vanguard Total World Stock ETF
    asset_class: equity         # equity | bond | commodity | reit | cash
    region: global              # us | cn | hk | global
    currency: usd               # usd | cny | hkd | eur | jpy
    data_source: yfinance       # yfinance | akshare
    weight: 0.60

  - symbol: BND
    name: Vanguard Total Bond Market ETF
    asset_class: bond
    region: us
    currency: usd
    data_source: yfinance
    weight: 0.40

rebalance:
  frequency: yearly             # none | monthly | quarterly | yearly
  threshold: 0.05               # 任一权重偏离 target 超过 5% 也触发（可省略）

base_currency: usd               # 组合计价货币
inception: 2007-09-26            # 起始日；BND 2007-09-26 成立
metadata:
  tags: [classic, retirement]
  notes: |
    自由字段，便于记录来源 / 思路。
```

### 校验

```bash
gap strategy validate examples/my_strategy.yaml
```

不合法会报错（如权重和不等于 1、enum 拼错、symbol 重复）。

### 跑回测

```bash
gap backtest examples/my_strategy.yaml --period 5y
gap backtest my_60_40_custom --period 10y     # 校验过的策略也可以直接用 id 引用
```

## 回测输出

```text
策略：60/40 经典股债
周期：2020-09-15 → 2025-09-15
初始：100,000.00
终值：135,080.42
总收益：+35.08%
CAGR  : +6.23%
夏普  : 0.43
最大回撤：-22.46%
年化波动率：+10.69%
胜率：+55.00%
最佳单日：+4.12%
最差单日：-3.87%
再平衡次数：5
```

字段含义：

| 指标 | 怎么算的 |
| --- | --- |
| **CAGR** | (终值/初值)^(1/年数) - 1 |
| **夏普** | (年化收益 - 无风险利率) / 年化波动率，无风险利率默认 2% |
| **最大回撤** | 历史 NAV 序列里从峰到谷的最大跌幅（负数） |
| **年化波动率** | 日收益标准差 × √252 |
| **胜率** | 收益 > 0 的交易日占比 |
| **再平衡次数** | 实际触发再平衡的天数（含 day 0 初始建仓） |

## 多策略对比

```bash
gap compare 60_40 permanent_portfolio all_weather --period 5y
```

输出对比表（按 CAGR 排序），方便一眼看出哪个策略风险调整后收益更好。

## 飞书卡片报告

见 [`feishu-setup.md`](feishu-setup.md)。配置好凭证后：

```bash
gap publish 60_40 --period 5y                     # 真发到默认 chat
gap publish 60_40 --period 5y --chat oc_abc123    # 发到指定 chat
gap publish 60_40 --period 5y --dry-run           # 只生成 JSON，不真发
```

卡片内容：
1. 标题 + 周期 + 初始/终值/总收益
2. 资金曲线（line chart，最多 1000 点）
3. 当前权重饼图（pie chart，过滤 0 权重）
4. 关键指标表格

## 调试技巧

```bash
# 看内置策略详情
gap strategy show 60_40

# 看缓存里有什么标的
gap data list

# 看价格数据是否够长
gap data fetch VT 10y

# 只看卡片 JSON（不发）
gap publish 60_40 --period 5y --dry-run | jq .
```

## 常见问题

**Q：回测结果是负的，是不是策略烂？**
A：历史表现不预示未来。任何回测都只看特定窗口。`--period max` 跑全周期比 5y 更稳。

**Q：A 股数据拉不到？**
A：akshare 不翻墙连不上。配置代理或者用 `pip install` 时把 `akshare` 卸掉，纯 yfinance 也能跑（少了 A 股标的）。

**Q：飞书发不出去？**
A：先 `--dry-run` 看 JSON；再检查 `~/.config/gap/credentials.json` 或环境变量 `FEISHU_APP_ID/SECRET/CHAT_ID` 是否齐全；再确认飞书机器人已加入目标 chat 且已启用「发送消息」权限。

**Q：自定义策略权重加不等于 1？**
A：`gap strategy validate` 会拦下。常见坑：忘了把小数写成 0.6 而不是 6。

**Q：怎么加新数据源？**
A：参考 `src/global_allocation/data/base.py`，实现 `DataSourceBase`（`fetch` 返回 `pd.DataFrame`，columns=asset，index=Date），然后在 `fetcher.py` 里注册。
