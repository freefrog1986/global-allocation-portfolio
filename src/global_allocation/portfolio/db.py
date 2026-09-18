"""实盘持仓账本 SQLite 存储。

参照 specs/090-portfolio-journal.md + specs/098-valuation-section.md。

表：funds / transactions / weekly_snapshots / valuation_indicators。
Decimal 全存 text（精度无损）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from global_allocation.portfolio.models import (
    Fund,
    Transaction,
    ValuationIndicator,
    ValuationIndicatorCode,
    WeeklySnapshot,
)


class PortfolioDB:
    """实盘账本 SQLite 包装。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> PortfolioDB:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ─── schema ───

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS funds (
                code        TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                asset_class TEXT NOT NULL,
                data_source TEXT NOT NULL,
                currency    TEXT NOT NULL DEFAULT 'CNY'
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                fund_code   TEXT NOT NULL,
                side        TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
                date        TEXT NOT NULL,
                shares      TEXT NOT NULL,
                price       TEXT NOT NULL,
                fee         TEXT NOT NULL DEFAULT '0',
                strategy    TEXT,
                tags        TEXT,
                note        TEXT,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (fund_code) REFERENCES funds(code)
            );

            CREATE INDEX IF NOT EXISTS idx_tx_fund_date
                ON transactions(fund_code, date);

            CREATE TABLE IF NOT EXISTS weekly_snapshots (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                week_end_date       TEXT NOT NULL UNIQUE,
                total_value         TEXT NOT NULL,
                week_return         TEXT NOT NULL DEFAULT '0',
                cumulative_return   TEXT NOT NULL DEFAULT '0',
                holdings_json       TEXT NOT NULL,
                created_at          TEXT NOT NULL
            );

            -- spec 098：估值指标快照（每日一次，自动 upsert）
            CREATE TABLE IF NOT EXISTS valuation_indicators (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                record_date     TEXT NOT NULL,             -- YYYY-MM-DD
                indicator_code  TEXT NOT NULL,             -- 4 个 enum 值
                value           TEXT NOT NULL,             -- Decimal as text
                source          TEXT NOT NULL,             -- "akshare:..." 来源标识
                UNIQUE (record_date, indicator_code)
            );

            CREATE INDEX IF NOT EXISTS idx_val_date
                ON valuation_indicators(record_date);
            """
        )
        self._conn.commit()

    # ─── funds ───

    def upsert_fund(self, fund: Fund) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO funds (code, name, asset_class, data_source, currency)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name=excluded.name,
                asset_class=excluded.asset_class,
                data_source=excluded.data_source,
                currency=excluded.currency
            """,
            (
                fund.code,
                fund.name,
                fund.asset_class.value,
                fund.data_source.value,
                fund.currency.value,
            ),
        )
        self._conn.commit()

    def get_fund(self, code: str) -> Fund | None:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM funds WHERE code = ?", (code,))
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_fund(row)

    def list_funds(self) -> list[Fund]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM funds ORDER BY code")
        return [_row_to_fund(r) for r in cur.fetchall()]

    def delete_fund(self, code: str) -> bool:
        cur = self._conn.cursor()
        cur.execute("DELETE FROM funds WHERE code = ?", (code,))
        self._conn.commit()
        return cur.rowcount > 0

    # ─── transactions ───

    def insert_transaction(self, tx: Transaction) -> int:
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO transactions (
                fund_code, side, date, shares, price, fee,
                strategy, tags, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tx.fund_code,
                tx.side.value,
                tx.date.isoformat(),
                str(tx.shares),
                str(tx.price),
                str(tx.fee),
                tx.strategy,
                json.dumps(tx.tags, ensure_ascii=False) if tx.tags else None,
                tx.note,
                (tx.created_at or datetime.now()).isoformat(),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def get_transaction(self, tx_id: int) -> Transaction | None:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_tx(row)

    def list_transactions(
        self,
        fund_code: str | None = None,
        tag: str | None = None,
        strategy: str | None = None,
    ) -> list[Transaction]:
        cur = self._conn.cursor()
        sql = "SELECT * FROM transactions WHERE 1=1"
        params: list[Any] = []
        if fund_code:
            sql += " AND fund_code = ?"
            params.append(fund_code)
        if tag:
            sql += " AND tags LIKE ?"
            params.append(f'%"{tag}"%')
        if strategy:
            sql += " AND strategy = ?"
            params.append(strategy)
        sql += " ORDER BY date DESC, id DESC"
        cur.execute(sql, params)
        return [_row_to_tx(r) for r in cur.fetchall()]

    # ─── snapshots ───

    def upsert_snapshot(self, snap: WeeklySnapshot) -> int:
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO weekly_snapshots (
                week_end_date, total_value, week_return,
                cumulative_return, holdings_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(week_end_date) DO UPDATE SET
                total_value=excluded.total_value,
                week_return=excluded.week_return,
                cumulative_return=excluded.cumulative_return,
                holdings_json=excluded.holdings_json,
                created_at=excluded.created_at
            """,
            (
                snap.week_end_date.isoformat(),
                str(snap.total_value),
                str(snap.week_return),
                str(snap.cumulative_return),
                snap.holdings_json,
                (snap.created_at or datetime.now()).isoformat(),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def get_snapshot_by_date(self, week_end_date: date) -> WeeklySnapshot | None:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM weekly_snapshots WHERE week_end_date = ?",
            (week_end_date.isoformat(),),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_snap(row)

    def get_latest_snapshot(self) -> WeeklySnapshot | None:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM weekly_snapshots ORDER BY week_end_date DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_snap(row)

    def get_latest_snapshot_before(self, before: date) -> WeeklySnapshot | None:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM weekly_snapshots WHERE week_end_date < ? "
            "ORDER BY week_end_date DESC LIMIT 1",
            (before.isoformat(),),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_snap(row)

    def get_first_snapshot_with_value(self) -> WeeklySnapshot | None:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM weekly_snapshots WHERE CAST(total_value AS REAL) > 0 "
            "ORDER BY week_end_date ASC LIMIT 1"
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_snap(row)

    def list_snapshots(self) -> list[WeeklySnapshot]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM weekly_snapshots ORDER BY week_end_date ASC")
        return [_row_to_snap(r) for r in cur.fetchall()]

    # ─── valuation_indicators（spec 098）───

    def upsert_valuation_indicator(self, ind: ValuationIndicator) -> int:
        """插入或更新单条估值指标（按 record_date + indicator_code 去重）。

        同一天同一指标拉多次时只保留最新一次（ON CONFLICT 覆盖 value/source）。
        返回 rowid。
        """
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO valuation_indicators (
                record_date, indicator_code, value, source
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(record_date, indicator_code) DO UPDATE SET
                value=excluded.value,
                source=excluded.source
            """,
            (
                ind.record_date.isoformat(),
                ind.indicator_code.value,
                str(ind.value),
                ind.source,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def get_valuation_indicator(
        self,
        record_date: date,
        code: ValuationIndicatorCode,
    ) -> ValuationIndicator | None:
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT * FROM valuation_indicators
            WHERE record_date = ? AND indicator_code = ?
            """,
            (record_date.isoformat(), code.value),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_valuation_indicator(row)

    def list_valuation_indicators_for_date(
        self,
        record_date: date,
    ) -> list[ValuationIndicator]:
        """取某一天的全部估值指标（最多 4 条）。

        publish 时用：检查今天 4 个指标是否齐全，不全就调 valuation update。
        """
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT * FROM valuation_indicators
            WHERE record_date = ?
            ORDER BY indicator_code
            """,
            (record_date.isoformat(),),
        )
        return [_row_to_valuation_indicator(r) for r in cur.fetchall()]

    def list_latest_valuation_indicators(self) -> list[ValuationIndicator]:
        """取数据库里最新的（不同日期里 record_date 最大）那一批指标。

        gap valuation show 用：展示"现在最新一天的数据"。
        """
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT * FROM valuation_indicators
            WHERE record_date = (
                SELECT MAX(record_date) FROM valuation_indicators
            )
            ORDER BY indicator_code
            """
        )
        return [_row_to_valuation_indicator(r) for r in cur.fetchall()]


# ─── helpers ───


def _row_to_fund(row: sqlite3.Row) -> Fund:
    from global_allocation.models import AssetClass, Currency, DataSource

    return Fund(
        code=row["code"],
        name=row["name"],
        asset_class=AssetClass(row["asset_class"]),
        data_source=DataSource(row["data_source"]),
        currency=Currency(row["currency"]),
    )


def _row_to_tx(row: sqlite3.Row) -> Transaction:
    from global_allocation.portfolio.models import TransactionSide

    tags_raw = row["tags"]
    tags: list[str] = json.loads(tags_raw) if tags_raw else []
    return Transaction(
        id=row["id"],
        fund_code=row["fund_code"],
        side=TransactionSide(row["side"]),
        date=date.fromisoformat(row["date"]),
        shares=Decimal(row["shares"]),
        price=Decimal(row["price"]),
        fee=Decimal(row["fee"]),
        strategy=row["strategy"],
        tags=tags,
        note=row["note"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_snap(row: sqlite3.Row) -> WeeklySnapshot:
    return WeeklySnapshot(
        id=row["id"],
        week_end_date=date.fromisoformat(row["week_end_date"]),
        total_value=Decimal(row["total_value"]),
        week_return=Decimal(row["week_return"]),
        cumulative_return=Decimal(row["cumulative_return"]),
        holdings_json=row["holdings_json"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_valuation_indicator(row: sqlite3.Row) -> ValuationIndicator:
    return ValuationIndicator(
        record_date=date.fromisoformat(row["record_date"]),
        indicator_code=ValuationIndicatorCode(row["indicator_code"]),
        value=Decimal(row["value"]),
        source=row["source"],
    )


__all__ = ["PortfolioDB"]
