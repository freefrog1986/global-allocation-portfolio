"""测试 src/global_allocation/feishu/publisher.py。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from global_allocation.feishu.credentials import FeishuCredentials
from global_allocation.feishu.publisher import publish_backtest_report
from global_allocation.models import (
    BacktestResult,
    PerformanceMetrics,
    PortfolioSnapshot,
)


def _make_result() -> BacktestResult:
    dates = pd.date_range("2024-01-01", periods=10, freq="D")
    equity = pd.DataFrame({"nav": [100000.0 + i * 10 for i in range(10)]}, index=dates)
    snapshots = [
        PortfolioSnapshot(
            date=dates[i].date(),
            total_value=Decimal(str(100000 + i * 10)),
            positions={"X": Decimal("100")},
            weights={"X": Decimal("1.0")},
            cash=Decimal("0"),
        )
        for i in range(10)
    ]
    metrics = PerformanceMetrics(
        cagr=Decimal("0.05"),
        sharpe=Decimal("0.5"),
        max_drawdown=Decimal("-0.1"),
        volatility=Decimal("0.1"),
        total_return=Decimal("0.05"),
        annual_return=Decimal("0.05"),
        correlation=pd.DataFrame(),
        best_day=Decimal("0.01"),
        worst_day=Decimal("-0.02"),
        win_rate=Decimal("0.5"),
    )
    return BacktestResult(
        strategy_id="test",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 10),
        initial_capital=Decimal("100000"),
        final_value=Decimal("100090"),
        equity_curve=equity,
        snapshots=snapshots,
        metrics=metrics,
        rebalance_events=[],
    )


CRED = FeishuCredentials(app_id="test_id", app_secret="test_secret", chat_id="oc_test")


class TestPublishBacktestReport:
    def test_dry_run_returns_card_json(self) -> None:
        result = _make_result()
        out = publish_backtest_report(
            result, credentials=CRED, dry_run=True
        )

        # 是合法 JSON
        import json

        parsed = json.loads(out)
        assert "header" in parsed
        assert "elements" in parsed
        # 没真发
        # （无法直接验证，但 dry_run 路径不发 API）

    def test_dry_run_with_custom_title(self) -> None:
        result = _make_result()
        out = publish_backtest_report(
            result, credentials=CRED, title="我的测试", dry_run=True
        )
        assert "我的测试" in out

    def test_dry_run_with_custom_chat_id_ignored(self) -> None:
        # chat_id 在 dry_run 时不真正使用，但参数接受
        result = _make_result()
        out = publish_backtest_report(
            result, credentials=CRED, chat_id="oc_other", dry_run=True
        )
        assert isinstance(out, str)

    @patch("global_allocation.feishu.publisher._send_card")
    def test_send_calls_api(self, mock_send: MagicMock) -> None:
        mock_send.return_value = "msg_123"

        result = _make_result()
        msg_id = publish_backtest_report(result, credentials=CRED)

        assert msg_id == "msg_123"
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["app_id"] == "test_id"
        assert call_kwargs["chat_id"] == "oc_test"

    @patch("global_allocation.feishu.publisher._send_card")
    @patch("global_allocation.feishu.publisher.time.sleep")
    def test_retries_on_failure(
        self, mock_sleep: MagicMock, mock_send: MagicMock
    ) -> None:
        mock_send.side_effect = [
            ConnectionError("net 1"),
            ConnectionError("net 2"),
            "msg_ok",
        ]

        result = _make_result()
        msg_id = publish_backtest_report(result, credentials=CRED)

        assert msg_id == "msg_ok"
        assert mock_send.call_count == 3

    @patch("global_allocation.feishu.publisher._send_card")
    @patch("global_allocation.feishu.publisher.time.sleep")
    def test_raises_after_3_failures(
        self, mock_sleep: MagicMock, mock_send: MagicMock
    ) -> None:
        mock_send.side_effect = ConnectionError("always fails")

        result = _make_result()
        with pytest.raises(RuntimeError, match="已重试 3 次"):
            publish_backtest_report(result, credentials=CRED)
        assert mock_send.call_count == 3

    @patch("global_allocation.feishu.publisher._send_card")
    def test_uses_provided_credentials(
        self, mock_send: MagicMock
    ) -> None:
        mock_send.return_value = "msg_x"
        custom = FeishuCredentials(
            app_id="custom_id", app_secret="custom_secret", chat_id="oc_custom"
        )
        result = _make_result()
        publish_backtest_report(result, credentials=custom)

        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["app_id"] == "custom_id"
        assert call_kwargs["app_secret"] == "custom_secret"
        assert call_kwargs["chat_id"] == "oc_custom"

    @patch("global_allocation.feishu.publisher._send_card")
    def test_custom_chat_id_overrides_credential(
        self, mock_send: MagicMock
    ) -> None:
        mock_send.return_value = "msg_y"
        result = _make_result()
        publish_backtest_report(
            result, credentials=CRED, chat_id="oc_override"
        )

        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["chat_id"] == "oc_override"
