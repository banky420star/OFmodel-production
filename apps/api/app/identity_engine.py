"""Persona Studio — Identity Engine.

Central module for all identity-locked image generation.
Every image generated for a persona goes through this engine,
ensuring consistent facial identity across shoots, gallery, and content.

The provider comes from the registry. Real edit-capable providers always get a
real, persisted identity reference; prompt-only generation is never used as a
substitute for a missing face anchor.
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


def _db_path() -> Path:
    env = os.getenv("PERSONA_STUDIO_DB", "")
    if env:
        return Path(env)
    return Path(__file__).parent.parent / "persona_studio.db"


DB_PATH = _db_path()
API_ROOT = Path(__file__).parent.parent
STORAGE_DIR = API_ROOT / "storage"
AVATAR_DIR = STORAGE_DIR / "avatars"
SHOOT_DIR = STORAGE_DIR / "shoots"
GALLERY_DIR = STORAGE_DIR / "gallery"
DATASET_DIR = STORAGE_DIR / "datasets"


def _get_db():
    return sqlite3.connect(str(_db_path()))


def _json(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def get_identity_lock(persona_id_hex: str) -> Optional[dict]:
    """Get the identity lock for a persona."""
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
        "style_tags": _json(row[3], []),
    }


def get_persona_name(persona_id_hex: str) -> Optional[str]:
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("SELECT name FROM personas WHERE id = ?", (persona_id_hex,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def _path_from_reference(value: str | None) -> Path | None:
    """Resolve one DB/media reference to a local storage path.

    Only files inside apps/api/storage are accepted. This intentionally rejects
    arbitrary filesystem paths and remote URLs: the image edit provider needs
    bytes that belong to this persona and are under Studio storage control.
    """
    if not value or not isinstance(value, str):
        return None
    raw = value.strip().split("?", 1)[0]
    if raw.startswith("http://") or raw.startswith("https://"):
        return None

    if raw.startswith("/api/v1/avatars/"):
        path = AVATAR_DIR / Path(raw).name
    elif raw.startswith("/api/v1/gallery/"):
        path = GALLERY_DIR / Path(raw).name
    elif raw.startswith("/api/v1/shoots/"):
        parts = Path(raw).parts
        # /api/v1/shoots/<shoot-id>/images/<filename>
        try:
            idx = parts.index("shoots")
            shoot_id = parts[idx + 1]
            filename = parts[-1]
            direct = SHOOT_DIR / shoot_id / filename
            short = SHOOT_DIR / shoot_id.replace("-", "")[:8] / filename
            path = direct if direct.exists() else short
        except (ValueError, IndexError):
            return None
    else:
        path = Path(raw)
        if not path.is_absolute():
            path = API_ROOT / path

    try:
        resolved = path.resolve()
        storage = STORAGE_DIR.resolve()
        resolved.relative_to(storage)
    except (OSError, ValueError):
        return None
    return resolved


def _usable_reference(path: Path | None, min_bytes: int = 20_000) -> bool:
    if path is None or not path.exists() or not path.is_file():
        return False
    try:
        if path.stat().st_size < min_bytes:
            return False
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:
        return False


def get_identity_reference_path(persona_id_hex: str) -> Optional[Path]:
    """Resolve the best real face reference for a persona.

    Resolution order is conservative:
      1. canonical avatar in storage/avatars (jpg/jpeg/png/webp)
      2. Persona.avatar_url when it resolves into Studio storage
      3. approved identity.reference_images
      4. non-mock ReferenceDataset images for the locked identity
      5. an already-approved shot for this persona (legacy recovery)

    Mock reference datasets are explicitly skipped. Nothing is generated here;
    this function only recovers real bytes already persisted by the Studio.
    """
    conn = _get_db()
    cur = conn.cursor()

    cur.execute("SELECT name, avatar_url FROM personas WHERE id = ?", (persona_id_hex,))
    persona = cur.fetchone()
    if not persona:
        conn.close()
        return None
    name, avatar_url = persona

    candidates: list[Path] = []
    stem = (name or "").lower()
    if stem:
        for ext in ("jpg", "jpeg", "png", "webp"):
            candidates.append(AVATAR_DIR / f"{stem}.{ext}")
    avatar_path = _path_from_reference(avatar_url)
    if avatar_path:
        candidates.append(avatar_path)

    cur.execute(
        "SELECT identity_id FROM identity_locks WHERE persona_id = ?",
        (persona_id_hex,),
    )
    lock_row = cur.fetchone()
    identity_id = lock_row[0] if lock_row and lock_row[0] else None

    if identity_id:
        # UUIDs can appear dashed or dashless in older local databases.
        identity_keys = [identity_id]
        compact = str(identity_id).replace("-", "")
        if compact not in identity_keys:
            identity_keys.append(compact)

        for key in identity_keys:
            cur.execute(
                "SELECT reference_images, metadata_json FROM identities WHERE id = ?",
                (key,),
            )
            row = cur.fetchone()
            if row:
                refs = _json(row[0], [])
                meta = _json(row[1], {})
                for field in ("avatar_path", "image_path", "reference_image", "reference_path"):
                    p = _path_from_reference(meta.get(field)) if isinstance(meta, dict) else None
                    if p:
                        candidates.append(p)
                for ref in refs if isinstance(refs, list) else []:
                    p = _path_from_reference(ref)
                    if p:
                        candidates.append(p)

            cur.execute(
                "SELECT image_keys, metadata_json FROM reference_datasets "
                "WHERE identity_id = ? ORDER BY created_at DESC",
                (key,),
            )
            for image_keys, metadata_json in cur.fetchall():
                meta = _json(metadata_json, {})
                if isinstance(meta, dict) and bool(meta.get("is_mock")):
                    continue
                keys = _json(image_keys, [])
                for ref in keys if isinstance(keys, list) else []:
                    p = _path_from_reference(ref)
                    if p:
                        candidates.append(p)

    # Legacy recovery: a previously approved shot is a stronger anchor than
    # inventing a new face and lets old personas repair themselves.
    try:
        cur.execute(
            "SELECT asset_key FROM shot_plans "
            "WHERE persona_id = ? AND generation_status = 'APPROVED' "
            "AND asset_key IS NOT NULL AND asset_key != '' "
            "ORDER BY created_at DESC LIMIT 10",
            (persona_id_hex,),
        )
        for (asset_key,) in cur.fetchall():
            p = _path_from_reference(asset_key)
            if p:
                candidates.append(p)
    except sqlite3.OperationalError:
        # Older DB snapshots may not have shot_plans yet.
        pass
    finally:
        conn.close()

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if _usable_reference(candidate):
            logger.info("identity reference resolved for %s: %s", persona_id_hex, candidate)
            return candidate
    return None


def get_avatar_bytes(persona_id_hex: str) -> Optional[bytes]:
    """Return normalized PNG bytes for the persona's best real reference."""
    reference_path = get_identity_reference_path(persona_id_hex)
    if reference_path is None:
        return None

    from PIL import Image

    with Image.open(reference_path) as source:
        img = source.convert("RGB")
        img.thumbnail((768, 768), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build_locked_prompt(identity_lock: dict, scene_prompt: str) -> str:
    base = identity_lock["identity_prompt"]
    return f"Perfectly preserve the facial features. {base}. {scene_prompt}"


def _mock_image_bytes(width: int, height: int, seed: int) -> bytes:
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
    """Generate an identity-locked image with a persisted visual reference."""
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
                    "IDENTITY REFERENCE MISSING: no real persisted avatar, "
                    "approved identity reference, non-mock dataset image, or "
                    "approved prior shot is available"
                ),
            }

    full_prompt = build_locked_prompt(lock, scene_prompt)
    seed = seed_override if seed_override is not None else lock["seed"]

    if primary_is_mock:
        image_bytes = _mock_image_bytes(width, height, seed)
        provider_name = "mock_image"
        latency_ms = 0
        success, error = True, ""
        is_mock = True
        fallback_reason = ""
    else:
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
            registry.record_runtime_error("image", f"{provider_name}: {error}")
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
    output = str(SHOOT_DIR / shoot_id_hex[:8] / f"shot_{shot_index:02d}.png")
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
    name = get_persona_name(persona_id_hex)
    if not name:
        return {"success": False, "error": "Persona not found"}

    output = str(GALLERY_DIR / f"{name.lower()}_{variant_name}.png")
    seed = int(hashlib.md5(variant_name.encode()).hexdigest()[:8], 16) % 2147483647
    return await generate_identity_locked(
        persona_id_hex=persona_id_hex,
        scene_prompt=scene_prompt,
        output_path=output,
        seed_override=seed,
    )
