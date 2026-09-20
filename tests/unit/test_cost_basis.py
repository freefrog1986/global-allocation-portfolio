"""测试 src/global_allocation/portfolio/cost_basis.py。

参照 spec 097（liubo 2026-09-21 手工录入的成本数据）。
"""

from __future__ import annotations

from decimal import Decimal

from global_allocation.portfolio.cost_basis import (
    CASH_LIKE_CODES,
    COST_BASIS_BY_CODE,
    TOTAL_COST_CNY,
    get_cost_basis,
    is_cash_like,
)


class TestCostBasisByCode:
    def test_has_twenty_nine_funds(self) -> None:
        """29 只基金有成本数字（32 - 3 只现金/类现金跳过）。"""
        assert len(COST_BASIS_BY_CODE) == 29

    def test_total_matches_sum(self) -> None:
        """总和等于 liubo 录入时手算的 307570。"""
        total = sum(COST_BASIS_BY_CODE.values())
        assert total == TOTAL_COST_CNY
        assert TOTAL_COST_CNY == Decimal("307570")

    def test_specific_a_share_amounts(self) -> None:
        """spec 097：6 只 A 股的成本数字。"""
        assert COST_BASIS_BY_CODE["013310"] == Decimal("20000")  # 华夏科创创业50
        assert COST_BASIS_BY_CODE["022434"] == Decimal("10500")  # 南方中证A500
        assert COST_BASIS_BY_CODE["008114"] == Decimal("50000")  # 天弘中证红利低波动100
        assert COST_BASIS_BY_CODE["017644"] == Decimal("5000")   # 博道中证1000指数增强
        assert COST_BASIS_BY_CODE["022424"] == Decimal("5000")   # 广发中证A500
        assert COST_BASIS_BY_CODE["014532"] == Decimal("2000")   # 易方达MSCI中国A50

    def test_specific_hk_amounts(self) -> None:
        """4 只港股的成本数字（014673 是 liubo 2026-09-20 补加的港股）。"""
        assert COST_BASIS_BY_CODE["004098"] == Decimal("50000")  # 前海开源港股通股息率50强
        assert COST_BASIS_BY_CODE["013127"] == Decimal("25000")  # 汇添富恒生科技
        assert COST_BASIS_BY_CODE["006809"] == Decimal("1010")   # 泰康香港银行指数
        assert COST_BASIS_BY_CODE["014673"] == Decimal("22000")  # 富国中证港股通互联网ETF联接A

    def test_specific_us_amounts(self) -> None:
        """9 只美股的成本数字。"""
        assert COST_BASIS_BY_CODE["519981"] == Decimal("3620")   # 长信标普100
        assert COST_BASIS_BY_CODE["018966"] == Decimal("2020")   # 汇添富纳100
        assert COST_BASIS_BY_CODE["539001"] == Decimal("1000")   # 建信纳100
        assert COST_BASIS_BY_CODE["017641"] == Decimal("50")     # 摩根标普500
        assert COST_BASIS_BY_CODE["016452"] == Decimal("20")     # 南方纳100
        assert COST_BASIS_BY_CODE["019524"] == Decimal("20")     # 华泰柏瑞纳100
        assert COST_BASIS_BY_CODE["017730"] == Decimal("5300")   # 嘉实全球产业升级
        assert COST_BASIS_BY_CODE["016664"] == Decimal("2370")   # 天弘全球高端制造
        assert COST_BASIS_BY_CODE["006373"] == Decimal("100")    # 国富全球科技互联

    def test_other_subclass_amounts(self) -> None:
        """国外发达 / 新兴市场 / REITs / 国内利率债 / 美债 / 商品。"""
        assert COST_BASIS_BY_CODE["457001"] == Decimal("4640")   # 国富亚洲机会
        assert COST_BASIS_BY_CODE["378006"] == Decimal("3910")   # 摩根全球新兴市场
        assert COST_BASIS_BY_CODE["028277"] == Decimal("5000")   # 华夏中证REITs全收益
        assert COST_BASIS_BY_CODE["160140"] == Decimal("5000")   # 南方道琼斯美国精选REIT
        assert COST_BASIS_BY_CODE["003547"] == Decimal("2000")   # 鹏华丰禄
        assert COST_BASIS_BY_CODE["000931"] == Decimal("3500")   # 国寿安保尊益信用纯债
        assert COST_BASIS_BY_CODE["100050"] == Decimal("6000")   # 富国全球债券
        assert COST_BASIS_BY_CODE["007360"] == Decimal("55000")  # 易方达中短期美元债
        assert COST_BASIS_BY_CODE["003385"] == Decimal("15000")  # 工银全球美元债
        assert COST_BASIS_BY_CODE["000216"] == Decimal("2510")   # 华安黄金ETF联接A


class TestCashLikeCodes:
    def test_three_funds_skipped(self) -> None:
        """3 只按现金/类现金处理（008505/004827/004137）。"""
        assert len(CASH_LIKE_CODES) == 3
        assert "008505" in CASH_LIKE_CODES
        assert "004827" in CASH_LIKE_CODES
        assert "004137" in CASH_LIKE_CODES

    def test_cash_like_not_in_cost_basis(self) -> None:
        """现金/类现金不在 COST_BASIS_BY_CODE 里。"""
        for code in CASH_LIKE_CODES:
            assert code not in COST_BASIS_BY_CODE


class TestGetCostBasis:
    def test_known_fund(self) -> None:
        assert get_cost_basis("013310") == Decimal("20000")
        assert get_cost_basis("014673") == Decimal("22000")

    def test_unknown_fund_returns_none(self) -> None:
        """未知基金返回 None，不抛错。"""
        assert get_cost_basis("999999") is None
        assert get_cost_basis("") is None

    def test_cash_like_returns_none(self) -> None:
        """现金/类现金返回 None（不进成本表）。"""
        assert get_cost_basis("004137") is None
        assert get_cost_basis("008505") is None
        assert get_cost_basis("004827") is None


class TestIsCashLike:
    def test_true_for_cash_like(self) -> None:
        assert is_cash_like("004137") is True
        assert is_cash_like("008505") is True
        assert is_cash_like("004827") is True

    def test_false_for_cost_basis_fund(self) -> None:
        """在成本表里的基金不算 cash_like。"""
        assert is_cash_like("013310") is False
        assert is_cash_like("004098") is False

    def test_false_for_unknown(self) -> None:
        """未知基金返回 False（不是现金/类现金，是未知）。"""
        assert is_cash_like("999999") is False


class TestAlignmentWithBreakdown:
    """关键不变量：cost_basis 表覆盖所有非现金子类 + 现金类跳过。"""

    def test_all_non_cash_global_funds_have_cost(self) -> None:
        """大类资产配置组合（SUBCLASS_BY_CODE）里所有基金：
        要么在 COST_BASIS_BY_CODE，要么在 CASH_LIKE_CODES。"""
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE

        for code in SUBCLASS_BY_CODE:
            assert (
                code in COST_BASIS_BY_CODE
                or code in CASH_LIKE_CODES
            ), f"{code} 既无成本也非 cash_like，缺数据"

    def test_cash_like_only_for_real_cash_like(self) -> None:
        """CASH_LIKE_CODES 里 3 只都是 SUBCLASS_BY_CODE 里的 CN_GOV_BOND 或 CASH。"""
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE, SwensenClass

        assert SUBCLASS_BY_CODE["008505"] == SwensenClass.CN_GOV_BOND
        assert SUBCLASS_BY_CODE["004827"] == SwensenClass.CN_GOV_BOND
        assert SUBCLASS_BY_CODE["004137"] == SwensenClass.CASH
