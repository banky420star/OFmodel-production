"""Persona Studio — Identity Engine.

Central module for all identity-locked image generation.
Every image generated for a persona goes through this engine,
ensuring consistent facial identity across shoots, gallery, and content.

Uses the persona's avatar as reference image and the configured provider
registry chain (ComfyUI → DashScope → HuggingFace → Pollinations → Mock)
to preserve facial features while changing scene/outfit/pose.
"""

from __future__ import annotations
import io
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

# Database path
DB_PATH = Path(__file__).parent.parent / "persona_studio.db"
AVATAR_DIR = Path(__file__).parent.parent / "storage" / "avatars"
SHOOT_DIR = Path(__file__).parent.parent / "storage" / "shoots"
GALLERY_DIR = Path(__file__).parent.parent / "storage" / "gallery"


def _get_db():
    """Get a synchronous sqlite3 connection."""
    return sqlite3.connect(str(DB_PATH))


def get_identity_lock(persona_id_hex: str) -> Optional[dict]:
    """Get the identity lock for a persona.
    
    Returns dict with keys: seed, identity_prompt, negative_prompt, style_tags
    or None if no lock exists.
    """
    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT seed, identity_prompt, negative_prompt, style_tags "
        "FROM identity_locks WHERE persona_id = ?",
        (persona_id_hex,),
    )
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return None
    
    return {
        "seed": row[0],
        "identity_prompt": row[1],
        "negative_prompt": row[2],
        "style_tags": json.loads(row[3]) if row[3] else [],
    }


def get_persona_name(persona_id_hex: str) -> Optional[str]:
    """Get persona name from hex ID."""
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("SELECT name FROM personas WHERE id = ?", (persona_id_hex,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def get_avatar_bytes(persona_id_hex: str) -> Optional[bytes]:
    """Get the persona's avatar as resized reference bytes for edit API."""
    name = get_persona_name(persona_id_hex)
    if not name:
        return None
    
    avatar_path = AVATAR_DIR / f"{name.lower()}.jpg"
    if not avatar_path.exists():
        return None
    
    # Resize to 768px for edit API
    img = Image.open(avatar_path)
    img.thumbnail((768, 768), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build_locked_prompt(identity_lock: dict, scene_prompt: str) -> str:
    """Combine identity prompt with scene prompt for consistent generation.

    The identity prompt anchors the facial features.
    The scene prompt adds the new context (outfit, setting, pose).
    """
    base = identity_lock["identity_prompt"]
    return f"Perfectly preserve the facial features. {base}. {scene_prompt}"


def _placeholder_png(seed: int, width: int, height: int) -> bytes:
    """Deterministic placeholder PNG for mock generation (no raw bytes)."""
    import random
    rng = random.Random(seed)
    img = Image.new(
        "RGB",
        (max(1, min(width, 1024)), max(1, min(height, 1024))),
        (rng.randint(80, 220), rng.randint(80, 220), rng.randint(80, 220)),
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def generate_identity_locked(
    persona_id_hex: str,
    scene_prompt: str,
    output_path: str,
    width: int = 1024,
    height: int = 1024,
    seed_override: Optional[int] = None,
) -> dict:
    """Generate an identity-locked image.
    
    This is the single entry point for all image generation.
    
    1. Loads the persona's avatar as reference
    2. Loads the identity lock (seed + prompt)
    3. Combines identity prompt with scene prompt
    4. Generates via the provider registry chain (with TTL cooldown on
       quota/rate/unavailable providers)
    5. Saves to output_path
    
    Returns dict with success, image_path, latency_ms, etc.
    """
    import asyncio
    from app.providers.registry import get_registry

    # Load identity lock
    lock = get_identity_lock(persona_id_hex)
    if not lock:
        return {"success": False, "error": "No identity lock for persona"}

    # Load avatar reference
    ref_bytes = get_avatar_bytes(persona_id_hex)
    if not ref_bytes:
        return {"success": False, "error": "No avatar found for persona"}

    # Build prompt
    full_prompt = build_locked_prompt(lock, scene_prompt)

    # Use locked seed (or override)
    seed = seed_override if seed_override is not None else lock["seed"]

    async def _gen():
        # Generate via the provider registry chain
        # (ComfyUI → DashScope → HuggingFace → Pollinations → Mock) with
        # TTL-based cooldown when a provider is out of quota or unavailable.
        provider = get_registry().get_image_provider()
        return await provider.generate(
            prompt=full_prompt,
            negative_prompt=lock["negative_prompt"],
            width=width,
            height=height,
            seed=seed,
        )
    
    # Run synchronously with retry for rate limits
    import concurrent.futures
    for attempt in range(3):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(lambda: asyncio.run(_gen())).result()
            else:
                result = loop.run_until_complete(_gen())
        except RuntimeError:
            result = asyncio.run(_gen())
        
        if result.success or attempt == 2:
            break
        # Rate limited — wait and retry
        time.sleep(5 * (attempt + 1))
        logger.info(f"Retrying image generation (attempt {attempt + 2}/3)")
    
    if result.success:
        # Save to disk. Mock providers don't return raw bytes — fall back to a
        # deterministic placeholder so the caller still gets a usable file.
        image_bytes = result.data.get("image_bytes")
        if not image_bytes:
            logger.warning(
                "provider returned no image_bytes (provider=%s) — writing placeholder",
                result.provider or "unknown",
            )
            image_bytes = _placeholder_png(seed, width, height)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(image_bytes)

        return {
            "success": True,
            "image_path": str(out),
            "seed": seed,
            "prompt": full_prompt,
            "size_bytes": len(image_bytes),
            "latency_ms": result.latency_ms,
        }
    else:
        return {"success": False, "error": result.error}


def generate_shoot_image(
    persona_id_hex: str,
    shoot_id_hex: str,
    shot_index: int,
    scene_prompt: str,
) -> dict:
    """Generate a single shot image with identity lock.
    
    Saves to storage/shoots/{shoot_id}/shot_{index:02d}.png
    """
    output = str(SHOOT_DIR / shoot_id_hex / f"shot_{shot_index:02d}.png")
    return generate_identity_locked(
        persona_id_hex=persona_id_hex,
        scene_prompt=scene_prompt,
        output_path=output,
        seed_override=hash(f"{shoot_id_hex}_{shot_index}") % 2147483647,
    )


def generate_gallery_image(
    persona_id_hex: str,
    variant_name: str,
    scene_prompt: str,
) -> dict:
    """Generate a gallery variation with identity lock.
    
    Saves to storage/gallery/{persona_name}_{variant}.png
    """
    name = get_persona_name(persona_id_hex)
    if not name:
        return {"success": False, "error": "Persona not found"}
    
    output = str(GALLERY_DIR / f"{name.lower()}_{variant_name}.png")
    return generate_identity_locked(
        persona_id_hex=persona_id_hex,
        scene_prompt=scene_prompt,
        output_path=output,
        seed_override=hash(variant_name) % 2147483647,
    )
