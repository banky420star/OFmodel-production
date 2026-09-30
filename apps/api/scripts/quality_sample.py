"""Generate real sample images through the production image provider.

This is not a test and not a mock: it resolves the *real* ComfyUI provider
through the same registry the app uses and writes the bytes it returns. Run it
with ComfyUI up (COMFYUI_URL in .env) to see what the line would actually ship.

    .venv/bin/python scripts/quality_sample.py [out_dir]

Each image is named after the prompt it came from so a reviewer can judge the
prompt→image relationship, not just "a picture".
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers.comfyui import ComfyUIImageProvider  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/persona_quality")
OUT.mkdir(parents=True, exist_ok=True)

NEGATIVE = (
    "cartoon, illustration, 3d render, cgi, deformed, disfigured, extra limbs, "
    "bad anatomy, blurry, low quality, watermark, text, signature"
)

# One prompt per quality axis a buyer actually judges on.
SAMPLES = [
    {
        "slug": "01_portrait_face",
        "note": "Does the face read as a photograph of one consistent adult person?",
        "prompt": (
            "Photorealistic editorial portrait photograph of a 26 year old woman, "
            "long blonde hair, blue eyes, high cheekbones, fair skin, athletic build, "
            "looking directly at camera, soft window light, shallow depth of field, "
            "85mm lens, neutral studio background, natural skin texture, "
            "sharp focus on the eyes"
        ),
        "width": 1024,
        "height": 1024,
    },
    {
        "slug": "02_lifestyle_scene",
        "note": "The 'content' shot: hands, background and lighting all have to hold up.",
        "prompt": (
            "Photorealistic lifestyle photograph of a 26 year old woman with long "
            "blonde hair sitting at a cafe table by a window, holding a coffee cup, "
            "wearing a cream knit sweater, morning light, candid natural expression, "
            "50mm lens, blurred street outside, realistic hands, film-like colour"
        ),
        "width": 1024,
        "height": 1024,
    },
    {
        "slug": "03_full_length",
        "note": "The hardest axis: proportion and hands at full body.",
        "prompt": (
            "Photorealistic full-length fashion photograph of a 26 year old woman "
            "with long blonde hair, standing against a plain concrete wall, wearing "
            "a fitted black dress, arms relaxed at her sides, soft overcast daylight, "
            "full body in frame, correct proportions, detailed hands, 35mm lens"
        ),
        "width": 832,
        "height": 1216,
    },
]


async def main() -> int:
    provider = ComfyUIImageProvider(
        base_url=os.getenv("COMFYUI_URL", "http://127.0.0.1:8188"),
        timeout=float(os.getenv("COMFYUI_TIMEOUT", "2700")),
    )

    health = await provider.health_check()
    if not health.success:
        print(json.dumps({"error": "comfyui unreachable", "detail": health.error}, indent=2))
        return 1
    print(f"comfyui: {health.data}")

    manifest = []
    for sample in SAMPLES:
        started = time.monotonic()
        result = await provider.generate(
            prompt=sample["prompt"],
            negative_prompt=NEGATIVE,
            width=sample["width"],
            height=sample["height"],
            steps=30,
            cfg_scale=7.0,
            seed=42,
        )
        elapsed = time.monotonic() - started
        if not result.success:
            print(f"FAILED {sample['slug']}: {result.error}")
            manifest.append({**sample, "ok": False, "error": result.error})
            continue

        raw = result.data["image_bytes"]
        path = OUT / f"{sample['slug']}.png"
        path.write_bytes(raw)

        from PIL import Image

        with Image.open(path) as im:
            w, h = im.size

        print(f"OK {sample['slug']}  {w}x{h}  {len(raw)/1e6:.2f} MB  {elapsed:.1f}s")
        manifest.append({
            "slug": sample["slug"],
            "note": sample["note"],
            "prompt": sample["prompt"],
            "width": w,
            "height": h,
            "bytes": len(raw),
            "seconds": round(elapsed, 1),
            "seed": result.data.get("seed"),
            "file": f"{sample['slug']}.png",
            "ok": True,
        })

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nwrote {OUT}/manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
