"""测试 src/global_allocation/portfolio/pe_rebalance.py。

参照 spec 022 (PE-TTM 周调仓策略，liubo 2026-09-24 拍板 + 二次细化)。

规则（liubo 2026-09-24 拍板）：
- 仓位 = 0 + 分位 < 50% → BUILD +1
- 仓位 = 0 + 分位 >= 50% → HOLD
- 仓位 in (0, 1) + 分位 < 50% → ADD +1（欠配 + 便宜 → 凑 1 仓）
- 仓位 in (0, 1) + 50% <= 分位 <= 80% → HOLD
- 仓位 in (0, 1) + 分位 > 80% → REDUCE -1
- 仓位 >= 1 + 分位 < 20% → ADD +1（深价值例外）
- 仓位 >= 1 + 20% <= 分位 <= 80% → HOLD
- 仓位 >= 1 + 分位 > 80% → REDUCE -1
- 数据缺失 → SKIP

估值指标（liubo 2026-09-24 扩展 + 2026-10-12 拍板 REITs 改 DY）：
- 股票类：PE-TTM
- US REIT：DY（yfinance VNQ 代理 MSCI US REIT 算 DY-TTM + 月度 DY-TTM 5Y 分位 → 估值分位）
- 中证 REITs：DY（akshare 932006 拿当前 DY + yfinance 508000.SS 算月度 DY-TTM 5Y 分位 → 估值分位）

spec REITs-DY（liubo 2026-10-12）之前：US REIT 用 P/FFO、中证 REITs 用 P/NAV。
- 枚举值 P_FFO / P_NAV 仍保留以兼容旧代码 / 旧数据
- 但 build_evaluations 现在走 LIVE_REIT_SNAPSHOT_BY_INDEX（不是 PE_SNAPSHOT_BY_INDEX）
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from global_allocation.portfolio.breakdown import SwensenClass
from global_allocation.portfolio.pe_rebalance import (
    FUND_INDEX_MAP,
    INDEX_DISPLAY_NAME,
    INDEX_WATCHLIST,
    LIVE_REIT_SNAPSHOT_BY_INDEX,
    PESignal,
    PE_SNAPSHOT_BY_INDEX,
    RebalanceAction,
    FundPEvaluation,
    ValuationMetric,
    build_evaluations,
    build_watchlist_evaluations,
    evaluate_fund,
    format_weekly_report,
    get_current_position,
    merge_by_etf,
    weekly_rebalance_plan,
)


# ─── autouse fixture：注入测试用 LIVE_REIT_SNAPSHOT_BY_INDEX（spec REITs-DY）───
#
# 模块加载时 load_live_reit_snapshot() 可能空（无 JSON）；测试需要 REITs 有数据才能验完整链路。
# 用 autouse 让所有 test 默认注入，个别 test 需要"空"时显式 monkeypatch clear()。


_TEST_REIT_DY_DATA = {
    ".MSCI_US_REIT": (
        ValuationMetric.DY,
        Decimal("3.76"),   # 3.76% DY-TTM（VNQ 代理）
        Decimal("0.42"),   # 5年估值分位 42%（低 = 便宜；原 DY 分位 58% 翻转）
        date(2026, 10, 9),
    ),
    "932006": (
        ValuationMetric.DY,
        Decimal("6.59"),   # 6.59% DY（中证 REITs 2026-09-16 最新）
        Decimal("0.48"),   # 5年估值分位 48%（原 DY 分位 52% 翻转）
        date(2026, 9, 16),
    ),
}


@pytest.fixture(autouse=True)
def _inject_reit_snapshot() -> None:
    """所有 test 默认注入测试用 REITs DY 数据。

    需要测"无 snapshot"路径的 test 显式调用
    `pr_mod.LIVE_REIT_SNAPSHOT_BY_INDEX.clear()` 即可。
    """
    from global_allocation.portfolio import pe_rebalance as pr_mod
    pr_mod.LIVE_REIT_SNAPSHOT_BY_INDEX.update(_TEST_REIT_DY_DATA)
    yield  # 让 test 跑
    # 清理（防止 test 顺序影响）
    pr_mod.LIVE_REIT_SNAPSHOT_BY_INDEX.clear()


def _make_eval(
    fund_code: str = "TEST",
    fund_name: str = "测试基金",
    subclass: SwensenClass = SwensenClass.CN_EQUITY,
    etf_index_code: str = "000510",
    etf_index_name: str = "中证 A500",
    metric: ValuationMetric = ValuationMetric.PE_TTM,
    metric_value: Decimal | None = Decimal("15.0"),
    metric_percentile: Decimal | None = Decimal("0.50"),
    current_position: Decimal = Decimal("1"),
) -> FundPEvaluation:
    """工厂函数：构造一个 FundPEvaluation。"""
    return FundPEvaluation(
        fund_code=fund_code,
        fund_name=fund_name,
        subclass=subclass,
        etf_index_code=etf_index_code,
        etf_index_name=etf_index_name,
        metric=metric,
        metric_value=metric_value,
        metric_percentile=metric_percentile,
        current_position=current_position,
    )


# ─── evaluate_fund 规则测试 ─────────────────────────────


class TestEvaluateFundZeroPosition:
    """仓位 = 0 时的规则。"""

    def test_zero_position_under_50_pct_builds(self) -> None:
        """空仓 + 分位 < 50% → BUILD +1。"""
        eval_ = _make_eval(metric_percentile=Decimal("0.30"), current_position=Decimal("0"))
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.BUILD
        assert action.change == Decimal("1")
        assert "建仓" in action.reason

    def test_zero_position_exactly_50_pct_holds(self) -> None:
        """空仓 + 分位 = 50% → HOLD（边界值用 >= 50% 规则）。"""
        eval_ = _make_eval(metric_percentile=Decimal("0.50"), current_position=Decimal("0"))
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.HOLD
        assert action.change == Decimal("0")

    def test_zero_position_above_50_pct_holds(self) -> None:
        """空仓 + 分位 > 50% → HOLD（等便宜再买）。"""
        eval_ = _make_eval(metric_percentile=Decimal("0.70"), current_position=Decimal("0"))
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.HOLD
        assert action.change == Decimal("0")

    def test_zero_position_above_80_pct_still_holds(self) -> None:
        """空仓 + 分位 > 80% → HOLD（无仓位不触发减仓，等便宜）。"""
        eval_ = _make_eval(metric_percentile=Decimal("0.90"), current_position=Decimal("0"))
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.HOLD
        assert action.change == Decimal("0")


class TestEvaluateFundHasPosition:
    """仓位 > 0 时的规则。

    liubo 2026-09-24 二次细化：
    - 仓位 in (0, 1)「欠配」：分位 < 50% 一律 ADD（凑 1 仓）；50-80% HOLD；> 80% REDUCE
    - 仓位 >= 1「已配足」：分位 < 20% ADD（深价值例外）；20-80% HOLD；> 80% REDUCE
    """

    def test_underweight_under_50_pct_adds(self) -> None:
        """仓位 in (0, 1) + 分位 < 50% → ADD +1（欠配 + 便宜 → 凑 1 仓）。"""
        eval_ = _make_eval(metric_percentile=Decimal("0.34"), current_position=Decimal("0.5"))
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.ADD
        assert action.change == Decimal("1")
        assert "加 1 仓凑目标" in action.reason

    def test_underweight_exactly_50_pct_holds(self) -> None:
        """仓位 in (0, 1) + 分位 = 50% → HOLD（边界 < 50% 才加）。"""
        eval_ = _make_eval(metric_percentile=Decimal("0.50"), current_position=Decimal("0.5"))
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.HOLD
        assert action.change == Decimal("0")

    def test_underweight_in_middle_holds(self) -> None:
        """仓位 in (0, 1) + 分位 70% → HOLD（50-80% 区间内不动）。"""
        eval_ = _make_eval(metric_percentile=Decimal("0.70"), current_position=Decimal("0.5"))
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.HOLD

    def test_underweight_above_80_pct_reduces(self) -> None:
        """仓位 in (0, 1) + 分位 > 80% → REDUCE -1。"""
        eval_ = _make_eval(metric_percentile=Decimal("0.85"), current_position=Decimal("0.5"))
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.REDUCE
        assert action.change == Decimal("-1")

    def test_full_position_under_20_pct_adds_deep_value(self) -> None:
        """仓位 >= 1 + 分位 < 20% → ADD +1（深价值例外）。"""
        eval_ = _make_eval(metric_percentile=Decimal("0.15"), current_position=Decimal("2"))
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.ADD
        assert action.change == Decimal("1")
        assert "深价值" in action.reason

    def test_full_position_under_50_pct_holds(self) -> None:
        """仓位 >= 1 + 分位 < 50% 但 >= 20% → HOLD（已配足，不深价值不再加）。"""
        eval_ = _make_eval(metric_percentile=Decimal("0.35"), current_position=Decimal("2"))
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.HOLD
        assert action.change == Decimal("0")

    def test_full_position_exactly_20_pct_holds(self) -> None:
        """仓位 >= 1 + 分位 = 20% → HOLD（边界 < 20% 才加）。"""
        eval_ = _make_eval(metric_percentile=Decimal("0.20"), current_position=Decimal("2"))
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.HOLD
        assert action.change == Decimal("0")

    def test_position_in_middle_holds(self) -> None:
        """仓位 >= 1 + 分位 50% → HOLD。"""
        eval_ = _make_eval(metric_percentile=Decimal("0.50"), current_position=Decimal("1"))
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.HOLD
        assert action.change == Decimal("0")

    def test_full_position_exactly_80_pct_holds(self) -> None:
        """仓位 >= 1 + 分位 = 80% → HOLD（边界 > 80% 才减）。"""
        eval_ = _make_eval(metric_percentile=Decimal("0.80"), current_position=Decimal("2"))
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.HOLD
        assert action.change == Decimal("0")

    def test_full_position_above_80_pct_reduces(self) -> None:
        """仓位 >= 1 + 分位 > 80% → REDUCE -1。"""
        eval_ = _make_eval(metric_percentile=Decimal("0.85"), current_position=Decimal("2"))
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.REDUCE
        assert action.change == Decimal("-1")
        assert "减仓" in action.reason


class TestEvaluateFundMissingData:
    """数据缺失时的处理。"""

    def test_metric_percentile_missing_skips(self) -> None:
        """分位缺失 → SKIP。"""
        eval_ = _make_eval(metric_value=Decimal("15.0"), metric_percentile=None)
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.SKIP
        assert action.change == Decimal("0")
        assert "缺失" in action.reason

    def test_metric_value_and_percentile_missing_skips(self) -> None:
        """估值倍数 + 分位都缺失 → SKIP。"""
        eval_ = _make_eval(metric_value=None, metric_percentile=None)
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.SKIP

    def test_action_preserves_input_metadata(self) -> None:
        """SKIP 时仍保留 fund_code / subclass / position 等元数据。"""
        eval_ = _make_eval(
            fund_code="999999",
            fund_name="缺失基金",
            subclass=SwensenClass.US_EQUITY,
            metric_percentile=None,
            current_position=Decimal("0.5"),
        )
        action = evaluate_fund(eval_)
        assert action.fund_code == "999999"
        assert action.subclass == SwensenClass.US_EQUITY
        assert action.current_position == Decimal("0.5")


class TestEvaluateFundMultiMetric:
    """多指标（REITs 用 P/FFO / P/NAV）的评估。"""

    def test_us_reit_p_ffo_uses_correct_metric_in_reason(self) -> None:
        """US REIT 用 P/FFO → reason 里应该出现 "P/FFO"。"""
        eval_ = _make_eval(
            subclass=SwensenClass.US_REIT,
            etf_index_code=".MSCI_US_REIT",
            metric=ValuationMetric.P_FFO,
            metric_value=Decimal("19.70"),
            metric_percentile=Decimal("0.60"),
            current_position=Decimal("0.5"),
        )
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.HOLD
        assert "P/FFO" in action.reason
        assert action.metric == ValuationMetric.P_FFO

    def test_cn_reit_p_nav_uses_correct_metric_in_reason(self) -> None:
        """中证 REITs 用 P/NAV → reason 里应该出现 "P/NAV"。

        liubo 2026-09-24 二次细化：仓位 0.5 < 1 + 分位 34% < 50% → ADD（凑 1 仓）。

        注：932006 上次调仓 2026-09-24，月度冷却期要到 2026-10-24 才能再调。
        传 today=2026-10-25 跳过冷却期。
        """
        eval_ = _make_eval(
            subclass=SwensenClass.CN_REIT,
            etf_index_code="932006",
            metric=ValuationMetric.P_NAV,
            metric_value=Decimal("1.03"),
            metric_percentile=Decimal("0.34"),
            current_position=Decimal("0.5"),
        )
        action = evaluate_fund(eval_, today=date(2026, 10, 25))
        assert action.signal == PESignal.ADD
        assert action.change == Decimal("1")
        assert "P/NAV" in action.reason
        assert action.metric == ValuationMetric.P_NAV

    def test_watchlist_zero_position_can_build(self) -> None:
        """watchlist（0 仓位）分位 < 50% → BUILD（提醒建仓）。"""
        eval_ = _make_eval(
            fund_code="(HSI)",
            fund_name="恒生指数（无持仓）",
            subclass=SwensenClass.HK_EQUITY,
            etf_index_code="HSI",
            metric=ValuationMetric.PE_TTM,
            metric_value=Decimal("11.30"),
            metric_percentile=Decimal("0.40"),  # < 50% → BUILD
            current_position=Decimal("0"),
        )
        action = evaluate_fund(eval_)
        assert action.signal == PESignal.BUILD
        assert action.change == Decimal("1")
        assert action.fund_code == "(HSI)"


# ─── evaluate_fund 不变量 ─────────────────────────────


class TestEvaluateFundInvariants:
    """信号和 change 的一致性不变量。"""

    @pytest.mark.parametrize("pct,pos", [
        (Decimal("0.05"), Decimal("1")),  # ADD
        (Decimal("0.85"), Decimal("1")),  # REDUCE
        (Decimal("0.30"), Decimal("0")),  # BUILD
        (Decimal("0.50"), Decimal("0")),  # HOLD
        (Decimal("0.50"), Decimal("2")),  # HOLD
        (None, Decimal("1")),             # SKIP
    ])
    def test_change_matches_signal(self, pct: Decimal | None, pos: Decimal) -> None:
        eval_ = _make_eval(metric_percentile=pct, current_position=pos)
        action = evaluate_fund(eval_)
        if action.signal in (PESignal.ADD, PESignal.BUILD):
            assert action.change == Decimal("1")
        elif action.signal == PESignal.REDUCE:
            assert action.change == Decimal("-1")
        else:  # HOLD / SKIP
            assert action.change == Decimal("0")


# ─── 月度冷却期（liubo 2026-09-25 拍板）──────────────────


class TestCooldown:
    """月度冷却期：同 ETF 距上次调仓 < 30 天 → ADD/REDUCE/BUILD 转 HOLD。"""

    def test_add_within_cooldown_becomes_hold(self) -> None:
        """冷却期内 ADD → 转 HOLD（change = 0，reason 提示冷却期）。"""
        from global_allocation.portfolio.pe_rebalance import (
            LAST_REBALANCE_BY_ETF,
            _evaluate_signal,
            _apply_cooldown,
        )

        eval_ = _make_eval(
            etf_index_code="930050",  # 上次调仓 2026-09-24
            metric_percentile=Decimal("0.10"),  # 触发 ADD
            current_position=Decimal("1"),
        )
        action = _evaluate_signal(eval_)
        assert action.signal == PESignal.ADD  # raw 信号是 ADD

        # 1 天后：冷却期
        cooled = _apply_cooldown(action, today=date(2026, 9, 25))
        assert cooled.signal == PESignal.HOLD
        assert cooled.change == Decimal("0")
        assert "冷却期" in cooled.reason
        assert "1 天" in cooled.reason  # (2026-09-25 - 2026-09-24).days = 1

    def test_add_after_cooldown_passes_through(self) -> None:
        """冷却期满（≥ 30 天）后 ADD 正常通过。"""
        from global_allocation.portfolio.pe_rebalance import (
            _apply_cooldown,
            _evaluate_signal,
        )

        eval_ = _make_eval(
            etf_index_code="930050",
            metric_percentile=Decimal("0.10"),
            current_position=Decimal("1"),
        )
        action = _evaluate_signal(eval_)

        # 31 天后：冷却期已过
        cooled = _apply_cooldown(action, today=date(2026, 10, 25))
        assert cooled.signal == PESignal.ADD  # 不变

    def test_reduce_within_cooldown_becomes_hold(self) -> None:
        """冷却期内 REDUCE → 转 HOLD。"""
        from global_allocation.portfolio.pe_rebalance import (
            _apply_cooldown,
            _evaluate_signal,
        )

        eval_ = _make_eval(
            etf_index_code="932006",  # 上次调仓 2026-09-24
            metric_percentile=Decimal("0.85"),  # 触发 REDUCE
            current_position=Decimal("1"),
        )
        action = _evaluate_signal(eval_)
        assert action.signal == PESignal.REDUCE

        cooled = _apply_cooldown(action, today=date(2026, 9, 25))
        assert cooled.signal == PESignal.HOLD
        assert "REDUCE" in cooled.reason

    def test_hold_passes_through_cooldown(self) -> None:
        """HOLD 信号不应用冷却期（本来就不动）。"""
        from global_allocation.portfolio.pe_rebalance import (
            _apply_cooldown,
            _evaluate_signal,
        )

        eval_ = _make_eval(
            etf_index_code="930050",
            metric_percentile=Decimal("0.50"),  # 触发 HOLD
            current_position=Decimal("1"),
        )
        action = _evaluate_signal(eval_)
        assert action.signal == PESignal.HOLD

        cooled = _apply_cooldown(action, today=date(2026, 9, 25))
        assert cooled.signal == PESignal.HOLD
        assert "冷却期" not in cooled.reason

    def test_unknown_etf_passes_through_cooldown(self) -> None:
        """LAST_REBALANCE_BY_ETF 没记录的 ETF（首次调仓）不应用冷却期。"""
        from global_allocation.portfolio.pe_rebalance import (
            _apply_cooldown,
            _evaluate_signal,
        )

        eval_ = _make_eval(
            etf_index_code="000510",  # 不在 LAST_REBALANCE_BY_ETF 里
            metric_percentile=Decimal("0.10"),  # < 20% 触发 ADD（深价值例外）
            current_position=Decimal("1"),
        )
        action = _evaluate_signal(eval_)
        assert action.signal == PESignal.ADD

        cooled = _apply_cooldown(action, today=date(2026, 9, 25))
        assert cooled.signal == PESignal.ADD  # 不变

    def test_2026_10_09_built_etfs_in_cooldown(self) -> None:
        """2026-10-09 新建仓的 000698 科创100 / 399673 创业板50 在 30 天冷却期内。

        liubo 2026-10-09 拍板换 020291→019857（科创100，建仓 1 仓）+ 160422→023414（创业板50，
        建仓 1 仓），都应该在 2026-11-08 之前冻结 ADD。修复 2026-10-12 用户报漏更新
        LAST_REBALANCE_BY_ETF 的 bug — 没录入的话本周报虽然不显示 ADD（因为分位 20.44%/46.27%
        都在 HOLD 范围），但下次分位跌破 20% 就会绕过冷却期错误触发。
        """
        from global_allocation.portfolio.pe_rebalance import (
            _apply_cooldown,
            _evaluate_signal,
        )

        for etf_code, etf_name in [("000698", "科创 100"), ("399673", "创业板 50")]:
            eval_ = _make_eval(
                etf_index_code=etf_code,
                metric=ValuationMetric.PS_TTM,
                metric_percentile=Decimal("0.10"),  # 模拟跌破 20% 深价值
                current_position=Decimal("1"),       # 已配足 1 仓
            )
            action = _evaluate_signal(eval_)
            assert action.signal == PESignal.ADD, f"{etf_name} 应该触发 ADD"

            # 2026-10-12 距 2026-10-09 = 3 天 < 30天 → 转 HOLD（冷却期冻结）
            cooled = _apply_cooldown(action, today=date(2026, 10, 12))
            assert cooled.signal == PESignal.HOLD, f"{etf_name} 在冷却期内应被冻结为 HOLD"
            assert "冷却期" in cooled.reason

            # 2026-11-09 距 2026-10-09 = 31 天 > 30天 → 解冻，ADD 正常通过
            cooled_pass = _apply_cooldown(action, today=date(2026, 11, 9))
            assert cooled_pass.signal == PESignal.ADD, f"{etf_name} 冷却期已过应通过"

    def test_last_rebalance_dict_has_initial_entries(self) -> None:
        """LAST_REBALANCE_BY_ETF 初始含 2026-09-24 两次调仓（A50 + 中证 REITs）+ 2026-10-09 新建仓 2 个（科创100/创业板50）。"""
        from global_allocation.portfolio.pe_rebalance import LAST_REBALANCE_BY_ETF

        assert LAST_REBALANCE_BY_ETF["930050"] == date(2026, 9, 24)
        assert LAST_REBALANCE_BY_ETF["932006"] == date(2026, 9, 24)
        assert LAST_REBALANCE_BY_ETF["000698"] == date(2026, 10, 9)
        assert LAST_REBALANCE_BY_ETF["399673"] == date(2026, 10, 9)

    def test_cooldown_days_constant_is_30(self) -> None:
        """COOLDOWN_DAYS = 30（月度调仓）。"""
        from global_allocation.portfolio.pe_rebalance import COOLDOWN_DAYS

        assert COOLDOWN_DAYS == 30


# ─── 持有期约束（liubo 2026-10-14 拍板）────────────────────


class TestHoldingPeriodConstraint:
    """REDUCE 必须等赎回费归零（最早购买日期 + 阈值天数 ≤ today）才能执行。

    数据来源：akshare 天天基金 fund_fee_em（2026-10-14 拉）。
    阈值 = max(REDEMPTION_FEE_DAYS_BY_CODE, HOLDING_PERIOD_DAYS_BY_CODE)
    PURCHASE_DATES_BY_CODE 缺失 = 未知 = 约束不生效（保留原 REDUCE）。
    """

    def test_reduce_within_holding_period_becomes_hold(self) -> None:
        """持有期内 REDUCE → 转 HOLD。"""
        from global_allocation.portfolio.pe_rebalance import (
            PURCHASE_DATES_BY_CODE,
            _check_holding_period,
        )
        from datetime import date as _date

        # 000216 黄金赎回费 365 天归零，假设今天 - 1 年 = 2025-10-14 还差 1 天
        PURCHASE_DATES_BY_CODE["000216"] = _date(2025, 10, 15)
        try:
            eval_ = _make_eval(
                fund_code="000216",
                etf_index_code="GOLD",
                metric=ValuationMetric.GOLD_HISTORICAL_PCT,
                metric_percentile=Decimal("0.90"),  # > 80% 触发 REDUCE
                current_position=Decimal("1"),
            )
            # 走 _evaluate_signal 跳过 composite_score（黄金走综合分路径不直接 REDUCE）
            # 改用普通 PE 路径：518981 长信标普100 赎回费 5 天归零
            PURCHASE_DATES_BY_CODE["519981"] = _date(2026, 10, 12)  # 3 天前
            eval2 = _make_eval(
                fund_code="519981",
                etf_index_code=".OEX",
                metric_percentile=Decimal("0.90"),
                current_position=Decimal("1"),
            )
            from global_allocation.portfolio.pe_rebalance import _evaluate_signal
            action = _evaluate_signal(eval2)
            assert action.signal == PESignal.REDUCE

            # 持有期 5 天，购买 3 天前 → 距今 3 < 5 → 冻结
            cooled = _check_holding_period(action, today=_date(2026, 10, 15))
            assert cooled.signal == PESignal.HOLD
            assert "持有期约束" in cooled.reason
        finally:
            PURCHASE_DATES_BY_CODE.pop("000216", None)
            PURCHASE_DATES_BY_CODE.pop("519981", None)

    def test_reduce_after_holding_period_passes(self) -> None:
        """持有期已过 → REDUCE 正常通过。"""
        from global_allocation.portfolio.pe_rebalance import (
            PURCHASE_DATES_BY_CODE,
            _check_holding_period,
            _evaluate_signal,
        )
        from datetime import date as _date

        PURCHASE_DATES_BY_CODE["519981"] = _date(2026, 10, 1)  # 14 天前
        try:
            eval_ = _make_eval(
                fund_code="519981",
                etf_index_code=".OEX",
                metric_percentile=Decimal("0.90"),
                current_position=Decimal("1"),
            )
            action = _evaluate_signal(eval_)
            assert action.signal == PESignal.REDUCE

            # 14 天 > 5 天阈值 → 通过
            passed = _check_holding_period(action, today=_date(2026, 10, 15))
            assert passed.signal == PESignal.REDUCE
        finally:
            PURCHASE_DATES_BY_CODE.pop("519981", None)

    def test_reduce_without_purchase_date_passes_through(self) -> None:
        """PURCHASE_DATES_BY_CODE 缺失这只基金 → 约束不生效，保留原 REDUCE。"""
        from global_allocation.portfolio.pe_rebalance import (
            PURCHASE_DATES_BY_CODE,
            _check_holding_period,
            _evaluate_signal,
        )
        from datetime import date as _date

        # 确保 519981 不在 dict 里
        PURCHASE_DATES_BY_CODE.pop("519981", None)
        eval_ = _make_eval(
            fund_code="519981",
            etf_index_code=".OEX",
            metric_percentile=Decimal("0.90"),
            current_position=Decimal("1"),
        )
        action = _evaluate_signal(eval_)
        assert action.signal == PESignal.REDUCE

        passed = _check_holding_period(action, today=_date(2026, 10, 15))
        assert passed.signal == PESignal.REDUCE  # 不变

    def test_holding_period_fund_blocks_reduce(self) -> None:
        """持有期基金（010942 招商瑞乐 180 天）锁定期内 REDUCE 冻结。"""
        from global_allocation.portfolio.pe_rebalance import (
            PURCHASE_DATES_BY_CODE,
            _check_holding_period,
            _evaluate_signal,
        )
        from datetime import date as _date

        # 010942 持有期 180 天，购买 30 天前 → 距今 30 < 180
        PURCHASE_DATES_BY_CODE["010942"] = _date(2026, 9, 14)
        try:
            eval_ = _make_eval(
                fund_code="010942",
                etf_index_code="中证全债",  # 占位
                metric_percentile=Decimal("0.90"),
                current_position=Decimal("1"),
            )
            action = _evaluate_signal(eval_)
            assert action.signal == PESignal.REDUCE

            cooled = _check_holding_period(action, today=_date(2026, 10, 14))
            assert cooled.signal == PESignal.HOLD
            assert "180 天" in cooled.reason
        finally:
            PURCHASE_DATES_BY_CODE.pop("010942", None)

    def test_merged_etf_all_codes_must_pass(self) -> None:
        """合并 ETF（fund_code 含 "+"）→ 任一子基金没过都 HOLD。"""
        from global_allocation.portfolio.pe_rebalance import (
            PURCHASE_DATES_BY_CODE,
            _check_holding_period,
        )
        from datetime import date as _date

        # 中证 A500 合并 3 只：022434 7 天 / 022424 7 天 / 022448 7 天
        # 022434 持有 30 天（解锁），022448 才持有 3 天（冻结）
        PURCHASE_DATES_BY_CODE["022434"] = _date(2026, 9, 1)
        PURCHASE_DATES_BY_CODE["022424"] = _date(2026, 9, 1)
        PURCHASE_DATES_BY_CODE["022448"] = _date(2026, 10, 1)  # 13 天前
        try:
            action = _make_eval(
                fund_code="022434+022424+022448",
                etf_index_code="000510",
                metric_percentile=Decimal("0.90"),
                current_position=Decimal("2.65"),
            ).__class__(  # 借用 FundPEvaluation，再转 RebalanceAction
                **{},
            ) if False else None

            # 构造一个 REDUCE RebalanceAction
            from global_allocation.portfolio.pe_rebalance import RebalanceAction
            action = RebalanceAction(
                fund_code="022434+022424+022448",
                fund_name="中证A500 合并",
                subclass=SwensenClass.CN_EQUITY,
                etf_index_code="000510",
                etf_index_name="中证A500",
                metric=ValuationMetric.PE_TTM,
                signal=PESignal.REDUCE,
                change=Decimal("-1"),
                reason="test",
                metric_value=Decimal("15.42"),
                metric_percentile=Decimal("0.90"),
                current_position=Decimal("2.65"),
            )

            # 022448 持有 13 天，阈值 7 天 → 通过
            # 实际上都通过，所有子基金都满足
            passed = _check_holding_period(action, today=_date(2026, 10, 14))
            assert passed.signal == PESignal.REDUCE

            # 现在让 022448 不满足：改为 3 天前
            PURCHASE_DATES_BY_CODE["022448"] = _date(2026, 10, 11)  # 3 天前 < 7 天
            held = _check_holding_period(action, today=_date(2026, 10, 14))
            assert held.signal == PESignal.HOLD
            assert "022448" in held.reason
        finally:
            PURCHASE_DATES_BY_CODE.pop("022434", None)
            PURCHASE_DATES_BY_CODE.pop("022424", None)
            PURCHASE_DATES_BY_CODE.pop("022448", None)

    def test_non_reduce_signals_skip_holding_period_check(self) -> None:
        """ADD / BUILD / HOLD / SKIP 不走持有期检查（不是卖出）。"""
        from global_allocation.portfolio.pe_rebalance import (
            PURCHASE_DATES_BY_CODE,
            _check_holding_period,
        )
        from datetime import date as _date

        PURCHASE_DATES_BY_CODE["000216"] = _date(2026, 10, 13)  # 1 天前
        try:
            # ADD 信号（深价值，1 仓 + < 20%）
            from global_allocation.portfolio.pe_rebalance import RebalanceAction
            add_action = RebalanceAction(
                fund_code="000216",
                fund_name="华安黄金",
                subclass=SwensenClass.COMMODITY,
                etf_index_code="GOLD",
                etf_index_name="黄金",
                metric=ValuationMetric.GOLD_HISTORICAL_PCT,
                signal=PESignal.ADD,
                change=Decimal("1"),
                reason="深价值加仓",
                metric_value=Decimal("907.50"),
                metric_percentile=Decimal("0.10"),
                current_position=Decimal("1"),
            )
            result = _check_holding_period(add_action, today=_date(2026, 10, 14))
            assert result.signal == PESignal.ADD  # 不变，ADD 不查持有期
        finally:
            PURCHASE_DATES_BY_CODE.pop("000216", None)

    def test_threshold_uses_max_of_redemption_and_holding(self) -> None:
        """_holding_period_threshold_days 取赎回费归零天数和持有期天数的最大值。"""
        from global_allocation.portfolio.pe_rebalance import _holding_period_threshold_days

        # 510742 持有期 365 天 + 赎回费 0% → 阈值 365
        assert _holding_period_threshold_days("010742") == 365
        # 010942 持有期 180 天 → 阈值 180
        assert _holding_period_threshold_days("010942") == 180
        # 000216 赎回费 365 天 → 阈值 365
        assert _holding_period_threshold_days("000216") == 365
        # 017641 赎回费 730 天 → 阈值 730
        assert _holding_period_threshold_days("017641") == 730
        # 未知基金 → None
        assert _holding_period_threshold_days("999999") is None

    def test_redemption_fee_dict_has_49_funds(self) -> None:
        """REDEMPTION_FEE_DAYS_BY_CODE 收录 49 只普通基金（52 总 - 3 持有期 - 1 货基 - 待核对 004137/其他）。

        数据源：akshare 天天基金 fund_fee_em（2026-10-14 拉）。
        """
        from global_allocation.portfolio.pe_rebalance import (
            REDEMPTION_FEE_DAYS_BY_CODE,
            HOLDING_PERIOD_DAYS_BY_CODE,
        )
        assert len(REDEMPTION_FEE_DAYS_BY_CODE) == 49
        assert len(HOLDING_PERIOD_DAYS_BY_CODE) == 3


# ─── get_current_position ─────────────────────────────


class TestGetCurrentPosition:
    def test_known_fund_returns_cost_over_10000(self) -> None:
        """liubo 拍板："成本 20000 就是 2 个仓位" — 53500 / 10000 = 5.35。"""
        from global_allocation.portfolio.cost_basis import COST_BASIS_BY_CODE
        # 找一个已知 cost 不为 0 的基金做测试
        for code, cost in COST_BASIS_BY_CODE.items():
            if cost > 0:
                pos = get_current_position(code)
                assert pos == cost / Decimal("10000")
                return
        pytest.fail("没找到 cost > 0 的基金，COST_BASIS_BY_CODE 是空的？")

    def test_unknown_fund_returns_zero(self) -> None:
        assert get_current_position("999999") == Decimal("0")

    def test_a_share_013310_position(self) -> None:
        """A 股 013310 华夏科创创业 50, 成本 20000 → 2.0 仓。"""
        assert get_current_position("013310") == Decimal("2.0")

    def test_a500_combined_cost(self) -> None:
        """中证 A500 三只基金合并：022434(10500) + 022424(5000) + 022448(11000) = 26500 / 10000 = 2.65。"""
        assert get_current_position("022434") == Decimal("1.05")
        assert get_current_position("022424") == Decimal("0.5")
        assert get_current_position("022448") == Decimal("1.1")


class TestGetCurrentPositionDingtouOverride:
    """第二十七轮（liubo 2026-10-17 反馈："000216 黄金 定投累计 7500 = 1 仓，凑一仓"）。

    5 只定投基金的 1 仓单位 per-fund 覆盖（不再走全局 10000）：
    - 000614 DAX cost=10000 / 1 仓=10000 → 1 仓
    - 016452 南方纳100 cost=1800 / 1 仓=1800 → 1 仓
    - 019172 摩根纳100 cost=2000 / 1 仓=2000 → 1 仓
    - 019441 万家纳100 cost=2000 / 1 仓=2000 → 1 仓
    - 000216 黄金 cost=7500 / 1 仓=7500 → 1 仓
    """

    def test_dingtou_funds_all_position_equals_1(self) -> None:
        """5 只定投基金 都 = 1 仓（因为 cost = 定投累计 = 1 仓单位）。"""
        for code in ("000614", "016452", "019172", "019441", "000216"):
            pos = get_current_position(code)
            assert pos == Decimal("1"), (
                f"{code} 应是 1 仓（cost = 定投累计 = 1 仓单位），"
                f"实际 {pos}"
            )

    def test_000216_gold_7500_equals_1_cang(self) -> None:
        """000216 黄金：cost=7500, 1 仓=7500 → 1 仓（liubo "凑一仓"原话）。

        关键修复：之前用全局 10000 → 0.75 仓 → 调仓建议「加仓」；现在用 per-fund 7500 → 1 仓 → HOLD。
        """
        assert get_current_position("000216") == Decimal("1")

    def test_non_dingtou_funds_still_use_global_10000(self) -> None:
        """非定投基金还是 cost / 10000（全局默认不变）。"""
        assert get_current_position("013310") == Decimal("2.0")  # 20000 / 10000
        assert get_current_position("013127") == Decimal("3.5")  # 35000 / 10000

    def test_dingtou_000216_signal_is_hold(self) -> None:
        """000216 黄金 signal = HOLD（满仓 + 综合分 2.5 在 2.0-4.0 区间）。

        之前 cost=7500 / 全局 10000 = 0.75 仓 → 欠配 + score 2.5 < 3.0 → ADD；
        改完 cost/7500 = 1 仓 → 满仓 + score 2.5 (2.0-4.0) → HOLD。
        """
        from global_allocation.portfolio.pe_rebalance import weekly_rebalance_plan
        actions = weekly_rebalance_plan()
        gold = next(a for a in actions if a.fund_code == "000216")
        assert gold.signal == PESignal.HOLD, (
            f"000216 黄金应 HOLD（满仓），实际 {gold.signal.value}"
        )


# ─── build_evaluations ─────────────────────────────


class TestBuildEvaluations:
    def test_has_one_eval_per_fund_in_map(self) -> None:
        """每只 FUND_INDEX_MAP 里的基金 1 个 eval。"""
        evals = build_evaluations()
        assert len(evals) == len(FUND_INDEX_MAP)

    def test_a_share_evaluations_have_metric(self) -> None:
        """A 股 9 只基金 metric 都从 snapshot 取到（2026-10-09 加 011612/019857/023414 3 只科创/创业宽基）。"""
        evals = build_evaluations()
        a_share = [e for e in evals if e.subclass == SwensenClass.CN_EQUITY]
        assert len(a_share) == 9
        for e in a_share:
            assert e.metric_value is not None
            assert e.metric_percentile is not None

    def test_reits_have_metric(self) -> None:
        """REITs 用 DY（spec REITs-DY — liubo 2026-10-12 拍板）替代 P/FFO / P/NAV。

        测试用 fixture 注入 LIVE_REIT_SNAPSHOT_BY_INDEX，验证 build_evaluations 走 live snapshot。
        """
        evals = build_evaluations()
        cn_reit = [e for e in evals if e.subclass == SwensenClass.CN_REIT]
        us_reit = [e for e in evals if e.subclass == SwensenClass.US_REIT]
        assert len(cn_reit) == 1
        assert len(us_reit) == 1
        assert cn_reit[0].metric == ValuationMetric.DY
        assert us_reit[0].metric == ValuationMetric.DY
        assert cn_reit[0].metric_value is not None
        assert us_reit[0].metric_value is not None

    def test_reits_skip_without_live_snapshot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """没 LIVE_REIT_SNAPSHOT_BY_INDEX 时 REITs 走 SKIP（reason 提示跑 reit-fetch）。

        spec REITs-DY — liubo 2026-10-12 拍板：REITs 不再硬编码在 PE_SNAPSHOT_BY_INDEX。
        """
        # 清空 LIVE_REIT_SNAPSHOT_BY_INDEX（fixture 默认有测试数据）
        from global_allocation.portfolio import pe_rebalance as pr_mod
        original = dict(pr_mod.LIVE_REIT_SNAPSHOT_BY_INDEX)
        try:
            pr_mod.LIVE_REIT_SNAPSHOT_BY_INDEX.clear()
            evals = build_evaluations()
            cn_reit = [e for e in evals if e.subclass == SwensenClass.CN_REIT]
            us_reit = [e for e in evals if e.subclass == SwensenClass.US_REIT]
            assert len(cn_reit) == 1
            assert len(us_reit) == 1
            # 没数据 → metric_value None → 评估时 SKIP
            assert cn_reit[0].metric_value is None
            assert us_reit[0].metric_value is None
        finally:
            pr_mod.LIVE_REIT_SNAPSHOT_BY_INDEX.update(original)

    def test_commodity_gold_has_composite_score(self) -> None:
        """商品（黄金）spec 099 加 2 指标综合分 → 不再 SKIP。

        黄金（000216 华安黄金 ETF 联接）现在有：
        - metric = GOLD_HISTORICAL_PCT（金价分位主指标，给卡片展示用）
        - metric_value = 当前 SGE Au99.99 金价（CNY/g）
        - metric_percentile = 10 年分位（fraction）
        - composite_score = 2 指标综合分 1-5
        """
        evals = build_evaluations()
        commodities = [e for e in evals if e.subclass == SwensenClass.COMMODITY]
        assert len(commodities) == 1
        gold = commodities[0]
        assert gold.fund_code == "000216"
        assert gold.metric == ValuationMetric.GOLD_HISTORICAL_PCT
        assert gold.metric_value is not None  # SGE Au99.99 当前价
        assert gold.metric_percentile is not None  # 10 年分位
        assert gold.composite_score is not None  # spec 099 综合分
        # 综合分 1-5 范围
        assert Decimal("1") <= gold.composite_score <= Decimal("5")
        # etf_index_code 改成 GOLD（不再是 GOLD_NO_METRIC）
        assert gold.etf_index_code == "GOLD"

    def test_em_equity_has_metric(self) -> None:
        """新兴市场现在有 PE（不再 SKIP）。"""
        evals = build_evaluations()
        em = [e for e in evals if e.subclass == SwensenClass.EM_EQUITY]
        assert len(em) == 1
        assert em[0].metric == ValuationMetric.PE_TTM
        assert em[0].metric_value is not None


class TestWatchlistEvaluations:
    """INDEX_WATCHLIST 评估（HSI 恒生指数）。"""

    def test_hsi_in_watchlist(self) -> None:
        """HSI 恒生指数在 INDEX_WATCHLIST 里。"""
        index_codes = [w[1] for w in INDEX_WATCHLIST]
        assert "HSI" in index_codes

    def test_build_watchlist_returns_one_per_entry(self) -> None:
        """每个 watchlist 项 1 个 eval。"""
        evals = build_watchlist_evaluations()
        assert len(evals) == len(INDEX_WATCHLIST)

    def test_watchlist_evals_have_zero_position(self) -> None:
        """watchlist eval 仓位都是 0。"""
        evals = build_watchlist_evaluations()
        for e in evals:
            assert e.current_position == Decimal("0")

    def test_watchlist_fund_code_starts_with_paren(self) -> None:
        """watchlist fund_code 用 "(INDEX)" 标记（不是真实 6 位基金代码）。"""
        evals = build_watchlist_evaluations()
        for e in evals:
            assert e.fund_code.startswith("(")

    def test_watchlist_name_includes_no_position_label(self) -> None:
        """watchlist 名称带「（无持仓）」标签。"""
        evals = build_watchlist_evaluations()
        for e in evals:
            assert "无持仓" in e.fund_name


# ─── merge_by_etf ─────────────────────────────


class TestMergeByETF:
    def test_single_fund_unaffected(self) -> None:
        """单只基金不合并。"""
        evals = [_make_eval(fund_code="SOLO", etf_index_code=".INX")]
        merged = merge_by_etf(evals)
        assert len(merged) == 1
        assert merged[0].fund_code == "SOLO"

    def test_three_funds_same_etf_merge_into_one(self) -> None:
        """3 只同 ETF 的基金合并为 1 个。"""
        evals = [
            _make_eval(fund_code="A", etf_index_code="000510", current_position=Decimal("1.05")),
            _make_eval(fund_code="B", etf_index_code="000510", current_position=Decimal("0.5")),
            _make_eval(fund_code="C", etf_index_code="000510", current_position=Decimal("1.1")),
        ]
        merged = merge_by_etf(evals)
        assert len(merged) == 1
        m = merged[0]
        assert m.fund_code == "A+B+C"
        assert m.current_position == Decimal("2.65")
        assert "(合并 3 只)" in m.fund_name

    def test_different_etfs_not_merged(self) -> None:
        """不同 ETF 不合并。"""
        evals = [
            _make_eval(fund_code="A", etf_index_code="000510"),
            _make_eval(fund_code="B", etf_index_code="000852"),
        ]
        merged = merge_by_etf(evals)
        assert len(merged) == 2

    def test_merge_keeps_metric_from_first_fund(self) -> None:
        """同 ETF 的估值取第一只基金的（同一指数值都相同）。"""
        evals = [
            _make_eval(fund_code="A", etf_index_code="000510", metric_value=Decimal("15.89"), metric_percentile=Decimal("0.4671")),
            _make_eval(fund_code="B", etf_index_code="000510", metric_value=Decimal("99.99"), metric_percentile=Decimal("0.99")),
        ]
        merged = merge_by_etf(evals)
        assert merged[0].metric_value == Decimal("15.89")
        assert merged[0].metric_percentile == Decimal("0.4671")


# ─── weekly_rebalance_plan ─────────────────────────────


class TestWeeklyRebalancePlan:
    def test_returns_one_action_per_etf_group(self) -> None:
        """每周调仓计划: 1 个 action per ETF group（同 ETF 合并 + watchlist）。"""
        actions = weekly_rebalance_plan()
        # 持仓基金合并后：
        #   CN_EQUITY 6 只 (4 ETF) → 4
        #   HK_EQUITY 1 只 (HSTECH) → 1
        #   US_EQUITY 7 只 (3 ETF) → 3
        #   FOREIGN_DM_EQUITY 1 只 (.GDAXI via 000614) → 1
        #   EM 1 → 1
        #   CN_REIT 1 → 1
        #   US_REIT 1 → 1
        #   COMMODITY 1 → 1
        # watchlist: HSI 1 + 国外发达 2 (.N225/.FCHI；.GDAXI 已被 000614 替代) = 3
        # 合计: 4 + 1 + 3 + 1 + 1 + 1 + 1 + 1 + 3 = 16
        # 第二十二轮（2026-09-29）liubo 把 013127 汇添富恒生科技 转到大类资产
        # → HK_EQUITY 从 0 → 1（HSI 仍在 watchlist，所以 HK 仍 2 group）
        # 2026-09-29 加 国外发达 3 个指数 watchlist（FOREIGN_DM_EQUITY 0 持仓，但想跟踪）
        # 第二十四轮（2026-09-29）加 000614 华安 DAX 联接 A → .GDAXI 从 watchlist 移到 FUND_INDEX_MAP
        # 总 actions 仍是 16（-1 watchlist +1 fund = 净 0 变化）
        # 第二十五轮（2026-10-09）加 011612/019857/023414 3 只科创/创业宽基 → CN_EQUITY 6+3=9 只 → 4+3=7 ETF
        # 总 actions = 16 + 3 = 19（liubo 2026-10-09）
        assert len(actions) == 19

    def test_foreign_dm_indices_appear_in_watchlist(self) -> None:
        """FOREIGN_DM_EQUITY：3 个评估（.N225 watchlist + .GDAXI 来自 000614 fund + .FCHI watchlist）。

        457001 2026-09-22 转 ETF 轮动组合后 FOREIGN_DM_EQUITY 子类 0 只基金。
        liubo 2026-09-29 拍板：跟踪 3 个国外发达指数方便建仓决策。
        第二十四轮（2026-09-29）liubo 加 000614 华安 DAX 联接 A → .GDAXI 从 watchlist 移到 fund map。
        2026-10-17 liubo 反馈"把定投的总成本算上去"——000614 cost 改为累计目标 10000 = 1 仓满仓 → HOLD。
        当前 snapshot（guchacha.com 2026-09-22）：
        - .N225  日经 225: PE 19.21 / 分位 69.8% → HOLD（空仓 + 分位 >= 50% → 等便宜）
        - .GDAXI 德国 DAX: PE 16.90 / 分位 47.1% → HOLD（满仓 + 分位 47.1% → 不动；2026-10-17 改 cost=10000）
        - .FCHI  法国 CAC 40: PE 17.31 / 分位 71.9% → HOLD（同 .N225）
        """
        actions = weekly_rebalance_plan()
        fdm_actions = [
            a for a in actions
            if a.subclass == SwensenClass.FOREIGN_DM_EQUITY
        ]
        assert len(fdm_actions) == 3
        codes = {a.etf_index_code for a in fdm_actions}
        assert codes == {".N225", ".GDAXI", ".FCHI"}
        # 分位 + 信号断言（guchacha 2026-09-22 数据）
        by_idx = {a.etf_index_code: a for a in fdm_actions}
        assert by_idx[".N225"].signal == PESignal.HOLD
        assert by_idx[".N225"].metric_percentile == Decimal("0.698")
        assert by_idx[".N225"].fund_code == "(.N225)"  # 仍是 watchlist
        # 关键：DAX 实际分位 47.1% < 50%，但 000614 cost 改为累计目标 10000 = 1 仓满仓 → HOLD
        # 2026-10-17 改：之前 cost=0 是"未开始"状态 → BUILD，现在 cost=累计 → 满仓 → HOLD
        assert by_idx[".GDAXI"].signal == PESignal.HOLD
        assert by_idx[".GDAXI"].metric_percentile == Decimal("0.471")
        assert by_idx[".GDAXI"].fund_code == "000614"  # 来自 FUND_INDEX_MAP，不是 watchlist
        assert by_idx[".FCHI"].signal == PESignal.HOLD
        assert by_idx[".FCHI"].metric_percentile == Decimal("0.719")
        assert by_idx[".FCHI"].fund_code == "(.FCHI)"  # 仍是 watchlist

    def test_actions_ordered_by_subclass_then_code(self) -> None:
        """按 SwensenClass 枚举顺序排，再按 fund_code 排。"""
        from global_allocation.portfolio.breakdown import SwensenClass
        actions = weekly_rebalance_plan()
        subclass_seq = [a.subclass for a in actions]
        # 应该是 SwensenClass 枚举顺序
        prev_idx = -1
        for sub in subclass_seq:
            curr_idx = list(SwensenClass).index(sub)
            assert curr_idx >= prev_idx
            prev_idx = curr_idx

    def test_a50_triggers_build_or_add(self) -> None:
        """A 股 A50 (930050) 分位 16.94% — 触发 ADD。

        014532 仓位 = 10000/10000 = 1.0（2026-09-24 从 008505 转入 8000 后达到 1 仓目标）。
        新规则：仓位 >= 1 + 分位 16.94% < 20% → ADD（深价值例外）。

        注：930050 上次调仓 2026-09-24，月度冷却期要到 2026-10-24 才能再调。
        所以这里传 today=2026-10-25 跳过冷却期。
        """
        actions = weekly_rebalance_plan(today=date(2026, 10, 25))
        a50_actions = [a for a in actions if "930050" in a.etf_index_code]
        assert len(a50_actions) == 1
        a50 = a50_actions[0]
        assert a50.signal == PESignal.ADD
        assert a50.change == Decimal("1")

    def test_a500_triggers_hold(self) -> None:
        """A500 合并 3 只基金，仓位 2.65 + 分位 46.71% → HOLD。"""
        actions = weekly_rebalance_plan()
        a500_actions = [a for a in actions if "000510" in a.etf_index_code]
        assert len(a500_actions) == 1
        assert a500_actions[0].signal == PESignal.HOLD

    def test_ndx_skips_when_data_missing(self) -> None:
        """纳 100 合并 5 只（1 联接 + 4 直接 QDII 场外）。

        liubo 2026-09-29 卖出 019524 + 加 019172/019441：合并组剩 5 只。
        fund_code = 5 只 "+" 串联；仓位 = 1 联接 + 3 直接 QDII 累计 / 10000 = 0.30 仓
        （019172/019441 初始成本 0；019524 已卖 -20）。

        2026-10-09 liubo 拍板清空：理杏仁 CSV 没 NDX 数据 → entry 删除 → 5 只 SKIP
        （估值数据缺失，规则上不能调仓）。
        """
        actions = weekly_rebalance_plan()
        ndx_actions = [a for a in actions if ".NDX" in a.etf_index_code]
        assert len(ndx_actions) == 1
        assert ndx_actions[0].signal == PESignal.SKIP
        # 5 只合并（含 1 联接 018966 + 539001/016452/019172/019441 4 直接 QDII 场外）
        ndx_fund = ndx_actions[0].fund_code
        assert "018966" in ndx_fund and "539001" in ndx_fund
        assert "016452" in ndx_fund and "019172" in ndx_fund
        assert "019441" in ndx_fund
        # 019524 已卖出（2026-09-29 liubo），不在合并组
        assert "019524" not in ndx_fund

    def test_no_subclass_left_as_skip_except_ndx(self) -> None:
        """spec 099 黄金加综合分后，只有 .NDX（数据缺失）会 SKIP。

        之前历史：REITs / EM 加估值指标后，只剩商品（黄金 GOLD_NO_METRIC）→ SKIP。
        现在：黄金走 2 指标综合分路径 → 不会再 SKIP。

        2026-10-09 拍板清空 .NDX 后，美股 NDX 5 只合并组因估值数据缺失 SKIP（数据源问题）。
        其他所有子类都不应 SKIP。
        """
        actions = weekly_rebalance_plan()
        skips = [a for a in actions if a.signal == PESignal.SKIP]
        # 只有 .NDX 5 只合并组 SKIP
        assert len(skips) == 1
        assert skips[0].etf_index_code == ".NDX"
        assert skips[0].subclass == SwensenClass.US_EQUITY

    def test_hsi_appears_as_hold_or_build(self) -> None:
        """HSI 恒生指数 watchlist 出现在 plan 里（HSI 分位 63% > 50% → HOLD）。"""
        actions = weekly_rebalance_plan()
        hsi_actions = [a for a in actions if a.etf_index_code == "HSI"]
        assert len(hsi_actions) == 1
        hsi = hsi_actions[0]
        assert hsi.subclass == SwensenClass.HK_EQUITY
        assert hsi.signal == PESignal.HOLD  # 63% > 50% → 等便宜
        assert hsi.current_position == Decimal("0")
        assert hsi.metric == ValuationMetric.PE_TTM

    def test_hstech_appears_with_013127(self) -> None:
        """HSTECH 恒生科技：2026-09-29 liubo 把 013127 转到大类资产。

        2026-09-29: 仓位 25000 / 10000 = 2.50 仓。
        2026-10-09 liubo 加仓 +1 仓 → 仓位变 3.50 仓（>= 1 仓）。
        2026-10-09 理杏仁 CSV：PE 21.8289 / 分位 19.69% < 20% → 深价值例外 → ADD +1
        （虽然仓位已经 3.50 仓远超 1 仓，但分位 < 20% 触发「已配足 + 深价值」分支）。
        """
        actions = weekly_rebalance_plan()
        hstech_actions = [a for a in actions if a.etf_index_code == "HSTECH"]
        assert len(hstech_actions) == 1
        hstech = hstech_actions[0]
        assert hstech.subclass == SwensenClass.HK_EQUITY
        assert hstech.fund_code == "013127"
        # 仓位 3.50 >= 1 + 分位 19.69% < 20% → 深价值 ADD +1
        assert hstech.signal == PESignal.ADD
        assert hstech.change == Decimal("1")
        assert hstech.current_position == Decimal("3.5")
        assert hstech.metric == ValuationMetric.PE_TTM

    def test_000614_tracks_gdaxi(self) -> None:
        """000614 华安 DAX 联接 A 是 FOREIGN_DM_EQUITY 子类 .GDAXI 的跟踪器（第二十四轮）。

        liubo 2026-09-29 决定走场外基金（513030 场内 DAX ETF 有溢价），
        用支付宝慧定投：每周三扣款 250-1000 元/周（平均 500），目标累计 10000 CNY = 1 仓（满额自动暂停）。
        2026-10-17 liubo 反馈"把定投的总成本算上去"——cost_basis 改为累计目标 10000 = 1 仓满仓 → HOLD。

        watchlist 的 .GDAXI 项已移除（被 fund 000614 替代，避免重复计算）。
        actions 里 .GDAXI 只有 1 个 action（来自 000614）。
        """
        actions = weekly_rebalance_plan()
        gdaxi = [a for a in actions if a.etf_index_code == ".GDAXI"]
        assert len(gdaxi) == 1, ".GDAXI 应该只有 000614 一个 action（watchlist 已移除）"
        a = gdaxi[0]
        assert a.fund_code == "000614"
        assert a.subclass == SwensenClass.FOREIGN_DM_EQUITY
        # 2026-10-17 改：cost=10000 = 1 仓满仓 + 分位 47.1% < 50% → HOLD（满仓即使便宜也不加仓）
        assert a.signal == PESignal.HOLD
        assert a.current_position == Decimal("1")  # 1 仓满仓（DCA 累计 10000 = 1 仓）
        assert a.metric_percentile == Decimal("0.471")
        assert a.metric == ValuationMetric.PE_TTM
        # .GDAXI 不在 watchlist 里了
        watchlist_idx = {w[1] for w in INDEX_WATCHLIST}
        assert ".GDAXI" not in watchlist_idx


# ─── format_weekly_report ─────────────────────────────


class TestFormatWeeklyReport:
    def test_report_contains_date(self) -> None:
        """报告含日期标识。

        注：传 today=2026-10-25 跳过冷却期，触发 ADD 信号 → 报告含加仓条目。
        日期标识随 today 参数动态生成（liubo 2026-09-28 改）。
        """
        report = format_weekly_report(
            weekly_rebalance_plan(today=date(2026, 10, 25)),
            today=date(2026, 10, 25),
        )
        assert "2026-10-25" in report

    def test_report_contains_summary(self) -> None:
        """报告含「本周操作」summary section（跳过冷却期后）。"""
        report = format_weekly_report(weekly_rebalance_plan(today=date(2026, 10, 25)))
        assert "本周操作" in report

    def test_report_contains_subclass_sections(self) -> None:
        """报告含各子类详细 section。"""
        report = format_weekly_report(weekly_rebalance_plan(today=date(2026, 10, 25)))
        assert "A 股" in report
        assert "美股" in report

    def test_report_for_add_action_shows_fund_name(self) -> None:
        """加仓操作在 summary 显示基金名。"""
        report = format_weekly_report(weekly_rebalance_plan(today=date(2026, 10, 25)))
        assert "中证 A50" in report
        assert "加仓" in report

    def test_report_handles_no_actions(self) -> None:
        """无操作时 summary 显示「本周无操作」。"""
        empty: list[RebalanceAction] = []
        report = format_weekly_report(empty)
        assert "本周无操作" in report


# ─── PE_SNAPSHOT 数据完整性 ─────────────────────────────


class TestPESnapshotData:
    def test_a_share_indices_present(self) -> None:
        """A 股 4 个核心指数都在 snapshot 里。"""
        for idx in ("000510", "000852", "930050", "931643"):
            assert idx in PE_SNAPSHOT_BY_INDEX

    def test_us_indices_present(self) -> None:
        """美股 INX + OEX 都在；.NDX 2026-10-09 liubo 拍板清空（理杏仁 CSV 无数据）。"""
        assert ".INX" in PE_SNAPSHOT_BY_INDEX
        assert ".OEX" in PE_SNAPSHOT_BY_INDEX
        # .NDX 不在 dict 是 expected：5 只纳指 100 基金 SKIP（数据源问题）
        assert ".NDX" not in PE_SNAPSHOT_BY_INDEX

    def test_foreign_dm_indices_present(self) -> None:
        """国外发达 3 个指数都在。"""
        assert ".N225" in PE_SNAPSHOT_BY_INDEX
        assert ".GDAXI" in PE_SNAPSHOT_BY_INDEX
        assert ".FCHI" in PE_SNAPSHOT_BY_INDEX

    def test_all_metric_percentiles_are_fraction(self) -> None:
        """所有分位值都是 fraction (0~1)，不是整数百分比。"""
        for idx, (metric, val, pct) in PE_SNAPSHOT_BY_INDEX.items():
            assert Decimal("0") <= pct <= Decimal("1"), f"{idx} 分位 {pct} 超出 [0, 1]"

    def test_em_reit_snapshot_entries(self) -> None:
        """MSCI EM 在 PE_SNAPSHOT_BY_INDEX；REITs 移到 LIVE_REIT_SNAPSHOT_BY_INDEX（spec REITs-DY）。"""
        assert ".MSCI_EM" in PE_SNAPSHOT_BY_INDEX
        # spec REITs-DY — liubo 2026-10-12 拍板：REITs 不再硬编码
        assert ".MSCI_US_REIT" not in PE_SNAPSHOT_BY_INDEX
        assert "932006" not in PE_SNAPSHOT_BY_INDEX
        # 但应该在 LIVE_REIT_SNAPSHOT_BY_INDEX（fixture 注入）
        assert ".MSCI_US_REIT" in LIVE_REIT_SNAPSHOT_BY_INDEX
        assert "932006" in LIVE_REIT_SNAPSHOT_BY_INDEX

    def test_us_reit_uses_dy_metric(self) -> None:
        """MSCI US REIT 现在用 DY（spec REITs-DY — liubo 2026-10-12 拍板）。"""
        metric, value, pct, _data_date = LIVE_REIT_SNAPSHOT_BY_INDEX[".MSCI_US_REIT"]
        assert metric == ValuationMetric.DY
        # value 应该是 percent number（3.76 = 3.76%）
        assert value is not None
        assert Decimal("1") < value < Decimal("20")  # 正常 DY 范围
        assert pct is not None
        assert Decimal("0") <= pct <= Decimal("1")

    def test_cn_reit_uses_dy_metric(self) -> None:
        """中证 REITs 用 DY。"""
        metric, value, pct, _data_date = LIVE_REIT_SNAPSHOT_BY_INDEX["932006"]
        assert metric == ValuationMetric.DY
        assert value is not None
        assert Decimal("1") < value < Decimal("20")
        assert pct is not None
        assert Decimal("0") <= pct <= Decimal("1")

    def test_a_share_uses_pe_ttm_metric(self) -> None:
        """A 股指数估值指标：
        - 宽基（PE 适用）：000510 / 000852 / 930050 → PE_TTM
        - 科创/创业类（PS 适用）：931643 → PS_TTM（liubo 2026-10-09 改）
        """
        for a_share in ("000510", "000852", "930050"):
            metric, _, _ = PE_SNAPSHOT_BY_INDEX[a_share]
            assert metric == ValuationMetric.PE_TTM
        # 931643 科创创业 50 改 PS（liubo 2026-10-09）
        metric, _, _ = PE_SNAPSHOT_BY_INDEX["931643"]
        assert metric == ValuationMetric.PS_TTM

    def test_new_growth_uses_ps_ttm(self) -> None:
        """2026-10-09 新加 3 个科创/创业类指数（000688 科创 50 / 000698 科创 100 / 399673 创业板 50）都用 PS-TTM。"""
        for a_share in ("000688", "000698", "399673"):
            metric, _, _ = PE_SNAPSHOT_BY_INDEX[a_share]
            assert metric == ValuationMetric.PS_TTM