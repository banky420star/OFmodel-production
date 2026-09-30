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

from app import paths
from app.database import AsyncSessionLocal
from app.models import Identity, IdentityLock, IdentityStatus, Persona

logger = logging.getLogger(__name__)

# Re-exported under their historical names: tests patch these attributes
# directly, so they must stay module-level here even though app/paths.py owns
# the value.
AVATAR_DIR = paths.AVATAR_DIR
SHOOT_DIR = paths.SHOOT_DIR
GALLERY_DIR = paths.GALLERY_DIR


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


async def get_persona_adapter(
    persona_id_hex: str, db: Optional[AsyncSession] = None
) -> dict:
    """The persona's trained adapter *and the base it was trained against*.

    Both come off **one** identity row on purpose. Reading the LoRA from the
    READY identity and the training base in a second query would let the two
    land on different candidates, and the render gate would then compare a base
    against an adapter it does not describe — a mismatch invented by the lookup
    rather than found in the weights.

    Returns `{"lora_name": <ComfyUI-relative filename or "">,
              "recorded_base": <the recorded base dict, or None>}`.
    """
    if db is None:
        async with AsyncSessionLocal() as session:
            return await get_persona_adapter(persona_id_hex, session)

    result = await db.execute(
        select(Identity)
        .where(Identity.persona_id == UUID(persona_id_hex))
        .where(Identity.lora_model_path != "")
        .order_by(Identity.created_at.desc())
    )
    identities = result.scalars().all()
    if not identities:
        return {"lora_name": "", "recorded_base": None}

    # Prefer a READY identity: a rejected or failed candidate may carry a path
    # from an earlier attempt, and applying that would be worse than applying
    # nothing.
    chosen = identities[0]
    for identity in identities:
        if identity.status == IdentityStatus.READY:
            chosen = identity
            break

    metadata = chosen.metadata_json if isinstance(chosen.metadata_json, dict) else {}
    recorded = metadata.get("lora_training_base")
    return {
        "lora_name": chosen.lora_model_path,
        "recorded_base": recorded if isinstance(recorded, dict) else None,
    }


async def get_persona_lora_name(
    persona_id_hex: str, db: Optional[AsyncSession] = None
) -> str:
    """The persona's trained LoRA adapter, or "" when it has none.

    Returns the **ComfyUI-relative filename** the trainer published under, which
    is what LoraLoader resolves against its own models/loras directory. An
    absolute path would be accepted by the code and load nothing, so the
    trainer records this form on the identity and keeps the absolute path on the
    TrainingJob and QA rows.

    Empty is the honest answer for a persona whose training failed — and per the
    build's failure policy that persona is still ACTIVE and usable, so callers
    must treat "" as "no adapter", not as an error.
    """
    adapter = await get_persona_adapter(persona_id_hex, db)
    return adapter["lora_name"]


async def generate_identity_locked(
    persona_id_hex: str,
    scene_prompt: str,
    output_path: str,
    width: int = 1024,
    height: int = 1024,
    seed_override: Optional[int] = None,
    db: Optional[AsyncSession] = None,
    adult: bool = False,
) -> dict:
    """Generate an identity-locked image.

    This is the single entry point for all image generation.

    1. Loads the identity lock (seed + prompt) through the async ORM
    2. Picks the configured image provider from the strict registry
    3. Checks that any adapter it is about to apply was trained on the
       checkpoint that will render it, and refuses when that cannot be shown
    4. Loads the persona's avatar as the edit reference so the face stays
       consistent across every image
    5. Saves to output_path

    `adult=True` selects the fail-closed policy on step 3 — see
    `lora_base.PathPolicy`. Callers on the adult route pass it; everything else
    is plate content, where an unrecorded base warns rather than blocking.

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

    # Apply the persona's trained adapter, when it has one. Identity consistency
    # used to rest entirely on img2img off the locked avatar: the LoRA was
    # trained and then never applied, because no generation path passed
    # lora_path even though every provider accepted it. Providers that cannot
    # apply a LoRA (DashScope's edit API has no adapter concept) are not handed
    # one — the parameter is filtered by signature rather than assumed, so
    # adding support to another provider needs no change here.
    import inspect

    adapter = await get_persona_adapter(persona_id_hex, db)
    lora_name = adapter["lora_name"]

    # The identity gate — and the reason the adapter and its recorded base are
    # read together, off one identity row.
    #
    # `lora_base_state` was already computed for the persona summary and
    # surfaced by the API, while every render path walked straight past it: a
    # swapped base was reported after the sale instead of refused before it. The
    # measured case is Naomi's adapter, trained on `sd_xl_base_1.0` and rendered
    # on `RealVisXL_V4.0` — same SDXL family, and the face came back identical
    # to no adapter at all.
    #
    # Fires only when an adapter is about to be applied. With no LoRA there is no
    # base binding to violate, and "" is the documented state of a persona whose
    # training failed — still a usable persona, not an error.
    if lora_name:
        from app.config import get_settings
        from app.lora_base import GateError, PathPolicy, enforce_before_render

        try:
            decision = enforce_before_render(
                adapter["recorded_base"],
                get_settings().COMFYUI_CHECKPOINT,
                path=PathPolicy.ADULT if adult else PathPolicy.PLATE,
            )
        except GateError as exc:
            # Named, not softened into a generic provider error: the operator's
            # next action is a retrain, and the reason has to say which base.
            logger.warning(
                "identity_gate_refused persona=%s state=%s reason=%s",
                persona_id_hex, exc.decision.state, exc.decision.reason,
            )
            return {
                "success": False,
                "error": f"identity gate: {exc.decision.reason}",
                "identity_gate_state": exc.decision.state,
                "lora_name": lora_name,
            }
        if decision.warn:
            logger.warning(
                "identity_gate_unverified persona=%s reason=%s",
                persona_id_hex, decision.reason,
            )

    lora_applied = bool(lora_name) and "lora_path" in inspect.signature(edit).parameters
    if lora_name and not lora_applied:
        logger.warning(
            "lora_not_applicable_to_provider persona=%s provider=%s lora=%s",
            persona_id_hex, type(provider).__name__, lora_name,
        )

    edit_kwargs: dict = {}
    if lora_applied:
        edit_kwargs["lora_path"] = lora_name

    result = await edit(
        reference_image_bytes=ref_bytes,
        prompt=full_prompt,
        negative_prompt=lock["negative_prompt"],
        width=width,
        height=height,
        seed=seed,
        **edit_kwargs,
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
        # Reported so a caller can tell an identity-locked image that used the
        # persona's own adapter from one that fell back to pure img2img.
        "lora_name": lora_name if lora_applied else "",
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