"""海外指数估值自动拉取（liubo 2026-10-24 拍板「这几个你去自动获取拉数据」）。

背景：大类资产配置组合里有一部分指数不在理杏仁 CSV 覆盖范围内
（.GDAXI 德国 DAX / .N225 日经 225 / .MSCI_EM 全球新兴市场），
以前是 guchacha / worldperatio 的手抄静态值，最新一期停在 2026-09，半年没更新。
liubo 要求这几个改成自动拉。

数据源：yfinance ETF 代理 + 月度 DY-TTM 序列（跟 spec REITs-DY 同一套方法）：
- 估值指标用**股息率 DY**（不是 PE-TTM）—— PE-TTM 的 10 年历史分位没有免费可拉的数据源，
  而 ETF 代理的分红 + 价格能拼出完整的月度 DY-TTM 序列。
- 代理 ETF：
  - .GDAXI  → DAX  （Global X DAX Germany ETF，直接跟踪 DAX 指数）
  - .N225   → EWJ  （iShares MSCI Japan ETF，代理日经 225）
  - .FCHI   → CAC.PA（Amundi CAC 40 UCITS ETF，代理法国 CAC 40）
  - .MSCI_EM → EEM （iShares MSCI Emerging Markets ETF，代理 MSCI 新兴市场）
- 法国专属的美股 ETF（EWF / FRAK / FLFR / EWPA）在 yfinance 上都已退市，
  只有巴黎上市的 CAC.PA 能用。

分位窗口 10 年（liubo 2026-10-24 拍板「用十年窗口」，跟理杏仁 CSV / 螺丝钉口径统一）。

输出：data/overseas_valuation/YYYY-MM-DD.json
失败：返回的 OverseasValuation.error 字段非空，调用方决定怎么处理。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path


# ─── 配置 ──────────────────────────────────────────

# 输出目录（XDG 兼容）
DATA_DIR = Path(
    os.environ.get(
        "GAP_OVERSEAS_VALUATION_DIR",
        str(Path.home() / ".local" / "share" / "gap" / "overseas_valuation"),
    )
)

# 分位窗口：10 年（liubo 2026-10-24 拍板）
HISTORY_YEARS = 10

# 实际拉取年数：多拉 2 年，保证序列首月的 trailing-12M 分红窗口完整
# （否则前 12 个月分红累计不全 → DY 偏低 → 分位被系统性高估）
FETCH_YEARS = HISTORY_YEARS + 2

# 指数代码 → (代理 ETF ticker, 代理说明)
OVERSEAS_PROXIES: dict[str, tuple[str, str]] = {
    ".GDAXI": ("DAX", "Global X DAX Germany ETF（直接跟踪 DAX 指数）"),
    ".N225": ("EWJ", "iShares MSCI Japan ETF（代理日经 225）"),
    ".FCHI": ("CAC.PA", "Amundi CAC 40 UCITS ETF（代理法国 CAC 40）"),
    ".MSCI_EM": ("EEM", "iShares MSCI Emerging Markets ETF（代理 MSCI 新兴市场）"),
}


# ─── 延迟 import 钩子（让 monkeypatch 能 setattr 替换）────────────


def _get_yf():
    import yfinance as yf
    return yf


# ─── 数据类 ──────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OverseasValuation:
    """海外指数估值快照。

    字段：
    - value: 当前 DY（percent number，2.16 = 2.16%）
    - dy_percentile_10y: 当前 DY 在月度序列里的分位（高 = DY 高 = 便宜）
    - valuation_percentile_10y: 1 - dy_percentile（低 = 便宜，跟 PE/PS 规则一致）
    - data_date: 月度序列最后一个月末日期
    - source: 数据来源描述
    - error: 错误信息（空 = 成功）
    """

    index_code: str
    metric: str
    value: Decimal | None
    dy_percentile_10y: Decimal | None
    valuation_percentile_10y: Decimal | None
    data_date: date
    source: str
    error: str = ""


# ─── 核心计算 ──────────────────────────────────────────


def _percentile_rank(values: list[float], current: float) -> float:
    """算 current 在 values 里的分位（fraction 0~1）。"""
    if not values:
        return float("nan")
    n = len(values)
    leq = sum(1 for v in values if v <= current)
    return leq / n


def _compute_dy_series_percentile(
    hist: object,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, "date | None"]:
    """从 yfinance hist（带 Close + Dividends 列）算月度 DY-TTM 序列的分位。

    Returns:
        (current_dy, dy_percentile_10y, valuation_percentile_10y, data_date)
        - current_dy: 当前 DY-TTM（percent number，2.16 = 2.16%）
        - dy_percentile_10y: 当前 DY 在月度序列里的分位（高 = DY 高 = 便宜）
        - valuation_percentile_10y: 1 - dy_percentile（低 = 便宜，跟 PE/PS 一致）
        - data_date: 月度序列最后一个月末日期

    序列构造（跟 spec REITs-DY 同一套）：
    1. 月度重采样，每月取月末 close
    2. 对每个月 t：sum dividends in (t-365, t] / close(t) = trailing-12M 股息率
    3. **丢掉 trailing 分红合计为 0 的月份** —— 那说明 trailing-12M 窗口没拉全
       （序列开头 / 数据缺口），留着会把 DY 分位系统性拉低 → 估值分位虚高
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
    dy_points: list[tuple[date, float]] = []
    for t in hist_m.index:
        cutoff = t - pd.Timedelta(days=365)
        trailing = divs[(divs.index > cutoff) & (divs.index <= t)].sum()
        close = hist_m.loc[t]
        if close and close > 0 and trailing > 0:
            dy_points.append((t.date(), (trailing / close) * 100))

    if not dy_points:
        return None, None, None, None

    # 只保留最近 HISTORY_YEARS 年（前面多拉的年份是为了让首月 trailing-12M 窗口完整）
    window_start = date.today().year - HISTORY_YEARS
    dy_points = [(d, v) for d, v in dy_points if d.year >= window_start]
    if not dy_points:
        return None, None, None, None
    dy_series = [v for _, v in dy_points]

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


# ─── 抓取函数 ──────────────────────────────────────────


def fetch_index(index_code: str, ticker: str, proxy_note: str) -> OverseasValuation:
    """拉单个海外指数的 DY + 10 年分位。"""
    source = f"yfinance {ticker}（{proxy_note}，trailing 12M dividends / close，月度 DY-TTM 序列算 10Y 分位）"
    try:
        yf = _get_yf()
        tk = yf.Ticker(ticker)
        hist = tk.history(period=f"{FETCH_YEARS}y")
        if hist is None or hist.empty:
            return OverseasValuation(
                index_code=index_code,
                metric="dy",
                value=None,
                dy_percentile_10y=None,
                valuation_percentile_10y=None,
                data_date=date.today(),
                source=source,
                error="yfinance 返回空数据",
            )

        current_dy, dy_pct, val_pct, data_date = _compute_dy_series_percentile(hist)
        if current_dy is None:
            return OverseasValuation(
                index_code=index_code,
                metric="dy",
                value=None,
                dy_percentile_10y=None,
                valuation_percentile_10y=None,
                data_date=date.today(),
                source=source,
                error="DY 序列算不出来（数据不足）",
            )

        return OverseasValuation(
            index_code=index_code,
            metric="dy",
            value=current_dy,
            dy_percentile_10y=dy_pct,
            valuation_percentile_10y=val_pct,
            data_date=data_date or date.today(),
            source=source,
        )
    except Exception as e:
        return OverseasValuation(
            index_code=index_code,
            metric="dy",
            value=None,
            dy_percentile_10y=None,
            valuation_percentile_10y=None,
            data_date=date.today(),
            source=source,
            error=f"{type(e).__name__}: {e}"[:200],
        )


def fetch_all() -> dict[str, OverseasValuation]:
    """拉所有代理海外指数的估值。"""
    return {
        code: fetch_index(code, ticker, note)
        for code, (ticker, note) in OVERSEAS_PROXIES.items()
    }


# ─── 持久化 ──────────────────────────────────────────


def to_json(
    snapshots: dict[str, OverseasValuation],
    fetched_at: datetime | None = None,
) -> dict:
    """OverseasValuation → JSON 友好 dict。"""
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
                "dy_percentile_10y": (
                    str(s.dy_percentile_10y) if s.dy_percentile_10y is not None else None
                ),
                "valuation_percentile_10y": (
                    str(s.valuation_percentile_10y)
                    if s.valuation_percentile_10y is not None
                    else None
                ),
                "data_date": s.data_date.isoformat(),
                "source": s.source,
                "error": s.error,
            }
            for code, s in snapshots.items()
        },
    }


def save_snapshot(snapshots: dict[str, OverseasValuation]) -> Path:
    """写 JSON 到 DATA_DIR/YYYY-MM-DD.json，返回文件路径。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = DATA_DIR / f"{today}.json"
    path.write_text(json.dumps(to_json(snapshots), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_snapshot(path: Path) -> dict[str, OverseasValuation]:
    """从 JSON 文件读回 OverseasValuation 字典。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, OverseasValuation] = {}
    for code, entry in raw.get("indices", {}).items():
        out[code] = OverseasValuation(
            index_code=code,
            metric=entry["metric"],
            value=Decimal(entry["value"]) if entry.get("value") else None,
            dy_percentile_10y=(
                Decimal(entry["dy_percentile_10y"]) if entry.get("dy_percentile_10y") else None
            ),
            valuation_percentile_10y=(
                Decimal(entry["valuation_percentile_10y"])
                if entry.get("valuation_percentile_10y")
                else None
            ),
            data_date=date.fromisoformat(entry["data_date"]),
            source=entry["source"],
            error=entry.get("error"),
        )
    return out


def load_latest_snapshot() -> tuple[Path, dict[str, OverseasValuation]] | None:
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
            val_pct = (
                f"{float(s.valuation_percentile_10y) * 100:.2f}%"
                if s.valuation_percentile_10y is not None
                else "—"
            )
            lines.append(f"  ✓ {code}: DY {dy_str} / 估值分位 {val_pct}（低 = 便宜）/ 数据日期 {s.data_date}")
    return "\n".join(lines)
