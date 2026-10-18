"""黄金估值指标自动拉取（spec 099 — liubo 2026-10-08 拍板综合分）。

数据源：
- 金价：akshare.spot_golden_benchmark_sge（日频 SGE Au99.99）
  - 5 年分位：月度均价序列，当前价在序列里的位置（低 = 便宜，跟 PE 一样方向）
- 实际利率：FRED DFII10（10Y TIPS 实际利率，日频 CSV）
  - 1/r = 1/0.0291 ≈ 34.36（高实际利率 = 金价压力大）
  - 实际利率 5 档评分：< 0% = 5 分（极便宜）, 0-1% = 4 分, 1-2% = 3 分, 2-3% = 2 分, > 3% = 1 分

输出：data/gold_valuation/YYYY-MM-DD.json
失败：返回的 GoldValuation.error 字段非空，调用方决定怎么处理。

liubo 2026-10-18 拍板：每次发周报前自动 fetch + 注入 GOLD_SNAPSHOT_BY_FUND（跟 fetch_reit_valuation 同模式）。
"""

from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.request import urlopen


# ─── 配置 ──────────────────────────────────────────

# 输出目录（XDG 兼容）
DATA_DIR = Path(
    os.environ.get(
        "GAP_GOLD_VALUATION_DIR",
        str(Path.home() / ".local" / "share" / "gap" / "gold_valuation"),
    )
)

# FRED DFII10 CSV URL（10Y TIPS 实际利率）
FRED_DFII10_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"

# 5 年窗口
HISTORY_YEARS = 5


# ─── 延迟 import 钩子（让 monkeypatch 能 setattr 替换）────────────


def _get_ak():
    import akshare as ak
    return ak


# ─── 数据类 ──────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GoldValuation:
    """黄金估值快照。

    字段：
    - gold_price: 当前 SGE Au99.99 价格（CNY/g，None = 数据缺失）
    - gold_pct_5y: 5 年分位（fraction 0.61 = 61%；低 = 便宜，跟 PE 一致）
    - real_yield_dfii10: FRED DFII10 当前值（fraction 0.0284 = 2.84%）
    - data_date: 数据对应的最新交易日
    - source: 数据来源描述
    - error: 错误信息（空 = 成功）
    """

    gold_price: Decimal | None
    gold_pct_5y: Decimal | None
    real_yield_dfii10: Decimal | None
    data_date: date
    source: str
    error: str = ""


# ─── 核心拉取函数 ──────────────────────────────────────────


def _fetch_real_yield_dfii10() -> tuple[Decimal | None, date | None, str]:
    """拉 FRED DFII10 最新值。返回 (real_yield_fraction, data_date, error_str)。

    real_yield_fraction: 0.0284 = 2.84%（已转 fraction）。
    """
    try:
        with urlopen(FRED_DFII10_URL, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
        # 跳过 header 行 (DATE,VALUE)
        reader = csv.reader(io.StringIO(raw))
        rows = [r for r in reader if r and r[0] not in ("DATE", "VALUE")]
        # rows[-1] 是最新一行（CSV 按日期升序）
        for row in reversed(rows):
            if len(row) >= 2 and row[1].strip() not in ("", "."):
                ry_str = row[1].strip()
                data_date = date.fromisoformat(row[0])
                # FRED 是 percent number (2.84 = 2.84%) → 转 fraction (0.0284)
                return (Decimal(ry_str) / Decimal("100"), data_date, "")
        return (None, None, "FRED DFII10 无最新非空数据")
    except Exception as e:
        return (None, None, f"FRED DFII10 拉取失败：{type(e).__name__}: {e}")


def _compute_gold_pct_5y(gold_series: list[tuple[date, Decimal]]) -> Decimal | None:
    """金价 5 年分位 = 当前价在序列里的位置（fraction, 0.61 = 61%）。

    跟 PE 分位算法一致（liubo 2026-10-08 拍板）：高 = 贵，低 = 便宜。

    输入：gold_series = [(date, price_cny_per_g), ...]，按日期升序。
    输出：current price 在 sorted(prices) 里的 percentile rank。
    """
    if len(gold_series) < 30:
        return None
    prices = sorted(float(p) for _, p in gold_series)
    current_price = float(gold_series[-1][1])
    # 严格小于的个数 / 总数（"min" method，跟其他 percentile 一致）
    less = sum(1 for p in prices if p < current_price)
    return Decimal(less) / Decimal(len(prices))


def fetch_gold() -> GoldValuation:
    """拉黄金估值：SGE Au99.99 当前价 + 5年分位 + FRED DFII10 实际利率。

    失败 → 返回的 GoldValuation.error 字段非空，value/percentile 为 None。
    """
    today = date.today()
    errors: list[str] = []

    # 1. 金价 + 5 年分位
    gold_price: Decimal | None = None
    gold_pct: Decimal | None = None
    price_date = today
    try:
        ak = _get_ak()
        df = ak.spot_golden_benchmark_sge()
        if df.empty:
            errors.append("akshare 金价返回空数据")
        else:
            # 晚盘价为日终结算价（最权威）
            # 数据按日期升序
            series: list[tuple[date, Decimal]] = []
            for _, row in df.iterrows():
                d = date.fromisoformat(str(row["交易时间"]))
                p = Decimal(str(row["晚盘价"]))
                series.append((d, p))
            if len(series) < 30:
                errors.append(f"akshare 金价历史序列太短（{len(series)} 条）")
            else:
                gold_price = series[-1][1]
                price_date = series[-1][0]
                gold_pct = _compute_gold_pct_5y(series)
    except Exception as e:
        errors.append(f"akshare 金价拉取失败：{type(e).__name__}: {e}")

    # 2. FRED DFII10
    real_yield, ry_data_date, ry_err = _fetch_real_yield_dfii10()
    if ry_err:
        errors.append(ry_err)

    # 数据日期取两个数据源里更新的
    if ry_data_date and ry_data_date > price_date:
        data_date = ry_data_date

    error_str = "; ".join(errors) if errors else ""
    return GoldValuation(
        gold_price=gold_price,
        gold_pct_5y=gold_pct,
        real_yield_dfii10=real_yield,
        data_date=data_date,
        source=(
            "akshare.spot_golden_benchmark_sge（SGE Au99.99）+ "
            "FRED DFII10（10Y TIPS 实际利率）"
        ),
        error=error_str,
    )


def fetch_all() -> dict[str, GoldValuation]:
    """拉黄金估值。返回 {fund_code: GoldValuation}。

    当前只有 1 只黄金基金（000216），所以返回 1 个 key。
    """
    return {"000216": fetch_gold()}


# ─── JSON 序列化 ──────────────────────────────────────────


def to_json(snapshots: dict[str, GoldValuation], fetched_at: datetime | None = None) -> dict:
    """GoldValuation → JSON 友好 dict。

    输出字段：
    - gold_price: 当前 SGE Au99.99 价格（CNY/g）
    - gold_pct_5y: 5 年分位（低 = 便宜）
    - real_yield_dfii10: FRED DFII10 实际利率（fraction）
    - data_date, source, error
    """
    if fetched_at is None:
        bj = timezone(timedelta(hours=8))
        fetched_at = datetime.now(bj)
    return {
        "fetched_at": fetched_at.isoformat(),
        "data_date": max(s.data_date for s in snapshots.values()).isoformat(),
        "funds": {
            code: {
                "gold_price": str(s.gold_price) if s.gold_price is not None else None,
                "gold_pct_5y": str(s.gold_pct_5y) if s.gold_pct_5y is not None else None,
                "real_yield_dfii10": (
                    str(s.real_yield_dfii10) if s.real_yield_dfii10 is not None else None
                ),
                "data_date": s.data_date.isoformat(),
                "source": s.source,
                "error": s.error,
            }
            for code, s in snapshots.items()
        },
    }


def save_snapshot(snapshots: dict[str, GoldValuation]) -> Path:
    """写 JSON 到 DATA_DIR/YYYY-MM-DD.json，返回文件路径。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = DATA_DIR / f"{today}.json"
    payload = to_json(snapshots)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_snapshot(path: Path) -> dict[str, GoldValuation]:
    """从 JSON 文件读回 GoldValuation 字典。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, GoldValuation] = {}
    for code, raw in payload["funds"].items():
        result[code] = GoldValuation(
            gold_price=Decimal(raw["gold_price"]) if raw.get("gold_price") else None,
            gold_pct_5y=Decimal(raw["gold_pct_5y"]) if raw.get("gold_pct_5y") else None,
            real_yield_dfii10=(
                Decimal(raw["real_yield_dfii10"])
                if raw.get("real_yield_dfii10")
                else None
            ),
            data_date=date.fromisoformat(raw["data_date"]),
            source=raw.get("source", ""),
            error=raw.get("error", ""),
        )
    return result


def load_latest_snapshot() -> tuple[Path, dict[str, GoldValuation]] | None:
    """加载 DATA_DIR 最新一份 JSON 快照。

    Returns:
        (path, snapshots) 或 None（无快照）
    """
    if not DATA_DIR.exists():
        return None
    files = sorted(DATA_DIR.glob("*.json"), reverse=True)
    if not files:
        return None
    latest = files[0]
    return (latest, load_snapshot(latest))


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
            price_str = f"{float(s.gold_price):.2f} CNY/g" if s.gold_price is not None else "—"
            pct_str = (
                f"{float(s.gold_pct_5y) * 100:.2f}%"
                if s.gold_pct_5y is not None
                else "—"
            )
            ry_str = (
                f"{float(s.real_yield_dfii10) * 100:.2f}%"
                if s.real_yield_dfii10 is not None
                else "—"
            )
            lines.append(
                f"  ✓ {code}: 金价 {price_str} / 5年分位 {pct_str} / "
                f"实际利率 {ry_str} / 数据日期 {s.data_date.isoformat()}"
            )
    return "\n".join(lines)


__all__ = [
    "DATA_DIR",
    "GoldValuation",
    "fetch_gold",
    "fetch_all",
    "to_json",
    "save_snapshot",
    "load_snapshot",
    "load_latest_snapshot",
    "run_fetch_cli",
]