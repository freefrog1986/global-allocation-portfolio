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

    # ─── 港股估值指标（spec 098.2）───

    def get_hk_ah_premium(self, on: date) -> Decimal | None:
        """AH 溢价（恒生沪深港通 AH 溢价指数 HSAHP / 100）。

        返回 ratio（1.24 = A 股比 H 股贵 24%）。
        值越大表示 H 股越便宜（低估信号）。
        """
        ...

    def get_hk_total_market_cap(self, on: date) -> Decimal | None:
        """港股通总市值（单位：HKD）。

        注：akshare/HKEX 没有现成 API 直接拉总市值。
        实现靠硬编码 HKEX 月度统计（详见 AkshareValuationSource 实现）。
        """
        ...

    def get_hk_gdp(self, on: date) -> Decimal | None:
        """香港最近一期 GDP（单位：百万 HKD，季度数据）。"""
        ...

    # ─── 美股估值指标（spec 098.3）───

    def get_us_10y_treasury_yield(self, on: date) -> Decimal | None:
        """美 10 年期国债收益率（返回 0~1 小数，不是 %）。"""
        ...

    def get_us_total_market_cap(self, on: date) -> Decimal | None:
        """美股总市值（NYSE + NASDAQ，单位：USD）。

        注：akshare 没有现成接口。硬编码 ~50 万亿 USD（2024 末）。
        TODO: 接 NYSE/NASDAQ 官方统计或本地缓存按月刷新。
        """
        ...

    def get_us_gdp(self, on: date) -> Decimal | None:
        """美国最近一期 GDP（单位：USD，绝对值）。

        注：akshare 的 macro_usa_gdp_monthly 返回的是 YoY 增长率，不是绝对值。
        硬编码 ~29 万亿 USD（2024 末）。TODO: 接 BEA 官方或 World Bank API。
        """
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
        """10 年期中国国债收益率（返回 0~1 小数，不是 %）。

        数据源：ak.bond_zh_us_rate()
        列：日期 / 中国国债收益率2年 / 5年 / 10年 / 30年 / ...
        注：早期实现用 ak.bond_china_yield()（"中债国债收益率曲线" 那个），但其
        数据被卡在 2021-01-22 附近（akshare 老 bug），导致估值永远错算。换成
        bond_zh_us_rate() 后日期稳定到今天（2026-09-19 实测最新 = 2026-09-18）。
        """
        try:
            import akshare as ak

            df = ak.bond_zh_us_rate()
            if df is None or df.empty:
                return None
            y_pct = _find_value_on_or_latest(df, "日期", on, "中国国债收益率10年")
            if y_pct is None:
                return None
            # akshare 返回百分数（1.68 = 1.68%），转 0~1 小数
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

    # ─── 港股估值指标（spec 098.2）───

    def get_hk_ah_premium(self, on: date) -> Decimal | None:
        """AH 溢价（恒生沪深港通 AH 溢价指数 HSAHP / 100）。

        数据源：ak.stock_hk_index_daily_sina('HSAHP')
        HSAHP 是恒生指数公司编制的"A 股 vs H 股"溢价指数：
          - 100 = A = H（无溢价）
          - >100 = A 贵于 H（A 溢价 / H 折价）
          - <100 = A 便宜于 H（A 折价 / H 溢价）
        返回 ratio（1.24 = A 股比 H 股贵 24%），方向是"值大=H便宜=低估"。
        """
        try:
            import akshare as ak

            df = ak.stock_hk_index_daily_sina("HSAHP")
            if df is None or df.empty:
                return None
            # 找 on 或 on 之前最近一天的收盘价
            df_sorted = df.sort_values("date")
            target = on
            df_sorted = df_sorted[df_sorted["date"] <= target]
            if df_sorted.empty:
                return None
            close = df_sorted["close"].iloc[-1]
            if close is None or close != close:  # NaN check
                return None
            # HSAHP 是"指数值"（100 为基准），ratio = 指数 / 100
            return Decimal(str(close)) / Decimal("100")
        except Exception:
            return None

    def get_hk_total_market_cap(self, on: date) -> Decimal | None:
        """港股通总市值（单位：HKD）。

        注：akshare 没有港股总市值的现成接口（eastmoney 代理不稳；
        HKEX 官网月报 Excel 直链经常变）。先用 liubo 提供的近期硬编码值
        （HKEX 月度统计，约 35-38 万亿 HKD），后续接 HKEX 官网或手动更新。

        Fallback: 2025-12 HKEX 月度统计约 38.5 万亿 HKD（主板+创业板，含非港股通）。
        TODO: 接 HKEX 官网或本地缓存按月刷新。
        """
        # Fallback: 2025-12 HKEX 月度统计（HKEX 主板 + 创业板）
        # 约 38.5 万亿 HKD（含非港股通股票）。保守估计取 35 万亿。
        from datetime import date as _date

        fallback_mcap_hkd = Decimal("38500000000000")  # 38.5 万亿 HKD
        cutoff = _date(2025, 12, 31)
        if on >= cutoff:
            return fallback_mcap_hkd
        # 历史月份按比例缩放 - 简化：返回同一值
        return fallback_mcap_hkd

    def get_hk_gdp(self, on: date) -> Decimal | None:
        """香港最近一期 GDP（单位：HKD，季度数据，绝对值）。

        数据源：ak.macro_china_hk_gbp()
        列：时间 / 前值 / 现值 / 发布日期
        单位：百万 HKD（注意不是元！）
        返回最近一个完整年度的 4 个季度累加。
        """
        try:
            import akshare as ak

            df = ak.macro_china_hk_gbp()
            if df is None or df.empty:
                return None
            # 数据按时间倒序（最新在 head）。找最近 4 个季度累加。
            # 时间格式："2025第3季度" → 解析年/季度
            import re

            def parse_period(s: str) -> tuple[int, int] | None:
                m = re.match(r"(\d{4})第([1-4])季度", str(s))
                if m is None:
                    return None
                return int(m.group(1)), int(m.group(2))

            # 找最近一个 Q4（即完整年度的最后季度）作为年度终点
            q4_rows = []
            for _, row in df.iterrows():
                p = parse_period(row["时间"])
                if p is None or row["现值"] != row["现值"]:  # NaN check
                    continue
                q4_rows.append((p, Decimal(str(row["现值"]))))
            if not q4_rows:
                return None
            # 按 (年, 季度) 降序排
            q4_rows.sort(key=lambda x: (x[0][0], x[0][1]), reverse=True)
            # 找最近一个 Q4
            recent_year_q4 = None
            for (y, q), v in q4_rows:
                if q == 4:
                    recent_year_q4 = (y, q)
                    break
            if recent_year_q4 is None:
                # 没有 Q4（数据不全），用最近 4 季度累加
                return sum(v for _, v in q4_rows[:4]) * Decimal("1000000")
            # 累加到该 Q4 为止（4 个季度）
            y_end, _ = recent_year_q4
            annual = sum(
                v for (y, q), v in q4_rows if y == y_end
            )
            # 单位：百万 HKD → HKD
            return annual * Decimal("1000000")
        except Exception:
            return None

    # ─── 美股估值指标（spec 098.3）───

    def get_us_10y_treasury_yield(self, on: date) -> Decimal | None:
        """美 10 年期国债收益率（返回 0~1 小数，不是 %）。

        数据源：ak.bond_zh_us_rate()
        列：日期 / ... / 美国国债收益率10年 / ...
        单位：百分数（5.01 = 5.01%）。
        """
        try:
            import akshare as ak

            df = ak.bond_zh_us_rate()
            if df is None or df.empty:
                return None
            y_pct = _find_value_on_or_latest(
                df, "日期", on, "美国国债收益率10年"
            )
            if y_pct is None:
                return None
            return Decimal(str(y_pct)) / Decimal("100")
        except Exception:
            return None

    def get_us_total_market_cap(self, on: date) -> Decimal | None:
        """美股总市值（NYSE + NASDAQ，单位：USD）。

        注：akshare 没有美股总市值的现成接口（eastmoney 代理不稳；
        NYSE/NASDAQ 官网月报 Excel 直链经常变）。

        硬编码 2024 年末值（NYSE ~$32 万亿 + NASDAQ ~$30 万亿）=
        约 $50 万亿 USD（含非美股 ADR）。保守估计 $50T。

        TODO: 接 NYSE 月度统计 https://www.nyse.com/markets/market-data
        或 NASDAQ 总市值月报 + 本地缓存按月刷新。
        """
        from datetime import date as _date

        fallback_mcap_usd = Decimal("50000000000000")  # 50 万亿 USD
        cutoff = _date(2024, 12, 31)
        if on >= cutoff:
            return fallback_mcap_usd
        # 历史月份按当前统一 fallback（数据缺失场景）
        return fallback_mcap_usd

    def get_us_gdp(self, on: date) -> Decimal | None:
        """美国最近一期 GDP（单位：USD，绝对值）。

        注：akshare 的 macro_usa_gdp_monthly 返回 YoY 增长率（%），不是绝对值。
        World Bank API 在本环境 SSL 受限（EOF 错误），BEA 需 API key。
        硬编码 2024 末值约 $29.2 万亿 USD（BEA Q4 2024 release）。

        TODO: 接 BEA 官方 https://apps.bea.gov/API/signup/index.cfm
        或本地缓存按季度刷新。
        """
        from datetime import date as _date

        fallback_gdp_usd = Decimal("29200000000000")  # 29.2 万亿 USD
        cutoff = _date(2024, 12, 31)
        if on >= cutoff:
            return fallback_gdp_usd
        # 历史月份按当前统一 fallback
        return fallback_gdp_usd


def _find_value_on_or_latest(
    df: object,
    date_col: str,
    on: date,
    value_col: str,
) -> object | None:
    """按 on 日期匹配行，找不到就用最新一行（容错：节假日/周末 akshare 不更新）。

    akshare 接口按日期升序排（head=最早，tail=最新），所以 fallback 用 tail(1)。

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
        # 没匹配到精确日期 → 取最新一行（节假日 / 周末兜底）。
        # akshare 数据按日期升序排，所以 latest = tail(1)。
        rows = df_str.tail(1)
    if rows.empty:
        return None
    val = rows[value_col].iloc[0]
    if pd.isna(val):
        return None
    return val


__all__ = ["ValuationSource", "AkshareValuationSource"]
