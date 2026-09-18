"""测试 src/global_allocation/portfolio/db.py。"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

import pytest

from global_allocation.models import AssetClass, Currency, DataSource
from global_allocation.portfolio.db import PortfolioDB
from global_allocation.portfolio.models import (
    Fund,
    Transaction,
    TransactionSide,
    ValuationIndicator,
    ValuationIndicatorCode,
    WeeklySnapshot,
)


@pytest.fixture
def db(tmp_path) -> PortfolioDB:
    return PortfolioDB(path=tmp_path / "test_portfolio.db")


class TestSchema:
    def test_creates_tables(self, db: PortfolioDB) -> None:
        cur = db._conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cur.fetchall()}
        assert "funds" in tables
        assert "transactions" in tables
        assert "weekly_snapshots" in tables
        # spec 098：估值指标表
        assert "valuation_indicators" in tables

    def test_idempotent_init(self, tmp_path) -> None:
        path = tmp_path / "test.db"
        PortfolioDB(path=path)  # 第一次打开 + 建表
        db2 = PortfolioDB(path=path)  # 二次打开
        cur = db2._conn.cursor()
        cur.execute("SELECT count(*) FROM funds")
        assert cur.fetchone()[0] == 0


class TestFunds:
    def test_upsert_new(self, db: PortfolioDB) -> None:
        f = Fund(code="163406", name="兴全合润", asset_class=AssetClass.MIXED)
        db.upsert_fund(f)
        loaded = db.get_fund("163406")
        assert loaded is not None
        assert loaded.code == "163406"
        assert loaded.name == "兴全合润"
        assert loaded.asset_class == AssetClass.MIXED
        assert loaded.currency == Currency.CNY
        assert loaded.data_source == DataSource.AKSHARE

    def test_upsert_update_name(self, db: PortfolioDB) -> None:
        db.upsert_fund(Fund(code="163406", name="旧名", asset_class=AssetClass.MIXED))
        db.upsert_fund(Fund(code="163406", name="新名", asset_class=AssetClass.MIXED))
        loaded = db.get_fund("163406")
        assert loaded is not None
        assert loaded.name == "新名"

    def test_get_fund_missing(self, db: PortfolioDB) -> None:
        assert db.get_fund("nope") is None

    def test_list_funds(self, db: PortfolioDB) -> None:
        db.upsert_fund(Fund(code="163406", name="A", asset_class=AssetClass.MIXED))
        db.upsert_fund(Fund(code="510300", name="B", asset_class=AssetClass.EQUITY))
        funds = db.list_funds()
        assert len(funds) == 2
        codes = {f.code for f in funds}
        assert codes == {"163406", "510300"}

    def test_delete_fund(self, db: PortfolioDB) -> None:
        db.upsert_fund(Fund(code="163406", name="A", asset_class=AssetClass.MIXED))
        assert db.delete_fund("163406") is True
        assert db.get_fund("163406") is None
        assert db.delete_fund("nope") is False


class TestTransactions:
    def _tx(self) -> Transaction:
        return Transaction(
            fund_code="163406",
            side=TransactionSide.BUY,
            date=date(2026, 9, 10),
            shares=Decimal("1000"),
            price=Decimal("2.350"),
            fee=Decimal("1.20"),
            strategy="定投",
            tags=["dca"],
            created_at=datetime(2026, 9, 10, 14, 30),
        )

    def test_insert_and_get(self, db: PortfolioDB) -> None:
        db.upsert_fund(Fund(code="163406", name="A", asset_class=AssetClass.MIXED))
        tx_id = db.insert_transaction(self._tx())
        assert tx_id > 0
        loaded = db.get_transaction(tx_id)
        assert loaded is not None
        assert loaded.fund_code == "163406"
        assert loaded.shares == Decimal("1000")
        assert loaded.price == Decimal("2.350")
        assert loaded.fee == Decimal("1.20")
        assert loaded.strategy == "定投"
        assert loaded.tags == ["dca"]
        assert loaded.side == TransactionSide.BUY

    def test_list_transactions_all(self, db: PortfolioDB) -> None:
        db.upsert_fund(Fund(code="163406", name="A", asset_class=AssetClass.MIXED))
        db.upsert_fund(Fund(code="510300", name="B", asset_class=AssetClass.EQUITY))
        db.insert_transaction(self._tx())
        db.insert_transaction(
            Transaction(
                fund_code="510300",
                side=TransactionSide.BUY,
                date=date(2026, 9, 11),
                shares=Decimal("500"),
                price=Decimal("3.85"),
                created_at=datetime(2026, 9, 11, 10),
            )
        )
        txs = db.list_transactions()
        assert len(txs) == 2

    def test_list_filter_by_fund(self, db: PortfolioDB) -> None:
        db.upsert_fund(Fund(code="163406", name="A", asset_class=AssetClass.MIXED))
        db.upsert_fund(Fund(code="510300", name="B", asset_class=AssetClass.EQUITY))
        db.insert_transaction(self._tx())
        db.insert_transaction(
            Transaction(
                fund_code="510300",
                side=TransactionSide.BUY,
                date=date(2026, 9, 11),
                shares=Decimal("500"),
                price=Decimal("3.85"),
                created_at=datetime(2026, 9, 11, 10),
            )
        )
        txs = db.list_transactions(fund_code="163406")
        assert len(txs) == 1
        assert txs[0].fund_code == "163406"

    def test_list_filter_by_tag(self, db: PortfolioDB) -> None:
        db.upsert_fund(Fund(code="163406", name="A", asset_class=AssetClass.MIXED))
        db.insert_transaction(self._tx())  # tags=["dca"]
        db.insert_transaction(
            Transaction(
                fund_code="163406",
                side=TransactionSide.SELL,
                date=date(2026, 9, 12),
                shares=Decimal("200"),
                price=Decimal("2.45"),
                tags=["profit-take"],
                created_at=datetime(2026, 9, 12, 11),
            )
        )
        txs = db.list_transactions(tag="dca")
        assert len(txs) == 1
        assert txs[0].side == TransactionSide.BUY
        assert txs[0].tags == ["dca"]

    def test_list_filter_by_strategy(self, db: PortfolioDB) -> None:
        db.upsert_fund(Fund(code="163406", name="A", asset_class=AssetClass.MIXED))
        db.insert_transaction(self._tx())  # strategy="定投"
        db.insert_transaction(
            Transaction(
                fund_code="163406",
                side=TransactionSide.BUY,
                date=date(2026, 9, 15),
                shares=Decimal("500"),
                price=Decimal("2.40"),
                strategy="抄底",
                created_at=datetime(2026, 9, 15, 9),
            )
        )
        txs = db.list_transactions(strategy="抄底")
        assert len(txs) == 1
        assert txs[0].strategy == "抄底"

    def test_ordered_by_date_desc(self, db: PortfolioDB) -> None:
        db.upsert_fund(Fund(code="163406", name="A", asset_class=AssetClass.MIXED))
        db.insert_transaction(self._tx())  # 2026-09-10
        db.insert_transaction(
            Transaction(
                fund_code="163406",
                side=TransactionSide.BUY,
                date=date(2026, 9, 20),
                shares=Decimal("500"),
                price=Decimal("2.40"),
                created_at=datetime(2026, 9, 20, 9),
            )
        )
        txs = db.list_transactions()
        assert txs[0].date == date(2026, 9, 20)
        assert txs[1].date == date(2026, 9, 10)


class TestSnapshots:
    def _snap(self, week_end: date = date(2026, 9, 11)) -> WeeklySnapshot:
        return WeeklySnapshot(
            week_end_date=week_end,
            total_value=Decimal("5000"),
            week_return=Decimal("0.012"),
            cumulative_return=Decimal("0.085"),
            holdings_json=json.dumps([{"fund_code": "163406", "weight": "1.0"}]),
            created_at=datetime(2026, 9, 11, 17),
        )

    def test_upsert_new(self, db: PortfolioDB) -> None:
        sid = db.upsert_snapshot(self._snap())
        assert sid > 0
        loaded = db.get_snapshot_by_date(date(2026, 9, 11))
        assert loaded is not None
        assert loaded.total_value == Decimal("5000")
        assert loaded.week_return == Decimal("0.012")

    def test_upsert_idempotent(self, db: PortfolioDB) -> None:
        db.upsert_snapshot(self._snap())
        # 第二次跑同一周，不抛错，覆盖
        db.upsert_snapshot(
            self._snap().model_copy(update={"total_value": Decimal("5500")})
        )
        loaded = db.get_snapshot_by_date(date(2026, 9, 11))
        assert loaded is not None
        assert loaded.total_value == Decimal("5500")

    def test_list_snapshots_ordered(self, db: PortfolioDB) -> None:
        db.upsert_snapshot(self._snap(date(2026, 9, 4)))
        db.upsert_snapshot(self._snap(date(2026, 9, 11)))
        db.upsert_snapshot(self._snap(date(2026, 9, 18)))
        snaps = db.list_snapshots()
        assert len(snaps) == 3
        assert [s.week_end_date for s in snaps] == [date(2026, 9, 4), date(2026, 9, 11), date(2026, 9, 18)]

    def test_latest_snapshot(self, db: PortfolioDB) -> None:
        db.upsert_snapshot(self._snap(date(2026, 9, 4)))
        db.upsert_snapshot(self._snap(date(2026, 9, 11)))
        latest = db.get_latest_snapshot()
        assert latest is not None
        assert latest.week_end_date == date(2026, 9, 11)

    def test_latest_before(self, db: PortfolioDB) -> None:
        db.upsert_snapshot(self._snap(date(2026, 9, 4)))
        db.upsert_snapshot(self._snap(date(2026, 9, 11)))
        before = db.get_latest_snapshot_before(date(2026, 9, 11))
        assert before is not None
        assert before.week_end_date == date(2026, 9, 4)

    def test_first_with_value(self, db: PortfolioDB) -> None:
        db.upsert_snapshot(self._snap(date(2026, 9, 4)))
        db.upsert_snapshot(self._snap(date(2026, 9, 11)))
        first = db.get_first_snapshot_with_value()
        assert first is not None
        assert first.week_end_date == date(2026, 9, 4)

    def test_no_snapshots(self, db: PortfolioDB) -> None:
        assert db.get_latest_snapshot() is None
        assert db.get_latest_snapshot_before(date(2026, 9, 11)) is None
        assert db.get_first_snapshot_with_value() is None


class TestValuationIndicators:
    """spec 098：估值指标快照表。

    关键设计点：
    - UNIQUE (record_date, indicator_code) 保证同一天同一指标只有一条
    - upsert 模式：value/source 可更新（拉新数据时覆盖旧值）
    - Decimal 全程精度无损（text 存储）
    """

    def _ind(
        self,
        record_date: date = date(2026, 9, 19),
        code: ValuationIndicatorCode = ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
        value: Decimal = Decimal("0.052"),
        source: str = "akshare:stock_zh_index_value_dbj_b",
    ) -> ValuationIndicator:
        return ValuationIndicator(
            record_date=record_date,
            indicator_code=code,
            value=value,
            source=source,
        )

    def test_upsert_and_get(self, db: PortfolioDB) -> None:
        ind_id = db.upsert_valuation_indicator(self._ind())
        assert ind_id > 0
        loaded = db.get_valuation_indicator(
            date(2026, 9, 19),
            ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
        )
        assert loaded is not None
        assert loaded.value == Decimal("0.052")
        assert loaded.source == "akshare:stock_zh_index_value_dbj_b"
        assert loaded.indicator_code == ValuationIndicatorCode.EQUITY_RISK_PREMIUM

    def test_upsert_overwrites_same_day_same_code(
        self, db: PortfolioDB
    ) -> None:
        """同一天同一指标的二次 upsert 应该覆盖 value/source（UNIQUE 约束）。"""
        db.upsert_valuation_indicator(
            self._ind(value=Decimal("0.05"), source="old-source")
        )
        db.upsert_valuation_indicator(
            self._ind(value=Decimal("0.052"), source="new-source")
        )
        loaded = db.get_valuation_indicator(
            date(2026, 9, 19),
            ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
        )
        assert loaded is not None
        assert loaded.value == Decimal("0.052")
        assert loaded.source == "new-source"
        # 只剩一条记录
        cur = db._conn.cursor()
        cur.execute(
            "SELECT count(*) FROM valuation_indicators "
            "WHERE record_date = ? AND indicator_code = ?",
            (date(2026, 9, 19).isoformat(), "equity_risk_premium"),
        )
        assert cur.fetchone()[0] == 1

    def test_different_codes_coexist(self, db: PortfolioDB) -> None:
        """同一天 4 个不同指标 → 4 条独立记录（不互相覆盖）。"""
        for code in ValuationIndicatorCode:
            db.upsert_valuation_indicator(
                self._ind(code=code, value=Decimal("0.05"))
            )
        day_indicators = db.list_valuation_indicators_for_date(date(2026, 9, 19))
        assert len(day_indicators) == 4
        assert {i.indicator_code for i in day_indicators} == set(ValuationIndicatorCode)

    def test_different_days_coexist(self, db: PortfolioDB) -> None:
        """不同日期的同一指标 → 2 条独立记录（历史保留）。"""
        db.upsert_valuation_indicator(self._ind(record_date=date(2026, 9, 18)))
        db.upsert_valuation_indicator(self._ind(record_date=date(2026, 9, 19)))
        d18 = db.get_valuation_indicator(
            date(2026, 9, 18),
            ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
        )
        d19 = db.get_valuation_indicator(
            date(2026, 9, 19),
            ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
        )
        assert d18 is not None and d19 is not None
        assert d18.record_date == date(2026, 9, 18)
        assert d19.record_date == date(2026, 9, 19)

    def test_get_missing_returns_none(self, db: PortfolioDB) -> None:
        assert db.get_valuation_indicator(
            date(2026, 9, 19),
            ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
        ) is None

    def test_list_for_empty_date(self, db: PortfolioDB) -> None:
        """DB 里没有任何数据，list 返回空 list（不报错）。"""
        assert db.list_valuation_indicators_for_date(date(2026, 9, 19)) == []

    def test_list_latest_returns_most_recent_day(self, db: PortfolioDB) -> None:
        """最新一天的数据：3 条指标（部分指标缺失）。"""
        db.upsert_valuation_indicator(
            self._ind(
                record_date=date(2026, 9, 18),
                code=ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
            )
        )
        db.upsert_valuation_indicator(
            self._ind(
                record_date=date(2026, 9, 19),
                code=ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
                value=Decimal("0.06"),
            )
        )
        db.upsert_valuation_indicator(
            self._ind(
                record_date=date(2026, 9, 19),
                code=ValuationIndicatorCode.PE_PERCENTILE,
                value=Decimal("0.28"),
            )
        )
        latest = db.list_latest_valuation_indicators()
        # 最新一天是 2026-09-19，应该有 2 条（ERP + PE_PERCENTILE）
        assert len(latest) == 2
        assert {i.indicator_code for i in latest} == {
            ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
            ValuationIndicatorCode.PE_PERCENTILE,
        }
        # ERP 的最新值是 0.06（不是更早的 0.052）
        erp = next(
            i for i in latest
            if i.indicator_code == ValuationIndicatorCode.EQUITY_RISK_PREMIUM
        )
        assert erp.value == Decimal("0.06")

    def test_list_latest_empty_db(self, db: PortfolioDB) -> None:
        """DB 完全空的时候，list_latest 返回 []。"""
        assert db.list_latest_valuation_indicators() == []

    def test_decimal_precision_preserved(self, db: PortfolioDB) -> None:
        """Decimal 精度无损 — 高精度的小数应该完整保留（不转 float）。"""
        precise = Decimal("0.05273849382716")
        db.upsert_valuation_indicator(self._ind(value=precise))
        loaded = db.get_valuation_indicator(
            date(2026, 9, 19),
            ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
        )
        assert loaded is not None
        assert loaded.value == precise  # 不会变成 0.05273849382716001 之类的浮点近似
