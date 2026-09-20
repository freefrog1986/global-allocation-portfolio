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
from global_allocation.portfolio.models import (
    FundValuation,
    TransactionSide,
    ValuationIndicator,
    ValuationIndicatorCode,
)
from global_allocation.portfolio.valuation import (
    AkshareFundPriceSource,
    PriceSource,
)
from global_allocation.portfolio.valuation_source import AkshareValuationSource

app = typer.Typer(help="实盘持仓账本：管理基金 / 交易 / 周快照 / 周报。")
fund_app = typer.Typer(help="管理基金。", invoke_without_command=True)
tx_app = typer.Typer(help="查看交易流水。")
valuation_app = typer.Typer(help="管理大类资产估值（A 股 4 个指标）。")
app.add_typer(fund_app, name="fund")
app.add_typer(tx_app, name="tx")
app.add_typer(valuation_app, name="valuation")

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

    # spec 098 方案 A：自动拉估值（如缺数据）
    today = _date.today()
    if not _has_complete_valuation_for_date(today):
        try:
            n = _update_valuation_for_date(today)
            if n == 4:
                console.print("[green]✓[/green] 已拉新估值（4/4 指标）")
            elif n > 0:
                console.print(f"[yellow]![/yellow] 已拉新估值（{n}/4 指标，部分 akshare 接口失败）")
            else:
                console.print("[yellow]![/yellow] 估值拉取全失败，卡片 Section 3 将显示空")
        except Exception as e:
            console.print(f"[yellow]![/yellow] 估值拉取异常：{e}")

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


# ─── valuation subcommand（spec 098）───


def _update_valuation_for_date(target_date: date) -> int:
    """拉 target_date 当天的 4 个估值指标并入库（spec 098 方案 A：自动拉）。

    返回成功入库的指标数（0~4）。失败指标不抛异常，跳过。
    spec 098 第 175 行：akshare 接口失败 → 跳过该指标，卡片显示"数据缺失"。
    """
    from global_allocation.portfolio.valuation_indicators import (
        compute_buffett_indicator,
        compute_equity_risk_premium,
        compute_pe_percentile,
    )

    db = _default_db()
    src = AkshareValuationSource()
    saved = 0

    # 1. 股债利差（需要 PE-TTM + 10Y 国债）
    pe = src.get_pe_ttm(target_date)
    y = src.get_10y_treasury_yield(target_date)
    if pe is not None and y is not None:
        try:
            erp = compute_equity_risk_premium(pe, y)
            db.upsert_valuation_indicator(
                ValuationIndicator(
                    record_date=target_date,
                    indicator_code=ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
                    value=erp,
                    source="akshare:stock_zh_index_value_csindex+bond_zh_us_rate",
                )
            )
            saved += 1
        except ValueError as e:
            console.print(f"[yellow]![/yellow] 股债利差计算失败：{e}")

    # 2. PE 分位（需要当前 PE + 历史序列）
    history = src.get_pe_history(years=10)
    if pe is not None and history:
        try:
            pct = compute_pe_percentile(pe, history)
            db.upsert_valuation_indicator(
                ValuationIndicator(
                    record_date=target_date,
                    indicator_code=ValuationIndicatorCode.PE_PERCENTILE,
                    value=pct,
                    source="akshare:stock_a_ttm_lyr",
                )
            )
            saved += 1
        except ValueError as e:
            console.print(f"[yellow]![/yellow] PE 分位计算失败：{e}")

    # 3. 巴菲特指标（需要 A 股总市值 + GDP）
    cap = src.get_a_share_total_market_cap(target_date)
    gdp = src.get_china_gdp(target_date)
    if cap is not None and gdp is not None:
        try:
            bf = compute_buffett_indicator(cap, gdp)
            db.upsert_valuation_indicator(
                ValuationIndicator(
                    record_date=target_date,
                    indicator_code=ValuationIndicatorCode.BUFFETT_INDICATOR,
                    value=bf,
                    source="akshare:stock_zh_a_spot_em+macro_china_gdp",
                )
            )
            saved += 1
        except ValueError as e:
            console.print(f"[yellow]![/yellow] 巴菲特指标计算失败：{e}")

    # 4. 股息率（直接拿，不需要 compute）
    dy = src.get_dividend_yield(target_date)
    if dy is not None:
        db.upsert_valuation_indicator(
            ValuationIndicator(
                record_date=target_date,
                indicator_code=ValuationIndicatorCode.DIVIDEND_YIELD,
                value=dy,
                source="akshare:stock_zh_index_value_csindex",
            )
        )
        saved += 1

    return saved


def _has_complete_valuation_for_date(target_date: date) -> bool:
    """检查 target_date 当天的 4 个指标是否都齐全（spec 098 第 167 行）。"""
    db = _default_db()
    return len(db.list_valuation_indicators_for_date(target_date)) >= 4


@valuation_app.command("update")
def cmd_valuation_update(
    on: str | None = typer.Option(
        None, "--date", help="日期 YYYY-MM-DD（默认今天）"
    ),
) -> None:
    """拉最新的 4 个估值指标并入库（手动刷新）。"""
    from datetime import date as _date

    target_date = _parse_date(on) if on else _date.today()
    n = _update_valuation_for_date(target_date)
    if n == 4:
        console.print(f"[green]✓[/green] {target_date} 全部 4 个指标已入库")
    elif n > 0:
        console.print(
            f"[yellow]![/yellow] {target_date} 只入库 {n}/4 个指标（部分 akshare 接口失败）"
        )
    else:
        console.print(f"[red]✗[/red] {target_date} 全部指标拉取失败（akshare 可能不可用）")
        raise typer.Exit(code=1)


@valuation_app.command("show")
def cmd_valuation_show() -> None:
    """显示数据库里最新一天的估值指标 + 评估 + 综合分（spec 098 第二十二轮）。

    spec 098.2：港股加 4 个指标后改成两段（A 股 + 港股），每段 4 指标 + 综合分。
    """
    from global_allocation.portfolio.valuation_indicators import (
        compute_composite_score,
        compute_verdict,
        interpret_composite_score,
        score_indicator,
    )

    db = _default_db()
    inds = db.list_latest_valuation_indicators()
    if not inds:
        console.print("[yellow]暂无估值数据（先跑 gap valuation update）[/yellow]")
        return

    record_date = inds[0].record_date
    console.print(f"[bold]估值日期：{record_date}[/bold]\n")

    # 按 code 分桶（A 股 vs 港股）— spec 098.2 把两类指标分两段展示
    by_code = {i.indicator_code: i for i in inds}
    a_share_codes = {
        ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
        ValuationIndicatorCode.PE_PERCENTILE,
        ValuationIndicatorCode.BUFFETT_INDICATOR,
        ValuationIndicatorCode.DIVIDEND_YIELD,
    }
    hk_codes = {
        ValuationIndicatorCode.HK_PE_PERCENTILE,
        ValuationIndicatorCode.HK_DIVIDEND_YIELD,
        ValuationIndicatorCode.HK_AH_PREMIUM,
        ValuationIndicatorCode.HK_BUFFETT_INDICATOR,
    }

    # 渲染单段（A 股 或 港股）：返回是否实际渲染
    def _render_section(
        title: str,
        codes: list[ValuationIndicatorCode],
        labels: dict[ValuationIndicatorCode, tuple[str, str, str]],
        short_names: dict[ValuationIndicatorCode, str],
    ) -> bool:
        """渲染一段（4 指标 + 综合分），返回该段是否有任何数据。

        没数据 → 返回 False（让 caller 决定要不要打 section header）。
        """
        has_any = any(c in by_code for c in codes)
        if not has_any:
            return False

        console.print(f"[bold]{title}[/bold]")
        table = Table(show_header=True, header_style="bold blue")
        table.add_column("指标", style="cyan")
        table.add_column("当前", justify="right")
        table.add_column("评估", justify="center")
        table.add_column("阈值", justify="left")

        scores: dict[ValuationIndicatorCode, int] = {}
        for code in codes:
            name, _, threshold = labels[code]
            if code not in by_code:
                table.add_row(name, "数据缺失", "n/a", threshold)
                continue
            ind = by_code[code]
            current = _format_indicator_value_for_cli(code, ind.value)
            verdict = compute_verdict(code, ind.value)
            verdict_color = {
                "偏低估": "green",
                "正常": "yellow",
                "偏高估": "red",
            }.get(verdict, "white")
            table.add_row(
                name,
                current,
                f"[{verdict_color}]{verdict}[/{verdict_color}]",
                threshold,
            )
            try:
                scores[code] = score_indicator(code, ind.value)
            except ValueError:
                pass

        # 综合分行（liubo 反馈"投票不加权"）
        if scores:
            score_list = [scores[c] for c in codes if c in scores]
            composite = compute_composite_score(score_list)
            verdict_str = interpret_composite_score(composite)
            parts = [
                f"{short_names[c]}:{scores[c]}" if c in scores else f"{short_names[c]}:-"
                for c in codes
            ]
            composite_value = "[" + " ".join(parts) + "]"
            verdict_color = {
                "极低": "bright_green",
                "低估": "green",
                "正常": "yellow",
                "偏高估": "red",
                "极高估": "bright_red",
            }.get(verdict_str, "white")
            table.add_row(
                "综合分",
                composite_value,
                f"[bold {verdict_color}]{float(composite):.1f} {verdict_str}[/bold {verdict_color}]",
                "1=极低估 5=极高估",
            )
        console.print(table)
        console.print()
        return True

    # A 股段
    a_share_labels = {
        ValuationIndicatorCode.EQUITY_RISK_PREMIUM: ("股债利差", "1/PE - 10Y国债", ">5% 低 / <2% 高"),
        ValuationIndicatorCode.PE_PERCENTILE: ("PE 分位", "10年百分位", "<30% 低 / >70% 高"),
        ValuationIndicatorCode.BUFFETT_INDICATOR: ("巴菲特指标", "市值/GDP", "<50% 低 / >80% 高"),
        ValuationIndicatorCode.DIVIDEND_YIELD: ("股息率", "分红/市值", ">3% 低 / <1% 高"),
    }
    a_share_short = {
        ValuationIndicatorCode.EQUITY_RISK_PREMIUM: "股债",
        ValuationIndicatorCode.PE_PERCENTILE: "PE",
        ValuationIndicatorCode.BUFFETT_INDICATOR: "巴菲特",
        ValuationIndicatorCode.DIVIDEND_YIELD: "股息",
    }
    _render_section(
        "A 股估值", list(a_share_codes), a_share_labels, a_share_short,
    )

    # 港股段（spec 098.2）
    hk_labels = {
        ValuationIndicatorCode.HK_PE_PERCENTILE: ("PE 分位", "10年百分位", "<30% 低 / >70% 高"),
        ValuationIndicatorCode.HK_DIVIDEND_YIELD: ("股息率", "分红/市值", ">3% 低 / <1% 高"),
        ValuationIndicatorCode.HK_AH_PREMIUM: ("AH 溢价", "A/H 价格比", ">150% 低估 / <40% 高估"),
        ValuationIndicatorCode.HK_BUFFETT_INDICATOR: ("港股巴菲特", "市值/GDP", "<800% 低估 / >1200% 高估"),
    }
    hk_short = {
        ValuationIndicatorCode.HK_PE_PERCENTILE: "PE",
        ValuationIndicatorCode.HK_DIVIDEND_YIELD: "股息",
        ValuationIndicatorCode.HK_AH_PREMIUM: "AH",
        ValuationIndicatorCode.HK_BUFFETT_INDICATOR: "港股巴",
    }
    _render_section(
        "港股估值", list(hk_codes), hk_labels, hk_short,
    )

    # 美股段（spec 098.3 — liubo 2026-09-20 拍板）
    us_codes = {
        ValuationIndicatorCode.US_PE_PERCENTILE,
        ValuationIndicatorCode.US_DIVIDEND_YIELD,
        ValuationIndicatorCode.US_BUFFETT_INDICATOR,
        ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM,
    }
    us_labels = {
        ValuationIndicatorCode.US_PE_PERCENTILE: ("PE 分位", "10年百分位", "<30% 低 / >70% 高"),
        ValuationIndicatorCode.US_DIVIDEND_YIELD: ("股息率", "分红/市值", ">3% 低 / <1% 高"),
        ValuationIndicatorCode.US_BUFFETT_INDICATOR: ("美股巴菲特", "市值/GDP", "<80% 低估 / >180% 高估"),
        ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM: ("股债利差", "1/PE - 美10Y", ">5% 低 / <2% 高"),
    }
    us_short = {
        ValuationIndicatorCode.US_PE_PERCENTILE: "PE",
        ValuationIndicatorCode.US_DIVIDEND_YIELD: "股息",
        ValuationIndicatorCode.US_BUFFETT_INDICATOR: "巴菲特",
        ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM: "股债",
    }
    _render_section(
        "美股估值", list(us_codes), us_labels, us_short,
    )


def _parse_lixinger_csv_value(raw: str) -> Decimal:
    """解析理杏仁 CSV 里的 Excel 公式格式（="12345.6"）。

    理杏仁导出的 CSV 用 Excel 公式表示（="12345.6"），前缀 "=" 加双引号。
    返回干净的 Decimal。
    """
    s = raw.strip()
    if s.startswith("="):
        s = s[1:]
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return Decimal(s)


def _format_indicator_value_for_cli(code: ValuationIndicatorCode, value: Decimal) -> str:
    """cli show 命令里单个指标值的格式化字符串（跟 card.py 的 _format_indicator_value 一致）。

    12 个指标的格式差异：
    - 股债利差 / 美股股债利差：保留正负号 + 2 位小数
    - A 股 / 港股 / 美股 PE 分位 + 股息率 + A 股巴菲特：2 位小数百分比
    - AH 溢价 / 港股巴菲特 / 美股巴菲特：整数百分比
    """
    pct = float(value) * 100
    if code in (
        ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
        ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM,
    ):
        return f"{pct:+.2f}%"
    if code in (
        ValuationIndicatorCode.HK_AH_PREMIUM,
        ValuationIndicatorCode.HK_BUFFETT_INDICATOR,
        ValuationIndicatorCode.US_BUFFETT_INDICATOR,
    ):
        return f"{pct:.0f}%"
    return f"{pct:.2f}%"


# ─── Per-fund 估值：指数 → 基金映射（spec 098 第二十七轮）───
#
# liubo 2026-09-19 反馈："我想知道每个基金的估值"。
# 数据来源：用户 6 只 A 股基金实际跟踪的指数（理杏仁 CSV 行 = 指数）。
#
# 索引规则（为什么存 index_code 而不是 fund_code）：
# - CSV 是按"指数"导出的（每行一个指数），不是按"基金"
# - 多只基金可能跟踪同一个指数（中证A500 + 增强A500）
# - 映射写死在 CLI 里（用户固定持有这 6 只基金），后续要加新基金改这里
#
# 特殊映射说明：
# - 930050 中证A50 作为 014532 MSCI中国A50 的替代（理杏仁无 MSCI中国A50）
# - 000510 中证A500 → 同时对应 022434 + 022424 两只基金（同跟踪一个指数）


DEFAULT_INDEX_TO_FUND_MAP: dict[str, list[str]] = {
    "930050": ["014532"],  # 中证A50  → MSCI中国A50（替身）
    "930955": ["008114"],  # 红利低波100 → 红利低波
    "000510": ["022434", "022424"],  # 中证A500 → 两只 A500 基金
    "000852": ["017644"],  # 中证1000 → 中证1000
    "931643": ["013310"],  # 科创创业50 → 科创创业50
    # 港股 3 指数（spec 098.2 — liubo 2026-09-19 方案 A）
    # 映射名跟 lixinger 导出格式对齐（用户灌 CSV 时如果 code 不对再改这里）
    "HSSCHKY": ["004098"],   # 恒生港股通高股息率 → 港股通股息率50（liubo 2026-09-20 实测，理杏仁实际指数代码）
    "930792": ["006809"],    # HK 银行（理杏仁 CSV 实际指数代码是 930792，数字格式）
    "HSTECH": ["013127"],    # 恒生科技 → 恒生科技 ETF
    # 美股 4 指数（spec 098.3 — liubo 2026-09-20）
    # liubo 2026-09-20 实测：理杏仁 lixinger CSV 里 NDX 行 PE/PB/PS/股息率全空，
    # 只能拿到 INX（标普 500）的估值。per-fund 阶段所有美股基金都 fallback 到 INX。
    # 等理杏仁补 NDX 数据后，NDX 行的 FundValuation 会被写入；当前 DB 查询按
    # index_code 路由，没 NDX 数据就 fallback 到 INX（per-fund 卡片显示 fallback 提示）。
    "INX":  ["017641"],   # 标普 500（lixinger "INX"，带 ="..." 格式）→ 摩根标普 500
    "GSPC": ["017641"],   # 标普 500（yahoo/alt code，备用）→ 摩根标普 500
    "OEX":  ["519981"],   # 标普 100 → 长信标普 100 等权重
    "NDX":  [
        "018966", "539001", "016452", "019524",  # 4 只纳指 100 ETF
        "017730", "016664", "006373",            # 3 只全球主题 QDII（实际偏纳指）
    ],
}


def _is_hk_fund(fund_code: str) -> bool:
    """判断基金代码是否属于港股（SwensenClass.HK_EQUITY）。

    通过 breakdown 模块的 SUBCLASS_BY_CODE 反查（避免在 cli 里硬编码）。
    """
    from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE, SwensenClass

    return SUBCLASS_BY_CODE.get(fund_code) == SwensenClass.HK_EQUITY


def _is_us_fund(fund_code: str) -> bool:
    """判断基金代码是否属于美股（SwensenClass.US_EQUITY）。

    通过 breakdown 模块的 SUBCLASS_BY_CODE 反查。
    """
    from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE, SwensenClass

    return SUBCLASS_BY_CODE.get(fund_code) == SwensenClass.US_EQUITY


# 港股指数代码集合（从 DEFAULT_INDEX_TO_FUND_MAP 自动派生，CSV 多市场筛选用）
HK_INDEX_CODES: set[str] = {
    code
    for code, funds in DEFAULT_INDEX_TO_FUND_MAP.items()
    if any(_is_hk_fund(f) for f in funds)
}

# 美股指数代码集合（从 DEFAULT_INDEX_TO_FUND_MAP 自动派生）
US_INDEX_CODES: set[str] = {
    code
    for code, funds in DEFAULT_INDEX_TO_FUND_MAP.items()
    if any(_is_us_fund(f) for f in funds)
}


@valuation_app.command("import-fund-csv")
def cmd_valuation_import_fund_csv(
    file: Annotated[Path, typer.Option("--file", "-f", help="理杏仁导出的 CSV 文件路径")],
    on: str | None = typer.Option(
        None, "--date", help="估值日期 YYYY-MM-DD（默认今天）"
    ),
) -> None:
    """从理杏仁 CSV 导入每只 A 股基金的估值（spec 098 第二十七轮）。

    CSV 格式：每行一个指数，列 = 收盘点位 / PE-TTM / PE分位 / 股息率（同 import-csv）。
    区别：import-csv 只读第一行（中证全指 → 4 个 A 股整体指标），本命令读所有行
    （每行一个指数 → 对应到用户持有的基金 → 写 fund_valuations 表）。

    映射规则（写死在 DEFAULT_INDEX_TO_FUND_MAP）：
    - 中证A50 (930050) → MSCI中国A50 (014532)
    - 红利低波100 (930955) → 红利低波 (008114)
    - 中证A500 (000510) → 中证A500 (022434) + 中证A500ETF联接 (022424)
    - 中证1000 (000852) → 中证1000 (017644)
    - 科创创业50 (931643) → 科创创业50 (013310)

    CSV 里没在映射表的指数（000985 中证全指）→ 跳过（那是整体指标，不是单基金）。

    同一天同一基金多次导入 → 覆盖最新一次（ON CONFLICT）。
    """
    from datetime import date as _date

    if not file.exists():
        console.print(f"[red]✗[/red] 文件不存在：{file}")
        raise typer.Exit(code=1)

    target_date = _parse_date(on) if on else _date.today()

    # 解析 CSV（理杏仁格式：Excel formula 前缀 = "value"，多个数据行）
    import csv as _csv

    saved = 0
    skipped = 0
    db = _default_db()
    src_name = f"lixinger_csv:{file.name}"

    try:
        with file.open(encoding="utf-8") as f:
            reader = _csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                console.print("[red]✗[/red] CSV 是空的（没 header）")
                raise typer.Exit(code=1)
            col_idx = {name: idx for idx, name in enumerate(header)}

            def _get(row: list[str], col: str) -> Decimal | None:
                if col not in col_idx:
                    return None
                raw = row[col_idx[col]]
                if not raw or raw == "n/a" or raw == "":
                    return None
                try:
                    return _parse_lixinger_csv_value(raw)
                except (InvalidOperation, IndexError):
                    return None

            for row in reader:
                # CSV 行：index_code 在第 1 列（="930050" 这种）
                if not row:
                    continue
                idx_raw = row[0].strip()
                if idx_raw.startswith("="):
                    idx_raw = idx_raw[1:]
                if idx_raw.startswith('"') and idx_raw.endswith('"'):
                    idx_raw = idx_raw[1:-1]
                idx_code = idx_raw.strip()

                if idx_code not in DEFAULT_INDEX_TO_FUND_MAP:
                    # 不在映射表（通常是 000985 中证全指 — 整体指标不入基金表）
                    skipped += 1
                    continue

                pe_ttm = _get(row, "PE-TTM(当前值)")
                pe_pct = _get(row, "PE-TTM(分位点%)")
                dy = _get(row, "股息率(当前值)")
                # 红利低波专用：股息率加权 PE 分位（用户手动从银行螺丝钉抄）
                pe_pct_dyw = _get(row, "PE分位(按股息率加权)")
                # ROE：最新 + 去年同期（成长股估值辅助）
                roe_latest = _get(row, "净资产收益率(ROE)(2026Q2)")
                roe_year_ago = _get(row, "净资产收益率(ROE)(2025Q2)")

                if (
                    pe_ttm is None
                    and pe_pct is None
                    and dy is None
                    and pe_pct_dyw is None
                    and roe_latest is None
                    and roe_year_ago is None
                ):
                    # 这行没数据（CSV 空行 / 数据列全是 n/a）
                    skipped += 1
                    continue

                for fund_code in DEFAULT_INDEX_TO_FUND_MAP[idx_code]:
                    fv = FundValuation(
                        record_date=target_date,
                        fund_code=fund_code,
                        index_code=idx_code,
                        pe_ttm=pe_ttm,
                        pe_percentile=pe_pct,
                        dividend_yield=dy,
                        pe_percentile_dy_weighted=pe_pct_dyw,
                        roe_latest=roe_latest,
                        roe_year_ago=roe_year_ago,
                        source=src_name,
                    )
                    db.upsert_fund_valuation(fv)
                    saved += 1

    except UnicodeDecodeError as e:
        console.print(f"[red]✗[/red] CSV 编码错误（要用 UTF-8）：{e}")
        raise typer.Exit(code=1) from e

    console.print(f"[green]✓[/green] {target_date} 已导入 {saved} 条基金估值")
    if skipped:
        console.print(f"[yellow]![/yellow] 跳过 {skipped} 行（未在映射表或无数据）")
    console.print("下一步：跑 `gap portfolio publish` 发飞书看效果。")


@valuation_app.command("import-csv")
def cmd_valuation_import_csv(
    file: Annotated[Path, typer.Option("--file", "-f", help="理杏仁导出的 CSV 文件路径")],
    on: str | None = typer.Option(
        None, "--date", help="估值日期 YYYY-MM-DD（默认今天）"
    ),
    auto_fill: bool = typer.Option(
        True,
        "--auto-fill/--no-auto-fill",
        help="是否自动用 akshare 补齐 CSV 里没有的指标（10Y 国债/巴菲特）",
    ),
) -> None:
    """从理杏仁导出的 CSV 导入估值指标（spec 098 第二十二轮）。

    CSV 来源：理杏仁「指数估值」页 → 导出 CSV。
    推荐格式：总市值加权 + 10年窗口（如 frog_总市值加权_10年_20260919_xxxxxx.csv）。

    CSV 列（只读这 3 个，其余字段忽略）：
    - 收盘点位：指数收盘点（仅信息记录）
    - PE-TTM(当前值)：PE-TTM 实数（如 20.18）
    - PE-TTM(分位点%)：10 年 PE 分位（**已经是 fraction**：如 0.7826 = 78.26%，直接用）
    - 股息率(当前值)：股息率（**已经是 fraction**：如 0.0206 = 2.06%，直接用）

    注：CSV 列名带"%"但值是 fraction，不要再 × 100。

    股债利差和巴菲特指标 CSV 里没有，--auto-fill=True 时用 akshare 拉：
    - 10Y 国债收益率（ak.bond_zh_us_rate）→ 算股债利差
    - A 股总市值 + GDP（ak.macro_china_stock_market_cap + macro_china_gdp）→ 算巴菲特
    """
    from datetime import date as _date
    from global_allocation.portfolio.valuation_indicators import (
        compute_buffett_indicator,
        compute_equity_risk_premium,
    )

    if not file.exists():
        console.print(f"[red]✗[/red] 文件不存在：{file}")
        raise typer.Exit(code=1)

    target_date = _parse_date(on) if on else _date.today()

    # 解析 CSV（理杏仁导出格式 = Excel formula 前缀）
    import csv as _csv

    try:
        with file.open(encoding="utf-8-sig") as f:
            reader = _csv.reader(f)
            header = next(reader)
            data_rows = [r for r in reader if r and r[0] and not r[0].startswith("数据")]
    except (StopIteration, _csv.Error, UnicodeDecodeError) as e:
        console.print(f"[red]✗[/red] CSV 解析失败：{e}")
        raise typer.Exit(code=1) from e

    # 找 000985 中证全指行（spec 098 第一期约定）。多市场 CSV（spec 098.3）里
    # INX/港股等都在前面，必须跳过。中证全指是 A 股整体估值的"代表指数"。
    data_row = None
    for r in data_rows:
        if len(r) > 0:
            idx_code = r[0].strip().strip('="').strip('"').lstrip(".")
            if idx_code == "000985":
                data_row = r
                break
    if data_row is None:
        console.print(
            "[red]✗[/red] CSV 里没找到 000985（中证全指）行 — A 股 region 需要中证全指数据。"
            " 如果是美股/港股专用 CSV，请用 `import-us-csv` 或 `import-hk-csv`。"
        )
        raise typer.Exit(code=1)

    # header → idx 映射
    col_idx = {name: idx for idx, name in enumerate(header)}

    def _get(col: str) -> Decimal | None:
        if col not in col_idx:
            return None
        raw = data_row[col_idx[col]]
        if not raw or raw == "n/a" or raw == "":
            return None
        try:
            return _parse_lixinger_csv_value(raw)
        except (InvalidOperation, IndexError):
            return None

    # 必填：收盘点位 / PE / PE 分位 / 股息率
    close_price = _get("收盘点位")
    pe_ttm = _get("PE-TTM(当前值)")
    pe_percentile_pct = _get("PE-TTM(分位点%)")
    dividend_yield_pct = _get("股息率(当前值)")

    missing = []
    if pe_ttm is None:
        missing.append("PE-TTM(当前值)")
    if pe_percentile_pct is None:
        missing.append("PE-TTM(分位点%)")
    if dividend_yield_pct is None:
        missing.append("股息率(当前值)")
    if missing:
        console.print(f"[red]✗[/red] CSV 缺少必填列：{', '.join(missing)}")
        console.print("提示：导 CSV 时选「总市值加权 + 10 年窗口」，包含 PE/PE分位/股息率。")
        raise typer.Exit(code=1)

    # 入库 3 个 CSV 直接给的指标
    # CSV 列名带 "%" 但值已经是 fraction（0.7826 = 78.26%），不要再 × 100
    db = _default_db()
    db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=target_date,
            indicator_code=ValuationIndicatorCode.PE_PERCENTILE,
            value=pe_percentile_pct,  # CSV 已是 fraction：0.7826 = 78.26%
            source=f"lixinger_csv:{file.name}",
        )
    )
    db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=target_date,
            indicator_code=ValuationIndicatorCode.DIVIDEND_YIELD,
            value=dividend_yield_pct,  # CSV 已是 fraction：0.0206 = 2.06%
            source=f"lixinger_csv:{file.name}",
        )
    )
    saved = 3
    console.print(f"[green]✓[/green] 已导入 CSV 指标 × 3（PE 分位 / 股息率 / 收盘点位 {float(close_price) if close_price else 'n/a'}）")

    # 自动补齐：股债利差（要 PE + 国债）+ 巴菲特（市值 + GDP）
    if not auto_fill:
        console.print("[yellow]![/yellow] --no-auto-fill：跳过股债利差 + 巴菲特补齐")
    else:
        src = AkshareValuationSource()

        # 股债利差 = 1/PE - 国债
        treasury = src.get_10y_treasury_yield(target_date)
        if pe_ttm is not None and treasury is not None:
            try:
                erp = compute_equity_risk_premium(pe_ttm, treasury)
                db.upsert_valuation_indicator(
                    ValuationIndicator(
                        record_date=target_date,
                        indicator_code=ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
                        value=erp,
                        source="lixinger_csv_pe+akshare:bond_zh_us_rate",
                    )
                )
                saved += 1
                console.print(
                    f"[green]✓[/green] 股债利差补齐：1/{float(pe_ttm):.2f} - {float(treasury) * 100:.2f}% = {float(erp) * 100:+.2f}%"
                )
            except ValueError as e:
                console.print(f"[yellow]![/yellow] 股债利差算失败：{e}")
        else:
            console.print("[yellow]![/yellow] 股债利差未补齐（akshare 国债接口失败）")

        # 巴菲特指标 = 总市值 / GDP
        market_cap = src.get_a_share_total_market_cap(target_date)
        gdp = src.get_china_gdp(target_date)
        if market_cap is not None and gdp is not None:
            try:
                bf = compute_buffett_indicator(market_cap, gdp)
                db.upsert_valuation_indicator(
                    ValuationIndicator(
                        record_date=target_date,
                        indicator_code=ValuationIndicatorCode.BUFFETT_INDICATOR,
                        value=bf,
                        source="akshare:stock_sse_summary+macro_china_gdp",
                    )
                )
                saved += 1
                console.print(
                    f"[green]✓[/green] 巴菲特指标补齐：{float(bf) * 100:.1f}%（市值 {float(market_cap) / 1e12:.1f}万亿 / GDP {float(gdp) / 1e12:.1f}万亿）"
                )
            except ValueError as e:
                console.print(f"[yellow]![/yellow] 巴菲特指标算失败：{e}")
        else:
            console.print("[yellow]![/yellow] 巴菲特指标未补齐（akshare 市值/GDP 接口失败）")

    console.print(f"\n[bold]{target_date} 共入库 {saved}/4 个指标[/bold]")
    console.print("下一步：跑 `gap valuation show` 看效果，或 `gap portfolio publish` 发飞书。")


@valuation_app.command("import-hk-csv")
def cmd_valuation_import_hk_csv(
    file: Annotated[Path, typer.Option("--file", "-f", help="理杏仁导出的 HK 指数 CSV 文件路径")],
    on: str | None = typer.Option(
        None, "--date", help="估值日期 YYYY-MM-DD（默认今天）"
    ),
    auto_fill: bool = typer.Option(
        True,
        "--auto-fill/--no-auto-fill",
        help="是否自动用 akshare 补齐 CSV 里没有的指标（AH 溢价 / 港股巴菲特）",
    ),
) -> None:
    """从理杏仁导出的 HK 指数 CSV 导入估值指标（spec 098.2）。

    CSV 来源：理杏仁「指数估值」页 → 导出（多个 HK 指数）。
    推荐 CSV 包含 3 个 HK 指数（liubo 2026-09-20 实测）：
      - HSTECH（恒生科技）→ 013127
      - 930792（HK 银行） → 006809
      - HSSCHKY（恒生港股通高股息率） → 004098

    CSV 列（每个指数一行）：
      - PE-TTM(分位点%)：10 年 PE 分位（已 fraction：0.7826 = 78.26%）
      - 股息率(当前值)：已 fraction：0.0206 = 2.06%

    聚合策略（liubo 2026-09-20 拍板）：HK 综合 = 3 指数**算术平均**。

    自动补齐（akshare）：
      - AH 溢价（ak.stock_hk_index_daily_sina('HSAHP')）
      - 港股巴菲特（HK 总市值 / HK GDP）
        - HK 总市值：akshare 没现成接口，先用 HKEX 月度统计硬编码（约 38.5 万亿 HKD）
        - HK GDP：ak.macro_china_hk_gbp（季度累加 = 年度）
    """
    from datetime import date as _date
    from global_allocation.portfolio.valuation_indicators import (
        compute_buffett_indicator,
    )

    if not file.exists():
        console.print(f"[red]✗[/red] 文件不存在：{file}")
        raise typer.Exit(code=1)

    target_date = _parse_date(on) if on else _date.today()

    # 解析 CSV（理杏仁导出格式 = Excel formula 前缀）
    import csv as _csv

    try:
        with file.open(encoding="utf-8-sig") as f:  # utf-8-sig 自动剥 BOM
            reader = _csv.reader(f)
            header = next(reader)
            data_rows = list(reader)
    except (StopIteration, _csv.Error, UnicodeDecodeError) as e:
        console.print(f"[red]✗[/red] CSV 解析失败：{e}")
        raise typer.Exit(code=1) from e

    # 跳过注释/空行（"数据来源于" 之类）
    data_rows = [r for r in data_rows if r and r[0] and not r[0].startswith("数据")]

    col_idx = {name: idx for idx, name in enumerate(header)}

    pe_pcts: list[Decimal] = []
    div_yields: list[Decimal] = []
    # 收集 per-fund 估值（spec 098 第二十七轮 — liubo 2026-09-20 反问"我只有 3 个港股基金吗"）
    fund_data: list[tuple[str, dict[str, Decimal | None]]] = []

    skipped = 0
    for row in data_rows:
        # 指数代码（用于日志 + 过滤）
        idx_code_raw = row[col_idx["指数代码"]] if "指数代码" in col_idx else "?"
        # 理杏仁 CSV 里指数代码带 ="..." 格式（如 ="HSTECH"），剥掉 ="" 包裹
        idx_code = idx_code_raw.strip().strip('="')

        # 只保留港股指数（HSTECH / HKBANK / HSSCHKY），跳过 A 股
        if idx_code not in HK_INDEX_CODES:
            skipped += 1
            continue

        # 解析 PE / 股息率（用于市场综合）
        pe_pct_raw = row[col_idx["PE-TTM(分位点%)"]] if "PE-TTM(分位点%)" in col_idx else ""
        div_raw = row[col_idx["股息率(当前值)"]] if "股息率(当前值)" in col_idx else ""

        try:
            pe = _parse_lixinger_csv_value(pe_pct_raw) if pe_pct_raw else None
            dy = _parse_lixinger_csv_value(div_raw) if div_raw else None
        except (InvalidOperation, IndexError):
            continue

        if pe is not None:
            pe_pcts.append(pe)
            console.print(f"  · {idx_code} PE 分位 {float(pe) * 100:.1f}%")
        if dy is not None:
            div_yields.append(dy)
            console.print(f"  · {idx_code} 股息率 {float(dy) * 100:.2f}%")

        # 解析 PE-TTM 实数 + ROE（用于 per-fund）
        pe_ttm_raw = row[col_idx.get("PE-TTM(当前值)", -1)] if "PE-TTM(当前值)" in col_idx else ""
        roe_q2_raw = row[col_idx.get("净资产收益率(ROE)(2026Q2)", -1)] if "净资产收益率(ROE)(2026Q2)" in col_idx else ""
        roe_yq_raw = row[col_idx.get("净资产收益率(ROE)(2025Q4)", -1)] if "净资产收益率(ROE)(2025Q4)" in col_idx else ""
        roe_yy_raw = row[col_idx.get("净资产收益率(ROE)(2025Q2)", -1)] if "净资产收益率(ROE)(2025Q2)" in col_idx else ""

        def _opt(raw: str) -> Decimal | None:
            if not raw:
                return None
            try:
                return _parse_lixinger_csv_value(raw)
            except (InvalidOperation, IndexError):
                return None

        fund_data.append((
            idx_code,
            {
                "pe_ttm": _opt(pe_ttm_raw),
                "pe_percentile": pe,
                "dividend_yield": dy,
                "roe_latest": _opt(roe_q2_raw) or _opt(roe_yq_raw),  # Q2 优先，回落到 Q4
                "roe_year_ago": _opt(roe_yy_raw),
            },
        ))

    if skipped > 0:
        console.print(f"[dim]跳过 {skipped} 行非港股指数（A 股）[/dim]")

    if not pe_pcts or not div_yields:
        console.print("[red]✗[/red] CSV 里没有 PE 分位 / 股息率数据")
        raise typer.Exit(code=1)

    # 算术平均 → HK 综合
    n = len(pe_pcts)
    avg_pe_pct = sum(pe_pcts) / Decimal(n)
    avg_div = sum(div_yields) / Decimal(n)

    console.print(
        f"\n[cyan]→[/cyan] {n} 个 HK 指数算术平均："
        f"PE 分位 {float(avg_pe_pct) * 100:.1f}% / 股息率 {float(avg_div) * 100:.2f}%"
    )

    db = _default_db()
    db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=target_date,
            indicator_code=ValuationIndicatorCode.HK_PE_PERCENTILE,
            value=avg_pe_pct,
            source=f"lixinger_csv_avg:{file.name}",
        )
    )
    db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=target_date,
            indicator_code=ValuationIndicatorCode.HK_DIVIDEND_YIELD,
            value=avg_div,
            source=f"lixinger_csv_avg:{file.name}",
        )
    )
    saved = 2
    console.print(f"[green]✓[/green] 已导入 HK CSV 指标 × 2（PE 分位 / 股息率）")

    # per-fund 估值（spec 098 第二十七轮）— 通过 DEFAULT_INDEX_TO_FUND_MAP 反查
    from global_allocation.portfolio.models import FundValuation

    fund_count = 0
    for idx_code, vals in fund_data:
        for fund_code in DEFAULT_INDEX_TO_FUND_MAP.get(idx_code, []):
            fv = FundValuation(
                record_date=target_date,
                fund_code=fund_code,
                index_code=idx_code,
                pe_ttm=vals["pe_ttm"],
                pe_percentile=vals["pe_percentile"],
                dividend_yield=vals["dividend_yield"],
                roe_latest=vals["roe_latest"],
                roe_year_ago=vals["roe_year_ago"],
                source=f"lixinger_csv:{file.name}",
            )
            db.upsert_fund_valuation(fv)
            fund_count += 1
            console.print(
                f"  · per-fund {fund_code} ({idx_code}): "
                f"PE {float(vals['pe_ttm']):.2f}, "
                f"PE 分位 {float(vals['pe_percentile']) * 100:.1f}%, "
                f"股息率 {float(vals['dividend_yield']) * 100:.2f}%"
                + (
                    f", ROE {float(vals['roe_latest']) * 100:.1f}%"
                    if vals["roe_latest"] is not None
                    else ""
                )
            )
    if fund_count:
        console.print(f"[green]✓[/green] per-fund 估值已写入 {fund_count} 只港股基金")

    # 自动补齐：AH 溢价 + 港股巴菲特
    if not auto_fill:
        console.print("[yellow]![/yellow] --no-auto-fill：跳过 AH 溢价 + 港股巴菲特补齐")
    else:
        src = AkshareValuationSource()

        # AH 溢价（ratio：1.24 = A 贵 24%）
        ah = src.get_hk_ah_premium(target_date)
        if ah is not None:
            db.upsert_valuation_indicator(
                ValuationIndicator(
                    record_date=target_date,
                    indicator_code=ValuationIndicatorCode.HK_AH_PREMIUM,
                    value=ah,
                    source="akshare:stock_hk_index_daily_sina(HSAHP)",
                )
            )
            saved += 1
            console.print(
                f"[green]✓[/green] AH 溢价补齐：{float(ah) * 100:.1f}%（A 股比 H 股贵 {float(ah) * 100:.1f}%）"
            )
        else:
            console.print("[yellow]![/yellow] AH 溢价未补齐（akshare HSAHP 接口失败）")

        # 港股巴菲特（HK mcap / HK GDP）
        mcap = src.get_hk_total_market_cap(target_date)
        gdp = src.get_hk_gdp(target_date)
        if mcap is not None and gdp is not None:
            try:
                bf = compute_buffett_indicator(mcap, gdp)
                db.upsert_valuation_indicator(
                    ValuationIndicator(
                        record_date=target_date,
                        indicator_code=ValuationIndicatorCode.HK_BUFFETT_INDICATOR,
                        value=bf,
                        source="akshare:hk_mcap(hardcoded)+macro_china_hk_gbp",
                    )
                )
                saved += 1
                console.print(
                    f"[green]✓[/green] 港股巴菲特补齐：{float(bf):.2f}"
                    f"（市值 {float(mcap) / 1e12:.1f}万亿 HKD / GDP {float(gdp) / 1e12:.1f}万亿 HKD）"
                )
            except ValueError as e:
                console.print(f"[yellow]![/yellow] 港股巴菲特算失败：{e}")
        else:
            console.print("[yellow]![/yellow] 港股巴菲特未补齐（akshare HK GDP 接口失败）")

    console.print(f"\n[bold]{target_date} 共入库 {saved}/4 个 HK 指标[/bold]")
    console.print("下一步：跑 `gap valuation show` 看效果，或 `gap portfolio publish` 发飞书。")


@valuation_app.command("import-us-csv")
def cmd_valuation_import_us_csv(
    file: Annotated[Path, typer.Option("--file", "-f", help="理杏仁导出的美股指数 CSV 文件路径")],
    on: str | None = typer.Option(
        None, "--date", help="估值日期 YYYY-MM-DD（默认今天）"
    ),
    auto_fill: bool = typer.Option(
        True,
        "--auto-fill/--no-auto-fill",
        help="是否自动用 akshare/硬编码 补齐 CSV 里没有的指标（美股巴菲特 + 美股股债利差）",
    ),
) -> None:
    """从理杏仁导出的美股指数 CSV 导入估值指标（spec 098.3）。

    CSV 来源：理杏仁「指数估值」页 → 导出（多个美股指数）。
    liubo 2026-09-20 实测：理杏仁能拉到 标普 500 (INX) 一行的 PE/股息率，
    NDX (纳斯达克 100) 那行 PE/PB/PS/股息率全空（理杏仁未提供）。

    CSV 列（每个指数一行）：
      - PE-TTM(分位点%)：10 年 PE 分位（已 fraction：0.6017 = 60.17%）
      - 股息率(当前值)：已 fraction：0.0106 = 1.06%

    聚合策略（liubo 2026-09-20 拍板）：US 综合 = INX 单指数（NDX 没数据）。
    后续理杏仁补 NDX 后会自动平均。

    自动补齐：
      - 美股巴菲特（us_buffett_indicator = US 市值 / US GDP）
        - US 市值：硬编码 50T USD（NYSE 32T + NASDAQ 30T - 重复 12T ≈ 50T）
        - US GDP：硬编码 29.2T USD（BEA Q4 2024）
      - 美股股债利差 = 1/PE - 美 10Y 国债（ak.bond_zh_us_rate 列 "美国国债收益率10年"）
    """
    from datetime import date as _date
    from global_allocation.portfolio.valuation_indicators import (
        compute_buffett_indicator,
        compute_equity_risk_premium,
    )

    if not file.exists():
        console.print(f"[red]✗[/red] 文件不存在：{file}")
        raise typer.Exit(code=1)

    target_date = _parse_date(on) if on else _date.today()

    # 解析 CSV（理杏仁导出格式 = Excel formula 前缀）
    import csv as _csv

    try:
        with file.open(encoding="utf-8-sig") as f:  # utf-8-sig 自动剥 BOM
            reader = _csv.reader(f)
            header = next(reader)
            data_rows = list(reader)
    except (StopIteration, _csv.Error, UnicodeDecodeError) as e:
        console.print(f"[red]✗[/red] CSV 解析失败：{e}")
        raise typer.Exit(code=1) from e

    # 跳过注释/空行（"数据来源于" 之类）
    data_rows = [r for r in data_rows if r and r[0] and not r[0].startswith("数据")]

    col_idx = {name: idx for idx, name in enumerate(header)}

    # per-fund 估值数据（spec 098 第二十七轮 — spec 098.3 同样落 fund_valuations 表）
    fund_data: list[tuple[str, dict[str, Decimal | None]]] = []
    saved = 0
    skipped = 0

    # ── 1. 解析 INX（标普 500）整体指标（PE 分位 / 股息率） ──
    # 整张 CSV 只有 INX 一行有数据；NDX 行全空被过滤掉
    for row in data_rows:
        idx_code_raw = row[col_idx["指数代码"]] if "指数代码" in col_idx else "?"
        # 理杏仁 CSV 里指数代码格式：="CODE"（Excel formula wrap）
        # 美股代码可能含 "."（如 .INX / .NDX — Yahoo Finance 风格 lixinger 命名）
        # 剥掉 =" 和 " 包裹，再去前导点
        idx_code = idx_code_raw.strip().strip('="').strip('"').lstrip(".")

        # 只保留美股指数（INX / GSPC / OEX / NDX），跳过 A 股 / 港股
        if idx_code not in US_INDEX_CODES:
            skipped += 1
            continue

        # PE-TTM 分位 + 股息率（用于市场综合）
        pe_pct_raw = row[col_idx["PE-TTM(分位点%)"]] if "PE-TTM(分位点%)" in col_idx else ""
        div_raw = row[col_idx["股息率(当前值)"]] if "股息率(当前值)" in col_idx else ""

        try:
            pe = _parse_lixinger_csv_value(pe_pct_raw) if pe_pct_raw else None
            dy = _parse_lixinger_csv_value(div_raw) if div_raw else None
        except (InvalidOperation, IndexError):
            console.print(f"[yellow]![/yellow] {idx_code} PE/股息率 解析失败：{pe_pct_raw!r} / {div_raw!r}")
            continue

        if pe is None and dy is None:
            console.print(f"[dim]跳过 {idx_code}（PE / 股息率 全空 — lixinger 未提供）[/dim]")
            continue

        # 写整体指标（us_pe_percentile / us_dividend_yield）
        db = _default_db()
        if pe is not None:
            db.upsert_valuation_indicator(
                ValuationIndicator(
                    record_date=target_date,
                    indicator_code=ValuationIndicatorCode.US_PE_PERCENTILE,
                    value=pe,
                    source=f"lixinger_csv:{file.name}",
                )
            )
            saved += 1
            console.print(
                f"[green]✓[/green] {idx_code} 美股 PE 分位 {float(pe) * 100:.1f}%"
            )
        if dy is not None:
            db.upsert_valuation_indicator(
                ValuationIndicator(
                    record_date=target_date,
                    indicator_code=ValuationIndicatorCode.US_DIVIDEND_YIELD,
                    value=dy,
                    source=f"lixinger_csv:{file.name}",
                )
            )
            saved += 1
            console.print(
                f"[green]✓[/green] {idx_code} 美股股息率 {float(dy) * 100:.2f}%"
            )

        # per-fund 估值数据
        pe_ttm_raw = row[col_idx.get("PE-TTM(当前值)", -1)] if "PE-TTM(当前值)" in col_idx else ""
        roe_q2_raw = row[col_idx.get("净资产收益率(ROE)(2026Q2)", -1)] if "净资产收益率(ROE)(2026Q2)" in col_idx else ""
        roe_yq_raw = row[col_idx.get("净资产收益率(ROE)(2025Q4)", -1)] if "净资产收益率(ROE)(2025Q4)" in col_idx else ""
        roe_yy_raw = row[col_idx.get("净资产收益率(ROE)(2025Q2)", -1)] if "净资产收益率(ROE)(2025Q2)" in col_idx else ""

        def _opt(raw: str) -> Decimal | None:
            if not raw:
                return None
            try:
                return _parse_lixinger_csv_value(raw)
            except (InvalidOperation, IndexError):
                return None

        fund_data.append((
            idx_code,
            {
                "pe_ttm": _opt(pe_ttm_raw),
                "pe_percentile": pe,
                "dividend_yield": dy,
                "roe_latest": _opt(roe_q2_raw) or _opt(roe_yq_raw),  # Q2 优先，回落到 Q4
                "roe_year_ago": _opt(roe_yy_raw),
            },
        ))

    if skipped > 0:
        console.print(f"[dim]跳过 {skipped} 行非美股指数（A 股 / 港股 / HS 等）[/dim]")

    if saved == 0:
        console.print("[red]✗[/red] CSV 里没有可用的美股估值数据（PE / 股息率 全空）")
        raise typer.Exit(code=1)

    # per-fund 估值写入（spec 098 第二十七轮 — 通过 DEFAULT_INDEX_TO_FUND_MAP 反查）
    from global_allocation.portfolio.models import FundValuation

    fund_count = 0
    for idx_code, vals in fund_data:
        for fund_code in DEFAULT_INDEX_TO_FUND_MAP.get(idx_code, []):
            fv = FundValuation(
                record_date=target_date,
                fund_code=fund_code,
                index_code=idx_code,  # 标准形式（INX 而非 .INX）
                pe_ttm=vals["pe_ttm"],
                pe_percentile=vals["pe_percentile"],
                dividend_yield=vals["dividend_yield"],
                roe_latest=vals["roe_latest"],
                roe_year_ago=vals["roe_year_ago"],
                source=f"lixinger_csv:{file.name}",
            )
            db.upsert_fund_valuation(fv)
            fund_count += 1
            if vals['pe_ttm'] is not None:
                console.print(
                    f"  · per-fund {fund_code} ({idx_code}): "
                    f"PE {float(vals['pe_ttm']):.2f}, "
                    f"PE 分位 {float(vals['pe_percentile']) * 100:.1f}%, "
                    f"股息率 {float(vals['dividend_yield']) * 100:.2f}%"
                )
            else:
                console.print(
                    f"  · per-fund {fund_code} ({idx_code}): 数据缺失"
                )
    if fund_count:
        console.print(f"[green]✓[/green] per-fund 估值已写入 {fund_count} 只美股基金")

    # 自动补齐：美股巴菲特 + 美股股债利差
    if not auto_fill:
        console.print("[yellow]![/yellow] --no-auto-fill：跳过美股巴菲特 + 股债利差补齐")
    else:
        src = AkshareValuationSource()

        # 美股巴菲特（US mcap / US GDP）
        mcap = src.get_us_total_market_cap(target_date)
        gdp = src.get_us_gdp(target_date)
        if mcap is not None and gdp is not None:
            try:
                bf = compute_buffett_indicator(mcap, gdp)
                db.upsert_valuation_indicator(
                    ValuationIndicator(
                        record_date=target_date,
                        indicator_code=ValuationIndicatorCode.US_BUFFETT_INDICATOR,
                        value=bf,
                        source="akshare:us_mcap(50T_USD_hardcoded)+us_gdp(29.2T_USD_hardcoded)",
                    )
                )
                saved += 1
                console.print(
                    f"[green]✓[/green] 美股巴菲特补齐：{float(bf) * 100:.0f}%"
                    f"（市值 {float(mcap) / 1e12:.1f}万亿 USD / GDP {float(gdp) / 1e12:.1f}万亿 USD）"
                )
            except ValueError as e:
                console.print(f"[yellow]![/yellow] 美股巴菲特算失败：{e}")
        else:
            console.print("[yellow]![/yellow] 美股巴菲特未补齐（市值/GDP 接口失败）")

        # 美股股债利差 = 1/PE - 美 10Y 国债
        # 需要 PE-TTM 实数：取 US_PE_PERCENTILE 当前值对应的指数（INX）的 PE 实数
        # 从 fund_data 里抓 pe_ttm（INX 有的话）
        pe_ttm = next((v["pe_ttm"] for code, v in fund_data if code == "INX" and v["pe_ttm"] is not None), None)
        us_treasury = src.get_us_10y_treasury_yield(target_date)
        if pe_ttm is not None and us_treasury is not None:
            try:
                erp = compute_equity_risk_premium(pe_ttm, us_treasury)
                db.upsert_valuation_indicator(
                    ValuationIndicator(
                        record_date=target_date,
                        indicator_code=ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM,
                        value=erp,
                        source="akshare:bond_zh_us_rate(美国国债收益率10年)+lixinger_csv",
                    )
                )
                saved += 1
                console.print(
                    f"[green]✓[/green] 美股股债利差补齐：{float(erp) * 100:+.2f}%"
                    f"（1/{float(pe_ttm):.2f} - {float(us_treasury) * 100:.2f}%）"
                )
            except ValueError as e:
                console.print(f"[yellow]![/yellow] 美股股债利差算失败：{e}")
        else:
            console.print(
                f"[yellow]![/yellow] 美股股债利差未补齐（PE-TTM={pe_ttm}, 国债={us_treasury}）"
            )

    console.print(f"\n[bold]{target_date} 共入库 {saved}/4 个 US 指标[/bold]")
    console.print("下一步：跑 `gap valuation show` 看效果，或 `gap portfolio publish` 发飞书。")


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
