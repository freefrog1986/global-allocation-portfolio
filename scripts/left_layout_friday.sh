#!/usr/bin/env bash
# scripts/left_layout_friday.sh — 每周五发「左侧布局」组合周报
#
# liubo 2026-10-20 拍板：「每周五做是否调仓的决定…然后你这个周报的话，也是每周五发」。
# 跟 pe_rebalance_friday.sh（PE-TTM 周调仓，同为周五）**分开两个脚本** ——
# 左侧布局 / 大类资产配置是三个独立组合，代码、仓位、日志都不混
# （见 memory/feedback_separate_portfolios.md）。
#
# 加到 cron：
#   17 16 * * 5 /home/lenovo/projects/global-allocation-portfolio/scripts/left_layout_friday.sh
# (周五 16:17 跑，比 pe_rebalance_friday.sh 的 16:07 晚 10 分钟，
#  这样 liubo 先看 PE 调仓卡，再看左侧布局卡)

set -euo pipefail

PROJECT_DIR="/home/lenovo/projects/global-allocation-portfolio"
LOG_FILE="$PROJECT_DIR/.local/share/gap/left_layout_friday.log"
mkdir -p "$(dirname "$LOG_FILE")"

cd "$PROJECT_DIR"

# 锁文件防并发（用户手动跑 + cron 同时触发）
LOCK_FILE="/tmp/left_layout_friday.lock"
if [ -f "$LOCK_FILE" ]; then
    echo "[$(date +%FT%T)] 已有进程在跑，退出" >> "$LOG_FILE"
    exit 0
fi
trap 'rm -f "$LOCK_FILE"' EXIT
touch "$LOCK_FILE"

echo "[$(date +%FT%T)] 开始发左侧布局周报" >> "$LOG_FILE"

# 左侧布局的估值数据是 liubo 手动维护在 INDEX_VALUATION_BY_CODE 里的，
# 所以周五自动发时，没填数据的类别估值显示灰色「—」属正常情况，
# liubo 补完数据可以手动重发一次。
uv run gap portfolio left-layout --send >> "$LOG_FILE" 2>&1

echo "[$(date +%FT%T)] 完成" >> "$LOG_FILE"