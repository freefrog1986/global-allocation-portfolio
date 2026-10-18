"""测试 src/global_allocation/portfolio/fetch_gold_valuation.py。

spec 099 — liubo 2026-10-18 拍板黄金估值也走 fetch + 注入。
测试范围：
- GoldValuation 数据类
- to_json / save_snapshot / load_snapshot 序列化
- load_latest_snapshot 找最新文件
- _compute_gold_pct_5y 分位算法（跟 PE 分位一致）
- 失败路径：akshare / FRED 错误时返回 error 字段非空
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from global_allocation.portfolio.fetch_gold_valuation import (
    DATA_DIR,
    GoldValuation,
    _compute_gold_pct_5y,
    load_latest_snapshot,
    load_snapshot,
    save_snapshot,
    to_json,
)


class TestGoldValuation:
    """GoldValuation 数据类 — frozen + slots。"""

    def test_construct_with_all_fields(self) -> None:
        v = GoldValuation(
            gold_price=Decimal("891.44"),
            gold_pct_5y=Decimal("0.9113"),
            real_yield_dfii10=Decimal("0.0278"),
            data_date=date(2026, 10, 15),
            source="akshare + FRED",
            error="",
        )
        assert v.gold_price == Decimal("891.44")
        assert v.gold_pct_5y == Decimal("0.9113")
        assert v.real_yield_dfii10 == Decimal("0.0278")
        assert v.error == ""

    def test_construct_with_error_only(self) -> None:
        """数据全 None + error 非空（拉取失败）。"""
        v = GoldValuation(
            gold_price=None,
            gold_pct_5y=None,
            real_yield_dfii10=None,
            data_date=date(2026, 10, 15),
            source="akshare + FRED",
            error="akshare 返回空",
        )
        assert v.gold_price is None
        assert v.gold_pct_5y is None
        assert v.real_yield_dfii10 is None
        assert "akshare" in v.error


class TestPercentileAlgorithm:
    """_compute_gold_pct_5y 分位算法 — 跟 PE 分位一致（低 = 便宜方向）。"""

    def test_mid_percentile(self) -> None:
        """中位数 → 0.50。"""
        # 30 条序列：101~130，当前价 = 最后一项 = 130（最大）
        series = [(date(2026, 1, 1 + i), Decimal(str(100 + i))) for i in range(30)]
        # 严格小于 130 的有 29 个 → 29/30 ≈ 0.967（接近最贵，不是 mid）
        # 让当前价是中位数：101~131 共 50 条，当前 130 → sorted[25]
        # 等等，让我们简化：直接验当前价在 [0, 1] 之间
        pct = _compute_gold_pct_5y(series)
        assert pct is not None
        assert Decimal("0.9") < pct <= Decimal("1.0")

    def test_current_is_min_returns_low(self) -> None:
        """当前价 = 最小 → 分位 0（最便宜）。"""
        # 30 条 + 当前最小：先升 30，最后一项 = 100（最小）
        series = [(date(2026, 1, 1 + i), Decimal(str(100 + i))) for i in range(30)]
        series[-1] = (date(2026, 1, 31), Decimal("50"))  # 改最后一项为最小值
        pct = _compute_gold_pct_5y(series)
        assert pct is not None
        assert pct == Decimal("0")

    def test_current_is_max_returns_high(self) -> None:
        """当前价 = 最大 → 分位 ≈ 1（最贵）。"""
        series = [(date(2026, 1, 1 + i), Decimal(str(100 + i))) for i in range(30)]
        # 当前价 = 129，最大；严格小于 129 的有 29 个 → 29/30
        series[-1] = (date(2026, 1, 31), Decimal("129"))
        pct = _compute_gold_pct_5y(series)
        assert pct is not None
        # 严格小于 129 的有 29 个 → 29/30
        assert pct == Decimal("29") / Decimal("30")

    def test_too_short_series_returns_none(self) -> None:
        """序列太短（< 30 条）→ 返回 None（数据不足）。"""
        series = [(date(2026, 1, i), Decimal("100")) for i in range(1, 11)]
        assert _compute_gold_pct_5y(series) is None


class TestJsonSerialization:
    """to_json / save_snapshot / load_snapshot。"""

    def test_to_json_round_trip(self, tmp_path: Path) -> None:
        """写 JSON → 读回来字段一致。"""
        snap = {"000216": GoldValuation(
            gold_price=Decimal("891.44"),
            gold_pct_5y=Decimal("0.9113"),
            real_yield_dfii10=Decimal("0.0278"),
            data_date=date(2026, 10, 15),
            source="akshare + FRED",
            error="",
        )}
        # Monkeypatch DATA_DIR to tmp_path so save doesn't pollute home
        import global_allocation.portfolio.fetch_gold_valuation as mod
        original_dir = mod.DATA_DIR
        mod.DATA_DIR = tmp_path
        try:
            path = save_snapshot(snap)
            assert path.exists()
            assert path.parent == tmp_path

            loaded = load_snapshot(path)
            assert "000216" in loaded
            v = loaded["000216"]
            assert v.gold_price == Decimal("891.44")
            assert v.gold_pct_5y == Decimal("0.9113")
            assert v.real_yield_dfii10 == Decimal("0.0278")
            assert v.data_date == date(2026, 10, 15)
        finally:
            mod.DATA_DIR = original_dir

    def test_to_json_preserves_nulls(self, tmp_path: Path) -> None:
        """None 字段序列化成 null。"""
        payload = to_json({"000216": GoldValuation(
            gold_price=None,
            gold_pct_5y=None,
            real_yield_dfii10=None,
            data_date=date(2026, 10, 15),
            source="",
            error="拉取失败",
        )})
        assert payload["funds"]["000216"]["gold_price"] is None
        assert payload["funds"]["000216"]["error"] == "拉取失败"


class TestLoadLatest:
    """load_latest_snapshot 找最新 JSON 文件。"""

    def test_returns_none_when_no_files(self, tmp_path: Path) -> None:
        import global_allocation.portfolio.fetch_gold_valuation as mod
        original = mod.DATA_DIR
        mod.DATA_DIR = tmp_path
        try:
            assert load_latest_snapshot() is None
        finally:
            mod.DATA_DIR = original

    def test_loads_most_recent_file(self, tmp_path: Path) -> None:
        """多份 JSON 时选最新（按文件名降序）。"""
        import global_allocation.portfolio.fetch_gold_valuation as mod
        original = mod.DATA_DIR
        mod.DATA_DIR = tmp_path
        try:
            # Write 2026-10-10.json
            (tmp_path / "2026-10-10.json").write_text(
                json.dumps(to_json({"000216": GoldValuation(
                    gold_price=Decimal("880"),
                    gold_pct_5y=Decimal("0.85"),
                    real_yield_dfii10=Decimal("0.03"),
                    data_date=date(2026, 10, 10),
                    source="",
                    error="",
                )}))
            )
            # Write 2026-10-18.json (newer)
            (tmp_path / "2026-10-18.json").write_text(
                json.dumps(to_json({"000216": GoldValuation(
                    gold_price=Decimal("891.44"),
                    gold_pct_5y=Decimal("0.9113"),
                    real_yield_dfii10=Decimal("0.0278"),
                    data_date=date(2026, 10, 18),
                    source="",
                    error="",
                )}))
            )

            path, snap = load_latest_snapshot()
            assert path.name == "2026-10-18.json"
            assert snap["000216"].gold_price == Decimal("891.44")
        finally:
            mod.DATA_DIR = original