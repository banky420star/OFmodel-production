#!/usr/bin/env python3
"""Preflight check for the ComfyUI image workflow."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request


def get_json(base_url: str, path: str) -> dict:
    request = urllib.request.Request(f"{base_url.rstrip('/')}{path}")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a ComfyUI instance for Persona Studio")
    project_root = Path(__file__).resolve().parents[1]
    env_file = project_root / ".env"
    env_values: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env_values[key.strip()] = value.split("#", 1)[0].strip()
    parser.add_argument(
        "--url",
        default=os.getenv("COMFYUI_URL", env_values.get("COMFYUI_URL", "http://127.0.0.1:8188")),
    )
    parser.add_argument(
        "--checkpoint",
        default=os.getenv(
            "COMFYUI_CHECKPOINT",
            env_values.get("COMFYUI_CHECKPOINT", "sd_xl_base_1.0.safetensors"),
        ),
    )
    args = parser.parse_args()
    base = args.url.rstrip("/")

    try:
        stats = get_json(base, "/system_stats")
    except (OSError, urllib.error.URLError) as exc:
        print(f"ComfyUI is not reachable at {base}: {exc}", file=sys.stderr)
        print("Start ComfyUI, then set COMFYUI_URL to this address.", file=sys.stderr)
        return 2

    devices = stats.get("devices") or []
    for device in devices:
        name = device.get("name", "unknown")
        vram = device.get("vram_total", 0)
        print(f"device: {name} ({vram} bytes VRAM)")

    try:
        object_info = get_json(base, "/object_info")
    except (OSError, urllib.error.URLError) as exc:
        print(f"ComfyUI is reachable but /object_info failed: {exc}", file=sys.stderr)
        return 3

    required = {"CheckpointLoaderSimple", "KSampler", "LoadImage", "VAEEncode", "SaveImage"}
    missing = sorted(required - object_info.keys())
    if missing:
        print(f"Missing required ComfyUI nodes: {', '.join(missing)}", file=sys.stderr)
        return 4

    checkpoints = (
        object_info.get("CheckpointLoaderSimple", {})
        .get("input", {})
        .get("required", {})
        .get("ckpt_name", [[], {}])[0]
    )
    expected = args.checkpoint
    if expected not in checkpoints:
        print(
            f"ComfyUI is reachable, but checkpoint {expected!r} is not installed. "
            "Add it under .local/ComfyUI/models/checkpoints or set "
            "COMFYUI_CHECKPOINT to an installed compatible file.",
            file=sys.stderr,
        )
        return 5

    print("ComfyUI API: reachable")
    print("Persona Studio workflow nodes: available")
    print(f"Checkpoint: {expected}")
    print("Next: set IMAGE_PROVIDER=comfyui and COMFYUI_URL in .env, then restart the API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
