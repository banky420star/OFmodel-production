"""Generate photoshoot images for all shoots.

Uses DashScope Qwen-Image Edit to generate scene-appropriate images
with each persona's identity preserved via reference image.
"""
import asyncio
import base64
import os
import sys
import time
import json
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, os.path.dirname(__file__))

# Load .env
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
from PIL import Image
import io
import httpx


def resize_ref(path: str) -> bytes:
    img = Image.open(path)
    img.thumbnail((768, 768), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# Shoot definitions: (shoot_id, persona_name, shoot_name, theme, scenes)
SHOOTS = [
    # Ava's shoots
    {
        "shoot_id": "a44341ce",
        "persona": "ava",
        "name": "Luxury Apartment Portrait",
        "scenes": [
            "Preserve the face exactly. She stands in a luxury penthouse apartment with floor-to-ceiling windows showing a city skyline. Wearing an elegant cream silk blouse tucked into high-waisted tailored trousers. Hair styled in soft waves. Natural light flooding in. Architectural interior. Luxury lifestyle editorial photography.",
            "Preserve the face exactly. She reclines on a minimalist white sofa in a modern luxury apartment. Wearing a cashmere loungewear set in soft grey. Holding a book. Afternoon light creating shadows through sheer curtains. Refined interior design. Lifestyle photography.",
            "Preserve the face exactly. She poses by a marble kitchen island in a luxury apartment. Wearing a fitted black turtleneck and gold necklace. Hair pulled back sleekly. Preparing coffee. Clean modern design. High-end interior photography.",
        ],
    },
    {
        "shoot_id": "75d677eb",
        "persona": "ava",
        "name": "New Shoot",
        "scenes": [
            "Preserve the face exactly. She walks through a modern art gallery with large abstract paintings. Wearing a structured white blazer over a black slip dress. Confident stride. Dramatic gallery lighting. Contemporary art in background. Fashion editorial.",
        ],
    },
    # Noor's shoots
    {
        "shoot_id": "b1756967",
        "persona": "noor",
        "name": "Travel Editorial Cape Town",
        "scenes": [
            "Preserve the face exactly. She stands at a scenic viewpoint overlooking Cape Town's coastline with Table Mountain in background. Wearing a flowing white linen dress. Hair blowing in the ocean breeze. Golden hour light. Dramatic landscape. Travel editorial photography.",
            "Preserve the face exactly. She walks along the colorful Bo-Kaap neighborhood with painted houses. Wearing a vibrant yellow sundress. Laughing joyfully. Bright African sunlight. Cultural travel photography.",
            "Preserve the face exactly. She sits at an outdoor cafe at the V&A Waterfront with harbor boats behind her. Wearing a relaxed linen shirt and straw hat. Holding a glass of wine. Sunset light reflecting off the water. Cape Town lifestyle photography.",
        ],
    },
    {
        "shoot_id": "911f640c",
        "persona": "noor",
        "name": "Minimalist Design Studio",
        "scenes": [
            "Preserve the face exactly. She sits at a clean white design desk with fabric swatches and mood boards. Wearing a minimalist beige jumpsuit. Hair in a low bun. Focused expression examining color palettes. Natural light from skylight. Design studio photography.",
            "Preserve the face exactly. She stands in a modern design studio surrounded by fabric rolls and mannequins. Wearing a tailored olive blazer over white tee. Thoughtful expression. Creative workspace. Professional photography.",
            "Preserve the face exactly. She sketches at a drafting table in a bright creative studio. Wearing a comfortable knit top. Hair loose around shoulders. Pencils and design tools scattered. Warm natural light. Creative lifestyle photography.",
        ],
    },
    {
        "shoot_id": "973382f4",
        "persona": "noor",
        "name": "Coffee Shop Lifestyle",
        "scenes": [
            "Preserve the face exactly. She sits in a cozy artisan coffee shop with exposed brick walls. Wearing a warm brown knit sweater. Holding a ceramic cup with latte art. Steam rising. Warm pendant lighting. Cozy cafe atmosphere. Lifestyle photography.",
            "Preserve the face exactly. She reads a book at a window seat in a cafe with plants and warm wood. Wearing a relaxed cream cardigan. Natural light streaming in. Peaceful morning vibe. Editorial lifestyle photography.",
            "Preserve the face exactly. She works on a laptop at a cafe counter. Wearing a simple white blouse. Hair tucked behind ears. Focused expression. Coffee cup nearby. Modern cafe interior. Candid lifestyle photography.",
        ],
    },
    # Zara's shoots
    {
        "shoot_id": "56d3c0cd",
        "persona": "zara",
        "name": "New Shoot",
        "scenes": [
            "Preserve the face exactly. She poses against a raw concrete wall in an industrial loft space. Wearing an all-black avant-garde outfit with geometric lines. Dramatic side lighting creating strong shadows. Edgy fashion editorial. High contrast photography.",
        ],
    },
]


async def generate_shoot_images():
    provider = DashScopeImageProvider()
    
    total_images = sum(len(s["scenes"]) for s in SHOOTS)
    done = 0
    
    for shoot in SHOOTS:
        persona = shoot["persona"]
        shoot_id = shoot["shoot_id"]
        name = shoot["name"]
        scenes = shoot["scenes"]
        
        print(f"\n{'='*60}")
        print(f"  {name} ({persona}) — {len(scenes)} images")
        print(f"{'='*60}")
        
        # Load reference avatar
        ref_path = f"storage/avatars/{persona}.jpg"
        if not Path(ref_path).exists():
            print(f"  SKIP: reference not found at {ref_path}")
            continue
        
        ref_bytes = resize_ref(ref_path)
        
        # Create shoot directory
        shoot_dir = Path(f"storage/shoots/{shoot_id}")
        shoot_dir.mkdir(parents=True, exist_ok=True)
        
        for i, scene_prompt in enumerate(scenes):
            done += 1
            out_path = shoot_dir / f"shot_{i+1:02d}.png"
            
            if out_path.exists() and out_path.stat().st_size > 10000:
                print(f"  [{done}/{total_images}] shot_{i+1:02d}: EXISTS ({out_path.stat().st_size} bytes)")
                continue
            
            print(f"  [{done}/{total_images}] shot_{i+1:02d}: generating...", end="", flush=True)
            t0 = time.time()
            
            result = await provider.edit_image(
                reference_image_bytes=ref_bytes,
                prompt=scene_prompt,
                width=1024,
                height=1024,
                seed=hash(f"{shoot_id}_{i}") % 2147483647,
            )
            
            elapsed = time.time() - t0
            
            if result.success:
                img_bytes = result.data["image_bytes"]
                out_path.write_bytes(img_bytes)
                print(f" OK {len(img_bytes)} bytes, {elapsed:.0f}s")
            else:
                print(f" FAILED: {result.error[:100]}")
            
            await asyncio.sleep(2)  # Rate limit
        
        # Update shoot in database
        await update_shoot(shoot_id, len(scenes), str(shoot_dir))
    
    print(f"\n{'='*60}")
    print(f"  DONE — {done} images generated")
    print(f"{'='*60}")


async def update_shoot(shoot_id: str, image_count: int, shoot_dir: str):
    """Update shoot status and generated_images in the database."""
    import asyncpg
    
    db_url = "postgresql+asyncpg://persona:persona@localhost:5432/persona"
    # Use synchronous sqlite check
    db_path = Path(__file__).parent / "persona_studio.db"
    if not db_path.exists():
        print(f"  DB not found, skipping update")
        return
    
    # Use sqlite3 directly
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    
    # Check if shoot exists
    cur.execute("SELECT id, generated_images FROM shoots WHERE id = ?", (shoot_id,))
    row = cur.fetchone()
    if not row:
        print(f"  Shoot {shoot_id[:8]} not in DB, skipping update")
        conn.close()
        return
    
    # Build image paths list
    image_paths = [f"storage/shoots/{shoot_id}/shot_{i+1:02d}.png" for i in range(image_count)]
    
    # Update the shoot
    cur.execute("""
        UPDATE shoots 
        SET generated_images = ?, progress = 100.0, status = 'completed'
        WHERE id = ?
    """, (json.dumps(image_paths), shoot_id))
    
    conn.commit()
    conn.close()
    print(f"  Updated shoot {shoot_id[:8]}: {image_count} images, 100% progress")


if __name__ == "__main__":
    asyncio.run(generate_shoot_images())
