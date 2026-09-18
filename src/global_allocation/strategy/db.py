"""策略配置管理 SQLite 存储。

参照 specs/091-strategy-config.md。

表：strategies / strategy_versions / plan_sleeves / plan_targets
Decimal 全存 text（精度无损）。
config 字段按 type 序列化为 JSON 字符串。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from global_allocation.strategy.models import (
    AllocationConfig,
    PlanSleeve,
    PlanTarget,
    SelectionConfig,
    Strategy,
    StrategyStatus,
    StrategyVersion,
)


class StrategyDB:
    """策略配置 SQLite 包装。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        # 开启 FK 约束（cascade delete 才生效）
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> StrategyDB:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ─── schema ───

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategies (
                id              TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                type            TEXT NOT NULL,
                description     TEXT NOT NULL DEFAULT '',
                active_version  INTEGER,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS strategy_versions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id     TEXT NOT NULL,
                version         INTEGER NOT NULL,
                status          TEXT NOT NULL,
                notes           TEXT NOT NULL DEFAULT '',
                config_json     TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                UNIQUE(strategy_id, version),
                FOREIGN KEY (strategy_id) REFERENCES strategies(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_versions_strategy
                ON strategy_versions(strategy_id, version DESC);

            CREATE TABLE IF NOT EXISTS plan_sleeves (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                version_id      INTEGER NOT NULL,
                code            TEXT NOT NULL,
                name            TEXT NOT NULL,
                target_weight   TEXT NOT NULL,
                min_weight      TEXT NOT NULL,
                max_weight      TEXT NOT NULL,
                tags            TEXT NOT NULL DEFAULT '[]',
                position        INTEGER NOT NULL DEFAULT 0,
                UNIQUE(version_id, code),
                FOREIGN KEY (version_id) REFERENCES strategy_versions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS plan_targets (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                sleeve_id       INTEGER NOT NULL,
                fund_code       TEXT NOT NULL,
                weight          TEXT NOT NULL,
                min_weight      TEXT NOT NULL,
                max_weight      TEXT NOT NULL,
                position        INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (sleeve_id) REFERENCES plan_sleeves(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_targets_sleeve
                ON plan_targets(sleeve_id);
            """
        )
        self._conn.commit()

    # ─── strategy CRUD ───

    def insert_strategy(self, s: Strategy) -> None:
        self._conn.execute(
            """
            INSERT INTO strategies (id, name, type, description, active_version, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                s.id,
                s.name,
                s.type.value,
                s.description,
                s.active_version,
                s.created_at.isoformat(),
                s.updated_at.isoformat(),
            ),
        )
        self._conn.commit()

    def get_strategy(self, strategy_id: str) -> Strategy | None:
        row = self._conn.execute(
            "SELECT * FROM strategies WHERE id = ?", (strategy_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_strategy(row)

    def list_strategies(self) -> list[Strategy]:
        rows = self._conn.execute(
            "SELECT * FROM strategies ORDER BY id"
        ).fetchall()
        return [self._row_to_strategy(r) for r in rows]

    def delete_strategy(self, strategy_id: str) -> None:
        self._conn.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))
        self._conn.commit()

    def update_active_version(self, strategy_id: str, version: int | None) -> None:
        self._conn.execute(
            "UPDATE strategies SET active_version = ?, updated_at = ? WHERE id = ?",
            (version, datetime.now().isoformat(), strategy_id),
        )
        self._conn.commit()

    # ─── version CRUD ───

    def insert_version(self, v: StrategyVersion) -> int:
        config_json = v.config.model_dump_json()
        cur = self._conn.execute(
            """
            INSERT INTO strategy_versions
                (strategy_id, version, status, notes, config_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                v.strategy_id,
                v.version,
                v.status.value,
                v.notes,
                config_json,
                v.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return cur.lastrowid or 0

    def get_version(self, version_id: int) -> StrategyVersion | None:
        row = self._conn.execute(
            "SELECT * FROM strategy_versions WHERE id = ?", (version_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_version(row)

    def get_latest_version(self, strategy_id: str) -> StrategyVersion | None:
        row = self._conn.execute(
            """
            SELECT * FROM strategy_versions
            WHERE strategy_id = ?
            ORDER BY version DESC LIMIT 1
            """,
            (strategy_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_version(row)

    def list_versions(self, strategy_id: str) -> list[StrategyVersion]:
        rows = self._conn.execute(
            "SELECT * FROM strategy_versions WHERE strategy_id = ? ORDER BY version",
            (strategy_id,),
        ).fetchall()
        return [self._row_to_version(r) for r in rows]

    def update_version_status(self, version_id: int, status: StrategyStatus) -> None:
        self._conn.execute(
            "UPDATE strategy_versions SET status = ? WHERE id = ?",
            (status.value, version_id),
        )
        self._conn.commit()

    # ─── sleeve CRUD ───

    def insert_sleeve(self, s: PlanSleeve) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO plan_sleeves
                (version_id, code, name, target_weight, min_weight, max_weight, tags, position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                s.version_id,
                s.code,
                s.name,
                str(s.target_weight),
                str(s.min_weight),
                str(s.max_weight),
                json.dumps(s.tags, ensure_ascii=False),
                s.position,
            ),
        )
        self._conn.commit()
        return cur.lastrowid or 0

    def get_sleeve(self, sleeve_id: int) -> PlanSleeve | None:
        row = self._conn.execute(
            "SELECT * FROM plan_sleeves WHERE id = ?", (sleeve_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_sleeve(row)

    def list_sleeves(self, version_id: int) -> list[PlanSleeve]:
        rows = self._conn.execute(
            "SELECT * FROM plan_sleeves WHERE version_id = ? ORDER BY position",
            (version_id,),
        ).fetchall()
        return [self._row_to_sleeve(r) for r in rows]

    # ─── target CRUD ───

    def insert_target(self, t: PlanTarget) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO plan_targets
                (sleeve_id, fund_code, weight, min_weight, max_weight, position)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                t.sleeve_id,
                t.fund_code,
                str(t.weight),
                str(t.min_weight),
                str(t.max_weight),
                t.position,
            ),
        )
        self._conn.commit()
        return cur.lastrowid or 0

    def list_targets(self, sleeve_id: int) -> list[PlanTarget]:
        rows = self._conn.execute(
            "SELECT * FROM plan_targets WHERE sleeve_id = ? ORDER BY position",
            (sleeve_id,),
        ).fetchall()
        return [self._row_to_target(r) for r in rows]

    # ─── row mappers ───

    def _row_to_strategy(self, row: sqlite3.Row) -> Strategy:
        return Strategy(
            id=row["id"],
            name=row["name"],
            type=row["type"],
            description=row["description"],
            active_version=row["active_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _row_to_version(self, row: sqlite3.Row) -> StrategyVersion:
        config_data = json.loads(row["config_json"])
        # 决定 config 类型
        if "rebalance_trigger" in config_data or "base_currency" in config_data:
            config: AllocationConfig | SelectionConfig = AllocationConfig.model_validate(
                config_data
            )
        else:
            config = SelectionConfig.model_validate(config_data)
        return StrategyVersion(
            id=row["id"],
            strategy_id=row["strategy_id"],
            version=row["version"],
            status=row["status"],
            notes=row["notes"],
            config=config,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _row_to_sleeve(self, row: sqlite3.Row) -> PlanSleeve:
        return PlanSleeve(
            id=row["id"],
            version_id=row["version_id"],
            code=row["code"],
            name=row["name"],
            target_weight=Decimal(row["target_weight"]),
            min_weight=Decimal(row["min_weight"]),
            max_weight=Decimal(row["max_weight"]),
            tags=json.loads(row["tags"]),
            position=row["position"],
        )

    def _row_to_target(self, row: sqlite3.Row) -> PlanTarget:
        return PlanTarget(
            id=row["id"],
            sleeve_id=row["sleeve_id"],
            fund_code=row["fund_code"],
            weight=Decimal(row["weight"]),
            min_weight=Decimal(row["min_weight"]),
            max_weight=Decimal(row["max_weight"]),
            position=row["position"],
        )


__all__ = ["StrategyDB"]
