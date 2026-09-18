"""Render a preview of the weekly report card from the demo portfolio.db.

Run:
  uv run python scripts/preview_weekly_card.py /tmp/liubo-show/gap/portfolio.db
"""

from __future__ import annotations

import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

# 让脚本能从 src/ 找到 package
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from global_allocation.portfolio.breakdown import (  # noqa: E402
    DISPLAY_NAME,
    SUBCLASS_BY_CODE,
    SwensenClass,
    compute_breakdown,
)
from global_allocation.portfolio.card import (  # noqa: E402
    build_portfolio_card,
    card_to_json,
)
from global_allocation.portfolio.db import PortfolioDB  # noqa: E402
from global_allocation.portfolio.journal import PortfolioJournal  # noqa: E402
from global_allocation.portfolio.models import Holding, Fund  # noqa: E402
from global_allocation.portfolio.valuation import PriceSource  # noqa: E402
from global_allocation.models import AssetClass  # noqa: E402


class StaticPriceSource:
    """Demo 用固定价格（按当前市值反推每只基金 market_value）。"""

    def __init__(self, prices: dict[str, Decimal]) -> None:
        self._prices = prices

    def get_price(self, code: str, on: date) -> Decimal | None:
        return self._prices.get(code)


# 从上轮分类时算的当前市值反推：市值 / 估算份额 ≈ 当前价格
# 这些价格是估算值（仅用于 demo），真实情况由 akshare 拉
DEMO_PRICES: dict[str, Decimal] = {
    "013310": Decimal("1.65"),   # 华夏科创创业50
    "022434": Decimal("1.15"),   # 南方中证A500
    "008114": Decimal("1.35"),   # 天弘中证红利低波动100
    "017644": Decimal("1.85"),   # 博道中证1000指数增强
    "022424": Decimal("1.16"),   # 广发中证A500
    "014532": Decimal("1.40"),   # 易方达MSCI中国A50
    "004098": Decimal("1.20"),   # 前海开源港股通股息率50强
    "013127": Decimal("0.95"),   # 汇添富恒生科技
    "006809": Decimal("1.25"),   # 泰康香港银行指数
    "519981": Decimal("4.50"),   # 长信标普100
    "018966": Decimal("3.20"),   # 汇添富纳100
    "539001": Decimal("3.50"),   # 建信纳100
    "017641": Decimal("2.40"),   # 摩根标普500
    "016452": Decimal("3.10"),   # 南方纳100
    "019524": Decimal("3.30"),   # 华泰柏瑞纳100
    "017730": Decimal("1.50"),   # 嘉实全球产业升级
    "016664": Decimal("1.20"),   # 天弘全球高端制造
    "006373": Decimal("2.10"),   # 国富全球科技互联
    "457001": Decimal("1.70"),   # 国富亚洲机会
    "378006": Decimal("1.60"),   # 摩根全球新兴市场
    "028277": Decimal("3.50"),   # 华夏中证REITs全收益
    "160140": Decimal("1.55"),   # 南方道琼斯美国精选REIT
    "008505": Decimal("1.05"),   # 浙商中短债A
    "004827": Decimal("1.05"),   # 平安中短债
    "003547": Decimal("1.10"),   # 鹏华丰禄
    "000931": Decimal("1.08"),   # 国寿安保尊益信用纯债
    "100050": Decimal("1.02"),   # 富国全球债券
    "007360": Decimal("1.03"),   # 易方达中短期美元债
    "003385": Decimal("1.04"),   # 工银全球美元债
    "000216": Decimal("2.30"),   # 华安黄金ETF联接A
    "004137": Decimal("1.00"),   # 博时合惠货币B
}


def render_breakdown_only(journal: PortfolioJournal) -> str:
    """只渲染 持仓现状 部分，便于 botmux 贴飞书。"""
    rows = compute_breakdown(journal)
    total_value = sum(
        (r["value"] for r in rows if r["count"] > 0),
        Decimal("0"),
    )
    lines = [
        "**持仓现状（按 Swensen 大类资产）**",
        "",
        "| 大类资产 | 基金数 | 市值(¥) | 占比 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for r in rows:
        if r["count"] == 0:
            continue
        lines.append(
            f"| {r['display_name']} | {r['count']} | "
            f"{float(r['value']):,.2f} | {float(r['weight']) * 100:.2f}% |"
        )
    lines.append(
        f"\n合计：{len(rows)} 个大类 / {float(total_value):,.2f} CNY"
    )
    return "\n".join(lines)


def main() -> None:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "/tmp/liubo-show/gap/portfolio.db"
    )
    journal = PortfolioJournal(
        db=PortfolioDB(path=db_path),
        price_source=StaticPriceSource(DEMO_PRICES),
    )
    # 打印 持仓现状 部分
    print(render_breakdown_only(journal))
    print()
    print("--- Full card JSON ---")
    # 拿 holdings 数 / 总市值作为 sanity check
    holdings = journal.compute_holdings()
    print(
        f"# {len(holdings)} 个持仓 / "
        f"总市值 {float(sum((h.market_value or Decimal('0') for h in holdings), Decimal('0'))):,.2f} CNY"
    )
    # 把整张卡片 dump 给前端排错
    card = build_portfolio_card(journal, title="实盘持仓 9 月")
    print(card_to_json(card))


if __name__ == "__main__":
    main()