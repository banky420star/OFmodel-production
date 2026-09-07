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
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Shoot, ContentPack, GeneratedImage, GeneratedVideo,
    GeneratedVoice, QAResult,
    ShootStatus, ContentPackStatus, QAStatus,
)
from app.providers.registry import get_registry


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
    """Generate images for the shoot."""
    shoot_id = input_data.get("shoot_id")
    image_count = input_data.get("image_count", 10)
    theme = input_data.get("theme", "lifestyle")

    generated = []
    img = _get_image()
    for i in range(image_count):
        result = await img.generate(
            prompt=f"{theme} lifestyle photo, professional, high quality, shot {i+1}",
            negative_prompt="blurry, low quality",
            seed=random.randint(0, 2**31),
        )
        if result.success:
            img = GeneratedImage(
                id=uuid4(),
                workflow_id=workflow_id,
                prompt=result.data.get("prompt", ""),
                image_key=result.data.get("image_key", ""),
                seed=result.data.get("seed", 0),
                generation_time_ms=result.data.get("generation_time_ms", 0),
                metadata_json={"shoot_id": shoot_id, "shot_index": i, "is_mock": True},
            )
            db.add(img)
            generated.append(str(img.id))

    # Update shoot
    if shoot_id:
        shoot = await db.get(Shoot, UUID(shoot_id))
        if shoot:
            shoot.status = ShootStatus.COMPLETED
            shoot.generated_images = generated

    return {"image_count": len(generated), "image_ids": generated}


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
                metadata_json={"shoot_id": shoot_id, "is_mock": True},
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
                metadata_json={"is_mock": True},
            )
            db.add(v)
            generated.append(str(v.id))

    return {"voice_count": len(generated), "voice_ids": generated}


async def quality_check_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Run QA on generated content."""
    llm2 = _get_llm()
    result = await llm2.complete(
        system_prompt="Evaluate content quality for a synthetic creator content pack.",
        user_prompt="QA check for generated content pack",
    )
    content = result.data.get("content", {})
    score = content.get("identity_score", 0.92)

    qa = QAResult(
        id=uuid4(),
        workflow_id=workflow_id,
        qa_type="content_quality",
        status=QAStatus.PASSED if score >= 0.85 else QAStatus.FAILED,
        score=score,
        threshold=0.85,
        details=content,
        images_checked=input_data.get("image_count", 0) + input_data.get("video_count", 0),
        passed_count=input_data.get("image_count", 0),
        failed_count=0,
    )
    db.add(qa)

    return {
        "approved": qa.status == QAStatus.PASSED,
        "score": score,
        "qa_id": str(qa.id),
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
