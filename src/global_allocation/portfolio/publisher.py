"""实盘账本 + PE-TTM 周调仓 飞书发送。

参照 specs/090-portfolio-journal.md + specs/022-pe-rebalance.md。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from global_allocation.feishu.credentials import FeishuCredentials, load_credentials
from global_allocation.portfolio.card import build_portfolio_card, card_to_json
from global_allocation.portfolio.etf_rotation_card import (
    build_etf_rotation_card,
)
from global_allocation.portfolio.journal import PortfolioJournal
from global_allocation.portfolio.pe_rebalance import RebalanceAction, weekly_rebalance_plan
from global_allocation.portfolio.pe_rebalance_card import (
    build_pe_rebalance_card,
)

logger = logging.getLogger(__name__)


def publish_portfolio_report(
    journal: PortfolioJournal,
    credentials: FeishuCredentials | None = None,
    chat_id: str | None = None,
    root_id: str | None = None,
    title: str | None = None,
    dry_run: bool = False,
) -> str:
    """发送实盘账本周报到飞书。

    Args:
        journal: 实盘账本实例。
        credentials: 凭证（默认从 env/文件读）。
        chat_id: 覆盖凭证里的 chat_id。
        root_id: 话题根消息 id（传入则发到话题 thread）。
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
                root_id=root_id,
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
    root_id: str | None = None,
) -> str:
    """实际调飞书 OpenAPI 发一张卡片，返回 message_id。

    有 root_id → 走 reply API + reply_in_thread=true（让消息进话题 thread）
    无 root_id → 走 create API（发到群顶）
    """
    import json
    import urllib.request

    token = _get_tenant_access_token(app_id, app_secret)

    if root_id:
        url = (
            f"https://open.feishu.cn/open-apis/im/v1/messages/"
            f"{root_id}/reply"
        )
        payload: dict[str, Any] = {
            "msg_type": "interactive",
            "content": card_json,
            "reply_in_thread": True,
        }
    else:
        url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
        payload = {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": card_json,
        }

    msg_req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(msg_req, timeout=15) as r:
        resp_body = json.loads(r.read())

    if resp_body.get("code", 0) != 0:
        raise RuntimeError(
            f"飞书 API 返回错误: code={resp_body.get('code')}, "
            f"msg={resp_body.get('msg')}"
        )

    return resp_body.get("data", {}).get("message_id", "") or ""


def _get_tenant_access_token(app_id: str, app_secret: str) -> str:
    """拿 tenant_access_token。"""
    import json
    import urllib.request

    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode(),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["tenant_access_token"]


__all__ = ["publish_portfolio_report", "publish_pe_rebalance_report", "publish_etf_rotation_report"]


# ─── PE-TTM 周调仓 飞书发送（spec 022 — liubo 2026-09-24 拍板）───


def publish_pe_rebalance_report(
    actions: list[RebalanceAction] | None = None,
    credentials: FeishuCredentials | None = None,
    chat_id: str | None = None,
    root_id: str | None = None,
    report_date: date | None = None,
    dry_run: bool = False,
) -> str:
    """发送 PE-TTM 周调仓卡片到飞书。

    Args:
        actions: 调仓 action 列表；None = 调用 weekly_rebalance_plan()。
        credentials: 凭证（默认从 env/文件读）。
        chat_id: 覆盖凭证里的 chat_id。
        root_id: 话题根消息 id（传入则发到话题 thread）。
        report_date: 报告日期（标题用）；None = 今天。
        dry_run: True → 返回卡片 JSON 字符串，不真发。

    Returns:
        dry_run=True → 卡片 JSON 字符串
        dry_run=False → message_id
    """
    if credentials is None:
        credentials = load_credentials()

    actual_chat_id = chat_id or credentials.chat_id

    card = build_pe_rebalance_card(actions=actions, report_date=report_date)
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
                root_id=root_id,
            )
        except Exception as e:
            last_error = e
            logger.warning("PE 周报发送失败（第 %d 次）：%s", attempt + 1, e)
            if attempt < 2:
                import time
                time.sleep(2**attempt)  # 1s, 2s
    raise RuntimeError(f"PE 周报飞书发送失败（重试 3 次）：{last_error}")


# ─── ETF 轮动组合 周快照 飞书发送（liubo 2026-09-29 拍板）───


def publish_etf_rotation_report(
    credentials: FeishuCredentials | None = None,
    chat_id: str | None = None,
    root_id: str | None = None,
    report_date: date | None = None,
    dry_run: bool = False,
) -> str:
    """发送 ETF 轮动组合 周快照卡片到飞书。

    Args:
        credentials: 凭证（默认从 env/文件读）。
        chat_id: 覆盖凭证里的 chat_id。
        root_id: 话题根消息 id（传入则发到话题 thread）。
        report_date: 报告日期（标题用）；None = 今天。
        dry_run: True → 返回卡片 JSON 字符串，不真发。

    Returns:
        dry_run=True → 卡片 JSON 字符串
        dry_run=False → message_id
    """
    if credentials is None:
        credentials = load_credentials()

    actual_chat_id = chat_id or credentials.chat_id

    card = build_etf_rotation_card(report_date=report_date)
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
                root_id=root_id,
            )
        except Exception as e:
            last_error = e
            logger.warning("ETF 轮动周报发送失败（第 %d 次）：%s", attempt + 1, e)
            if attempt < 2:
                import time
                time.sleep(2**attempt)  # 1s, 2s
    raise RuntimeError(f"ETF 轮动周报飞书发送失败（重试 3 次）：{last_error}")
