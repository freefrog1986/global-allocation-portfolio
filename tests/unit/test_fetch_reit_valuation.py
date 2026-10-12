"""测试 src/global_allocation/portfolio/fetch_reit_valuation.py。

spec REITs-DY — liubo 2026-10-12 拍板。

测试范围：
- percentile_rank 工具函数
- REITValuation 数据类构造 + 字段
- to_json / save_snapshot / load_snapshot 序列化
- load_latest_snapshot 找最新文件
- 失败路径：fetch_us_reit / fetch_cn_reit 返回 error 字段非空，value/percentile 为 None
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from global_allocation.portfolio.fetch_reit_valuation import (
    DATA_DIR,
    HISTORY_YEARS,
    REITValuation,
    US_REIT_PROXY,
    CN_REIT_PROXY,
    _percentile_rank,
    load_latest_snapshot,
    load_snapshot,
    save_snapshot,
    to_json,
)


# ─── _percentile_rank ─────────────────────────────


class TestPercentileRank:
    def test_basic_50_percentile(self) -> None:
        """[1, 2, 3, 4, 5] 里 3 是 60% 分位（3 个 <= 3，含自己）。"""
        assert _percentile_rank([1, 2, 3, 4, 5], 3) == pytest.approx(0.6)

    def test_min_value(self) -> None:
        """最小值 → 0.2 分位（1/5）。"""
        assert _percentile_rank([1, 2, 3, 4, 5], 1) == pytest.approx(0.2)

    def test_max_value(self) -> None:
        """最大值 → 1.0 分位。"""
        assert _percentile_rank([1, 2, 3, 4, 5], 5) == pytest.approx(1.0)

    def test_below_min(self) -> None:
        """比最小值还小 → 0.0。"""
        assert _percentile_rank([10, 20, 30], 5) == 0.0

    def test_empty(self) -> None:
        """空列表 → NaN。"""
        import math
        assert math.isnan(_percentile_rank([], 5))

    def test_works_on_unsorted(self) -> None:
        """不要求升序。"""
        assert _percentile_rank([5, 3, 1, 4, 2], 3) == pytest.approx(0.6)


# ─── REITValuation 数据类 ─────────────────────────────


class TestREITValuation:
    def test_success_construction(self) -> None:
        s = REITValuation(
            index_code=".MSCI_US_REIT",
            metric="dy",
            value=Decimal("3.76"),
            dy_percentile_5y=Decimal("0.58"),
            valuation_percentile_5y=Decimal("0.42"),
            data_date=date(2026, 10, 9),
            source="yfinance VNQ",
        )
        assert s.index_code == ".MSCI_US_REIT"
        assert s.metric == "dy"
        assert s.value == Decimal("3.76")
        assert s.dy_percentile_5y == Decimal("0.58")
        assert s.valuation_percentile_5y == Decimal("0.42")
        assert s.data_date == date(2026, 10, 9)
        assert s.error is None

    def test_failure_construction(self) -> None:
        """失败时 value/percentile 是 None，error 非空。"""
        s = REITValuation(
            index_code="932006",
            metric="dy",
            value=None,
            dy_percentile_5y=None,
            valuation_percentile_5y=None,
            data_date=date.today(),
            source="akshare 932006 + yfinance 508000.SS",
            error="akshare 失败",
        )
        assert s.value is None
        assert s.dy_percentile_5y is None
        assert s.valuation_percentile_5y is None
        assert s.error == "akshare 失败"

    def test_frozen(self) -> None:
        """frozen=True 不允许修改字段。"""
        s = REITValuation(
            index_code=".MSCI_US_REIT",
            metric="dy",
            value=Decimal("3.76"),
            dy_percentile_5y=Decimal("0.58"),
            valuation_percentile_5y=Decimal("0.42"),
            data_date=date(2026, 10, 9),
            source="test",
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            s.value = Decimal("4.0")  # type: ignore[misc]


# ─── to_json / load_snapshot 序列化 ─────────────────────────────


class TestSerialization:
    @pytest.fixture
    def sample_snapshots(self) -> dict[str, REITValuation]:
        return {
            ".MSCI_US_REIT": REITValuation(
                index_code=".MSCI_US_REIT",
                metric="dy",
                value=Decimal("3.76"),
                dy_percentile_5y=Decimal("0.58"),
                valuation_percentile_5y=Decimal("0.42"),
                data_date=date(2026, 10, 9),
                source="yfinance VNQ",
            ),
            "932006": REITValuation(
                index_code="932006",
                metric="dy",
                value=Decimal("6.59"),
                dy_percentile_5y=Decimal("0.52"),
                valuation_percentile_5y=Decimal("0.48"),
                data_date=date(2026, 9, 16),
                source="akshare + yfinance",
            ),
        }

    def test_to_json_structure(self, sample_snapshots: dict[str, REITValuation]) -> None:
        out = to_json(sample_snapshots)
        assert "fetched_at" in out
        # data_date 取所有指数里最晚的（932006 是 9-16，US REIT 是 10-9 → 10-9）
        assert out["data_date"] == "2026-10-09"
        assert set(out["indices"].keys()) == {".MSCI_US_REIT", "932006"}
        # value / percentile 序列化成 str（保持 Decimal 精度）
        assert out["indices"][".MSCI_US_REIT"]["value"] == "3.76"
        assert out["indices"][".MSCI_US_REIT"]["dy_percentile_5y"] == "0.58"
        assert out["indices"][".MSCI_US_REIT"]["valuation_percentile_5y"] == "0.42"

    def test_save_and_load_round_trip(
        self,
        sample_snapshots: dict[str, REITValuation],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """save_snapshot → load_snapshot 数据完整还原。"""
        # 改 DATA_DIR 到 tmp_path
        monkeypatch.setattr("global_allocation.portfolio.fetch_reit_valuation.DATA_DIR", tmp_path)

        path = save_snapshot(sample_snapshots)
        assert path.exists()
        assert path.name == date.today().isoformat() + ".json"

        # 读回
        loaded = load_snapshot(path)
        assert set(loaded.keys()) == {".MSCI_US_REIT", "932006"}
        for code, original in sample_snapshots.items():
            assert loaded[code].index_code == original.index_code
            assert loaded[code].value == original.value
            assert loaded[code].dy_percentile_5y == original.dy_percentile_5y
            assert loaded[code].valuation_percentile_5y == original.valuation_percentile_5y
            assert loaded[code].data_date == original.data_date
            assert loaded[code].source == original.source
            assert loaded[code].error == original.error

    def test_load_latest_snapshot_finds_newest(
        self,
        sample_snapshots: dict[str, REITValuation],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """load_latest_snapshot 找 glob 排序后最新的文件。"""
        monkeypatch.setattr("global_allocation.portfolio.fetch_reit_valuation.DATA_DIR", tmp_path)

        # 写 3 个不同日期的 JSON
        for d in ("2026-10-01", "2026-10-08", "2026-10-15"):
            (tmp_path / f"{d}.json").write_text(
                json.dumps(
                    to_json(sample_snapshots),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        result = load_latest_snapshot()
        assert result is not None
        path, loaded = result
        # 最新的应该是 10-15
        assert path.name == "2026-10-15.json"
        assert ".MSCI_US_REIT" in loaded

    def test_load_latest_snapshot_no_files(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """DATA_DIR 空时返回 None。"""
        monkeypatch.setattr("global_allocation.portfolio.fetch_reit_valuation.DATA_DIR", tmp_path)
        result = load_latest_snapshot()
        assert result is None

    def test_load_latest_snapshot_no_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """DATA_DIR 不存在时返回 None（不抛错）。"""
        nonexistent = tmp_path / "no_such_dir"
        monkeypatch.setattr("global_allocation.portfolio.fetch_reit_valuation.DATA_DIR", nonexistent)
        result = load_latest_snapshot()
        assert result is None


# ─── 失败路径（mock yfinance/akshare 抛异常）────────────────


class TestFailurePaths:
    def test_fetch_us_reit_handles_yfinance_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """yfinance 抛异常时返回 error 字段非空的 REITValuation，不抛。"""
        from global_allocation.portfolio import fetch_reit_valuation as f

        def _boom_yf():
            class _FakeYF:
                @staticmethod
                def Ticker(*args, **kwargs):
                    raise ConnectionError("yfinance 网络断了")
            return _FakeYF

        monkeypatch.setattr(f, "_get_yf", _boom_yf)

        result = f.fetch_us_reit()
        assert result.error is not None
        assert "ConnectionError" in result.error
        assert result.value is None
        assert result.dy_percentile_5y is None
        assert result.valuation_percentile_5y is None
        assert result.index_code == ".MSCI_US_REIT"

    def test_fetch_cn_reit_handles_akshare_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """akshare 抛异常时返回 error 字段非空，但 yfinance 部分仍能成功。"""
        from global_allocation.portfolio import fetch_reit_valuation as f

        def _boom_ak():
            class _FakeAK:
                @staticmethod
                def stock_zh_index_value_csindex(*args, **kwargs):
                    raise ConnectionError("akshare 502")
            return _FakeAK

        monkeypatch.setattr(f, "_get_ak", _boom_ak)

        result = f.fetch_cn_reit()
        # DY 失败（akshare 抛错）
        assert result.value is None
        # error 字段非空
        assert result.error is not None
        assert "DY 失败" in result.error
        assert result.index_code == "932006"


# ─── 常量检查 ─────────────────────────────


class TestConstants:
    def test_proxy_symbols(self) -> None:
        """代理 ETF 选 VNQ + 508000.SS（Vanguard + 华夏 REITs 联接 ETF）。"""
        assert US_REIT_PROXY == "VNQ"
        assert CN_REIT_PROXY == "508000.SS"

    def test_history_years_is_5(self) -> None:
        """5 年窗口（spec REITs-DY：5 年分位）。"""
        assert HISTORY_YEARS == 5

    def test_data_dir_default(self) -> None:
        """DATA_DIR 默认是 ~/.local/share/gap/reit_valuation。"""
        assert "reit_valuation" in str(DATA_DIR)
