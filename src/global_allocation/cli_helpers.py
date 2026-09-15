"""CLI 共享工具：周期解析、回测快捷入口、Rich 输出。"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.table import Table

from global_allocation.backtest.engine import BacktestEngine
from global_allocation.data.fetcher import fetch_many
from global_allocation.models import Strategy
from global_allocation.strategies.registry import get_strategy

_console = Console()


def get_console() -> Console:
    """全局 Rich console。"""
    return _console


def parse_period(period: str) -> tuple[date, date]:
    """把 '1y'/'5y'/'10y'/'max' 解析成 (start, end)。"""
    today = date.today()
    mapping = {
        "1y": timedelta(days=365),
        "3y": timedelta(days=365 * 3),
        "5y": timedelta(days=365 * 5),
        "10y": timedelta(days=365 * 10),
    }
    if period == "max":
        start = date(1990, 1, 1)
    elif period in mapping:
        start = today - mapping[period]
    else:
        raise ValueError(
            f"不支持的 period: {period}。可选: 1y / 3y / 5y / 10y / max"
        )
    return start, today


def load_strategy(strategy_id: str) -> Strategy:
    """加载策略：内置 ID 或 YAML 文件路径。"""
    path = Path(strategy_id)
    if path.exists() and path.suffix in {".yaml", ".yml"}:
        return _load_strategy_from_yaml(path)
    # 内置
    try:
        cls = get_strategy(strategy_id)
    except KeyError as e:
        raise ValueError(str(e)) from e
    instance = cls()
    instance.validate()
    return instance.build()


def _load_strategy_from_yaml(path: Path) -> Strategy:
    """从 YAML 文件加载策略。"""
    from global_allocation.models import (
        Asset,
        AssetClass,
        Currency,
        DataSource,
        RebalanceRule,
        Region,
        Strategy,
        TargetWeight,
    )

    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))

    target_weights: list[TargetWeight] = []
    for tw in data.get("target_weights", []):
        asset_data = tw["asset"]
        asset = Asset(
            symbol=asset_data["symbol"],
            name=asset_data["name"],
            asset_class=AssetClass(asset_data["asset_class"]),
            region=Region(asset_data["region"]),
            currency=Currency(asset_data["currency"]),
            data_source=DataSource(asset_data.get("data_source", "yfinance")),
        )
        target_weights.append(
            TargetWeight(asset=asset, weight=Decimal(str(tw["weight"])))
        )

    rebalance_data = data["rebalance"]
    rebalance = RebalanceRule(
        frequency=rebalance_data["frequency"],
        threshold=(
            Decimal(str(rebalance_data["threshold"]))
            if rebalance_data.get("threshold") is not None
            else None
        ),
    )

    inception_raw = data["inception"]
    if isinstance(inception_raw, date):
        inception_date = inception_raw
    else:
        inception_date = date.fromisoformat(str(inception_raw))

    return Strategy(
        id=data["id"],
        name=data["name"],
        description=data.get("description", ""),
        target_weights=target_weights,
        rebalance=rebalance,
        base_currency=Currency(data["base_currency"]),
        inception=inception_date,
        metadata=data.get("metadata", {}),
    )


def run_backtest(
    strategy: Strategy,
    period: str = "5y",
    start: date | None = None,
    end: date | None = None,
    capital: Decimal = Decimal("100000"),
    cost_bps: Decimal = Decimal("10"),
) -> Any:
    """端到端：加载数据 + 跑回测。"""
    if start is None or end is None:
        start, end = parse_period(period)

    assets = [tw.asset for tw in strategy.target_weights]
    prices = fetch_many(assets, start, end, how="inner")

    engine = BacktestEngine(
        initial_capital=capital,
        cost_bps=cost_bps,
    )
    return engine.run(strategy, prices)


def print_metrics_table(results: list[tuple[str, Any]], console: Console | None = None) -> None:
    """打印多个策略的指标对比表。"""
    console = console or _console
    table = Table(show_header=True, header_style="bold")
    table.add_column("策略", style="cyan")
    table.add_column("CAGR", justify="right")
    table.add_column("夏普", justify="right")
    table.add_column("最大回撤", justify="right")
    table.add_column("波动率", justify="right")
    table.add_column("总收益", justify="right")

    for name, result in results:
        m = result.metrics
        table.add_row(
            name,
            _fmt_pct(m.cagr),
            _fmt_dec(m.sharpe),
            _fmt_pct(m.max_drawdown),
            _fmt_pct(m.volatility),
            _fmt_pct(m.total_return),
        )
    console.print(table)


def print_single_result(result: Any, console: Console | None = None) -> None:
    """打印单个回测结果的详细指标。"""
    console = console or _console
    m = result.metrics

    console.rule(f"[bold]{result.strategy_id}[/bold] 回测结果")
    console.print(f"周期：[cyan]{result.start_date}[/cyan] → [cyan]{result.end_date}[/cyan]")
    console.print(f"初始资金：[green]{_fmt_dec(result.initial_capital)}[/green]")
    console.print(f"终值：[green]{_fmt_dec(result.final_value)}[/green]")
    console.print(f"总收益：[green]{_fmt_pct(m.total_return)}[/green]")
    console.print(f"CAGR：[green]{_fmt_pct(m.cagr)}[/green]")
    console.print(f"夏普：[yellow]{_fmt_dec(m.sharpe)}[/yellow]")
    console.print(f"最大回撤：[red]{_fmt_pct(m.max_drawdown)}[/red]")
    console.print(f"年化波动率：[yellow]{_fmt_pct(m.volatility)}[/yellow]")
    console.print(f"再平衡事件：[magenta]{len(result.rebalance_events)}[/magenta] 次")


def _fmt_pct(value: Any, decimals: int = 2) -> str:
    v = float(value) * 100
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.{decimals}f}%"


def _fmt_dec(value: Any, decimals: int = 2) -> str:
    return f"{float(value):,.{decimals}f}"


__all__ = [
    "get_console",
    "parse_period",
    "load_strategy",
    "run_backtest",
    "print_metrics_table",
    "print_single_result",
]
