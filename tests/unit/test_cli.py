"""测试 src/global_allocation/cli.py。

用 Typer 的 CliRunner 跑命令，data fetch/clear 用 mock 避免真实网络/IO。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from typer.testing import CliRunner

from global_allocation.cli import app
from global_allocation.cli_helpers import (
    load_strategy,
    parse_period,
)

runner = CliRunner()


# ────────────────────────────────────────────────────────────────────
# parse_period / load_strategy 单元测试
# ────────────────────────────────────────────────────────────────────


class TestParsePeriod:
    def test_1y(self) -> None:
        start, end = parse_period("1y")
        assert (end - start).days == 365
        assert end == date.today()

    def test_5y(self) -> None:
        start, end = parse_period("5y")
        assert (end - start).days == 365 * 5

    def test_max(self) -> None:
        start, end = parse_period("max")
        assert start.year <= 1990
        assert end == date.today()

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="不支持"):
            parse_period("20y")


class TestLoadStrategy:
    def test_load_builtin(self) -> None:
        strategy = load_strategy("60_40")
        assert strategy.id == "60_40"

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="not found|nonexistent"):
            load_strategy("nonexistent_strategy")

    def test_load_from_yaml(self, tmp_path) -> None:
        yaml_content = """
id: my_strategy
name: My Test
description: For testing
base_currency: usd
inception: 2024-01-01
rebalance:
  frequency: yearly
target_weights:
  - asset:
      symbol: AAPL
      name: Apple
      asset_class: equity
      region: us
      currency: usd
      data_source: yfinance
    weight: 0.5
  - asset:
      symbol: BND
      name: Bond
      asset_class: bond
      region: us
      currency: usd
      data_source: yfinance
    weight: 0.5
"""
        path = tmp_path / "strategy.yaml"
        path.write_text(yaml_content, encoding="utf-8")
        strategy = load_strategy(str(path))
        assert strategy.id == "my_strategy"
        assert len(strategy.target_weights) == 2


# ────────────────────────────────────────────────────────────────────
# CLI command tests (用 CliRunner + mocks)
# ────────────────────────────────────────────────────────────────────


class TestStrategyCommands:
    def test_strategy_list(self) -> None:
        result = runner.invoke(app, ["strategy", "list"])
        assert result.exit_code == 0
        assert "60_40" in result.stdout
        assert "永久组合" in result.stdout or "permanent_portfolio" in result.stdout

    def test_strategy_show_builtin(self) -> None:
        result = runner.invoke(app, ["strategy", "show", "60_40"])
        assert result.exit_code == 0
        assert "VT" in result.stdout
        assert "BND" in result.stdout

    def test_strategy_show_unknown(self) -> None:
        result = runner.invoke(app, ["strategy", "show", "nope"])
        assert result.exit_code == 1


class TestDataCommands:
    @patch("global_allocation.cli.get_default_cache")
    def test_data_list_empty(self, mock_cache_factory: MagicMock) -> None:
        mock_cache = MagicMock()
        mock_cache.list_symbols.return_value = []
        mock_cache_factory.return_value = mock_cache

        result = runner.invoke(app, ["data", "list"])
        assert result.exit_code == 0
        assert "空" in result.stdout

    @patch("global_allocation.cli.get_default_cache")
    def test_data_list_with_items(self, mock_cache_factory: MagicMock) -> None:
        mock_cache = MagicMock()
        mock_cache.list_symbols.return_value = [
            ("AAPL", "yfinance"),
            ("510300.SH", "akshare"),
        ]
        mock_cache_factory.return_value = mock_cache

        result = runner.invoke(app, ["data", "list"])
        assert result.exit_code == 0
        assert "AAPL" in result.stdout
        assert "510300.SH" in result.stdout

    @patch("global_allocation.cli.get_default_cache")
    def test_data_clear_all(self, mock_cache_factory: MagicMock) -> None:
        mock_cache = MagicMock()
        mock_cache_factory.return_value = mock_cache

        result = runner.invoke(app, ["data", "clear", "--all"])
        assert result.exit_code == 0
        mock_cache.clear_all.assert_called_once()

    @patch("global_allocation.cli.get_default_cache")
    def test_data_clear_specific(self, mock_cache_factory: MagicMock) -> None:
        mock_cache = MagicMock()
        mock_cache_factory.return_value = mock_cache

        result = runner.invoke(app, ["data", "clear", "AAPL"])
        assert result.exit_code == 0
        # yfinance 和 akshare 都清了
        assert mock_cache.clear.call_count == 2

    def test_data_clear_needs_arg(self) -> None:
        result = runner.invoke(app, ["data", "clear"])
        assert result.exit_code == 1


class TestBacktestCommand:
    @patch("global_allocation.cli.run_backtest")
    @patch("global_allocation.cli.load_strategy")
    def test_backtest_runs(
        self, mock_load: MagicMock, mock_run: MagicMock
    ) -> None:
        from global_allocation.models import BacktestResult, PerformanceMetrics

        mock_strategy = MagicMock()
        mock_load.return_value = mock_strategy

        # 构造假结果
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        equity = pd.DataFrame(
            {"nav": [100000.0 + i for i in range(10)]}, index=dates
        )
        metrics = PerformanceMetrics(
            cagr=Decimal("0.05"),
            sharpe=Decimal("0.5"),
            max_drawdown=Decimal("-0.1"),
            volatility=Decimal("0.1"),
            total_return=Decimal("0.05"),
            annual_return=Decimal("0.05"),
            correlation=pd.DataFrame(),
            best_day=Decimal("0.01"),
            worst_day=Decimal("-0.01"),
            win_rate=Decimal("0.5"),
        )
        mock_result = BacktestResult(
            strategy_id="60_40",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 10),
            initial_capital=Decimal("100000"),
            final_value=Decimal("100090"),
            equity_curve=equity,
            snapshots=[],
            metrics=metrics,
            rebalance_events=[],
        )
        mock_run.return_value = mock_result

        result = runner.invoke(app, ["backtest", "60_40"])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    @patch("global_allocation.cli.load_strategy")
    def test_backtest_strategy_not_found(
        self, mock_load: MagicMock
    ) -> None:
        mock_load.side_effect = ValueError("not found")

        result = runner.invoke(app, ["backtest", "nope"])
        assert result.exit_code == 1


class TestCompareCommand:
    @patch("global_allocation.cli.run_backtest")
    @patch("global_allocation.cli.load_strategy")
    def test_compare_two_strategies(
        self, mock_load: MagicMock, mock_run: MagicMock
    ) -> None:
        from global_allocation.models import BacktestResult, PerformanceMetrics

        def make_strategy(name: str) -> Any:
            s = MagicMock()
            s.name = name
            return s

        def make_result(name: str) -> Any:
            dates = pd.date_range("2024-01-01", periods=5, freq="D")
            equity = pd.DataFrame({"nav": [100.0 + i for i in range(5)]}, index=dates)
            metrics = PerformanceMetrics(
                cagr=Decimal("0.05"),
                sharpe=Decimal("0.5"),
                max_drawdown=Decimal("-0.1"),
                volatility=Decimal("0.1"),
                total_return=Decimal("0.05"),
                annual_return=Decimal("0.05"),
                correlation=pd.DataFrame(),
                best_day=Decimal("0.01"),
                worst_day=Decimal("-0.01"),
                win_rate=Decimal("0.5"),
            )
            return BacktestResult(
                strategy_id=name,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
                initial_capital=Decimal("100000"),
                final_value=Decimal("105000"),
                equity_curve=equity,
                snapshots=[],
                metrics=metrics,
                rebalance_events=[],
            )

        mock_load.side_effect = [make_strategy("S1"), make_strategy("S2")]
        mock_run.side_effect = [make_result("s1"), make_result("s2")]

        result = runner.invoke(app, ["compare", "60_40", "permanent_portfolio"])
        assert result.exit_code == 0
        # 表格包含两个策略名
        assert "S1" in result.stdout or "S2" in result.stdout


class TestPublishCommand:
    @patch("global_allocation.cli.run_backtest")
    @patch("global_allocation.feishu.publisher.publish_backtest_report")
    @patch("global_allocation.cli.load_strategy")
    def test_publish_dry_run(
        self,
        mock_load: MagicMock,
        mock_publish: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        from global_allocation.models import BacktestResult, PerformanceMetrics

        mock_load.return_value = MagicMock(id="60_40")

        dates = pd.date_range("2024-01-01", periods=5, freq="D")
        equity = pd.DataFrame({"nav": [100.0 + i for i in range(5)]}, index=dates)
        metrics = PerformanceMetrics(
            cagr=Decimal("0.05"),
            sharpe=Decimal("0.5"),
            max_drawdown=Decimal("-0.1"),
            volatility=Decimal("0.1"),
            total_return=Decimal("0.05"),
            annual_return=Decimal("0.05"),
            correlation=pd.DataFrame(),
            best_day=Decimal("0.01"),
            worst_day=Decimal("-0.01"),
            win_rate=Decimal("0.5"),
        )
        mock_result = BacktestResult(
            strategy_id="60_40",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 5),
            initial_capital=Decimal("100000"),
            final_value=Decimal("105000"),
            equity_curve=equity,
            snapshots=[],
            metrics=metrics,
            rebalance_events=[],
        )
        mock_run.return_value = mock_result
        mock_publish.return_value = '{"card": "json"}'

        result = runner.invoke(app, ["publish", "60_40", "--dry-run"])
        assert result.exit_code == 0
        mock_publish.assert_called_once()
        call_kwargs = mock_publish.call_args.kwargs
        assert call_kwargs["dry_run"] is True
        assert "card" in result.stdout


class TestVersionCommand:
    def test_version(self) -> None:
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "gap" in result.stdout


class TestAppHelp:
    def test_app_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Global Allocation Portfolio" in result.stdout

    def test_strategy_help(self) -> None:
        result = runner.invoke(app, ["strategy", "--help"])
        assert result.exit_code == 0
        assert "list" in result.stdout

    def test_data_help(self) -> None:
        result = runner.invoke(app, ["data", "--help"])
        assert result.exit_code == 0
        assert "fetch" in result.stdout
