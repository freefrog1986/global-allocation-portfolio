"""测试 src/global_allocation/strategy/signals.py。

参照 specs/091-strategy-config.md §估值信号。
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
    PlanSleeve,
    PlanTarget,
    SelectionConfig,
    Strategy,
    StrategyStatus,
    StrategyType,
    StrategyVersion,
    ValuationSignal,
)
from global_allocation.strategy.repo import StrategyRepo
from global_allocation.strategy.signals import compute_signal_actions


@pytest.fixture
def repo(tmp_path: Path) -> StrategyRepo:
    return StrategyRepo(db=StrategyDB(path=tmp_path / "strategy.db"))


def _seed_selection(
    repo: StrategyRepo,
    cfg: SelectionConfig | None = None,
) -> tuple[Strategy, StrategyVersion, list[PlanSleeve], dict[int, list[PlanTarget]]]:
    """建 selection 策略 + 2 sleeve / 2 fund plan。"""
    if cfg is None:
        cfg = SelectionConfig(
            entry_signal=ValuationSignal(
                entry_threshold=Decimal("30"), exit_threshold=Decimal("70")
            ),
            exit_signal=ValuationSignal(
                entry_threshold=Decimal("30"), exit_threshold=Decimal("70")
            ),
        )
    s = Strategy(
        id="a-share-dividend",
        name="A 股红利",
        type=StrategyType.SELECTION,
        created_at=datetime(2026, 9, 18),
        updated_at=datetime(2026, 9, 18),
    )
    repo.create_strategy(s)
    v = StrategyVersion(
        strategy_id="a-share-dividend",
        version=1,
        status=StrategyStatus.ACTIVE,
        config=cfg,
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


# ─── compute_signal_actions ─────────────────────────────────


class TestSignalActions:
    def test_no_signals_returns_empty(self, repo: StrategyRepo) -> None:
        """selection 策略没配 entry/exit signal → 空 actions。"""
        cfg = SelectionConfig()  # 没信号
        s, v, sleeves, targets_map = _seed_selection(repo, cfg=cfg)
        out = compute_signal_actions(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_percentiles={"fund_0": Decimal("20")},
            current_holdings=[],
            current_prices={},
            as_of=date(2026, 9, 18),
        )
        assert out.actions == []

    def test_below_entry_triggers_buy(self, repo: StrategyRepo) -> None:
        """fund_0 percentile=20 < entry=30 → buy。"""
        s, v, sleeves, targets_map = _seed_selection(repo)
        # 有 cash（用 fund_cash 占位）→ total_value=1000 → 买 fund_0 到 49%
        holdings = [_holding("fund_cash", "1000", "1.00")]
        out = compute_signal_actions(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_percentiles={"fund_0": Decimal("20")},
            current_holdings=holdings,
            current_prices={
                "fund_0": Decimal("1.00"),
                "fund_cash": Decimal("1.00"),
            },
            as_of=date(2026, 9, 18),
        )
        a = next(a for a in out.actions if a.fund_code == "fund_0")
        assert a.action == "buy"
        assert a.delta_shares > 0

    def test_above_exit_triggers_sell(self, repo: StrategyRepo) -> None:
        """fund_0 percentile=80 > exit=70 + 已持有 → sell。"""
        s, v, sleeves, targets_map = _seed_selection(repo)
        holdings = [_holding("fund_0", "100", "1.00")]
        out = compute_signal_actions(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_percentiles={"fund_0": Decimal("80")},
            current_holdings=holdings,
            current_prices={"fund_0": Decimal("1.00")},
            as_of=date(2026, 9, 18),
        )
        a = next(a for a in out.actions if a.fund_code == "fund_0")
        assert a.action == "sell"
        assert a.delta_shares < 0

    def test_in_band_no_action(self, repo: StrategyRepo) -> None:
        """percentile 在 [30, 70] → 无 action。"""
        s, v, sleeves, targets_map = _seed_selection(repo)
        out = compute_signal_actions(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_percentiles={"fund_0": Decimal("50")},
            current_holdings=[_holding("fund_0", "100", "1.00")],
            current_prices={"fund_0": Decimal("1.00")},
            as_of=date(2026, 9, 18),
        )
        assert out.actions == []

    def test_below_entry_no_buy_if_already_held(self, repo: StrategyRepo) -> None:
        """已持有 + percentile < entry → 不再 buy（避免重复）。"""
        s, v, sleeves, targets_map = _seed_selection(repo)
        holdings = [_holding("fund_0", "100", "1.00")]
        out = compute_signal_actions(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_percentiles={"fund_0": Decimal("20")},
            current_holdings=holdings,
            current_prices={"fund_0": Decimal("1.00")},
            as_of=date(2026, 9, 18),
        )
        assert out.actions == []

    def test_above_exit_no_sell_if_not_held(self, repo: StrategyRepo) -> None:
        """未持有 + percentile > exit → 无 action（不能卖没持有的）。"""
        s, v, sleeves, targets_map = _seed_selection(repo)
        out = compute_signal_actions(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_percentiles={"fund_0": Decimal("80")},
            current_holdings=[],
            current_prices={"fund_0": Decimal("1.00")},
            as_of=date(2026, 9, 18),
        )
        assert out.actions == []

    def test_missing_percentile_skips(self, repo: StrategyRepo) -> None:
        """没 percentile 数据的 fund → 跳过。"""
        s, v, sleeves, targets_map = _seed_selection(repo)
        out = compute_signal_actions(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_percentiles={},  # 没数据
            current_holdings=[],
            current_prices={"fund_0": Decimal("1.00")},
            as_of=date(2026, 9, 18),
        )
        assert out.actions == []

    def test_holding_not_in_target_sells(self, repo: StrategyRepo) -> None:
        """持有但不在 target 列表的 fund → sell。"""
        s, v, sleeves, targets_map = _seed_selection(repo)
        holdings = [
            _holding("fund_0", "100", "1.00"),  # target fund, in band
            _holding("fund_extra", "50", "1.00"),  # 不在 target
        ]
        out = compute_signal_actions(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_percentiles={"fund_0": Decimal("50")},
            current_holdings=holdings,
            current_prices={
                "fund_0": Decimal("1.00"),
                "fund_extra": Decimal("1.00"),
            },
            as_of=date(2026, 9, 18),
        )
        extra = next(a for a in out.actions if a.fund_code == "fund_extra")
        assert extra.action == "sell"
        assert extra.sleeve_code == "(none)"

    def test_summary(self, repo: StrategyRepo) -> None:
        s, v, sleeves, targets_map = _seed_selection(repo)
        out = compute_signal_actions(
            strategy=s,
            version=v,
            sleeves=sleeves,
            targets_by_sleeve=targets_map,
            current_percentiles={
                "fund_0": Decimal("20"),  # buy
                "fund_1": Decimal("80"),  # not held, no sell
            },
            current_holdings=[],
            current_prices={
                "fund_0": Decimal("1.00"),
                "fund_1": Decimal("1.00"),
            },
            as_of=date(2026, 9, 18),
        )
        assert len(out.actions) == 1
        assert "1" in out.summary
