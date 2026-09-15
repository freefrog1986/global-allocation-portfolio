# Global Allocation Portfolio (`gap`)

全球资产配置组合：策略模板 + 历史数据 + 回测 + 飞书图表卡片报告。

> 仓库地址：<https://github.com/freefrog1986/global-allocation-portfolio>
>
> 状态：**alpha**（开发中，API 还在变）

## 为什么做这个

跨 A 股、港股、美股、债、商品、REITs 的全球资产配置需要：
1. **可复现的策略** —— YAML 描述的权重 + 再平衡规则，git 里能 diff
2. **可验证的回测** —— 跑历史数据，看真实收益/夏普/最大回撤
3. **可分享的结果** —— 在飞书里直接看图表卡片，不开电脑也能看

## 它现在能做什么

| 功能 | 状态 | 入口 |
| --- | --- | --- |
| 4 个内置策略（60/40、永久组合、全天候、风险平价） | ✅ MVP | `gap strategy list` |
| 自定义策略（YAML） | ✅ MVP | `gap strategy validate examples/my.yaml` |
| 历史数据获取（yfinance + akshare，SQLite 缓存） | ✅ MVP | `gap data fetch AAPL 5y` |
| 单策略回测（CAGR / 夏普 / 最大回撤 / 波动率） | ✅ MVP | `gap backtest 60_40 --period 5y` |
| 多策略对比 | ✅ MVP | `gap compare 60_40 permanent_portfolio --period 5y` |
| 飞书交互卡片报告（chart card，含 line / bar / pie） | ✅ MVP | `gap publish 60_40` |
| CLI | ✅ MVP | `gap --help` |

## 它**不**做什么

- **不连券商、不自动下单** —— 这是研究工具，不是交易终端
- **不给投资建议** —— 回测结果是历史表现，不是未来收益承诺
- **不做实时行情推送** —— 数据是日频拉取，不是 tick 级

## 安装

```bash
git clone https://github.com/freefrog1986/global-allocation-portfolio.git
cd global-allocation-portfolio
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,backtest]'
```

## 30 秒上手

```bash
# 看一下内置的策略
gap strategy list

# 跑一个 60/40 回测
gap backtest 60_40 --period 5y

# 对比两个策略
gap compare 60_40 permanent_portfolio --period 10y

# 把结果发到飞书当前话题（需要先配置飞书应用凭证）
gap publish 60_40 --period 5y
```

## 文档

- [`docs/user-guide.md`](docs/user-guide.md) —— 详细使用说明
- [`docs/strategies.md`](docs/strategies.md) —— 内置策略详解
- [`docs/feishu-setup.md`](docs/feishu-setup.md) —— 飞书机器人配置
- [`specs/`](specs/) —— 每个功能的 SDD 规格文档
- [`docs/adr/`](docs/adr/) —— 架构决策记录
- [`CHANGELOG.md`](CHANGELOG.md) —— 改动历史

## 贡献

见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

## 协议

MIT —— 见 [`LICENSE`](LICENSE)。
