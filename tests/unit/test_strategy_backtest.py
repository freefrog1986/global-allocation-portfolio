"""测试 src/global_allocation/strategy/backtest.py。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from global_allocation.strategy.backtest import (
    build_backtest_strategy,
    run_strategy_backtest,
)
from global_allocation.strategy.db import StrategyDB
from global_allocation.strategy.models import (
    AllocationConfig,
    PlanSleeve,
    PlanTarget,
    RebalanceTrigger,
    SelectionConfig,
    Strategy,
    StrategyStatus,
    StrategyType,
    StrategyVersion,
)
from global_allocation.strategy.repo import StrategyRepo


@pytest.fixture
def repo(tmp_path: Path) -> StrategyRepo:
    return StrategyRepo(db=StrategyDB(path=tmp_path / "strategy.db"))


def _seed(
    repo: StrategyRepo,
    config: AllocationConfig | None = None,
) -> tuple[Strategy, StrategyVersion, list[PlanSleeve], dict[int, list[PlanTarget]]]:
    if config is None:
        config = AllocationConfig(
            rebalance_trigger=RebalanceTrigger(calendar="quarterly")
        )
    s = Strategy(
        id="global-alloc",
        name="全球配置",
        type=StrategyType.ALLOCATION,
        created_at=datetime(2026, 9, 18),
        updated_at=datetime(2026, 9, 18),
    )
    repo.create_strategy(s)
    v = StrategyVersion(
        strategy_id="global-alloc",
        version=1,
        status=StrategyStatus.ACTIVE,
        config=config,
        created_at=datetime(2026, 9, 18),
    )
    vid = repo.new_version(v)
    sleeves: list[PlanSleeve] = []
    targets_by_sleeve: dict[int, list[PlanTarget]] = {}
    for i, code in enumerate(["510300", "008114"]):
        sleeve = PlanSleeve(
            version_id=vid,
            code=f"sleeve_{i}",
            name=f"sleeve_{i}",
            target_weight=Decimal("0.50"),
            min_weight=Decimal("0.40"),
            max_weight=Decimal("0.60"),
            position=i,
        )
        sid = repo.add_sleeve(sleeve)
        sleeves.append(sleeve.model_copy(update={"id": sid}))
        t = PlanTarget(
            sleeve_id=sid,
            fund_code=code,
            weight=Decimal("0.98"),
            min_weight=Decimal("0.95"),
            max_weight=Decimal("0.99"),
            position=0,
        )
        tid = repo.add_target(t)
        targets_by_sleeve[sid] = [t.model_copy(update={"id": tid})]
    v2 = repo.get_version(vid)
    assert v2 is not None
    return s, v2, sleeves, targets_by_sleeve


def _make_prices() -> pd.DataFrame:
    """构造 60 天的合成价格数据。"""
    dates = pd.date_range(start="2026-01-01", periods=60, freq="D")
    data = {
        "510300": [3.0 + 0.01 * i for i in range(60)],
        "008114": [1.0 + 0.005 * i for i in range(60)],
    }
    return pd.DataFrame(data, index=dates)


# ─── build_backtest_strategy ────────────────────────────────


class TestBuildBacktestStrategy:
    def test_basic(self, repo: StrategyRepo) -> None:
        s, v, sleeves, targets_map = _seed(repo)
        bt = build_backtest_strategy(
            strategy_id="global-alloc",
            strategy_name="全球配置",
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            inception=date(2026, 1, 1),
        )
        assert bt.id == "global-alloc"
        assert len(bt.target_weights) == 2
        symbols = {tw.asset.symbol for tw in bt.target_weights}
        assert symbols == {"510300", "008114"}
        # 各 50% × 0.98 ≈ 0.49
        weights = {tw.asset.symbol: tw.weight for tw in bt.target_weights}
        assert all(abs(w - Decimal("0.49")) < Decimal("0.01") for w in weights.values())

    def test_quarterly_freq(self, repo: StrategyRepo) -> None:
        s, v, sleeves, targets_map = _seed(repo)
        bt = build_backtest_strategy(
            strategy_id="x",
            strategy_name="X",
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            inception=date(2026, 1, 1),
        )
        assert bt.rebalance.frequency == "quarterly"

    def test_selection_rejected(self, repo: StrategyRepo) -> None:
        s, v, sleeves, targets_map = _seed(repo)
        sel_v = v.model_copy(
            update={"config": SelectionConfig()}
        )
        with pytest.raises(ValueError, match="allocation"):
            build_backtest_strategy(
                strategy_id="x",
                strategy_name="X",
                version=sel_v,
                sleeves=sleeves,
                targets_by_sleeve=targets_map,
                inception=date(2026, 1, 1),
            )

    def test_empty_plan_rejected(self, repo: StrategyRepo) -> None:
        s = Strategy(
            id="x",
            name="X",
            type=StrategyType.ALLOCATION,
            created_at=datetime(2026, 9, 18),
            updated_at=datetime(2026, 9, 18),
        )
        repo.create_strategy(s)
        v = StrategyVersion(
            strategy_id="x",
            version=1,
            status=StrategyStatus.ACTIVE,
            config=AllocationConfig(),
            created_at=datetime(2026, 9, 18),
        )
        vid = repo.new_version(v)
        v2 = repo.get_version(vid)
        assert v2 is not None
        with pytest.raises(ValueError, match="没有"):
            build_backtest_strategy(
                strategy_id="x",
                strategy_name="X",
                version=v2,
                sleeves=[],
                targets_by_sleeve={},
                inception=date(2026, 1, 1),
            )


# ─── run_strategy_backtest ──────────────────────────────────


class TestRunStrategyBacktest:
    def test_basic(self, repo: StrategyRepo) -> None:
        s, v, sleeves, targets_map = _seed(repo)
        prices = _make_prices()
        result = run_strategy_backtest(
            strategy_id="global-alloc",
            strategy_name="全球配置",
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            prices=prices,
        )
        assert result.strategy_id == "global-alloc"
        assert result.initial_capital == Decimal("100000")
        assert len(result.snapshots) == 60
        assert result.metrics is not None

    def test_custom_initial_capital(self, repo: StrategyRepo) -> None:
        s, v, sleeves, targets_map = _seed(repo)
        prices = _make_prices()
        result = run_strategy_backtest(
            strategy_id="global-alloc",
            strategy_name="全球配置",
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            prices=prices,
            initial_capital=Decimal("50000"),
        )
        assert result.initial_capital == Decimal("50000")

    def test_selection_rejected(self, repo: StrategyRepo) -> None:
        s, v, sleeves, targets_map = _seed(repo)
        sel_v = v.model_copy(update={"config": SelectionConfig()})
        prices = _make_prices()
        with pytest.raises(ValueError, match="allocation"):
            run_strategy_backtest(
                strategy_id="x",
                strategy_name="X",
                version=sel_v,
                sleeves=sleeves,
                targets_by_sleeve=targets_map,
                prices=prices,
            )
