# Specs Index

> **Spec-Driven Development**：每个功能先写规格，规格 review 通过再写代码（这一版不需要 review，写完即开发）。
> 规格模板：`specs/_template.md`。

| # | 标题 | 状态 | 实现状态 |
| --- | --- | --- | --- |
| [001](001-spec-driven-process.md) | Spec-Driven Development 流程 | Stable | Done |
| [010](010-data-models.md) | 核心数据模型 | Draft | Planned |
| [020](020-strategy-base.md) | 策略基类 API | Draft | Planned |
| [030](030-built-in-strategies.md) | 4 个内置策略 | Draft | Planned |
| [040](040-data-fetch.md) | 历史数据获取 | Draft | Planned |
| [050](050-backtest-engine.md) | 回测引擎 | Draft | Planned |
| [060](060-performance-metrics.md) | 性能指标 | Draft | Planned |
| [070](070-cli.md) | CLI 接口 | Draft | Planned |
| [080](080-feishu-card.md) | 飞书 interactive card | Draft | Planned |
| [090](090-portfolio-journal.md) | 实盘持仓账本 | Draft | Planned |

**Legend**：Stable = 流程规范不再改；Draft = 还在迭代；Planned = 还没动代码。

## 阅读顺序

如果你是第一次接触这个仓库：

1. [001](001-spec-driven-process.md) —— 了解开发流程
2. [010](010-data-models.md) —— 整个系统的"名词表"
3. [020](020-strategy-base.md) —— 策略是怎么定义的
4. [030](030-built-in-strategies.md) —— 4 个内置策略长啥样
5. [040](040-data-fetch.md) + [050](050-backtest-engine.md) —— 数据怎么来、怎么跑回测
6. [060](060-performance-metrics.md) —— 业绩怎么算
7. [070](070-cli.md) —— 怎么用命令行
8. [080](080-feishu-card.md) —— 怎么发飞书

## 相关目录

- [`docs/adr/`](../docs/adr/) —— 架构决策记录（为什么选这个库、这个方案）
- [`docs/decisions/`](../docs/decisions/) —— 过程决策（什么时候改了什么）
- [`docs/user-guide.md`](../docs/user-guide.md) —— 终端用户的使用说明
