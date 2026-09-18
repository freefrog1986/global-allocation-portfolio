"""策略配置管理子包。

参照 specs/091-strategy-config.md。

跟现有的 global_allocation.strategies（回测模板 60/40、全天候）解耦：
- strategies/  = 预设回测模板（spec 020/030）
- strategy/    = 用户的实盘配置管理（spec 091）
"""

from __future__ import annotations
