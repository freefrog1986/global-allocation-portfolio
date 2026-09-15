"""实盘账本飞书发送。

参照 specs/090-portfolio-journal.md。
"""

from __future__ import annotations

import logging
from typing import Any

from global_allocation.feishu.credentials import FeishuCredentials, load_credentials
from global_allocation.portfolio.card import build_portfolio_card, card_to_json
from global_allocation.portfolio.journal import PortfolioJournal

logger = logging.getLogger(__name__)


def publish_portfolio_report(
    journal: PortfolioJournal,
    credentials: FeishuCredentials | None = None,
    chat_id: str | None = None,
    title: str | None = None,
    dry_run: bool = False,
) -> str:
    """发送实盘账本周报到飞书。

    Args:
        journal: 实盘账本实例。
        credentials: 凭证（默认从 env/文件读）。
        chat_id: 覆盖凭证里的 chat_id。
        title: 卡片标题。
        dry_run: True → 返回 JSON 字符串，不真发。

    Returns:
        dry_run=True → 卡片 JSON 字符串
        dry_run=False → message_id
    """
    if credentials is None:
        credentials = load_credentials()

    actual_chat_id = chat_id or credentials.chat_id

    card = build_portfolio_card(journal, title=title)
    card_json = card_to_json(card)

    if dry_run:
        return card_json

    # 真实发送（重试 3 次）
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return _send_card(
                app_id=credentials.app_id,
                app_secret=credentials.app_secret,
                chat_id=actual_chat_id,
                card_json=card_json,
            )
        except Exception as e:
            last_error = e
            logger.warning("发送失败（第 %d 次）：%s", attempt + 1, e)
            if attempt < 2:
                import time
                time.sleep(2**attempt)  # 1s, 2s
    raise RuntimeError(f"飞书发送失败（重试 3 次）：{last_error}")


def _send_card(
    app_id: str,
    app_secret: str,
    chat_id: str,
    card_json: str,
) -> str:
    """实际调 lark-oapi 发一张卡片，返回 message_id。"""
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
    )

    client = lark.Client(app_id, app_secret, lark.LogLevel.WARNING)

    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(card_json)
            .build()
        )
        .build()
    )

    response = client.im.v1.message.create(request)

    if not response.success():
        code = getattr(response, "code", "unknown")
        msg = getattr(response, "msg", "unknown")
        raise RuntimeError(f"飞书 API 返回错误: code={code}, msg={msg}")

    data: Any = response.data
    msg_id = getattr(data, "message_id", "")
    return msg_id or ""


__all__ = ["publish_portfolio_report"]
