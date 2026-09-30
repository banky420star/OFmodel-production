"""Persona Studio — Content pack workflow.

Flow:
    plan_shoot
    → generate_images
    → generate_videos
    → generate_voiceover
    → quality_check
    → assemble_pack
    → generate_captions
    → finalize
"""

from __future__ import annotations
import random
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Shoot, ContentPack, GeneratedImage, GeneratedVideo,
    GeneratedVoice, QAResult,
    ShootStatus, ContentPackStatus, QAStatus, persona_storage_hex,
)
from app import paths
from app.providers.registry import get_registry

# Same tree the identity engine writes shoots into, so a generated shot is
# findable by anything that already looks there (and by a human).
SHOOT_DIR = paths.SHOOT_DIR


def _get_llm():
    return get_registry().get_llm_provider()


def _get_image():
    return get_registry().get_image_provider()


def _get_video():
    return get_registry().get_video_provider()


def _get_voice():
    return get_registry().get_voice_provider()


def _get_storage():
    return get_registry().get_storage_provider()


async def plan_shoot_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Generate a shoot plan using LLM."""
    persona_id = input_data.get("persona_id")
    theme = input_data.get("theme", "lifestyle")

    llm = _get_llm()
    result = await llm.complete(
        system_prompt="Generate a detailed shoot plan for a synthetic creator.",
        user_prompt=f"Plan a {theme} shoot for the persona",
    )

    shoot = Shoot(
        id=uuid4(),
        persona_id=UUID(persona_id) if persona_id else uuid4(),
        name=f"{theme.title()} Shoot",
        status=ShootStatus.DRAFT,
        theme=theme,
        image_count=input_data.get("image_count", 10),
        metadata_json=result.data.get("content", {}),
    )
    db.add(shoot)
    await db.flush()

    return {
        "shoot_id": str(shoot.id),
        "plan": result.data.get("content", {}),
    }


async def generate_shoot_images_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Generate images for the shoot.

    Every image goes through `generate_identity_locked`, not the raw provider.
    The raw path took a `session_id` and produced whatever the prompt asked for,
    so a content pack could ship photos of a different face entirely — and it
    was the reason the persona's trained LoRA never applied: only the identity
    path passes `lora_path`, and only it supplies the locked avatar as the edit
    reference.
    """
    from app.identity_engine import generate_identity_locked, stable_seed

    shoot_id = input_data.get("shoot_id")
    image_count = input_data.get("image_count", 10)
    theme = input_data.get("theme", "lifestyle")

    shoot = None
    if shoot_id:
        try:
            shoot = await db.get(Shoot, UUID(str(shoot_id)))
        except ValueError:
            # A malformed id reaches here as a clear reason rather than as a
            # traceback in the step's error message.
            return {
                "image_count": 0,
                "image_ids": [],
                "images_persisted": 0,
                "identity_locked": False,
                "lora_applied": [],
                "errors": [f"'{shoot_id}' is not a shoot id"],
                "requested_count": image_count,
            }

    # Which persona these images must resemble. The workflow's input carries it
    # on a create-model run; on a shoot started from the UI it may exist only on
    # the shoot row, so it is read back from there rather than assumed absent.
    #
    # Normalized through persona_storage_hex: `get_identity_lock` keys the
    # identity_locks table by the dashless 32-char hex, so a dashed UUID finds
    # no lock and the shoot fails with "No identity lock for persona" on a
    # persona that plainly has one.
    persona_id = input_data.get("persona_id") or ""
    if not persona_id and shoot and shoot.persona_id:
        persona_id = str(shoot.persona_id)
    if persona_id:
        try:
            persona_id = persona_storage_hex(persona_id)
        except (ValueError, AttributeError):
            return {
                "image_count": 0,
                "image_ids": [],
                "images_persisted": 0,
                "identity_locked": False,
                "lora_applied": [],
                "errors": [f"'{persona_id}' is not a persona id"],
                "requested_count": image_count,
            }

    generated: list[str] = []
    persisted = 0
    errors: list[str] = []
    lora_names: set[str] = set()

    # Where this shoot's pixels go. They have to land somewhere: this handler
    # used to keep only the provider's image_key and drop the bytes, so nothing
    # downstream could look at an image even if it wanted to — which is how the
    # quality check ended up scoring a number it never measured.
    shot_dir = SHOOT_DIR / (str(shoot_id)[:8] if shoot_id else "unassigned")
    shot_dir.mkdir(parents=True, exist_ok=True)

    for i in range(image_count):
        scene_prompt = f"{theme} lifestyle photo, professional, high quality, shot {i+1}"
        seed = stable_seed(str(shoot_id or workflow_id), i)
        output_path = shot_dir / f"shot_{i:02d}.png"

        if persona_id:
            result = await generate_identity_locked(
                persona_id_hex=persona_id,
                scene_prompt=scene_prompt,
                output_path=str(output_path),
                seed_override=seed,
                db=db,
            )
            if not result.get("success"):
                # Named, not swallowed: a shoot with no images and no reason is
                # the failure this whole handler exists to stop repeating.
                errors.append(f"shot {i}: {result.get('error', 'unknown error')}")
                continue
            if result.get("lora_name"):
                lora_names.add(result["lora_name"])
            persisted += 1
            gen_img = GeneratedImage(
                id=uuid4(),
                workflow_id=workflow_id,
                prompt=result.get("prompt", scene_prompt),
                image_key=str(output_path),
                seed=result.get("seed", seed),
                generation_time_ms=result.get("latency_ms", 0),
                metadata_json={
                    "shoot_id": shoot_id,
                    "shot_index": i,
                    "provider": result.get("provider", ""),
                    "local_path": str(output_path),
                    "bytes_persisted": True,
                    "identity_locked": True,
                    "lora_name": result.get("lora_name", ""),
                },
            )
            db.add(gen_img)
            generated.append(str(gen_img.id))
            continue

        # No persona anywhere in this run: a generic theme shoot. Say so on the
        # image rather than letting it read as identity-locked later.
        img = _get_image()
        result = await img.generate(
            prompt=scene_prompt,
            negative_prompt="blurry, low quality",
            seed=random.randint(0, 2**31),
        )
        if not result.success:
            errors.append(f"shot {i}: {result.error or 'provider returned nothing'}")
            continue

        image_bytes = result.data.get("image_bytes")
        local_path = None
        if image_bytes:
            output_path.write_bytes(image_bytes)
            local_path = output_path
            persisted += 1

        gen_img = GeneratedImage(
            id=uuid4(),
            workflow_id=workflow_id,
            prompt=result.data.get("prompt", ""),
            image_key=result.data.get("image_key", ""),
            seed=result.data.get("seed", 0),
            generation_time_ms=result.data.get("generation_time_ms", 0),
            metadata_json={
                "shoot_id": shoot_id,
                "shot_index": i,
                "provider": result.provider,
                "local_path": str(local_path) if local_path else "",
                "bytes_persisted": bool(local_path),
                "identity_locked": False,
                "lora_name": "",
            },
        )
        db.add(gen_img)
        generated.append(str(gen_img.id))

    # Update shoot. A shoot that was supposed to be the persona and produced
    # nothing is FAILED — marking it COMPLETED would let assembly build a pack
    # around zero images and call it done.
    if shoot:
        shoot.generated_images = generated
        shoot.status = (
            ShootStatus.FAILED if (persona_id and not generated)
            else ShootStatus.COMPLETED
        )

    return {
        "image_count": len(generated),
        "image_ids": generated,
        "images_persisted": persisted,
        # An identity-locked shoot that produced nothing is a failure, not a
        # thin pack: the images would not have been the persona.
        "identity_locked": bool(persona_id),
        "lora_applied": sorted(lora_names),
        "errors": errors,
        "requested_count": image_count,
    }


async def generate_shoot_videos_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Generate videos for the shoot."""
    shoot_id = input_data.get("shoot_id")
    video_count = min(input_data.get("video_count", 2), 5)

    generated = []
    vid_prov = _get_video()
    for i in range(video_count):
        result = await vid_prov.text_to_video(
            prompt=f"{input_data.get('theme', 'lifestyle')} scene, shot {i+1}",
            duration=20.0,
        )
        if result.success:
            vid = GeneratedVideo(
                id=uuid4(),
                workflow_id=workflow_id,
                prompt=result.data.get("prompt", ""),
                video_key=result.data.get("video_key", ""),
                duration_seconds=result.data.get("duration", 4.0),
                fps=result.data.get("fps", 24),
                generation_time_ms=result.data.get("generation_time_ms", 0),
                metadata_json={"shoot_id": shoot_id, "provider": result.provider},
            )
            db.add(vid)
            generated.append(str(vid.id))

    return {"video_count": len(generated), "video_ids": generated}


async def generate_voiceover_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Generate voiceover for content."""
    persona_name = input_data.get("persona_name", "model")
    voice_style = input_data.get("voice_style", "South African English")

    voc = _get_voice()
    voice_result = await voc.create_voice(
        name=f"{persona_name}_shoot_voice",
        accent=voice_style,
    )
    voice_id = voice_result.data.get("voice_id", "")

    scripts = [
        f"Welcome to my {input_data.get('theme', 'lifestyle')} moment.",
        f"Living my best life, one day at a time.",
    ]

    generated = []
    for script in scripts:
        synth = await voc.synthesize(
            text=script,
            voice_id=voice_id,
        )
        if synth.success:
            v = GeneratedVoice(
                id=uuid4(),
                workflow_id=workflow_id,
                text=script,
                voice_key=synth.data.get("voice_key", ""),
                voice_id=voice_id,
                duration_seconds=synth.data.get("duration_seconds", 0),
                generation_time_ms=synth.data.get("generation_time_ms", 0),
                metadata_json={"provider": synth.provider},
            )
            db.add(v)
            generated.append(str(v.id))

    return {"voice_count": len(generated), "voice_ids": generated}


async def quality_check_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Curate the generated images — the real gate, not a scoreboard.

    What stood here asked the LLM "QA check for generated content pack", took
    `content.get("identity_score", 0.92)` — a value that PASSES — hardcoded
    `failed_count=0`, and set `images_checked` from the step's own input rather
    than counting anything. It could not fail and never read a pixel.

    Now: every image the previous step persisted is measured (resolution, focus,
    exposure, contrast), the NSFW classifier's score is recorded as a label
    only, and a local vision model is asked about generation artifacts when one
    is configured. A gate that runs no check reports REVIEW, never PASSED.
    """
    from app.config import get_settings
    from app.curation import curate_images
    from app.providers.moderation import get_moderator

    settings = get_settings()

    # Collect the images this workflow generated, then keep only the ones whose
    # bytes actually landed on disk. An image we cannot open is not a pass: it
    # is recorded as unverified and the gate reports it.
    rows = (await db.execute(
        select(GeneratedImage).where(GeneratedImage.workflow_id == workflow_id)
    )).scalars().all()

    local_paths: list[str] = []
    unverified: list[dict] = []
    for row in rows:
        path = (row.metadata_json or {}).get("local_path") or ""
        if path and Path(path).exists():
            local_paths.append(path)
        else:
            unverified.append({
                "image_id": str(row.id),
                "image_key": row.image_key,
                "reason": "generated bytes were not persisted, so nothing could be measured",
            })

    report = await curate_images(
        local_paths,
        vision_model=settings.CURATION_VISION_MODEL,
        ollama_url=settings.OLLAMA_URL,
    )

    # The NSFW classifier is a label here, never a gate. This pipeline exists to
    # produce explicit content, so a "nsfw" verdict marks the intended output —
    # gating on it would reject the product. It is recorded so the operator can
    # see what the classifier thought, and nothing more.
    labels = []
    moderator = None
    if local_paths and settings.MODERATION_PROVIDER:
        try:
            moderator = get_moderator()
            for path in local_paths[:5]:  # a sample; this is a label, not a gate
                verdict = await moderator.classify_image(path)
                labels.append({"path": path, **verdict})
        except Exception as exc:  # never let a label sink the build
            labels.append({"error": f"{type(exc).__name__}: {exc}"})

    if not local_paths:
        status = QAStatus.REVIEW
    elif report["status"] == "failed":
        status = QAStatus.FAILED
    elif report["status"] == "passed":
        status = QAStatus.PASSED
    else:
        status = QAStatus.REVIEW

    details = {
        "checks": report["reports"],
        "reasons": report["reasons"],
        "vision_judge": report["vision_judge"],
        "unverified_images": unverified,
        "nsfw_labels": labels,
        "images_expected": int(input_data.get("image_count", 0)),
        "images_measured": report["images_checked"],
    }

    qa = QAResult(
        id=uuid4(),
        workflow_id=workflow_id,
        qa_type="content_quality",
        status=status,
        score=report["score"],
        threshold=1.0,  # every measured image must pass; this is a gate, not an average
        details=details,
        images_checked=report["images_checked"],
        passed_count=report["passed_count"],
        failed_count=report["failed_count"] + len(unverified),
    )
    db.add(qa)
    await db.flush()

    return {
        "approved": status == QAStatus.PASSED,
        "status": status.value,
        "score": report["score"],
        "qa_id": str(qa.id),
        "images_measured": report["images_checked"],
        "images_unverified": len(unverified),
        "reasons": report["reasons"],
        "warnings": [
            f"{len(unverified)} generated image(s) could not be measured: their "
            "bytes were never persisted"
        ] if unverified else [],
    }


async def assemble_pack_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Assemble the content pack."""
    persona_id = input_data.get("persona_id")
    shoot_id = input_data.get("shoot_id")

    pack = ContentPack(
        id=uuid4(),
        persona_id=UUID(persona_id) if persona_id else uuid4(),
        name=f"{input_data.get('theme', 'Content').title()} Pack",
        status=ContentPackStatus.ASSEMBLED,
        platform=input_data.get("platform", "all"),
        images=input_data.get("step_1_output", {}).get("image_ids", []),
        videos=input_data.get("step_2_output", {}).get("video_ids", []),
        voiceovers=input_data.get("step_3_output", {}).get("voice_ids", []),
        captions=[],
    )
    db.add(pack)
    await db.flush()

    return {"pack_id": str(pack.id), "status": "assembled"}


async def generate_captions_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Generate captions for the content pack."""
    theme = input_data.get("theme", "lifestyle")

    captions = []
    llm3 = _get_llm()
    for i in range(5):
        result = await llm3.complete(
            system_prompt="Generate a social media caption for a lifestyle content creator.",
            user_prompt=f"Write a caption for a {theme} photo",
        )
        content = result.data.get("content", {})
        captions.append(content.get("caption", f"Shot {i+1} ✨"))

    return {"captions": captions, "count": len(captions)}


async def finalize_pack_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Finalize and publish-ready the content pack."""
    pack_id = input_data.get("pack_id") or input_data.get("step_5_output", {}).get("pack_id")
    if pack_id:
        pack = await db.get(ContentPack, UUID(pack_id))
        if pack:
            pack.captions = input_data.get("step_6_output", {}).get("captions", [])
            pack.status = ContentPackStatus.ASSEMBLED

    return {"status": "pack_finalized", "pack_id": pack_id}
