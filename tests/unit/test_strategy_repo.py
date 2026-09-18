"""测试 src/global_allocation/strategy/repo.py。

参照 specs/091-strategy-config.md。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from global_allocation.strategy.db import StrategyDB
from global_allocation.strategy.models import (
    AllocationConfig,
    RebalanceTrigger,
    SelectionConfig,
    Strategy,
    StrategyStatus,
    StrategyType,
    StrategyVersion,
    ValuationSignal,
)
from global_allocation.strategy.repo import CheckResult, StrategyRepo


@pytest.fixture
def repo(tmp_path: Path) -> StrategyRepo:
    return StrategyRepo(db=StrategyDB(path=tmp_path / "strategy.db"))


def _init_strategy(
    repo: StrategyRepo,
    type_: StrategyType = StrategyType.ALLOCATION,
    config: AllocationConfig | SelectionConfig | None = None,
) -> Strategy:
    s = Strategy(
        id="a-share-dividend",
        name="A 股红利",
        type=type_,
        created_at=datetime(2026, 9, 18),
        updated_at=datetime(2026, 9, 18),
    )
    repo.create_strategy(s)
    # 默认总是创建 v1（config 默认 AllocationConfig，跟 type 默认 ALLOCATION 一致）
    if config is None:
        config = AllocationConfig() if type_ == StrategyType.ALLOCATION else SelectionConfig()
    v = StrategyVersion(
        strategy_id="a-share-dividend",
        version=1,
        status=StrategyStatus.DRAFT,
        config=config,
        created_at=datetime(2026, 9, 18),
    )
    repo.new_version(v)
    return s


def _add_sleeve_with_target(
    repo: StrategyRepo,
    version_id: int,
    sleeve_code: str = "financial",
    sleeve_target: Decimal = Decimal("0.40"),
    sleeve_min: Decimal = Decimal("0.30"),
    sleeve_max: Decimal = Decimal("0.50"),
    targets: list[tuple[str, Decimal, Decimal, Decimal]] | None = None,
) -> int:
    from global_allocation.strategy.models import PlanSleeve, PlanTarget

    s = PlanSleeve(
        version_id=version_id,
        code=sleeve_code,
        name=sleeve_code,
        target_weight=sleeve_target,
        min_weight=sleeve_min,
        max_weight=sleeve_max,
        position=0,
    )
    sid = repo.add_sleeve(s)
    if targets:
        for pos, (fund, w, lo, hi) in enumerate(targets):
            t = PlanTarget(
                sleeve_id=sid,
                fund_code=fund,
                weight=w,
                min_weight=lo,
                max_weight=hi,
                position=pos,
            )
            repo.add_target(t)
    return sid


def _add_valid_plan(
    repo: StrategyRepo,
    version_id: int,
    n_sleeves: int = 2,
) -> None:
    """添加一个能通过 check 的有效 plan：n_sleeves 个 sleeve，每个 2 个 target。

    每个 sleeve 的 target_weight = 1/n_sleeves，sleeve 内 2 个 target 各 0.5。
    """
    sleeve_w = Decimal("1") / Decimal(n_sleeves)
    sleeve_min = sleeve_w - Decimal("0.05")
    sleeve_max = sleeve_w + Decimal("0.05")
    for i in range(n_sleeves):
        _add_sleeve_with_target(
            repo,
            version_id,
            sleeve_code=f"sleeve_{i}",
            sleeve_target=sleeve_w,
            sleeve_min=sleeve_min,
            sleeve_max=sleeve_max,
            targets=[
                (f"fund_{i}_a", Decimal("0.50"), Decimal("0.40"), Decimal("0.60")),
                (f"fund_{i}_b", Decimal("0.50"), Decimal("0.40"), Decimal("0.60")),
            ],
        )


# ─── create_strategy ────────────────────────────────────────


class TestCreateStrategy:
    def test_basic(self, repo: StrategyRepo) -> None:
        _init_strategy(repo)
        fetched = repo.get_strategy("a-share-dividend")
        assert fetched is not None
        assert fetched.id == "a-share-dividend"

    def test_duplicate_id_raises(self, repo: StrategyRepo) -> None:
        _init_strategy(repo)
        s2 = Strategy(
            id="a-share-dividend",
            name="dup",
            type=StrategyType.ALLOCATION,
            created_at=datetime(2026, 9, 18),
            updated_at=datetime(2026, 9, 18),
        )
        with pytest.raises(sqlite3.IntegrityError):
            repo.create_strategy(s2)


# ─── new_version ─────────────────────────────────────────────


class TestNewVersion:
    def test_first_version_starts_at_1(self, repo: StrategyRepo) -> None:
        _init_strategy(repo)
        latest = repo.get_active_version("a-share-dividend")
        # 初始化时没有 active（因为是 draft）
        assert latest is not None
        assert latest.version == 1
        assert latest.status == StrategyStatus.DRAFT

    def test_second_version_increments(self, repo: StrategyRepo) -> None:
        _init_strategy(repo)
        v2 = StrategyVersion(
            strategy_id="a-share-dividend",
            version=2,
            status=StrategyStatus.DRAFT,
            config=AllocationConfig(),
            created_at=datetime(2026, 9, 18),
        )
        repo.new_version(v2)
        all_versions = repo.list_versions("a-share-dividend")
        assert [v.version for v in all_versions] == [1, 2]

    def test_skip_version_rejected(self, repo: StrategyRepo) -> None:
        _init_strategy(repo)
        v3 = StrategyVersion(
            strategy_id="a-share-dividend",
            version=3,  # skip 2
            status=StrategyStatus.DRAFT,
            config=AllocationConfig(),
            created_at=datetime(2026, 9, 18),
        )
        with pytest.raises(ValueError, match="strictly increment"):
            repo.new_version(v3)


# ─── activate_version ───────────────────────────────────────


class TestActivateVersion:
    def test_basic(self, repo: StrategyRepo) -> None:
        _init_strategy(repo)
        latest = repo.get_active_version("a-share-dividend")
        assert latest is not None
        # 加一个完整 plan 让 check 通过
        _add_valid_plan(repo, latest.id)  # type: ignore[arg-type]
        repo.activate_version("a-share-dividend", 1)
        s = repo.get_strategy("a-share-dividend")
        assert s is not None
        assert s.active_version == 1

    def test_failed_check_blocks_activate(self, repo: StrategyRepo) -> None:
        _init_strategy(repo)
        # 不加 sleeve，check 失败
        with pytest.raises(ValueError, match="check failed"):
            repo.activate_version("a-share-dividend", 1)

    def test_cascades_archive(self, repo: StrategyRepo) -> None:
        _init_strategy(repo)
        v1 = repo.get_active_version("a-share-dividend")
        assert v1 is not None
        _add_valid_plan(repo, v1.id)  # type: ignore[arg-type]
        repo.activate_version("a-share-dividend", 1)

        v2 = StrategyVersion(
            strategy_id="a-share-dividend",
            version=2,
            status=StrategyStatus.DRAFT,
            config=AllocationConfig(),
            created_at=datetime(2026, 9, 18),
        )
        repo.new_version(v2)
        # v2 不是 active（v1 仍是），按 version 号找
        v2_row = next(
            v for v in repo.list_versions("a-share-dividend") if v.version == 2
        )
        _add_valid_plan(repo, v2_row.id)  # type: ignore[arg-type]
        repo.activate_version("a-share-dividend", 2)

        # v1 应该是 archived
        all_versions = repo.list_versions("a-share-dividend")
        v1_fetched = next(v for v in all_versions if v.version == 1)
        v2_fetched = next(v for v in all_versions if v.version == 2)
        assert v1_fetched.status == StrategyStatus.ARCHIVED
        assert v2_fetched.status == StrategyStatus.ACTIVE


# ─── check ──────────────────────────────────────────────────


class TestCheck:
    def test_pass_valid_plan(self, repo: StrategyRepo) -> None:
        _init_strategy(repo)
        v = repo.get_active_version("a-share-dividend")
        assert v is not None
        _add_valid_plan(repo, v.id)  # type: ignore[arg-type]
        result = repo.check("a-share-dividend", 1)
        assert isinstance(result, CheckResult)
        assert result.passed
        assert result.errors == []

    def test_fail_sleeve_weight_not_sum_to_1(self, repo: StrategyRepo) -> None:
        _init_strategy(repo)
        v = repo.get_active_version("a-share-dividend")
        assert v is not None
        _add_sleeve_with_target(
            repo,
            v.id,  # type: ignore[arg-type]
            sleeve_target=Decimal("0.50"),
            sleeve_min=Decimal("0.40"),
            sleeve_max=Decimal("0.60"),
            targets=[("x", Decimal("0.50"), Decimal("0.40"), Decimal("0.60"))],
        )
        # 权重和 = 0.50 ≠ 1.0
        result = repo.check("a-share-dividend", 1)
        assert not result.passed
        assert any("权重和" in e for e in result.errors)

    def test_fail_target_weight_not_sum_to_1(self, repo: StrategyRepo) -> None:
        _init_strategy(repo)
        v = repo.get_active_version("a-share-dividend")
        assert v is not None
        _add_sleeve_with_target(
            repo,
            v.id,  # type: ignore[arg-type]
            sleeve_target=Decimal("0.60"),
            sleeve_min=Decimal("0.50"),
            sleeve_max=Decimal("0.70"),
            targets=[
                ("a", Decimal("0.30"), Decimal("0.20"), Decimal("0.40")),
                ("b", Decimal("0.30"), Decimal("0.20"), Decimal("0.40")),
            ],
        )
        # target 权重和 = 0.60 ≠ 1.0
        result = repo.check("a-share-dividend", 1)
        assert not result.passed
        assert any("权重和" in e for e in result.errors)

    def test_fail_selection_no_signals(self, repo: StrategyRepo) -> None:
        _init_strategy(
            repo, type_=StrategyType.SELECTION, config=SelectionConfig()
        )
        v = repo.get_active_version("a-share-dividend")
        assert v is not None
        _add_valid_plan(repo, v.id)  # type: ignore[arg-type]
        result = repo.check("a-share-dividend", 1)
        assert not result.passed
        assert any("signal" in e for e in result.errors)

    def test_pass_selection_with_signals(self, repo: StrategyRepo) -> None:
        cfg = SelectionConfig(
            entry_signal=ValuationSignal(
                entry_threshold=Decimal("30"), exit_threshold=Decimal("70")
            ),
            exit_signal=ValuationSignal(
                entry_threshold=Decimal("30"), exit_threshold=Decimal("70")
            ),
        )
        _init_strategy(repo, type_=StrategyType.SELECTION, config=cfg)
        v = repo.get_active_version("a-share-dividend")
        assert v is not None
        _add_valid_plan(repo, v.id)  # type: ignore[arg-type]
        result = repo.check("a-share-dividend", 1)
        assert result.passed


# ─── list / get helpers ────────────────────────────────────


class TestListHelpers:
    def test_list_strategies(self, repo: StrategyRepo) -> None:
        _init_strategy(repo)
        result = repo.list_strategies()
        assert len(result) == 1
        assert result[0].id == "a-share-dividend"

    def test_list_versions(self, repo: StrategyRepo) -> None:
        _init_strategy(repo)
        v2 = StrategyVersion(
            strategy_id="a-share-dividend",
            version=2,
            status=StrategyStatus.DRAFT,
            config=AllocationConfig(),
            created_at=datetime(2026, 9, 18),
        )
        repo.new_version(v2)
        result = repo.list_versions("a-share-dividend")
        assert [v.version for v in result] == [1, 2]

    def test_list_sleeves(self, repo: StrategyRepo) -> None:
        _init_strategy(repo)
        v = repo.get_active_version("a-share-dividend")
        assert v is not None
        _add_sleeve_with_target(repo, v.id, sleeve_code="financial")
        _add_sleeve_with_target(repo, v.id, sleeve_code="other")
        sleeves = repo.list_sleeves(v.id)  # type: ignore[arg-type]
        assert {s.code for s in sleeves} == {"financial", "other"}


# ─── get_active_version ────────────────────────────────────


class TestGetActiveVersion:
    def test_returns_latest(self, repo: StrategyRepo) -> None:
        _init_strategy(repo)
        v = repo.get_active_version("a-share-dividend")
        assert v is not None
        assert v.version == 1

    def test_returns_active_when_set(self, repo: StrategyRepo) -> None:
        _init_strategy(repo)
        v1 = repo.get_active_version("a-share-dividend")
        assert v1 is not None
        _add_valid_plan(repo, v1.id)  # type: ignore[arg-type]
        repo.activate_version("a-share-dividend", 1)
        # 此时 active_version 指向 v1
        v2 = StrategyVersion(
            strategy_id="a-share-dividend",
            version=2,
            status=StrategyStatus.DRAFT,
            config=AllocationConfig(),
            created_at=datetime(2026, 9, 18),
        )
        repo.new_version(v2)
        active = repo.get_active_version("a-share-dividend")
        assert active is not None
        # active_version 还是 1（v2 是 draft）
        assert active.version == 1


# ─── check on inactive version (e.g. draft) ────────────────


class TestCheckWithTrigger:
    def test_allocation_with_trigger_passes(self, repo: StrategyRepo) -> None:
        cfg = AllocationConfig(
            rebalance_trigger=RebalanceTrigger(
                calendar="quarterly", threshold=Decimal("0.05")
            )
        )
        _init_strategy(repo, type_=StrategyType.ALLOCATION, config=cfg)
        v = repo.get_active_version("a-share-dividend")
        assert v is not None
        _add_valid_plan(repo, v.id)  # type: ignore[arg-type]
        result = repo.check("a-share-dividend", 1)
        assert result.passed
