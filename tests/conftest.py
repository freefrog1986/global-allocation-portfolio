"""pytest 配置 + 共享 fixtures。

放在 tests/ 而不是 tests/unit/ 是为了让子目录继承。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# 把 src 加到 sys.path，避免 editable install 失败时的 import error
import sys

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _isolate_xdg_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """所有测试用 tmp_path 隔离 XDG 目录。

    避免测试污染用户真实的 ~/.local/share/gap/ 和 ~/.config/gap/。
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()


@pytest.fixture
def integration() -> bool:
    """标记当前是否是 integration test。

    用法：``@pytest.mark.skipif(not pytest.lazy_fixture("integration"), ...)`` —— 不需要，集成测试按
    ``@pytest.mark.integration`` 标记即可，CI 上 ``pytest -m "not integration"`` 跳过。
    """
    return bool(os.getenv("GAP_RUN_INTEGRATION"))
