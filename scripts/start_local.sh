#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT/apps/api"
WEB_DIR="$ROOT/apps/web"
PID_DIR="$ROOT/.local/run"
LOG_DIR="$ROOT/.local/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

cleanup() {
  for pid_file in "$PID_DIR"/*.pid; do
    [[ -f "$pid_file" ]] || continue
    pid="$(cat "$pid_file")"
    kill "$pid" 2>/dev/null || true
    rm -f "$pid_file"
  done
}
trap cleanup EXIT INT TERM

if [[ -x "$API_DIR/.venv/bin/python" ]]; then
  PYTHON="$API_DIR/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

(
  cd "$API_DIR"
  PYTHONPATH="$API_DIR" "$PYTHON" -m uvicorn app.main:app \
    --host "${API_HOST:-127.0.0.1}" \
    --port "${API_PORT:-8000}" \
    --loop asyncio \
    --http h11
) >"$LOG_DIR/api.log" 2>&1 &
echo $! >"$PID_DIR/api.pid"

(
  cd "$WEB_DIR"
  npm run dev -- --hostname "${WEB_HOST:-127.0.0.1}" --port "${WEB_PORT:-3000}"
) >"$LOG_DIR/web.log" 2>&1 &
echo $! >"$PID_DIR/web.pid"

printf 'Local Persona Studio started\n'
printf '  Web: http://%s:%s\n' "${WEB_HOST:-127.0.0.1}" "${WEB_PORT:-3000}"
printf '  API: http://%s:%s/docs\n' "${API_HOST:-127.0.0.1}" "${API_PORT:-8000}"
printf '  Logs: %s\n' "$LOG_DIR"
printf 'Press Ctrl-C to stop both services.\n'

wait
