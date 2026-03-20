#!/bin/bash
# run.sh — entry point cho cronjob Japan Stock Scanner
# Cronjob: mùng 4 hàng tháng, 00:05 JST
#   5 0 4 * * /path/to/japan-scanner/run.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Timeframe — đổi thành 1WK / 1D khi cần
TF="1MO"

# Timestamp helper — luôn JST, consistent với log format chuẩn hệ thống
now_jst() { TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M:%S JST'; }

# Python interpreter cố định — cron env không có PATH đầy đủ
PYTHON="$SCRIPT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    PYTHON="$(command -v python3 || true)"
fi

# Load .env nếu có (fallback cho môi trường không set env trực tiếp)
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

# Log file cho cron output (khác với batch log của Python)
CRON_LOG="$LOG_DIR/cron.log"

# Separator — dễ đọc khi log dài hàng tháng
echo "--------------------------------------------------" >> "$CRON_LOG"

# Guard: python không tìm thấy
if [ -z "$PYTHON" ]; then
    echo "[$(now_jst)] ERROR: python not found, exit" >> "$CRON_LOG"
    exit 1
fi

# Lock — tránh chạy trùng batch (cron delay hoặc manual run)
LOCK_FILE="/tmp/japan_scanner_${TF}.lock"
exec 200>"$LOCK_FILE"
flock -n 200 || {
    echo "[$(now_jst)] another instance is running (tf=$TF), exit" >> "$CRON_LOG"
    exit 1
}

echo "[$(now_jst)] run.sh started (tf=$TF python=$PYTHON)" >> "$CRON_LOG"
START_TS=$(date +%s)

# set +e để capture exit code dù scanner fail
set +e
"$PYTHON" scanner.py --timeframe "$TF" >> "$CRON_LOG" 2>&1
EXIT_CODE=$?

DURATION=$(( $(date +%s) - START_TS ))
echo "[$(now_jst)] run.sh finished (tf=$TF exit=$EXIT_CODE duration=${DURATION}s)" >> "$CRON_LOG"

exit $EXIT_CODE
