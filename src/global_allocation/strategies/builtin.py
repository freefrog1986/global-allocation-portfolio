"""4 个内置策略。

参照 specs/030-built-in-strategies.md。

每个策略类：
  - 定义 id/name/description 类属性
  - 实现 build() 返回完整 Strategy
  - 默认 weights_at() 返回 build().target_weights
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from global_allocation.models import (
    Asset,
    AssetClass,
    Currency,
    DataSource,
    RebalanceRule,
    Region,
    Strategy,
    TargetWeight,
)
from global_allocation.strategies.base import StrategyBase

# ────────────────────────────────────────────────────────────────────
# 1. 60/40 — 经典股债
# ────────────────────────────────────────────────────────────────────


class SixtyForty(StrategyBase):
    """60% 全球股票 + 40% 美国债，按年再平衡。"""

    id = "60_40"
    name = "60/40 经典股债"
    description = (
        "60% 全球股票 ETF (VT) + 40% 美国债 ETF (BND) 的传统退休金组合，"
        "按年再平衡。Brinson-Hood-Beebower (1986) 的经典应用。"
    )
    rebalance = RebalanceRule(frequency="yearly")

    def build(self) -> Strategy:
        vt = Asset(
            symbol="VT",
            name="Vanguard Total World Stock ETF",
            asset_class=AssetClass.EQUITY,
            region=Region.GLOBAL,
            currency=Currency.USD,
            data_source=DataSource.YFINANCE,
        )
        bnd = Asset(
            symbol="BND",
            name="Vanguard Total Bond Market ETF",
            asset_class=AssetClass.BOND,
            region=Region.US,
            currency=Currency.USD,
            data_source=DataSource.YFINANCE,
        )
        return Strategy(
            id=self.id,
            name=self.name,
            description=self.description,
            target_weights=[
                TargetWeight(asset=vt, weight=Decimal("0.60")),
                TargetWeight(asset=bnd, weight=Decimal("0.40")),
            ],
            rebalance=self.rebalance,
            base_currency=Currency.USD,
            inception=date(2007, 9, 26),  # BND 成立日
        )


# ────────────────────────────────────────────────────────────────────
# 2. Permanent Portfolio — 永久组合（Harry Browne）
# ────────────────────────────────────────────────────────────────────


class PermanentPortfolio(StrategyBase):
    """Harry Browne 的四等分组合：股、债、金、现金各 25%。"""

    id = "permanent_portfolio"
    name = "永久组合 (Harry Browne)"
    description = (
        "股、债、黄金、现金各 25% 的四等分组合，目标是任何经济环境都不亏。"
        "Browne (1999) Fail-Safe Investing。"
    )
    rebalance = RebalanceRule(frequency="yearly")

    def build(self) -> Strategy:
        vti = Asset(
            symbol="VTI",
            name="Vanguard Total Stock Market ETF",
            asset_class=AssetClass.EQUITY,
            region=Region.US,
            currency=Currency.USD,
            data_source=DataSource.YFINANCE,
        )
        agg = Asset(
            symbol="AGG",
            name="iShares Core US Aggregate Bond ETF",
            asset_class=AssetClass.BOND,
            region=Region.US,
            currency=Currency.USD,
            data_source=DataSource.YFINANCE,
        )
        gld = Asset(
            symbol="GLD",
            name="SPDR Gold Shares",
            asset_class=AssetClass.COMMODITY,
            region=Region.GLOBAL,
            currency=Currency.USD,
            data_source=DataSource.YFINANCE,
        )
        shy = Asset(
            symbol="SHY",
            name="iShares 1-3 Year Treasury Bond ETF",
            asset_class=AssetClass.CASH,
            region=Region.US,
            currency=Currency.USD,
            data_source=DataSource.YFINANCE,
        )
        return Strategy(
            id=self.id,
            name=self.name,
            description=self.description,
            target_weights=[
                TargetWeight(asset=vti, weight=Decimal("0.25")),
                TargetWeight(asset=agg, weight=Decimal("0.25")),
                TargetWeight(asset=gld, weight=Decimal("0.25")),
                TargetWeight(asset=shy, weight=Decimal("0.25")),
            ],
            rebalance=self.rebalance,
            base_currency=Currency.USD,
            inception=date(2004, 11, 18),  # GLD 成立日
        )


# ────────────────────────────────────────────────────────────────────
# 3. All Weather — 桥水全天候（简化版）
# ────────────────────────────────────────────────────────────────────


class AllWeather(StrategyBase):
    """Ray Dalio 的全天候组合简化版：分散通胀/通缩、增长/衰退。"""

    id = "all_weather"
    name = "桥水全天候（简化版）"
    description = (
        "5 个 ETF 分散通胀/通缩 + 增长/衰退两个维度。"
        "这是零售简化版，桥水原版用杠杆 + 通胀挂钩债券 + 衍生品。"
    )
    rebalance = RebalanceRule(frequency="yearly")

    def build(self) -> Strategy:
        vti = Asset(
            symbol="VTI",
            name="Vanguard Total Stock Market ETF",
            asset_class=AssetClass.EQUITY,
            region=Region.US,
            currency=Currency.USD,
            data_source=DataSource.YFINANCE,
        )
        tlt = Asset(
            symbol="TLT",
            name="iShares 20+ Year Treasury Bond ETF",
            asset_class=AssetClass.BOND,
            region=Region.US,
            currency=Currency.USD,
            data_source=DataSource.YFINANCE,
        )
        ief = Asset(
            symbol="IEF",
            name="iShares 7-10 Year Treasury Bond ETF",
            asset_class=AssetClass.BOND,
            region=Region.US,
            currency=Currency.USD,
            data_source=DataSource.YFINANCE,
        )
        gld = Asset(
            symbol="GLD",
            name="SPDR Gold Shares",
            asset_class=AssetClass.COMMODITY,
            region=Region.GLOBAL,
            currency=Currency.USD,
            data_source=DataSource.YFINANCE,
        )
        djp = Asset(
            symbol="DJP",
            name="iPath Bloomberg Commodity Index Total Return ETN",
            asset_class=AssetClass.COMMODITY,
            region=Region.GLOBAL,
            currency=Currency.USD,
            data_source=DataSource.YFINANCE,
        )
        return Strategy(
            id=self.id,
            name=self.name,
            description=self.description,
            target_weights=[
                TargetWeight(asset=vti, weight=Decimal("0.30")),
                TargetWeight(asset=tlt, weight=Decimal("0.40")),
                TargetWeight(asset=ief, weight=Decimal("0.15")),
                TargetWeight(asset=gld, weight=Decimal("0.075")),
                TargetWeight(asset=djp, weight=Decimal("0.075")),
            ],
            rebalance=self.rebalance,
            base_currency=Currency.USD,
            inception=date(2002, 7, 26),  # TLT 成立日
        )


# ────────────────────────────────────────────────────────────────────
# 4. Risk Parity — 风险平价
# ────────────────────────────────────────────────────────────────────


class RiskParity(StrategyBase):
    """风险平价：每种资产对组合总风险的贡献相等。

    build() 返回等权（fallback）。真正的风险平价权重由
    :func:`global_allocation.backtest.engine.compute_risk_parity_weights`
    在回测时计算（需要历史价格）。
    """

    id = "risk_parity"
    name = "风险平价 (Risk Parity)"
    description = (
        "不按金额配，按风险贡献配：每种资产对组合总风险的贡献相等。"
        "Qian (2005) Risk Parity Portfolios；Bridgewater All Weather 底层思想。"
    )
    rebalance = RebalanceRule(frequency="monthly", threshold=Decimal("0.10"))

    # 4 个标的，等权回测 fallback
    _SYMBOLS = ["VTI", "AGG", "GLD", "VNQ"]

    def build(self) -> Strategy:
        weights = [Decimal("0.25")] * 4  # 等权
        assets = [
            Asset(
                symbol=symbol,
                name=self._name_for(symbol),
                asset_class=self._class_for(symbol),
                region=Region.US if symbol != "GLD" else Region.GLOBAL,
                currency=Currency.USD,
                data_source=DataSource.YFINANCE,
            )
            for symbol in self._SYMBOLS
        ]
        return Strategy(
            id=self.id,
            name=self.name,
            description=self.description,
            target_weights=[
                TargetWeight(asset=asset, weight=weight)
                for asset, weight in zip(assets, weights, strict=True)
            ],
            rebalance=self.rebalance,
            base_currency=Currency.USD,
            inception=date(2004, 9, 23),  # GLD ETF 可用作参考起点
        )

    @staticmethod
    def _name_for(symbol: str) -> str:
        return {
            "VTI": "Vanguard Total Stock Market ETF",
            "AGG": "iShares Core US Aggregate Bond ETF",
            "GLD": "SPDR Gold Shares",
            "VNQ": "Vanguard Real Estate ETF",
        }.get(symbol, symbol)

    @staticmethod
    def _class_for(symbol: str) -> AssetClass:
        return {
            "VTI": AssetClass.EQUITY,
            "AGG": AssetClass.BOND,
            "GLD": AssetClass.COMMODITY,
            "VNQ": AssetClass.REIT,
        }.get(symbol, AssetClass.EQUITY)


__all__ = [
    "SixtyForty",
    "PermanentPortfolio",
    "AllWeather",
    "RiskParity",
]
