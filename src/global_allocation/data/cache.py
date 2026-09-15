"""SQLite 历史数据缓存层。

参照 specs/040-data-fetch.md。

存储每个 (symbol, source, date) 的 OHLCV 行；
提供 put / get / clear / list_symbols 操作。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd


# ────────────────────────────────────────────────────────────────────
# Schema
# ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ohlcv_cache (
    symbol     TEXT NOT NULL,
    source     TEXT NOT NULL,
    date       DATE NOT NULL,
    open       REAL NOT NULL,
    high       REAL NOT NULL,
    low        REAL NOT NULL,
    close      REAL NOT NULL,
    adj_close  REAL NOT NULL,
    volume     INTEGER NOT NULL,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, source, date)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_date
    ON ohlcv_cache (symbol, date);
"""


class Cache:
    """本地 SQLite 缓存层。

    按 (symbol, source) 维度存储 OHLCV 日线数据。
    """

    def __init__(self, db_path: Path | str) -> None:
        """打开/创建缓存文件并确保 schema 存在。

        Args:
            db_path: SQLite 数据库文件路径。父目录不存在会自动创建。
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # 第一次连接时建表
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @property
    def db_path(self) -> Path:
        """返回缓存数据库文件路径。"""
        return self._db_path

    # ─── connection helper ───

    def _connect(self) -> sqlite3.Connection:
        """获取一个新的连接。调用方负责 close（用 with 块）。"""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ─── write ───

    def put(self, symbol: str, source: str, df: pd.DataFrame) -> None:
        """把 DataFrame 写入缓存（覆盖该 symbol+source 的已有数据）。

        Args:
            symbol: ticker（如 'AAPL'、'510300.SH'）
            source: 数据源名（'yfinance' / 'akshare'）
            df: DataFrame，index=DatetimeIndex，columns 至少含
                ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume']
        """
        required = {"Open", "High", "Low", "Close", "Adj Close", "Volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing columns: {missing}")

        with self._connect() as conn:
            # 先清旧数据（按 symbol+source）
            conn.execute(
                "DELETE FROM ohlcv_cache WHERE symbol = ? AND source = ?",
                (symbol, source),
            )
            # 插新数据
            rows: list[tuple[Any, ...]] = []
            for dt, row in df.iterrows():
                ts: pd.Timestamp = pd.Timestamp(dt)  # type: ignore[arg-type]
                rows.append(
                    (
                        symbol,
                        source,
                        ts.date().isoformat(),
                        float(row["Open"]),
                        float(row["High"]),
                        float(row["Low"]),
                        float(row["Close"]),
                        float(row["Adj Close"]),
                        int(row["Volume"]),
                    )
                )
            conn.executemany(
                """
                INSERT INTO ohlcv_cache
                  (symbol, source, date, open, high, low, close, adj_close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    # ─── read ───

    def get(self, symbol: str, source: str) -> pd.DataFrame | None:
        """从缓存读取某个 (symbol, source) 的全部 OHLCV 行。

        Returns:
            DataFrame（index=DatetimeIndex, columns=OHLCV）；无数据返回 None。
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT date, open, high, low, close, adj_close, volume
                FROM ohlcv_cache
                WHERE symbol = ? AND source = ?
                ORDER BY date
                """,
                (symbol, source),
            ).fetchall()

        if not rows:
            return None

        df = pd.DataFrame(
            [
                {
                    "Open": r["open"],
                    "High": r["high"],
                    "Low": r["low"],
                    "Close": r["close"],
                    "Adj Close": r["adj_close"],
                    "Volume": r["volume"],
                }
                for r in rows
            ],
            index=pd.to_datetime([r["date"] for r in rows]),
        )
        return df

    # ─── clear ───

    def clear(self, symbol: str, source: str) -> None:
        """清掉某个 (symbol, source) 的全部缓存。"""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM ohlcv_cache WHERE symbol = ? AND source = ?",
                (symbol, source),
            )
            conn.commit()

    def clear_all(self) -> None:
        """清掉整个缓存文件的内容。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM ohlcv_cache")
            conn.commit()

    # ─── list ───

    def list_symbols(self) -> list[tuple[str, str]]:
        """列出缓存里所有 (symbol, source) 对。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol, source FROM ohlcv_cache"
            ).fetchall()
        return [(r["symbol"], r["source"]) for r in rows]


__all__ = ["Cache"]
