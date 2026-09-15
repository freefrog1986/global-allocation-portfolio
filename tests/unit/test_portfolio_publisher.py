"""测试 src/global_allocation/portfolio/publisher.py。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from global_allocation.feishu.credentials import FeishuCredentials
from global_allocation.models import AssetClass
from global_allocation.portfolio.db import PortfolioDB
from global_allocation.portfolio.journal import PortfolioJournal
from global_allocation.portfolio.publisher import publish_portfolio_report


class FakePriceSource:
    def __init__(self, prices: dict[str, Decimal]) -> None:
        self._prices = prices

    def get_price(self, code: str, on: date) -> Decimal | None:
        return self._prices.get(code)


@pytest.fixture
def journal(tmp_path: Path) -> PortfolioJournal:
    db = PortfolioDB(path=tmp_path / "p.db")
    prices = FakePriceSource({"163406": Decimal("2.50")})
    return PortfolioJournal(db=db, price_source=prices)


@pytest.fixture
def seeded_journal(journal: PortfolioJournal) -> PortfolioJournal:
    journal.add_fund("163406", "兴全合润", AssetClass.MIXED)
    journal.record_buy(
        fund_code="163406",
        trade_date=date(2026, 9, 1),
        shares=Decimal("1000"),
        price=Decimal("2.30"),
    )
    return journal


@pytest.fixture
def creds() -> FeishuCredentials:
    return FeishuCredentials(
        app_id="cli_test",
        app_secret="secret",
        chat_id="oc_test",
    )


class TestPublishPortfolioReport:
    def test_dry_run_returns_json(
        self, seeded_journal: PortfolioJournal, creds: FeishuCredentials
    ) -> None:
        result = publish_portfolio_report(
            seeded_journal, credentials=creds, dry_run=True
        )
        assert isinstance(result, str)
        # 是合法 JSON
        import json
        parsed = json.loads(result)
        assert "header" in parsed
        assert "elements" in parsed

    def test_dry_run_no_holdings_raises(
        self, journal: PortfolioJournal, creds: FeishuCredentials
    ) -> None:
        with pytest.raises(ValueError, match="持仓"):
            publish_portfolio_report(journal, credentials=creds, dry_run=True)

    def test_actual_send_calls_lark(
        self, seeded_journal: PortfolioJournal, creds: FeishuCredentials
    ) -> None:
        with patch(
            "global_allocation.portfolio.publisher._send_card"
        ) as mock_send:
            mock_send.return_value = "om_msg_id"
            result = publish_portfolio_report(
                seeded_journal, credentials=creds, dry_run=False
            )
            assert result == "om_msg_id"
            mock_send.assert_called_once()

    def test_retry_on_failure(
        self, seeded_journal: PortfolioJournal, creds: FeishuCredentials
    ) -> None:
        with patch(
            "global_allocation.portfolio.publisher._send_card"
        ) as mock_send:
            mock_send.side_effect = [RuntimeError("fail1"), RuntimeError("fail2"), "om_msg"]
            result = publish_portfolio_report(
                seeded_journal, credentials=creds, dry_run=False
            )
            assert result == "om_msg"
            assert mock_send.call_count == 3

    def test_give_up_after_3_tries(
        self, seeded_journal: PortfolioJournal, creds: FeishuCredentials
    ) -> None:
        with patch(
            "global_allocation.portfolio.publisher._send_card"
        ) as mock_send:
            mock_send.side_effect = RuntimeError("always fails")
            with pytest.raises(RuntimeError, match="重试 3 次"):
                publish_portfolio_report(
                    seeded_journal, credentials=creds, dry_run=False
                )
            assert mock_send.call_count == 3

    def test_chat_id_override(
        self, seeded_journal: PortfolioJournal, creds: FeishuCredentials
    ) -> None:
        with patch(
            "global_allocation.portfolio.publisher._send_card"
        ) as mock_send:
            mock_send.return_value = "om_msg"
            publish_portfolio_report(
                seeded_journal,
                credentials=creds,
                chat_id="oc_other",
                dry_run=False,
            )
            args, kwargs = mock_send.call_args
            assert kwargs["chat_id"] == "oc_other"

    def test_title_override(
        self, seeded_journal: PortfolioJournal, creds: FeishuCredentials
    ) -> None:
        result = publish_portfolio_report(
            seeded_journal,
            credentials=creds,
            title="周五收盘组合",
            dry_run=True,
        )
        import json
        parsed = json.loads(result)
        assert parsed["header"]["title"]["content"] == "周五收盘组合"
