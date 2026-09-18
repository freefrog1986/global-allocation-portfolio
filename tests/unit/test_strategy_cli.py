"""测试 src/global_allocation/strategy/cli.py。"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from global_allocation.strategy.cli import app

runner = CliRunner(mix_stderr=False)


def _isolated_db(monkeypatch, tmp_path: Path) -> Path:
    """重设 XDG_DATA_HOME 到 tmp，避免污染真实数据。"""
    db_path = tmp_path / "strategy.db"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return db_path


# ─── init ───


class TestInit:
    def test_create_allocation(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        result = runner.invoke(
            app,
            [
                "init",
                "global-alloc",
                "--name",
                "全球配置",
                "--type",
                "allocation",
            ],
        )
        assert result.exit_code == 0, result.stderr or result.stdout
        assert "已创建" in result.stdout

    def test_create_selection(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        result = runner.invoke(
            app,
            [
                "init",
                "a-share-dividend",
                "--name",
                "A 股红利",
                "--type",
                "selection",
            ],
        )
        assert result.exit_code == 0, result.stderr or result.stdout
        assert "已创建" in result.stdout

    def test_duplicate_id_fails(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        runner.invoke(
            app,
            ["init", "dup", "--name", "X", "--type", "allocation"],
        )
        result = runner.invoke(
            app,
            ["init", "dup", "--name", "X", "--type", "allocation"],
        )
        assert result.exit_code == 1
        assert "已存在" in result.stderr or "已存在" in result.stdout

    def test_invalid_type(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        result = runner.invoke(
            app,
            ["init", "x", "--name", "X", "--type", "invalid"],
        )
        assert result.exit_code != 0


# ─── list ───


class TestList:
    def test_empty(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "暂无" in result.stdout

    def test_with_strategies(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        runner.invoke(app, ["init", "a", "--name", "A", "--type", "allocation"])
        runner.invoke(app, ["init", "b", "--name", "B", "--type", "selection"])
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "a" in result.stdout
        assert "b" in result.stdout


# ─── show ───


class TestShow:
    def test_not_found(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        result = runner.invoke(app, ["show", "nope"])
        assert result.exit_code == 1

    def test_basic(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        runner.invoke(
            app,
            ["init", "a-share-dividend", "--name", "A 股红利", "--type", "selection"],
        )
        result = runner.invoke(app, ["show", "a-share-dividend"])
        assert result.exit_code == 0
        assert "selection" in result.stdout
        assert "v1" in result.stdout


# ─── check ───


class TestCheck:
    def test_pass(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        runner.invoke(app, ["init", "x", "--name", "X", "--type", "allocation"])
        # 加一个完整 plan
        runner.invoke(
            app,
            [
                "sleeve-add",
                "x",
                "--version",
                "1",
                "financial",
                "--name",
                "金融",
                "--target",
                "0.50",
                "--min",
                "0.40",
                "--max",
                "0.60",
            ],
        )
        runner.invoke(
            app,
            [
                "sleeve-add",
                "x",
                "--version",
                "1",
                "tech",
                "--name",
                "科技",
                "--target",
                "0.50",
                "--min",
                "0.40",
                "--max",
                "0.60",
            ],
        )
        runner.invoke(
            app,
            [
                "target-add",
                "x",
                "--version",
                "1",
                "financial",
                "510300",
                "--weight",
                "0.50",
                "--min",
                "0.40",
                "--max",
                "0.60",
            ],
        )
        runner.invoke(
            app,
            [
                "target-add",
                "x",
                "--version",
                "1",
                "financial",
                "008114",
                "--weight",
                "0.50",
                "--min",
                "0.40",
                "--max",
                "0.60",
            ],
        )
        runner.invoke(
            app,
            [
                "target-add",
                "x",
                "--version",
                "1",
                "tech",
                "159915",
                "--weight",
                "0.50",
                "--min",
                "0.40",
                "--max",
                "0.60",
            ],
        )
        runner.invoke(
            app,
            [
                "target-add",
                "x",
                "--version",
                "1",
                "tech",
                "513050",
                "--weight",
                "0.50",
                "--min",
                "0.40",
                "--max",
                "0.60",
            ],
        )
        result = runner.invoke(app, ["check", "x", "--version", "1"])
        assert result.exit_code == 0, result.stdout
        assert "通过" in result.stdout

    def test_fail(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        runner.invoke(app, ["init", "x", "--name", "X", "--type", "allocation"])
        # 没加 sleeve → 失败
        result = runner.invoke(app, ["check", "x", "--version", "1"])
        assert result.exit_code == 1
        assert "失败" in result.stdout


# ─── version-new ───


class TestVersionNew:
    def test_basic(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        runner.invoke(app, ["init", "x", "--name", "X", "--type", "allocation"])
        result = runner.invoke(
            app, ["version-new", "x", "--notes", "v2 test"]
        )
        assert result.exit_code == 0, result.stdout
        assert "v2" in result.stdout

    def test_no_strategy_fails(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        result = runner.invoke(app, ["version-new", "nope", "--notes", "x"])
        assert result.exit_code == 1


# ─── activate ───


class TestActivate:
    def test_pass(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        runner.invoke(app, ["init", "x", "--name", "X", "--type", "allocation"])
        # 加 plan 让 check 通过
        for code in ["financial", "tech"]:
            runner.invoke(
                app,
                [
                    "sleeve-add",
                    "x",
                    "--version",
                    "1",
                    code,
                    "--name",
                    code,
                    "--target",
                    "0.50",
                    "--min",
                    "0.40",
                    "--max",
                    "0.60",
                ],
            )
        for code, fund in [("financial", "510300"), ("financial", "008114"),
                           ("tech", "159915"), ("tech", "513050")]:
            runner.invoke(
                app,
                [
                    "target-add",
                    "x",
                    "--version",
                    "1",
                    code,
                    fund,
                    "--weight",
                    "0.50",
                    "--min",
                    "0.40",
                    "--max",
                    "0.60",
                ],
            )
        result = runner.invoke(app, ["activate", "x", "--version", "1"])
        assert result.exit_code == 0, result.stdout
        assert "激活" in result.stdout

    def test_fail(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        runner.invoke(app, ["init", "x", "--name", "X", "--type", "allocation"])
        # 没 plan → check 失败 → activate 失败
        result = runner.invoke(app, ["activate", "x", "--version", "1"])
        assert result.exit_code == 1


# ─── rebalance (dry-run) ───


class TestRebalanceDryRun:
    def test_basic(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        runner.invoke(app, ["init", "x", "--name", "X", "--type", "allocation"])
        runner.invoke(
            app,
            [
                "sleeve-add",
                "x",
                "--version",
                "1",
                "fin",
                "--name",
                "金融",
                "--target",
                "0.50",
                "--min",
                "0.40",
                "--max",
                "0.60",
            ],
        )
        runner.invoke(
            app,
            [
                "target-add",
                "x",
                "--version",
                "1",
                "fin",
                "510300",
                "--weight",
                "0.50",
                "--min",
                "0.40",
                "--max",
                "0.60",
            ],
        )
        result = runner.invoke(app, ["rebalance", "x", "--dry-run"])
        assert result.exit_code == 0, result.stdout
        assert "fin" in result.stdout


# ─── next-rebalance ───


class TestNextRebalance:
    def test_no_calendar(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        runner.invoke(app, ["init", "x", "--name", "X", "--type", "allocation"])
        result = runner.invoke(app, ["next-rebalance", "x"])
        assert result.exit_code == 0
        assert "无" in result.stdout

    def test_not_found(self, monkeypatch, tmp_path: Path) -> None:
        _isolated_db(monkeypatch, tmp_path)
        result = runner.invoke(app, ["next-rebalance", "nope"])
        assert result.exit_code == 1
