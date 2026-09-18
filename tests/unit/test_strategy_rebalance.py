"""测试 src/global_allocation/strategy/rebalance.py。

参照 specs/091-strategy-config.md §再平衡建议。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from global_allocation.models import AssetClass
from global_allocation.portfolio.models import Fund, Holding
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
    ValuationSignal,
)
from global_allocation.strategy.rebalance import (
    compute_next_rebalance_date,
    compute_rebalance_suggestion,
)
from global_allocation.strategy.repo import StrategyRepo

# ─── fixtures / helpers ─────────────────────────────────────


@pytest.fixture
def repo(tmp_path: Path) -> StrategyRepo:
    return StrategyRepo(db=StrategyDB(path=tmp_path / "strategy.db"))


def _seed_strategy(
    repo: StrategyRepo,
    type_: StrategyType = StrategyType.ALLOCATION,
    config: AllocationConfig | SelectionConfig | None = None,
) -> tuple[Strategy, StrategyVersion, list[PlanSleeve], dict[int, list[PlanTarget]]]:
    """建策略 + 1 个 active version + 2 sleeve/2 fund plan。

    Returns (strategy, version, sleeves, targets_by_sleeve)。
    每个 sleeve target=0.50, band (0.40, 0.60)，含 1 fund target=0.98, band (0.95, 0.99)。
    fund_0 整体 band = [0.40*0.95, 0.60*0.99] = [0.38, 0.594]，target=0.50*0.98=0.49。
    """
    s = Strategy(
        id="a-share-dividend",
        name="A 股红利",
        type=type_,
        created_at=datetime(2026, 9, 18),
        updated_at=datetime(2026, 9, 18),
    )
    repo.create_strategy(s)
    if config is None:
        config = AllocationConfig()
    v = StrategyVersion(
        strategy_id="a-share-dividend",
        version=1,
        status=StrategyStatus.ACTIVE,
        config=config,
        created_at=datetime(2026, 9, 18),
    )
    vid = repo.new_version(v)
    sleeves: list[PlanSleeve] = []
    targets_by_sleeve: dict[int, list[PlanTarget]] = {}
    for i in range(2):
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
            fund_code=f"fund_{i}",
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


def _holding(fund_code: str, shares: str, price: str) -> Holding:
    f = Fund(code=fund_code, name=fund_code, asset_class=AssetClass.EQUITY)
    return Holding(
        fund=f,
        shares=Decimal(shares),
        avg_cost=Decimal("1.00"),
        market_price=Decimal(price),
    )


# ─── compute_rebalance_suggestion ────────────────────────────


class TestComputeRebalanceSuggestion:
    def test_no_drift_no_actions(self, repo: StrategyRepo) -> None:
        """持仓和 target 对齐 → 无 actions。"""
        s, v, sleeves, targets_map = _seed_strategy(repo)
        holdings = [
            _holding("fund_0", "500", "1.00"),
            _holding("fund_1", "500", "1.00"),
        ]
        prices = {"fund_0": Decimal("1.00"), "fund_1": Decimal("1.00")}
        out = compute_rebalance_suggestion(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_holdings=holdings,
            current_prices=prices,
            as_of=date(2026, 9, 18),
        )
        assert out.total_value == Decimal("1000.00")
        assert out.actions == []
        assert "0" in out.summary or "无" in out.summary

    def test_drift_below_band_buy(self, repo: StrategyRepo) -> None:
        """fund_0 占比 0.30 < band_min=0.38 → buy。"""
        s, v, sleeves, targets_map = _seed_strategy(repo)
        holdings = [
            _holding("fund_0", "300", "1.00"),
            _holding("fund_1", "700", "1.00"),
        ]
        prices = {"fund_0": Decimal("1.00"), "fund_1": Decimal("1.00")}
        out = compute_rebalance_suggestion(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_holdings=holdings,
            current_prices=prices,
            as_of=date(2026, 9, 18),
        )
        assert out.total_value == Decimal("1000.00")
        a0 = next(a for a in out.actions if a.fund_code == "fund_0")
        assert a0.action == "buy"
        assert a0.delta_shares > 0
        assert a0.current_weight == Decimal("0.30")
        assert a0.target_weight == Decimal("0.49")

    def test_drift_above_band_sell(self, repo: StrategyRepo) -> None:
        """fund_0 占比 0.80 > band_max=0.594 → sell。"""
        s, v, sleeves, targets_map = _seed_strategy(repo)
        holdings = [
            _holding("fund_0", "800", "1.00"),
            _holding("fund_1", "200", "1.00"),
        ]
        prices = {"fund_0": Decimal("1.00"), "fund_1": Decimal("1.00")}
        out = compute_rebalance_suggestion(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_holdings=holdings,
            current_prices=prices,
            as_of=date(2026, 9, 18),
        )
        a0 = next(a for a in out.actions if a.fund_code == "fund_0")
        assert a0.action == "sell"
        assert a0.delta_shares < 0

    def test_holding_not_in_target_sell(self, repo: StrategyRepo) -> None:
        """fund_extra 不在 target → sell。"""
        s, v, sleeves, targets_map = _seed_strategy(repo)
        holdings = [
            _holding("fund_0", "500", "1.00"),
            _holding("fund_1", "400", "1.00"),
            _holding("fund_extra", "100", "1.00"),
        ]
        prices = {
            "fund_0": Decimal("1.00"),
            "fund_1": Decimal("1.00"),
            "fund_extra": Decimal("1.00"),
        }
        out = compute_rebalance_suggestion(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_holdings=holdings,
            current_prices=prices,
            as_of=date(2026, 9, 18),
        )
        extra_actions = [a for a in out.actions if a.fund_code == "fund_extra"]
        assert len(extra_actions) == 1
        assert extra_actions[0].action == "sell"
        assert extra_actions[0].sleeve_code == "(none)"
        assert "不在" in extra_actions[0].note

    def test_empty_holdings_no_actions(self, repo: StrategyRepo) -> None:
        s, v, sleeves, targets_map = _seed_strategy(repo)
        out = compute_rebalance_suggestion(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_holdings=[],
            current_prices={},
            as_of=date(2026, 9, 18),
        )
        assert out.total_value == Decimal("0")
        assert out.actions == []
        assert "无" in out.summary or "空" in out.summary

    def test_target_fund_with_zero_holdings_buy(self, repo: StrategyRepo) -> None:
        """fund_0 不在 holding → current_weight=0 < band_min=0.38 → buy。"""
        s, v, sleeves, targets_map = _seed_strategy(repo)
        holdings = [_holding("fund_1", "1000", "1.00")]
        prices = {"fund_0": Decimal("1.00"), "fund_1": Decimal("1.00")}
        out = compute_rebalance_suggestion(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_holdings=holdings,
            current_prices=prices,
            as_of=date(2026, 9, 18),
        )
        a0 = next(a for a in out.actions if a.fund_code == "fund_0")
        assert a0.action == "buy"
        assert a0.current_weight == Decimal("0")
        assert a0.delta_shares > 0

    def test_summary_includes_action_count(self, repo: StrategyRepo) -> None:
        s, v, sleeves, targets_map = _seed_strategy(repo)
        holdings = [
            _holding("fund_0", "900", "1.00"),  # 0.9 > 0.60 → sell
            _holding("fund_1", "100", "1.00"),  # 0.1 < 0.38 → buy
        ]
        prices = {"fund_0": Decimal("1.00"), "fund_1": Decimal("1.00")}
        out = compute_rebalance_suggestion(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_holdings=holdings,
            current_prices=prices,
            as_of=date(2026, 9, 18),
        )
        assert len(out.actions) == 2
        assert "2" in out.summary

    def test_action_sleeve_code_populated(self, repo: StrategyRepo) -> None:
        s, v, sleeves, targets_map = _seed_strategy(repo)
        holdings = [
            _holding("fund_0", "300", "1.00"),
            _holding("fund_1", "700", "1.00"),
        ]
        prices = {"fund_0": Decimal("1.00"), "fund_1": Decimal("1.00")}
        out = compute_rebalance_suggestion(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_holdings=holdings,
            current_prices=prices,
            as_of=date(2026, 9, 18),
        )
        a = out.actions[0]
        assert a.sleeve_code == "sleeve_0"

    def test_action_est_value_uses_abs_delta(self, repo: StrategyRepo) -> None:
        """est_value = |delta_shares| × price。"""
        s, v, sleeves, targets_map = _seed_strategy(repo)
        # fund_0: 150 × 2.00 = 300, weight=0.30 < band_min=0.38 → buy
        holdings = [
            _holding("fund_0", "150", "2.00"),
            _holding("fund_1", "700", "1.00"),
        ]
        prices = {"fund_0": Decimal("2.00"), "fund_1": Decimal("1.00")}
        out = compute_rebalance_suggestion(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_holdings=holdings,
            current_prices=prices,
            as_of=date(2026, 9, 18),
        )
        a = next(x for x in out.actions if x.fund_code == "fund_0")
        # target_weight=0.49, total=1000 → target_value=490, price=2.00 → target_shares=245
        # delta=245-150=95, est_value=95*2.00=190
        assert a.target_shares == Decimal("245.00")
        assert a.delta_shares == Decimal("95.00")
        assert a.est_value == Decimal("190.00")

    def test_missing_price_skips_action(self, repo: StrategyRepo) -> None:
        """price 缺失 → 跳过该 fund 的 action（spec 边界 4）。"""
        s, v, sleeves, targets_map = _seed_strategy(repo)
        holdings = [
            _holding("fund_0", "200", "1.00"),  # drift → 但没价格
            _holding("fund_1", "800", "1.00"),
        ]
        prices = {"fund_1": Decimal("1.00")}  # fund_0 没价格
        out = compute_rebalance_suggestion(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_holdings=holdings,
            current_prices=prices,
            as_of=date(2026, 9, 18),
        )
        # fund_0 没价格被跳过；fund_1: weight=0.80 > 0.60 → sell
        codes = {a.fund_code for a in out.actions}
        assert "fund_0" not in codes


# ─── compute_next_rebalance_date ────────────────────────────


class TestNextRebalanceDate:
    def test_selection_strategy_returns_none(self, repo: StrategyRepo) -> None:
        cfg = SelectionConfig(
            entry_signal=ValuationSignal(),
            exit_signal=ValuationSignal(),
        )
        s, v, sleeves, targets_map = _seed_strategy(
            repo, type_=StrategyType.SELECTION, config=cfg
        )
        next_d, reason = compute_next_rebalance_date(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            last_rebalance=date(2026, 6, 18),
        )
        assert next_d is None
        assert "selection" in reason.lower() or "估值" in reason

    def test_calendar_quarterly(self, repo: StrategyRepo) -> None:
        cfg = AllocationConfig(
            rebalance_trigger=RebalanceTrigger(calendar="quarterly")
        )
        s, v, sleeves, targets_map = _seed_strategy(repo, config=cfg)
        next_d, reason = compute_next_rebalance_date(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            last_rebalance=date(2026, 6, 18),
        )
        assert next_d == date(2026, 9, 16)  # +90 天
        assert "quarterly" in reason

    def test_calendar_monthly(self, repo: StrategyRepo) -> None:
        cfg = AllocationConfig(
            rebalance_trigger=RebalanceTrigger(calendar="monthly")
        )
        s, v, sleeves, targets_map = _seed_strategy(repo, config=cfg)
        next_d, reason = compute_next_rebalance_date(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            last_rebalance=date(2026, 6, 18),
        )
        assert next_d == date(2026, 7, 18)  # +30 天
        assert "monthly" in reason

    def test_no_last_rebalance_no_calendar(self, repo: StrategyRepo) -> None:
        cfg = AllocationConfig(rebalance_trigger=RebalanceTrigger())
        s, v, sleeves, targets_map = _seed_strategy(repo, config=cfg)
        next_d, _ = compute_next_rebalance_date(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            last_rebalance=None,
        )
        assert next_d is None

    def test_threshold_triggered_returns_today(self, repo: StrategyRepo) -> None:
        cfg = AllocationConfig(
            rebalance_trigger=RebalanceTrigger(
                calendar="quarterly", threshold=Decimal("0.05")
            )
        )
        s, v, sleeves, targets_map = _seed_strategy(repo, config=cfg)
        # fund_0 drift 50% > 5%
        holdings = [
            _holding("fund_0", "900", "1.00"),
            _holding("fund_1", "100", "1.00"),
        ]
        prices = {"fund_0": Decimal("1.00"), "fund_1": Decimal("1.00")}
        next_d, reason = compute_next_rebalance_date(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            last_rebalance=date(2026, 6, 18),
            current_holdings=holdings,
            current_prices=prices,
            as_of=date(2026, 7, 1),
        )
        assert next_d == date(2026, 7, 1)
        assert "threshold" in reason.lower() or "drift" in reason.lower()
