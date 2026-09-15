"""飞书凭证管理。

参照 specs/080-feishu-card.md。

凭证读取优先级：
  1. 环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CHAT_ID
  2. 配置文件 ~/.config/gap/credentials.json（XDG_CONFIG_HOME 兼容）

绝不在源码里 hardcode。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FeishuCredentials:
    """飞书应用凭证。"""

    app_id: str
    app_secret: str
    chat_id: str


def _credentials_file() -> Path:
    """获取凭证文件路径（XDG_CONFIG_HOME 兼容）。"""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".config"
    return base / "gap" / "credentials.json"


def load_credentials() -> FeishuCredentials:
    """读取飞书凭证。

    Returns:
        FeishuCredentials 对象。

    Raises:
        FileNotFoundError: 凭证文件不存在。
        ValueError: 凭证缺失字段。
    """
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    chat_id = os.environ.get("FEISHU_CHAT_ID")

    # 环境变量没设全，尝试从文件读
    if not (app_id and app_secret and chat_id):
        path = _credentials_file()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            app_id = app_id or data.get("app_id", "")
            app_secret = app_secret or data.get("app_secret", "")
            chat_id = chat_id or data.get("chat_id", "")

    if not app_id:
        raise ValueError("缺少 FEISHU_APP_ID（环境变量或 ~/.config/gap/credentials.json）")
    if not app_secret:
        raise ValueError("缺少 FEISHU_APP_SECRET")
    if not chat_id:
        raise ValueError("缺少 FEISHU_CHAT_ID")

    return FeishuCredentials(
        app_id=app_id, app_secret=app_secret, chat_id=chat_id
    )


__all__ = ["FeishuCredentials", "load_credentials"]
