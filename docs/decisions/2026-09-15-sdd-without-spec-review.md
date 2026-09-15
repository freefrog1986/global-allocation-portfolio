# 决策日志：2026-09-15 — SDD 不强制 review

> 类型：流程调整
> 决策者：liubo

## 背景

specs/001-spec-driven-process.md 默认流程里写了"（可选）review spec"。

## 决策

**本仓库单人开发，spec 写完直接动代码，不需要 review 环节。**

## 影响

- specs/000-index.md 状态表新增 "Implemented" 时机改为：commit + push 后
- commit message 仍然要引用 spec 编号（spec 001 不变）
- 测试覆盖率 ≥ 80% 仍然是硬指标（spec 001 不变）

## 触发条件

如果未来这个仓库变成多人开发，需要恢复 review 流程。届时在 specs/001 里加回 review gate，并在 PR template 里强制要求 spec 链接。
