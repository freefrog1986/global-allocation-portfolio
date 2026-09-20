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


# ─── 1-5 分打分 + 综合分（spec 098 第二十二轮）───


class TestValuationScoring:
    """show 命令展示综合分（liubo 反馈"投票不加权"）。"""

    def test_show_displays_composite_score(self, fake_db: PortfolioDB) -> None:
        """show 命令应该把综合分行也展示出来（5 行：4 指标 + 1 综合）。"""
        today = date(2026, 9, 19)
        # 4 个分数 = [1, 2, 3, 3]，综合 = 2.25 → 2.3 → "低估"
        fake_db.upsert_valuation_indicator(
            ValuationIndicator(
                record_date=today,
                indicator_code=ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
                value=Decimal("0.052"),  # 5.2% → 1
                source="test",
            )
        )
        fake_db.upsert_valuation_indicator(
            ValuationIndicator(
                record_date=today,
                indicator_code=ValuationIndicatorCode.PE_PERCENTILE,
                value=Decimal("0.28"),  # 28% → 2
                source="test",
            )
        )
        fake_db.upsert_valuation_indicator(
            ValuationIndicator(
                record_date=today,
                indicator_code=ValuationIndicatorCode.BUFFETT_INDICATOR,
                value=Decimal("0.65"),  # 65% → 3
                source="test",
            )
        )
        fake_db.upsert_valuation_indicator(
            ValuationIndicator(
                record_date=today,
                indicator_code=ValuationIndicatorCode.DIVIDEND_YIELD,
                value=Decimal("0.025"),  # 2.5% → 3
                source="test",
            )
        )

        result = runner.invoke(app, ["valuation", "show"])
        assert result.exit_code == 0, result.output
        # 综合分行应该出现
        assert "综合" in result.stdout
        assert "2.3" in result.stdout
        assert "低估" in result.stdout


# ─── 理杏仁 CSV 导入（spec 098 第二十二轮）───


def _write_lixinger_csv(path: Path, rows: list[str]) -> Path:
    """构造一个最小可用理杏仁 CSV（包含必需的 3 列）。"""
    content = "\n".join(rows)
    path.write_text(content, encoding="utf-8")
    return path


class TestValuationImportCsv:
    """`gap valuation import-csv <file>` 从理杏仁导出的 CSV 导入估值指标。"""

    def test_import_csv_with_excel_formula_prefix(
        self, tmp_path: Path, fake_db: PortfolioDB
    ) -> None:
        """CSV 单元格式 = Excel 公式前缀 '="..."'，import-csv 必须能正确解析。

        列名带"%"但值已经是 fraction（理杏仁的特殊格式）：
        - PE-TTM(分位点%): 0.7826 (= 78.26%)
        - 股息率(当前值): 0.0206 (= 2.06%)
        """
        csv = tmp_path / "lixinger.csv"
        _write_lixinger_csv(
            csv,
            [
                # header
                "指数代码,指数名称,发布时间,收盘点位,PE-TTM(当前值),PE-TTM(分位点%),股息率(当前值)",
                # data row（Excel formula 前缀 + 值是 fraction，不是百分数）
                '="000985",="中证全指",="2011-08-02",="5862.03",="20.1806",="0.7826",="0.0206"',
            ],
        )

        # 国债 + 市值 + GDP 全部 mock 成功
        from global_allocation.portfolio.valuation_source import AkshareValuationSource

        with patch.object(AkshareValuationSource, "get_10y_treasury_yield", return_value=Decimal("0.0168")), \
             patch.object(AkshareValuationSource, "get_a_share_total_market_cap", return_value=Decimal("95e12")), \
             patch.object(AkshareValuationSource, "get_china_gdp", return_value=Decimal("140e12")):
            result = runner.invoke(app, ["valuation", "import-csv", "--file", str(csv), "--date", "2026-09-19"])

        assert result.exit_code == 0, result.output

        # 验证 4 个指标都入库
        inds = fake_db.list_valuation_indicators_for_date(date(2026, 9, 19))
        assert len(inds) == 4

        # PE 分位：CSV 已 fraction → 0.7826
        pe_pct = next(i for i in inds if i.indicator_code == ValuationIndicatorCode.PE_PERCENTILE)
        assert pe_pct.value == Decimal("0.7826")

        # 股息率：CSV 已 fraction → 0.0206
        dy = next(i for i in inds if i.indicator_code == ValuationIndicatorCode.DIVIDEND_YIELD)
        assert dy.value == Decimal("0.0206")

        # 股债利差：1/20.1806 - 0.0168 ≈ 0.0328
        erp = next(i for i in inds if i.indicator_code == ValuationIndicatorCode.EQUITY_RISK_PREMIUM)
        assert erp.value.quantize(Decimal("0.0001")) == Decimal("0.0328")

        # 巴菲特：95e12 / 140e12 ≈ 0.6786
        bf = next(i for i in inds if i.indicator_code == ValuationIndicatorCode.BUFFETT_INDICATOR)
        assert bf.value.quantize(Decimal("0.0001")) == Decimal("0.6786")

    def test_import_csv_no_auto_fill(
        self, tmp_path: Path, fake_db: PortfolioDB
    ) -> None:
        """--no-auto-fill：只入库 CSV 的 3 个指标，不拉 akshare。"""
        csv = tmp_path / "lixinger.csv"
        _write_lixinger_csv(
            csv,
            [
                "指数代码,收盘点位,PE-TTM(当前值),PE-TTM(分位点%),股息率(当前值)",
                '="000985",="5862",="20",="0.80",="0.020"',
            ],
        )

        # 即使 akshare 返回数据，--no-auto-fill 也不应该调它们
        from global_allocation.portfolio.valuation_source import AkshareValuationSource

        with patch.object(AkshareValuationSource, "get_10y_treasury_yield") as mock_yield, \
             patch.object(AkshareValuationSource, "get_a_share_total_market_cap") as mock_cap, \
             patch.object(AkshareValuationSource, "get_china_gdp") as mock_gdp:
            result = runner.invoke(app, [
                "valuation", "import-csv",
                "--file", str(csv),
                "--date", "2026-09-19",
                "--no-auto-fill",
            ])

        assert result.exit_code == 0, result.output
        assert mock_yield.call_count == 0
        assert mock_cap.call_count == 0
        assert mock_gdp.call_count == 0

        inds = fake_db.list_valuation_indicators_for_date(date(2026, 9, 19))
        # 只有 PE 分位 + 股息率（2 条）
        assert len(inds) == 2
        codes = {i.indicator_code for i in inds}
        assert codes == {ValuationIndicatorCode.PE_PERCENTILE, ValuationIndicatorCode.DIVIDEND_YIELD}

    def test_import_csv_missing_required_column(self, tmp_path: Path, fake_db: PortfolioDB) -> None:
        """CSV 缺股息率列 → 报错退出。"""
        csv = tmp_path / "lixinger.csv"
        _write_lixinger_csv(
            csv,
            [
                "指数代码,收盘点位,PE-TTM(当前值),PE-TTM(分位点%)",  # 缺股息率
                '="000985",="5862",="20",="0.80"',
            ],
        )

        result = runner.invoke(app, ["valuation", "import-csv", "--file", str(csv)])
        assert result.exit_code == 1
        assert "股息率" in result.stdout
        assert fake_db.list_valuation_indicators_for_date(date.today()) == []

    def test_import_csv_file_not_found(self, fake_db: PortfolioDB) -> None:
        """文件不存在 → exit_code=1 + 明确错误。"""
        result = runner.invoke(app, ["valuation", "import-csv", "--file", "/nonexistent/path.csv"])
        assert result.exit_code == 1
        assert "文件不存在" in result.stdout


# ─── Per-fund 估值 CSV 导入（spec 098 第二十七轮）───


class TestValuationImportFundCsv:
    """`gap valuation import-fund-csv <file>` 从理杏仁 CSV 导入每只 A 股基金的估值。

    区别于 import-csv（只读第一行的中证全指 → 4 个整体指标）：
    - import-fund-csv 读所有行 → 每行一个指数 → 映射到具体基金 → 写 fund_valuations 表。
    - 没在 DEFAULT_INDEX_TO_FUND_MAP 的指数（000985 中证全指）→ 跳过。
    """

    def test_import_basic_three_indices(
        self, tmp_path: Path, fake_db: PortfolioDB
    ) -> None:
        """3 行有效指数（中证A50 / 红利低波 / 中证1000）→ 入库 3 条。"""
        csv = tmp_path / "lixinger.csv"
        _write_lixinger_csv(
            csv,
            [
                # header
                "指数代码,指数名称,收盘点位,PE-TTM(当前值),PE-TTM(分位点%),股息率(当前值),"
                "PE分位(按股息率加权),净资产收益率(ROE)(2026Q2),净资产收益率(ROE)(2025Q2)",
                # 3 个有效行（中证全指 000985 在映射表外）
                '="930050",="中证A50",="1700",="15.78",="0.1626",="0.0298",="",="",=""',
                '="930955",="红利低波100",="3200",="8.85",="0.8180",="0.0448",="0.20",="",=""',
                '="000852",="中证1000",="6800",="35.5",="0.55",="0.011",="",="0.0834",="0.0543"',
            ],
        )

        result = runner.invoke(app, [
            "valuation", "import-fund-csv", "--file", str(csv), "--date", "2026-09-19"
        ])
        assert result.exit_code == 0, result.output

        fvs = fake_db.list_fund_valuations_for_date(date(2026, 9, 19))
        assert len(fvs) == 3

        by_code = {fv.fund_code: fv for fv in fvs}
        # 中证A50 → MSCI中国A50 (014532)
        a50 = by_code["014532"]
        assert a50.index_code == "930050"
        assert a50.pe_ttm == Decimal("15.78")
        assert a50.pe_percentile == Decimal("0.1626")
        assert a50.dividend_yield == Decimal("0.0298")
        assert a50.pe_percentile_dy_weighted is None
        # 红利低波 → 红利低波 (008114)
        dy = by_code["008114"]
        assert dy.index_code == "930955"
        assert dy.pe_percentile_dy_weighted == Decimal("0.20")
        # 中证1000 → 中证1000 (017644)
        cs1000 = by_code["017644"]
        assert cs1000.roe_latest == Decimal("0.0834")
        assert cs1000.roe_year_ago == Decimal("0.0543")

    def test_index_with_multiple_funds_splits_rows(
        self, tmp_path: Path, fake_db: PortfolioDB
    ) -> None:
        """中证A500 映射到 2 只基金 → 同一指数行入库 2 条。"""
        csv = tmp_path / "lixinger.csv"
        _write_lixinger_csv(
            csv,
            [
                "指数代码,收盘点位,PE-TTM(当前值),PE-TTM(分位点%),股息率(当前值),"
                "PE分位(按股息率加权),净资产收益率(ROE)(2026Q2),净资产收益率(ROE)(2025Q2)",
                '="000510",="5500",="18.5",="0.4638",="0.022",="",="",=""',
            ],
        )

        result = runner.invoke(app, [
            "valuation", "import-fund-csv", "--file", str(csv), "--date", "2026-09-19"
        ])
        assert result.exit_code == 0, result.output

        fvs = fake_db.list_fund_valuations_for_date(date(2026, 9, 19))
        # 中证A500 → 022434 + 022424 两只基金
        assert len(fvs) == 2
        codes = {fv.fund_code for fv in fvs}
        assert codes == {"022434", "022424"}
        for fv in fvs:
            assert fv.index_code == "000510"
            assert fv.pe_percentile == Decimal("0.4638")

    def test_unmapped_index_skipped(
        self, tmp_path: Path, fake_db: PortfolioDB
    ) -> None:
        """000985 中证全指不在映射表 → 跳过（这是整体指标，不是单基金）。"""
        csv = tmp_path / "lixinger.csv"
        _write_lixinger_csv(
            csv,
            [
                "指数代码,收盘点位,PE-TTM(当前值),PE-TTM(分位点%),股息率(当前值),"
                "PE分位(按股息率加权),净资产收益率(ROE)(2026Q2),净资产收益率(ROE)(2025Q2)",
                '="000985",="5862",="20.18",="0.7826",="0.0206",="",="",=""',
            ],
        )

        result = runner.invoke(app, [
            "valuation", "import-fund-csv", "--file", str(csv), "--date", "2026-09-19"
        ])
        assert result.exit_code == 0, result.output
        assert fake_db.list_fund_valuations_for_date(date(2026, 9, 19)) == []
        # 提示跳过
        assert "跳过" in result.stdout

    def test_empty_csv_exits_one(self, tmp_path: Path, fake_db: PortfolioDB) -> None:
        """CSV 没 header（空文件）→ exit_code=1。"""
        csv = tmp_path / "empty.csv"
        csv.write_text("", encoding="utf-8")

        result = runner.invoke(app, [
            "valuation", "import-fund-csv", "--file", str(csv)
        ])
        assert result.exit_code == 1

    def test_file_not_found(self, fake_db: PortfolioDB) -> None:
        """文件不存在 → exit_code=1。"""
        result = runner.invoke(app, [
            "valuation", "import-fund-csv", "--file", "/nonexistent/path.csv"
        ])
        assert result.exit_code == 1
        assert "文件不存在" in result.stdout

    def test_same_fund_twice_upserts(
        self, tmp_path: Path, fake_db: PortfolioDB
    ) -> None:
        """同一天同一基金两次导入 → 覆盖（ON CONFLICT）。"""
        csv = tmp_path / "lixinger.csv"
        _write_lixinger_csv(
            csv,
            [
                "指数代码,收盘点位,PE-TTM(当前值),PE-TTM(分位点%),股息率(当前值),"
                "PE分位(按股息率加权),净资产收益率(ROE)(2026Q2),净资产收益率(ROE)(2025Q2)",
                '="930050",="1700",="15.78",="0.1626",="0.0298",="",="",=""',
            ],
        )

        # 第一次入库
        runner.invoke(app, [
            "valuation", "import-fund-csv", "--file", str(csv), "--date", "2026-09-19"
        ])
        # 第二次入库，PE 变了
        _write_lixinger_csv(
            csv,
            [
                "指数代码,收盘点位,PE-TTM(当前值),PE-TTM(分位点%),股息率(当前值),"
                "PE分位(按股息率加权),净资产收益率(ROE)(2026Q2),净资产收益率(ROE)(2025Q2)",
                '="930050",="1750",="16.5",="0.20",="0.030",="",="",=""',
            ],
        )
        runner.invoke(app, [
            "valuation", "import-fund-csv", "--file", str(csv), "--date", "2026-09-19"
        ])

        fvs = fake_db.list_fund_valuations_for_date(date(2026, 9, 19))
        assert len(fvs) == 1  # 没重复
        assert fvs[0].pe_ttm == Decimal("16.5")  # 最新值
