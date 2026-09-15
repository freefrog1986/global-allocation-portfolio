# ADR 0005: 本地存储用 SQLite

> 状态：Accepted
> 日期：2026-09-15
> 决策者：liubo

## 背景

需要本地存储：
1. 历史价格缓存（spec 040）
2. 策略配置（用户自定义 YAML）
3. 回测结果（spec 050）
4. 凭证（spec 080）

## 决策

**价格缓存 + 回测结果用 SQLite**（文件 `~/.local/share/gap/cache.db`）
**策略配置用 YAML**（文件 `~/.config/gap/strategies/*.yaml`）
**凭证用 JSON 文件**（`~/.config/gap/credentials.json`，chmod 600）

## 理由

### SQLite

- stdlib 自带，零依赖
- 单文件，git 忽略（按 .gitignore）
- 适合读多写少的场景（行情缓存基本只写一次、读很多次）
- pandas 原生支持 `pd.read_sql`

### YAML

- 策略配置是人写的，要可读、可 diff
- pydantic 直接 `Strategy.model_validate_yaml()` 校验
- 比 JSON 少噪音（不用引号）

### JSON（凭证）

- 凭证是机器读的，不给人看
- 用 keyring 是 v0.2 的事（MVP 简化）

## 后果

- **正面**：零运维、跨平台、单文件备份方便
- **负面**：并发写有锁（但本地单人用，无影响）
- **风险**：凭证明文存盘（用 chmod 600 + 不进 git 缓解）

## 备选方案

| 方案 | 否决理由 |
| --- | --- |
| PostgreSQL | 单人本地用，overkill |
| DuckDB | 适合 OLAP 但 spec 050 没复杂查询 |
| CSV | 没事务，容易坏 |
| JSON 文件存数据 | 大数据慢，不支持范围查询 |
