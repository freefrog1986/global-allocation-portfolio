"""估值数据源（spec 098）。

akshare 适配层 — 6 个方法拉取原始数据，compute 层负责计算指标。
所有 akshare 调用都用 try/except 包好，失败返回 None（不抛异常），
让上层（compute / card）能优雅降级（spec 098 边界情况）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol


class ValuationSource(Protocol):
    """估值数据源接口（spec 098 数据契约）。

    每个方法：
    - 入参 = 拉数据的日期（on）+ 可选参数（指数代码 / 年限）
    - 返回 Decimal / list[Decimal] / None（akshare 失败或缺数据）
    - 不抛异常（callers 必须自己处理 None）
    """

    def get_pe_ttm(
        self,
        on: date,
        index_code: str = "000985",
    ) -> Decimal | None:
        """中证全指（000985）的 PE-TTM。"""
        ...

    def get_pe_history(
        self,
        index_code: str = "000985",
        years: int = 10,
    ) -> list[Decimal]:
        """中证全指过去 N 年的 PE-TTM 序列（中位数，月频）。

        返回 [] 表示 akshare 失败或无数据。
        """
        ...

    def get_dividend_yield(
        self,
        on: date,
        index_code: str = "000985",
    ) -> Decimal | None:
        """中证全指的股息率（返回 0~1 小数，不是 %）。"""
        ...

    def get_10y_treasury_yield(self, on: date) -> Decimal | None:
        """10 年期国债收益率（返回 0~1 小数，不是 %）。"""
        ...

    def get_a_share_total_market_cap(self, on: date) -> Decimal | None:
        """A 股总市值（单位：元 CNY）。"""
        ...

    def get_china_gdp(self, on: date) -> Decimal | None:
        """中国最近一期 GDP（单位：元 CNY，季度数据）。"""
        ...


class AkshareValuationSource:
    """akshare 数据源实现。

    数据源映射（spec 098 表）：
    - PE / 股息率：ak.stock_zh_index_value_csindex(symbol="000985")
      （中证指数官方估值接口，返回最近 20 天 daily 数据；
       字段：市盈率1 / 市盈率2 / 股息率1 / 股息率2 / 日期 等）
    - PE 历史（10 年）：ak.stock_a_ttm_lyr()
      （全部 A 股等权重/中位数 PE 月频历史，从 2005 至今 261 行；
       字段：middlePETTM / quantileInRecent10YearsMiddlePeTtm 等）
    - 10 年期国债收益率：ak.bond_china_yield()（中债国债收益率曲线）
    - A 股总市值：ak.stock_zh_a_spot_em()（实时行情 sum 总市值列）
    - 中国 GDP：ak.macro_china_gdp()（季度数据，绝对值单位"亿元"）

    失败处理：所有 akshare 调用包 try/except → 返回 None / []
    （spec 098 边界：akshare 接口失败 → 跳过该指标，卡片行显示"数据缺失"）
    """

    # ─── PE（用全 A middle PE，跟 history 同口径）───

    def get_pe_ttm(
        self,
        on: date,
        index_code: str = "000985",  # 实际取值忽略 — 接口固定返回全 A
    ) -> Decimal | None:
        """全 A PE-TTM 中位数（最新值）。

        选全 A middle PE 而不是中证全指 PE 的原因：
        - 历史分位需要 current/history 同口径，否则 percentile 算出来不准
          （中证全指历史只有 20 天，没法算 10 年分位）
        - 全 A middle PE 历史有 261 行月频数据（2005 至今），窗口稳定
        - 全 A 是"沪深两市所有 A 股"的代表，跟"整个 A 股"语义一致
        - spec 098 第 41~45 行说"中证全A = 沪深两市所有 A 股"，全 A 同义

        on 参数用于缓存命中检查。akshare 返回的是最新一行（截至今天），
        忽略 on 的精确日期（数据本身没有"日期"列，是截面数据）。
        """
        try:
            import akshare as ak

            df = ak.stock_a_ttm_lyr()
            if df is None or df.empty:
                return None
            # 最新一行（已按时间倒序）
            pe = df["middlePETTM"].iloc[-1]
            if pe is None or pe != pe:  # NaN check
                return None
            return Decimal(str(pe))
        except Exception:
            return None

    def get_dividend_yield(
        self,
        on: date,
        index_code: str = "000985",
    ) -> Decimal | None:
        try:
            import akshare as ak

            df = ak.stock_zh_index_value_csindex(symbol=index_code)
            if df is None or df.empty:
                return None
            dy_pct = _find_value_on_or_latest(df, "日期", on, "股息率1")
            if dy_pct is None:
                return None
            # akshare 返回的是百分数（2.5 = 2.5%），转 0~1 小数
            return Decimal(str(dy_pct)) / Decimal("100")
        except Exception:
            return None

    # ─── PE 历史分位（全部 A 股月频，过去 10 年）───

    def get_pe_history(
        self,
        index_code: str = "000985",  # 实际取值忽略 — 接口固定返回全 A
        years: int = 10,
    ) -> list[Decimal]:
        """全 A PE-TTM 中位数历史序列（最近 N 年月频）。

        数据源：ak.stock_a_ttm_lyr() — 返回 2005 至今 ~261 行月频数据。
        历史窗口默认 10 年（spec 098 第 31 行）。

        返回最近 N 年（约 N*12 个点）的全 A middle PE-TTM 序列。
        compute 层用 (current, history) 算 percentile。

        index_code 参数保留以匹配 Protocol 接口，但 stock_a_ttm_lyr 实际只
        返回全 A 数据。spec 098 后续 sub-spec（098.x）会针对其他市场改用不同函数。
        """
        try:
            import akshare as ak

            df = ak.stock_a_ttm_lyr()
            if df is None or df.empty:
                return []
            # 过滤 NaN，按时间升序
            series = df["middlePETTM"].dropna().tolist()
            if years > 0 and len(series) > years * 12:
                # 截取最近 N 年（月频 12 个点/年）
                series = series[-(years * 12):]
            return [Decimal(str(pe)) for pe in series]
        except Exception:
            return []

    # ─── 10 年期国债收益率 ───

    def get_10y_treasury_yield(self, on: date) -> Decimal | None:
        try:
            import akshare as ak

            df = ak.bond_china_yield()
            if df is None or df.empty:
                return None
            # 过滤出"中债国债收益率曲线"（其他曲线有信用利差）
            df = df[df["曲线名称"] == "中债国债收益率曲线"]
            if df.empty:
                return None
            y_pct = _find_value_on_or_latest(df, "日期", on, "10年")
            if y_pct is None:
                return None
            # akshare 返回百分数（2.85 = 2.85%），转 0~1 小数
            return Decimal(str(y_pct)) / Decimal("100")
        except Exception:
            return None

    # ─── A 股总市值 ───

    def get_a_share_total_market_cap(self, on: date) -> Decimal | None:
        """全 A 总市值（上交所 + 深交所，单位：元 CNY）。

        实现思路（优先级递减）：
        1. stock_sse_summary + stock_szse_summary — 当日数据
           （上交所"股票"总市值列单位是亿；深交所"股票"总市值列单位是元）
           这个组合跟实时数据基本一致但更稳定（不走 eastmoney）
        2. macro_china_stock_market_cap 月度数据 — Fallback
           （当前月未填，回退到最近一个有数据的月份；数据有时滞 1 个月）
        3. stock_zh_a_spot_em 实时行情 — 最后兜底
           （eastmoney 代理不稳时容易失败 → fallback chain 排后面）

        on 参数用于缓存命中检查（callers 可以传 today 然后检查数据库是否已拉）。
        实际接口返回的是最新可用数据，忽略 on 的精确日期。
        """
        # 1. 优先：上交所 + 深交所 当日 summary
        try:
            import akshare as ak

            sse = ak.stock_sse_summary()
            szse = ak.stock_szse_summary()
            total_yi = Decimal("0")
            if sse is not None and not sse.empty:
                row = sse[sse["项目"] == "总市值"]
                if not row.empty:
                    # 上交所"股票"列单位：亿元
                    total_yi += Decimal(str(row["股票"].iloc[0]))
            if szse is not None and not szse.empty:
                row = szse[szse["证券类别"] == "股票"]
                if not row.empty:
                    # 深交所"总市值"列单位：元（实测 ≈ 2.5e13）
                    # 转成亿元便于相加：2.5e13 / 1e8 = 2.5e5 亿
                    total_yi += Decimal(str(row["总市值"].iloc[0])) / Decimal("100000000")
            if total_yi > 0:
                # 统一单位：亿元 → 元
                return total_yi * Decimal("100000000")
        except Exception:
            pass

        # 2. Fallback: macro_china_stock_market_cap 月度数据
        try:
            import akshare as ak

            df = ak.macro_china_stock_market_cap()
            if df is not None and not df.empty:
                # 找第一个 上海 + 深圳 市价总值都不为空的行（最新有数据的月份）
                for _, row in df.iterrows():
                    sh = row.get("市价总值-上海")
                    sz = row.get("市价总值-深圳")
                    if (
                        sh is not None and sh == sh
                        and sz is not None and sz == sz
                    ):
                        # 单位亿元 → 元
                        return (Decimal(str(sh)) + Decimal(str(sz))) * Decimal("100000000")
        except Exception:
            pass

        # 3. 最后兜底：实时行情（eastmoney 全 A 总市值）
        try:
            import akshare as ak

            df = ak.stock_zh_a_spot_em()
            if df is not None and not df.empty and "总市值" in df.columns:
                total = df["总市值"].sum()
                if total is not None and total == total:  # not NaN
                    return Decimal(str(total))
        except Exception:
            pass

        return None

    # ─── 中国 GDP ───

    def get_china_gdp(self, on: date) -> Decimal | None:
        """中国最近一期 GDP 绝对值（年度数据，单位：元 CNY）。

        akshare 的 macro_china_gdp 返回季度累计 GDP。巴菲特指标需要**年度**
        GDP 做分母（市场是流量 vs 总市值是存量，要看年化产能）。

        返回最近一个**完整年度**的 GDP（macro_china_gdp 数据按时间倒序，
        第一行通常是最新季度的累计值，但累计不能直接用 — 找最近的
        "第1-4季度" 行）。

        on 参数用于缓存命中检查。
        """
        try:
            import akshare as ak

            df = ak.macro_china_gdp()
            if df is None or df.empty:
                return None
            # 找第一个 "第1-4季度" 行（最近完整年度）
            annual_rows = df[df["季度"].str.contains("第1-4季度", na=False)]
            if annual_rows.empty:
                # Fallback: 用第一行（最新季度累计）— 数据残缺时的兜底
                row = df.iloc[0]
            else:
                row = annual_rows.iloc[0]  # 最新完整年度（已按时间倒序）
            gdp_yi = row["国内生产总值-绝对值"]
            if gdp_yi is None or gdp_yi != gdp_yi:  # NaN check
                return None
            return Decimal(str(gdp_yi)) * Decimal("100000000")
        except Exception:
            return None


def _find_value_on_or_latest(
    df: object,
    date_col: str,
    on: date,
    value_col: str,
) -> object | None:
    """按 on 日期匹配行，找不到就用最新一行（容错：节假日/周末 akshare 不更新）。

    返回原始值（可能是 float / Decimal / NaN）。返回 None 表示数据真没有。
    """
    import pandas as pd

    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    target = on.isoformat()
    # 字符串化比较（akshare 日期列通常是 object）
    df_str = df.copy()
    df_str[date_col] = df_str[date_col].astype(str)
    rows = df_str[df_str[date_col] == target]
    if rows.empty:
        # 没匹配到精确日期 → 用最新一条（节假日 / 周末兜底）
        rows = df_str.head(1)
    if rows.empty:
        return None
    val = rows[value_col].iloc[0]
    if pd.isna(val):
        return None
    return val


__all__ = ["ValuationSource", "AkshareValuationSource"]
