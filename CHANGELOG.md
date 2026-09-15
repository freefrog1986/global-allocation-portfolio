# Changelog

所有这个项目的显著改动都记录在这里。格式基于 [Keep a Changelog](https://keepachangelog.com/)，版本遵循 [SemVer](https://semver.org/)。

> 提交约定见 `.gitmessage` 提示，按 conventional commits（`feat:` / `fix:` / `test:` / `docs:` / `refactor:` / `chore:` / `perf:` / `ci:`）写。

## [Unreleased]

### Added
- 用户文档（`docs/user-guide.md` / `docs/strategies.md` / `docs/feishu-setup.md`）

### Fixed
- `BacktestEngine._rebalance_to` 在 Decimal 精度下可能产生极小的负 cash（~1E-25），
  导致 `PortfolioSnapshot.cash >= 0` 校验失败（永久组合在 5y 实盘回测里复现）。
  修复：在返回前把 `(-1E-20, 0)` 区间内的 cash clamp 到 0。

## [0.1.0] - 2026-09-15

第一版 MVP。已交付：

- 核心模型（Pydantic v2 frozen）：Asset / Strategy / TargetWeight / RebalanceRule / BacktestResult / PerformanceMetrics（specs/010）
- 策略基类 + 4 个内置策略：60/40、永久组合、桥水全天候、风险平价（specs/020, 030）
- 数据获取层：yfinance + akshare 双源，自动选 + 3-retry 指数退避，SQLite 缓存（XDG_DATA_HOME）（specs/040）
- 回测引擎（事件驱动日频）+ 性能指标（CAGR / Sharpe / 最大回撤 / 波动率 / 胜率 / 最佳/最差单日 / 相关性矩阵）（specs/050, 060）
- CLI（Typer）：`gap strategy {list,show,validate}` / `gap data {list,clear,fetch}` / `gap backtest` / `gap compare` / `gap publish` / `gap version`
- 飞书 chart card 报告（lark-oapi SDK）：资金曲线 line chart + 当前权重 pie + 关键指标 table，自动降采样到 1000 点
- 凭证加载：env vars / `~/.config/gap/credentials.json` 二选一，env 优先
- 单元测试 213 个，覆盖率 92.71%（`pytest` + `pytest-cov`）
- CI：ruff lint + mypy strict + pytest
- 规格文档 `specs/000-080` 与 ADR 模板

[Unreleased]: https://github.com/freefrog1986/global-allocation-portfolio/compare/main...HEAD
