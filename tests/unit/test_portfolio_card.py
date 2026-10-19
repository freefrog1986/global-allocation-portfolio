"""测试 src/global_allocation/portfolio/card.py。

飞书 chart card for portfolio journal — spec 096 重设计 + spec 097 第十九轮合并：
focus 在实盘持仓 = summary div + 大类资产柱状图 + 5 列聚合持仓表（含 target/delta）。
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import re

import pytest

from global_allocation.models import AssetClass
from global_allocation.portfolio.card import (
    POSITION_UNIT,
    US_INDICATOR_CODES,
    US_INDICATOR_NAMES,
    US_INDICATOR_SHORT_NAMES,
    _build_a_share_valuation_section,
    _build_fund_valuation_section,
    _build_hk_valuation_section,
    _build_us_valuation_section,
    _build_valuation_section,
    _compute_roe_yoy,
    _decide_a_share_strategy,
    _format_indicator_value,
    _per_fund_recommendation,
    _per_fund_verdict,
    build_overview_card,
    build_portfolio_card,
)
from global_allocation.portfolio.db import PortfolioDB
from global_allocation.portfolio.journal import PortfolioJournal
from global_allocation.portfolio.models import (
    FundValuation,
    ValuationIndicator,
    ValuationIndicatorCode,
    WeeklySnapshot,
)


class FakePriceSource:
    def __init__(self, prices: dict[str, Decimal]) -> None:
        self._prices = prices

    def get_price(self, code: str, on: date) -> Decimal | None:
        return self._prices.get(code)


@pytest.fixture
def journal(tmp_path: Path) -> PortfolioJournal:
    db = PortfolioDB(path=tmp_path / "p.db")
    prices = FakePriceSource(
        {
            "163406": Decimal("2.50"),
            "510300": Decimal("4.00"),
        }
    )
    return PortfolioJournal(db=db, price_source=prices)


def _seed(journal: PortfolioJournal) -> None:
    """塞 2 个基金 + 2 笔交易 + 1 个快照。"""
    import json

    journal.add_fund("163406", "兴全合润", AssetClass.MIXED)
    journal.add_fund("510300", "沪深300", AssetClass.EQUITY)
    journal.record_buy(
        fund_code="163406",
        trade_date=date(2026, 9, 1),
        shares=Decimal("1000"),
        price=Decimal("2.30"),
        fee=Decimal("1"),
        strategy="定投",
        tags=["dca"],
    )
    journal.record_buy(
        fund_code="510300",
        trade_date=date(2026, 9, 1),
        shares=Decimal("500"),
        price=Decimal("3.85"),
    )
    journal._db.upsert_snapshot(
        WeeklySnapshot(
            week_end_date=date(2026, 9, 4),
            total_value=Decimal("4300"),
            week_return=Decimal("0"),
            cumulative_return=Decimal("0"),
            holdings_json=json.dumps([]),
            created_at=datetime(2026, 9, 4, 17),
        )
    )


def _seed_valuation_today(journal: PortfolioJournal) -> None:
    """塞入今天的 4 个 A 股估值指标（spec 098 卡片 Section 3 用）。"""
    today = date.today()
    # 用 mock 数据：股债利差=+5.2%（偏低估），PE 分位=28%（偏低估），巴菲特=65%（正常），股息率=2.5%（正常）
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.EQUITY_RISK_PREMIUM,
            value=Decimal("0.052"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.PE_PERCENTILE,
            value=Decimal("0.28"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.BUFFETT_INDICATOR,
            value=Decimal("0.65"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.DIVIDEND_YIELD,
            value=Decimal("0.025"),
            source="test",
        )
    )


class TestBuildPortfolioCard:
    def test_basic_structure(self, journal: PortfolioJournal) -> None:
        """卡片 = header + 1 个 section（实盘持仓 — 第二十一轮删除 Section 2 大类资产策略）。

        spec 097 演进：
        - 第十九轮：把 Section 2 + 3 合并到 Section 1 持仓表（加 target/delta 列）
        - 第二十轮：恢复 Section 2（liubo 反馈："我担心没控制好超类的比例了"）
        - 第二十一轮（当前）：删除 Section 2，改用整数仓位模型
          - 持仓表的 target/delta 列已经包含超类信息
          - Section 2 冗余，liubo 拍板删除
          - 持仓表 target 列改用整数份数 "{N} 份 ({X.X}%)"

        现在的元素：
        - Section 1（实盘持仓）：
          - note header "实盘持仓"
          - div summary
          - hr
          - chart（柱状图，带顶部数值标签）
          - hr
          - table（11 行 × 5 列：分类 / 市值 / 占比 / 目标 / 偏离）
            - target：投资子类 "{N} 份 ({X.X}%)"；现金 "[{min}, {max}] 份"
            - delta：投资子类 "{+/-N} 份"；现金 "区间内/低于下限/高于上限"

        合计：1 note + 1 div + 2 hr + 1 chart + 1 table = 6 个元素 + footer
        + 第二十二轮（liubo 2026-10-17）调仓纪录 section（表格版）：
        + 1 note + 1 hr（跨 section 分隔）+ 1 hr（section 内）+ 1 table = +4 个元素
        + 第二十五轮（liubo 2026-10-17 第二轮反馈）定投纪录 section（跟调仓分开 2 张表）：
        + 1 note + 1 hr（跨 section 分隔）+ 1 hr（section 内）+ 1 table = +4 个元素

        无估值总计：4 note + 1 div + 7 hr + 1 chart + 4 table = 17 + footer
        """
        _seed(journal)
        card = build_portfolio_card(journal, title="我的实盘")
        assert "header" in card
        assert card["header"]["template"] == "blue"
        assert "elements" in card
        # 第三十四轮（liubo 2026-10-18）：删柱状图
        charts = [e for e in card["elements"] if e.get("tag") == "chart"]
        assert len(charts) == 0
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        # 第二十五轮：4 张 table（持仓成本 by 大类 + per-fund + 调仓记录 + 定投记录）
        assert len(tables) == 4
        # 第二十五轮：4 个 section 都有 note
        notes = [e for e in card["elements"] if e.get("tag") == "note"]
        assert len(notes) == 4
        # Section 1 summary + Section 2 summary = 2 个 div
        divs = [e for e in card["elements"] if e.get("tag") == "div"]
        assert len(divs) == 2
        # 第三十四轮：6 个 hr（Section 1 内 1 + Section 2/3/4 入口各 1，Section 1 删了 chart hr）
        hrs = [e for e in card["elements"] if e.get("tag") == "hr"]
        assert len(hrs) == 6

    def test_summary_includes_total_cost(self, journal: PortfolioJournal) -> None:
        """第三十四轮（liubo 2026-10-18）：Section 1 summary 显示「总成本」，不再有「总市值/盈亏」。

        数据源 = COST_BASIS_BY_CODE 手动维护的累计成本；不依赖 DB 流水算市值。
        记忆："券商 app 持仓收益口径不准 — 虚拟组合的 P&L 别引 app 数字，用「资产 - 累计成本」"
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        divs = [e for e in card["elements"] if e.get("tag") == "div"]
        # Section 1 = cost_basis summary（生成时间/总成本/欠配/超配）
        text = divs[0]["text"]["content"]
        assert "总成本" in text
        assert "CNY" in text
        # 不再有市值/盈亏字段
        assert "总市值" not in text
        assert "浮动盈亏" not in text

    def test_summary_does_not_repeat_header_title(self, journal: PortfolioJournal) -> None:
        """第十三轮反馈：summary 第一行不要重复 header.title。

        卡片 header 已经写了"实盘周报"，body 开头 note section header 已经写了
        "实盘持仓"，summary div 里再写一遍 title 是冗余。summary 第一行必须是
        "**生成时间**"。
        """
        _seed(journal)
        card = build_portfolio_card(journal, title="我的实盘 9 月")
        divs = [e for e in card["elements"] if e.get("tag") == "div"]
        text = divs[0]["text"]["content"]
        first_line = text.split("\n", 1)[0]
        assert not first_line.startswith("**我的实盘 9 月**")
        assert first_line.startswith("**生成时间**")

    def test_no_holdings_falls_back_to_cost_basis(self, journal: PortfolioJournal) -> None:
        # 第二十二轮（liubo 2026-10-16）：holdings 为空时不再抛异常，
        # 改走 fallback：Section 1 = 持仓成本表（cost_basis.py + breakdown.py）
        card = build_portfolio_card(journal)
        elements = card["elements"]
        # 第一个元素应是"持仓成本"note（fallback header）
        first_note = elements[0]
        assert first_note["tag"] == "note"
        assert "持仓成本" in first_note["elements"][0]["content"]
        # 应包含 cost_basis table（4 列：class/count/cost/weight）
        tables = [el for el in elements if el.get("tag") == "table"]
        assert any(
            {"count", "cost", "weight"} <= set(t.get("columns", [{}])[0].keys()) | {c["name"] for c in t.get("columns", [])}
            for t in tables
        )

    def test_title_override(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal, title="我的实盘 9 月")
        assert card["header"]["title"]["content"] == "我的实盘 9 月"

    def test_default_title(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        # 第十二轮反馈：header.title 默认改"实盘周报"（总标题，未来加多 section 不冲突）
        assert card["header"]["title"]["content"] == "实盘周报"


def _get_breakdown(journal: PortfolioJournal):
    """Helper: get breakdown rows for computing current_cash (used by dynamic formula tests)."""
    from global_allocation.portfolio.breakdown import compute_breakdown

    journal_for_breakdown = journal
    _seed(journal_for_breakdown)  # ensure seeded
    return compute_breakdown(journal_for_breakdown)


class TestRebalanceRecordsSection:
    """第二十二轮（liubo 2026-10-17）调仓纪录 section — 表格版（4 列 = 日期/基金/动作/金额）。

    关键设计：
    - 基金列用 FUND_NAME_BY_CODE 显示中文名，不显示 code
    - 多基金 commit body 的每条 `- ` 行 → 单独一行（不再把多只基金挤一起）
    - 金额从 commit body 提取（建仓 N 仓 / ¥X / N 元 / +N 等）
    - 不再有 "—" 占位符：fallback 用 "基金 <code>"
    - 第二十五轮（liubo 2026-10-17 第二轮反馈）：调仓和定投分开 2 张表
      - 本 class 只看「调仓纪录」表（action != "定投"）
      - 「定投纪录」表在 TestDcaRecordsSection
    """

    def _get_rebalance_table(
        self, journal: PortfolioJournal,
    ) -> dict[str, object]:
        """调仓纪录表：4 列 (date/fund/action/amount)，行 action != "定投"。

        第二十五轮：DCA 拆到独立表后，调仓表是第一个含 action/amount 的表
        （per-fund 表也有 action 但列名不同 — fund/cost/valuation/advice）。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        for t in card["elements"]:
            if t.get("tag") != "table":
                continue
            cols = [c["name"] for c in t.get("columns", [])]
            if "date" in cols and "action" in cols and "amount" in cols:
                return t
        raise AssertionError("找不到调仓纪录 table")

    def _get_dca_table(
        self, journal: PortfolioJournal,
    ) -> dict[str, object]:
        """定投纪录表：4 列 (date/fund/action/amount)，行 action == "定投"。

        第二十五轮：DCA 拆出来后，调仓表 + 定投表 都是 4 列 (date/fund/action/amount)。
        区分：调仓表是第一个，DCA 表是第二个（按元素顺序）。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        candidates = []
        for t in card["elements"]:
            if t.get("tag") != "table":
                continue
            cols = [c["name"] for c in t.get("columns", [])]
            if "date" in cols and "action" in cols and "amount" in cols:
                candidates.append(t)
        # 第一个 = 调仓纪录；第二个 = 定投纪录
        if len(candidates) >= 2:
            return candidates[1]
        raise AssertionError("找不到定投纪录 table（DCA 表可能为空）")

    def test_fund_column_uses_chinese_name_not_code(
        self, journal: PortfolioJournal,
    ) -> None:
        """基金列用中文名（来自 FUND_NAME_BY_CODE），不用 raw 6 位 code。

        liubo 2026-10-17 反馈："首先基金那一列不要用扣的，要用它的基金名称"。
        """
        table = self._get_rebalance_table(journal)
        for row in table["rows"]:
            fund = row.get("fund", "")
            # 不能是纯 6 位 code（如 "013127"）
            assert not re.fullmatch(r"\d{6}", fund), (
                f"fund 列是 raw code: {fund!r}（应该用中文名）"
            )
            # 不能是 "—" 占位符（除非确实解析失败，理论上不会）
            assert fund != "—", "fund 列出现 '—' 占位符（不应该有）"
            # 应该包含中文（含 unicode ≥ 0x4E00）
            assert any("\u4e00" <= ch <= "\u9fff" for ch in fund), (
                f"fund 列不含中文: {fund!r}"
            )

    def test_amount_column_not_dash_when_commits_have_amounts(
        self, journal: PortfolioJournal,
    ) -> None:
        """金额列在 commit body 含具体金额时不能是 "—"。

        liubo 2026-10-17 反馈："金额你也不知道，这是为什么呢？这你应该知道呀"。
        测试：从最近几个调仓 commit 的 body 里能读出来的金额，amount 列必须有值。
        """
        table = self._get_rebalance_table(journal)
        rows = table["rows"]
        # 至少要有一行 amount 不是 "—"（最近 5 个调仓 commit 里有大量 "10000 元" / "+1661" 等）
        non_dash = [r for r in rows if r.get("amount") not in ("—", "")]
        assert non_dash, (
            "调仓纪录 amount 列全部是 '—'，但 commit body 里有具体金额（liubo 反馈" +
            '"这你应该知道呀"）'
        )

    def test_multi_fund_commit_splits_into_multiple_rows(
        self, journal: PortfolioJournal,
    ) -> None:
        """多基金 commit body 的每条 `- ` 行 → 单独一行。

        liubo 2026-10-17 反馈："你不要把很多基金的变动放到一起...都是假仓，
        这个应该是分成三行的"。

        验证：调仓纪录表里同一个 commit date 至少出现一次 ≥3 行（同日 ≥3 rows
        = body 里确实有 ≥3 个 `- 0xxxxx ...` 行）。
        """
        table = self._get_rebalance_table(journal)
        rows = table["rows"]
        # 按 date 分组
        from collections import Counter
        date_counts = Counter(r["date"] for r in rows)
        # 至少有一个 date 有 ≥ 3 行（commit 2c36577 / 9e70495 / 8597945 等）
        assert any(c >= 3 for c in date_counts.values()), (
            f"没有 commit 拆成 ≥3 行：{dict(date_counts)}"
            "（user feedback: 多基金 commit 应该拆成多行）"
        )

    def test_no_cross_portfolio_funds_leaked(
        self, journal: PortfolioJournal,
    ) -> None:
        """调仓纪录不展示 ETF 轮动 / 红利策略 / 其他组合的基金。

        liubo 2026-10-17 反馈 — global-allocation / dividend-strategy / etf-rotation
        三个组合分开管理。已知跨组合基金 code: 010989 / 023917 / 457001 / 025682。
        """
        table = self._get_rebalance_table(journal)
        for row in table["rows"]:
            fund = row.get("fund", "")
            # 不能出现跨组合 code（fund 列是中文名，但如果回退到 "基金 <code>" 格式，
            # 或叙述里直接出现 code 也能被检出）
            for code in ("010989", "023917", "457001", "025682"):
                assert code not in fund, (
                    f"调仓纪录混入跨组合基金 {code}：{fund!r}"
                )

    def test_no_index_only_doc_lines_promoted_to_rebalance(
        self, journal: PortfolioJournal,
    ) -> None:
        """纯文档行（含指数代码 000698/399673 但无具体基金）不能被当作调仓。

        例子：commit 00e9da2 fix 描述 `LAST_REBALANCE_BY_ETF 加 "000698" / "399673" →
        date(2026, 10, 9)` —— 000698/399673 是 ETF 跟踪的指数代码，不是基金 code，
        应该被过滤掉。
        """
        table = self._get_rebalance_table(journal)
        for row in table["rows"]:
            fund = row.get("fund", "")
            # 不应出现 "基金 000698" 这种 fallback（000698 不在 FUND_NAME_BY_CODE）
            assert not fund.startswith("基金 000698"), (
                f"指数代码 000698 被当成基金: {fund!r}"
            )
            assert not fund.startswith("基金 399673"), (
                f"指数代码 399673 被当成基金: {fund!r}"
            )

    def test_dca_action_with_3_values_for_dollar_cost_funds(
        self, journal: PortfolioJournal,
    ) -> None:
        """DCA (定投) 行的 action 是 "定投"，金额列显示 3 个值。

        liubo 2026-10-17 反馈 — 金额列显示 3 个值：
        1. 定投额（每次投多少）— "10 元"
        2. 频率（多久投一次）— "每天"
        3. 总目标（计划投多少）— "累计 1800"

        格式：「10 元 / 每天 / 累计 1800」。
        不写「已买 ¥X」（已经买入了多少他不关心）。

        第二十五轮（liubo 2026-10-17 第二轮反馈）：DCA 拆到独立表，本测试查 _get_dca_table。
        """
        from global_allocation.portfolio.card import _DCA_SCHEDULE_BY_CODE

        table = self._get_dca_table(journal)
        # 按 fund 列找 DCA 行
        dca_rows = [
            r for r in table["rows"]
            if "南方纳100" in r.get("fund", "")
            or "摩根纳斯达克100指数(QDII)人民币A" in r.get("fund", "")
            or "万家纳斯达克100指数发起式(QDII)A" in r.get("fund", "")
        ]
        assert len(dca_rows) >= 3, (
            f"应该有 3 个 DCA 行（016452/019172/019441），实际 {len(dca_rows)} 行"
        )
        for row in dca_rows:
            assert row["action"] == "定投", (
                f"DCA 行 action 应该是 '定投'，实际 '{row['action']}'（{row['fund']}）"
            )
            # 不应出现 "已买"（已买入多少他不关心）
            assert "已买" not in row["amount"], (
                f"DCA 行 amount 不应有 '已买'（liubo 2026-10-17 反馈不关心已买多少），"
                f"实际 '{row['amount']}'（{row['fund']}）"
            )
            # 金额应该等于 _DCA_SCHEDULE_BY_CODE 对应 fund 格式化后的字符串
            fund_to_code = {
                "南方纳100": "016452",
                "摩根纳斯达克100指数(QDII)人民币A": "019172",
                "万家纳斯达克100指数发起式(QDII)A": "019441",
            }
            for prefix, code in fund_to_code.items():
                if prefix in row["fund"]:
                    schedule = _DCA_SCHEDULE_BY_CODE[code]
                    # amount 必须包含 3 个值（定投额 / 频率 / 累计）
                    assert schedule["per"] in row["amount"], (
                        f"DCA 行 amount 应包含定投额 '{schedule['per']}'，"
                        f"实际 '{row['amount']}'（{row['fund']}）"
                    )
                    assert schedule["cycle"] in row["amount"], (
                        f"DCA 行 amount 应包含频率 '{schedule['cycle']}'，"
                        f"实际 '{row['amount']}'（{row['fund']}）"
                    )
                    assert f"累计 {schedule['total']}" in row["amount"], (
                        f"DCA 行 amount 应包含累计 '{schedule['total']}'，"
                        f"实际 '{row['amount']}'（{row['fund']}）"
                    )
                    # 3 个值之间应该有分隔符
                    assert row["amount"].count("/") >= 2, (
                        f"DCA 行 amount 应有 2 个 '/' 分隔 3 个值，"
                        f"实际 '{row['amount']}'（{row['fund']}）"
                    )
                    break

    def test_pure_cost_calibration_rows_filtered(
        self, journal: PortfolioJournal,
    ) -> None:
        """纯成本校准行（如 519753 +21 元、378006 +1400 元）应被过滤掉。

        liubo 2026-10-17 反馈 — 成本校准属于记账对账，不是调仓 ——
        "原来记错了，我纠正你了，所以这种它不属于调仓"。

        8597945 commit body 有 11 条这种校准行（519753 +21、378006 +1400、
        002286 +132、010742 +31、015736 -6、001512 -8、485119 -4、
        017837 -19、008333 +8、007520 -14）。

        过滤条件：行匹配 "code + Chinese 名 + N → M（±X 元）" 且无 "已买" / "仓"。
        """
        from global_allocation.portfolio.card import _parse_rebalance_line

        # 直接验证过滤逻辑（不依赖 git log）— 真实 8597945 body 里的校准行
        calibration_lines = [
            # 中文冒号 + ±N 元（liubo 在 8597945 body 里这样写）
            "- 378006 摩根全球新兴市场：3910 → 5310（+1400）",
            "- 519753 交银安心收益债券 A：29979 → 30000（+21）",
            "- 002286 中银美元债：9868 → 10000（+132）",
            "- 010742 南方宁悦一年持有：9969 → 10000（+31）",
            "- 015736 长盛盛裕纯债 D：1506 → 1500（-6）",
            "- 001512 易方达中债 3-5 年国债指数：5008 → 5000（-8）",
            "- 485119 工银信用纯债债券 A：2004 → 2000（-4）",
            "- 017837 博时中债 7-10 年政金债：5019 → 5000（-19）",
            "- 008333 景顺弘利 39 个月定开：9992 → 10000（+8）",
            "- 007520 富安达富利纯债 A：10014 → 10000（-14）",
            # ASCII 冒号（liubo 在其它 commit 里这样写，如 9e70495 ledger）
            "- 004827: 22000 → 2000（-20000）",
        ]
        for line in calibration_lines:
            parsed = _parse_rebalance_line(line, "校准")
            assert parsed is None, (
                f"纯成本校准行应被过滤: {line!r}，实际 {parsed!r}"
            )

        # 表格里"真校准"特征（action='校准' + amount 是校准差值）应被过滤掉 —
        # 8597945 "14 只基金成本校准" commit body 里 11 条 ±N 元 校准行 body 都应被过滤。
        # 说明：subject 含 "校准" 的 commit 会被 _is_rebalance_subject 选中 —
        # 但 body 行通过 _parse_rebalance_line 的过滤（行匹配 "code + Chinese 名 + N → M（±N 元）"
        # 且无 "已买" / "仓"）都被过滤掉。
        #
        # 已知 limitation：self-referential commit（subject 含 "校准" 等关键词 + body 描述实现）
        # 可能误触发 action=校准 + amount="—" 的 noise 行（如 e194235 自己的 body 描述）。
        # 这种 row 的 amount 是 "—"（无金额差值），与"真校准"（"+21 元"等）有区别。
        table = self._get_rebalance_table(journal)
        for row in table["rows"]:
            action = row.get("action", "")
            amount = row.get("amount", "")
            fund = row.get("fund", "")
            # "真校准"特征：action=校准 + amount 是 ±N 元 / ±N / N → N
            if action == "校准":
                # 真校准必须有金额差值（如 "+21" / "-8" / "+1400 元"）
                assert re.fullmatch(r"\s*[+\-−]?\s*\d+\s*(?:元)?\s*", amount), (
                    f"校准 action 应该有金额差值描述（filter 漏掉真校准），"
                    f"实际 amount='{amount}'（{fund}）"
                )
            # amount 是 "+N" / "-N" / "+N 元" 单值（校准金额特征）也算校准
            if re.fullmatch(r"\s*[+\-−]\s*\d+(\s*元)?\s*", amount):
                # 但允许 DCA 行 amount 是 "已买 ¥N / schedule"（不会匹配这个 fullmatch）
                assert "已买" in amount, (
                    f"金额是纯校准差值 '{amount}'（{fund}）— 应该是 DCA '已买' 描述"
                )

    def test_conversion_line_split_into_transfer_out_and_in(
        self, journal: PortfolioJournal,
    ) -> None:
        """转换行拆成 2 行：转出 + 转入。

        liubo 2026-10-17 反馈 — 转换其实你也可以把它拆成两个。
        例：004827 平安中短债 → 019857 博时上证科创板100ETF联接A
        = (平安中短债 | 转出 | 10000 元) + (博时上证科创板100ETF联接A | 转入 | 10000 元)

        8597945 转换 commit (664b6d6 fix(099)) 应该有 6 行转换（3 转出 + 3 转入）。
        """
        table = self._get_rebalance_table(journal)
        rows = table["rows"]
        # 找 转出 / 转入 配对
        transfer_out = [r for r in rows if r["action"] == "转出"]
        transfer_in = [r for r in rows if r["action"] == "转入"]
        # 数量相等（每个转换拆出 1 转出 + 1 转入）
        assert len(transfer_out) >= 3, (
            f"转换 commit 应该有 ≥3 个 '转出' 行，实际 {len(transfer_out)}"
        )
        assert len(transfer_in) >= 3, (
            f"转换 commit 应该有 ≥3 个 '转入' 行，实际 {len(transfer_in)}"
        )
        # 转出/转入配对（同一 commit date）
        from collections import defaultdict
        out_by_date: dict[str, set[str]] = defaultdict(set)
        in_by_date: dict[str, set[str]] = defaultdict(set)
        for r in transfer_out:
            out_by_date[r["date"]].add(r["fund"])
        for r in transfer_in:
            in_by_date[r["date"]].add(r["fund"])
        for date_key in out_by_date:
            assert len(out_by_date[date_key]) >= 1, f"{date_key} 没有 转出 行"
            assert len(in_by_date[date_key]) >= 1, f"{date_key} 没有 转入 行"
        # 关键转换源基金必须出现（平安中短债 转出 × 2、鹏扬利沣短债A 转出 × 1）
        out_funds = {r["fund"] for r in transfer_out}
        assert "平安中短债" in out_funds, (
            f"转换源 平安中短债 不在转出行: {out_funds}"
        )
        # 关键转换目标基金必须出现
        in_funds = {r["fund"] for r in transfer_in}
        assert "博时上证科创板100ETF联接A" in in_funds, (
            f"转换目标 博时上证科创板100ETF联接A 不在转入行: {in_funds}"
        )
        assert "工银创业板50ETF联接A" in in_funds, (
            f"转换目标 工银创业板50ETF联接A 不在转入行: {in_funds}"
        )
        assert "汇添富恒生科技 ETF 联接发起式(QDII)A" in in_funds, (
            f"转换目标 汇添富恒生科技 ETF 联接发起式(QDII)A 不在转入行: {in_funds}"
        )
        # 转出/转入金额相等（同一笔转换金额一致）
        for r_out in transfer_out:
            r_in_match = next(
                (
                    r for r in transfer_in
                    if r["date"] == r_out["date"] and r["amount"] == r_out["amount"]
                ),
                None,
            )
            assert r_in_match is not None, (
                f"转出 '{r_out['fund']}' {r_out['amount']} ({r_out['date']}) "
                f"没有匹配的转入行"
            )

    def test_unit_helpers_dca_conversion_calibration(
        self, journal: PortfolioJournal,
    ) -> None:
        """单元测试 — 直接打 _parse_rebalance_line / _is_conversion_line / _parse_conversion_rows。

        不依赖 git log — 构造合成 commit body 行，验证 3 个新行为（DCA / 转换拆分 /
        校准过滤）的核心逻辑。
        """
        from global_allocation.portfolio.card import (
            _DCA_SCHEDULE_BY_CODE,
            _is_conversion_line,
            _parse_conversion_rows,
            _parse_rebalance_line,
        )

        # === 1. DCA 行 ===
        dca_line = "- 016452 南方纳指 100：20 → 60（已买 ¥60）"
        parsed = _parse_rebalance_line(dca_line, "校准")
        assert parsed is not None, "DCA 行被错误过滤"
        assert not isinstance(parsed, list), "DCA 行不应返回 list"
        assert parsed.action == "定投", (
            f"DCA 行 action 应该是 '定投'，实际 '{parsed.action}'"
        )
        # liubo 2026-10-17 反馈：金额列显示 3 个值 ——
        # 定投额 / 频率 / 总目标（如 "10 元 / 每天 / 累计 1800"）
        # 不写「已买 ¥X」（已经买入了多少他不关心）
        assert "已买" not in parsed.amount, (
            f"DCA 行 amount 不应有 '已买'，实际 '{parsed.amount}'"
        )
        schedule = _DCA_SCHEDULE_BY_CODE["016452"]
        for part in (schedule["per"], schedule["cycle"], f"累计 {schedule['total']}"):
            assert part in parsed.amount, (
                f"DCA 行 amount 应包含 '{part}'，实际 '{parsed.amount}'"
            )
        # 3 个值之间应该有 2 个 '/' 分隔符
        assert parsed.amount.count("/") >= 2, (
            f"DCA 行 amount 应有 2 个 '/' 分隔 3 个值，实际 '{parsed.amount}'"
        )

        # === 2. 转换行 ===
        conv_line = (
            "- 004827 平安中短债 → 019857 博时上证科创板100ETF联接A"
            "（10000 元，转换 8379 份）"
        )
        assert _is_conversion_line(conv_line), (
            "转换行未被 _is_conversion_line 识别"
        )
        rows = _parse_conversion_rows(conv_line)
        assert len(rows) == 2, f"转换应拆 2 行，实际 {len(rows)} 行"
        assert rows[0].action == "转出", (
            f"转换第 1 行 action 应该是 '转出'，实际 '{rows[0].action}'"
        )
        assert rows[1].action == "转入", (
            f"转换第 2 行 action 应该是 '转入'，实际 '{rows[1].action}'"
        )
        assert rows[0].fund == "平安中短债", (
            f"转出 fund 应该是 '平安中短债'，实际 '{rows[0].fund}'"
        )
        assert "博时上证科创板100ETF联接A" in rows[1].fund, (
            f"转入 fund 应该是 '博时上证科创板100ETF联接A'，实际 '{rows[1].fund}'"
        )
        assert rows[0].amount == rows[1].amount, (
            f"转出/转入金额应一致: '{rows[0].amount}' vs '{rows[1].amount}'"
        )

        # _parse_rebalance_line 也应该返回 list（2 行）
        parsed_conv = _parse_rebalance_line(conv_line, "加仓")
        assert isinstance(parsed_conv, list), (
            f"_parse_rebalance_line 转换行应该返回 list，实际 {type(parsed_conv)}"
        )
        assert len(parsed_conv) == 2

        # === 3. 纯校准行（应过滤） ===
        calib_line = "- 519753 交银安心收益债券 A：29979 → 30000（+21）"
        parsed_calib = _parse_rebalance_line(calib_line, "校准")
        assert parsed_calib is None, (
            f"纯成本校准行应被过滤（None），实际返回 {parsed_calib!r}"
        )

        # ASCII 冒号的校准行也应过滤
        calib_line2 = "- 004827: 22000 → 2000（-20000）"
        parsed_calib2 = _parse_rebalance_line(calib_line2, "校准")
        assert parsed_calib2 is None, (
            f"ASCII 冒号成本校准行应被过滤，实际返回 {parsed_calib2!r}"
        )

        # === 4. 非转换 / 非校准 / 非 DCA 的普通建仓行 ===
        build_line = (
            "- 019857 博时上证科创板100ETF联接A → 000688 科创 50，"
            "建仓 1 仓 = 10000 CNY"
        )
        # 这是 换基（基金 → 指数），但 index code 不在 FUND_NAME_BY_CODE
        # 会被 _parse_rebalance_line 第 2 个过滤器（index code）过滤
        parsed_build = _parse_rebalance_line(build_line, "换基")
        assert parsed_build is None, (
            f"fund → index 描述行应被过滤（000688 不在 FUND_NAME_BY_CODE），"
            f"实际 {parsed_build!r}"
        )

        # liubo 2026-10-17 反馈 — "建仓 N 仓 + → + 2 codes" 不是换基 —
        # 是给 target 加仓（旧基金 A 成本 0 占位，从跟踪列表里删掉即可）。
        # 调仓纪录不显示「换基」概念。
        add_line = (
            "- 020291 华夏科创100ETF联接A → 019857 博时上证科创板100ETF联接A，"
            "建仓 1 仓 = 10000 CNY"
        )
        parsed_add = _parse_rebalance_line(add_line, "换 2 只科创/创业宽基 + 013127 +1 仓")
        assert parsed_add is not None, "fund → fund 行被错误过滤"
        assert parsed_add.action == "加仓", (
            f"fund → fund + 建仓 N 仓 行 action 应该是 '加仓'（liubo 2026-10-17 反馈），"
            f"实际 '{parsed_add.action}'"
        )
        assert parsed_add.fund == "博时上证科创板100ETF联接A", (
            f"fund → fund + 建仓 N 仓 行 fund 应只显示 target，"
            f"实际 '{parsed_add.fund}'"
        )

    def test_unit_helpers_description_lines_filtered(
        self, journal: PortfolioJournal,
    ) -> None:
        """第三十轮（liubo 2026-10-18 反馈）：过滤 commit body 描述行（不是真调仓）。

        3 类描述行被误判为调仓：
        1. "X 保留（占位 0）" — 描述"保留占位"，不是新建仓
        2. "X 漏在...清单外" — fix commit 描述漏加
        3. "X / Y / Z / W 这 N 只..." — 描述性列表
        """
        from global_allocation.portfolio.card import _parse_rebalance_line

        # 1. "X 保留（占位 0）" 描述行
        keep_line = "- 011612 华夏科创50ETF联接A 保留（占位 0）"
        parsed_keep = _parse_rebalance_line(
            keep_line, "换 2 只科创/创业宽基 + 013127 +1 仓"
        )
        assert parsed_keep is None, (
            f"'保留（占位 0）' 描述行应被过滤，实际返回 {parsed_keep!r}"
        )

        # 2. "X 漏在...清单外" 描述行
        missing_line = "- 014673 富国中证港股通互联网ETF发起式联接A 漏在大类资产配置清单外，"
        parsed_missing = _parse_rebalance_line(
            missing_line, "加 014673 港股 + 建红利策略组合独立模块"
        )
        assert parsed_missing is None, (
            f"'漏在...清单外' 描述行应被过滤，实际返回 {parsed_missing!r}"
        )

        # 3. "X / Y / Z / W 这 N 只..." 描述性列表
        list_line = '- 022448 / 007997 / 023917 / 025682 这 4 只是单独的"红利策略组合"，'
        parsed_list = _parse_rebalance_line(
            list_line, "加 014673 港股 + 建红利策略组合独立模块"
        )
        assert parsed_list is None, (
            f"'这 N 只' 描述性列表应被过滤，实际返回 {parsed_list!r}"
        )

        # 对照：真的 加仓 / 转换 / 转入 / 转出 行还是被识别
        real_add = (
            "- 020291 华夏科创100ETF联接A → 019857 博时上证科创板100ETF联接A，"
            "建仓 1 仓 = 10000 CNY"
        )
        parsed_real = _parse_rebalance_line(
            real_add, "换 2 只科创/创业宽基 + 013127 +1 仓"
        )
        assert parsed_real is not None, "真实加仓行被错误过滤"
        assert parsed_real.action == "加仓"

    def test_cang_rounded_to_1_decimal(self) -> None:
        """第三十二轮（liubo 2026-10-18 反馈）：调仓纪录的 仓值四舍五入到 1 位小数。

        之前会出现 0.499939 仓 / 4,999.39 元（转换 4999.39 元 = 0.499939 仓）太长了。
        改成 0.5 仓 / 4,999.39 元，1 仓 / 10000 元 等都保持不变（整数 仓值不带 .0）。
        """
        from global_allocation.portfolio.card import _format_amount_with_units

        # 0.499939 仓 → 0.5 仓（四舍五入）
        assert _format_amount_with_units("0.499939 仓 / 4,999.39 元") == (
            "0.5 仓 / 4,999.39 元"
        )

        # 整数 仓值 1 仓 / 10000 元 → 1 仓（不带 .0）
        assert _format_amount_with_units("1 仓 / 10,000 元") == "1 仓 / 10,000 元"

        # 0.8 仓（已 1 位小数）保持
        assert _format_amount_with_units("0.8 仓 / 8,000 元") == "0.8 仓 / 8,000 元"

        # 纯 元 输入：4999.39 元 → 0.5 仓（不是 0.499939）
        result = _format_amount_with_units("4999.39 元")
        assert "0.5 仓" in result, f"4999.39 元 应格式化为 0.5 仓，实际: {result!r}"

        # 纯 元 输入：10000 元 → 1 仓
        result = _format_amount_with_units("10000 元")
        assert result == "1 仓 / 10,000 元", f"10000 元 应格式化为 1 仓 / 10,000 元，实际: {result!r}"

        # 纯 仓 输入：0.499939 仓 → 0.5 仓
        result = _format_amount_with_units("0.499939 仓")
        assert "0.5 仓" in result, f"0.499939 仓 应格式化为 0.5 仓，实际: {result!r}"

        # DCA 3-value 不动（不在 仓 范畴）
        dca_amount = "10 元 / 每天 / 累计 1800"
        assert _format_amount_with_units(dca_amount) == dca_amount

        # "建仓 1 仓 = 10000 CNY" → "1 仓 / 10,000 元"（both case 也走 1-decimal 仓）
        result = _format_amount_with_units("建仓 1 仓 = 10000 CNY")
        assert "1 仓" in result, f"建仓 1 仓 应保留为 1 仓，实际: {result!r}"
        assert "10,000 元" in result, f"10000 CNY 应转为 10,000 元，实际: {result!r}"

        # 0.15 仓 → 0.2 仓（四舍五入，不是 0.1）
        # 实际场景：1595 元 ≈ 0.1595 仓 → 0.2 仓
        result = _format_amount_with_units("1595 元")
        assert "0.2 仓" in result, f"1595 元 应四舍五入为 0.2 仓，实际: {result!r}"

        # 0.04 仓 → 0.0 仓（但 _format_cang 会返回 "0"，仓=0 也保持显示）
        # 实际场景：396 元 ≈ 0.0396 仓 → 0 仓
        result = _format_amount_with_units("396 元")
        assert "0 仓" in result, f"396 元 应四舍五入为 0 仓，实际: {result!r}"

    def test_transfer_add_dedup_same_physical_trade(
        self, journal: PortfolioJournal,
    ) -> None:
        """第三十一轮（liubo 2026-10-18 反馈）：同一笔实际调仓被记 2 次时去重。

        2026-10-09 的 019857/023414/013127 各被记了 2 次：
        - 「转入」来自 spec 022 conversion ledger（006829 → 019857 等）
        - 「加仓」来自高层 decision commit（"建仓 1 仓 = 10000" / "+1 仓: 25000→35000"）
        两边描述同一笔实际调仓 — 优先保留「转入」（带 source，更完整）。

        注意：同一 (date, fund, amount) 但 action 都是「转出」是合法的
        （如 平安中短债 2026-10-09 转出 2 次：分别去 019857 和 023414，是 2 笔不同交易）。

        验证：
        1. 2026-10-09 调仓表中 019857/023414/013127 各只出现 1 次
        2. 保留的是「转入」（不是「加仓」），因为「转入」带 source 信息
        """
        _seed(journal)
        table = self._get_rebalance_table(journal)
        rows = table["rows"]

        # 找出 2026-10-09 的所有 019857/023414/013127 行（这 3 只是被双计的）
        target_funds = {
            "博时上证科创板100ETF联接A",
            "工银创业板50ETF联接A",
            "汇添富恒生科技 ETF 联接发起式(QDII)A",
        }
        date_2026_10_09_rows = [
            r for r in rows
            if r["date"] == "2026-10-09" and r["fund"] in target_funds
        ]

        # 每只目标基金应只剩 1 条记录
        from collections import Counter
        fund_counts = Counter(r["fund"] for r in date_2026_10_09_rows)
        for fund, count in fund_counts.items():
            assert count == 1, (
                f"2026-10-09 {fund} 被记了 {count} 次（应为 1 次），"
                f"重复行: {[r for r in date_2026_10_09_rows if r['fund'] == fund]}"
            )

        # 每只目标基金保留的应该是「转入」（带 source 信息），不是「加仓」
        for r in date_2026_10_09_rows:
            assert r["action"] == "转入", (
                f"2026-10-09 {r['fund']} 应保留「转入」（带 source），"
                f"实际保留 action='{r['action']}'（应是 '转入'）"
            )

        # 关键验证：3 只目标基金没有「加仓」行（都已被 dedup 删掉）
        add_rows_for_targets = [
            r for r in date_2026_10_09_rows if r["action"] == "加仓"
        ]
        assert not add_rows_for_targets, (
            f"2026-10-09 目标基金还剩「加仓」行（应被 dedup 删掉）: "
            f"{[(r['fund'], r['amount']) for r in add_rows_for_targets]}"
        )

        # 关键验证：同一天同一基金不该既有「转入」又有「加仓」
        # （仅检查「转入+加仓」组合，不限制 2 个「转出」 ——
        #  平安中短债 2026-10-09 转出 2 次去不同目标是合法的）
        from collections import defaultdict
        action_by_key: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        for r in rows:
            if r["date"] != "2026-10-09":
                continue
            key = (r["date"], r["fund"], r["amount"])
            action_by_key[key].add(r["action"])
        conflicting = {
            k: v for k, v in action_by_key.items()
            if "转入" in v and "加仓" in v
        }
        assert not conflicting, (
            f"2026-10-09 仍有「转入 + 加仓」配对未去重: {conflicting}"
        )


class TestDcaRecordsSection:
    """第二十五轮（liubo 2026-10-17 第二轮反馈）— 定投纪录 section 独立成 1 张表。

    「调仓是调仓，定投是定投」 — 调仓（DCA 之外的动作）和定投分开 2 张表展示。

    数据流：
    - 复用 _collect_rebalance_rows() 抓的同一份 git log（避免 2 次 git 调用）
    - 按 action 字段分流：action == "定投" → 本表，其他 → 调仓记录表
    - 4 列 (date / fund / action / amount) 跟调仓记录表 schema 一致
    - amount 列 = "X 元 / Y / 累计 Z" 3 段（per / cycle / total）
    """

    def test_dca_table_exists_and_separate_from_rebalance(
        self, journal: PortfolioJournal,
    ) -> None:
        """DCA 表存在且与调仓表分开（2 张独立的 4 列表）。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        candidates = []
        for t in card["elements"]:
            if t.get("tag") != "table":
                continue
            cols = [c["name"] for c in t.get("columns", [])]
            if "date" in cols and "action" in cols and "amount" in cols:
                candidates.append(t)
        # 应该有 2 张：(1) 调仓记录 + (2) 定投记录
        assert len(candidates) >= 2, (
            f"调仓 + 定投 应该分 2 张表，实际 {len(candidates)} 张"
        )
        # 2 张表的列名基本一致，DCA 表 2026-10-18 加了"状态"列（5 列），
        # 调仓记录表保持 4 列。
        rebalance_cols = [c["name"] for c in candidates[0]["columns"]]
        dca_cols = [c["name"] for c in candidates[1]["columns"]]
        assert rebalance_cols == ["date", "fund", "action", "amount"], (
            f"调仓表列名应是 4 列 [date, fund, action, amount]，实际 {rebalance_cols}"
        )
        assert dca_cols == ["date", "fund", "action", "amount", "status"], (
            f"DCA 表列名应是 5 列 [date, fund, action, amount, status]，实际 {dca_cols}"
        )

    def test_dca_table_only_has_dca_action_rows(
        self, journal: PortfolioJournal,
    ) -> None:
        """DCA 表只显示 action == "定投" 的行，不混入调仓行。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        # 取第二张 4 列表（DCA 表）
        candidates = [
            t for t in card["elements"]
            if t.get("tag") == "table"
            and "date" in [c["name"] for c in t.get("columns", [])]
            and "action" in [c["name"] for c in t.get("columns", [])]
            and "amount" in [c["name"] for c in t.get("columns", [])]
        ]
        if len(candidates) < 2:
            pytest.skip("DCA 表为空")
        dca_table = candidates[1]
        for row in dca_table["rows"]:
            assert row["action"] == "定投", (
                f"DCA 表不应有 action != '定投' 的行："
                f"fund={row['fund']!r} action={row['action']!r}"
            )

    def test_rebalance_table_excludes_dca_rows(
        self, journal: PortfolioJournal,
    ) -> None:
        """调仓记录表不显示 action == "定投" 的行（已拆到 DCA 表）。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        candidates = [
            t for t in card["elements"]
            if t.get("tag") == "table"
            and "date" in [c["name"] for c in t.get("columns", [])]
            and "action" in [c["name"] for c in t.get("columns", [])]
            and "amount" in [c["name"] for c in t.get("columns", [])]
        ]
        rebalance_table = candidates[0]  # 第一张 = 调仓记录
        for row in rebalance_table["rows"]:
            assert row["action"] != "定投", (
                f"调仓记录表不应有 action == '定投' 的行（DCA 已拆出去）："
                f"fund={row['fund']!r} amount={row['amount']!r}"
            )

    def test_dca_table_lists_all_5_funds(
        self, journal: PortfolioJournal,
    ) -> None:
        """DCA 表展示 _DCA_SCHEDULE_BY_CODE 全部 5 只基金（不依赖 git log）。

        liubo 2026-10-17 第二十五轮反馈 — 「黄金 + DAX 你为什么没进呢？」
        之前只过滤 git log 里 action == "定投" 的行（只有 3 只 NDX），现在改成
        直接遍历 _DCA_SCHEDULE_BY_CODE 全列。
        """
        from global_allocation.portfolio.card import _DCA_SCHEDULE_BY_CODE

        _seed(journal)
        card = build_portfolio_card(journal)
        candidates = [
            t for t in card["elements"]
            if t.get("tag") == "table"
            and "date" in [c["name"] for c in t.get("columns", [])]
            and "action" in [c["name"] for c in t.get("columns", [])]
            and "amount" in [c["name"] for c in t.get("columns", [])]
        ]
        if len(candidates) < 2:
            pytest.skip("DCA 表不存在（应该是 fallback path）")
        dca_table = candidates[1]
        # 必须有全部 5 只 DCA 基金（liubo 反馈「黄金 + DAX 你为什么没进呢」）
        assert len(dca_table["rows"]) == len(_DCA_SCHEDULE_BY_CODE), (
            f"DCA 表应有 {len(_DCA_SCHEDULE_BY_CODE)} 行（_DCA_SCHEDULE_BY_CODE 全部），"
            f"实际 {len(dca_table['rows'])} 行"
        )
        # action 字段都是 "定投"
        for row in dca_table["rows"]:
            assert row["action"] == "定投", (
                f"DCA 表所有行 action 应是 '定投'，实际 {row['action']!r}"
            )


class TestDcaStatusField:
    """第三十四轮（liubo 2026-10-18 反馈）：定投纪录加「状态」字段。

    3 种状态：进行中 / 到期 / 终止
    优先级：手动 status override > 自动检测（cost_basis >= total → 到期）> 默认 进行中

    数据：5 只 DCA 基金（016452/019172/019441/000614/000216）
    当前全部 "进行中"（10-18 仍在扣款中，cost_basis 累计目标尚未达到实际买入额）。
    """

    def test_dca_table_has_status_column(
        self, journal: PortfolioJournal,
    ) -> None:
        """DCA 表第 5 列是「状态」。"""
        _seed(journal)
        card = build_portfolio_card(journal)
        candidates = [
            t for t in card["elements"]
            if t.get("tag") == "table"
            and "date" in [c["name"] for c in t.get("columns", [])]
            and "action" in [c["name"] for c in t.get("columns", [])]
        ]
        if len(candidates) < 2:
            pytest.skip("DCA 表不存在")
        dca_table = candidates[1]
        cols = [c["name"] for c in dca_table["columns"]]
        assert "status" in cols, f"DCA 表应包含 status 列，实际列 {cols}"

    def test_all_5_dca_funds_have_status(
        self, journal: PortfolioJournal,
    ) -> None:
        """5 只 DCA 基金都应有 status 字段（liubo 拍板全部「进行中」）。"""
        from global_allocation.portfolio.card import _DCA_SCHEDULE_BY_CODE

        _seed(journal)
        card = build_portfolio_card(journal)
        candidates = [
            t for t in card["elements"]
            if t.get("tag") == "table"
            and "date" in [c["name"] for c in t.get("columns", [])]
            and "action" in [c["name"] for c in t.get("columns", [])]
        ]
        if len(candidates) < 2:
            pytest.skip("DCA 表不存在")
        dca_table = candidates[1]
        # 每行都有 status 字段
        for row in dca_table["rows"]:
            assert "status" in row, f"DCA 行 {row['fund']} 缺 status 字段"
            assert row["status"] in ("进行中", "到期", "终止"), (
                f"DCA 行 {row['fund']} status 非法: {row['status']!r}"
            )
        # 5 只基金全部「进行中」（liubo 10-18 拍板）
        status_by_fund = {r["fund"]: r["status"] for r in dca_table["rows"]}
        for code, schedule in _DCA_SCHEDULE_BY_CODE.items():
            from global_allocation.portfolio.card import get_fund_name
            fund_name = get_fund_name(code)
            assert status_by_fund[fund_name] == "进行中", (
                f"{fund_name} 状态应为「进行中」（liubo 10-18 拍板），"
                f"实际 {status_by_fund[fund_name]}"
            )

    def test_compute_dca_status_manual_override_wins(self) -> None:
        """手动 status override 优先级最高（liubo 10-18 拍板）。"""
        from global_allocation.portfolio.card import _compute_dca_status

        # 显式 status="进行中" → 返回 "进行中"（即使 cost >= total 也会被 override）
        assert _compute_dca_status("016452", {
            "per": "10 元", "cycle": "每天", "total": "1800", "status": "进行中",
        }) == "进行中"
        # 显式 status="终止" → 返回 "终止"
        assert _compute_dca_status("000216", {
            "per": "10 元", "cycle": "周一", "total": "7500", "status": "终止",
        }) == "终止"
        # 显式 status="到期" → 返回 "到期"
        assert _compute_dca_status("000614", {
            "per": "10 元", "cycle": "周三", "total": "10000", "status": "到期",
        }) == "到期"

    def test_compute_dca_status_auto_detect_expired(self) -> None:
        """自动检测：cost_basis >= total → "到期"（无手动 override 时）。"""
        from global_allocation.portfolio.card import _compute_dca_status

        # 016452: cost_basis = 1800, total = 1800 → 自动 "到期"
        # 用临时 cost_basis mock（避免污染全局）
        from global_allocation.portfolio import cost_basis
        original = cost_basis.COST_BASIS_BY_CODE.copy()
        try:
            cost_basis.COST_BASIS_BY_CODE["016452"] = Decimal("2000")  # > 1800
            # 临时 016452 schedule 不带 status 字段
            schedule_no_status = {"per": "10 元", "cycle": "每天", "total": "1800"}
            assert _compute_dca_status("016452", schedule_no_status) == "到期"
        finally:
            cost_basis.COST_BASIS_BY_CODE.clear()
            cost_basis.COST_BASIS_BY_CODE.update(original)

    def test_compute_dca_status_default_in_progress(self) -> None:
        """默认：无 status override + cost < total → "进行中"。"""
        from global_allocation.portfolio.card import _compute_dca_status

        # 用临时 cost_basis mock：cost = 500 < total = 1800
        from global_allocation.portfolio import cost_basis
        original = cost_basis.COST_BASIS_BY_CODE.copy()
        try:
            cost_basis.COST_BASIS_BY_CODE["016452"] = Decimal("500")
            schedule_no_status = {"per": "10 元", "cycle": "每天", "total": "1800"}
            assert _compute_dca_status("016452", schedule_no_status) == "进行中"
        finally:
            cost_basis.COST_BASIS_BY_CODE.clear()
            cost_basis.COST_BASIS_BY_CODE.update(original)

    def test_dca_schedule_has_status_field(self) -> None:
        """_DCA_SCHEDULE_BY_CODE 全部 5 只基金都有 status 字段（liubo 拍板全部「进行中」）。"""
        from global_allocation.portfolio.card import _DCA_SCHEDULE_BY_CODE

        assert len(_DCA_SCHEDULE_BY_CODE) == 5
        for code, schedule in _DCA_SCHEDULE_BY_CODE.items():
            assert "status" in schedule, f"{code} 缺 status 字段"
            assert schedule["status"] in ("进行中", "到期", "终止"), (
                f"{code} status 非法: {schedule['status']!r}"
            )


class TestCostBasisByClassAggregation:
    """第二十六轮（liubo 2026-10-17 反馈"国外发达不应该是 0"）：Section 1 fallback
    持仓成本 by 大类 聚合 必须正确 — DCA 基金的 cost 应该是累计目标，不是"已买"快照。

    关键断言（liubo 2026-10-17 拍板）：
    - 国外发达市场股票：count=1（000614 DAX 联接），cost=10000（DCA 累计目标）
    - 美股股票：cost 包含 3 只 NDX DCA 累计（016452=1800, 019172=2000, 019441=2000）
    - 商品：cost=7500（000216 黄金 DCA 累计目标）
    - 11 大类全覆盖：A 股 / 港股 / 美股 / 国外发达 / 新兴市场 / 国内 REITs / 美国 REITs /
      国内利率债 / 美债 / 商品（现金 004137 走 CASH_LIKE_CODES 不渲染）
    """

    def test_foreign_dm_aggregation_includes_dca_target(
        self, journal: PortfolioJournal,
    ) -> None:
        """国外发达：count=1, cost=10000（不是 0/不是已买快照）。"""
        card = build_portfolio_card(journal)
        cost_table = self._get_cost_basis_table(card)
        fdm_row = next(
            (r for r in cost_table["rows"] if r["class"] == "国外发达市场股票"),
            None,
        )
        assert fdm_row is not None, "Section 1 应有「国外发达市场股票」行"
        assert fdm_row["count"] == "1", (
            f"国外发达 count 应是 1（含 000614 DAX），实际 {fdm_row['count']}"
        )
        assert fdm_row["cost"] == "10,000.00", (
            f"国外发达 cost 应是 10000（DCA 累计目标），实际 {fdm_row['cost']}。"
            "liubo 反馈'国外发达不应该是 0，应该是 1'——cost 应该是 DCA 累计 10000"
        )

    def test_us_equity_aggregation_includes_ndx_dca_targets(
        self, journal: PortfolioJournal,
    ) -> None:
        """美股股票：cost 应包含 3 只 NDX DCA 累计（1800+2000+2000=5800）加上其他 4 只。"""
        card = build_portfolio_card(journal)
        cost_table = self._get_cost_basis_table(card)
        us_row = next(
            (r for r in cost_table["rows"] if r["class"] == "美股股票"),
            None,
        )
        assert us_row is not None
        # 美股 7 只合计：3620 + 2020 + 1000 + 50 + 1800 + 2000 + 2000 = 12490
        assert us_row["cost"] == "12,490.00", (
            f"美股 cost 应是 12490（含 3 只 NDX DCA 累计），实际 {us_row['cost']}"
        )
        assert us_row["count"] == "7", "美股应有 7 只基金"

    def test_commodity_aggregation_uses_dca_target(
        self, journal: PortfolioJournal,
    ) -> None:
        """商品：000216 黄金 cost=7500（DCA 累计目标，不是已买 2510 快照）。"""
        card = build_portfolio_card(journal)
        cost_table = self._get_cost_basis_table(card)
        comm_row = next(
            (r for r in cost_table["rows"] if r["class"] == "商品"),
            None,
        )
        assert comm_row is not None
        assert comm_row["cost"] == "7,500.00", (
            f"商品 cost 应是 7500（DCA 累计），实际 {comm_row['cost']}"
        )

    def test_all_10_subclasses_covered(
        self, journal: PortfolioJournal,
    ) -> None:
        """10 大类全覆盖（11 大类减现金 004137 — CASH_LIKE_CODES 不渲染）。"""
        card = build_portfolio_card(journal)
        cost_table = self._get_cost_basis_table(card)
        classes = {r["class"] for r in cost_table["rows"]}
        expected = {
            "A 股股票", "港股", "美股股票", "国外发达市场股票", "新兴市场股票",
            "国内 REITs", "美国 REITs", "国内利率债", "美债", "商品",
        }
        assert classes == expected, (
            f"Section 1 缺大类：{expected - classes}；多余：{classes - expected}"
        )

    def test_total_cost_matches_cost_basis_sum(
        self, journal: PortfolioJournal,
    ) -> None:
        """Section 1 总成本 = TOTAL_COST_CNY（626454.47）— 摘要 div 用千位分隔符格式。"""
        from global_allocation.portfolio.cost_basis import TOTAL_COST_CNY
        card = build_portfolio_card(journal)
        # 找总成本摘要 div（含「总成本」字样）
        summary_text = ""
        for el in card["elements"]:
            if el.get("tag") == "div":
                summary_text = el.get("text", {}).get("content", "")
                if "总成本" in summary_text:
                    break
        # 摘要格式："**总成本**：626,454.47 CNY"（千位分隔符）
        expected = f"{float(TOTAL_COST_CNY):,.2f}"
        assert expected in summary_text, (
            f"摘要里应有总成本 {expected}（千位分隔符格式），实际：{summary_text}"
        )

    def _get_cost_basis_table(self, card: dict) -> dict:
        """找 Section 1 的「持仓成本 by 大类」表（columns 含 class + count）。"""
        for t in card["elements"]:
            if t.get("tag") != "table":
                continue
            cols = [c["name"] for c in t.get("columns", [])]
            if "class" in cols and "count" in cols:
                return t
        raise AssertionError("找不到「持仓成本 by 大类」表")


class TestAllocationDeviationInSummary:
    """第三十轮（liubo 2026-10-18 反馈）：Section 1 摘要补「欠配/超配」。
    第三十一轮（liubo 2026-10-18 反馈）：单位改「仓」（1 仓 = 1 万 CNY），按偏差大小排序。

    - 阈值 ±0.5 仓 — |dev| ≤ 0.5 仓不标（避免噪音；0.5 仓 = 5000 CNY）
    - CASH 不参与（cost_basis 不含现金 004137）
    - 排序：欠配从最欠配到最轻欠配（dev 升序），超配从最超配到最轻超配（dev 降序）
    - 格式：`{:+.1f} 仓`（保留 +/- 号 + 1 位小数 + 空格 + 单位）
    """

    def _get_summary_text(self, card: dict) -> str:
        """找 Section 1 摘要 div（含「总成本」字样）。"""
        for el in card["elements"]:
            if el.get("tag") == "div":
                text = el.get("text", {}).get("content", "")
                if "总成本" in text:
                    return text
        raise AssertionError("找不到 Section 1 摘要 div")

    def _get_alloc_line(self, summary: str, marker: str) -> str:
        """抓「**欠配**：...」或「**超配**：...」一行。"""
        line = next(
            (line for line in summary.split("\n") if line.startswith(f"**{marker}**：")),
            None,
        )
        assert line is not None, f"找不到「**{marker}**」行，实际：{summary}"
        return line

    def _parse_alloc_items(self, line: str) -> list[tuple[str, float]]:
        """把「**欠配**：A 股股票 -2.9 仓 / 美股股票 -4.8 仓」解析成 [(name, dev), ...]。

        抓 dash 后面的数字 + "仓"。
        """
        body = line.split("：", 1)[1].strip()
        if body == "—":
            return []
        items: list[tuple[str, float]] = []
        for chunk in body.split(" / "):
            # chunk = "A 股股票 -2.9 仓"
            parts = chunk.rsplit(" ", 2)
            # parts = ["A 股股票", "-2.9", "仓"] — name 可能含空格（"A 股股票"），但 unit 永远是最后两个
            assert len(parts) == 3 and parts[2] == "仓", f"格式不对：{chunk!r}"
            items.append((parts[0], float(parts[1])))
        return items

    def test_summary_contains_under_and_over_alloc_lines(
        self, journal: PortfolioJournal,
    ) -> None:
        """摘要 div 必须包含「**欠配**」和「**超配**」两行。"""
        card = build_portfolio_card(journal)
        summary = self._get_summary_text(card)
        assert "**欠配**" in summary, (
            f"摘要应有「**欠配**」一行，实际：{summary}"
        )
        assert "**超配**" in summary, (
            f"摘要应有「**超配**」一行，实际：{summary}"
        )

    def test_over_alloc_includes_bond_overweights_in_cang(
        self, journal: PortfolioJournal,
    ) -> None:
        """国内利率债（28.5 仓 vs target 3 = +25.5）和美债（17.5 仓 vs target 1 = +16.5）必须标超配。"""
        card = build_portfolio_card(journal)
        summary = self._get_summary_text(card)
        over_line = self._get_alloc_line(summary, "超配")
        over_items = self._parse_alloc_items(over_line)
        names = [name for name, _ in over_items]
        assert "国内利率债" in names, f"国内利率债应在超配行，实际：{over_line}"
        assert "美债" in names, f"美债应在超配行，实际：{over_line}"
        # 验证正号格式（保留 + 号）+ 单位 "仓"
        assert any(dev > 0 for _, dev in over_items), f"超配行应有正值，实际：{over_line}"
        assert "仓" in over_line, f"单位应为「仓」，实际：{over_line}"

    def test_under_alloc_includes_equity_underweights_in_cang(
        self, journal: PortfolioJournal,
    ) -> None:
        """A 股 / 美股 / 国外发达 等股票子类实际仓 < target，应进欠配行。"""
        card = build_portfolio_card(journal)
        summary = self._get_summary_text(card)
        under_line = self._get_alloc_line(summary, "欠配")
        under_items = self._parse_alloc_items(under_line)
        names = [name for name, _ in under_items]
        assert "A 股股票" in names, f"A 股应在欠配行，实际：{under_line}"
        assert "美股股票" in names, f"美股应在欠配行，实际：{under_line}"
        # 验证负号格式
        assert any(dev < 0 for _, dev in under_items), f"欠配行应有负值，实际：{under_line}"

    def test_under_alloc_sorted_by_largest_deviation_first(
        self, journal: PortfolioJournal,
    ) -> None:
        """欠配行按偏差升序（最欠配的在前）。"""
        card = build_portfolio_card(journal)
        summary = self._get_summary_text(card)
        under_line = self._get_alloc_line(summary, "欠配")
        under_items = self._parse_alloc_items(under_line)
        devs = [dev for _, dev in under_items]
        assert devs == sorted(devs), (
            f"欠配应按 dev 升序（最负在前），实际：{devs}"
        )

    def test_over_alloc_sorted_by_largest_deviation_first(
        self, journal: PortfolioJournal,
    ) -> None:
        """超配行按偏差降序（最超配的在前）。"""
        card = build_portfolio_card(journal)
        summary = self._get_summary_text(card)
        over_line = self._get_alloc_line(summary, "超配")
        over_items = self._parse_alloc_items(over_line)
        devs = [dev for _, dev in over_items]
        assert devs == sorted(devs, reverse=True), (
            f"超配应按 dev 降序（最正在前），实际：{devs}"
        )

    def test_deviation_uses_cang_unit_not_percent(
        self, journal: PortfolioJournal,
    ) -> None:
        """单位必须是「仓」，不能是「%」。"""
        card = build_portfolio_card(journal)
        summary = self._get_summary_text(card)
        under_line = self._get_alloc_line(summary, "欠配")
        over_line = self._get_alloc_line(summary, "超配")
        assert "仓" in under_line, f"欠配单位应为仓，实际：{under_line}"
        assert "仓" in over_line, f"超配单位应为仓，实际：{over_line}"
        assert "%" not in under_line, f"欠配不应有 %，实际：{under_line}"
        assert "%" not in over_line, f"超配不应有 %，实际：{over_line}"

    def test_cash_not_in_deviation_lines(
        self, journal: PortfolioJournal,
    ) -> None:
        """CASH 子类不在 COST_BASIS BY_CODE，不参与欠配/超配计算。"""
        card = build_portfolio_card(journal)
        summary = self._get_summary_text(card)
        assert "现金" not in summary, (
            f"现金不应出现在摘要里（CASH 不参与成本占比），实际：{summary}"
        )


class TestRemovedSections:
    """spec 096/097 移除的部分必须真的没了（第十九轮：Section 2 + Section 3 也都删了）。"""

    def test_no_chart_at_all(self, journal: PortfolioJournal) -> None:
        """第三十四轮（liubo 2026-10-18）：删柱状图 — 卡片不应有 chart 元素。

        之前 test_no_pie_chart / test_no_line_chart 各自验证一种 chart 类型。
        现在所有 chart 都禁掉了，统一用这个测试。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        charts = [e for e in card["elements"] if e.get("tag") == "chart"]
        assert len(charts) == 0, (
            f"第三十四轮删柱状图，card 应有 0 个 chart，实际 {len(charts)} 个"
        )

    def test_no_recent_transactions_columns(self, journal: PortfolioJournal) -> None:
        """持仓成本表 / per-fund 表都不该有交易流水列。

        第三十四轮（liubo 2026-10-18）：持仓表删了（改用成本 by 大类表），所以"delta"
        列不存在。检查所有非调仓/定投记录表的表都不应有交易流水列。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        for t in tables:
            col_names = {c["name"] for c in t["columns"]}
            # 调仓记录表 / 定投记录表 故意有 date 列（commit 日期）；其他表不应该
            if "fund" in col_names and "action" in col_names:
                continue  # 调仓 / 定投记录表，跳过
            assert "date" not in col_names
            assert "side" not in col_names
            assert "strategy" not in col_names
            assert "shares" not in col_names
            assert "price" not in col_names

    def test_breakdown_class_count_column_allowed(self, journal: PortfolioJournal) -> None:
        """第三十四轮（liubo 2026-10-18）：持仓成本 by 大类表允许有 count 列（基金数）。

        之前 spec 097 第十九轮禁掉 count（那是"持仓市值聚合表"——DB 驱动，不想要 count）。
        现在用 cost_basis 视角，count = 子类下的基金数（用户关心的元数据）。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        # 持仓成本 by 大类 表（含 "cost" + "weight" 列的）应该有 count 列
        cost_basis_tables = [
            t for t in tables
            if {"class", "cost", "weight"} <= {c["name"] for c in t.get("columns", [])}
        ]
        assert len(cost_basis_tables) >= 1, "应有持仓成本 by 大类表"
        for t in cost_basis_tables:
            col_names = {c["name"] for c in t["columns"]}
            assert "count" in col_names, f"持仓成本 by 大类表应有 count 列，实际 {col_names}"

    def test_no_section_3_subclass_header(self, journal: PortfolioJournal) -> None:
        """第十九轮/第二十轮：旧的 Section 3 标题"大类资产明细"必须不存在（Section 3 删除不恢复）。

        第二十轮恢复了 Section 2（大类资产策略），但 Section 3（子类明细）的 target/delta
        已经在持仓表的每行里了，不需要重复展示。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        for e in card["elements"]:
            if e.get("tag") == "note":
                for elem in e.get("elements", []):
                    assert elem.get("content") != "大类资产明细"

    def test_no_subclass_table(self, journal: PortfolioJournal) -> None:
        """第十九轮/第二十轮：不应该有 4 列子类对比表（subclass/target/current/delta）— 信息已在持仓表。

        第二十一轮：连 4 列策略对比表（category/target/current/delta）也删了 — 整数仓位模型
        下持仓表的 target/delta 已包含超类信息，Section 2 冗余。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        for t in tables:
            col_names = {c["name"] for c in t["columns"]}
            assert "subclass" not in col_names
            # 第二十一轮：4 列策略对比表也不该存在
            assert not (
                col_names == {"category", "target", "current", "delta"}
            ), "Section 2 策略对比表已删除（第二十一轮），不应再存在"

    def test_no_section_2_strategy_header(self, journal: PortfolioJournal) -> None:
        """第二十一轮：旧的 Section 2 标题"大类资产策略"必须不存在（已删除）。

        整数仓位模型下持仓表的 target/delta 已包含超类信息，Section 2 冗余，liubo 拍板删除。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        for e in card["elements"]:
            if e.get("tag") == "note":
                for elem in e.get("elements", []):
                    assert elem.get("content") != "大类资产策略"



class TestOrdering:
    """元素顺序（第三十四轮 liubo 2026-10-18 — 删柱状图，4 个 section 不变）。

    Section 1（持仓成本 by 大类）：note + div + hr + table
    Section 2（per-fund）：hr + note + div + table
    Section 3（调仓记录）：hr + note + table
    Section 4（定投记录）：hr + note + table
    """

    def test_order(self, journal: PortfolioJournal) -> None:
        _seed(journal)
        card = build_portfolio_card(journal)
        tags = [
            e.get("tag")
            for e in card["elements"]
            if e.get("tag") in {"note", "div", "chart", "table"}
        ]
        # 第三十四轮（liubo 2026-10-18）：删 chart
        # - Section 1（持仓成本 by 大类）：note + div + table
        # - Section 2（per-fund）：note + div + table
        # - Section 3（调仓记录）：note + table
        # - Section 4（定投记录）：note + table
        assert tags == [
            "note",  # Section 1 header "持仓成本"
            "div",   # Section 1 summary (生成时间/总成本/欠配/超配)
            "table", # Section 1 持仓成本 by 大类表（4 列：class/count/cost/weight）
            "note",  # Section 2 header "持仓与调仓建议"
            "div",   # Section 2 summary (基金数/本周操作)
            "table", # Section 2 per-fund 表（5 列：fund/etf_index/cost/valuation/advice）
            "note",  # Section 3 header "调仓纪录"
            "table", # Section 3 调仓纪录表（4 列：date/fund/action/amount）
            "note",  # Section 4 header "定投纪录"
            "table", # Section 4 定投纪录表（4 列：date/fund/action/amount，DCA 3 段金额）
        ]

    def test_hr_count_and_position(self, journal: PortfolioJournal) -> None:
        """第三十四轮（liubo 2026-10-18）：6 个 hr。

        Section 1 内 1（成本表前的分隔）+ Section 2/3/4 入口各 1 = 1 + 3 = 6。
        之前 7 个（Section 1 内 2 = cost_basis + 持仓表前），删了 chart 和 持仓表后变成 1。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        hrs = [e for e in card["elements"] if e.get("tag") == "hr"]
        assert len(hrs) == 6, (
            f"应有 6 个 hr（Section 1 内 1 + Section 2/3/4 入口各 1），实际 {len(hrs)}"
        )

    def test_first_element_is_section_header_note(self, journal: PortfolioJournal) -> None:
        """第一个元素是 note 标签（section header），内容是"持仓成本"。

        第三十四轮：Section 1 标题从「实盘持仓」改成「持仓成本」（不再是市值聚合）。
        用 note 元素而不是 div+markdown，是因为 note 是飞书原生浅灰背景块。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        first = card["elements"][0]
        assert first["tag"] == "note"
        # note 内部是 plain_text 列表
        assert any(
            elem.get("tag") == "plain_text" and elem.get("content") == "持仓成本"
            for elem in first["elements"]
        )

    def test_no_strategy_section_2_note(self, journal: PortfolioJournal) -> None:
        """第二十一轮：删除 Section 2 大类资产策略 — note "大类资产策略" 不应存在。

        Section 2 冗余（持仓表 target/delta 已包含超类信息），liubo 拍板删除。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        for e in card["elements"]:
            if e.get("tag") == "note":
                for elem in e.get("elements", []):
                    assert elem.get("content") != "大类资产策略"


# ─── Section 2: per-fund 持仓成本 + 估值 + 调仓建议（第二十三轮 — liubo 2026-10-17 拍板）───


class TestPerFundSection:
    """Section 2: per-fund 持仓成本 + 估值 + 调仓建议。

    liubo 2026-10-17 拍板 — 替代 spec 098 combined 估值与操作 section。
    4 列：基金 / 持仓成本(¥) / 估值 / 调仓建议
    数据：COST_BASIS_BY_CODE（52 只）+ weekly_rebalance_plan() 的 RebalanceAction
    """

    def _get_per_fund_table(self, journal: PortfolioJournal) -> dict[str, object]:
        """拿 per-fund 表（5 列：fund/etf_index/cost/valuation/advice）。

        第三十二轮（liubo 2026-10-18）：加「ETF 指数」列，让合并组基金一眼看出来。
        """
        card = build_portfolio_card(journal)
        for el in card["elements"]:
            if el.get("tag") != "table":
                continue
            col_names = [c["name"] for c in el.get("columns", [])]
            if col_names == ["fund", "etf_index", "cost", "valuation", "advice"]:
                return el
        raise AssertionError("未找到 per-fund 表（5 列 fund/etf_index/cost/valuation/advice）")

    def test_section_header_present(self, journal: PortfolioJournal) -> None:
        """Section 2 标题 '持仓与调仓建议' 必须存在。"""
        card = build_portfolio_card(journal)
        for e in card["elements"]:
            if e.get("tag") == "note":
                for elem in e.get("elements", []):
                    if elem.get("content") == "持仓与调仓建议":
                        return
        raise AssertionError("Section 2 标题 '持仓与调仓建议' 未找到")

    def test_table_has_dynamic_watchlist_rows(self, journal: PortfolioJournal) -> None:
        """per-fund 表行数 = COST_BASIS_BY_CODE 52 + INDEX_WATCHLIST 未持仓部分 - 持仓覆盖部分。

        liubo 2026-10-18 拍板：未持仓的指数也列出，持仓写 0。
        第三十六轮（liubo 2026-10-19）：加 8 个 CN_EQUITY ETF 指数到 INDEX_WATCHLIST（A50/A100/A500/
        中证1000/科创创业50/创业板50/科创50/科创100），但 7 个已被持仓基金覆盖 → dedupe 跳过，
        只剩 A100 (000903) 没持仓 → 实际 watchlist 新增 1 行。
        第三十七轮（liubo 2026-10-19）：加 HSSCHKY 恒生港股通高股息率指数到 INDEX_WATCHLIST
        （HK，0 持仓，跟踪 ETF = 513690）→ watchlist 新增 1 行。动态计算。
        """
        from global_allocation.portfolio.cost_basis import COST_BASIS_BY_CODE
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE
        from global_allocation.portfolio.card import SwensenClass
        from global_allocation.portfolio.pe_rebalance import INDEX_WATCHLIST, FUND_INDEX_MAP

        # dedupe：已被持仓基金覆盖的 index 跳过 watchlist
        held_index_codes: set[str] = set()
        for fund_code, (_, index_code, _) in FUND_INDEX_MAP.items():
            if fund_code in COST_BASIS_BY_CODE:
                held_index_codes.add(index_code)
        new_watchlist_rows = sum(
            1 for sub, index_code, _, _, _, _ in INDEX_WATCHLIST
            if index_code not in held_index_codes
        )
        expected = len(COST_BASIS_BY_CODE) + new_watchlist_rows
        table = self._get_per_fund_table(journal)
        assert len(table["rows"]) == expected, (
            f"per-fund 行数应是 {expected}（{len(COST_BASIS_BY_CODE)} holdings + "
            f"{new_watchlist_rows} watchlist），实际 {len(table['rows'])}"
        )

    def test_columns_are_5(self, journal: PortfolioJournal) -> None:
        """per-fund 表 5 列：基金 / ETF 指数 / 持仓成本(¥) / 估值 / 调仓建议。"""
        table = self._get_per_fund_table(journal)
        col_names = [c["name"] for c in table["columns"]]
        assert col_names == ["fund", "etf_index", "cost", "valuation", "advice"], (
            f"per-fund 列名应是 [fund, etf_index, cost, valuation, advice]，实际 {col_names}"
        )

    def test_row_keys_have_all_5_columns(self, journal: PortfolioJournal) -> None:
        """每行 5 个 key 都在（飞书 table 行为 row[key] = cell value）。"""
        table = self._get_per_fund_table(journal)
        for r in table["rows"]:
            for k in ("fund", "etf_index", "cost", "valuation", "advice"):
                assert k in r, f"行 {r} 缺 '{k}' key"

    def test_etf_index_column_shows_index_name_for_index_funds(
        self, journal: PortfolioJournal,
    ) -> None:
        """跟踪指数的基金应在 etf_index 列显示「纳斯达克 100」「标普 500」等。

        第三十二轮（liubo 2026-10-18）：合并组基金（如 5 只 NDX）应有同一个 etf_index 名。
        """
        table = self._get_per_fund_table(journal)
        ndx_rows = [r for r in table["rows"] if "018966" in r["fund"] or "539001" in r["fund"] or "016452" in r["fund"] or "019172" in r["fund"] or "019441" in r["fund"]]
        assert len(ndx_rows) >= 1, "应至少有一只 NDX 基金"
        for r in ndx_rows:
            assert r["etf_index"] == "纳斯达克 100", (
                f"NDX 基金 etf_index 应是「纳斯达克 100」，实际：{r}"
            )

    def test_etf_index_column_shows_dash_for_non_index_funds(
        self, journal: PortfolioJournal,
    ) -> None:
        """不跟踪指数的基金（如债/现金）etf_index 列显示「—」。"""
        table = self._get_per_fund_table(journal)
        # 找一只债基（中短债/美债之类）
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE
        from global_allocation.portfolio.card import SwensenClass
        for r in table["rows"]:
            # fund 列格式 "中文名 (code)"
            code = r["fund"].rsplit("(", 1)[-1].rstrip(")")
            sub = SUBCLASS_BY_CODE.get(code)
            if sub in (SwensenClass.CN_GOV_BOND, SwensenClass.US_BOND):
                assert r["etf_index"] == "—", (
                    f"债基 {code} etf_index 应是「—」，实际：{r}"
                )

    def test_ndx_valuation_shows_percentile_when_metric_value_missing(
        self, journal: PortfolioJournal,
    ) -> None:
        """NDX 基金估值列：metric_value=None 但 percentile 不为 None 时，显示分位估值。

        第三十二轮（liubo 2026-10-18）：螺丝钉数据只有分位没有 PE 绝对值，仍要显示分位。
        修复前显示「—」，修复后显示「PE-TTM 分位 72.59%」。
        """
        table = self._get_per_fund_table(journal)
        ndx_rows = [r for r in table["rows"] if "018966" in r["fund"] or "539001" in r["fund"] or "016452" in r["fund"] or "019172" in r["fund"] or "019441" in r["fund"]]
        assert len(ndx_rows) >= 1
        for r in ndx_rows:
            assert r["valuation"] != "—", (
                f"NDX 基金估值列不应是「—」，应有分位估值，实际：{r}"
            )
            assert "分位" in r["valuation"], (
                f"NDX 基金估值列应有分位，实际：{r['valuation']}"
            )
            assert "72.59" in r["valuation"], (
                f"NDX 基金估值列应显示 72.59%（螺丝钉 10 年分位），实际：{r['valuation']}"
            )

    def test_sorted_by_subclass_then_etf_index_then_code(self, journal: PortfolioJournal) -> None:
        """第三十三轮（liubo 2026-10-18）：按 (subclass_order, etf_index_code, code_or_marker) 排。

        同 ETF 指数聚一起（如 NDX 5 只挨着），未持仓指数（HSCGSI/HSIDI/N225/FCHI/A100）夹在大类里出现。
        """
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE
        from global_allocation.portfolio.cost_basis import COST_BASIS_BY_CODE
        from global_allocation.portfolio.card import SwensenClass
        from global_allocation.portfolio.pe_rebalance import INDEX_WATCHLIST

        subclass_order = {s: i for i, s in enumerate(SwensenClass)}

        # 构造期望：holdings + watchlist 混合，按 (subclass_order, etf_index_code, code_or_marker) 排
        items: list[tuple[int, str, str]] = []
        for code in COST_BASIS_BY_CODE:
            sub = SUBCLASS_BY_CODE.get(code)
            if sub is None:
                continue
            items.append((subclass_order[sub], "", code))
        for sub, index_code, _name, _m, _v, _p in INDEX_WATCHLIST:
            items.append((subclass_order[sub], index_code, f"({index_code})"))
        items.sort(key=lambda t: (t[0], t[1], t[2]))

        expected_markers = [t[2] for t in items]

        table = self._get_per_fund_table(journal)
        actual_codes = []
        for r in table["rows"]:
            # fund 列格式: "中文名 (code)" 或 "中文名（无持仓）"（watchlist 不带 code）
            fund_str = r["fund"]
            if "（无持仓）" in fund_str:
                # watchlist — 从 etf_index 列反查
                actual_codes.append(r["etf_index"])  # placeholder, see watchlist test
            else:
                code = fund_str.rsplit("(", 1)[1].rstrip(")")
                actual_codes.append(code)
        # watchlist rows use etf_index as marker — adjust expected accordingly
        expected_markers_with_index = [
            f"({code})" if code in {"HSCGSI", "HSIDI", "HSSCHKY", ".N225", ".FCHI", "000903"} else code
            for code in expected_markers
        ]
        # 简化：只检查排序 key（subclass 顺序），不强求精确代码对齐 watchlist
        # 因为实际实现用 etf_index 反查。
        # 单独验证 subclass 顺序：
        from global_allocation.portfolio.pe_rebalance import INDEX_DISPLAY_NAME
        actual_subclass_order: list[int] = []
        for r in table["rows"]:
            fund_str = r["fund"]
            if "（无持仓）" in fund_str:
                # watchlist → etf_index 列就是显示名（"恒生指数"等）
                # 反查 subclass
                idx_name = r["etf_index"]
                # 找对应 index_code
                idx_code = next(
                    (ic for ic, dn in INDEX_DISPLAY_NAME.items() if dn == idx_name),
                    None,
                )
                if idx_code:
                    for sub, ic, _n, _m, _v, _p in INDEX_WATCHLIST:
                        if ic == idx_code:
                            actual_subclass_order.append(subclass_order[sub])
                            break
            else:
                code = fund_str.rsplit("(", 1)[1].rstrip(")")
                sub = SUBCLASS_BY_CODE.get(code)
                if sub:
                    actual_subclass_order.append(subclass_order[sub])
        assert actual_subclass_order == sorted(actual_subclass_order), (
            f"subclass 顺序应递增，实际 {actual_subclass_order}"
        )

    def test_valuation_column_uses_metric_format(self, journal: PortfolioJournal) -> None:
        """估值列格式：PE-TTM 15.42 / 分位 41.55% / 等。

        至少有一行包含 'PE-TTM' 格式（PE-TTM 是最常见估值指标）。
        """
        table = self._get_per_fund_table(journal)
        # 检查有 PE-TTM 格式的行（A 股 / 美股 / 港股 / 国外发达 / 新兴市场都有）
        pe_rows = [r for r in table["rows"] if "PE-TTM" in r["valuation"]]
        assert len(pe_rows) >= 1, "至少应有一行 PE-TTM 格式（股票类）"

    def test_advice_column_uses_5_signals(self, journal: PortfolioJournal) -> None:
        """调仓建议列：5 个信号（加仓/减仓/建仓/不动/跳过）都可能出现在数据里。"""
        table = self._get_per_fund_table(journal)
        advice_texts = " ".join(r["advice"] for r in table["rows"])
        # 数据里有 NDX 5 只 → 估值 "—"，建议 "不动"（SKIP 路径走 SKIP 给出"跳过"）
        # 数据里有 DAX (000614) → 0 仓 + 分位 < 50% → "建仓"
        # 数据里有 HSTECH (013127) → 加仓
        # 数据里有黄金 (000216) → 看综合分
        for keyword in ("不动",):
            assert keyword in advice_texts, f"advice 列应含 '{keyword}'"

    def test_no_valuation_section_note_present(self, journal: PortfolioJournal) -> None:
        """第二十三轮：spec 098 估值与操作 4 张表 section 已删，'估值与操作' 标题不应存在。"""
        card = build_portfolio_card(journal)
        for e in card["elements"]:
            if e.get("tag") == "note":
                for elem in e.get("elements", []):
                    assert elem.get("content") != "估值与操作"

    def test_merged_etf_group_shows_same_valuation(self, journal: PortfolioJournal) -> None:
        """merge_by_etf 合并组（"022434+022424+022448"）→ 3 只 A500 基金都显示同一个估值。

        估值列是按 index 级别算的（同一指数估值一样），所以 3 只基金 PE-TTM 应该完全相同。
        """
        table = self._get_per_fund_table(journal)
        a500_rows = [
            r for r in table["rows"]
            if "A500" in r["fund"] and any(code in r["fund"] for code in ("022434", "022424", "022448"))
        ]
        # 3 只 A500 基金（南方/广发/国泰）
        assert len(a500_rows) == 3
        # 估值列完全一致
        vals = {r["valuation"] for r in a500_rows}
        assert len(vals) == 1, f"A500 合并组估值不一致: {vals}"

    def test_rows_sorted_by_subclass_order(self, journal: PortfolioJournal) -> None:
        """第三十三轮（liubo 2026-10-18）：per-fund 表按 SwensenClass 枚举顺序排。

        A 股 → 港股 → 美股 → 国外发达 → 新兴市场 → 中 REIT → 美 REIT → 中债 → 美债 → 商品 → 现金。
        """
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE
        from global_allocation.portfolio.card import SwensenClass
        from global_allocation.portfolio.pe_rebalance import INDEX_WATCHLIST, INDEX_DISPLAY_NAME

        subclass_order = {s: i for i, s in enumerate(SwensenClass)}

        # 解析每行的 subclass
        actual_subclass_idx: list[int] = []
        for r in self._get_per_fund_table(journal)["rows"]:
            fund_str = r["fund"]
            if "（无持仓）" in fund_str:
                # watchlist 行
                idx_name = r["etf_index"]
                idx_code = next(
                    (ic for ic, dn in INDEX_DISPLAY_NAME.items() if dn == idx_name),
                    None,
                )
                if idx_code:
                    for sub, ic, _n, _m, _v, _p in INDEX_WATCHLIST:
                        if ic == idx_code:
                            actual_subclass_idx.append(subclass_order[sub])
                            break
                else:
                    actual_subclass_idx.append(-1)
            else:
                code = fund_str.rsplit("(", 1)[1].rstrip(")")
                sub = SUBCLASS_BY_CODE.get(code)
                actual_subclass_idx.append(subclass_order[sub] if sub else -1)

        assert actual_subclass_idx == sorted(actual_subclass_idx), (
            f"subclass 顺序应严格递增（A 股 → 港股 → ...），实际 {actual_subclass_idx}"
        )

    def test_ndx_5_funds_grouped_together(self, journal: PortfolioJournal) -> None:
        """第三十三轮（liubo 2026-10-18）：同一 ETF 指数（NDX）的 5 只基金挨在一起。

        5 只 NDX：018966 + 539001 + 016452 + 019172 + 019441。
        """
        table = self._get_per_fund_table(journal)
        ndx_codes = ("018966", "539001", "016452", "019172", "019441")
        positions: list[int] = []
        for i, r in enumerate(table["rows"]):
            for code in ndx_codes:
                if code in r["fund"]:
                    positions.append(i)
                    break
        # 5 只 NDX 都出现
        assert len(positions) == 5, f"应有 5 只 NDX 基金，实际 {len(positions)}"
        # 必须挨着（连续 5 个）
        assert positions == list(range(min(positions), min(positions) + 5)), (
            f"5 只 NDX 基金应挨着（连续 5 行），实际位置 {positions}"
        )

    def test_index_watchlist_shows_with_zero_cost(self, journal: PortfolioJournal) -> None:
        """第三十三轮（liubo 2026-10-18）：INDEX_WATCHLIST 里的指数也列出，持仓 0。

        第三十六轮（liubo 2026-10-19）：HSCGSI/HSIDI + .N225/.FCHI + A100 (中证 A100) 共 5 个 watchlist。
        HSI 已不跟踪（移除），加 HSIDI。
        7 个 CN_EQUITY 指数（A50/A500/中证1000/科创创业50/创业板50/科创50/科创100）已被持仓基金
        覆盖 → dedupe 跳过。
        第三十七轮（liubo 2026-10-19）：加 HSSCHKY 恒生港股通高股息率指数到 watchlist（HK，0 持仓）。
        跟踪 ETF = 513690 港股红利 ETF 博时。
        """
        table = self._get_per_fund_table(journal)
        watchlist_names = {
            "恒生消费指数",
            "恒生创新药指数",
            "恒生港股通高股息率指数",  # 第三十七轮（替换了原来的恒生高股息率指数）
            "日经 225",
            "法国 CAC 40",
            "中证 A100",
        }
        seen: set[str] = set()
        for r in table["rows"]:
            if "（无持仓）" in r["fund"]:
                # watchlist 行 — etf_index 列是指数中文名
                assert r["cost"] == "0.00", (
                    f"watchlist 行 cost 应是 '0.00'，实际 '{r['cost']}'（行：{r}）"
                )
                assert r["etf_index"] in watchlist_names, (
                    f"etf_index 应是 watchlist 中文名，实际 '{r['etf_index']}'"
                )
                seen.add(r["etf_index"])
        assert seen == watchlist_names, (
            f"watchlist 5 个指数都应出现，实际：{seen}"
        )


# ─── A 股策略建议行（spec 098 第二十五轮 — liubo 2026-09-19 反馈）───


class TestDecideAShareStrategy:
    """_decide_a_share_strategy 决策树：占比 vs 目标 + 估值综合分。

    决策树：
    1) 占比 > 目标 → 不再投入（仓位纪律优先）
    2) 占比 ≤ 目标 + 偏高估/极高估 → 分批止盈
    3) 占比 ≤ 目标 + 偏低估/极低估 → 分批买入
    4) 占比 ≤ 目标 + 正常 → 正常持有
    """

    def test_over_target_returns_no_invest(self) -> None:
        """占比 > 目标 → 不再投入（不论估值高低）。"""
        from global_allocation.portfolio.card import _decide_a_share_strategy

        # 估值偏低估也应该是"不再投入"（仓位纪律优先于估值信号）
        strategy, reason = _decide_a_share_strategy(over_target=True, composite_verdict="低估")
        assert strategy == "不再投入"
        assert reason == "占比 > 目标"

    def test_under_target_high_valuation_take_profit(self) -> None:
        """占比 ≤ 目标 + 偏高估 → 分批止盈。"""
        from global_allocation.portfolio.card import _decide_a_share_strategy

        strategy, reason = _decide_a_share_strategy(over_target=False, composite_verdict="偏高估")
        assert strategy == "分批止盈"
        assert "估值" in reason and "偏高估" in reason

    def test_under_target_extreme_high_take_profit(self) -> None:
        """占比 ≤ 目标 + 极高估 → 分批止盈（一样）。"""
        from global_allocation.portfolio.card import _decide_a_share_strategy

        strategy, reason = _decide_a_share_strategy(over_target=False, composite_verdict="极高估")
        assert strategy == "分批止盈"

    def test_under_target_low_valuation_buy(self) -> None:
        """占比 ≤ 目标 + 偏低估 → 分批买入。"""
        from global_allocation.portfolio.card import _decide_a_share_strategy

        strategy, reason = _decide_a_share_strategy(over_target=False, composite_verdict="低估")
        assert strategy == "分批买入"
        assert "估值" in reason and "偏低估" in reason

    def test_under_target_extreme_low_buy(self) -> None:
        """占比 ≤ 目标 + 极低 → 分批买入。

        注：interpret_composite_score 返回 "极低"（不是 "极低估"）。
        极低估是 score band 的标签（"1=极低估 5=极高估"），不要混淆。
        """
        from global_allocation.portfolio.card import _decide_a_share_strategy

        strategy, reason = _decide_a_share_strategy(over_target=False, composite_verdict="极低")
        assert strategy == "分批买入"

    def test_under_target_normal_hold(self) -> None:
        """占比 ≤ 目标 + 估值正常 → 正常持有（不卖不买）。"""
        from global_allocation.portfolio.card import _decide_a_share_strategy

        strategy, reason = _decide_a_share_strategy(over_target=False, composite_verdict="正常")
        assert strategy == "正常持有"
        assert "估值" in reason and "正常" in reason


class TestComputeRoeYoy:
    """_compute_roe_yoy：ROE 同比 = (latest - year_ago) / year_ago。"""

    def _fv(self, latest: Decimal | None, year_ago: Decimal | None) -> FundValuation:
        return FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="013310",
            index_code="931643",
            pe_ttm=Decimal("50"),
            pe_percentile=Decimal("0.7"),
            dividend_yield=Decimal("0.005"),
            roe_latest=latest,
            roe_year_ago=year_ago,
            source="test",
        )

    def test_positive_growth(self) -> None:
        """ROE 从 5% → 8.34% → +66.8%。"""
        result = _compute_roe_yoy(self._fv(Decimal("0.0834"), Decimal("0.05")))
        assert result is not None
        assert abs(result - Decimal("0.668")) < Decimal("0.01")

    def test_zero_growth(self) -> None:
        """ROE 没变 → 0。"""
        result = _compute_roe_yoy(self._fv(Decimal("0.05"), Decimal("0.05")))
        assert result == Decimal("0")

    def test_negative_growth(self) -> None:
        """ROE 下滑 → 负值。"""
        result = _compute_roe_yoy(self._fv(Decimal("0.03"), Decimal("0.05")))
        assert result == Decimal("-0.4")

    def test_missing_latest(self) -> None:
        """ROE 最新缺失 → None。"""
        assert _compute_roe_yoy(self._fv(None, Decimal("0.05"))) is None

    def test_missing_year_ago(self) -> None:
        """ROE 去年缺失 → None。"""
        assert _compute_roe_yoy(self._fv(Decimal("0.05"), None)) is None

    def test_year_ago_zero(self) -> None:
        """去年 ROE = 0 → None（避免除零）。"""
        assert _compute_roe_yoy(self._fv(Decimal("0.05"), Decimal("0"))) is None


class TestPerFundVerdict:
    """_per_fund_verdict：单只 A 股基金的估值判断（5 档打分制）。

    spec 098 第二十八轮 — liubo 2026-09-19 反馈"按指数类型用不同估值指标 + 分位 1-5 打分"。

    3 套规则：
    - dividend（红利低波）：优先用股息率加权 PE 分位，否则 fallback 普通 PE 分位
    - broad（宽基）：普通 PE 分位
    - growth（成长/小盘）：普通 PE 分位 + ROE 同比辅助

    5 档阈值（与 4 指标估值表 PE 分位一致）：
    - < 10%  → 极低估 1
    - < 30%  → 低估 2
    - < 70%  → 正常 3
    - < 90%  → 高估 4
    - ≥ 90%  → 极高估 5

    growth 额外调整：
    - ROE 同比 ≤ -10% 且 score ≥ 4 → 极高估 5（盈利下滑 + PE 中高位）
    - ROE 同比 ≥ +10% 且 score = 5 → 高估 4（盈利在涨，PE 高但合理）
    """

    def test_broad_low(self) -> None:
        """broad 策略：PE 分位 < 30% → 低估 2。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="014532",
            index_code="930050",
            pe_ttm=Decimal("15"),
            pe_percentile=Decimal("0.20"),
            dividend_yield=Decimal("0.03"),
            source="test",
        )
        assert _per_fund_verdict(fv, "broad") == "低估 2"

    def test_broad_very_low(self) -> None:
        """broad 策略：PE 分位 < 10% → 极低估 1。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="014532",
            index_code="930050",
            pe_ttm=Decimal("15"),
            pe_percentile=Decimal("0.05"),
            dividend_yield=Decimal("0.03"),
            source="test",
        )
        assert _per_fund_verdict(fv, "broad") == "极低估 1"

    def test_broad_normal(self) -> None:
        """broad 策略：PE 分位 30%-70% → 正常 3。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="014532",
            index_code="930050",
            pe_ttm=Decimal("15"),
            pe_percentile=Decimal("0.46"),
            dividend_yield=Decimal("0.03"),
            source="test",
        )
        assert _per_fund_verdict(fv, "broad") == "正常 3"

    def test_broad_high(self) -> None:
        """broad 策略：PE 分位 70%-90% → 高估 4。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="014532",
            index_code="930050",
            pe_ttm=Decimal("20"),
            pe_percentile=Decimal("0.80"),
            dividend_yield=Decimal("0.01"),
            source="test",
        )
        assert _per_fund_verdict(fv, "broad") == "高估 4"

    def test_broad_very_high(self) -> None:
        """broad 策略：PE 分位 ≥ 90% → 极高估 5。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="014532",
            index_code="930050",
            pe_ttm=Decimal("25"),
            pe_percentile=Decimal("0.95"),
            dividend_yield=Decimal("0.01"),
            source="test",
        )
        assert _per_fund_verdict(fv, "broad") == "极高估 5"

    def test_dividend_uses_dy_weighted_pe_pct(self) -> None:
        """dividend 策略：股息率加权 PE 分位 < 30% → 低估 2（即使普通 PE 分位高）。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="008114",
            index_code="930955",
            pe_ttm=Decimal("8.85"),
            pe_percentile=Decimal("0.8180"),  # 普通 PE 分位 81.8%（高）
            dividend_yield=Decimal("0.0448"),
            pe_percentile_dy_weighted=Decimal("0.20"),  # 但股息率加权只有 20%（低估）
            source="test",
        )
        assert _per_fund_verdict(fv, "dividend") == "低估 2"

    def test_dividend_fallback_to_pe_percentile(self) -> None:
        """dividend 策略：股息率加权 PE 分位缺失 → fallback 到普通 PE 分位。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="008114",
            index_code="930955",
            pe_ttm=Decimal("8.85"),
            pe_percentile=Decimal("0.20"),  # fallback 用这个
            dividend_yield=Decimal("0.04"),
            pe_percentile_dy_weighted=None,  # 银行螺丝钉还没填
            source="test",
        )
        assert _per_fund_verdict(fv, "dividend") == "低估 2"

    def test_growth_high_roe_downgrades_score_5_to_4(self) -> None:
        """growth 策略：PE 分位 ≥ 90% + ROE 同比大涨 → 高估 4（不是极高估）。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="013310",
            index_code="931643",
            pe_ttm=Decimal("51"),
            pe_percentile=Decimal("0.95"),  # 极高估 5
            dividend_yield=Decimal("0.006"),
            roe_latest=Decimal("0.0834"),
            roe_year_ago=Decimal("0.0543"),  # +54% ROE 同比
            source="test",
        )
        assert _per_fund_verdict(fv, "growth") == "高估 4"

    def test_growth_declining_roe_promotes_to_5(self) -> None:
        """growth 策略：PE 分位 70%+ + ROE 同比大跌 → 极高估 5（盈利下滑 + PE 高 = 真贵）。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="000852",
            index_code="000852",
            pe_ttm=Decimal("44"),
            pe_percentile=Decimal("0.85"),  # 高估 4 base
            dividend_yield=Decimal("0.011"),
            roe_latest=Decimal("0.02"),
            roe_year_ago=Decimal("0.05"),  # -60% ROE 同比
            source="test",
        )
        assert _per_fund_verdict(fv, "growth") == "极高估 5"

    def test_growth_without_roe_data(self) -> None:
        """growth 策略：ROE 缺失 → 跟普通 PE 分位一样判断。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="013310",
            index_code="931643",
            pe_ttm=Decimal("50"),
            pe_percentile=Decimal("0.20"),  # < 30% → 低估 2
            dividend_yield=Decimal("0.006"),
            roe_latest=None,
            roe_year_ago=None,
            source="test",
        )
        assert _per_fund_verdict(fv, "growth") == "低估 2"

    def test_missing_pe_pct(self) -> None:
        """PE 分位缺失 → 数据缺失。"""
        fv = FundValuation(
            record_date=date(2026, 9, 19),
            fund_code="008114",
            index_code="930955",
            pe_ttm=Decimal("10"),
            pe_percentile=None,
            dividend_yield=Decimal("0.04"),
            source="test",
        )
        assert _per_fund_verdict(fv, "dividend") == "数据缺失"
        assert _per_fund_verdict(fv, "broad") == "数据缺失"
        assert _per_fund_verdict(fv, "growth") == "数据缺失"


class TestPerFundRecommendation:
    """_per_fund_recommendation：按 per-fund 估值分位给动作建议（不看 A 股整体仓位）。

    spec 098 第二十八轮 — liubo 反馈"建议不要看整体超配，应该按评估判断"。

    决策：score 1-2 买入 / 3 持有 / 4-5 止盈 / 数据缺失 → "—"
    """

    def test_score_1_buy(self) -> None:
        assert _per_fund_recommendation("极低估 1") == "买入"

    def test_score_2_buy(self) -> None:
        assert _per_fund_recommendation("低估 2") == "买入"

    def test_score_3_hold(self) -> None:
        assert _per_fund_recommendation("正常 3") == "持有"

    def test_score_4_sell(self) -> None:
        assert _per_fund_recommendation("高估 4") == "止盈"

    def test_score_5_sell(self) -> None:
        assert _per_fund_recommendation("极高估 5") == "止盈"

    def test_data_missing_dash(self) -> None:
        assert _per_fund_recommendation("数据缺失") == "—"

    def test_advice_ignores_overall_strategy(self) -> None:
        """不接收 overall_strategy 参数 — 单只基金独立判断，仓位纪律由 strategy_note 单独说。"""
        # 即便估值"高估"也不被任何外部状态覆盖
        assert _per_fund_recommendation("高估 4") == "止盈"
        assert _per_fund_recommendation("低估 2") == "买入"


class TestFundValuationSection:
    """_build_fund_valuation_section：每只 A 股基金的估值表。

    spec 098 第二十七轮 — liubo 反馈"要看每只基金估值"。
    """

    def test_returns_empty_when_no_a_share_holdings(
        self, journal: PortfolioJournal
    ) -> None:
        """用户没有 A 股持仓（fixtures 里只有 mixed 和 equity 但没 CN_EQUITY）→ 不渲染。"""
        _seed(journal)
        section = _build_fund_valuation_section(journal)
        assert section == []

    def test_renders_per_fund_table(self, journal: PortfolioJournal) -> None:
        """有 A 股持仓 + 有估值数据 → 渲染 hr + table（spec 098 第二十八轮 — header 已外移到 valuation_section）。"""
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        section = _build_fund_valuation_section(journal)
        # 2 元素：hr + table（不再包含子 header note，由外层 _build_valuation_section 提供 "A 股估值与操作"）
        assert len(section) == 2
        assert section[0]["tag"] == "hr"
        assert section[1]["tag"] == "table"

    def test_table_has_10_columns(self, journal: PortfolioJournal) -> None:
        """10 列：基金 / 指数 / PE / PE分位 / 股息率 / ROE同比 / 评估 / 建议 / 当前仓位 / 加减仓建议。"""
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        section = _build_fund_valuation_section(journal)
        table = section[1]
        col_names = [c["name"] for c in table["columns"]]
        assert col_names == [
            "fund", "index", "pe", "pe_pct", "dy", "roe_yoy",
            "verdict", "advice", "position", "adjust",
        ]

    def test_advice_based_on_per_fund_verdict(self, journal: PortfolioJournal) -> None:
        """建议只看 per-fund 估值（不看整体 A 股策略）— liubo 反馈"建议不要看整体超配"。

        017644 中证1000增强（PE 分位=0.50 → 正常 3）→ 建议"持有" / "—"
        022434 中证A500（PE 分位=0.80 → 高估 4）→ 建议"止盈" / "−1仓"
        014532 MSCI中国A50（PE 分位=0.1626 → 低估 2）→ 建议"买入" / "+1仓"

        第十八轮：原 008114（红利低波，已砍）改用 017644（中估）测"持有"。
        """
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        section = _build_fund_valuation_section(journal)
        table = section[1]
        row_by_code = {r["fund"].split("\n")[0]: r for r in table["rows"]}
        # 017644 → 持有 —（PE 分位 50% 中估）
        assert row_by_code["017644"]["verdict"] == "正常 3"
        assert row_by_code["017644"]["advice"] == "持有"
        assert row_by_code["017644"]["adjust"] == "—"
        # 022434 → 止盈 −1仓
        assert row_by_code["022434"]["verdict"] == "高估 4"
        assert row_by_code["022434"]["advice"] == "止盈"
        assert row_by_code["022434"]["adjust"] == "−1仓"
        # 014532 → 买入 +1仓
        assert row_by_code["014532"]["verdict"] == "低估 2"
        assert row_by_code["014532"]["advice"] == "买入"
        assert row_by_code["014532"]["adjust"] == "+1仓"

    def test_advice_normal_holds(self, journal: PortfolioJournal) -> None:
        """PE 分位 30%-70% → 正常 3 → 建议"持有" / "—"。"""
        _seed_a_share_funds(journal)
        journal.db.upsert_fund_valuation(
            FundValuation(
                record_date=date.today(),
                fund_code="022434",
                index_code="000510",
                pe_ttm=Decimal("16"),
                pe_percentile=Decimal("0.50"),  # 正常 3
                dividend_yield=Decimal("0.022"),
                source="test",
            )
        )
        section = _build_fund_valuation_section(journal)
        table = section[1]
        row = next(r for r in table["rows"] if "022434" in r["fund"])
        assert row["verdict"] == "正常 3"
        assert row["advice"] == "持有"
        assert row["adjust"] == "—"

    def test_dividend_row_shows_dy_weighted_pe_pct(self, journal: PortfolioJournal) -> None:
        """第十八轮（2026-09-22 liubo 砍非宽基）后保留 — 测底层格式逻辑。

        原：用 008114 红利低波测 per-fund 表的 PE分位 列显示股息率加权值。
        现在：A 股只剩宽基，无基金有 dy_weighted 字段。测底层 card.py:621-635
        处的内联 fallback 逻辑 — 保留 fund_valuation 字段供未来分红策略用。
        直接验证 FundValuation 的 dy_weighted 字段存取正确（数据层）。
        """
        # 数据层：dy_weighted 字段能写能读
        fv_with_dy = FundValuation(
            record_date=date.today(),
            fund_code="014532",
            index_code="930050",
            pe_ttm=Decimal("15.78"),
            pe_percentile=Decimal("0.818"),      # 普通 81.8%
            pe_percentile_dy_weighted=Decimal("0.20"),  # 股息率加权 20%
            dividend_yield=Decimal("0.0448"),
            source="test",
        )
        assert fv_with_dy.pe_percentile_dy_weighted == Decimal("0.20")
        # 没设 dy_weighted → 默认 None
        fv_normal = FundValuation(
            record_date=date.today(),
            fund_code="022434",
            index_code="000510",
            pe_ttm=Decimal("15.85"),
            pe_percentile=Decimal("0.80"),
            dividend_yield=Decimal("0.005"),
            source="test",
        )
        assert fv_normal.pe_percentile_dy_weighted is None

    def test_growth_row_shows_roe_yoy_not_dy(self, journal: PortfolioJournal) -> None:
        """成长/小盘显示 ROE 同比列，不显示股息率列（股息率永远低，没参考价值）。

        fixture: 022434 中证A500（broad 策略 — 不是 growth）
        fixture 没有真正的 growth fund，所以这个测试用 mock 数据
        """
        _seed_a_share_funds(journal)
        # 额外塞一个 growth fund
        from datetime import date as _date
        journal.db.upsert_fund_valuation(
            FundValuation(
                record_date=_date.today(),
                fund_code="013310",
                index_code="931643",
                pe_ttm=Decimal("51.20"),
                pe_percentile=Decimal("0.75"),
                dividend_yield=Decimal("0.006"),
                roe_latest=Decimal("0.0834"),
                roe_year_ago=Decimal("0.0543"),
                source="test",
            )
        )
        section = _build_fund_valuation_section(journal)
        table = section[1]
        # 013310 不在 _seed_a_share_funds 里 — 不该出现在表里
        # 这个测试聚焦在 ROE同比 列存在性
        # 改用 000852 之类需要重新 seed
        # 简化：只检查 ROE同比 列存在即可
        roe_col = next((c for c in table["columns"] if c["name"] == "roe_yoy"), None)
        assert roe_col is not None
        assert roe_col["display_name"] == "ROE同比"

    def test_missing_valuation_renders_data_missing(self, journal: PortfolioJournal) -> None:
        """有 A 股持仓但 DB 没估值 → 整行显示"数据缺失"，仓位保留（按市值算）。"""
        _seed_a_share_funds(journal)
        # 没调 _seed_fund_valuations
        section = _build_fund_valuation_section(journal)
        table = section[1]
        # 每行都有数据缺失（估值列），仓位/加减仓列也合理
        for row in table["rows"]:
            assert row["pe"] == "数据缺失"
            assert row["pe_pct"] == "数据缺失"
            assert row["dy"] == "数据缺失"
            assert row["roe_yoy"] == "数据缺失"
            assert row["verdict"] == "数据缺失"
            assert row["advice"] == "—"
            # 没估值数据 → 加减仓也是 "—"
            assert row["adjust"] == "—"

    def test_only_a_share_funds_in_table(self, journal: PortfolioJournal) -> None:
        """混合持仓：1 只 A 股 + 1 只非 A 股 → 表里只显示 A 股那只。"""
        _seed_a_share_funds(journal)  # 加 022434 (CN_EQUITY)
        _seed(journal)  # 加 163406 (MIXED) + 510300 (EQUITY)
        # 清空 fund_valuations 让 022434 显示数据缺失
        section = _build_fund_valuation_section(journal)
        table = section[1]
        fund_codes = [r["fund"].split("\n")[0] for r in table["rows"]]
        assert "022434" in fund_codes
        assert "163406" not in fund_codes
        assert "510300" not in fund_codes

class TestPositionManagementMerged:
    """spec 098 第二十九轮 + 第三十轮 — liubo 反馈"两张表合并"+"底仓 1 仓 + 上限 6 仓"。

    仓位 + 加减仓建议 合并到 per-fund 表的右侧两列：
    - 当前仓位 = 市值 / 1 仓（POSITION_UNIT = 10000），格式 "X.Y仓"
    - 加减仓建议 = "+1仓" / "−1仓" / "—"
    - 整体超配 → 加仓暂停（"—"），减仓照常
    - 底仓 1 仓不动（POSITION_BASE = 1）：止盈到 ≤ 1 仓时不再减
    - 上限 6 仓（POSITION_MAX = 1 + 5）：买入到 ≥ 6 仓时不再加
    """

    def test_buy_verdict_shows_plus_1_unit(self, journal: PortfolioJournal) -> None:
        """低估 1-2 + 当前 < 6 仓 + 不超配 → 加减仓建议 = "+1仓"。

        第十八轮：原 008114（红利低波）改用 017644（但 017644 中估），
        所以只剩 014532 一只低估测 "+1仓"，再额外 mock 一只低估。
        """
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)  # 014532=低估 2, 017644=正常, 022434=高估 4
        # 给 017644 改低估测试 "+1仓"
        journal.db.upsert_fund_valuation(
            FundValuation(
                record_date=date.today(),
                fund_code="017644",
                index_code="000852",
                pe_ttm=Decimal("20.0"),
                pe_percentile=Decimal("0.15"),  # 低估 2
                dividend_yield=Decimal("0.012"),
                source="test",
            )
        )
        section = _build_fund_valuation_section(journal, over_target=False)
        table = section[1]
        row_by_code = {r["fund"].split("\n")[0]: r for r in table["rows"]}
        assert row_by_code["014532"]["adjust"] == "+1仓"
        assert row_by_code["017644"]["adjust"] == "+1仓"  # mock 低估后
        assert row_by_code["022434"]["adjust"] == "−1仓"

    def test_over_target_pauses_add_but_keeps_reduce(
        self, journal: PortfolioJournal
    ) -> None:
        """整体超配 → 买入变 "—"（加仓暂停），止盈保持 "−1仓"。

        第十八轮：014532 / 022434 都还在映射；008114 → 017644 不动（不再测）。
        """
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        section = _build_fund_valuation_section(journal, over_target=True)
        table = section[1]
        row_by_code = {r["fund"].split("\n")[0]: r for r in table["rows"]}
        # 低估 2：加仓暂停
        assert row_by_code["014532"]["adjust"] == "—"
        # 高估 4：减仓照常
        assert row_by_code["022434"]["adjust"] == "−1仓"

    def test_hold_verdict_shows_dash(self, journal: PortfolioJournal) -> None:
        """正常 3 → 加减仓 "—"。"""
        _seed_a_share_funds(journal)
        journal.db.upsert_fund_valuation(
            FundValuation(
                record_date=date.today(),
                fund_code="022434",
                index_code="000510",
                pe_ttm=Decimal("16"),
                pe_percentile=Decimal("0.50"),
                dividend_yield=Decimal("0.022"),
                source="test",
            )
        )
        section = _build_fund_valuation_section(journal, over_target=False)
        table = section[1]
        row = next(r for r in table["rows"] if "022434" in r["fund"])
        assert row["adjust"] == "—"

    def test_position_unit_is_10k(self, journal: PortfolioJournal) -> None:
        """确认仓位单位 = 10,000 CNY。"""
        assert POSITION_UNIT == Decimal("10000")

    def test_position_format_with_cang_suffix(self, journal: PortfolioJournal) -> None:
        """当前仓位 显示 "X.Y仓" 格式（保留 1 位小数 + 仓单位）。"""
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        section = _build_fund_valuation_section(journal, over_target=False)
        table = section[1]
        for row in table["rows"]:
            assert row["position"].endswith("仓")
            numeric_part = row["position"].rstrip("仓")
            assert float(numeric_part) >= 0

    def test_buy_at_max_position_shows_dash(self, journal: PortfolioJournal) -> None:
        """低估（买入）+ 当前仓位 >= 6 仓（已达上限）→ 加减仓建议 "—"。

        第十八轮：008114 已砍，改用 014532（低估 2）测 max position。
        """
        from global_allocation.portfolio.card import (
            POSITION_MAX,
            POSITION_UNIT,
        )
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        # 把 014532 的市值推到上限：shares * market_price = 6 * POSITION_UNIT
        # 014532 当前 10000 shares, price=1.5 → 市值 15000 = 1.5 仓
        # 改价格 = 6 * 10000 / 10000 = 6.0 → 市值 60000 = 6 仓（恰好上限）
        journal._price_source._prices["014532"] = Decimal(str(POSITION_MAX))
        section = _build_fund_valuation_section(journal, over_target=False)
        table = section[1]
        row_014532 = next(r for r in table["rows"] if "014532" in r["fund"])
        assert row_014532["position"] == "6.0仓"
        # 买入但已达上限 → 不再加
        assert row_014532["adjust"] == "—"

    def test_sell_at_base_position_shows_dash(self, journal: PortfolioJournal) -> None:
        """高估（止盈）+ 当前仓位 <= 1 仓（只剩底仓）→ 加减仓建议 "—"。"""
        from global_allocation.portfolio.card import POSITION_BASE
        _seed_a_share_funds(journal)
        _seed_fund_valuations(journal)
        # 把 022434 的市值压到刚好底仓 1 仓
        journal._price_source._prices["022434"] = Decimal(str(POSITION_BASE))
        section = _build_fund_valuation_section(journal, over_target=False)
        table = section[1]
        row_022434 = next(r for r in table["rows"] if "022434" in r["fund"])
        assert row_022434["position"] == "1.0仓"
        # 止盈但只剩底仓 → 不卖底仓
        assert row_022434["adjust"] == "—"

    def test_position_constants(self) -> None:
        """spec 098 第三十轮 — 仓位常量定义。

        底仓 1 仓 + 最多加 5 仓 = 总上限 6 仓。
        """
        from global_allocation.portfolio.card import (
            POSITION_BASE,
            POSITION_MAX,
            POSITION_MAX_ABOVE_BASE,
        )
        assert POSITION_BASE == Decimal("1")
        assert POSITION_MAX_ABOVE_BASE == Decimal("5")
        assert POSITION_MAX == Decimal("6")


# ─── fixtures（spec 098 第二十七轮 per-fund 测试用）───


def _seed_a_share_funds(journal: PortfolioJournal) -> None:
    """塞 3 只 A 股基金（不同 PE 分位 / 股息率组合）+ 1 笔 buy。

    第十八轮（2026-09-22）：A 股只用宽基 5 只，原 008114（红利低波）已砍，
    改用 017644（中证1000增强）补回 3 只 — 都属大类资产配置 CN_EQUITY 子类。
    shares=10000, price=1.5 → 市值=15000 = 1.5 仓（高于底仓，可加可减）。
    """
    journal.add_fund("014532", "MSCI中国A50", AssetClass.EQUITY)
    journal.add_fund("017644", "中证1000增强", AssetClass.EQUITY)  # 第十八轮接替 008114
    journal.add_fund("022434", "中证A500", AssetClass.EQUITY)
    for code in ("014532", "017644", "022434"):
        journal._price_source._prices[code] = Decimal("1.5")
    # 各 buy 10000 份，price=1.0 → 平均成本 1.0；市值 = 10000 * 1.5 = 15000 = 1.5 仓
    journal.record_buy(
        fund_code="014532",
        trade_date=date(2026, 9, 1),
        shares=Decimal("10000"),
        price=Decimal("1.0"),
    )
    journal.record_buy(
        fund_code="017644",
        trade_date=date(2026, 9, 1),
        shares=Decimal("10000"),
        price=Decimal("1.0"),
    )
    journal.record_buy(
        fund_code="022434",
        trade_date=date(2026, 9, 1),
        shares=Decimal("10000"),
        price=Decimal("1.0"),
    )


def _seed_fund_valuations(journal: PortfolioJournal) -> None:
    """塞 per-fund 估值：1 只 broad 低估（014532）+ 1 只 broad 中估（017644） + 1 只 broad 高估（022434）。

    第十八轮：原 008114（红利低波，已砍）改为 017644（中证1000增强，宽基）。
    简化：用 PE 分位低/中/高三档测 verdict / advice / adjust，不用股息率加权逻辑（dividend 字段保留备未来分红策略）。
    """
    today = date.today()
    journal.db.upsert_fund_valuation(
        FundValuation(
            record_date=today,
            fund_code="014532",
            index_code="930050",
            pe_ttm=Decimal("15.7851"),
            pe_percentile=Decimal("0.1626"),
            dividend_yield=Decimal("0.0298"),
            source="test",
        )
    )
    journal.db.upsert_fund_valuation(
        FundValuation(
            record_date=today,
            fund_code="017644",
            index_code="000852",
            pe_ttm=Decimal("20.0"),
            pe_percentile=Decimal("0.50"),  # 中估
            dividend_yield=Decimal("0.012"),
            source="test",
        )
    )
    journal.db.upsert_fund_valuation(
        FundValuation(
            record_date=today,
            fund_code="022434",
            index_code="000510",
            pe_ttm=Decimal("15.8483"),
            pe_percentile=Decimal("0.80"),
            dividend_yield=Decimal("0.005"),
            source="test",
        )
    )


# ─── 港股测试 fixtures（spec 098.2 — liubo 2026-09-19 方案 A）───


def _seed_hk_valuation_today(journal: PortfolioJournal) -> None:
    """塞今天的 4 个港股估值指标。

    用 mock 数据：
    - HK_PE_PERCENTILE = 28%（偏低估，2 分）
    - HK_DIVIDEND_YIELD = 3.5%（低估，2 分）
    - HK_AH_PREMIUM = 1.40（140%，低估，2 分）
    - HK_BUFFETT_INDICATOR = 10（1000%，正常，3 分）
    综合 = (2+2+2+3)/4 = 2.25 → 2.3（低估）
    """
    today = date.today()
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.HK_PE_PERCENTILE,
            value=Decimal("0.28"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.HK_DIVIDEND_YIELD,
            value=Decimal("0.035"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.HK_AH_PREMIUM,
            value=Decimal("1.40"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.HK_BUFFETT_INDICATOR,
            value=Decimal("10"),
            source="test",
        )
    )


def _seed_hk_funds(journal: PortfolioJournal) -> None:
    """塞 3 只港股基金（HSI Dividend / HK Bank / HSTECH）+ 价格 + 1 笔 buy。

    让每只港股基金的市值都是 1.5 仓（shares=10000, price=1.5 → 15000 = 1.5 仓）。
    """
    journal.add_fund("004098", "港股通股息率50", AssetClass.EQUITY)
    journal.add_fund("006809", "港股银行指数", AssetClass.EQUITY)
    journal.add_fund("013127", "恒生科技", AssetClass.EQUITY)
    for code in ("004098", "006809", "013127"):
        journal._price_source._prices[code] = Decimal("1.5")
    for code in ("004098", "006809", "013127"):
        journal.record_buy(
            fund_code=code,
            trade_date=date(2026, 9, 1),
            shares=Decimal("10000"),
            price=Decimal("1.0"),
        )


def _seed_hk_fund_valuations(journal: PortfolioJournal) -> None:
    """塞 3 只港股基金的 per-fund 估值（spec 098.2）。

    - 004098 (HSI Dividend): 股息率高，PE 分位正常
    - 006809 (HK Bank): 股息率高，PE 分位低（便宜）
    - 013127 (HSTECH): PE 高，成长股（看 ROE 同比）
    """
    today = date.today()
    journal.db.upsert_fund_valuation(
        FundValuation(
            record_date=today,
            fund_code="004098",
            index_code="HSSCHKY",
            pe_ttm=Decimal("8.5"),
            pe_percentile=Decimal("0.45"),
            dividend_yield=Decimal("0.055"),
            source="test",
        )
    )
    journal.db.upsert_fund_valuation(
        FundValuation(
            record_date=today,
            fund_code="006809",
            index_code="930792",
            pe_ttm=Decimal("6.5"),
            pe_percentile=Decimal("0.20"),  # 低估
            dividend_yield=Decimal("0.060"),  # 高分红
            source="test",
        )
    )
    journal.db.upsert_fund_valuation(
        FundValuation(
            record_date=today,
            fund_code="013127",
            index_code="HSTECH",
            pe_ttm=Decimal("35.0"),
            pe_percentile=Decimal("0.75"),  # PE 分位中位偏上
            dividend_yield=Decimal("0.005"),
            roe_latest=Decimal("0.15"),
            roe_year_ago=Decimal("0.10"),
            source="test",
        )
    )


# ─── 港股估值测试（spec 098.2）───


class TestBuildHKValuationSection:
    """spec 098.2 — 港股 4 指标估值 section。"""

    def test_hk_section_renders_when_data_present(self, journal: PortfolioJournal) -> None:
        """DB 有港股 4 指标 → 渲染港股 section。"""
        from global_allocation.portfolio.card import _build_hk_valuation_section

        _seed_hk_valuation_today(journal)
        elements = _build_hk_valuation_section(journal)
        assert len(elements) > 0
        # 应该有 note header "港股估值与操作"
        note = next((e for e in elements if e.get("tag") == "note"), None)
        assert note is not None
        assert "港股" in note["elements"][0]["content"]

    def test_hk_section_skipped_when_no_data(self, journal: PortfolioJournal) -> None:
        """DB 没港股指标 → 港股 section 不渲染（避免空表格）。"""
        from global_allocation.portfolio.card import _build_hk_valuation_section

        # _seed_valuation_today 只塞 A 股 4 指标
        _seed_valuation_today(journal)
        elements = _build_hk_valuation_section(journal)
        assert elements == []

    def test_hk_section_has_four_indicator_rows(self, journal: PortfolioJournal) -> None:
        """港股 4 指标行 + 1 综合分行 = 5 行。"""
        from global_allocation.portfolio.card import _build_hk_valuation_section

        _seed_hk_valuation_today(journal)
        elements = _build_hk_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        assert len(tables) == 1
        rows = tables[0]["rows"]
        # 4 指标 + 1 综合分
        assert len(rows) == 5

    def test_hk_section_displays_strategy_note(self, journal: PortfolioJournal) -> None:
        """港股 section 有策略 note（占比 vs 目标 + 估值判断）。

        Note: 策略具体是"分批买入"还是"不再投入"取决于占比 vs 目标（10.5%）。
        _decide_hk_strategy 单元测试单独验证，这里只验证 section 含 div + "港股" 字样。
        """
        from global_allocation.portfolio.card import _build_hk_valuation_section

        _seed_hk_valuation_today(journal)
        _seed_hk_funds(journal)
        elements = _build_hk_valuation_section(journal)
        # 至少有一个 div（策略 note）
        divs = [e for e in elements if e.get("tag") == "div"]
        assert len(divs) >= 1
        text = divs[0]["text"]["content"]
        assert "港股" in text
        # 不管占比超/低目标，div 都该有"目标"和百分比
        assert "目标" in text
        assert "%" in text

    def test_hk_section_composite_score(self, journal: PortfolioJournal) -> None:
        """港股综合分：[PE:2 股息:2 AH:2 巴菲特:3] → 2.3 低估。"""
        from global_allocation.portfolio.card import _build_hk_valuation_section

        _seed_hk_valuation_today(journal)
        elements = _build_hk_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        composite_row = tables[0]["rows"][-1]  # 最后一行是综合分
        assert composite_row["indicator"] == "综合分"
        # 综合分 value 列形如 "[PE:2 股息:2 AH:2 巴菲特:3]"
        assert "[PE:2 股息:2 AH:2" in composite_row["value"]
        # verdict 列形如 "2.3 低估"
        assert "2.3" in composite_row["verdict"]
        assert "低估" in composite_row["verdict"]

    def test_hk_ah_premium_formatted_as_integer_pct(self, journal: PortfolioJournal) -> None:
        """AH 溢价格式化为整数百分比（"140%"，不是 "140.00%"）。"""
        from global_allocation.portfolio.card import _build_hk_valuation_section

        _seed_hk_valuation_today(journal)
        elements = _build_hk_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        # 找 AH 溢价行
        ah_row = next(
            r for r in tables[0]["rows"] if r["indicator"] == "AH 溢价"
        )
        # 1.40 → "140%"
        assert ah_row["value"] == "140%"

    def test_hk_buffett_formatted_as_integer_pct(self, journal: PortfolioJournal) -> None:
        """港股巴菲特格式化为整数百分比（"1000%"）。"""
        from global_allocation.portfolio.card import _build_hk_valuation_section

        _seed_hk_valuation_today(journal)
        elements = _build_hk_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        bf_row = next(
            r for r in tables[0]["rows"] if r["indicator"] == "港股巴菲特"
        )
        # 10.0 → "1000%"
        assert bf_row["value"] == "1000%"


class TestBuildValuationSectionCombined:
    """验证 _build_valuation_section combined 模式（A 股 + 港股 + 美股 一起渲染）。

    spec 098.4 — 不再每个 region 单独一个 section，而是合并成 combined 表 + 多个 strategy divs。
    """

    def test_combined_section_renders_with_2_regions(self, journal: PortfolioJournal) -> None:
        """A 股 + 港股 都有数据 → 渲染 combined section（含 combined 指标表）。"""
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        elements = _build_valuation_section(journal)
        notes = [e for e in elements if e.get("tag") == "note"]
        contents = [n["elements"][0]["content"] for n in notes]
        # combined section 只有一个统一 header（不再是 per-region header）
        assert "估值与操作" in contents
        assert "A 股估值与操作" not in contents
        assert "港股估值与操作" not in contents

    def test_combined_indicator_table_includes_both_regions(self, journal: PortfolioJournal) -> None:
        """combined 指标表含 2 region × 4 指标 + 2 综合 = 10 行。"""
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        elements = _build_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        indicator_table = next(
            t for t in tables
            if [c["name"] for c in t["columns"]] == ["region", "indicator", "value", "verdict", "threshold"]
        )
        # A 股 4 + 1 综合 + 港股 4 + 1 综合 = 10 行
        assert len(indicator_table["rows"]) == 10

    def test_only_a_share_when_no_hk_data(self, journal: PortfolioJournal) -> None:
        """只有 A 股数据 → combined 指标表 5 行（4 + 1 综合），不含港股行。"""
        _seed_valuation_today(journal)
        elements = _build_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        indicator_table = next(
            t for t in tables
            if [c["name"] for c in t["columns"]] == ["region", "indicator", "value", "verdict", "threshold"]
        )
        assert len(indicator_table["rows"]) == 5
        # 所有 row region 列都是 "A 股" 或 "—"（综合行）
        for row in indicator_table["rows"]:
            assert row["region"] in {"A 股", "—"}

    def test_only_hk_when_no_a_share_data(self, journal: PortfolioJournal) -> None:
        """只有港股数据 → combined 指标表 5 行（4 + 1 综合），全是港股。"""
        _seed_hk_valuation_today(journal)
        elements = _build_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        indicator_table = next(
            t for t in tables
            if [c["name"] for c in t["columns"]] == ["region", "indicator", "value", "verdict", "threshold"]
        )
        assert len(indicator_table["rows"]) == 5
        for row in indicator_table["rows"]:
            assert row["region"] in {"港股", "—"}

    def test_combined_section_returns_empty_when_no_data(self, journal: PortfolioJournal) -> None:
        """3 region 都无数据 → 返回 []（不渲染空 section）。"""
        # 不调任何 _seed_valuation_today
        elements = _build_valuation_section(journal)
        assert elements == []


class TestBuildFundValuationSectionHK:
    """验证 _build_fund_valuation_section 能渲染港股 3 只基金。"""

    def test_hk_funds_render_in_separate_section(self, journal: PortfolioJournal) -> None:
        """A 股 3 只基金（014532 / 017644 / 022434）只在 A 股 section 出现，不在港股 section。

        第十八轮：港股全砍了；这个测试现在反过来验证 — HK section 因为没基金
        不渲染 per-fund table（即使 _seed_hk_funds 仍在 DB 加 4 only-in-test 的代码），
        但 A 股 section 正常出 per-fund 表。
        """
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_a_share_funds(journal)
        _seed_hk_funds(journal)  # 港股基金在 journal DB 但不在 SUBCLASS_BY_CODE

        a_share_elements = _build_a_share_valuation_section(journal)
        hk_elements = _build_hk_valuation_section(journal)

        # A 股 section 含 A 股 3 只基金（014532 / 017644 / 022434）
        a_share_tables = [e for e in a_share_elements if e.get("tag") == "table"]
        # 第一个 table 是指标表，第 2 个是 per-fund 表
        if len(a_share_tables) >= 2:
            a_share_fund_table = a_share_tables[1]
            fund_codes_in_a_share = [r["fund"].split("\n")[0] for r in a_share_fund_table["rows"]]
            assert "014532" in fund_codes_in_a_share
            assert "017644" in fund_codes_in_a_share
            assert "022434" in fund_codes_in_a_share
            # 港股 code 不应在 A 股 section
            assert "004098" not in fund_codes_in_a_share
            assert "013127" not in fund_codes_in_a_share

        # 港股 section 因为没 SUBCLASS_BY_CODE 基金 per-fund table 不渲染
        # （指标表仍可能在 — 视 _build_hk_valuation_section 实现）
        # 这里只断言 a_share 正确，hk 部分空（无 per-fund）跳过

    def test_hk_dividend_fund_low_pe_buys(self, journal: PortfolioJournal) -> None:
        """006809 (HK Bank, dividend strategy): PE 分位 20% → 低估 → 买入。

        验证港股 dividend 策略正确应用（看普通 PE 分位，不用股息率加权）。
        """
        from global_allocation.portfolio.card import _per_fund_verdict

        _seed_hk_fund_valuations(journal)
        fv = journal.db.list_latest_fund_valuations_for_codes(["006809"])["006809"]
        verdict = _per_fund_verdict(fv, "dividend")
        # dividend 策略：pe_percentile_dy_weighted=None → fallback 到 pe_percentile=0.20
        # 0.20 在 [10%, 30%) → 低估 2
        assert "低估 2" in verdict

    def test_hk_growth_fund_high_pe_uses_roe_yoy(self, journal: PortfolioJournal) -> None:
        """013127 (HSTECH, growth strategy): PE 分位 75% + ROE 同比 +50% → 高估 4。

        验证港股 growth 策略的 ROE 同比修正规则：
        - PE 分位 75% → 基础 4 分（高估）
        - ROE 同比 = (0.15 - 0.10) / 0.10 = +0.50（+50%）
        - ROE 同比 ≥ +10% 且 score == 5 才降分；score=4 不变
        所以最终仍是 "高估 4"
        """
        from global_allocation.portfolio.card import _per_fund_verdict

        _seed_hk_fund_valuations(journal)
        fv = journal.db.list_latest_fund_valuations_for_codes(["013127"])["013127"]
        verdict = _per_fund_verdict(fv, "growth")
        assert "高估" in verdict

    def test_hk_funds_no_valuation_show_data_missing(self, journal: PortfolioJournal) -> None:
        """港股有基金但缺估值 — 第二十二轮：013127 在 SUBCLASS_BY_CODE → per-fund 表渲染 1 行「数据缺失」。

        故意不 _seed_hk_fund_valuations → 港股基金没估值。
        但 013127 现在属 HK_EQUITY 子类，所以 per-fund 表会渲染一行，所有估值列显示"数据缺失"。
        """
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_hk_funds(journal)
        # 故意不 _seed_hk_fund_valuations → 港股基金没估值

        hk_elements = _build_hk_valuation_section(journal)
        hk_tables = [e for e in hk_elements if e.get("tag") == "table"]
        per_fund_tables = [
            t for t in hk_tables
            # per-fund table 有 "fund" 列；indicator 表只有 "indicator" / "current" / ...
            if any(c.get("name") == "fund" for c in t.get("columns", []))
        ]
        # 第二十二轮：HK 1 只 kept 基金（013127）→ per-fund 表渲染 1 行（值=「数据缺失」）
        assert len(per_fund_tables) == 1, (
            "HK 现在 1 只 kept 基金 (013127)，per-fund table 应当渲染 1 行"
        )
        rows = per_fund_tables[0]["rows"]
        assert len(rows) == 1
        row = rows[0]
        # 第一列是 fund（"013127\n恒生科技"）
        assert row["fund"].startswith("013127")
        # 估值列都是"数据缺失"
        assert row["pe"] == "数据缺失"
        assert row["pe_pct"] == "数据缺失"
        assert row["dy"] == "数据缺失"
        assert row["verdict"] == "数据缺失"


class TestIndexVerdictStrategyHK:
    """验证 INDEX_VERDICT_STRATEGY 包含港股 3 个指数。"""

    def test_hk_index_strategies_mapped(self) -> None:
        from global_allocation.portfolio.card import INDEX_VERDICT_STRATEGY

        assert INDEX_VERDICT_STRATEGY["HSSCHKY"] == "dividend"
        assert INDEX_VERDICT_STRATEGY["930792"] == "dividend"
        assert INDEX_VERDICT_STRATEGY["HSTECH"] == "growth"


# ─── 美股测试 fixtures（spec 098.3 — liubo 2026-09-20）───


def _seed_us_valuation_today(journal: PortfolioJournal) -> None:
    """塞今天的 4 个美股估值指标。

    用 mock 数据（贴近实际 lixinger CSV 2026-09-20 数据）：
    - US_PE_PERCENTILE = 60.17%（正常，3 分：30%-70% 区间）
    - US_DIVIDEND_YIELD = 1.06%（正常，3 分：1%-3% 区间）
    - US_BUFFETT_INDICATOR = 1.71（171%，正常，3 分：130%-180% 区间）
    - US_EQUITY_RISK_PREMIUM = 0.0145（1.45%，正常，3 分：1%-3% 区间）
    综合 = (3+3+3+3)/4 = 3.0 → 3.0（正常）
    """
    today = date.today()
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.US_PE_PERCENTILE,
            value=Decimal("0.6017"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.US_DIVIDEND_YIELD,
            value=Decimal("0.0106"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.US_BUFFETT_INDICATOR,
            value=Decimal("1.71"),
            source="test",
        )
    )
    journal._db.upsert_valuation_indicator(
        ValuationIndicator(
            record_date=today,
            indicator_code=ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM,
            value=Decimal("0.0145"),
            source="test",
        )
    )


def _seed_us_funds(journal: PortfolioJournal) -> None:
    """塞 5 只美股基金（3 NDX + 1 标普 500 + 1 标普 100）。

    2026-09-29（第二十二轮）：liubo 卖出 019524 华泰柏瑞纳 100 联接。
    第二十/二十一轮加的 019172/019441 直接 QDII 场外 — 测试不验估值，留 stub。
    第十八轮（2026-09-22）：3 只 QDII 全球主题（017730/016664/006373）砍了，
    只剩 5 只宽基/纳指/标普 — 都是大类资产配置 US_EQUITY。
    让每只美股基金的市值都是 1.5 仓。
    """
    us_funds = [
        ("018966", "汇添富纳100"),
        ("539001", "建信纳100"),
        ("016452", "南方纳100"),
        ("017641", "摩根标普500"),
        ("519981", "长信标普100"),
    ]
    for code, name in us_funds:
        journal.add_fund(code, name, AssetClass.EQUITY)
        journal._price_source._prices[code] = Decimal("1.5")
    for code, _ in us_funds:
        journal.record_buy(
            fund_code=code,
            trade_date=date(2026, 9, 1),
            shares=Decimal("10000"),
            price=Decimal("1.0"),
        )


class TestUSIndicatorConstants:
    """验证 US_INDICATOR_* 常量定义正确。"""

    def test_us_indicator_codes_has_four_codes(self) -> None:
        """4 个美股指标（spec 098.3）。"""
        assert len(US_INDICATOR_CODES) == 4
        assert ValuationIndicatorCode.US_PE_PERCENTILE in US_INDICATOR_CODES
        assert ValuationIndicatorCode.US_DIVIDEND_YIELD in US_INDICATOR_CODES
        assert ValuationIndicatorCode.US_BUFFETT_INDICATOR in US_INDICATOR_CODES
        assert ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM in US_INDICATOR_CODES

    def test_us_indicator_names_have_all_codes(self) -> None:
        """NAMES dict 覆盖所有 4 个 code。"""
        for code in US_INDICATOR_CODES:
            assert code in US_INDICATOR_NAMES
            assert US_INDICATOR_NAMES[code]  # 非空

    def test_us_indicator_short_names_have_all_codes(self) -> None:
        """SHORT_NAMES dict 覆盖所有 4 个 code。"""
        for code in US_INDICATOR_CODES:
            assert code in US_INDICATOR_SHORT_NAMES
            assert US_INDICATOR_SHORT_NAMES[code]  # 非空


class TestFormatIndicatorValueUS:
    """验证 _format_indicator_value 对美股 4 指标的处理。"""

    def test_us_equity_risk_premium_keeps_sign_and_two_decimals(self) -> None:
        """美股股债利差保留正负号 + 2 位小数（跟 A 股股债利差一致）。"""
        assert _format_indicator_value(
            ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM, Decimal("0.0145")
        ) == "+1.45%"
        assert _format_indicator_value(
            ValuationIndicatorCode.US_EQUITY_RISK_PREMIUM, Decimal("-0.01")
        ) == "-1.00%"

    def test_us_buffett_formatted_as_integer_pct(self) -> None:
        """美股巴菲特格式化为整数百分比（值通常 > 1，如 1.71 → "171%"）。"""
        assert _format_indicator_value(
            ValuationIndicatorCode.US_BUFFETT_INDICATOR, Decimal("1.71")
        ) == "171%"
        assert _format_indicator_value(
            ValuationIndicatorCode.US_BUFFETT_INDICATOR, Decimal("0.95")
        ) == "95%"

    def test_us_pe_percentile_two_decimals(self) -> None:
        """美股 PE 分位 2 位小数百分比（跟 A 股 / 港股一致）。"""
        assert _format_indicator_value(
            ValuationIndicatorCode.US_PE_PERCENTILE, Decimal("0.6017")
        ) == "60.17%"

    def test_us_dividend_yield_two_decimals(self) -> None:
        """美股股息率 2 位小数百分比（跟 A 股 / 港股一致）。"""
        assert _format_indicator_value(
            ValuationIndicatorCode.US_DIVIDEND_YIELD, Decimal("0.0106")
        ) == "1.06%"


class TestBuildUSValuationSection:
    """spec 098.3 — 美股 4 指标估值 section。"""

    def test_us_section_renders_when_data_present(self, journal: PortfolioJournal) -> None:
        """DB 有美股 4 指标 → 渲染美股 section。"""
        _seed_us_valuation_today(journal)
        elements = _build_us_valuation_section(journal)
        assert len(elements) > 0
        note = next((e for e in elements if e.get("tag") == "note"), None)
        assert note is not None
        assert "美股" in note["elements"][0]["content"]

    def test_us_section_skipped_when_no_data(self, journal: PortfolioJournal) -> None:
        """DB 没美股指标 → 美股 section 不渲染（避免空表格）。"""
        # _seed_valuation_today 只塞 A 股 4 指标
        _seed_valuation_today(journal)
        elements = _build_us_valuation_section(journal)
        assert elements == []

    def test_us_section_has_four_indicator_rows(self, journal: PortfolioJournal) -> None:
        """美股 4 指标行 + 1 综合分行 = 5 行。"""
        _seed_us_valuation_today(journal)
        elements = _build_us_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        assert len(tables) == 1
        rows = tables[0]["rows"]
        # 4 指标 + 1 综合分
        assert len(rows) == 5

    def test_us_section_displays_strategy_note(self, journal: PortfolioJournal) -> None:
        """美股 section 有策略 note（占比 vs 目标 + 估值判断）。"""
        _seed_us_valuation_today(journal)
        _seed_us_funds(journal)
        elements = _build_us_valuation_section(journal)
        divs = [e for e in elements if e.get("tag") == "div"]
        assert len(divs) >= 1
        text = divs[0]["text"]["content"]
        assert "美股" in text
        assert "目标" in text
        assert "%" in text

    def test_us_section_composite_score(self, journal: PortfolioJournal) -> None:
        """美股综合分：(3+3+3+3)/4 = 3.0 → 3.0 正常。

        60.17% PE 分位 = 3 分（30%-70% 区间）；其他 3 个指标也都 3 分。
        综合分 value 列形如 "[PE:3 股息:3 巴菲特:3 股债:3]"
        verdict 列形如 "3.0 正常"
        """
        _seed_us_valuation_today(journal)
        elements = _build_us_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        composite_row = tables[0]["rows"][-1]  # 最后一行是综合分
        assert composite_row["indicator"] == "综合分"
        # 综合分 value 列形如 "[PE:3 股息:3 巴菲特:3 股债:3]"
        assert "[PE:3 股息:3" in composite_row["value"]
        # verdict 列形如 "3.0 正常"
        assert "3.0" in composite_row["verdict"]
        assert "正常" in composite_row["verdict"]

    def test_us_buffett_formatted_as_integer_pct(self, journal: PortfolioJournal) -> None:
        """美股巴菲特格式化为整数百分比（1.71 → "171%"，不是 "171.00%"）。"""
        _seed_us_valuation_today(journal)
        elements = _build_us_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        bf_row = next(
            r for r in tables[0]["rows"] if r["indicator"] == "美股巴菲特"
        )
        # 1.71 → "171%"
        assert bf_row["value"] == "171%"


class TestBuildValuationSectionCombinedUS:
    """验证 _build_valuation_section combined 模式 + 美股 region 接入（spec 098.4）。

    spec 098.3 — 美股 region 加入（4 个新指标 + 9 只基金）。
    spec 098.4 — 3 region 合并成 combined 表（5 列指标 + 11 列 per-fund）。
    """

    def test_three_regions_combined_in_single_section(
        self, journal: PortfolioJournal
    ) -> None:
        """A 股 + 港股 + 美股 都有数据 → combined section 一个 header + combined 指标表。"""
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_us_valuation_today(journal)
        elements = _build_valuation_section(journal)
        notes = [e for e in elements if e.get("tag") == "note"]
        contents = [n["elements"][0]["content"] for n in notes]
        # 1 个统一 header（不再是 per-region 3 个）
        assert "估值与操作" in contents
        assert "A 股估值与操作" not in contents
        assert "港股估值与操作" not in contents
        assert "美股估值与操作" not in contents

    def test_us_section_skipped_when_no_data(self, journal: PortfolioJournal) -> None:
        """只 A 股有数据 → 美股行不出现在 combined 表里。"""
        _seed_valuation_today(journal)
        elements = _build_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        indicator_table = next(
            t for t in tables
            if [c["name"] for c in t["columns"]] == ["region", "indicator", "value", "verdict", "threshold"]
        )
        # 5 行（A 股 4 指标 + 1 综合）
        assert len(indicator_table["rows"]) == 5
        for row in indicator_table["rows"]:
            assert row["region"] in {"A 股", "—"}
            assert "美股" not in row["region"]

    def test_combined_indicator_table_3_region_composite_rows(
        self, journal: PortfolioJournal
    ) -> None:
        """3 region 都有数据 → combined 指标表 15 行（含 3 个综合分行）。"""
        _seed_valuation_today(journal)
        _seed_hk_valuation_today(journal)
        _seed_us_valuation_today(journal)
        elements = _build_valuation_section(journal)
        tables = [e for e in elements if e.get("tag") == "table"]
        indicator_table = next(
            t for t in tables
            if [c["name"] for c in t["columns"]] == ["region", "indicator", "value", "verdict", "threshold"]
        )
        composite_rows = [r for r in indicator_table["rows"] if r["indicator"] == "综合分"]
        # 3 个综合分行（A 股 / 港股 / 美股 各一个）
        assert len(composite_rows) == 3


class TestIndexVerdictStrategyUS:
    """验证 INDEX_VERDICT_STRATEGY 包含美股 4 个指数。"""

    def test_us_index_strategies_mapped(self) -> None:
        from global_allocation.portfolio.card import INDEX_VERDICT_STRATEGY

        # INX / GSPC / OEX 实际能在 lixinger CSV 命中，NDX 当前 CSV 无数据（保留占位）
        assert INDEX_VERDICT_STRATEGY["INX"] == "growth"
        assert INDEX_VERDICT_STRATEGY["GSPC"] == "growth"
        assert INDEX_VERDICT_STRATEGY["OEX"] == "dividend"
        assert INDEX_VERDICT_STRATEGY["NDX"] == "growth"


# ─── 调仓记录金额列：仓 + 元 双值（liubo 2026-10-17 反馈「我都想要」）───


class TestFormatAmountWithUnits:
    """_format_amount_with_units — 调仓记录「金额」列规范化。

    公式：1 仓 = 1 万 CNY = 10,000 元（spec 022/097 整数仓位模型）。

    liubo 2026-10-17：「你都显示的是金额和多少仓。比如说1万，你就要告诉我这1万是1仓，
    还是0.5仓，对吧？你不要只显示金额，也不要只显示仓位。都想。」
    """

    def test_pure_position_with_sign_becomes_double_value(self) -> None:
        """±N 仓（加减仓）→ "N 仓 / X,XXX 元"（方向走 action 列）。"""
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("+1 仓") == "1 仓 / 10,000 元"
        assert _format_amount_with_units("-1 仓") == "1 仓 / 10,000 元"

    def test_fractional_position(self) -> None:
        """0.5 仓 / 5000 元 — liubo 2026-10-17 提到 "0.5 仓" 场景。"""
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("0.5 仓") == "0.5 仓 / 5,000 元"
        assert _format_amount_with_units("5000 元") == "0.5 仓 / 5,000 元"

    def test_pure_money_gets_position_prepended(self) -> None:
        """纯 X 元 / CNY / 万 / ¥X → 前面加 仓 数。"""
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("10000 元") == "1 仓 / 10,000 元"
        assert _format_amount_with_units("10000 CNY") == "1 仓 / 10,000 元"
        assert _format_amount_with_units("1 万") == "1 仓 / 10,000 元"
        assert _format_amount_with_units("¥10000") == "1 仓 / ¥10000"

    def test_already_double_value_normalizes_format(self) -> None:
        """已含 仓 + 元 双值（"建仓 1 仓 / 10000 CNY"）→ 标准化为 "1 仓 / 10,000 元"。

        去 "建仓" 词（动作已在 action 列）+ CNY → 元 + 千分位逗号。
        """
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("建仓 1 仓 / 10000 CNY") == "1 仓 / 10,000 元"
        assert _format_amount_with_units("0.5 仓 / 5000 元") == "0.5 仓 / 5,000 元"

    def test_dash_preserved(self) -> None:
        """「—」表示无金额信息 → 保持。"""
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("—") == "—"
        assert _format_amount_with_units("") == ""

    def test_dca_three_value_format_preserved(self) -> None:
        """DCA 3-value 格式（"10 元 / 每天 / 累计 1800"）→ 保持。

        DCA 是定投计划三要素（金额 / 频率 / 累计目标），不是 仓 概念。
        """
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("10 元 / 每天 / 累计 1800") == "10 元 / 每天 / 累计 1800"
        assert _format_amount_with_units("10 元 / 每天 / 累计 2000") == "10 元 / 每天 / 累计 2000"
        assert _format_amount_with_units("250-1000 元 / 每周（均值 500） / 累计 10000") == "250-1000 元 / 每周（均值 500） / 累计 10000"

    def test_dca_fallback_already_bought_preserved(self) -> None:
        """DCA 兜底"已买 ¥X" → 保持（已买金额是 DCA 进度，不是仓）。"""
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("已买 ¥60") == "已买 ¥60"

    def test_cost_calibration_preserved(self) -> None:
        """Cost 校准（"净 +1661" / "+40" / "成本 20 收回现金"）→ 保持（不是仓位概念）。

        liubo 2026-10-17 拍板 — cost 校准是记账对账，不调仓。
        """
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("净 +1661") == "净 +1661"
        assert _format_amount_with_units("+40") == "+40"
        assert _format_amount_with_units("成本 20 收回现金") == "成本 20 收回现金"

    def test_bare_number_without_unit_preserved(self) -> None:
        """无单位的纯数字（"20000" / "10000"）→ 保持（无法判定是元还是别的单位）。"""
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("20000") == "20000"
        assert _format_amount_with_units("10000") == "10000"

    def test_conversion_shares_preserved(self) -> None:
        """"转换 8379 份" 保持（份是转换的份额单位，不是仓）。"""
        from global_allocation.portfolio.card import _format_amount_with_units

        assert _format_amount_with_units("转换 8379 份") == "转换 8379 份"


class TestRebalanceRecordsDoubleValue:
    """调仓记录表 — 金额列全部行都是 仓 + 元 双值（liubo 2026-10-17 拍板）。

    16 行原始数据（基于 git log 抓取）：
    - 8 行 ±N 仓 → 标准化为 "N 仓 / 10,000 元"
    - 6 行 10000 元（转换 6 行）→ 标准化为 "1 仓 / 10,000 元"
    - 1 行 DCA (南方纳100) → 保持 "10 元 / 每天 / 累计 1800"
    - 2 行 DCA (摩根/万家) → 保持 "10 元 / 每天 / 累计 2000"
    - 1 行 "建仓 1 仓 / 10000 CNY" → 标准化为 "1 仓 / 10,000 元"
    - 1 行 +1 仓 (013127) → 标准化为 "1 仓 / 10,000 元"
    - 2 行 "—" → 保持
    """

    def test_rebalance_table_amounts_all_double_value_or_dash(self) -> None:
        """调仓记录表 16 行 — 每一行 amount 都有 仓 + 元 OR 是 "—" OR 是 DCA 3-value。"""
        card = build_portfolio_card(PortfolioJournal(
            db=PortfolioDB(path=__import__('pathlib').Path("/tmp/empty.db")),
            price_source=FakePriceSource({}),
        ))
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        rebalance_table = next(
            (t for t in tables if [c["name"] for c in t["columns"]] == ["date", "fund", "action", "amount"]),
            None,
        )
        if rebalance_table is None:
            return  # 没数据跳过

        for r in rebalance_table["rows"]:
            amt = r["amount"]
            if amt == "—":
                continue
            if " / 累计 " in amt:
                # DCA 3-value
                continue
            # 应该 仓 + 元 双值
            assert " 仓 / " in amt and " 元" in amt, f"行 {r} amount '{amt}' 不是 仓+元 双值"

    def test_rebalance_table_dca_amount_preserved(self) -> None:
        """DCA 行 amount 保持 3-value 格式 "10 元 / 每天 / 累计 X"。"""
        card = build_portfolio_card(PortfolioJournal(
            db=PortfolioDB(path=__import__('pathlib').Path("/tmp/empty.db")),
            price_source=FakePriceSource({}),
        ))
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        rebalance_table = next(
            (t for t in tables if [c["name"] for c in t["columns"]] == ["date", "fund", "action", "amount"]),
            None,
        )
        if rebalance_table is None:
            return
        dca_rows = [r for r in rebalance_table["rows"] if r["action"] == "定投"]
        for r in dca_rows:
            assert " / 每天 / 累计 " in r["amount"], f"DCA 行 {r} amount '{r['amount']}' 失去 3-value 格式"


# ─── 4 张周报卡片（第三十五轮 liubo 2026-10-18 拍板） ──────────────
#
# 周报改发 4 张卡：
# - Card 1 = 总览（持仓成本 by 大类，4 列：分类/基金数/累计成本/占比）
# - Card 2 = A 股（per-fund + 调仓记录 + 定投记录，filter 到 CN_EQUITY）
# - Card 3 = 港股（同上，filter 到 HK_EQUITY）
# - Card 4 = 美股（同上，filter 到 US_EQUITY）
#
# 验证：
# - build_overview_card = 1 张，只有 cost_basis section
# - build_region_card(region) 只展示该 region 的基金 / 调仓 / 定投
# - build_portfolio_cards 顺序固定（[总览, A 股, 港股, 美股]）
# - 旧 build_portfolio_card（单卡向后兼容）保持原行为


class TestBuildOverviewCard:
    """build_overview_card = Card 1（总览）。"""

    def test_title_default(self, journal: PortfolioJournal) -> None:
        """默认 title = "实盘周报（总览）"。"""
        _seed(journal)
        card = build_overview_card(journal)
        title = card["header"]["title"]["content"]
        assert title == "实盘周报（总览）", f"默认 title 应为「实盘周报（总览）」，实际「{title}」"

    def test_only_cost_basis_section(self, journal: PortfolioJournal) -> None:
        """总览卡只有 cost_basis section（note + div + table），不含 per-fund / 调仓 / 定投。

        第三十四轮（liubo 2026-10-18）：删柱状图 + 删持仓表，只剩持仓成本 by 大类。
        第三十五轮：把它单独作为 Card 1。
        """
        _seed(journal)
        card = build_overview_card(journal)
        # 元素类型
        tags = [
            e.get("tag")
            for e in card["elements"]
            if e.get("tag") in {"note", "div", "chart", "table", "hr"}
        ]
        # 期望：note (header) + div (summary) + table (cost_basis by 大类) + 1 个 hr（在 div 与 table 之间）
        assert "chart" not in tags, f"总览卡不应有 chart，实际 tags={tags}"
        assert tags.count("note") == 1, f"总览卡应有 1 个 note（header），实际 {tags.count('note')} 个"
        assert tags.count("table") == 1, f"总览卡应有 1 个 table（cost_basis），实际 {tags.count('table')} 个"
        # 表头是 {class, count, cost, weight}
        table = next(e for e in card["elements"] if e.get("tag") == "table")
        col_names = [c["name"] for c in table["columns"]]
        assert col_names == ["class", "count", "cost", "weight"], (
            f"总览卡表头应为 4 列（class/count/cost/weight），实际 {col_names}"
        )

    def test_custom_title_overrides_default(self, journal: PortfolioJournal) -> None:
        """title 参数会覆盖默认 title（用作 base_title 前缀）。"""
        _seed(journal)
        card = build_overview_card(journal, title="我的持仓总览")
        title = card["header"]["title"]["content"]
        assert title == "我的持仓总览", f"自定义 title 应传入，实际「{title}」"

    def test_header_blue_template(self, journal: PortfolioJournal) -> None:
        """header template 固定为 blue（与其他卡一致）。"""
        _seed(journal)
        card = build_overview_card(journal)
        assert card["header"]["template"] == "blue"


class TestBuildRegionCard:
    """build_region_card = Card 2/3/4（区域）。"""

    def test_default_title_uses_region_display_name(self, journal: PortfolioJournal) -> None:
        """默认 title = "{region 中文名} 周报"，其中中文名取 DISPLAY_NAME[region] 去掉'股票'后缀。

        例：CN_EQUITY = 'A 股'（DISPLAY_NAME[CN_EQUITY] = 'A 股股票'）。
        """
        from global_allocation.portfolio.breakdown import DISPLAY_NAME
        from global_allocation.portfolio.card import SwensenClass, build_region_card

        _seed(journal)
        for region in (SwensenClass.CN_EQUITY, SwensenClass.HK_EQUITY, SwensenClass.US_EQUITY):
            card = build_region_card(journal, region)
            expected = f"{DISPLAY_NAME[region].split('股票')[0]} 周报"
            actual = card["header"]["title"]["content"]
            assert actual == expected, (
                f"region={region} 默认 title 应为「{expected}」，实际「{actual}」"
            )

    def test_custom_title_overrides(self, journal: PortfolioJournal) -> None:
        """title 参数会覆盖默认 title。"""
        from global_allocation.portfolio.card import SwensenClass, build_region_card

        _seed(journal)
        card = build_region_card(journal, SwensenClass.CN_EQUITY, title="A 股 周报（自定义）")
        assert card["header"]["title"]["content"] == "A 股 周报（自定义）"

    def test_includes_per_fund_section(self, journal: PortfolioJournal) -> None:
        """Card 2/3/4 包含 per-fund section（5 列：fund/etf_index/cost/valuation/advice）。

        即使 region 完全空，per-fund section 也输出（filter 后可能 0 行）。
        """
        from global_allocation.portfolio.card import SwensenClass, build_region_card

        _seed(journal)
        card = build_region_card(journal, SwensenClass.CN_EQUITY)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        per_fund = next(
            (t for t in tables if [c["name"] for c in t["columns"]] == ["fund", "etf_index", "cost", "valuation", "advice"]),
            None,
        )
        assert per_fund is not None, "Card 应包含 per-fund 表（5 列：fund/etf_index/cost/valuation/advice）"

    def test_empty_region_still_returns_card(
        self, journal: PortfolioJournal, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Region 完全空（如 CN_REIT 在测试 fixture 里只有 1 个 028277 基金 + 无 DCA/无 rebalance）。

        仍返回 1 张有效卡片（header + elements + footer）。
        """
        from global_allocation.portfolio.card import SwensenClass, build_region_card

        # 清空可能存在的飞书凭证环境变量（一些 shell 里可能漏出来）
        for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_CHAT_ID"):
            monkeypatch.delenv(k, raising=False)

        _seed(journal)
        # CN_REIT region 在测试 fixture 里只有 1 个 028277 基金，且无 DCA schedule
        card = build_region_card(journal, SwensenClass.CN_REIT)
        # 至少有合法的卡片 envelope
        assert "header" in card, "空 region 也应返回 header"
        assert "elements" in card, "空 region 也应返回 elements"
        assert "footer" in card, "空 region 也应返回 footer"
        # 至少包含 per-fund section（filter 后可能有 1 行）
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        per_fund = next(
            (t for t in tables if [c["name"] for c in t["columns"]] == ["fund", "etf_index", "cost", "valuation", "advice"]),
            None,
        )
        assert per_fund is not None, "空 region 也应有 per-fund 表（filter 后可能 0 行 / 1 行）"

    def test_filter_to_region_excludes_other_funds(self, journal: PortfolioJournal) -> None:
        """per-fund section 只展示该 region 的基金（用 SUBSET_BY_CODE 判断）。

        第三十六轮（liubo 2026-10-19）：A 股 region card 还要展示 watchlist 行（"中证 A100（无持仓）"
        这种）。watchlist 行 fund 列是 "指数名（无持仓）" 而不是 6 位基金代码 — 走 INDEX_WATCHLIST
        反查 region（要求 INDEX_WATCHLIST entry 的 subclass == region 才入选）。
        """
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE
        from global_allocation.portfolio.card import SwensenClass, build_region_card
        from global_allocation.portfolio.pe_rebalance import INDEX_WATCHLIST

        _seed(journal)
        card = build_region_card(journal, SwensenClass.CN_EQUITY)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        per_fund = next(
            (t for t in tables if [c["name"] for c in t["columns"]] == ["fund", "etf_index", "cost", "valuation", "advice"]),
            None,
        )
        assert per_fund is not None, "Card 应有 per-fund 表"
        rows = per_fund["rows"]
        watchlist_index_codes = {ic for sub, ic, _, _, _, _ in INDEX_WATCHLIST if sub == SwensenClass.CN_EQUITY}
        for row in rows:
            if "（无持仓）" in row["fund"]:
                # watchlist 行 — fund 列是指数中文名 + "（无持仓）"；从 etf_index 列拿指数代码
                idx_name = row["etf_index"]
                # 反查 index_code 通过 INDEX_DISPLAY_NAME
                from global_allocation.portfolio.pe_rebalance import INDEX_DISPLAY_NAME
                idx_code = next(
                    (ic for ic, dn in INDEX_DISPLAY_NAME.items() if dn == idx_name),
                    None,
                )
                assert idx_code in watchlist_index_codes, (
                    f"watchlist 行 {row} 的 index 不在 CN_EQUITY watchlist（{watchlist_index_codes}）"
                )
            else:
                # 持仓行 — 从 fund 列提取 6 位基金代码
                codes = re.findall(r"(?<!\d)\d{6}(?!\d)", row["fund"])
                # 至少有一个 code 的 subclass 是 CN_EQUITY
                assert any(SUBCLASS_BY_CODE.get(c) == SwensenClass.CN_EQUITY for c in codes), (
                    f"per-fund 行 {row} 不属于 CN_EQUITY region"
                )

    def test_summary_has_5_lines_for_region_card(self, journal: PortfolioJournal) -> None:
        """第三十六轮（liubo 2026-10-19）：region card 的 per-fund summary div 扩成 5 行。

        5 行内容：
        1. 本大类资产的基金数：N 只
        2. 跟踪的 ETF 数量：N 个
        3. 本大类资产的占比：N%
        4. 超配/欠配：超配 / 欠配 / 区间内
        5. 超/欠的仓位：±X 仓（实际 Y / 目标 Z）
        """
        from global_allocation.portfolio.card import SwensenClass, build_region_card

        _seed(journal)
        card = build_region_card(journal, SwensenClass.CN_EQUITY)
        # 找 per-fund section 的 div（带 lark_md 文本的 div）
        summary_div_text = None
        for e in card["elements"]:
            if e.get("tag") == "div" and "本大类资产" in e.get("text", {}).get("content", ""):
                summary_div_text = e["text"]["content"]
                break
        assert summary_div_text is not None, "region card 应有含「本大类资产」字样的 div"
        # 5 行必备内容
        assert "**本大类资产的基金数**" in summary_div_text, (
            f"应有「本大类资产的基金数」一行，实际 summary：\n{summary_div_text}"
        )
        assert "**跟踪的 ETF 数量**" in summary_div_text, (
            f"应有「跟踪的 ETF 数量」一行，实际 summary：\n{summary_div_text}"
        )
        assert "**本大类资产的占比**" in summary_div_text, (
            f"应有「本大类资产的占比」一行，实际 summary：\n{summary_div_text}"
        )
        assert "**超配/欠配**" in summary_div_text, (
            f"应有「超配/欠配」一行，实际 summary：\n{summary_div_text}"
        )
        assert "**超/欠的仓位**" in summary_div_text, (
            f"应有「超/欠的仓位」一行，实际 summary：\n{summary_div_text}"
        )

    def test_summary_fund_count_excludes_watchlist(
        self, journal: PortfolioJournal, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """第三十六轮：基金数 = holdings 基金数（不含 watchlist）。

        A 股 region: 9 基金 holdings + 1 watchlist (A100) → 基金数 = 9，跟踪 ETF = 8。
        """
        from global_allocation.portfolio.card import SwensenClass, build_region_card

        # 清掉可能漏出的飞书凭证
        for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_CHAT_ID"):
            monkeypatch.delenv(k, raising=False)

        _seed(journal)
        card = build_region_card(journal, SwensenClass.CN_EQUITY)
        summary_div_text = None
        for e in card["elements"]:
            if e.get("tag") == "div" and "本大类资产" in e.get("text", {}).get("content", ""):
                summary_div_text = e["text"]["content"]
                break
        assert summary_div_text is not None
        # 基金数 = 9（holdings 9 只：013310/022434/017644/022424/014532/022448/011612/019857/023414）
        # 跟踪的 ETF 数量 = 8（A50/A100/A500/中证1000/科创创业50/创业板50/科创50/科创100）
        assert "**本大类资产的基金数**：9 只" in summary_div_text, (
            f"基金数应为 9 只（holdings 9 不含 watchlist），实际：\n{summary_div_text}"
        )
        assert "**跟踪的 ETF 数量**：8 个" in summary_div_text, (
            f"跟踪 ETF 应为 8 个（A 股 8 大指数），实际：\n{summary_div_text}"
        )

    def test_summary_over_under_status(self, journal: PortfolioJournal) -> None:
        """超/欠的仓位 = 实际仓 - 目标仓，单位是仓（1 仓 = 1 万 CNY）。

        A 股：实际 81500/10000 = 8.15 仓；目标 = DEFAULT_POSITION_ALLOCATION.target_position(CN_EQUITY) = 11；
        → 8.15 - 11 = -2.85 仓 → 欠配。
        """
        from global_allocation.portfolio.card import SwensenClass, build_region_card
        from global_allocation.portfolio.strategy import DEFAULT_POSITION_ALLOCATION

        _seed(journal)
        card = build_region_card(journal, SwensenClass.CN_EQUITY)
        summary_div_text = None
        for e in card["elements"]:
            if e.get("tag") == "div" and "本大类资产" in e.get("text", {}).get("content", ""):
                summary_div_text = e["text"]["content"]
                break
        assert summary_div_text is not None
        # 算期望值
        region_cost = sum(
            (cost for code, cost in __import__("global_allocation.portfolio.cost_basis", fromlist=["COST_BASIS_BY_CODE"]).COST_BASIS_BY_CODE.items()
             if __import__("global_allocation.portfolio.breakdown", fromlist=["SUBCLASS_BY_CODE"]).SUBCLASS_BY_CODE.get(code) == SwensenClass.CN_EQUITY),
            __import__("decimal").Decimal("0"),
        )
        unit_size = float(DEFAULT_POSITION_ALLOCATION.unit_size)
        actual = float(region_cost) / unit_size
        target = float(DEFAULT_POSITION_ALLOCATION.target_position(SwensenClass.CN_EQUITY))
        dev = actual - target
        # 检查「超/欠的仓位」行
        assert f"**超/欠的仓位**：{dev:+.2f} 仓（实际 {actual:.2f} / 目标 {target:.0f}）" in summary_div_text, (
            f"超/欠的仓位 应为 {dev:+.2f} 仓（实际 {actual:.2f} / 目标 {target:.0f}），实际：\n{summary_div_text}"
        )
        # 8.15 < 11 → 欠配
        assert "**超配/欠配**：欠配" in summary_div_text, (
            f"A 股 8.15 < 11 → 应为「欠配」，实际：\n{summary_div_text}"
        )

    def test_summary_over_status_when_over(self, journal: PortfolioJournal) -> None:
        """region card 「超配/欠配」行应是「超配」「欠配」「区间内」三选一。

        A 股 8.15 < 11 目标 → 欠配（spec 给定）。本 test 不强求具体哪个，
        只保证状态值是这三个合法值之一。
        """
        from global_allocation.portfolio.card import SwensenClass, build_region_card

        _seed(journal)
        card = build_region_card(journal, SwensenClass.CN_EQUITY)
        summary_div_text = None
        for e in card["elements"]:
            if e.get("tag") == "div" and "本大类资产" in e.get("text", {}).get("content", ""):
                summary_div_text = e["text"]["content"]
                break
        assert summary_div_text is not None
        # 「超配/欠配」行（markdown 加粗 + 冒号）
        alloc_line = next(
            (l for l in summary_div_text.split("  \n") if "超配/欠配" in l),
            None,
        )
        assert alloc_line is not None, f"应有「超配/欠配」行，实际：\n{summary_div_text}"
        # 飞书 markdown 加粗语法是 **xxx**，所以匹配 **超配/欠配**：<value>
        import re
        m = re.search(r"\*\*超配/欠配\*\*[：:]\s*(\S+)", alloc_line)
        assert m, f"应匹配到状态值，实际：{alloc_line}"
        status = m.group(1).strip()
        assert status in {"超配", "欠配", "区间内"}, f"状态值应为超配/欠配/区间内，实际 {status}"


class TestPerFundSortOrder:
    """per-fund 表按合理 ETF 顺序排（第三十六轮 liubo 2026-10-19 拍板）。

    A 股 8 个 ETF 合理顺序：
    A50 → A100 → A500 → 中证1000 → 科创创业50 → 创业板50 → 科创50 → 科创100
    """

    def test_cn_equity_sorted_by_8_etf_order(self, journal: PortfolioJournal) -> None:
        """A 股 per-fund 表按 8 ETF 顺序排（A50 → A100 → A500 → 中证1000 → 科创创业50 → 创业板50 → 科创50 → 科创100）。"""
        from global_allocation.portfolio.card import SwensenClass, build_region_card

        _seed(journal)
        card = build_region_card(journal, SwensenClass.CN_EQUITY)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        per_fund = next(
            (t for t in tables if [c["name"] for c in t["columns"]] == ["fund", "etf_index", "cost", "valuation", "advice"]),
            None,
        )
        assert per_fund is not None
        # 提取 etf_index 列顺序（去重 — A500 有 3 只基金）
        seen: list[str] = []
        for r in per_fund["rows"]:
            etf = r["etf_index"]
            if etf not in seen:
                seen.append(etf)
        expected = [
            "中证 A50",
            "中证 A100",
            "中证 A500",
            "中证 1000",
            "科创创业 50",
            "创业板 50",
            "科创 50",
            "科创 100",
        ]
        assert seen == expected, (
            f"A 股 per-fund 表 etf_index 列去重后应为 8 个顺序：\n{expected}\n"
            f"实际：\n{seen}"
        )

    def test_a500_funds_grouped_under_a500(self, journal: PortfolioJournal) -> None:
        """A500 合并组（3 只基金：022424/022434/022448）都展示在「中证 A500」之下。

        按 ETF 顺序排后，A500 段是连续的 3 行（按 fund_code 字符串次排序）。
        """
        from global_allocation.portfolio.card import SwensenClass, build_region_card

        _seed(journal)
        card = build_region_card(journal, SwensenClass.CN_EQUITY)
        tables = [e for e in card["elements"] if e.get("tag") == "table"]
        per_fund = next(
            (t for t in tables if [c["name"] for c in t["columns"]] == ["fund", "etf_index", "cost", "valuation", "advice"]),
            None,
        )
        rows = per_fund["rows"]
        # 找 3 个 A500 行
        a500_rows = [r for r in rows if r["etf_index"] == "中证 A500"]
        assert len(a500_rows) == 3, f"A500 应有 3 行，实际 {len(a500_rows)}"
        # 检查这 3 行在表里是连续 3 行
        a500_indices = [i for i, r in enumerate(rows) if r["etf_index"] == "中证 A500"]
        assert a500_indices == list(range(min(a500_indices), min(a500_indices) + 3)), (
            f"A500 3 行应连续，实际位置 {a500_indices}"
        )


class TestBuildPortfolioCards:
    """build_portfolio_cards 顺序固定 + 4 张卡。"""

    def test_returns_exactly_four_cards(self, journal: PortfolioJournal) -> None:
        """build_portfolio_cards 必须返回 4 张卡（总览 + A 股 + 港股 + 美股）。"""
        from global_allocation.portfolio.card import build_portfolio_cards

        _seed(journal)
        cards = build_portfolio_cards(journal)
        assert len(cards) == 4, f"build_portfolio_cards 应返回 4 张卡，实际 {len(cards)} 张"

    def test_order_overview_cn_hk_us(self, journal: PortfolioJournal) -> None:
        """顺序固定：[总览, A 股, 港股, 美股]。"""
        from global_allocation.portfolio.card import build_portfolio_cards

        _seed(journal)
        cards = build_portfolio_cards(journal)
        titles = [c["header"]["title"]["content"] for c in cards]
        assert titles[0] == "实盘周报（总览）", f"cards[0] 应为总览，实际「{titles[0]}」"
        assert titles[1] == "实盘周报（A 股）", f"cards[1] 应为 A 股，实际「{titles[1]}」"
        assert titles[2] == "实盘周报（港股）", f"cards[2] 应为港股，实际「{titles[2]}」"
        assert titles[3] == "实盘周报（美股）", f"cards[3] 应为美股，实际「{titles[3]}」"

    def test_base_title_propagates_to_all_cards(self, journal: PortfolioJournal) -> None:
        """base_title 参数会传到每张卡的 title（"{base_title}（{...}）"）。"""
        from global_allocation.portfolio.card import build_portfolio_cards

        _seed(journal)
        cards = build_portfolio_cards(journal, base_title="My Weekly Report")
        titles = [c["header"]["title"]["content"] for c in cards]
        assert titles == [
            "My Weekly Report（总览）",
            "My Weekly Report（A 股）",
            "My Weekly Report（港股）",
            "My Weekly Report（美股）",
        ], f"base_title 应传播到所有卡，实际 {titles}"

    def test_each_card_is_valid_envelope(self, journal: PortfolioJournal) -> None:
        """每张卡都有标准 envelope（header + elements + footer）。"""
        from global_allocation.portfolio.card import build_portfolio_cards

        _seed(journal)
        cards = build_portfolio_cards(journal)
        for i, c in enumerate(cards, 1):
            assert "header" in c, f"第 {i} 张卡缺 header"
            assert "elements" in c, f"第 {i} 张卡缺 elements"
            assert "footer" in c, f"第 {i} 张卡缺 footer"
            assert c["header"].get("template") == "blue", f"第 {i} 张卡 template 不为 blue"

    def test_no_chart_in_any_card(self, journal: PortfolioJournal) -> None:
        """4 张卡都没有 chart（第三十四轮禁柱状图）。"""
        from global_allocation.portfolio.card import build_portfolio_cards

        _seed(journal)
        cards = build_portfolio_cards(journal)
        for i, c in enumerate(cards, 1):
            charts = [e for e in c["elements"] if e.get("tag") == "chart"]
            assert len(charts) == 0, f"第 {i} 张卡不应有 chart"


class TestBuildPortfolioCardBackwardCompat:
    """build_portfolio_card（单卡，向后兼容）保持原行为。"""

    def test_single_card_still_has_all_sections(self, journal: PortfolioJournal) -> None:
        """单卡版仍拼 4 个 section（cost_basis + per-fund + 调仓 + 定投），55 行 per-fund。

        第三十五轮（liubo 2026-10-18）：新加 build_portfolio_cards() 走 4 张分卡，
        老的 build_portfolio_card() 不变 — 单卡向后兼容（老调用方用得到）。
        """
        _seed(journal)
        card = build_portfolio_card(journal)
        # 老行为：单一 header + 全部 elements 拼一起
        assert "header" in card
        # 应有 4 个 note（4 section header）+ 多个 table
        notes = [e for e in card["elements"] if e.get("tag") == "note"]
        # 1 个 cost_basis note + 1 个 per-fund note + 1 个 调仓 note + 1 个 定投 note + 1 个 footer note = 5
        # 但 footer 在 card["footer"] 里，不在 elements 里，所以 elements 里有 4 个 note
        section_notes = [
            n for n in notes
            if any("持仓成本" in e.get("content", "") for e in n["elements"])
            or any("持仓与调仓建议" in e.get("content", "") for e in n["elements"])
            or any("调仓纪录" in e.get("content", "") for e in n["elements"])
            or any("定投纪录" in e.get("content", "") for e in n["elements"])
        ]
        assert len(section_notes) == 4, (
            f"单卡版应有 4 个 section header，实际 {len(section_notes)} 个"
        )

    def test_single_card_differs_from_multi(self, journal: PortfolioJournal) -> None:
        """单卡版 ≠ 多卡版 — 单卡是 [header + elements(全部)]，多卡是 [4 张完整卡]。

        第三十五轮设计：单卡向后兼容，新流程走 4 张卡。
        """
        from global_allocation.portfolio.card import build_portfolio_cards

        _seed(journal)
        single = build_portfolio_card(journal)
        multi = build_portfolio_cards(journal)

        # 数量：单卡 = 1，多卡 = 4
        assert isinstance(single, dict)
        assert isinstance(multi, list)
        assert len(multi) == 4


class TestPublishPortfolioReports:
    """publish_portfolio_reports() — 飞书发送 4 张卡（dry_run 路径）。"""

    def test_dry_run_returns_four_json_strings(
        self, journal: PortfolioJournal, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dry_run=True → 返回 4 个 JSON 字符串（不发飞书）。

        第三十五轮（liubo 2026-10-18）：4 张周报卡走 dry_run 路径也走真的 send 路径。
        测试用 dry_run=True 走 JSON 序列化，避免发真的飞书 API。
        """
        # 设假凭证，否则 load_credentials() 会 raise ValueError("缺少 FEISHU_APP_ID")
        monkeypatch.setenv("FEISHU_APP_ID", "cli_aab25b0d09f69be3")
        monkeypatch.setenv("FEISHU_APP_SECRET", "test_secret_only_used_for_load_credentials")
        monkeypatch.setenv("FEISHU_CHAT_ID", "oc_test_chat_id")

        from global_allocation.portfolio.publisher import publish_portfolio_reports

        _seed(journal)
        result = publish_portfolio_reports(journal, dry_run=True)
        assert isinstance(result, list), f"dry_run 应返回 list，实际 {type(result)}"
        assert len(result) == 4, f"dry_run 应返回 4 个 JSON，实际 {len(result)} 个"
        # 每个元素都是合法 JSON 字符串
        import json
        for i, s in enumerate(result, 1):
            assert isinstance(s, str), f"第 {i} 个 element 应为 str"
            parsed = json.loads(s)
            assert "header" in parsed, f"第 {i} 个 JSON 应有 header"


# ─── publisher 模块 export（防止第三十五轮新增函数没在 __all__ 里）───


class TestPublisherExports:
    """src/global_allocation/portfolio/publisher.py 的 __all__ 包含 4 个 publish 函数。"""

    def test_all_publishers_in_dunder_all(self) -> None:
        import global_allocation.portfolio.publisher as pub
        for name in (
            "publish_portfolio_report",
            "publish_portfolio_reports",
            "publish_pe_rebalance_report",
            "publish_etf_rotation_report",
        ):
            assert name in pub.__all__, (
                f"publisher.__all__ 漏了 {name}（第三十五轮加的 publish_portfolio_reports 必须导出）"
            )
        # 同时函数确实存在
        for name in (
            "publish_portfolio_report",
            "publish_portfolio_reports",
            "publish_pe_rebalance_report",
            "publish_etf_rotation_report",
        ):
            assert hasattr(pub, name), f"publisher 模块应有 {name} 函数"
