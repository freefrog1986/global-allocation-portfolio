"""策略配置管理 CLI。

参照 specs/091-strategy-config.md。

挂在 `gap plan` 子命令下（`gap strategy` 已被 backtest 策略占用）。
"""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from global_allocation.strategy.db import StrategyDB
from global_allocation.strategy.models import (
    AllocationConfig,
    PlanSleeve,
    PlanTarget,
    SelectionConfig,
    Strategy,
    StrategyStatus,
    StrategyType,
    StrategyVersion,
)
from global_allocation.strategy.repo import StrategyRepo

app = typer.Typer(help="用户策略配置：分层 / 版本 / sleeve / target / 再平衡。", no_args_is_help=True)
console = Console()


# ─── defaults ───


def _default_db_path() -> Path:
    """默认 SQLite 路径：$XDG_DATA_HOME/gap/strategy.db 或 ~/.local/share/gap/strategy.db。"""
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "gap" / "strategy.db"


def _default_db() -> StrategyDB:
    return StrategyDB(path=_default_db_path())


def _default_repo() -> StrategyRepo:
    return StrategyRepo(db=_default_db())


def _parse_decimal(s: str, name: str) -> Decimal:
    try:
        return Decimal(s)
    except InvalidOperation as e:
        raise typer.BadParameter(f"{name} 必须是数字，got '{s}'") from e


def _parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise typer.BadParameter(f"日期必须是 YYYY-MM-DD，got '{s}'") from e


# ─── init ───


@app.command("init")
def cmd_init(
    strategy_id: Annotated[str, typer.Argument(help="策略 ID（kebab-case）")],
    name: Annotated[str, typer.Option("--name", help="策略显示名")],
    type_: Annotated[
        str,
        typer.Option("--type", help="策略类型：allocation | selection"),
    ],
    notes: Annotated[
        str, typer.Option("--notes", help="v1 备注")
    ] = "init",
) -> None:
    """创建策略 + v1（draft）版本。"""
    try:
        type_enum = StrategyType(type_)
    except ValueError as e:
        raise typer.BadParameter(
            f"--type 必须是 allocation 或 selection，got '{type_}'"
        ) from e

    repo = _default_repo()
    if repo.get_strategy(strategy_id) is not None:
        typer.echo(f"策略 '{strategy_id}' 已存在", err=True)
        raise typer.Exit(code=1)

    now = datetime.now()
    s = Strategy(
        id=strategy_id,
        name=name,
        type=type_enum,
        created_at=now,
        updated_at=now,
    )
    repo.create_strategy(s)

    if type_enum == StrategyType.ALLOCATION:
        config: AllocationConfig | SelectionConfig = AllocationConfig()
    else:
        config = SelectionConfig()

    v = StrategyVersion(
        strategy_id=strategy_id,
        version=1,
        status=StrategyStatus.DRAFT,
        config=config,
        notes=notes,
        created_at=now,
    )
    vid = repo.new_version(v)
    typer.echo(f"✓ 已创建策略 '{strategy_id}' (v1, draft, id={vid})")


# ─── list ───


@app.command("list")
def cmd_list() -> None:
    """列出所有策略。"""
    repo = _default_repo()
    items = repo.list_strategies()
    if not items:
        typer.echo("（暂无策略。先跑 `gap plan init <id> --type <t> --name <n>`）")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("名称")
    table.add_column("类型")
    table.add_column("Active")
    table.add_column("创建时间")

    for s in items:
        table.add_row(
            s.id,
            s.name,
            s.type.value,
            str(s.active_version) if s.active_version else "-",
            s.created_at.strftime("%Y-%m-%d"),
        )
    console.print(table)


# ─── show ───


@app.command("show")
def cmd_show(
    strategy_id: Annotated[str, typer.Argument(help="策略 ID")],
) -> None:
    """显示策略详情（所有 versions + 当前 active 的 sleeves/targets）。"""
    repo = _default_repo()
    s = repo.get_strategy(strategy_id)
    if s is None:
        typer.echo(f"策略 '{strategy_id}' 不存在", err=True)
        raise typer.Exit(code=1)

    console.rule(f"[bold]{s.name}[/bold] ({s.id})")
    console.print(f"[cyan]类型[/cyan]：{s.type.value}")
    console.print(f"[cyan]active_version[/cyan]：{s.active_version}")
    console.print(f"[cyan]创建[/cyan]：{s.created_at.isoformat()}")

    versions = repo.list_versions(strategy_id)
    if not versions:
        console.print("\n[dim]（暂无 version）[/dim]")
        return

    console.print(f"\n[bold]versions[/bold] ({len(versions)})")
    for v in versions:
        marker = " ← active" if v.version == s.active_version else ""
        console.print(
            f"  v{v.version} [{v.status.value}]{marker}  "
            f"created={v.created_at.strftime('%Y-%m-%d')}"
        )

    active = repo.get_active_version(strategy_id)
    if active is not None and active.id is not None:
        full = repo.get_full_version(active.id)
        if full and full.sleeves:
            console.print(f"\n[bold]sleeves (v{active.version})[/bold]")
            sleeve_table = Table(show_header=True, header_style="bold")
            sleeve_table.add_column("code", style="cyan")
            sleeve_table.add_column("name")
            sleeve_table.add_column("target", justify="right")
            sleeve_table.add_column("band", justify="right")
            for sl in full.sleeves:
                sleeve_table.add_row(
                    sl.code,
                    sl.name,
                    f"{sl.target_weight:.2%}",
                    f"[{sl.min_weight:.2%}, {sl.max_weight:.2%}]",
                )
            console.print(sleeve_table)

            console.print("\n[bold]targets[/bold]")
            for sl in full.sleeves:
                if sl.id is None:
                    continue
                targets = full.targets.get(sl.id, [])
                for t in targets:
                    console.print(
                        f"  {sl.code}/{t.fund_code}  "
                        f"weight={t.weight:.2%}  "
                        f"band=[{t.min_weight:.2%}, {t.max_weight:.2%}]"
                    )


# ─── sleeve add ───


@app.command("sleeve-add")
def cmd_sleeve_add(
    strategy_id: Annotated[str, typer.Argument(help="策略 ID")],
    version: Annotated[int, typer.Option("--version", help="version 号")],
    code: Annotated[str, typer.Argument(help="sleeve code（kebab-case）")],
    name: Annotated[str, typer.Option("--name", help="sleeve 显示名")],
    target: Annotated[str, typer.Option("--target", help="目标权重 0~1")],
    min_: Annotated[str, typer.Option("--min", help="下界 0~1")],
    max_: Annotated[str, typer.Option("--max", help="上界 0~1")],
) -> None:
    """给某个 version 加 sleeve。"""
    repo = _default_repo()
    versions = repo.list_versions(strategy_id)
    target_v = next((v for v in versions if v.version == version), None)
    if target_v is None or target_v.id is None:
        typer.echo(f"version {version} 不存在", err=True)
        raise typer.Exit(code=1)

    sleeve = PlanSleeve(
        version_id=target_v.id,
        code=code,
        name=name,
        target_weight=_parse_decimal(target, "--target"),
        min_weight=_parse_decimal(min_, "--min"),
        max_weight=_parse_decimal(max_, "--max"),
    )
    sid = repo.add_sleeve(sleeve)
    typer.echo(f"✓ 已加 sleeve '{code}' (id={sid})")


# ─── target add ───


@app.command("target-add")
def cmd_target_add(
    strategy_id: Annotated[str, typer.Argument(help="策略 ID")],
    version: Annotated[int, typer.Option("--version", help="version 号")],
    sleeve_code: Annotated[str, typer.Argument(help="sleeve code")],
    fund_code: Annotated[str, typer.Argument(help="基金代码")],
    weight: Annotated[str, typer.Option("--weight", help="权重 0~1")],
    min_: Annotated[str, typer.Option("--min", help="下界 0~1")],
    max_: Annotated[str, typer.Option("--max", help="上界 0~1")],
) -> None:
    """给 sleeve 加 fund target。"""
    repo = _default_repo()
    versions = repo.list_versions(strategy_id)
    target_v = next((v for v in versions if v.version == version), None)
    if target_v is None or target_v.id is None:
        typer.echo(f"version {version} 不存在", err=True)
        raise typer.Exit(code=1)

    sleeves = repo.list_sleeves(target_v.id)
    sleeve = next((s for s in sleeves if s.code == sleeve_code), None)
    if sleeve is None or sleeve.id is None:
        typer.echo(f"sleeve '{sleeve_code}' 不存在", err=True)
        raise typer.Exit(code=1)

    t = PlanTarget(
        sleeve_id=sleeve.id,
        fund_code=fund_code,
        weight=_parse_decimal(weight, "--weight"),
        min_weight=_parse_decimal(min_, "--min"),
        max_weight=_parse_decimal(max_, "--max"),
    )
    tid = repo.add_target(t)
    typer.echo(f"✓ 已加 target '{fund_code}' (id={tid})")


# ─── check ───


@app.command("check")
def cmd_check(
    strategy_id: Annotated[str, typer.Argument(help="策略 ID")],
    version: Annotated[int, typer.Option("--version", help="version 号")],
) -> None:
    """校验某 version 是否通过 check。"""
    repo = _default_repo()
    result = repo.check(strategy_id, version)
    if result.passed:
        typer.echo(f"✓ version {version} check 通过")
    else:
        typer.echo(f"✗ version {version} check 失败：")
        for e in result.errors:
            typer.echo(f"  - {e}")
        raise typer.Exit(code=1)


# ─── version new ───


@app.command("version-new")
def cmd_version_new(
    strategy_id: Annotated[str, typer.Argument(help="策略 ID")],
    notes: Annotated[str, typer.Option("--notes", help="版本备注")],
) -> None:
    """新建 draft version（v+1）。"""
    repo = _default_repo()
    versions = repo.list_versions(strategy_id)
    if not versions:
        typer.echo(f"策略 '{strategy_id}' 没有 version", err=True)
        raise typer.Exit(code=1)

    latest = max(versions, key=lambda v: v.version)
    new_num = latest.version + 1
    now = datetime.now()
    v = StrategyVersion(
        strategy_id=strategy_id,
        version=new_num,
        status=StrategyStatus.DRAFT,
        config=latest.config.model_copy(deep=True),
        notes=notes,
        created_at=now,
    )
    vid = repo.new_version(v)
    typer.echo(f"✓ 已创建 v{new_num} (draft, id={vid})")


# ─── activate ───


@app.command("activate")
def cmd_activate(
    strategy_id: Annotated[str, typer.Argument(help="策略 ID")],
    version: Annotated[int, typer.Option("--version", help="version 号")],
) -> None:
    """激活某个 version（先 check → cascade archive 旧 active）。"""
    repo = _default_repo()
    try:
        repo.activate_version(strategy_id, version)
    except ValueError as e:
        typer.echo(f"激活失败：{e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"✓ 已激活 v{version}")


# ─── rebalance (dry-run) ───


@app.command("rebalance")
def cmd_rebalance(
    strategy_id: Annotated[str, typer.Argument(help="策略 ID")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="只打印卡片 JSON，不发飞书")
    ] = True,
    send: Annotated[
        bool, typer.Option("--send", help="发到飞书（默认 dry-run）")
    ] = False,
) -> None:
    """算当前 active version 的再平衡建议（卡片预览 / 发飞书）。

    默认 dry-run：打印 plan + 卡片 JSON。--send 才会真发飞书。
    MVP：不接真实持仓数据。
    """
    repo = _default_repo()
    s = repo.get_strategy(strategy_id)
    if s is None:
        typer.echo(f"策略 '{strategy_id}' 不存在", err=True)
        raise typer.Exit(code=1)
    v = repo.get_active_version(strategy_id)
    if v is None or v.id is None:
        typer.echo(f"策略 '{strategy_id}' 没有 active version", err=True)
        raise typer.Exit(code=1)
    full = repo.get_full_version(v.id)
    if full is None or not full.sleeves:
        typer.echo("（无 sleeves，跳过）")
        return

    from global_allocation.feishu.card import (
        build_strategy_rebalance_card,
        card_to_json,
    )
    from global_allocation.strategy.models import RebalanceSuggestion

    suggestion = RebalanceSuggestion(
        strategy_id=strategy_id,
        version=v.version,
        as_of=date.today(),
        total_value=Decimal("0"),
        actions=[],
        summary="无持仓数据（MVP 未接 portfolio）",
    )

    if not send:
        # dry-run 模式
        console.rule(f"[bold]{s.name}[/bold] rebalance (v{v.version})")
        console.print("[dim]（MVP: 无 portfolio 持仓接入，仅打印 plan + 卡片）[/dim]")
        for sl in full.sleeves:
            if sl.id is None:
                continue
            targets = full.targets.get(sl.id, [])
            for t in targets:
                console.print(
                    f"  {sl.code}/{t.fund_code}  target={t.weight:.2%}  "
                    f"band=[{t.min_weight:.2%}, {t.max_weight:.2%}]"
                )

        card = build_strategy_rebalance_card(suggestion)
        console.print("\n[bold]卡片预览[/bold]")
        console.print(card_to_json(card))
        return

    # 真发飞书
    from global_allocation.feishu.publisher import publish_strategy_rebalance

    try:
        msg_id = publish_strategy_rebalance(
            suggestion=suggestion,
            title=f"{s.name} 再平衡建议",
            dry_run=False,
        )
        typer.echo(f"✓ 已发送到飞书 (message_id={msg_id})")
    except (RuntimeError, ValueError) as e:
        typer.echo(f"发送失败：{e}", err=True)
        raise typer.Exit(code=1) from e


# ─── next-rebalance ───


@app.command("next-rebalance")
def cmd_next_rebalance(
    strategy_id: Annotated[str, typer.Argument(help="策略 ID")],
    last: Annotated[
        str | None,
        typer.Option("--last", help="上次再平衡日期 YYYY-MM-DD"),
    ] = None,
) -> None:
    """算下次再平衡日期（仅 allocation 策略）。"""
    from global_allocation.strategy.rebalance import compute_next_rebalance_date

    repo = _default_repo()
    s = repo.get_strategy(strategy_id)
    if s is None:
        typer.echo(f"策略 '{strategy_id}' 不存在", err=True)
        raise typer.Exit(code=1)
    v = repo.get_active_version(strategy_id)
    if v is None or v.id is None:
        typer.echo(f"策略 '{strategy_id}' 没有 active version", err=True)
        raise typer.Exit(code=1)
    full = repo.get_full_version(v.id)
    if full is None:
        typer.echo("version 数据缺失", err=True)
        raise typer.Exit(code=1)

    last_d = _parse_date(last) if last else None
    next_d, reason = compute_next_rebalance_date(
        strategy=s,
        version=v,
        sleeves=full.sleeves,
        targets_by_sleeve=full.targets,
        last_rebalance=last_d,
    )
    if next_d is None:
        typer.echo(f"无下次再平衡：{reason or '（无 calendar / threshold 触发条件）'}")
    else:
        typer.echo(f"下次再平衡：{next_d.isoformat()}（{reason}）")


__all__ = ["app"]
