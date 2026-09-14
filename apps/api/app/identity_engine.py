"""Persona Studio — Identity Engine.

Central module for all identity-locked image generation.
Every image generated for a persona goes through this engine,
ensuring consistent facial identity across shoots, gallery, and content.

The provider comes from the registry (DashScope Qwen-Image Edit in hybrid
mode, the deterministic mock in mock mode). When a real provider executes,
the avatar is passed as the reference image so the face stays consistent.
"""

from __future__ import annotations

import io
import hashlib
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Database path — overridable via PERSONA_STUDIO_DB (tests point this at the
# test database so the sync engine reads the same rows the async API writes).
def _db_path() -> Path:
    env = os.getenv("PERSONA_STUDIO_DB", "")
    if env:
        return Path(env)
    return Path(__file__).parent.parent / "persona_studio.db"


DB_PATH = _db_path()
AVATAR_DIR = Path(__file__).parent.parent / "storage" / "avatars"
SHOOT_DIR = Path(__file__).parent.parent / "storage" / "shoots"
GALLERY_DIR = Path(__file__).parent.parent / "storage" / "gallery"


def _get_db():
    """Get a synchronous sqlite3 connection."""
    return sqlite3.connect(str(_db_path()))


def get_identity_lock(persona_id_hex: str) -> Optional[dict]:
    """Get the identity lock for a persona.

    persona_id_hex is the full 32-char dashless UUID hex — the same key the
    async ORM path writes.

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

    from PIL import Image

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


def _mock_image_bytes(width: int, height: int, seed: int) -> bytes:
    """Deterministic placeholder PNG for the mock provider path."""
    from app.providers.mocks import _fake_png

    return _fake_png(width, height, seed)


async def generate_identity_locked(
    persona_id_hex: str,
    scene_prompt: str,
    output_path: str,
    width: int = 1024,
    height: int = 1024,
    seed_override: Optional[int] = None,
) -> dict:
    """Generate an identity-locked image.

    This is the single entry point for all image generation.

    1. Loads the identity lock (seed + prompt)
    2. Picks the configured image provider from the registry
    3. For a real edit-capable provider, loads the persona's avatar as the
       reference so the face stays consistent across every image
    4. Saves to output_path

    The result is honest about which provider executed: mock results carry
    is_mock=True and are never presented as real AI-provider output.
    """
    lock = get_identity_lock(persona_id_hex)
    if not lock:
        return {"success": False, "error": "No identity lock for persona"}

    from app.providers.registry import get_registry

    registry = get_registry()
    primary = registry.get_image_provider()
    primary_is_mock = "mock" in type(primary).__name__.lower()

    ref_bytes = None
    if not primary_is_mock:
        ref_bytes = get_avatar_bytes(persona_id_hex)
        if not ref_bytes:
            return {
                "success": False,
                "error": (
                    "No avatar found for persona — the edit-based provider "
                    "needs a reference image to lock identity"
                ),
            }

    full_prompt = build_locked_prompt(lock, scene_prompt)
    seed = seed_override if seed_override is not None else lock["seed"]

    # Reference image: real edit-based providers need the avatar as the identity
    # anchor. Mock generation doesn't use a reference, so a missing avatar only
    # blocks the real path.
    ref_bytes = None
    if not primary_is_mock:
        ref_bytes = get_avatar_bytes(persona_id_hex)

    if primary_is_mock:
        # Deterministic development image — clearly labelled as mock.
        image_bytes = _mock_image_bytes(width, height, seed)
        provider_name = "mock_image"
        latency_ms = 0
        success, error = True, ""
        is_mock = True
        fallback_reason = ""
    else:
        if not ref_bytes:
            return {
                "success": False,
                "error": (
                    "No avatar found for persona — the edit-based provider "
                    "needs a reference image to lock identity"
                ),
            }
        result = await primary.edit_image(
            reference_image_bytes=ref_bytes,
            prompt=full_prompt,
            negative_prompt=lock["negative_prompt"],
            width=width,
            height=height,
            seed=seed,
        )
        success, error = result.success, result.error
        image_bytes = result.data.get("image_bytes") if success else None
        provider_name = result.provider
        latency_ms = result.latency_ms
        is_mock = False
        fallback_reason = ""

        if not success:
            # Provider failover: a configured real provider that fails at
            # runtime (auth, quota, outage) must not silently kill the
            # pipeline. Fall through to the deterministic mock, but mark the
            # output honestly so nothing downstream can present it as real
            # provider output.
            registry.record_runtime_error("image", f"{provider_name}: {error}")
            mock_provider = registry.get_mock_image_provider()
            image_bytes = _mock_image_bytes(width, height, seed)
            provider_name = f"mock_image (failover from {provider_name})"
            is_mock = True
            fallback_reason = f"{error}"
            success, latency_ms = True, latency_ms

    if not success or not image_bytes:
        return {
            "success": False,
            "error": error or f"{provider_name} returned no image",
            "provider": provider_name,
        }

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(image_bytes)

    return {
        "success": True,
        "image_path": str(out),
        "provider": provider_name,
        "is_mock": is_mock,
        "fallback_reason": fallback_reason,
        "seed": seed,
        "prompt": full_prompt,
        "size_bytes": len(image_bytes),
        "latency_ms": latency_ms,
    }


async def generate_shoot_image(
    persona_id_hex: str,
    shoot_id_hex: str,
    shot_index: int,
    scene_prompt: str,
) -> dict:
    """Generate a single shot image with identity lock.

    Saves to storage/shoots/{shoot_id_hex_prefix}/shot_{index:02d}.png
    """
    output = str(SHOOT_DIR / shoot_id_hex[:8] / f"shot_{shot_index:02d}.png")
    # md5-derived seed: hash() is salted per process and would change on restart
    seed = int(hashlib.md5(f"{shoot_id_hex}_{shot_index}".encode()).hexdigest()[:8], 16) % 2147483647
    return await generate_identity_locked(
        persona_id_hex=persona_id_hex,
        scene_prompt=scene_prompt,
        output_path=output,
        seed_override=seed,
    )


async def generate_gallery_image(
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
    # md5-derived seed: hash() is salted per process and would change on restart
    seed = int(hashlib.md5(variant_name.encode()).hexdigest()[:8], 16) % 2147483647
    return await generate_identity_locked(
        persona_id_hex=persona_id_hex,
        scene_prompt=scene_prompt,
        output_path=output,
        seed_override=seed,
    )
