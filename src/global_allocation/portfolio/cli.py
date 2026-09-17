"""实盘账本 CLI。

参照 specs/090-portfolio-journal.md。
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from global_allocation.models import AssetClass
from global_allocation.portfolio.db import PortfolioDB
from global_allocation.portfolio.journal import PortfolioJournal
from global_allocation.portfolio.models import TransactionSide
from global_allocation.portfolio.valuation import (
    AkshareFundPriceSource,
    PriceSource,
)

app = typer.Typer(help="实盘持仓账本：管理基金 / 交易 / 周快照 / 周报。")
fund_app = typer.Typer(help="管理基金。", invoke_without_command=True)
tx_app = typer.Typer(help="查看交易流水。")
app.add_typer(fund_app, name="fund")
app.add_typer(tx_app, name="tx")

console = Console()


# ─── 默认 db / price 注入（测试用 monkeypatch） ───


def _default_db_path() -> Path:
    """默认 SQLite 路径：$XDG_DATA_HOME/gap/portfolio.db 或 ~/.local/share/gap/portfolio.db。"""
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "gap" / "portfolio.db"


def _default_db() -> PortfolioDB:
    return PortfolioDB(path=_default_db_path())


def _default_price_source() -> PriceSource:
    """生产用 akshare。"""
    return AkshareFundPriceSource()


def _default_journal() -> PortfolioJournal:
    return PortfolioJournal(db=_default_db(), price_source=_default_price_source())


# ─── helpers ───


def _parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise typer.BadParameter(f"日期格式应为 YYYY-MM-DD，实际 {s}") from e


def _parse_decimal(s: str, name: str) -> Decimal:
    try:
        return Decimal(s)
    except InvalidOperation as e:
        raise typer.BadParameter(f"{name} 应为数字，实际 {s}") from e


def _parse_asset_class(s: str) -> AssetClass:
    try:
        return AssetClass(s)
    except ValueError as e:
        valid = ", ".join(a.value for a in AssetClass)
        raise typer.BadParameter(f"asset-class 应为 {valid}，实际 {s}") from e


# ─── commands ───


@app.command("init")
def cmd_init(
    name: str = typer.Option(..., "--name", help="组合名"),
) -> None:
    """初始化组合（一次性）。"""
    db_path = _default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = PortfolioDB(path=db_path)
    db.close()
    console.print(f"[green]✓[/green] 已创建 portfolio db：{db_path}")
    console.print(f"组合名：{name}")


@app.command("show")
def cmd_show() -> None:
    """显示当前持仓 + 浮动盈亏。"""
    journal = _default_journal()
    holdings = journal.compute_holdings()
    if not holdings:
        console.print("[yellow]暂无持仓[/yellow]")
        return

    table = Table(title="当前持仓", show_header=True, header_style="bold blue")
    table.add_column("基金", style="cyan")
    table.add_column("份额", justify="right")
    table.add_column("成本均价", justify="right")
    table.add_column("最新价", justify="right")
    table.add_column("市值", justify="right")
    table.add_column("占比", justify="right")
    table.add_column("浮动盈亏", justify="right")
    table.add_column("盈亏 %", justify="right")

    total = sum((h.market_value for h in holdings if h.market_value is not None), Decimal("0"))
    for h in holdings:
        weight = (h.market_value / total * Decimal("100")) if total > 0 and h.market_value is not None else Decimal("0")
        pnl_pct_str = (
            f"{float(h.unrealized_pnl_pct) * 100:+.2f}%"
            if h.unrealized_pnl_pct is not None
            else "n/a"
        )
        pnl_str = (
            f"{float(h.unrealized_pnl):+,.2f}"
            if h.unrealized_pnl is not None
            else "n/a"
        )
        mv_str = (
            f"{float(h.market_value):,.2f}"
            if h.market_value is not None
            else "n/a"
        )
        table.add_row(
            f"{h.fund.code}\n{h.fund.name}",
            f"{float(h.shares):,.2f}",
            f"{float(h.avg_cost):.4f}",
            f"{float(h.market_price):.4f}" if h.market_price is not None else "n/a",
            mv_str,
            f"{float(weight):.2f}%",
            pnl_str,
            pnl_pct_str,
        )
    console.print(table)
    console.print(f"\n[bold]总市值：{float(total):,.2f} CNY[/bold]")


@app.command("import")
def cmd_import(
    file: Annotated[Path, typer.Option("--file", "-f", help="JSON 文件路径")],
    asset_class_default: str = typer.Option(
        "mixed",
        "--asset-class-default",
        help="JSON 里没指定 asset_class 时的默认（equity/bond/commodity/reit/cash/mixed）",
    ),
) -> None:
    """从 JSON 文件批量导入当前持仓（券商截图 → 落库）。

    JSON 格式（每条）：
      {"code": "...", "name": "...", "asset_class": "equity",
       "current_value": "...", "cumulative_pnl": "..."}

    asset_class 可省略（用 --asset-class-default）。
    """
    import json as _json

    if not file.exists():
        console.print(f"[red]✗[/red] 文件不存在：{file}")
        raise typer.Exit(code=1)

    try:
        data = _json.loads(file.read_text(encoding="utf-8"))
    except _json.JSONDecodeError as e:
        console.print(f"[red]✗[/red] JSON 解析失败：{e}")
        raise typer.Exit(code=1) from e

    if not isinstance(data, list):
        console.print("[red]✗[/red] JSON 顶层必须是数组")
        raise typer.Exit(code=1)

    default_ac = _parse_asset_class(asset_class_default)
    holdings: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        ac_str = row.get("asset_class")
        try:
            ac = _parse_asset_class(ac_str) if ac_str else default_ac
        except typer.BadParameter:
            ac = default_ac
        holdings.append(
            {
                "code": str(row["code"]),
                "name": str(row["name"]),
                "asset_class": ac,
                "current_value": str(row.get("current_value", "0")),
                "cumulative_pnl": str(row.get("cumulative_pnl", "0")),
            }
        )

    journal = _default_journal()
    try:
        n = journal.import_holdings(holdings)
    except Exception as e:
        console.print(f"[red]✗[/red] 导入失败：{e}")
        raise typer.Exit(code=1) from e

    skipped = len(holdings) - n
    console.print(f"[green]✓[/green] 已导入 {n} 只基金")
    if skipped > 0:
        console.print(f"[yellow]![/yellow] 跳过 {skipped} 条（current_value=0 或拿不到当前价）")
    console.print("提示：cost_basis 起点 = 当前市值；之后所有盈亏从这次开始跟踪。")
    console.print("      历史的累计盈亏暂不入库（合成 buy 在当前价买入）。")


@app.command("snapshot")
def cmd_snapshot(
    on: str = typer.Option(
        ..., "--date", help="快照日期 YYYY-MM-DD（默认今天）"
    ),
) -> None:
    """拍一张快照（默认拍到指定日期）。"""
    trade_date = _parse_date(on)
    journal = _default_journal()
    snap = journal.take_snapshot(week_end_date=trade_date)
    console.print(
        f"[green]✓[/green] 快照 {snap.week_end_date}：总值 {float(snap.total_value):,.2f} CNY"
    )


@app.command("report")
def cmd_report(
    weekly: bool = typer.Option(False, "--weekly", help="本周周报"),
) -> None:
    """看持仓报告（默认）/ 周报（--weekly）。"""
    journal = _default_journal()
    holdings = journal.compute_holdings()
    if not holdings:
        console.print("[yellow]暂无持仓[/yellow]")
        return

    total = sum((h.market_value for h in holdings if h.market_value is not None), Decimal("0"))
    total_cost = sum((h.cost_basis for h in holdings), Decimal("0"))
    total_pnl = total - total_cost
    total_pnl_pct = (total_pnl / total_cost) if total_cost > 0 else Decimal("0")

    if weekly:
        latest = journal.get_latest_snapshot()
        if latest:
            console.print(f"上周快照：{latest.week_end_date}，总值 {float(latest.total_value):,.2f}")
            console.print(f"本周累计回报：{float(latest.cumulative_return) * 100:+.2f}%")
    console.print(f"总成本：{float(total_cost):,.2f} CNY")
    console.print(f"总市值：{float(total):,.2f} CNY")
    console.print(f"总浮动盈亏：{float(total_pnl):+,.2f} CNY ({float(total_pnl_pct) * 100:+.2f}%)")


@app.command("publish")
def cmd_publish(
    weekly: bool = typer.Option(False, "--weekly", help="周报模式（先拍快照再发）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只生成卡片 JSON，不真发"),
    chat: str | None = typer.Option(None, "--chat", help="覆盖默认 chat_id"),
    root: str | None = typer.Option(
        None, "--root", help="话题根消息 id（发到话题 thread 而不是父群）"
    ),
    title: str | None = typer.Option(None, "--title", help="卡片标题"),
) -> None:
    """发飞书卡片（实盘账本 + 周快照）。"""
    from datetime import date as _date

    journal = _default_journal()
    holdings = journal.compute_holdings()
    if not holdings:
        console.print("[yellow]暂无持仓，无可发内容[/yellow]")
        return

    # --weekly 模式：先拍一张今天（或上一交易日）的快照
    if weekly:
        try:
            journal.take_snapshot(week_end_date=_date.today())
            console.print("[green]✓[/green] 已拍最新快照")
        except ValueError as e:
            console.print(f"[yellow]![/yellow] 跳过快照：{e}")

    from global_allocation.portfolio.publisher import publish_portfolio_report

    try:
        result = publish_portfolio_report(
            journal,
            chat_id=chat,
            root_id=root,
            title=title,
            dry_run=dry_run,
        )
    except Exception as e:
        console.print(f"[red]✗[/red] 发送失败：{e}")
        raise typer.Exit(code=1) from e

    if dry_run:
        console.print("[cyan]dry-run[/cyan] 卡片 JSON（前 200 字符）：")
        console.print(result[:200] + ("..." if len(result) > 200 else ""))
    else:
        console.print(f"[green]✓[/green] 已发送：message_id = {result}")


# ─── fund subcommand ───


@fund_app.command("add")
def cmd_fund_add(
    code: str = typer.Argument(..., help="基金代码"),
    name: str = typer.Option(..., "--name", help="基金名"),
    asset_class: str = typer.Option(
        ..., "--asset-class", help="equity / bond / commodity / reit / cash / mixed"
    ),
) -> None:
    """注册一个基金。"""
    journal = _default_journal()
    fund = journal.add_fund(code=code, name=name, asset_class=_parse_asset_class(asset_class))
    console.print(f"[green]✓[/green] 已添加：{fund.code} {fund.name} ({fund.asset_class.value})")


@fund_app.command("list")
def cmd_fund_list() -> None:
    """列出已登记的基金。"""
    journal = _default_journal()
    funds = journal.list_funds()
    if not funds:
        console.print("[yellow]暂无基金[/yellow]")
        return
    table = Table(title="已登记基金", show_header=True, header_style="bold blue")
    table.add_column("代码", style="cyan")
    table.add_column("名称")
    table.add_column("类型")
    table.add_column("数据源")
    for f in funds:
        table.add_row(f.code, f.name, f.asset_class.value, f.data_source.value)
    console.print(table)


# ─── buy / sell ───


def _record_cmd(side: TransactionSide) -> Any:
    """buy / sell 共用的下层函数。"""

    def _inner(
        fund_code: Annotated[str, typer.Argument(help="基金代码")],
        trade_date: Annotated[str, typer.Option("--date", help="交易日 YYYY-MM-DD")],
        shares: Annotated[str, typer.Option("--shares", help="份额")],
        price: Annotated[str, typer.Option("--price", help="成交单价")],
        fee: Annotated[str, typer.Option("--fee", help="手续费")] = "0",
        strategy: Annotated[str | None, typer.Option("--strategy", help="策略说明")] = None,
        tags: Annotated[list[str] | None, typer.Option("--tags", help="标签（可多个）")] = None,
        note: Annotated[str | None, typer.Option("--note", help="备注")] = None,
    ) -> None:
        journal = _default_journal()
        tx_date = _parse_date(trade_date)
        shares_dec = _parse_decimal(shares, "shares")
        price_dec = _parse_decimal(price, "price")
        fee_dec = _parse_decimal(fee, "fee")
        try:
            if side == TransactionSide.BUY:
                tx = journal.record_buy(
                    fund_code=fund_code,
                    trade_date=tx_date,
                    shares=shares_dec,
                    price=price_dec,
                    fee=fee_dec,
                    strategy=strategy,
                    tags=tags,
                    note=note,
                )
            else:
                tx = journal.record_sell(
                    fund_code=fund_code,
                    trade_date=tx_date,
                    shares=shares_dec,
                    price=price_dec,
                    fee=fee_dec,
                    strategy=strategy,
                    tags=tags,
                    note=note,
                )
        except ValueError as e:
            console.print(f"[red]✗[/red] {e}")
            raise typer.Exit(code=1) from e
        console.print(
            f"[green]✓[/green] 已记录 {side.value} {tx.fund_code} "
            f"{float(tx.shares):,.2f} 份 @ {float(tx.price):.4f}"
        )
        if tx.strategy:
            console.print(f"  策略：{tx.strategy}")
        if tx.tags:
            console.print(f"  标签：{', '.join(tx.tags)}")

    return _inner


app.command("buy")(_record_cmd(TransactionSide.BUY))
app.command("sell")(_record_cmd(TransactionSide.SELL))


# ─── tx subcommand ───


@tx_app.command("list")
def cmd_tx_list(
    fund: str | None = typer.Option(None, "--fund", help="按基金过滤"),
    tag: str | None = typer.Option(None, "--tag", help="按标签过滤"),
    strategy: str | None = typer.Option(None, "--strategy", help="按策略过滤"),
) -> None:
    """列出所有交易。"""
    journal = _default_journal()
    txs = journal.list_transactions(fund_code=fund, tag=tag, strategy=strategy)
    if not txs:
        console.print("[yellow]暂无交易[/yellow]")
        return
    table = Table(title="交易流水", show_header=True, header_style="bold blue")
    table.add_column("日期", style="cyan")
    table.add_column("方向")
    table.add_column("基金")
    table.add_column("份额", justify="right")
    table.add_column("价格", justify="right")
    table.add_column("手续费", justify="right")
    table.add_column("策略")
    table.add_column("标签")
    for tx in txs:
        side_color = "green" if tx.side == TransactionSide.BUY else "red"
        table.add_row(
            tx.date.isoformat(),
            f"[{side_color}]{tx.side.value}[/{side_color}]",
            tx.fund_code,
            f"{float(tx.shares):,.2f}",
            f"{float(tx.price):.4f}",
            f"{float(tx.fee):.2f}",
            tx.strategy or "",
            ", ".join(tx.tags),
        )
    console.print(table)


__all__ = ["app"]
