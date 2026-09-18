"""策略配置高层业务方法。

参照 specs/091-strategy-config.md。

封装 StrategyDB，提供：
- create_strategy / new_version / activate_version
- add_sleeve / add_target
- list / get helpers（带 sleeves + targets 一起拿）
- check：校验权重和、band、selection 必填 signal
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from global_allocation.strategy.db import StrategyDB
from global_allocation.strategy.models import (
    PlanSleeve,
    PlanTarget,
    SelectionConfig,
    Strategy,
    StrategyStatus,
    StrategyVersion,
)

_TOLERANCE = Decimal("0.0001")


@dataclass
class CheckResult:
    """check 校验结果。"""

    passed: bool
    errors: list[str] = field(default_factory=list)


@dataclass
class FullVersion:
    """一个 version + 它的所有 sleeves + targets。"""

    version: StrategyVersion
    sleeves: list[PlanSleeve]
    targets: dict[int, list[PlanTarget]]  # sleeve_id -> targets


class StrategyRepo:
    """策略配置业务层。"""

    def __init__(self, db: StrategyDB) -> None:
        self._db = db

    # ─── strategy ───

    def create_strategy(self, s: Strategy) -> None:
        self._db.insert_strategy(s)

    def get_strategy(self, strategy_id: str) -> Strategy | None:
        return self._db.get_strategy(strategy_id)

    def list_strategies(self) -> list[Strategy]:
        return self._db.list_strategies()

    def delete_strategy(self, strategy_id: str) -> None:
        self._db.delete_strategy(strategy_id)

    # ─── version ───

    def new_version(self, v: StrategyVersion) -> int:
        # version 号必须严格递增
        latest = self._db.get_latest_version(v.strategy_id)
        if latest is not None and v.version != latest.version + 1:
            raise ValueError(
                f"version must strictly increment: latest={latest.version}, "
                f"got={v.version}"
            )
        return self._db.insert_version(v)

    def get_version(self, version_id: int) -> StrategyVersion | None:
        return self._db.get_version(version_id)

    def get_active_version(self, strategy_id: str) -> StrategyVersion | None:
        """返回当前生效的 version（按 strategies.active_version 指针）。"""
        s = self._db.get_strategy(strategy_id)
        if s is None or s.active_version is None:
            # fallback：取最新一条（兼容还没 activate 的情况）
            return self._db.get_latest_version(strategy_id)
        rows = self._db.list_versions(strategy_id)
        for v in rows:
            if v.version == s.active_version:
                return v
        return None

    def list_versions(self, strategy_id: str) -> list[StrategyVersion]:
        return self._db.list_versions(strategy_id)

    def activate_version(self, strategy_id: str, version: int) -> None:
        # 1. 校验通过 check
        result = self.check(strategy_id, version)
        if not result.passed:
            errs = "; ".join(result.errors)
            raise ValueError(
                f"version {version} check failed for strategy '{strategy_id}': {errs}"
            )

        # 2. 把同 strategy 的所有 version 标 archived（事务）
        versions = self._db.list_versions(strategy_id)
        for v in versions:
            if v.version == version and v.id is not None:
                self._db.update_version_status(v.id, StrategyStatus.ACTIVE)
            elif v.id is not None:
                self._db.update_version_status(v.id, StrategyStatus.ARCHIVED)

        # 3. 更新 strategy 指针
        self._db.update_active_version(strategy_id, version)

    # ─── sleeve / target ───

    def add_sleeve(self, s: PlanSleeve) -> int:
        return self._db.insert_sleeve(s)

    def add_target(self, t: PlanTarget) -> int:
        return self._db.insert_target(t)

    def list_sleeves(self, version_id: int) -> list[PlanSleeve]:
        return self._db.list_sleeves(version_id)

    def list_targets(self, sleeve_id: int) -> list[PlanTarget]:
        return self._db.list_targets(sleeve_id)

    def get_full_version(self, version_id: int) -> FullVersion | None:
        """拿一个 version + 它的所有 sleeves + targets（一次拿全）。"""
        v = self._db.get_version(version_id)
        if v is None:
            return None
        sleeves = self._db.list_sleeves(version_id)
        targets: dict[int, list[PlanTarget]] = {}
        for s in sleeves:
            if s.id is not None:
                targets[s.id] = self._db.list_targets(s.id)
        return FullVersion(version=v, sleeves=sleeves, targets=targets)

    # ─── check ───

    def check(self, strategy_id: str, version: int) -> CheckResult:
        """校验权重和 + band + 必填字段。返回 CheckResult。"""
        # 找 version
        versions = self._db.list_versions(strategy_id)
        target_version: StrategyVersion | None = None
        for v in versions:
            if v.version == version:
                target_version = v
                break
        if target_version is None or target_version.id is None:
            return CheckResult(
                passed=False, errors=[f"version {version} not found"]
            )

        full = self.get_full_version(target_version.id)
        if full is None:
            return CheckResult(passed=False, errors=["version data missing"])

        errs: list[str] = []
        sleeves = full.sleeves
        targets_map = full.targets

        # 1. 必须有至少 1 个 sleeve
        if not sleeves:
            errs.append("至少需要 1 个 sleeve")

        # 2. sleeve 权重和 = 1.0
        sleeve_total = sum((s.target_weight for s in sleeves), Decimal("0"))
        if abs(sleeve_total - Decimal("1.0")) > _TOLERANCE:
            errs.append(f"sleeve 权重和 = {sleeve_total}, 必须 = 1.0")

        # 3. 每个 sleeve：内部 target 权重和 + 每个 target weight ∈ band
        for s in sleeves:
            sub_targets = targets_map.get(s.id or -1, [])
            if not sub_targets:
                errs.append(f"sleeve {s.code} 没有 target")
                continue
            sub_total = sum((t.weight for t in sub_targets), Decimal("0"))
            if abs(sub_total - Decimal("1.0")) > _TOLERANCE:
                errs.append(
                    f"sleeve {s.code} 内 target 权重和 = {sub_total}, 必须 = 1.0"
                )

        # 4. selection 策略必须配 entry/exit signal
        if isinstance(target_version.config, SelectionConfig):
            if target_version.config.entry_signal is None:
                errs.append("selection 策略必须配置 entry_signal")
            if target_version.config.exit_signal is None:
                errs.append("selection 策略必须配置 exit_signal")

        # 5. allocation 策略的 trigger 合法性（threshold ∈ (0, 1)）— 在 Pydantic 层已校验

        return CheckResult(passed=len(errs) == 0, errors=errs)

    def check_active(self, strategy_id: str) -> CheckResult:
        """校验当前 active version。"""
        s = self._db.get_strategy(strategy_id)
        if s is None or s.active_version is None:
            return CheckResult(passed=False, errors=["no active version"])
        return self.check(strategy_id, s.active_version)


__all__ = ["StrategyRepo", "CheckResult", "FullVersion"]
