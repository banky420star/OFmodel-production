"""Generate gallery variations for all personas using DashScope Qwen-Image Edit.

Reads each persona's avatar and generates outfit/pose/setting variations
while preserving facial identity.
"""
import asyncio
import base64
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("WAN_API_KEY", "")

# Load from .env
# Load from .env — walk up from CWD to find it
cwd = Path.cwd()
for parent in [cwd] + list(cwd.parents):
    env_path = parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                os.environ[key.strip()] = val.strip()
        break

from app.providers.dashscope_image import DashScopeImageProvider


GALLERY_DIR = Path(__file__).parent / "storage" / "gallery"

VARIATIONS = {
    "ava": {
        "ref": "storage/avatars/ava.jpg",
        "edits": [
            {
                "name": "casual",
                "prompt": (
                    "Perfectly preserve the facial features of the woman in the input image. "
                    "She is wearing a casual white linen oversized shirt and high-waisted blue jeans. "
                    "Standing in a sunlit minimalist apartment with white walls and wooden floors. "
                    "Natural daylight from large windows. Relaxed pose, leaning against a doorframe, "
                    "one hand in pocket. Soft natural makeup. Warm golden tones. "
                    "Lifestyle editorial photography. Shallow depth of field."
                ),
            },
            {
                "name": "formal",
                "prompt": (
                    "Perfectly preserve the facial features of the woman in the input image. "
                    "She is wearing an elegant black silk evening dress with thin straps. "
                    "Standing on a balcony at sunset with city skyline behind her. "
                    "Hair styled in loose waves. Statement gold earrings. "
                    "Confident pose, looking over shoulder at camera. "
                    "Warm sunset rim lighting. Luxury fashion editorial. "
                    "Shot on Hasselblad medium format."
                ),
            },
            {
                "name": "outdoor",
                "prompt": (
                    "Perfectly preserve the facial features of the woman in the input image. "
                    "She is walking along a sandy beach at golden hour. "
                    "Wearing a flowing white sundress that catches the ocean breeze. "
                    "Barefoot, hair blowing naturally. Looking towards the camera with a genuine smile. "
                    "Ocean waves in the background. Warm golden hour sunlight. "
                    "Travel lifestyle photography. Natural and candid feeling."
                ),
            },
        ],
    },
    "noor": {
        "ref": "storage/avatars/noor.jpg",
        "edits": [
            {
                "name": "hiking",
                "prompt": (
                    "Perfectly preserve the facial features of the woman in the input image. "
                    "She is hiking on a mountain trail with dramatic landscape behind her. "
                    "Wearing a fitted olive green utility jacket over a cream top, with a backpack. "
                    "Hair in a practical braid. Confident adventurous expression. "
                    "Dramatic mountain landscape with golden light. "
                    "Outdoor adventure photography. National Geographic quality."
                ),
            },
            {
                "name": "cafe",
                "prompt": (
                    "Perfectly preserve the facial features of the woman in the input image. "
                    "She is sitting in a cozy European cafe with warm wood and brick interior. "
                    "Wearing a rust-colored knit sweater. Holding a ceramic coffee cup. "
                    "Natural smile, looking down at a book on the table. "
                    "Warm ambient lighting from pendant lamps. Shallow depth of field. "
                    "Lifestyle editorial photography. Intimate and inviting mood."
                ),
            },
            {
                "name": "city",
                "prompt": (
                    "Perfectly preserve the facial features of the woman in the input image. "
                    "She is standing on a vibrant city street at dusk. "
                    "Wearing a tailored camel coat over a cream turtleneck. "
                    "Hair loose and flowing. Confident stride. "
                    "Neon signs and city lights create colorful bokeh in the background. "
                    "Street photography style. Cinematic mood. Urban fashion editorial."
                ),
            },
        ],
    },
    "zara": {
        "ref": "storage/avatars/zara.jpg",
        "edits": [
            {
                "name": "business",
                "prompt": (
                    "Perfectly preserve the facial features of the woman in the input image. "
                    "She is sitting at a modern glass conference table. "
                    "Wearing a sharp navy blue power suit with white silk blouse. "
                    "Hair sleek and professional. Commanding presence. "
                    "Minimalist office with floor-to-ceiling windows showing city view. "
                    "Clean studio lighting. Corporate fashion editorial."
                ),
            },
            {
                "name": "weekend",
                "prompt": (
                    "Perfectly preserve the facial features of the woman in the input image. "
                    "She is at a rooftop brunch on a sunny day. "
                    "Wearing a relaxed beige linen set with delicate gold jewelry. "
                    "Hair in a messy bun. Natural no-makeup look. "
                    "Laughing candidly, holding a fresh juice glass. "
                    "Bright natural sunlight. Blurred greenery background. "
                    "Candid lifestyle photography. Fresh and relaxed."
                ),
            },
            {
                "name": "artistic",
                "prompt": (
                    "Perfectly preserve the facial features of the woman in the input image. "
                    "She is in an art gallery surrounded by large abstract paintings. "
                    "Wearing a flowing all-black avant-garde outfit with sculptural draping. "
                    "Hair slicked back dramatically. Intense contemplative expression. "
                    "Dramatic gallery lighting with colored light from artworks. "
                    "Fine art fashion editorial. Moody and atmospheric."
                ),
            },
        ],
    },
}


async def generate_variations():
    provider = DashScopeImageProvider()
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    
    total = sum(len(v["edits"]) for v in VARIATIONS.values())
    done = 0
    
    for persona_name, config in VARIATIONS.items():
        ref_path = Path(__file__).parent / config["ref"]
        if not ref_path.exists():
            print(f"  SKIP {persona_name}: reference not found at {ref_path}")
            continue
        
        ref_bytes = ref_path.read_bytes()
        print(f"\n{'='*60}")
        print(f"  {persona_name.upper()} — generating {len(config['edits'])} variations")
        print(f"{'='*60}")
        
        for edit in config["edits"]:
            done += 1
            out_path = GALLERY_DIR / f"{persona_name}_{edit['name']}.png"
            
            if out_path.exists() and out_path.stat().st_size > 10000:
                print(f"  [{done}/{total}] {edit['name']}: EXISTS ({out_path.stat().st_size} bytes)")
                continue
            
            print(f"  [{done}/{total}] {edit['name']}: generating...", end="", flush=True)
            t0 = time.time()
            
            result = await provider.edit_image(
                reference_image_bytes=ref_bytes,
                prompt=edit["prompt"],
                width=1536,
                height=2048,
                seed=hash(edit["name"]) % 2147483647,
            )
            
            elapsed = time.time() - t0
            
            if result.success:
                img_bytes = result.data["image_bytes"]
                out_path.write_bytes(img_bytes)
                print(f" OK {len(img_bytes)} bytes, {elapsed:.1f}s")
            else:
                print(f" FAILED: {result.error[:100]}")
    
    print(f"\n{'='*60}")
    print(f"  DONE — {done} variations")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(generate_variations())
