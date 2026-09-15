"""测试 src/global_allocation/feishu/card.py。"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from global_allocation.feishu.card import build_backtest_card, card_to_json
from global_allocation.models import (
    Asset,
    AssetClass,
    BacktestResult,
    Currency,
    DataSource,
    PerformanceMetrics,
    PortfolioSnapshot,
    RebalanceEvent,
    Region,
)


def _make_asset(symbol: str) -> Asset:
    return Asset(
        symbol=symbol,
        name=symbol,
        asset_class=AssetClass.EQUITY,
        region=Region.US,
        currency=Currency.USD,
        data_source=DataSource.YFINANCE,
    )


def _make_backtest_result(n_days: int = 30) -> BacktestResult:
    """构造一个最小的 BacktestResult 用于测试。"""
    dates = pd.date_range("2024-01-01", periods=n_days, freq="D")
    nav = [100000.0 + i * 100 for i in range(n_days)]
    equity_curve = pd.DataFrame(
        {
            "nav": nav,
            "price_AAPL": [150.0 + i * 0.1 for i in range(n_days)],
        },
        index=dates,
    )

    snapshots = [
        PortfolioSnapshot(
            date=dates[i].date(),
            total_value=Decimal(str(nav[i])),
            positions={"AAPL": Decimal("100"), "BND": Decimal("100")},
            weights={"AAPL": Decimal("0.6"), "BND": Decimal("0.4")},
            cash=Decimal("0"),
        )
        for i in range(n_days)
    ]

    metrics = PerformanceMetrics(
        cagr=Decimal("0.0829"),
        sharpe=Decimal("0.87"),
        max_drawdown=Decimal("-0.2215"),
        volatility=Decimal("0.1234"),
        total_return=Decimal("0.4893"),
        annual_return=Decimal("0.08"),
        correlation=pd.DataFrame({"AAPL": [1.0], "BND": [1.0]}, index=["AAPL", "BND"]),
        best_day=Decimal("0.02"),
        worst_day=Decimal("-0.03"),
        win_rate=Decimal("0.55"),
    )

    return BacktestResult(
        strategy_id="60_40",
        start_date=date(2024, 1, 1),
        end_date=dates[-1].date(),
        initial_capital=Decimal("100000"),
        final_value=Decimal(str(nav[-1])),
        equity_curve=equity_curve,
        snapshots=snapshots,
        metrics=metrics,
        rebalance_events=[
            RebalanceEvent(
                date=date(2024, 1, 1),
                triggered_by="schedule",
                trades=[],
                cost_bps=Decimal("10"),
            )
        ],
    )


class TestBuildBacktestCard:
    def test_basic_structure(self) -> None:
        result = _make_backtest_result()
        card = build_backtest_card(result)

        assert "header" in card
        assert "elements" in card
        assert "footer" in card
        # header
        assert card["header"]["template"] == "blue"
        assert "title" in card["header"]

    def test_has_equity_line_chart(self) -> None:
        result = _make_backtest_result()
        card = build_backtest_card(result)

        # 找第一个 chart
        charts = [e for e in card["elements"] if e.get("tag") == "chart"]
        assert len(charts) >= 1
        equity_chart = charts[0]["chart_spec"]
        assert equity_chart["type"] == "line"
        assert "x_axis" in equity_chart
        assert "y_axis" in equity_chart
        assert len(equity_chart["series"]) == 1

    def test_has_pie_chart(self) -> None:
        result = _make_backtest_result()
        card = build_backtest_card(result)

        charts = [e for e in card["elements"] if e.get("tag") == "chart"]
        assert len(charts) >= 2
        pie = charts[1]["chart_spec"]
        assert pie["type"] == "pie"
        # 至少有 2 个权重分片
        assert len(pie["series"][0]["data"]) >= 2

    def test_has_metrics_table(self) -> None:
        result = _make_backtest_result()
        card = build_backtest_card(result)

        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        assert len(tables) == 1
        table = tables[0]
        assert "columns" in table
        assert "rows" in table
        # 至少包含 CAGR 行
        metrics = [r[0].get("metric", "") for r in table["rows"]]
        assert "CAGR" in metrics
        assert "夏普" in metrics

    def test_summary_includes_title_and_dates(self) -> None:
        result = _make_backtest_result()
        card = build_backtest_card(result, title="我的策略")

        # header title
        assert card["header"]["title"]["content"] == "我的策略"
        # summary text 包含日期
        divs = [e for e in card["elements"] if e.get("tag") == "div"]
        assert len(divs) >= 1
        summary_text = divs[0]["text"]["content"]
        assert "2024-01-01" in summary_text
        assert "我的策略" in summary_text

    def test_default_title_uses_strategy_id(self) -> None:
        result = _make_backtest_result()
        card = build_backtest_card(result)
        assert card["header"]["title"]["content"] == "60_40"

    def test_too_few_points_raises(self) -> None:
        result = _make_backtest_result(n_days=1)
        with pytest.raises(ValueError, match="不足"):
            build_backtest_card(result)

    def test_downsample_when_too_many_points(self) -> None:
        # 1500 个点 > 1000 阈值
        result = _make_backtest_result(n_days=1500)
        card = build_backtest_card(result)

        charts = [e for e in card["elements"] if e.get("tag") == "chart"]
        equity_chart = charts[0]["chart_spec"]
        # x 轴和 series 都被降采样到 1000
        assert len(equity_chart["x_axis"]["data"]) == 1000
        assert len(equity_chart["series"][0]["data"]) == 1000

    def test_no_downsample_when_under_limit(self) -> None:
        result = _make_backtest_result(n_days=100)
        card = build_backtest_card(result)

        charts = [e for e in card["elements"] if e.get("tag") == "chart"]
        equity_chart = charts[0]["chart_spec"]
        assert len(equity_chart["x_axis"]["data"]) == 100

    def test_card_to_json_preserves_chinese(self) -> None:
        result = _make_backtest_result()
        card = build_backtest_card(result, title="60/40 经典股债")
        json_str = card_to_json(card)
        # 中文 title 保留
        assert "60/40 经典股债" in json_str
        # 是合法 JSON
        parsed = json.loads(json_str)
        assert parsed["header"]["title"]["content"] == "60/40 经典股债"

    def test_pie_chart_excludes_zero_weights(self) -> None:
        # 构造一个 3 个标的但权重 0 的
        result = _make_backtest_result()
        new_weights = {
            "AAPL": Decimal("0.6"),
            "BND": Decimal("0.4"),
            "CASH": Decimal("0"),
        }
        # PortfolioSnapshot is frozen → 用 model_copy 替换
        new_snap = result.snapshots[-1].model_copy(update={"weights": new_weights})
        new_snapshots = result.snapshots[:-1] + [new_snap]
        new_result = result.model_copy(update={"snapshots": new_snapshots})

        card = build_backtest_card(new_result)

        charts = [e for e in card["elements"] if e.get("tag") == "chart"]
        pie = charts[1]["chart_spec"]
        pie_names = [d["name"] for d in pie["series"][0]["data"]]
        # CASH 不应该出现在饼图
        assert "CASH" not in pie_names
