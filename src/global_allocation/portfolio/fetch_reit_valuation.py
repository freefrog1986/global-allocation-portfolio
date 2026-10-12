"""REITs 估值指标自动拉取（spec REITs-DY — liubo 2026-10-12 拍板）。

数据源：
- US REIT：yfinance VNQ（Vanguard REIT ETF，跟踪 MSCI US REIT 接近）
  - 当前 DY-TTM = trailing 12M dividends / current close
  - 5Y price percentile = 当前价在 5Y 日线收盘价序列里的分位
- CN REIT 932006：akshare stock_zh_index_value_csindex（最新 DY）
  + yfinance 508000.SS（华夏 REITs ETF，5Y 日线）算价分位

输出：data/reit_valuation/YYYY-MM-DD.json
失败：返回 None + log warning（不抛异常；周五 cron 跑失败也继续出报告）

liubo 2026-10-12 拍板：放弃 P/FFO / P/NAV（NAREIT 月报 + 招商月报难抓、6 周未更新），
改用股息率（DY）做 REITs 估值指标。每周五 cron 拉一次，周报显示「DY X.XX% / 5年价分位 Y% / 数据日期」。
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
    - price_percentile_5y: 5年价分位（fraction 0.42 = 42%）
    - data_date: 估值数据本身的日期（不是 fetch 日期）
    - source: 数据源说明
    - error: 抓取失败的错误信息（成功时为 None）
    """

    index_code: str
    metric: str
    value: Decimal | None
    price_percentile_5y: Decimal | None
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


def fetch_us_reit() -> REITValuation:
    """拉 US REIT（MSCI US REIT proxy: VNQ）的 DY + 5年价分位。

    失败 → 返回的 REITValuation.error 字段非空，value/percentile 为 None。
    """
    try:
        yf = _get_yf()
        import pandas as pd

        t = yf.Ticker(US_REIT_PROXY)
        hist = t.history(period=f"{HISTORY_YEARS}y")
        if hist.empty:
            return REITValuation(
                index_code=".MSCI_US_REIT",
                metric="dy",
                value=None,
                price_percentile_5y=None,
                data_date=date.today(),
                source=f"yfinance {US_REIT_PROXY}",
                error="yfinance 返回空数据",
            )

        # 价分位：用过去 5Y daily close 算 current close 的分位
        closes: list[float] = hist["Close"].dropna().tolist()
        current_price = float(hist["Close"].iloc[-1])
        price_pct = _percentile_rank(closes, current_price)

        # DY-TTM：过去 365 天 dividends / 当前价
        # 兼容 pandas 新旧 API：直接用 .loc 过滤
        cutoff = hist.index.max() - pd.Timedelta(days=365)
        recent = hist.loc[hist.index >= cutoff]
        trailing_divs = float(recent["Dividends"].sum())
        dy_ttm_pct = (trailing_divs / current_price) * 100  # 百分数

        latest_date = hist.index.max().date()
        return REITValuation(
            index_code=".MSCI_US_REIT",
            metric="dy",
            value=Decimal(str(round(dy_ttm_pct, 4))),  # 3.7623 → 3.7623
            price_percentile_5y=Decimal(str(round(price_pct, 4))),
            data_date=latest_date,
            source=f"yfinance {US_REIT_PROXY}（Vanguard REIT ETF 代理 MSCI US REIT，"
            f"trailing 12M dividends / close）",
        )
    except Exception as e:
        return REITValuation(
            index_code=".MSCI_US_REIT",
            metric="dy",
            value=None,
            price_percentile_5y=None,
            data_date=date.today(),
            source=f"yfinance {US_REIT_PROXY}",
            error=f"{type(e).__name__}: {e}"[:200],
        )


def fetch_cn_reit() -> REITValuation:
    """拉 CN REIT（932006 中证 REITs）的 DY（akshare）+ 5年价分位（yfinance 508000.SS）。"""
    dy_value: Decimal | None = None
    dy_date: date | None = None
    dy_source = "akshare stock_zh_index_value_csindex(932006)"
    dy_error: str | None = None

    price_pct: Decimal | None = None
    price_date: date | None = None
    price_source = f"yfinance {CN_REIT_PROXY}（华夏 REITs ETF 代理 932006）"
    price_error: str | None = None

    # 1. 当前 DY（akshare）
    try:
        ak = _get_ak()
        df = ak.stock_zh_index_value_csindex(symbol="932006")
        if df is None or df.empty or "股息率1" not in df.columns:
            dy_error = "akshare 返回空数据"
        else:
            # 最新一行（已按日期升序，tail(1)）
            latest = df.tail(1).iloc[0]
            dy_pct = float(latest["股息率1"])
            if dy_pct != dy_pct:  # NaN
                dy_error = "akshare DY 字段为 NaN"
            else:
                dy_value = Decimal(str(round(dy_pct, 4)))
                # 日期字段是 'YYYY-MM-DD' 字符串
                dy_date = date.fromisoformat(str(latest["日期"]))
    except Exception as e:
        dy_error = f"akshare {type(e).__name__}: {e}"[:150]

    # 2. 5年价分位（yfinance 508000.SS）
    try:
        yf = _get_yf()
        t = yf.Ticker(CN_REIT_PROXY)
        hist = t.history(period=f"{HISTORY_YEARS}y")
        if hist.empty:
            price_error = "yfinance 返回空数据"
        else:
            closes: list[float] = hist["Close"].dropna().tolist()
            current_price = float(hist["Close"].iloc[-1])
            price_pct_dec = Decimal(str(round(_percentile_rank(closes, current_price), 4)))
            price_pct = price_pct_dec
            price_date = hist.index.max().date()
    except Exception as e:
        price_error = f"yfinance {type(e).__name__}: {e}"[:150]

    # 拼装
    if dy_value is None and price_pct is None:
        error = f"akshare: {dy_error}; yfinance: {price_error}"
        return REITValuation(
            index_code="932006",
            metric="dy",
            value=None,
            price_percentile_5y=None,
            data_date=date.today(),
            source=f"{dy_source} + {price_source}",
            error=error,
        )

    # data_date 取两者中较新的（更接近"现在"）
    candidates = [d for d in (dy_date, price_date) if d is not None]
    final_date = max(candidates) if candidates else date.today()

    # partial error: 哪个没拉到就标哪个
    error_parts = []
    if dy_value is None:
        error_parts.append(f"DY 失败: {dy_error}")
    if price_pct is None:
        error_parts.append(f"价分位失败: {price_error}")
    error_msg = "; ".join(error_parts) if error_parts else None

    return REITValuation(
        index_code="932006",
        metric="dy",
        value=dy_value,
        price_percentile_5y=price_pct,
        data_date=final_date,
        source=f"{dy_source} + {price_source}",
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
    """REITValuation → JSON 友好 dict。"""
    if fetched_at is None:
        # 北京时间
        bj = timezone(timedelta(hours=8))
        fetched_at = datetime.now(bj)
    return {
        "fetched_at": fetched_at.isoformat(),
        "data_date": max(s.data_date for s in snapshots.values()).isoformat(),
        "indices": {
            code: {
                "metric": s.metric,
                "value": str(s.value) if s.value is not None else None,
                "price_percentile_5y": str(s.price_percentile_5y) if s.price_percentile_5y is not None else None,
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
            price_percentile_5y=Decimal(entry["price_percentile_5y"])
            if entry.get("price_percentile_5y")
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
            pct_str = (
                f"{float(s.price_percentile_5y) * 100:.2f}%"
                if s.price_percentile_5y is not None
                else "—"
            )
            lines.append(
                f"  ✓ {code}: DY {dy_str} / 5Y价分位 {pct_str} / "
                f"数据日期 {s.data_date.isoformat()}"
            )
    return "\n".join(lines)


__all__ = [
    "DATA_DIR",
    "REITValuation",
    "fetch_us_reit",
    "fetch_cn_reit",
    "fetch_all",
    "to_json",
    "save_snapshot",
    "load_snapshot",
    "load_latest_snapshot",
    "run_fetch_cli",
]
