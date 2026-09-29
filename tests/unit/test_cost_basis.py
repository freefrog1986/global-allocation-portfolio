"""测试 src/global_allocation/portfolio/cost_basis.py。

参照 spec 097（liubo 2026-09-21 手工录入的成本数据）。
2026-09-22 砍非宽基股权 11 只后只算大类资产配置组合（47 → 36）。
2026-09-22 又把 457001 国富亚洲机会移到 ETF 轮动组合（36 → 35）。
2026-09-22 第十九轮：加 022448 + 007997 + 7 只新债 + 解锁 008505/004827 → 46 只 + 1 cash。
2026-09-24 第二次调仓：008505 转 4999.39 → 028277（国内 REITs 凑 1 仓）。
2026-09-29 第二十轮：加 019172 摩根纳斯达克100指数(QDII)人民币A → 47 只（成本 0；DCA 进行中）。
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
    def test_has_forty_seven_funds(self) -> None:
        """47 只大类资产配置基金有成本数字（第二十轮 liubo 2026-09-29 加 019172 后）。

        019172 摩根纳指100 QDII 人民币A：直接 QDII（非联接），DCA ¥10/天，
        初始成本 0（元代买）；合并到 NDX 仓位（4 联接 + 019172）。
        """
        assert len(COST_BASIS_BY_CODE) == 47

    def test_total_matches_sum(self) -> None:
        """总和等于 liubo 算出的 579163.47（46 只；2026-09-24 第二次调仓后 +0.61）。
        2026-09-22 第十九轮时为 579162.86；2026-09-24 因 028277 bump 到 10000 多了 0.61。
        第二十轮 019172 成本 0，总和不变。
        """
        total = sum(COST_BASIS_BY_CODE.values())
        assert total == TOTAL_COST_CNY
        assert TOTAL_COST_CNY == Decimal("579163.47")

    def test_specific_a_share_amounts(self) -> None:
        """6 只 A 股宽基（第十九轮加 022448 后 6 只）。"""
        assert COST_BASIS_BY_CODE["013310"] == Decimal("20000")  # 华夏科创创业50
        assert COST_BASIS_BY_CODE["022434"] == Decimal("10500")  # 南方中证A500
        assert COST_BASIS_BY_CODE["017644"] == Decimal("5000")   # 博道中证1000指数增强
        assert COST_BASIS_BY_CODE["022424"] == Decimal("5000")   # 广发中证A500
        assert COST_BASIS_BY_CODE["014532"] == Decimal("10000")  # 易方达MSCI中国A50（2026-09-24 +8000）
        # 第十九轮从红利策略组合移过来
        assert COST_BASIS_BY_CODE["022448"] == Decimal("11000")  # 国泰中证A500ETF发起联接A

    def test_specific_us_amounts(self) -> None:
        """7 只美股宽基（标普500/标普100 + 5 只纳100，第二十轮加 019172）。"""
        assert COST_BASIS_BY_CODE["519981"] == Decimal("3620")   # 长信标普100
        assert COST_BASIS_BY_CODE["018966"] == Decimal("2020")   # 汇添富纳100
        assert COST_BASIS_BY_CODE["539001"] == Decimal("1000")   # 建信纳100
        assert COST_BASIS_BY_CODE["017641"] == Decimal("50")     # 摩根标普500
        assert COST_BASIS_BY_CODE["016452"] == Decimal("20")     # 南方纳100（第十九轮补回）
        assert COST_BASIS_BY_CODE["019524"] == Decimal("20")     # 华泰柏瑞纳100
        assert COST_BASIS_BY_CODE["019172"] == Decimal("0")      # 摩根纳指100 QDII 人民币A（DCA 第二十轮加）

    def test_hk_amounts_removed(self) -> None:
        """港股 5 只都砍了，不在 cost_basis。"""
        for code in ("004098", "013127", "006809", "014673", "016495"):
            assert code not in COST_BASIS_BY_CODE, f"{code} 已砍"

    def test_themed_us_amounts_removed(self) -> None:
        """美股 3 只 QDII 主题都砍了。"""
        for code in ("017730", "016664", "006373"):
            assert code not in COST_BASIS_BY_CODE, f"{code} 已砍"

    def test_dividend_lowvol_removed(self) -> None:
        """A 股 3 只红利低波都砍了。"""
        for code in ("005561", "007605", "008114"):
            assert code not in COST_BASIS_BY_CODE, f"{code} 已砍"

    def test_other_subclass_amounts(self) -> None:
        """国外发达（liubo 2026-09-22 移 457001 到 ETF 轮动组合）/ 新兴市场 / REITs / 国内利率债 / 美债 / 商品。"""
        assert "457001" not in COST_BASIS_BY_CODE  # 已移到 ETF 轮动组合
        assert COST_BASIS_BY_CODE["378006"] == Decimal("3910")   # 摩根全球新兴市场
        assert COST_BASIS_BY_CODE["028277"] == Decimal("10000")  # 华夏中证REITs全收益（2026-09-24 +4999.39 凑 1 仓，bump 到 10000 避免 Decimal 噪声）
        assert COST_BASIS_BY_CODE["160140"] == Decimal("5000")   # 南方道琼斯美国精选REIT
        assert COST_BASIS_BY_CODE["003547"] == Decimal("2000")   # 鹏华丰禄
        assert COST_BASIS_BY_CODE["000931"] == Decimal("3500")   # 国寿安保尊益信用纯债
        assert COST_BASIS_BY_CODE["100050"] == Decimal("6000")   # 富国全球债券
        assert COST_BASIS_BY_CODE["007360"] == Decimal("55000")  # 易方达中短期美元债
        assert COST_BASIS_BY_CODE["003385"] == Decimal("15000")  # 工银全球美元债
        assert COST_BASIS_BY_CODE["000216"] == Decimal("2510")   # 华安黄金ETF联接A

    def test_unlocked_short_bond_funds(self) -> None:
        """第十九轮解锁：008505/004827 不再按现金，进 cost_basis。
        2026-09-24 liubo 转换：第一次 008505 8000 → 014532，第二次 008505 4999.39 → 028277。
        现在 008505 剩 4493.47（= 9492.86 - 4999.39 - 0；17492.86 减两次转出）。
        """
        assert COST_BASIS_BY_CODE["008505"] == Decimal("4493.47")
        assert COST_BASIS_BY_CODE["004827"] == Decimal("22000")

    def test_007997_moved_from_dividend_strategy(self) -> None:
        """第十九轮：007997 易方达年年恒秋一年定开债A 从红利策略组合移过来。"""
        assert COST_BASIS_BY_CODE["007997"] == Decimal("5000")

    def test_seven_new_cn_gov_bond_funds(self) -> None:
        """第十九轮新增 7 只中债。"""
        assert COST_BASIS_BY_CODE["004534"] == Decimal("20000")  # 汇添富双盈回报
        assert COST_BASIS_BY_CODE["110017"] == Decimal("1000")   # 易方达增强回报
        assert COST_BASIS_BY_CODE["009625"] == Decimal("1000")   # 天弘中债3-5年政金债
        assert COST_BASIS_BY_CODE["005690"] == Decimal("1000")   # 中银安享
        assert COST_BASIS_BY_CODE["400030"] == Decimal("1000")   # 东方添益
        assert COST_BASIS_BY_CODE["010942"] == Decimal("20000")  # 招商瑞乐6个月
        assert COST_BASIS_BY_CODE["008420"] == Decimal("20000")  # 广发招泰


class TestCashLikeCodes:
    def test_only_cash_fund_skipped(self) -> None:
        """第十九轮：008505/004827 解锁后，剩 004137 一只按现金处理。"""
        assert len(CASH_LIKE_CODES) == 1
        assert "004137" in CASH_LIKE_CODES

    def test_cash_like_not_in_cost_basis(self) -> None:
        """现金/类现金不在 COST_BASIS_BY_CODE 里。"""
        for code in CASH_LIKE_CODES:
            assert code not in COST_BASIS_BY_CODE

    def test_unlocked_funds_no_longer_cash_like(self) -> None:
        """008505/004827 第十九轮解锁后不再是 cash_like。"""
        assert "008505" not in CASH_LIKE_CODES
        assert "004827" not in CASH_LIKE_CODES


class TestGetCostBasis:
    def test_known_fund(self) -> None:
        assert get_cost_basis("013310") == Decimal("20000")
        assert get_cost_basis("457001") is None  # 已移到 ETF 轮动组合

    def test_unknown_fund_returns_none(self) -> None:
        """未知基金返回 None，不抛错。"""
        assert get_cost_basis("999999") is None
        assert get_cost_basis("") is None

    def test_cut_fund_returns_none(self) -> None:
        """砍掉的 11 只非宽基返回 None（已不在 cost_basis）。"""
        assert get_cost_basis("004098") is None  # 港股
        assert get_cost_basis("017730") is None  # QDII 主题
        assert get_cost_basis("008114") is None  # A 股红利低波

    def test_cash_like_returns_none(self) -> None:
        """现金/类现金返回 None（不进成本表）。"""
        assert get_cost_basis("004137") is None

    def test_unlocked_funds_have_cost(self) -> None:
        """008505/004827 第十九轮解锁后有成本数字。
        008505: 17492.86 → 9492.86（第一次转 8000 到 014532）→ 4493.47（第二次转 4999.39 到 028277）。
        """
        assert get_cost_basis("008505") == Decimal("4493.47")
        assert get_cost_basis("004827") == Decimal("22000")

    def test_moved_from_dividend_has_cost(self) -> None:
        """007997 第十九轮从红利策略组合移过来，有成本数字。"""
        assert get_cost_basis("007997") == Decimal("5000")

    def test_new_seven_funds_have_cost(self) -> None:
        """第十九轮新增 7 只中债有成本数字。"""
        assert get_cost_basis("004534") == Decimal("20000")
        assert get_cost_basis("008420") == Decimal("20000")


class TestIsCashLike:
    def test_true_for_cash_like(self) -> None:
        assert is_cash_like("004137") is True

    def test_false_for_cost_basis_fund(self) -> None:
        """在成本表里的基金不算 cash_like。"""
        assert is_cash_like("013310") is False
        assert is_cash_like("457001") is False

    def test_false_for_unlocked_fund(self) -> None:
        """008505/004827 第十九轮解锁后不算 cash_like。"""
        assert is_cash_like("008505") is False
        assert is_cash_like("004827") is False

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
        """CASH_LIKE_CODES 里 004137 在 SUBCLASS_BY_CODE 里属 CASH。"""
        from global_allocation.portfolio.breakdown import SUBCLASS_BY_CODE, SwensenClass

        assert SUBCLASS_BY_CODE["004137"] == SwensenClass.CASH