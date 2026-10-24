#!/usr/bin/env bash
# scripts/pe_rebalance_friday.sh — 每周五跑 PE-TTM 周调仓 + 发飞书
#
# spec 022 — liubo 2026-09-24 拍板每周五发周报。
# 加到 cron：
#   7 16 * * 5 /home/lenovo/projects/global-allocation-portfolio/scripts/pe_rebalance_friday.sh
# (避开 :00 高峰；周五 16:07 跑)

set -euo pipefail

PROJECT_DIR="/home/lenovo/projects/global-allocation-portfolio"
LOG_FILE="$PROJECT_DIR/.local/share/gap/pe_rebalance_friday.log"
mkdir -p "$(dirname "$LOG_FILE")"

cd "$PROJECT_DIR"

# 锁文件防并发（用户手动跑 + cron 同时触发）
LOCK_FILE="/tmp/pe_rebalance_friday.lock"
if [ -f "$LOCK_FILE" ]; then
    echo "[$(date +%FT%T)] 已有进程在跑，退出" >> "$LOG_FILE"
    exit 0
fi
trap 'rm -f "$LOCK_FILE"' EXIT
touch "$LOCK_FILE"

echo "[$(date +%FT%T)] 开始 PE-TTM 周调仓" >> "$LOG_FILE"

# 1. 先拉 REITs 估值（spec REITs-DY — liubo 2026-10-12 拍板）
# 失败不阻断：report 仍跑（用上次 JSON 或 fallback SKIP）
echo "[$(date +%FT%T)] 拉 REITs DY 数据" >> "$LOG_FILE"
uv run gap portfolio reit-fetch >> "$LOG_FILE" 2>&1 || echo "[$(date +%FT%T)] REITs 拉取失败，继续跑 report" >> "$LOG_FILE"

# 1b. 拉海外指数估值（liubo 2026-10-24：DAX / 日经 225 / 法国 CAC 40 / 全球新兴市场）
# 失败不阻断：report 仍跑（回落 PE_SNAPSHOT_BY_INDEX 里的静态值）
echo "[$(date +%FT%T)] 拉海外指数 DY 数据" >> "$LOG_FILE"
uv run gap portfolio overseas-fetch >> "$LOG_FILE" 2>&1 || echo "[$(date +%FT%T)] 海外指数拉取失败，继续跑 report" >> "$LOG_FILE"

# 2. uv run gap portfolio pe-rebalance --send 通过飞书 OpenAPI 发卡片到话题 thread
# topic root: om_x100b6410364be0b0c45b4dbfab88ab1 (botmux history 2026-09-24)
TOPIC_ROOT="om_x100b6410364be0b0c45b4dbfab88ab1"
uv run gap portfolio pe-rebalance --send --root "$TOPIC_ROOT" >> "$LOG_FILE" 2>&1

echo "[$(date +%FT%T)] 完成" >> "$LOG_FILE"