"""测试 src/global_allocation/strategy/db.py。

参照 specs/091-strategy-config.md。
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from global_allocation.strategy.db import StrategyDB
from global_allocation.strategy.models import (
    AllocationConfig,
    PlanSleeve,
    PlanTarget,
    SelectionConfig,
    Strategy,
    StrategyStatus,
    StrategyType,
    StrategyVersion,
)


@pytest.fixture
def db(tmp_path: Path) -> StrategyDB:
    return StrategyDB(path=tmp_path / "strategy.db")


def _make_strategy(
    id_: str = "a-share-dividend",
    name: str = "我的 A 股红利",
    type_: StrategyType = StrategyType.SELECTION,
) -> Strategy:
    return Strategy(
        id=id_,
        name=name,
        type=type_,
        description="",
        created_at=datetime(2026, 9, 18),
        updated_at=datetime(2026, 9, 18),
    )


def _make_version(
    strategy_id: str = "a-share-dividend",
    version: int = 1,
    status: StrategyStatus = StrategyStatus.DRAFT,
    config: AllocationConfig | SelectionConfig | None = None,
) -> StrategyVersion:
    if config is None:
        config = SelectionConfig()
    return StrategyVersion(
        strategy_id=strategy_id,
        version=version,
        status=status,
        config=config,
        created_at=datetime(2026, 9, 18),
    )


# ─── schema init ─────────────────────────────────────────────


class TestSchema:
    def test_init_creates_tables(self, db: StrategyDB) -> None:
        tables = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = [r["name"] for r in tables]
        assert "strategies" in names
        assert "strategy_versions" in names
        assert "plan_sleeves" in names
        assert "plan_targets" in names

    def test_path_parent_created(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "strategy.db"
        StrategyDB(path=nested)
        assert nested.parent.exists()


# ─── strategy CRUD ───────────────────────────────────────────


class TestStrategyCRUD:
    def test_insert_and_get(self, db: StrategyDB) -> None:
        s = _make_strategy()
        db.insert_strategy(s)
        fetched = db.get_strategy("a-share-dividend")
        assert fetched is not None
        assert fetched.id == "a-share-dividend"
        assert fetched.name == "我的 A 股红利"
        assert fetched.type == StrategyType.SELECTION
        assert fetched.active_version is None

    def test_get_missing_returns_none(self, db: StrategyDB) -> None:
        assert db.get_strategy("nonexistent") is None

    def test_list_strategies(self, db: StrategyDB) -> None:
        db.insert_strategy(_make_strategy(id_="a", name="A"))
        db.insert_strategy(_make_strategy(id_="b", name="B"))
        result = db.list_strategies()
        assert len(result) == 2
        ids = {s.id for s in result}
        assert ids == {"a", "b"}

    def test_delete_cascades(self, db: StrategyDB) -> None:
        db.insert_strategy(_make_strategy())
        v = _make_version()
        vid = db.insert_version(v)
        s = PlanSleeve(
            version_id=vid,
            code="financial",
            name="金融红利",
            target_weight=Decimal("0.40"),
            min_weight=Decimal("0.30"),
            max_weight=Decimal("0.50"),
            position=0,
        )
        sid = db.insert_sleeve(s)
        t = PlanTarget(
            sleeve_id=sid,
            fund_code="510300",
            weight=Decimal("0.50"),
            min_weight=Decimal("0.40"),
            max_weight=Decimal("0.60"),
            position=0,
        )
        db.insert_target(t)

        db.delete_strategy("a-share-dividend")

        # cascade
        assert db.get_version(vid) is None
        assert db._conn.execute(
            "SELECT COUNT(*) AS c FROM plan_sleeves WHERE id=?", (sid,)
        ).fetchone()["c"] == 0
        assert db._conn.execute(
            "SELECT COUNT(*) AS c FROM plan_targets WHERE id=?", (t.id,)
        ).fetchone()["c"] == 0

    def test_update_active_version(self, db: StrategyDB) -> None:
        db.insert_strategy(_make_strategy())
        db.update_active_version("a-share-dividend", 2)
        fetched = db.get_strategy("a-share-dividend")
        assert fetched is not None
        assert fetched.active_version == 2


# ─── version CRUD ────────────────────────────────────────────


class TestVersionCRUD:
    def test_insert_and_get(self, db: StrategyDB) -> None:
        db.insert_strategy(_make_strategy())
        v = _make_version()
        vid = db.insert_version(v)
        assert vid > 0

        fetched = db.get_version(vid)
        assert fetched is not None
        assert fetched.strategy_id == "a-share-dividend"
        assert fetched.version == 1
        assert isinstance(fetched.config, SelectionConfig)

    def test_get_latest_version(self, db: StrategyDB) -> None:
        db.insert_strategy(_make_strategy())
        db.insert_version(_make_version(version=1))
        db.insert_version(_make_version(version=2))
        db.insert_version(_make_version(version=3))

        latest = db.get_latest_version("a-share-dividend")
        assert latest is not None
        assert latest.version == 3

    def test_list_versions(self, db: StrategyDB) -> None:
        db.insert_strategy(_make_strategy())
        for n in (1, 2, 3):
            db.insert_version(_make_version(version=n))
        versions = db.list_versions("a-share-dividend")
        assert [v.version for v in versions] == [1, 2, 3]

    def test_config_round_trip_allocation(self, db: StrategyDB) -> None:
        db.insert_strategy(_make_strategy(type_=StrategyType.ALLOCATION))
        config = AllocationConfig(
            base_currency="usd",  # type: ignore[arg-type]
            rebalance_trigger={
                "calendar": "quarterly",  # type: ignore[arg-type]
                "threshold": "0.05",
                "cashflow": True,
            },  # type: ignore[arg-type]
        )
        db.insert_version(_make_version(config=config))
        latest = db.get_latest_version("a-share-dividend")
        assert latest is not None
        assert isinstance(latest.config, AllocationConfig)
        assert latest.config.base_currency.value == "usd"
        assert latest.config.rebalance_trigger.calendar == "quarterly"
        assert latest.config.rebalance_trigger.threshold == Decimal("0.05")
        assert latest.config.rebalance_trigger.cashflow is True

    def test_update_status(self, db: StrategyDB) -> None:
        db.insert_strategy(_make_strategy())
        db.insert_version(_make_version())
        latest = db.get_latest_version("a-share-dividend")
        assert latest is not None
        vid = latest.id
        assert vid is not None
        db.update_version_status(vid, StrategyStatus.ACTIVE)
        after = db.get_version(vid)
        assert after is not None
        assert after.status == StrategyStatus.ACTIVE


# ─── sleeve + target CRUD ───────────────────────────────────


class TestSleeveTargetCRUD:
    def _seed_sleeve(self, db: StrategyDB) -> int:
        db.insert_strategy(_make_strategy())
        db.insert_version(_make_version())
        latest = db.get_latest_version("a-share-dividend")
        assert latest is not None
        return latest.id  # type: ignore[return-value]

    def test_insert_and_get_sleeve(self, db: StrategyDB) -> None:
        vid = self._seed_sleeve(db)
        s = PlanSleeve(
            version_id=vid,
            code="financial",
            name="金融红利",
            target_weight=Decimal("0.40"),
            min_weight=Decimal("0.30"),
            max_weight=Decimal("0.50"),
            tags=["equity"],
            position=0,
        )
        sid = db.insert_sleeve(s)
        fetched = db.get_sleeve(sid)
        assert fetched is not None
        assert fetched.code == "financial"
        assert fetched.tags == ["equity"]
        assert fetched.target_weight == Decimal("0.40")

    def test_list_sleeves_by_version(self, db: StrategyDB) -> None:
        vid = self._seed_sleeve(db)
        for code, pos in [("a", 0), ("b", 1), ("c", 2)]:
            s = PlanSleeve(
                version_id=vid,
                code=code,
                name=code,
                target_weight=Decimal("0.30"),
                min_weight=Decimal("0.20"),
                max_weight=Decimal("0.40"),
                position=pos,
            )
            db.insert_sleeve(s)
        sleeves = db.list_sleeves(vid)
        assert [s.code for s in sleeves] == ["a", "b", "c"]

    def test_unique_sleeve_code_per_version(self, db: StrategyDB) -> None:
        vid = self._seed_sleeve(db)
        s = PlanSleeve(
            version_id=vid,
            code="x",
            name="x",
            target_weight=Decimal("0.30"),
            min_weight=Decimal("0.20"),
            max_weight=Decimal("0.40"),
            position=0,
        )
        db.insert_sleeve(s)
        with pytest.raises(Exception):  # sqlite3.IntegrityError
            db.insert_sleeve(s)

    def test_insert_and_list_targets(self, db: StrategyDB) -> None:
        vid = self._seed_sleeve(db)
        s = PlanSleeve(
            version_id=vid,
            code="financial",
            name="x",
            target_weight=Decimal("0.40"),
            min_weight=Decimal("0.30"),
            max_weight=Decimal("0.50"),
            position=0,
        )
        sid = db.insert_sleeve(s)
        for code, pos in [("510300", 0), ("008114", 1)]:
            t = PlanTarget(
                sleeve_id=sid,
                fund_code=code,
                weight=Decimal("0.50"),
                min_weight=Decimal("0.40"),
                max_weight=Decimal("0.60"),
                position=pos,
            )
            db.insert_target(t)
        targets = db.list_targets(sid)
        assert [t.fund_code for t in targets] == ["510300", "008114"]
