"""fetch_overseas_valuation 测试（liubo 2026-10-24 拍板海外指数自动拉数）。"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from global_allocation.portfolio.fetch_overseas_valuation import (
    DATA_DIR,
    FETCH_YEARS,
    HISTORY_YEARS,
    OVERSEAS_PROXIES,
    OverseasValuation,
    _compute_dy_series_percentile,
    _percentile_rank,
    load_latest_snapshot,
    load_snapshot,
    save_snapshot,
    to_json,
)


# ─── 分位算法 ──────────────────────────────────────────


class TestPercentileRank:
    def test_basic_50_percentile(self) -> None:
        assert _percentile_rank([1.0, 2.0, 3.0, 4.0], 2.0) == 0.5

    def test_min_value(self) -> None:
        assert _percentile_rank([1.0, 2.0, 3.0], 1.0) == pytest.approx(1 / 3)

    def test_max_value(self) -> None:
        assert _percentile_rank([1.0, 2.0, 3.0], 3.0) == 1.0

    def test_empty(self) -> None:
        import math

        assert math.isnan(_percentile_rank([], 1.0))


class TestDySeriesPercentile:
    """月度 DY-TTM 序列构造（跟 spec REITs-DY 同一套算法）。"""

    @staticmethod
    def _frame(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
        """rows = [(日期, close, dividend), ...] → yfinance 风格的 DataFrame。"""
        idx = pd.to_datetime([r[0] for r in rows])
        return pd.DataFrame(
            {"Close": [r[1] for r in rows], "Dividends": [r[2] for r in rows]},
            index=idx,
        )

    def test_trailing_12m_dividend_sum(self) -> None:
        """DY = 过去 12 个月分红合计 / 当月月末收盘。"""
        rows = []
        # 24 个月，每月 1 号收盘 100，每季度末发 1.0 分红
        start = pd.Timestamp("2020-01-01")
        for i in range(24):
            ts = start + pd.DateOffset(months=i)
            div = 1.0 if i % 3 == 2 else 0.0
            rows.append((ts.strftime("%Y-%m-%d"), 100.0, div))
        hist = self._frame(rows)

        value, dy_pct, val_pct, data_date = _compute_dy_series_percentile(hist)
        # 2020-03 起才有 trailing 12M 分红（前面 12 个月窗口不完整 → 分红合计 0 → 被丢掉）
        # 从 2021-03 开始每月都有 4.0 的 trailing 分红 → DY = 4.0 / 100 = 4%
        assert value == Decimal("4.0")
        # 所有月份 DY 一样 → 分位 100% → 估值分位 0%
        assert dy_pct == Decimal("1.0000")
        assert val_pct == Decimal("0.0000")
        assert data_date is not None

    def test_zero_dividend_months_are_dropped(self) -> None:
        """trailing 分红合计为 0 的月份必须被丢掉（窗口没拉全，会把分位系统性拉低）。"""
        rows = []
        start = pd.Timestamp("2016-01-01")
        for i in range(24):
            ts = start + pd.DateOffset(months=i)
            rows.append((ts.strftime("%Y-%m-%d"), 100.0, 0.0))
        # 只在最后 12 个月发分红
        for i in range(12, 24):
            ts = start + pd.DateOffset(months=i)
            rows.append((ts.strftime("%Y-%m-%d"), 100.0, 0.0))
        hist = self._frame(rows)

        value, _, _, _ = _compute_dy_series_percentile(hist)
        # 全 0 → 没有有效 DY 序列 → 全 None
        assert value is None

    def test_flip_direction_low_valuation_is_cheap(self) -> None:
        """估值分位 = 1 - DY 分位（低 = 便宜，跟 PE/PS 规则一致）。"""
        rows = []
        start = pd.Timestamp("2016-01-01")
        # 价格一路上涨 → 同样分红下 DY 递减 → 估值分位应偏高（贵）
        for i in range(24):
            ts = start + pd.DateOffset(months=i)
            price = 100.0 + i * 5
            rows.append((ts.strftime("%Y-%m-%d"), price, 0.0))
        for i in range(12, 24):
            ts = start + pd.DateOffset(months=i)
            price = 100.0 + i * 5
            rows.append((ts.strftime("%Y-%m-%d"), price, 12.0))
        hist = self._frame(rows)

        _, dy_pct, val_pct, _ = _compute_dy_series_percentile(hist)
        assert dy_pct + val_pct == Decimal("1.0000")

    def test_empty_history(self) -> None:
        empty = pd.DataFrame({"Close": [], "Dividends": []})
        assert _compute_dy_series_percentile(empty) == (None, None, None, None)


# ─── 配置 ──────────────────────────────────────────


class TestConfig:
    def test_proxy_symbols(self) -> None:
        """4 个海外指数都有代理 ETF（liubo 2026-10-24 点名：DAX / 新兴市场 / 日经 / 法国）。"""
        assert set(OVERSEAS_PROXIES.keys()) == {".GDAXI", ".N225", ".FCHI", ".MSCI_EM"}
        assert OVERSEAS_PROXIES[".GDAXI"][0] == "DAX"
        assert OVERSEAS_PROXIES[".N225"][0] == "EWJ"
        assert OVERSEAS_PROXIES[".FCHI"][0] == "CAC.PA"
        assert OVERSEAS_PROXIES[".MSCI_EM"][0] == "EEM"

    def test_history_years_is_10(self) -> None:
        """liubo 2026-10-24 拍板「用十年窗口」。"""
        assert HISTORY_YEARS == 10

    def test_fetch_years_leaves_room_for_trailing_window(self) -> None:
        """多拉 2 年，保证序列首月的 trailing-12M 分红窗口完整。"""
        assert FETCH_YEARS == HISTORY_YEARS + 2

    def test_data_dir_default(self) -> None:
        assert DATA_DIR.name == "overseas_valuation"


# ─── 数据类 ──────────────────────────────────────────


class TestOverseasValuation:
    def test_success_construction(self) -> None:
        s = OverseasValuation(
            index_code=".GDAXI",
            metric="dy",
            value=Decimal("2.16"),
            dy_percentile_10y=Decimal("0.38"),
            valuation_percentile_10y=Decimal("0.62"),
            data_date=date(2026, 10, 24),
            source="yfinance DAX",
        )
        assert s.index_code == ".GDAXI"
        assert s.metric == "dy"
        assert s.error == ""

    def test_frozen(self) -> None:
        s = OverseasValuation(
            index_code=".GDAXI",
            metric="dy",
            value=Decimal("2.16"),
            dy_percentile_10y=Decimal("0.38"),
            valuation_percentile_10y=Decimal("0.62"),
            data_date=date(2026, 10, 24),
            source="yfinance DAX",
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            s.value = Decimal("4.0")  # type: ignore[misc]


# ─── 序列化 ──────────────────────────────────────────


class TestSerialization:
    @pytest.fixture
    def sample_snapshots(self) -> dict[str, OverseasValuation]:
        return {
            ".GDAXI": OverseasValuation(
                index_code=".GDAXI",
                metric="dy",
                value=Decimal("2.16"),
                dy_percentile_10y=Decimal("0.38"),
                valuation_percentile_10y=Decimal("0.62"),
                data_date=date(2026, 10, 24),
                source="yfinance DAX",
            ),
            ".N225": OverseasValuation(
                index_code=".N225",
                metric="dy",
                value=Decimal("3.79"),
                dy_percentile_10y=Decimal("0.94"),
                valuation_percentile_10y=Decimal("0.06"),
                data_date=date(2026, 10, 24),
                source="yfinance EWJ",
            ),
        }

    def test_to_json_structure(self, sample_snapshots: dict[str, OverseasValuation]) -> None:
        out = to_json(sample_snapshots)
        assert "fetched_at" in out
        assert out["data_date"] == "2026-10-24"
        assert set(out["indices"].keys()) == {".GDAXI", ".N225"}
        assert out["indices"][".GDAXI"]["value"] == "2.16"
        assert out["indices"][".GDAXI"]["dy_percentile_10y"] == "0.38"
        assert out["indices"][".GDAXI"]["valuation_percentile_10y"] == "0.62"

    def test_save_and_load_round_trip(
        self,
        sample_snapshots: dict[str, OverseasValuation],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "global_allocation.portfolio.fetch_overseas_valuation.DATA_DIR", tmp_path
        )
        path = save_snapshot(sample_snapshots)
        assert path.exists()
        assert path.name == date.today().isoformat() + ".json"

        loaded = load_snapshot(path)
        assert set(loaded.keys()) == {".GDAXI", ".N225"}
        for code, original in sample_snapshots.items():
            assert loaded[code].index_code == original.index_code
            assert loaded[code].value == original.value
            assert loaded[code].dy_percentile_10y == original.dy_percentile_10y
            assert loaded[code].valuation_percentile_10y == original.valuation_percentile_10y
            assert loaded[code].data_date == original.data_date

    def test_load_latest_snapshot_no_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "global_allocation.portfolio.fetch_overseas_valuation.DATA_DIR",
            tmp_path / "not_exist",
        )
        assert load_latest_snapshot() is None
