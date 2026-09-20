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
    FundValuation,
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

            -- spec 098 第二十七轮：每只 A 股基金的估值快照（按基金，不是按指数）
            -- 一个 fund_code 可以有多条记录（不同日期覆盖式 upsert）
            CREATE TABLE IF NOT EXISTS fund_valuations (
                id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                record_date                 TEXT NOT NULL,                  -- YYYY-MM-DD
                fund_code                   TEXT NOT NULL,                  -- 基金代码（如 014532）
                index_code                  TEXT NOT NULL,                  -- 跟踪的指数代码（如 930050 中证A50）
                pe_ttm                      TEXT,                          -- PE-TTM 实数（如 15.7851）；空 = 数据缺失
                pe_percentile               TEXT,                          -- 10 年分位（fraction：0.4638 = 46.38%）
                dividend_yield              TEXT,                          -- 股息率（fraction：0.0251 = 2.51%）
                pe_percentile_dy_weighted   TEXT,                          -- 股息率加权 PE 分位（红利低波手动填，来自银行螺丝钉）
                roe_latest                  TEXT,                          -- 最新报告期 ROE（fraction：0.0834 = 8.34%）
                roe_year_ago                TEXT,                          -- 去年同期 ROE（同口径）
                source                      TEXT NOT NULL,                 -- "lixinger_csv:..." 来源标识
                UNIQUE (record_date, fund_code)
            );

            CREATE INDEX IF NOT EXISTS idx_fund_val_fund
                ON fund_valuations(fund_code);
            CREATE INDEX IF NOT EXISTS idx_fund_val_date
                ON fund_valuations(record_date);
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

    # ─── fund_valuations（spec 098 第二十七轮）───

    def upsert_fund_valuation(self, fv: FundValuation) -> int:
        """插入或更新单只基金的估值快照（按 record_date + fund_code 去重）。

        同一天同一基金多次导入时只保留最新一次（ON CONFLICT 覆盖）。
        返回 rowid。
        """
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO fund_valuations (
                record_date, fund_code, index_code,
                pe_ttm, pe_percentile, dividend_yield,
                pe_percentile_dy_weighted, roe_latest, roe_year_ago,
                source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_date, fund_code) DO UPDATE SET
                index_code=excluded.index_code,
                pe_ttm=excluded.pe_ttm,
                pe_percentile=excluded.pe_percentile,
                dividend_yield=excluded.dividend_yield,
                pe_percentile_dy_weighted=excluded.pe_percentile_dy_weighted,
                roe_latest=excluded.roe_latest,
                roe_year_ago=excluded.roe_year_ago,
                source=excluded.source
            """,
            (
                fv.record_date.isoformat(),
                fv.fund_code,
                fv.index_code,
                str(fv.pe_ttm) if fv.pe_ttm is not None else None,
                str(fv.pe_percentile) if fv.pe_percentile is not None else None,
                str(fv.dividend_yield) if fv.dividend_yield is not None else None,
                str(fv.pe_percentile_dy_weighted) if fv.pe_percentile_dy_weighted is not None else None,
                str(fv.roe_latest) if fv.roe_latest is not None else None,
                str(fv.roe_year_ago) if fv.roe_year_ago is not None else None,
                fv.source,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def get_fund_valuation(
        self,
        record_date: date,
        fund_code: str,
    ) -> FundValuation | None:
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT * FROM fund_valuations
            WHERE record_date = ? AND fund_code = ?
            """,
            (record_date.isoformat(), fund_code),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_fund_valuation(row)

    def list_fund_valuations_for_date(
        self,
        record_date: date,
    ) -> list[FundValuation]:
        """取某一天的全部基金估值（卡片展示用：用户持有的所有 A 股基金）。"""
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT * FROM fund_valuations
            WHERE record_date = ?
            ORDER BY fund_code
            """,
            (record_date.isoformat(),),
        )
        return [_row_to_fund_valuation(r) for r in cur.fetchall()]

    def list_latest_fund_valuations_for_codes(
        self,
        fund_codes: list[str],
    ) -> dict[str, FundValuation]:
        """取每只基金最新一天的估值（卡片用：按 holdings 里的 fund_code 找最新数据）。

        用 LEFT JOIN + 找每只 fund_code 的 MAX(record_date) — 比循环 N 次 query 快。
        返回 fund_code → FundValuation 映射（缺失的 fund 不在结果里）。

        实现：先用子查询挑出每只基金的最新日期，再 LEFT JOIN fund_valuations。
        """
        if not fund_codes:
            return {}
        cur = self._conn.cursor()
        placeholders = ",".join("?" for _ in fund_codes)
        cur.execute(
            f"""
            SELECT fv.* FROM fund_valuations fv
            INNER JOIN (
                SELECT fund_code, MAX(record_date) AS max_date
                FROM fund_valuations
                WHERE fund_code IN ({placeholders})
                GROUP BY fund_code
            ) latest
                ON fv.fund_code = latest.fund_code
                AND fv.record_date = latest.max_date
            WHERE fv.fund_code IN ({placeholders})
            """,
            (*fund_codes, *fund_codes),
        )
        return {r["fund_code"]: _row_to_fund_valuation(r) for r in cur.fetchall()}


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


def _row_to_fund_valuation(row: sqlite3.Row) -> FundValuation:
    """SQLite row → FundValuation。空值（None / 空字符串）→ Decimal 字段 None。"""
    def _d(col: str) -> Decimal | None:
        v = row[col]
        return Decimal(v) if v else None

    return FundValuation(
        record_date=date.fromisoformat(row["record_date"]),
        fund_code=row["fund_code"],
        index_code=row["index_code"],
        pe_ttm=_d("pe_ttm"),
        pe_percentile=_d("pe_percentile"),
        dividend_yield=_d("dividend_yield"),
        pe_percentile_dy_weighted=_d("pe_percentile_dy_weighted"),
        roe_latest=_d("roe_latest"),
        roe_year_ago=_d("roe_year_ago"),
        source=row["source"],
    )


__all__ = ["PortfolioDB"]
