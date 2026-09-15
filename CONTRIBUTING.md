# 贡献指南

> 当前是单人开发，欢迎 issue / PR。

## 流程

按 [`specs/001-spec-driven-process.md`](specs/001-spec-driven-process.md)：

1. 写 spec（除非是 typo / doc-only 改动）
2. 写测试（RED）
3. 写实现（GREEN）
4. 重构
5. 跑 `pytest --cov`，覆盖率 ≥ 80%
6. commit，message 引用 spec 编号

## 提交规范

按 conventional commits：

```
feat(020): add StrategyBase ABC

Implements specs/020-strategy-base.md.

- Add StrategyBase abstract class with build() / validate()
- Add unit tests covering 6 boundary cases
- Coverage: 92%
```

类型：`feat` / `fix` / `refactor` / `test` / `docs` / `chore` / `perf` / `ci`
作用域：spec 编号（如 `020`）或模块名（如 `data` / `backtest` / `cli`）

## 本地开发

```bash
git clone https://github.com/freefrog1986/global-allocation-portfolio.git
cd global-allocation-portfolio
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,backtest]'

# 跑测试
pytest

# 跑特定 spec 的测试
pytest tests/unit/test_strategy_base.py -v

# 跑非 integration 测试（CI 默认）
pytest -m "not integration"

# 跑全部测试（含 integration）
GAP_RUN_INTEGRATION=1 pytest

# lint + 类型检查
ruff check src tests
ruff format src tests
mypy src
```

## 风格

- 200-400 行/文件，800 行硬上限
- 函数 < 50 行
- 用 `@final` 标注不打算被子类重写的方法
- 用 `Decimal` 做金额，不用 `float`
- 用 Pydantic v2 做数据校验
- 测试用 AAA（Arrange-Act-Assert）

## Issue / PR

- 单 issue/PR 一个主题
- 标题简洁，body 描述背景 + 改动 + 测试
- PR 链接对应 spec / issue

## 不要做的事

- ❌ 跳过 spec 直接写代码（除非 typo / doc-only）
- ❌ 跳过测试直接 commit
- ❌ 覆盖率 < 80% 就 push
- ❌ commit message 不引用 spec 编号
- ❌ 把凭证 / `.env` / 本地数据库 commit 进去
