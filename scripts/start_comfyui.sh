#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMFY_DIR="$ROOT/.local/ComfyUI"
VENV="$ROOT/.local/comfy-venv"
cd "$COMFY_DIR"
if [[ "${COMFYUI_CPU:-0}" == "1" ]]; then
  exec "$VENV/bin/python" main.py --listen 127.0.0.1 --port "${COMFYUI_PORT:-8188}" --cpu
fi
exec "$VENV/bin/python" main.py --listen 127.0.0.1 --port "${COMFYUI_PORT:-8188}"
