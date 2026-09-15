"""回测引擎。

参照 specs/050-backtest-engine.md。

公开 API：
  - BacktestEngine(initial_capital, cost_bps, slippage_bps).run(strategy, prices)
  - compute_risk_parity_weights(prices)  （在 risk_parity.py）
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd

from global_allocation.backtest.metrics import compute_metrics
from global_allocation.backtest.risk_parity import compute_risk_parity_weights
from global_allocation.models import (
    BacktestResult,
    PortfolioSnapshot,
    RebalanceEvent,
    RebalanceRule,
    Strategy,
    Trade,
)


class BacktestEngine:
    """日频回测引擎（事件驱动）。"""

    def __init__(
        self,
        initial_capital: Decimal = Decimal("100000"),
        cost_bps: Decimal = Decimal("10"),  # 0.10% 双向
        slippage_bps: Decimal = Decimal("0"),  # MVP 不模拟
    ) -> None:
        if initial_capital <= 0:
            raise ValueError(f"initial_capital 必须 > 0，实际 {initial_capital}")
        if cost_bps < 0:
            raise ValueError(f"cost_bps 不能为负，实际 {cost_bps}")
        if slippage_bps < 0:
            raise ValueError(f"slippage_bps 不能为负，实际 {slippage_bps}")

        self._initial_capital = initial_capital
        self._cost_bps = cost_bps
        self._slippage_bps = slippage_bps

    # ─── public API ───

    def run(
        self, strategy: Strategy, prices: pd.DataFrame
    ) -> BacktestResult:
        """跑一次回测。

        Args:
            strategy: 策略定义
            prices: 宽表 DataFrame，columns=asset symbol，index=Date，values=Adj Close。

        Returns:
            BacktestResult（equity_curve + snapshots + metrics + rebalance_events）。

        算法：
          day 0（第一行）: 用初始资金按目标权重买入
          for each trading day t:
              if 触发再平衡:
                  target_weights = strategy.weights_at(t)
                  rebalance_to(target_weights, prices[t])
              NAV = sum(shares * prices[t]) + cash
              record snapshot at t

        Edge cases 见 spec 050 边界情况 1-8.
        """
        if prices.empty:
            raise ValueError("prices 不能为空")
        if strategy.target_weights is None or not strategy.target_weights:
            raise ValueError("strategy.target_weights 不能为空")

        # 校准 prices 列与 strategy.assets 对齐（容忍顺序不一致、多余列）
        target_symbols = [tw.asset.symbol for tw in strategy.target_weights]
        missing = [s for s in target_symbols if s not in prices.columns]
        if missing:
            raise ValueError(f"prices 缺下列标的: {missing}")

        # 强制 columns 顺序按 strategy.target_weights（cash 不在 prices 里）
        price_cols = target_symbols  # 只关心策略里的资产
        price_df = prices[price_cols].copy()

        # state
        positions: dict[str, Decimal] = {s: Decimal("0") for s in target_symbols}
        cash: Decimal = self._initial_capital
        snapshots: list[PortfolioSnapshot] = []
        rebalance_events: list[RebalanceEvent] = []

        # risk parity 策略：day 0 用真实风险平价权重替代 build() 的等权
        if strategy.id == "risk_parity":
            # 用第一天的历史数据计算（实际是 prices 全程）
            target_weights_map = compute_risk_parity_weights(price_df)
        else:
            target_weights_map = {
                tw.asset.symbol: tw.weight for tw in strategy.target_weights
            }

        # 遍历每个交易日
        last_rebalance_date: date | None = None
        equity_records: list[dict[str, Any]] = []

        for i, (dt, row) in enumerate(price_df.iterrows()):
            row_dict: dict[str, Decimal] = {}
            for s in target_symbols:
                v = row[s]
                if pd.isna(v):
                    # NaN → 用前一天的收盘价（spec 050 边界 1）
                    if snapshots:
                        prev_prices = {
                            p_sym: Decimal(str(equity_records[-1][f"price_{p_sym}"]))
                            for p_sym in target_symbols
                            if f"price_{p_sym}" in equity_records[-1]
                        }
                        row_dict[s] = prev_prices.get(s, Decimal("0"))
                    else:
                        row_dict[s] = Decimal("0")
                else:
                    row_dict[s] = Decimal(str(float(v)))
            current_dt: date = pd.Timestamp(dt).date()  # type: ignore[arg-type]

            # 处理 NaN：跳过这天的交易（spec 050 边界 1）
            if any(pd.isna(row[s]) for s in target_symbols):
                # NAV 用上一天的（如果存在）
                if snapshots:
                    prev = snapshots[-1]
                    nav = prev.total_value
                else:
                    nav = self._initial_capital
                snapshots.append(
                    PortfolioSnapshot(
                        date=current_dt,
                        total_value=nav,
                        positions={s: positions[s] for s in target_symbols},
                        weights=self._calc_weights(positions, row_dict, cash),
                        cash=cash,
                    )
                )
                equity_records.append(
                    {
                        "date": current_dt,
                        "nav": float(nav),
                        **{f"price_{s}": float(row_dict[s]) for s in target_symbols},
                    }
                )
                continue

            # day 0: 初始建仓
            if i == 0:
                target_weights_map = self._maybe_update_risk_parity(
                    strategy, price_df.iloc[: i + 1], target_weights_map
                )
                positions, cash, trades, cost = self._rebalance_to(
                    target_weights=target_weights_map,
                    current_positions=positions,
                    cash=cash,
                    prices=row_dict,
                    cost_bps=self._cost_bps,
                    slippage_bps=self._slippage_bps,
                )
                rebalance_events.append(
                    RebalanceEvent(
                        date=current_dt,
                        triggered_by="schedule",
                        trades=trades,
                        cost_bps=self._cost_bps,
                    )
                )
                last_rebalance_date = current_dt

            else:
                # 检查再平衡
                weights_now = self._calc_weights(positions, row_dict, cash)
                should, reason = self._should_rebalance(
                    strategy.rebalance,
                    current_dt=current_dt,
                    inception=strategy.inception,
                    last_rebalance=last_rebalance_date,
                    current_weights=weights_now,
                    target_weights=target_weights_map,
                )
                if should:
                    target_weights_map = self._maybe_update_risk_parity(
                        strategy, price_df.iloc[: i + 1], target_weights_map
                    )
                    positions, cash, trades, cost = self._rebalance_to(
                        target_weights=target_weights_map,
                        current_positions=positions,
                        cash=cash,
                        prices=row_dict,
                        cost_bps=self._cost_bps,
                        slippage_bps=self._slippage_bps,
                    )
                    rebalance_events.append(
                        RebalanceEvent(
                            date=current_dt,
                            triggered_by=reason,  # type: ignore[arg-type]
                            trades=trades,
                            cost_bps=self._cost_bps,
                        )
                    )
                    last_rebalance_date = current_dt

            # 计算 NAV
            nav = sum(positions[s] * row_dict[s] for s in target_symbols) + cash

            # record snapshot
            snapshots.append(
                PortfolioSnapshot(
                    date=current_dt,
                    total_value=nav,
                    positions=dict(positions),
                    weights=self._calc_weights(positions, row_dict, cash),
                    cash=cash,
                )
            )
            equity_records.append(
                {
                    "date": current_dt,
                    "nav": float(nav),
                    **{f"price_{s}": float(row_dict[s]) for s in target_symbols},
                }
            )

        # equity_curve DataFrame
        equity_df = pd.DataFrame(equity_records).set_index("date")
        equity_df.index = pd.to_datetime(equity_df.index)

        # 计算 metrics
        metrics = compute_metrics(equity_df)

        # 最后一个 nav 即 final_value
        final_value = snapshots[-1].total_value

        return BacktestResult(
            strategy_id=strategy.id,
            start_date=snapshots[0].date,
            end_date=snapshots[-1].date,
            initial_capital=self._initial_capital,
            final_value=final_value,
            equity_curve=equity_df,
            snapshots=snapshots,
            metrics=metrics,
            rebalance_events=rebalance_events,
        )

    # ─── helpers ───

    @staticmethod
    def _maybe_update_risk_parity(
        strategy: Strategy,
        price_history: pd.DataFrame,
        current_weights: dict[str, Decimal],
    ) -> dict[str, Decimal]:
        """如果 strategy 是 risk parity，重算权重；否则原样返回。"""
        if strategy.id == "risk_parity":
            return compute_risk_parity_weights(price_history)
        return current_weights

    @staticmethod
    def _calc_weights(
        positions: dict[str, Decimal],
        prices: dict[str, Decimal],
        cash: Decimal,
    ) -> dict[str, Decimal]:
        """算当前权重：每个 position 的市值 / 总 NAV。"""
        nav = sum(positions[s] * prices[s] for s in positions) + cash
        if nav <= 0:
            n = len(positions)
            return {s: Decimal("0") for s in positions} if n else {}
        return {
            s: (positions[s] * prices[s]) / nav for s in positions
        }

    @staticmethod
    def _should_rebalance(
        rule: RebalanceRule,
        current_dt: date,
        inception: date,
        last_rebalance: date | None,
        current_weights: dict[str, Decimal],
        target_weights: dict[str, Decimal],
    ) -> tuple[bool, str | None]:
        """判断是否要再平衡。返回 (should, reason)。

        触发条件（OR）：
          - schedule: frequency != 'none' 且到了对应周期
          - threshold: rule.threshold 非空 且任一标的当前权重偏离 target 超过 threshold
        """
        # threshold 检查
        if rule.threshold is not None:
            thr = float(rule.threshold)
            for sym, target_w in target_weights.items():
                cur_w = float(current_weights.get(sym, Decimal("0")))
                if abs(cur_w - float(target_w)) > thr:
                    return True, "threshold"

        # schedule 检查
        freq = rule.frequency
        if freq == "none":
            return False, None

        # 第一次再平衡日 = inception；之后按周期推
        if last_rebalance is None:
            return False, None  # day 0 已经处理

        months_since_last = _months_between(last_rebalance, current_dt)

        if freq == "monthly":
            if months_since_last >= 1:
                return True, "schedule"
        elif freq == "quarterly":
            if months_since_last >= 3:
                return True, "schedule"
        elif freq == "yearly":
            if months_since_last >= 12:
                return True, "schedule"

        return False, None

    @staticmethod
    def _rebalance_to(
        target_weights: dict[str, Decimal],
        current_positions: dict[str, Decimal],
        cash: Decimal,
        prices: dict[str, Decimal],
        cost_bps: Decimal,
        slippage_bps: Decimal,
    ) -> tuple[dict[str, Decimal], Decimal, list[Trade], Decimal]:
        """调仓到目标权重。返回 (新 positions, 新 cash, trades, total_cost)。

        算法：
          1. 算 NAV
          2. 算每个标的的目标市值 = NAV * weight
          3. 算每个标的的 delta_value = target - current
          4. 先卖出（delta_value < 0），再买入（delta_value > 0）
          5. 每次交易扣手续费（cost_bps/10000 * trade_value）
          6. slippage 暂时不加（MVP）
        """
        nav = sum(current_positions[s] * prices[s] for s in current_positions) + cash

        # 目标市值
        target_values = {s: nav * target_weights.get(s, Decimal("0")) for s in current_positions}

        # 当前市值
        current_values = {
            s: current_positions[s] * prices[s] for s in current_positions
        }

        # deltas
        deltas = {s: target_values[s] - current_values[s] for s in current_positions}

        trades: list[Trade] = []
        total_cost = Decimal("0")

        # 先卖后买
        # 先执行卖出（累计 cash）
        new_cash = cash
        new_positions = dict(current_positions)
        cost_rate = cost_bps / Decimal("10000")

        for s, d in deltas.items():
            if d >= 0:
                continue
            # 卖出 abs(d) 价值的股票
            price = prices[s]
            if price <= 0:
                continue
            sell_value = -d  # positive
            # 扣手续费
            fee = sell_value * cost_rate
            net_proceeds = sell_value - fee
            shares = sell_value / price  # 按市值算 shares，不扣手续费
            new_positions[s] = new_positions[s] - shares
            new_cash = new_cash + net_proceeds
            trades.append(
                Trade(
                    symbol=s,
                    side="sell",
                    shares=shares,
                    price=price,
                    fee=fee,
                )
            )
            total_cost += fee

        # 再执行买入（用 available_cash 减去卖出后剩余 = new_cash）
        # 按目标 delta 比例分配（如果钱不够，按比例缩小）
        buy_total = sum(d for s, d in deltas.items() if d > 0)

        if buy_total > 0 and new_cash > 0:
            # 实际可用 = new_cash（卖出后）
            scale = min(Decimal("1"), new_cash / buy_total)
            for s, d in deltas.items():
                if d <= 0:
                    continue
                price = prices[s]
                if price <= 0:
                    continue
                buy_value = d * scale
                fee = buy_value * cost_rate
                net_invest = buy_value - fee
                shares = net_invest / price  # 用扣手续费后的钱买
                new_positions[s] = new_positions[s] + shares
                new_cash = new_cash - buy_value
                trades.append(
                    Trade(
                        symbol=s,
                        side="buy",
                        shares=shares,
                        price=price,
                        fee=fee,
                    )
                )
                total_cost += fee

        # 把极小的负 cash（rounding error）clamp 到 0
        if new_cash < 0 and abs(new_cash) < Decimal("1E-20"):
            new_cash = Decimal("0")

        return new_positions, new_cash, trades, total_cost


def _months_between(d1: date, d2: date) -> int:
    """算两个 date 之间相差的完整月份数（d2 > d1）。"""
    return (d2.year - d1.year) * 12 + (d2.month - d1.month)


__all__ = ["BacktestEngine"]
