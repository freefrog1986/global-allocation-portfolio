"""飞书消息发送。

参照 specs/080-feishu-card.md。

用 lark-oapi SDK 发消息卡片。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from global_allocation.feishu.card import (
    build_backtest_card,
    build_strategy_rebalance_card,
    card_to_json,
)
from global_allocation.feishu.credentials import FeishuCredentials, load_credentials
from global_allocation.models import BacktestResult
from global_allocation.strategy.models import RebalanceSuggestion

logger = logging.getLogger(__name__)


def publish_backtest_report(
    result: BacktestResult,
    credentials: FeishuCredentials | None = None,
    chat_id: str | None = None,
    title: str | None = None,
    dry_run: bool = False,
) -> str:
    """发送回测报告到飞书话题。

    Args:
        result: 回测结果。
        credentials: 自定义凭证（默认从 env/文件读）。
        chat_id: 自定义 chat_id（默认用凭证里的）。
        title: 卡片标题。
        dry_run: True = 返回卡片 JSON，不真发。

    Returns:
        dry_run=True → 卡片 JSON 字符串
        dry_run=False → message_id

    Raises:
        ValueError: 凭证缺失。
        RuntimeError: 发送失败（重试 3 次后）。
    """
    if credentials is None:
        credentials = load_credentials()

    actual_chat_id = chat_id or credentials.chat_id

    card = build_backtest_card(result, title=title)
    card_json = card_to_json(card)

    if dry_run:
        return card_json

    # 真实发送（重试 3 次）
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            message_id = _send_card(
                app_id=credentials.app_id,
                app_secret=credentials.app_secret,
                chat_id=actual_chat_id,
                card_json=card_json,
            )
            return message_id
        except Exception as e:
            last_error = e
            logger.warning("飞书发送失败 (attempt %d/3): %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(0.5 * (2**attempt))
            else:
                break

    raise RuntimeError(f"飞书发送失败（已重试 3 次）: {last_error}")


def publish_strategy_rebalance(
    suggestion: RebalanceSuggestion,
    credentials: FeishuCredentials | None = None,
    chat_id: str | None = None,
    title: str | None = None,
    dry_run: bool = False,
) -> str:
    """发送策略再平衡建议到飞书话题。

    Args:
        suggestion: 再平衡建议。
        credentials: 自定义凭证（默认从 env/文件读）。
        chat_id: 自定义 chat_id（默认用凭证里的）。
        title: 卡片标题。
        dry_run: True = 返回卡片 JSON，不真发。

    Returns:
        dry_run=True → 卡片 JSON 字符串
        dry_run=False → message_id
    """
    if credentials is None:
        credentials = load_credentials()

    actual_chat_id = chat_id or credentials.chat_id

    card = build_strategy_rebalance_card(suggestion, title=title)
    card_json = card_to_json(card)

    if dry_run:
        return card_json

    # 真实发送（重试 3 次）
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            message_id = _send_card(
                app_id=credentials.app_id,
                app_secret=credentials.app_secret,
                chat_id=actual_chat_id,
                card_json=card_json,
            )
            return message_id
        except Exception as e:
            last_error = e
            logger.warning("飞书发送失败 (attempt %d/3): %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(0.5 * (2**attempt))
            else:
                break

    raise RuntimeError(f"飞书发送失败（已重试 3 次）: {last_error}")


def _send_card(
    app_id: str,
    app_secret: str,
    chat_id: str,
    card_json: str,
) -> str:
    """实际调 lark-oapi 发一张卡片，返回 message_id。"""
    # 延迟导入（lark-oapi 启动慢）
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
        # SDK 失败时抛错
        code = getattr(response, "code", "unknown")
        msg = getattr(response, "msg", "unknown")
        raise RuntimeError(f"飞书 API 返回错误: code={code}, msg={msg}")

    data: Any = response.data
    msg_id = getattr(data, "message_id", "")
    return msg_id or ""


__all__ = ["publish_backtest_report", "publish_strategy_rebalance"]
