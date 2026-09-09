#!/usr/bin/env bash
# Keep the progress page up: restart it if it ever exits, and log only the restarts.
cd "$(dirname "$0")/.." || exit 1
PORT=${1:-18900}
LOG=${NOVEL_TMP_DIR:-$(cd .. && pwd)/tmp}/status_server.log
while true; do
  .venv/bin/python scripts/status_server.py "$PORT" >> "$LOG" 2>&1
  echo "$(date '+%m-%d %H:%M:%S') status server exited ($?), restarting" >> "$LOG"
  sleep 5
done
