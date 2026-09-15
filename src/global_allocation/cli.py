"""CLI 入口（Typer）。

参照 specs/070-cli.md。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer

from global_allocation.cli_helpers import (
    get_console,
    load_strategy,
    print_metrics_table,
    print_single_result,
    run_backtest,
)
from global_allocation.data.fetcher import get_default_cache
from global_allocation.models import (
    Asset,
    AssetClass,
    Currency,
    DataSource,
    Region,
)

app = typer.Typer(
    name="gap",
    help="Global Allocation Portfolio — 策略、回测、飞书报告",
    no_args_is_help=True,
    add_completion=False,
)

strategy_app = typer.Typer(help="管理策略", no_args_is_help=True)
data_app = typer.Typer(help="管理数据", no_args_is_help=True)
app.add_typer(strategy_app, name="strategy")
app.add_typer(data_app, name="data")

# 实盘持仓账本
from global_allocation.portfolio.cli import app as portfolio_app  # noqa: E402

app.add_typer(portfolio_app, name="portfolio")


# ────────────────────────────────────────────────────────────────────
# 顶层
# ────────────────────────────────────────────────────────────────────


@app.command()
def version() -> None:
    """显示版本号。"""
    from global_allocation import __version__

    typer.echo(f"gap {__version__}")


# ────────────────────────────────────────────────────────────────────
# strategy 子命令
# ────────────────────────────────────────────────────────────────────


@strategy_app.command("list")
def strategy_list() -> None:
    """列出所有内置策略。"""
    from global_allocation.strategies.registry import STRATEGY_REGISTRY

    console = get_console()
    from rich.table import Table

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("名称")
    table.add_column("说明", style="dim")

    for sid in sorted(STRATEGY_REGISTRY):
        cls = STRATEGY_REGISTRY[sid]
        table.add_row(cls.id, cls.name, cls.description[:50] + "...")

    console.print(table)


@strategy_app.command("show")
def strategy_show(
    strategy_id: Annotated[
        str,
        typer.Argument(help="策略 ID 或 YAML 文件路径"),
    ],
) -> None:
    """显示策略详情。"""
    try:
        strategy = load_strategy(strategy_id)
    except (ValueError, FileNotFoundError) as e:
        typer.echo(f"错误：{e}", err=True)
        raise typer.Exit(code=1) from e

    console = get_console()
    from rich.table import Table

    console.rule(f"[bold]{strategy.name}[/bold] ({strategy.id})")
    console.print(strategy.description)
    console.print(f"\n[cyan]基准货币[/cyan]：{strategy.base_currency.value}")
    console.print(f"[cyan]起始日[/cyan]：{strategy.inception}")
    console.print(
        f"[cyan]再平衡[/cyan]：{strategy.rebalance.frequency}"
        + (
            f"（阈值 {strategy.rebalance.threshold}）"
            if strategy.rebalance.threshold
            else ""
        )
    )

    table = Table(show_header=True, header_style="bold")
    table.add_column("标的", style="cyan")
    table.add_column("类别")
    table.add_column("地域")
    table.add_column("权重", justify="right")

    for tw in strategy.target_weights:
        table.add_row(
            tw.asset.symbol,
            tw.asset.asset_class.value,
            tw.asset.region.value,
            f"{float(tw.weight) * 100:.2f}%",
        )

    console.print(table)


@strategy_app.command("validate")
def strategy_validate(
    yaml_path: Annotated[Path, typer.Argument(help="YAML 文件路径", exists=True)],
) -> None:
    """校验 YAML 策略文件。"""
    try:
        strategy = load_strategy(str(yaml_path))
        typer.echo(f"✓ 策略 '{strategy.id}' 校验通过")
    except Exception as e:
        typer.echo(f"✗ 校验失败：{e}", err=True)
        raise typer.Exit(code=1) from e


# ────────────────────────────────────────────────────────────────────
# data 子命令
# ────────────────────────────────────────────────────────────────────


@data_app.command("list")
def data_list() -> None:
    """列出已缓存的标的。"""
    cache = get_default_cache()
    pairs = cache.list_symbols()

    if not pairs:
        typer.echo("缓存为空")
        return

    from rich.table import Table

    table = Table(show_header=True, header_style="bold")
    table.add_column("标的", style="cyan")
    table.add_column("数据源")

    for symbol, source in sorted(pairs):
        table.add_row(symbol, source)

    get_console().print(table)


@data_app.command("clear")
def data_clear(
    symbol: Annotated[
        str | None,
        typer.Argument(help="标的代码（不传则清全部）"),
    ] = None,
    all_: Annotated[
        bool,
        typer.Option("--all", help="清空所有缓存"),
    ] = False,
) -> None:
    """清缓存。"""
    cache = get_default_cache()

    if all_:
        cache.clear_all()
        typer.echo("✓ 已清空所有缓存")
    elif symbol:
        # 需要数据源——这里只清 yfinance 和 akshare 两个
        for source in (DataSource.YFINANCE, DataSource.AKSHARE):
            cache.clear(symbol, source.value)
        typer.echo(f"✓ 已清 {symbol} 缓存")
    else:
        typer.echo("错误：需要传 symbol 或 --all", err=True)
        raise typer.Exit(code=1)


@data_app.command("fetch")
def data_fetch(
    symbol: Annotated[str, typer.Argument(help="标的代码")],
    period: Annotated[
        str, typer.Option("--period", help="1y/3y/5y/10y/max")
    ] = "5y",
    source: Annotated[
        str, typer.Option("--source", help="yfinance/akshare（默认自动）")
    ] = "auto",
    refresh: Annotated[bool, typer.Option("--refresh", help="强制重新拉")] = False,
) -> None:
    """拉取并缓存历史数据。"""
    from global_allocation.cli_helpers import parse_period
    from global_allocation.data.fetcher import fetch_ohlcv

    start, end = parse_period(period)

    # 自动选 source
    if source == "auto":
        if symbol.endswith((".SH", ".SZ")) or symbol.isdigit():
            ds = DataSource.AKSHARE
        else:
            ds = DataSource.YFINANCE
    else:
        ds = DataSource(source)

    # 构造 Asset（最小信息）
    asset = Asset(
        symbol=symbol,
        name=symbol,
        asset_class=AssetClass.EQUITY,
        region=(
            Region.CN if symbol.endswith((".SH", ".SZ"))
            else Region.US
        ),
        currency=(
            Currency.CNY if symbol.endswith((".SH", ".SZ"))
            else Currency.USD
        ),
        data_source=ds,
    )

    df = fetch_ohlcv(asset, start, end, refresh=refresh)
    typer.echo(
        f"✓ {symbol} 拉取完成：{len(df)} 行 "
        f"({df.index[0].date()} → {df.index[-1].date()})"
    )


# ────────────────────────────────────────────────────────────────────
# 顶层 backtest / compare / publish
# ────────────────────────────────────────────────────────────────────


@app.command()
def backtest(
    strategy_id: Annotated[str, typer.Argument(help="策略 ID 或 YAML 路径")],
    period: Annotated[
        str, typer.Option("--period", help="1y/3y/5y/10y/max")
    ] = "5y",
    start: Annotated[
        str | None, typer.Option("--start", help="起始日期 YYYY-MM-DD")
    ] = None,
    end: Annotated[
        str | None, typer.Option("--end", help="结束日期 YYYY-MM-DD")
    ] = None,
    capital: Annotated[
        str, typer.Option("--capital", help="初始资金")
    ] = "100000",
    cost_bps: Annotated[
        str, typer.Option("--cost-bps", help="双边手续费 (bps)")
    ] = "10",
) -> None:
    """跑单个策略回测。"""
    try:
        strategy = load_strategy(strategy_id)
    except (ValueError, FileNotFoundError) as e:
        typer.echo(f"错误：{e}", err=True)
        raise typer.Exit(code=1) from e

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    try:
        result = run_backtest(
            strategy,
            period=period,
            start=start_d,
            end=end_d,
            capital=Decimal(capital),
            cost_bps=Decimal(cost_bps),
        )
    except Exception as e:
        typer.echo(f"回测失败：{e}", err=True)
        raise typer.Exit(code=2) from e

    print_single_result(result)


@app.command()
def compare(
    strategies: Annotated[
        list[str], typer.Argument(help="策略 ID 或 YAML 路径（多个）")
    ],
    period: Annotated[
        str, typer.Option("--period", help="1y/3y/5y/10y/max")
    ] = "5y",
    capital: Annotated[
        str, typer.Option("--capital", help="初始资金")
    ] = "100000",
    cost_bps: Annotated[
        str, typer.Option("--cost-bps", help="双边手续费 (bps)")
    ] = "10",
) -> None:
    """对比多个策略回测结果。"""
    results: list[tuple[str, object]] = []
    cap = Decimal(capital)
    cb = Decimal(cost_bps)
    for sid in strategies:
        try:
            strategy = load_strategy(sid)
            result = run_backtest(
                strategy, period=period, capital=cap, cost_bps=cb
            )
            results.append((strategy.name, result))
        except Exception as e:
            typer.echo(f"⚠ {sid} 失败：{e}", err=True)

    if not results:
        typer.echo("没有任何策略跑成功", err=True)
        raise typer.Exit(code=2)

    print_metrics_table(results)


@app.command()
def publish(
    strategy_id: Annotated[str, typer.Argument(help="策略 ID 或 YAML 路径")],
    period: Annotated[
        str, typer.Option("--period", help="1y/3y/5y/10y/max")
    ] = "5y",
    title: Annotated[
        str | None, typer.Option("--title", help="卡片标题")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="只生成卡片 JSON，不真发")
    ] = False,
) -> None:
    """回测并发送飞书卡片报告。"""
    from global_allocation.feishu.publisher import publish_backtest_report

    try:
        strategy = load_strategy(strategy_id)
        result = run_backtest(strategy, period=period)
    except Exception as e:
        typer.echo(f"回测失败：{e}", err=True)
        raise typer.Exit(code=2) from e

    try:
        output = publish_backtest_report(
            result, title=title, dry_run=dry_run
        )
    except Exception as e:
        typer.echo(f"飞书发送失败：{e}", err=True)
        typer.echo("提示：需要配置 FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CHAT_ID", err=True)
        raise typer.Exit(code=2) from e

    if dry_run:
        typer.echo(output)
    else:
        typer.echo(f"✓ 已发送，message_id={output}")


@app.command()
def strategies() -> None:
    """列出内置策略（strategy list 的别名）。"""
    strategy_list()


__all__ = ["app"]
