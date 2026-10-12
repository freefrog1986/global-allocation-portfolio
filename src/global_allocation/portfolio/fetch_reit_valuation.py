"""REITs 估值指标自动拉取（spec REITs-DY — liubo 2026-10-12 拍板）。

数据源：
- US REIT：yfinance VNQ（Vanguard REIT ETF，跟踪 MSCI US REIT 接近）
  - 当前 DY-TTM = trailing 12M dividends / current close
  - 5Y DY 分位：月度 DY-TTM 序列（~60 个点），当前 DY 在序列里的位置
  - 高 DY 分位 = 当前 DY 比历史高 = 便宜
- CN REIT 932006：akshare stock_zh_index_value_csindex（最新 DY）
  + yfinance 508000.SS（华夏 REITs ETF）算月度 DY-TTM 5Y 分位

输出：data/reit_valuation/YYYY-MM-DD.json
失败：返回 None + log warning（不抛异常；周五 cron 跑失败也继续出报告）

liubo 2026-10-12 拍板：放弃 P/FFO / P/NAV（NAREIT 月报 + 招商月报难抓、6 周未更新），
改用股息率（DY）做 REITs 估值指标。每周五 cron 拉一次。

分位方向（重要）：
- 高 DY = 便宜（price 被压低）
- 低 DY = 贵（price 涨上去了）
- JSON 里存两份：
  - `dy_percentile_5y`：原始 DY 分位（高 = 便宜）— 用于展示
  - `valuation_percentile_5y`：1 - dy_percentile（低 = 便宜）— 用于规则
- pe_rebalance.py 用 valuation_percentile_5y 进 LIVE_REIT_SNAPSHOT_BY_INDEX，跟
  PE/PS 一样走「低分位 = 便宜 → ADD」的规则（无需改 _evaluate_signal）
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path


# ─── 配置 ──────────────────────────────────────────

# 输出目录（XDG 兼容）
DATA_DIR = Path(
    os.environ.get(
        "GAP_REIT_VALUATION_DIR",
        str(Path.home() / ".local" / "share" / "gap" / "reit_valuation"),
    )
)

# 5 年窗口（指数 932006 基日 2021-09-30，5Y 是最大窗口）
HISTORY_YEARS = 5

# 代理 ETF
US_REIT_PROXY = "VNQ"            # Vanguard REIT ETF（流动性最好，跟踪 MSCI US REIT 接近）
CN_REIT_PROXY = "508000.SS"       # 华夏 REITs ETF，跟踪 932006 接近


# ─── 延迟 import 钩子（让 monkeypatch 能 setattr 替换）────────────
#
# fetch_* 函数通过这两个函数取 akshare / yfinance。测试可以
# `monkeypatch.setattr(f, "_get_ak", lambda: fake_module)` 来 mock 失败路径。
# 模块级 import 不可 mock（import 是一次性的，setattr 不影响函数内 import）。


def _get_ak():
    import akshare as ak
    return ak


def _get_yf():
    import yfinance as yf
    return yf


# ─── 数据类 ──────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class REITValuation:
    """单只 REITs 指数的估值快照。

    字段：
    - index_code: 内部代码（".MSCI_US_REIT" / "932006"）
    - metric: "dy"（股息率，percent number like 3.76）
    - value: 当前 DY（percent，3.76 = 3.76%）
    - dy_percentile_5y: 5Y DY 分位（fraction 0.75 = 75%；高 = DY 高 = 便宜）
    - valuation_percentile_5y: 估值分位 = 1 - dy_percentile（低 = 便宜，跟 PE 一致）
    - data_date: 估值数据本身的日期（不是 fetch 日期）
    - source: 数据源说明
    - error: 抓取失败的错误信息（成功时为 None）
    """

    index_code: str
    metric: str
    value: Decimal | None
    dy_percentile_5y: Decimal | None
    valuation_percentile_5y: Decimal | None
    data_date: date
    source: str
    error: str | None = None


# ─── 抓取函数 ──────────────────────────────────────────


def _percentile_rank(values: list[float], current: float) -> float:
    """算 current 在 values 里的分位（fraction 0~1）。

    用简单 (count_leq / N) — 包括 current 自己。
    values 必须升序或乱序都行（先排序）。
    """
    if not values:
        return float("nan")
    n = len(values)
    leq = sum(1 for v in values if v <= current)
    return leq / n


def _compute_dy_series_percentile(
    hist: object,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, "date | None"]:
    """从 yfinance hist（带 Close + Dividends 列）算月度 DY-TTM 5Y 序列的分位。

    Returns:
        (current_dy, dy_percentile_5y, valuation_percentile_5y, data_date)
        - current_dy: 当前 DY-TTM（percent number，3.76 = 3.76%）
        - dy_percentile_5y: 当前 DY 在月度序列里的分位（高 = DY 高 = 便宜）
        - valuation_percentile_5y: 1 - dy_percentile（低 = 便宜，跟 PE/PS 一致）
        - data_date: 月度序列最后一个月末日期
    """
    import pandas as pd

    if hist is None or hist.empty:
        return None, None, None, None

    divs = hist["Dividends"]
    closes = hist["Close"]

    # 月度重采样：每月末取 close
    hist_m = closes.resample("ME").last().dropna()
    if hist_m.empty:
        return None, None, None, None

    # 对每个月 t：sum dividends in (t-365, t] / close(t)
    dy_series: list[float] = []
    for t in hist_m.index:
        cutoff = t - pd.Timedelta(days=365)
        trailing = divs[(divs.index > cutoff) & (divs.index <= t)].sum()
        close = hist_m.loc[t]
        if close and close > 0:
            dy_series.append((trailing / close) * 100)

    if not dy_series:
        return None, None, None, None

    current_dy = dy_series[-1]
    dy_pct = _percentile_rank(dy_series, current_dy)
    # 估值分位：翻转（高 DY = 低估值 = 便宜 → 跟 PE/PS 规则一致）
    val_pct = 1.0 - dy_pct

    return (
        Decimal(str(round(current_dy, 4))),
        Decimal(str(round(dy_pct, 4))),
        Decimal(str(round(val_pct, 4))),
        hist_m.index[-1].date(),
    )


def fetch_us_reit() -> REITValuation:
    """拉 US REIT（MSCI US REIT proxy: VNQ）的 DY + 5Y DY 分位。

    用 yfinance VNQ 5 年 daily history 算月度 DY-TTM 序列，再算当前 DY 的分位。

    失败 → 返回的 REITValuation.error 字段非空，value/percentile 为 None。
    """
    try:
        yf = _get_yf()

        t = yf.Ticker(US_REIT_PROXY)
        hist = t.history(period=f"{HISTORY_YEARS}y")
        if hist.empty:
            return REITValuation(
                index_code=".MSCI_US_REIT",
                metric="dy",
                value=None,
                dy_percentile_5y=None,
                valuation_percentile_5y=None,
                data_date=date.today(),
                source=f"yfinance {US_REIT_PROXY}",
                error="yfinance 返回空数据",
            )

        current_dy, dy_pct, val_pct, data_date = _compute_dy_series_percentile(hist)

        if current_dy is None:
            return REITValuation(
                index_code=".MSCI_US_REIT",
                metric="dy",
                value=None,
                dy_percentile_5y=None,
                valuation_percentile_5y=None,
                data_date=date.today(),
                source=f"yfinance {US_REIT_PROXY}",
                error="DY 序列算不出来（数据不足）",
            )

        return REITValuation(
            index_code=".MSCI_US_REIT",
            metric="dy",
            value=current_dy,
            dy_percentile_5y=dy_pct,
            valuation_percentile_5y=val_pct,
            data_date=data_date or date.today(),
            source=f"yfinance {US_REIT_PROXY}（Vanguard REIT ETF 代理 MSCI US REIT，"
            f"trailing 12M dividends / close，月度 DY-TTM 序列算 5Y 分位）",
        )
    except Exception as e:
        return REITValuation(
            index_code=".MSCI_US_REIT",
            metric="dy",
            value=None,
            dy_percentile_5y=None,
            valuation_percentile_5y=None,
            data_date=date.today(),
            source=f"yfinance {US_REIT_PROXY}",
            error=f"{type(e).__name__}: {e}"[:200],
        )


def fetch_cn_reit() -> REITValuation:
    """拉 CN REIT（932006 中证 REITs）的当前 DY（akshare）+ 5Y DY 分位（yfinance 508000.SS）。

    当前 DY 用 akshare（更准，对应指数本身）；
    5Y DY 分位用 508000.SS 月度 DY-TTM 序列（华夏 REITs ETF，代理 932006）。
    """
    dy_value: Decimal | None = None
    dy_date: date | None = None
    dy_source = "akshare stock_zh_index_value_csindex(932006)"
    dy_error: str | None = None

    dy_pct: Decimal | None = None
    val_pct: Decimal | None = None
    pct_date: date | None = None
    pct_source = f"yfinance {CN_REIT_PROXY}（华夏 REITs ETF 代理 932006，月度 DY-TTM 5Y 分位）"
    pct_error: str | None = None

    # 1. 当前 DY（akshare）
    try:
        ak = _get_ak()
        df = ak.stock_zh_index_value_csindex(symbol="932006")
        if df is None or df.empty or "股息率1" not in df.columns:
            dy_error = "akshare 返回空数据"
        else:
            latest = df.tail(1).iloc[0]
            dy_pct_val = float(latest["股息率1"])
            if dy_pct_val != dy_pct_val:  # NaN
                dy_error = "akshare DY 字段为 NaN"
            else:
                dy_value = Decimal(str(round(dy_pct_val, 4)))
                dy_date = date.fromisoformat(str(latest["日期"]))
    except Exception as e:
        dy_error = f"akshare {type(e).__name__}: {e}"[:150]

    # 2. 5Y DY 分位（yfinance 508000.SS 月度 DY-TTM 序列）
    try:
        yf = _get_yf()
        t = yf.Ticker(CN_REIT_PROXY)
        hist = t.history(period=f"{HISTORY_YEARS}y")
        if hist.empty:
            pct_error = "yfinance 返回空数据"
        else:
            _, dy_pct, val_pct, pct_date = _compute_dy_series_percentile(hist)
            if dy_pct is None:
                pct_error = "DY 序列算不出来（数据不足）"
    except Exception as e:
        pct_error = f"yfinance {type(e).__name__}: {e}"[:150]

    # 拼装
    if dy_value is None and val_pct is None:
        error = f"akshare: {dy_error}; yfinance: {pct_error}"
        return REITValuation(
            index_code="932006",
            metric="dy",
            value=None,
            dy_percentile_5y=None,
            valuation_percentile_5y=None,
            data_date=date.today(),
            source=f"{dy_source} + {pct_source}",
            error=error,
        )

    # data_date 取两者中较新的（更接近"现在"）
    candidates = [d for d in (dy_date, pct_date) if d is not None]
    final_date = max(candidates) if candidates else date.today()

    error_parts: list[str] = []
    if dy_value is None:
        error_parts.append(f"DY 失败: {dy_error}")
    if val_pct is None:
        error_parts.append(f"分位失败: {pct_error}")
    error_msg = "; ".join(error_parts) if error_parts else None

    return REITValuation(
        index_code="932006",
        metric="dy",
        value=dy_value,
        dy_percentile_5y=dy_pct,
        valuation_percentile_5y=val_pct,
        data_date=final_date,
        source=f"{dy_source} + {pct_source}",
        error=error_msg,
    )


# ─── 主入口 ──────────────────────────────────────────


def fetch_all() -> dict[str, REITValuation]:
    """拉所有 REITs 指数的估值。返回 {index_code: REITValuation}。"""
    return {
        ".MSCI_US_REIT": fetch_us_reit(),
        "932006": fetch_cn_reit(),
    }


def to_json(snapshots: dict[str, REITValuation], fetched_at: datetime | None = None) -> dict:
    """REITValuation → JSON 友好 dict。

    输出字段：
    - metric: "dy"
    - value: 当前 DY（percent number 字符串）
    - dy_percentile_5y: 原始 DY 分位（高 = 便宜）
    - valuation_percentile_5y: 1 - dy_percentile（低 = 便宜）
    - data_date, source, error
    """
    if fetched_at is None:
        bj = timezone(timedelta(hours=8))
        fetched_at = datetime.now(bj)
    return {
        "fetched_at": fetched_at.isoformat(),
        "data_date": max(s.data_date for s in snapshots.values()).isoformat(),
        "indices": {
            code: {
                "metric": s.metric,
                "value": str(s.value) if s.value is not None else None,
                "dy_percentile_5y": str(s.dy_percentile_5y) if s.dy_percentile_5y is not None else None,
                "valuation_percentile_5y": (
                    str(s.valuation_percentile_5y)
                    if s.valuation_percentile_5y is not None
                    else None
                ),
                "data_date": s.data_date.isoformat(),
                "source": s.source,
                "error": s.error,
            }
            for code, s in snapshots.items()
        },
    }


def save_snapshot(snapshots: dict[str, REITValuation]) -> Path:
    """写 JSON 到 DATA_DIR/YYYY-MM-DD.json，返回文件路径。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = DATA_DIR / f"{today}.json"
    payload = to_json(snapshots)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_snapshot(path: Path) -> dict[str, REITValuation]:
    """从 JSON 文件读回 REITValuation 字典。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, REITValuation] = {}
    for code, entry in raw.get("indices", {}).items():
        out[code] = REITValuation(
            index_code=code,
            metric=entry["metric"],
            value=Decimal(entry["value"]) if entry.get("value") else None,
            dy_percentile_5y=Decimal(entry["dy_percentile_5y"])
            if entry.get("dy_percentile_5y")
            else None,
            valuation_percentile_5y=Decimal(entry["valuation_percentile_5y"])
            if entry.get("valuation_percentile_5y")
            else None,
            data_date=date.fromisoformat(entry["data_date"]),
            source=entry["source"],
            error=entry.get("error"),
        )
    return out


def load_latest_snapshot() -> tuple[Path, dict[str, REITValuation]] | None:
    """读 DATA_DIR 里最新的 JSON 快照。None = 没有可用快照。"""
    if not DATA_DIR.exists():
        return None
    files = sorted(DATA_DIR.glob("*.json"), reverse=True)
    if not files:
        return None
    latest = files[0]
    return latest, load_snapshot(latest)


# ─── CLI 入口（由 cli.py 调用）────────────────────────────────


def run_fetch_cli() -> str:
    """CLI 入口：fetch + save + 打印摘要。返回 status 字符串。

    不抛异常（失败时返回 error 字符串，exit code 由调用方决定）。
    """
    snapshots = fetch_all()
    path = save_snapshot(snapshots)

    lines: list[str] = []
    lines.append(f"✓ 写入 {path}")
    for code, s in snapshots.items():
        if s.error:
            lines.append(f"  ✗ {code}: {s.error}")
        else:
            dy_str = f"{float(s.value):.2f}%" if s.value is not None else "—"
            # 显示 DY 原始分位（高 = 便宜）— 跟 card 一致
            dy_pct_str = (
                f"{float(s.dy_percentile_5y) * 100:.2f}%"
                if s.dy_percentile_5y is not None
                else "—"
            )
            val_pct_str = (
                f"{float(s.valuation_percentile_5y) * 100:.2f}%"
                if s.valuation_percentile_5y is not None
                else "—"
            )
            lines.append(
                f"  ✓ {code}: DY {dy_str} / DY 5Y分位 {dy_pct_str} "
                f"(估值分位 {val_pct_str}) / 数据日期 {s.data_date.isoformat()}"
            )
    return "\n".join(lines)


__all__ = [
    "DATA_DIR",
    "REITValuation",
    "_compute_dy_series_percentile",
    "fetch_us_reit",
    "fetch_cn_reit",
    "fetch_all",
    "to_json",
    "save_snapshot",
    "load_snapshot",
    "load_latest_snapshot",
    "run_fetch_cli",
]
