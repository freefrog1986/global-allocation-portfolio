"""测试 src/global_allocation/portfolio/cli.py 中的 valuation 子命令。

test_portfolio_cli.py 用了过时的 CliRunner(mix_stderr=False) — pre-existing 环境问题，
单独测 valuation CLI（不动那个文件）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from global_allocation.portfolio.cli import app
from global_allocation.portfolio.db import PortfolioDB
from global_allocation.portfolio.models import (
    ValuationIndicator,
    ValuationIndicatorCode,
)


@pytest.fixture
def fake_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PortfolioDB:
    """把 _default_db 注入到 tmp_path（避免污染真实 db）。"""
    db_path = tmp_path / "valuation.db"
    db = PortfolioDB(path=db_path)

    def fake_db_factory() -> PortfolioDB:
        return db

    monkeypatch.setattr(
        "global_allocation.portfolio.cli._default_db",
        fake_db_factory,
    )
    return db


runner = CliRunner()


class TestValuationShowEmpty:
    def test_show_when_db_empty(self, fake_db: PortfolioDB) -> None:
        """DB 空时显示提示，不抛异常。"""
        result = runner.invoke(app, ["valuation", "show"])
        assert result.exit_code == 0
        assert "暂无估值数据" in result.stdout


class TestValuationShowWithData:
    def test_show_displays_all_four_indicators(self, fake_db: PortfolioDB) -> None:
        """DB 有 4 条指标时，show 把全部展示出来。"""
        today = date(2026, 9, 19)
        fake_db.upsert_valuation_indicator(
            ValuationIndicator(
                record_date=today,
                indicator_code=ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
                value=Decimal("0.052"),
                source="test",
            )
        )
        fake_db.upsert_valuation_indicator(
            ValuationIndicator(
                record_date=today,
                indicator_code=ValuationIndicatorCode.PE_PERCENTILE,
                value=Decimal("0.28"),
                source="test",
            )
        )
        fake_db.upsert_valuation_indicator(
            ValuationIndicator(
                record_date=today,
                indicator_code=ValuationIndicatorCode.BUFFETT_INDICATOR,
                value=Decimal("0.65"),
                source="test",
            )
        )
        fake_db.upsert_valuation_indicator(
            ValuationIndicator(
                record_date=today,
                indicator_code=ValuationIndicatorCode.DIVIDEND_YIELD,
                value=Decimal("0.025"),
                source="test",
            )
        )

        result = runner.invoke(app, ["valuation", "show"])
        assert result.exit_code == 0, result.output
        # 4 个指标名都要在
        assert "股债利差" in result.stdout
        assert "PE 分位" in result.stdout
        assert "巴菲特指标" in result.stdout
        assert "股息率" in result.stdout
        # 评估
        assert "偏低估" in result.stdout  # ERP=5.2% > 5%
        assert "偏低估" in result.stdout  # PE=28% < 30%
        assert "正常" in result.stdout  # 巴菲特=65% in 50-80%
        assert "正常" in result.stdout  # 股息率=2.5% in 1-3%

    def test_show_partial_data(self, fake_db: PortfolioDB) -> None:
        """只入库 1 条指标 → 其他行显示"数据缺失"。"""
        fake_db.upsert_valuation_indicator(
            ValuationIndicator(
                record_date=date(2026, 9, 19),
                indicator_code=ValuationIndicatorCode.PE_PERCENTILE,
                value=Decimal("0.85"),  # 偏高估
                source="test",
            )
        )

        result = runner.invoke(app, ["valuation", "show"])
        assert result.exit_code == 0
        assert "偏高估" in result.stdout
        assert "数据缺失" in result.stdout


class TestValuationUpdateIntegration:
    """测试 update 命令：mock akshare + 验证入库正确。"""

    def test_update_with_all_apis_success(self, fake_db: PortfolioDB) -> None:
        """所有 akshare 接口成功 → 入库 4 条。"""
        from global_allocation.portfolio.valuation_source import AkshareValuationSource

        target = date(2026, 9, 19)
        with patch.object(AkshareValuationSource, "get_pe_ttm", return_value=Decimal("20")), \
             patch.object(AkshareValuationSource, "get_10y_treasury_yield", return_value=Decimal("0.03")), \
             patch.object(AkshareValuationSource, "get_pe_history",
                          return_value=[Decimal("15"), Decimal("20"), Decimal("25")]), \
             patch.object(AkshareValuationSource, "get_a_share_total_market_cap",
                          return_value=Decimal("65e12")), \
             patch.object(AkshareValuationSource, "get_china_gdp",
                          return_value=Decimal("100e12")), \
             patch.object(AkshareValuationSource, "get_dividend_yield",
                          return_value=Decimal("0.025")):
            result = runner.invoke(app, ["valuation", "update", "--date", target.isoformat()])

        assert result.exit_code == 0, result.output
        inds = fake_db.list_valuation_indicators_for_date(target)
        assert len(inds) == 4

        # 验证：股债利差 = 1/20 - 0.03 = 0.02
        erp = next(i for i in inds if i.indicator_code == ValuationIndicatorCode.EQUITY_RISK_PREMIUM)
        assert erp.value == Decimal("0.02")
        # PE 分位：(20 - 15) / (25 - 15) = 0.5
        pct = next(i for i in inds if i.indicator_code == ValuationIndicatorCode.PE_PERCENTILE)
        assert pct.value == Decimal("0.5")
        # 巴菲特 = 65e12 / 100e12 = 0.65
        bf = next(i for i in inds if i.indicator_code == ValuationIndicatorCode.BUFFETT_INDICATOR)
        assert bf.value == Decimal("0.65")
        # 股息率 = 0.025
        dy = next(i for i in inds if i.indicator_code == ValuationIndicatorCode.DIVIDEND_YIELD)
        assert dy.value == Decimal("0.025")

    def test_update_with_all_apis_fail(self, fake_db: PortfolioDB) -> None:
        """所有 akshare 失败 → exit_code=1 + 0 条入库。"""
        from global_allocation.portfolio.valuation_source import AkshareValuationSource

        with patch.object(AkshareValuationSource, "get_pe_ttm", return_value=None), \
             patch.object(AkshareValuationSource, "get_10y_treasury_yield", return_value=None), \
             patch.object(AkshareValuationSource, "get_pe_history", return_value=[]), \
             patch.object(AkshareValuationSource, "get_a_share_total_market_cap", return_value=None), \
             patch.object(AkshareValuationSource, "get_china_gdp", return_value=None), \
             patch.object(AkshareValuationSource, "get_dividend_yield", return_value=None):
            result = runner.invoke(app, ["valuation", "update"])

        assert result.exit_code == 1
        assert len(fake_db.list_valuation_indicators_for_date(date.today())) == 0

    def test_update_partial_success(self, fake_db: PortfolioDB) -> None:
        """部分 akshare 成功 → 部分入库，exit_code=0 + 提示部分成功。"""
        from global_allocation.portfolio.valuation_source import AkshareValuationSource

        with patch.object(AkshareValuationSource, "get_pe_ttm", return_value=Decimal("20")), \
             patch.object(AkshareValuationSource, "get_10y_treasury_yield", return_value=Decimal("0.03")), \
             patch.object(AkshareValuationSource, "get_pe_history", return_value=[]), \
             patch.object(AkshareValuationSource, "get_a_share_total_market_cap", return_value=None), \
             patch.object(AkshareValuationSource, "get_china_gdp", return_value=None), \
             patch.object(AkshareValuationSource, "get_dividend_yield", return_value=None):
            result = runner.invoke(app, ["valuation", "update"])

        # 股债利差能算（PE + 国债都有），但其他 3 个不能 → 总共 1 条
        assert result.exit_code == 0
        # rich 格式化在数字前后加了 ANSI 码 — strip 后断言
        import re

        clean = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
        assert "1/4" in clean
        # 部分成功的提示文字
        assert "部分 akshare 接口失败" in result.stdout
