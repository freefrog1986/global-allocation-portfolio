# ADR 0002: 数据源选 yfinance + akshare

> 状态：Accepted
> 日期：2026-09-15
> 决策者：liubo

## 背景

全球资产需要跨市场数据：A 股、港股、美股、欧股、债、商品、黄金、REITs。

## 决策

- **美股/全球 ETF**：yfinance（Yahoo Finance 非官方）
- **A 股/港股**：akshare
- 缓存层：SQLite 本地文件

## 理由

| 维度 | yfinance | akshare | 商业 API（Wind/iFind） |
| --- | --- | --- | --- |
| 美股/全球 | ✅ 覆盖全 | ❌ 不覆盖 | ✅ 覆盖全 |
| A 股 | ⚠️ 部分 | ✅ 覆盖全 | ✅ 覆盖全 |
| 免费 | ✅ | ✅ | ❌ 几万/年 |
| 稳定性 | ⚠️ 中等（Yahoo 改了页面就要修） | ⚠️ 中等（接口偶发变） | ✅ |
| 频率 | 日频足够 | 日频足够 | 日/分钟 |

商业 API 太贵（券商账号另算），pass。免费的两个**互补**覆盖主要市场。

## 后果

- **正面**：零成本，覆盖 90% 用户场景
- **负面**：数据源不稳定（要锁版本 + 重试 + 缓存）
- **风险**：yfinance 的 GitHub issue 区经常有"突然坏了"的帖子
- **缓解**：
  - SQLite 缓存所有请求（断网时仍可用缓存）
  - 重试 + 指数退避
  - 锁依赖版本（`yfinance>=0.2.40,<0.3`）

## 备选方案

- **tushare**：和 akshare 类似，但需要注册 token（积分制），pass
- **Alpha Vantage**：免费版限流严重（5 req/min），pass
- **自己爬**：维护成本高，pass

## 后续

如果 yfinance/akshare 长期挂掉，v0.2 考虑接商业 API（接口层已经抽象好，加新 source 就行）。
