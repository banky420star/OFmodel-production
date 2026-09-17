#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT/apps/api"
cd "$API_DIR"

if [[ -x "$API_DIR/.venv/bin/python" ]]; then
  PYTHON="$API_DIR/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

export PYTHONPATH="$API_DIR"
exec "$PYTHON" -m uvicorn app.main:app \
  --host "${API_HOST:-0.0.0.0}" \
  --port "${API_PORT:-8000}" \
  --loop asyncio \
  --http h11
