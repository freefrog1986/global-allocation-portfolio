# Changelog

所有这个项目的显著改动都记录在这里。格式基于 [Keep a Changelog](https://keepachangelog.com/)，版本遵循 [SemVer](https://semver.org/)。

> 提交约定见 `.gitmessage` 提示，按 conventional commits（`feat:` / `fix:` / `test:` / `docs:` / `refactor:` / `chore:` / `perf:` / `ci:`）写。

## [Unreleased]

### Added
- 项目骨架（pyproject / CI / README / LICENSE / .gitignore）
- 规格文档（`specs/`）与架构决策记录（`docs/adr/`）模板
- 计划支持的功能：策略基类、4 个内置策略、数据获取、回测引擎、CLI、飞书 chart card 报告

### Changed
- 无

### Removed
- 无

## [0.1.0] - TBD

第一版 MVP。计划包含：
- 策略基类 + 4 个内置策略（60/40、永久组合、全天候、风险平价）
- yfinance + akshare 历史数据获取（SQLite 缓存）
- 向量化回测引擎 + 性能指标（CAGR / 夏普 / 最大回撤 / 波动率 / 相关性）
- CLI（`gap` 命令）
- 飞书 interactive card 发图（chart card，含 line / bar / pie）

[Unreleased]: https://github.com/freefrog1986/global-allocation-portfolio/compare/main...HEAD
