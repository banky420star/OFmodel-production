"""Persona Studio — identity-locked generation engine.

All database reads go through the async ORM (the same database the rest of
the API writes through). There is deliberately NO sqlite3 side-channel here:
a second connection cannot see rows the request-session has not committed,
which is exactly the defect class that made identity locks invisible in the
previous codebase. When a caller already holds a session (routes, workflow
handlers) it is reused; otherwise the engine opens one from AsyncSessionLocal.

Real providers only: the registry has no mock adapters, so a missing
identity lock, a missing avatar, or a provider without edit support is an
honest failure — never a placeholder image.
"""

from __future__ import annotations

import io
import zlib
import logging
from pathlib import Path
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import IdentityLock, Persona

logger = logging.getLogger(__name__)

AVATAR_DIR = Path(__file__).parent.parent / "storage" / "avatars"
SHOOT_DIR = Path(__file__).parent.parent / "storage" / "shoots"
GALLERY_DIR = Path(__file__).parent.parent / "storage" / "gallery"


def stable_seed(*parts: object) -> int:
    """Deterministic seed that survives API restarts.

    Python's salted hash() would give a different seed every process, silently
    regenerating a persona's face — crc32 of the same parts is stable forever.
    """
    return zlib.crc32("|".join(str(p) for p in parts).encode()) % 2147483647


async def get_identity_lock(
    persona_id_hex: str, db: Optional[AsyncSession] = None
) -> Optional[dict]:
    """Get the identity lock for a persona.

    persona_id_hex is the full 32-char dashless UUID hex — the same key the
    async ORM path writes (persona_storage_hex).

    Returns dict with keys: seed, identity_prompt, negative_prompt, style_tags
    or None if no lock exists.
    """
    if db is None:
        async with AsyncSessionLocal() as session:
            return await get_identity_lock(persona_id_hex, session)

    result = await db.execute(
        select(IdentityLock).where(IdentityLock.persona_id == persona_id_hex)
    )
    lock = result.scalar_one_or_none()
    if not lock:
        return None

    return {
        "seed": lock.seed,
        "identity_prompt": lock.identity_prompt,
        "negative_prompt": lock.negative_prompt,
        "style_tags": lock.style_tags if isinstance(lock.style_tags, list) else [],
    }


async def get_persona_name(
    persona_id_hex: str, db: Optional[AsyncSession] = None
) -> Optional[str]:
    """Get persona name from hex ID."""
    if db is None:
        async with AsyncSessionLocal() as session:
            return await get_persona_name(persona_id_hex, session)

    result = await db.execute(select(Persona).where(Persona.id == UUID(persona_id_hex)))
    persona = result.scalar_one_or_none()
    return persona.name if persona else None


async def get_avatar_bytes(
    persona_id_hex: str, db: Optional[AsyncSession] = None
) -> Optional[bytes]:
    """Get the persona's avatar as resized reference bytes for edit API."""
    name = await get_persona_name(persona_id_hex, db)
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


async def generate_identity_locked(
    persona_id_hex: str,
    scene_prompt: str,
    output_path: str,
    width: int = 1024,
    height: int = 1024,
    seed_override: Optional[int] = None,
    db: Optional[AsyncSession] = None,
) -> dict:
    """Generate an identity-locked image.

    This is the single entry point for all image generation.

    1. Loads the identity lock (seed + prompt) through the async ORM
    2. Picks the configured image provider from the strict registry
    3. Loads the persona's avatar as the edit reference so the face stays
       consistent across every image
    4. Saves to output_path

    A missing lock, missing avatar, or provider without edit support is a
    real failure — no placeholder is ever substituted.
    """
    lock = await get_identity_lock(persona_id_hex, db)
    if not lock:
        return {"success": False, "error": "No identity lock for persona"}

    from app.providers.registry import get_registry

    provider = get_registry().get_image_provider()

    edit = getattr(provider, "edit_image", None)
    if edit is None:
        return {
            "success": False,
            "error": (
                f"Provider '{type(provider).__name__}' does not support "
                "identity-locked edit generation — set IMAGE_PROVIDER=dashscope"
            ),
        }

    ref_bytes = await get_avatar_bytes(persona_id_hex, db)
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

    result = await edit(
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
    db: Optional[AsyncSession] = None,
) -> dict:
    """Generate a single shot image with identity lock.

    Saves to storage/shoots/{shoot_id_hex_prefix}/shot_{index:02d}.png
    """
    output = str(SHOOT_DIR / shoot_id_hex[:8] / f"shot_{shot_index:02d}.png")
    return await generate_identity_locked(
        persona_id_hex=persona_id_hex,
        scene_prompt=scene_prompt,
        output_path=output,
        seed_override=stable_seed(shoot_id_hex, shot_index),
        db=db,
    )


async def generate_gallery_image(
    persona_id_hex: str,
    variant_name: str,
    scene_prompt: str,
    db: Optional[AsyncSession] = None,
) -> dict:
    """Generate a gallery variation with identity lock.

    Saves to storage/gallery/{persona_name}_{variant}.png
    """
    name = await get_persona_name(persona_id_hex, db)
    if not name:
        return {"success": False, "error": "Persona not found"}

    output = str(GALLERY_DIR / f"{name.lower()}_{variant_name}.png")
    return await generate_identity_locked(
        persona_id_hex=persona_id_hex,
        scene_prompt=scene_prompt,
        output_path=output,
        seed_override=stable_seed(variant_name),
        db=db,
    )