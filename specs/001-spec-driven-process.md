# Spec 001: Spec-Driven Development 流程

> 状态：Stable
> 最后更新：2026-09-15

## 目标

定义本仓库的开发流程：**每个功能先写规格，再写代码**。规格是需求的唯一权威来源，code review 不接受规格里没写的特性。

## 为什么 SDD

- **避免代码驱动需求** —— 没有 spec 就写代码，会让实现带着个人偏好走偏
- **可 review 的设计文档** —— spec 用 git 管理，可 diff、可 blame
- **可追溯的实现** —— spec 里有"实现 PR"字段，commit 信息反过来引用 spec 编号
- **强制把模糊需求写清楚** —— 写 spec 的过程就是澄清需求

## 流程

每个新功能必须按顺序经过：

```
1. 写 specs/NNN-<功能名>.md（基于 _template.md）
2. 在 specs/000-index.md 注册
3. （可选）写 docs/decisions/YYYY-MM-DD-<决策>.md 如果过程里有设计权衡
4. 在 docs/adr/ 写 ADR 如果引入了新库/新架构（沿用 NNN 编号体系）
5. 写 tests/ 里的测试（RED）
6. 跑测试，确认失败
7. 在 src/ 里写实现（GREEN）
8. 重构（IMPROVE）
9. 跑 pytest --cov，确认覆盖率 ≥ 80%
10. commit，commit message 引用 spec 编号："feat(020): implement strategy base class"
11. 更新 CHANGELOG.md 的 Unreleased 段
12. （可选）开 PR —— 本仓库单人开发，可直接 push main
```

## 编号体系

- `specs/NNN-<功能名>.md` —— NNN 是 3 位数字，按实现顺序递增
- `docs/adr/NNNN-<决策名>.md` —— NNNN 是 4 位数字
- `docs/decisions/YYYY-MM-DD-<决策名>.md` —— 按日期组织

## Spec 模板

见 [`_template.md`](_template.md)。每个 spec 必须包含：

- **目标** —— 一句话说清
- **不在范围内** —— 防止蔓延
- **API 概览** —— 函数签名 + 示例
- **数据契约** —— 涉及的 model
- **边界情况** —— 列出 ≥3 个
- **验收标准** —— checklist + 覆盖率
- **依赖** —— 引用其他 spec

## 状态机

```
Draft → Stable → Implemented → Deprecated
```

- **Draft**：还在改的草案
- **Stable**：API 锁定，可以照着实现
- **Implemented**：代码已实现，spec 应冻结
- **Deprecated**：被新 spec 替代，链接到继任者

## commit message 规范

按 `common/git-workflow.md`（仓库根）的 conventional commits 格式，且**必须引用 spec 编号**：

```
feat(020): implement strategy base class

- Add Strategy ABC with weights() and rebalance() abstract methods
- Add TargetWeight dataclass for weight specifications
- See specs/020-strategy-base.md

Closes specs/020
```

前缀作用域 `(NNN)` 对应 spec 编号。

## 违反流程的处理

- 没有 spec 就提交代码 → reviewer 直接打回
- spec 改了但 commit 没引用 → commit message 也要 amend
- 测试覆盖率 < 80% → 不能 merge

## 不在范围内

- **不强制 review** —— 当前仓库单人开发，spec 写完直接开干（用户要求）
- **不引入 spec-as-test 框架**（如 cucumber）—— 普通 pytest 测试已经够
- **不**写"已完成" spec 的 archive —— git 历史就是 archive
