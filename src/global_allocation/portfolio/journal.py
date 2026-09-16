"""实盘账本业务逻辑。

参照 specs/090-portfolio-journal.md。

PortfolioJournal = db + price_source 上的一层薄封装：
  - add_fund / record_buy / record_sell
  - compute_holdings（BUY - SELL 聚合 + 加权平均成本 + 最新市值 + 浮动盈亏）
  - take_snapshot（拍一张快照 + 算周/累计收益）
  - list_transactions（带过滤）
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from global_allocation.models import AssetClass
from global_allocation.portfolio.db import PortfolioDB
from global_allocation.portfolio.models import (
    Fund,
    Holding,
    Transaction,
    TransactionSide,
    WeeklySnapshot,
)
from global_allocation.portfolio.valuation import PriceSource


class PortfolioJournal:
    """实盘账本业务逻辑入口。"""

    def __init__(self, db: PortfolioDB, price_source: PriceSource) -> None:
        self._db = db
        self._price_source = price_source

    # ─── fund management ───

    def add_fund(
        self,
        code: str,
        name: str,
        asset_class: AssetClass,
    ) -> Fund:
        """注册一个基金。重复注册 → 覆盖 name / asset_class。"""
        fund = Fund(code=code, name=name, asset_class=asset_class)
        self._db.upsert_fund(fund)
        return fund

    def get_fund(self, code: str) -> Fund | None:
        return self._db.get_fund(code)

    def list_funds(self) -> list[Fund]:
        return self._db.list_funds()

    # ─── transactions ───

    def record_buy(
        self,
        fund_code: str,
        trade_date: date,
        shares: Decimal,
        price: Decimal,
        fee: Decimal = Decimal("0"),
        strategy: str | None = None,
        tags: list[str] | None = None,
        note: str | None = None,
    ) -> Transaction:
        """记录一笔买入。"""
        return self._record(
            side=TransactionSide.BUY,
            fund_code=fund_code,
            trade_date=trade_date,
            shares=shares,
            price=price,
            fee=fee,
            strategy=strategy,
            tags=tags or [],
            note=note,
        )

    def record_sell(
        self,
        fund_code: str,
        trade_date: date,
        shares: Decimal,
        price: Decimal,
        fee: Decimal = Decimal("0"),
        strategy: str | None = None,
        tags: list[str] | None = None,
        note: str | None = None,
    ) -> Transaction:
        """记录一笔卖出。如果卖出超过当前持仓，抛 ValueError。"""
        return self._record(
            side=TransactionSide.SELL,
            fund_code=fund_code,
            trade_date=trade_date,
            shares=shares,
            price=price,
            fee=fee,
            strategy=strategy,
            tags=tags or [],
            note=note,
        )

    def list_transactions(
        self,
        fund_code: str | None = None,
        tag: str | None = None,
        strategy: str | None = None,
    ) -> list[Transaction]:
        return self._db.list_transactions(fund_code=fund_code, tag=tag, strategy=strategy)

    # ─── holdings（BUY - SELL 聚合） ───

    def compute_holdings(self) -> list[Holding]:
        """聚合当前所有持仓（shares > 0）。"""
        all_txs = self._db.list_transactions()
        if not all_txs:
            return []

        # 按 fund_code 聚合
        by_fund: dict[str, dict[str, Decimal]] = {}
        for tx in all_txs:
            agg = by_fund.setdefault(
                tx.fund_code,
                {"shares": Decimal("0"), "buy_value": Decimal("0"), "buy_fee": Decimal("0")},
            )
            if tx.side == TransactionSide.BUY:
                agg["shares"] += tx.shares
                agg["buy_value"] += tx.shares * tx.price
                agg["buy_fee"] += tx.fee
            else:  # SELL
                agg["shares"] -= tx.shares

        # 查 fund + 价格
        holdings: list[Holding] = []
        for code, agg in by_fund.items():
            net_shares = agg["shares"]
            if net_shares <= 0:
                continue  # 已清仓
            fund = self._db.get_fund(code)
            if fund is None:
                continue  # 基金已删除
            # 加权平均成本 = (buy_value + buy_fee) / shares
            avg_cost = (agg["buy_value"] + agg["buy_fee"]) / net_shares
            market_price = self._price_source.get_price(code, date.today())
            holdings.append(
                Holding(
                    fund=fund,
                    shares=net_shares,
                    avg_cost=avg_cost,
                    market_price=market_price,
                )
            )
        return holdings

    # ─── snapshot ───

    def take_snapshot(
        self,
        week_end_date: date,
    ) -> WeeklySnapshot:
        """拍一张快照。同一 week_end_date 二次调用是覆盖（UNIQUE）。"""
        holdings = self.compute_holdings()
        if not holdings:
            raise ValueError("没有持仓，跳过 snapshot")

        # 所有 holdings 必须有 market_price，否则不让拍
        missing_prices = [h.fund.code for h in holdings if h.market_price is None]
        if missing_prices:
            raise ValueError(f"以下基金拿不到最新价格：{missing_prices}")

        total_value = sum((h.market_value for h in holdings if h.market_value is not None), Decimal("0"))

        # 周涨跌 vs 上一个快照
        prev = self._db.get_latest_snapshot_before(week_end_date)
        if prev is not None and prev.total_value > 0:
            week_return = total_value / prev.total_value - Decimal("1")
        else:
            week_return = Decimal("0")

        # 累计涨跌幅 vs 第一个有 total_value 的快照
        first = self._db.get_first_snapshot_with_value()
        if first is not None and first.week_end_date != week_end_date and first.total_value > 0:
            cumulative_return = total_value / first.total_value - Decimal("1")
        else:
            cumulative_return = Decimal("0")

        # holdings_json
        holdings_data: list[dict[str, Any]] = []
        for h in holdings:
            weight = (
                h.market_value / total_value if total_value > 0 else Decimal("0")  # type: ignore[operator]
            )
            holdings_data.append(
                {
                    "fund_code": h.fund.code,
                    "fund_name": h.fund.name,
                    "shares": str(h.shares),
                    "avg_cost": str(h.avg_cost),
                    "market_price": str(h.market_price) if h.market_price is not None else None,
                    "market_value": str(h.market_value) if h.market_value is not None else None,
                    "weight": str(weight),
                    "pnl_pct": str(h.unrealized_pnl_pct) if h.unrealized_pnl_pct is not None else None,
                }
            )

        snap = WeeklySnapshot(
            week_end_date=week_end_date,
            total_value=total_value,
            week_return=week_return.quantize(Decimal("0.000001")),
            cumulative_return=cumulative_return.quantize(Decimal("0.000001")),
            holdings_json=json.dumps(holdings_data, ensure_ascii=False),
            created_at=datetime.now(),
        )
        self._db.upsert_snapshot(snap)
        return snap

    def get_latest_snapshot(self) -> WeeklySnapshot | None:
        return self._db.get_latest_snapshot()

    def list_snapshots(self) -> list[WeeklySnapshot]:
        return self._db.list_snapshots()

    # ─── import ───

    def import_holdings(
        self,
        holdings: list[dict[str, Any]],
    ) -> int:
        """从当前快照（券商截图 / app 导出）批量导入持仓。

        每条 holdings 字段：
          - code: str
          - name: str
          - asset_class: AssetClass
          - current_value: str | Decimal  当前市值
          - cumulative_pnl: str | Decimal  累计盈亏（负数 = 亏损）

        每条 holdings 会：
          1. upsert_fund
          2. 写一笔合成 buy 交易：date=today, shares=current_value / current_price,
             price=current_price, fee=0, strategy="[imported] baseline",
             tags=["import"]
          3. 跳过 current_value=0 或拿不到当前价的

        返回成功导入的条数。
        """
        imported = 0
        for h in holdings:
            code = h["code"]
            name = h["name"]
            asset_class = h["asset_class"]
            current_value = Decimal(str(h["current_value"]))
            # cumulative_pnl 暂不写库（合成 buy 在当前价买入 → cost_basis = current_value）

            if current_value <= 0:
                continue

            market_price = self._price_source.get_price(code, date.today())
            if market_price is None or market_price <= 0:
                continue

            self.add_fund(code, name, asset_class)

            # shares = current_value / market_price（保留 4 位小数，模拟券商精度）
            shares = (current_value / market_price).quantize(Decimal("0.0001"))

            tx = Transaction(
                fund_code=code,
                side=TransactionSide.BUY,
                date=date.today(),
                shares=shares,
                price=market_price,
                fee=Decimal("0"),
                strategy="[imported] baseline",
                tags=["import"],
                note=None,
                created_at=datetime.now(),
            )
            self._db.insert_transaction(tx)
            imported += 1
        return imported

    # ─── private ───

    def _record(
        self,
        side: TransactionSide,
        fund_code: str,
        trade_date: date,
        shares: Decimal,
        price: Decimal,
        fee: Decimal,
        strategy: str | None,
        tags: list[str],
        note: str | None,
    ) -> Transaction:
        fund = self._db.get_fund(fund_code)
        if fund is None:
            raise ValueError(f"基金 {fund_code} 未登记，请先 `gap portfolio fund add`")

        if shares <= 0:
            raise ValueError(f"份额必须 > 0，实际 {shares}")
        if price <= 0:
            raise ValueError(f"价格必须 > 0，实际 {price}")
        if fee < 0:
            raise ValueError(f"手续费不能为负，实际 {fee}")

        if side == TransactionSide.SELL:
            # 校验持仓充足
            txs = self._db.list_transactions(fund_code=fund_code)
            net = sum(
                (t.shares if t.side == TransactionSide.BUY else -t.shares)
                for t in txs
            )
            if net < shares:
                raise ValueError(
                    f"持仓不足：{fund_code} 当前 {net} 份，要卖 {shares} 份"
                )

        tx = Transaction(
            fund_code=fund_code,
            side=side,
            date=trade_date,
            shares=shares,
            price=price,
            fee=fee,
            strategy=strategy,
            tags=tags,
            note=note,
            created_at=datetime.now(),
        )
        tx_id = self._db.insert_transaction(tx)
        return tx.model_copy(update={"id": tx_id})


__all__ = ["PortfolioJournal"]
