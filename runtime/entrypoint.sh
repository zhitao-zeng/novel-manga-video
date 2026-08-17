#!/bin/sh
set -eu

/opt/venvs/controller/bin/python /app/runtime/model_supervisor.py &
supervisor_pid=$!

cleanup() {
  kill -TERM "$supervisor_pid" 2>/dev/null || true
  wait "$supervisor_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

ready_attempt=0
until curl --noproxy '*' --fail --silent --show-error \
  http://127.0.0.1:18090/ready >/dev/null; do
  ready_attempt=$((ready_attempt + 1))
  if [ "$ready_attempt" -ge 60 ]; then
    echo "model supervisor did not become reachable" >&2
    exit 1
  fi
  if ! kill -0 "$supervisor_pid" 2>/dev/null; then
    echo "model supervisor exited during startup" >&2
    exit 1
  fi
  sleep 1
done

/opt/venvs/controller/bin/python -m uvicorn novel_manga.api:app \
  --host 0.0.0.0 --port 80 --workers 1
