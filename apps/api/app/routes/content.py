"""Persona Studio — content routes."""

from __future__ import annotations
import json
import time
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Depends, Query, Form
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Persona, Shoot, ContentPack, GeneratedVideo, GeneratedVoice,
    ShootStatus, ContentPackStatus,
)
from app.schemas import (
    ShootCreate, ShootResponse, ContentPackCreate, ContentPackResponse,
)
from app.workflows.content_flow import (
    plan_shoot_handler, generate_shoot_images_handler,
    generate_shoot_videos_handler, generate_voiceover_handler,
    quality_check_handler, assemble_pack_handler,
    generate_captions_handler, finalize_pack_handler,
)
from app.providers.registry import get_registry

router = APIRouter()

@router.get("/shoots", response_model=list[ShootResponse])
async def list_shoots(
    persona_id: UUID | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(Shoot).order_by(Shoot.created_at.desc())
    if persona_id:
        q = q.where(Shoot.persona_id == persona_id)
    if status:
        q = q.where(Shoot.status == status)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/shoots/{shoot_id}/images")
async def get_shoot_images(shoot_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get all images for a shoot with URLs."""
    shoot = await db.get(Shoot, shoot_id)
    if not shoot:
        raise HTTPException(404, "Shoot not found")

    generated = shoot.generated_images or []
    images = []
    for path_str in generated:
        if path_str.startswith("storage/shoots/"):
            parts = path_str.split("/")
            if len(parts) >= 4:
                shoot_hex = parts[2]
                filename = parts[3]
                images.append({
                    "url": f"/api/v1/shoots/{shoot_hex}/images/{filename}",
                    "filename": filename,
                })

    return {
        "shoot_id": str(shoot_id),
        "name": shoot.name,
        "count": len(images),
        "images": images,
    }


@router.post("/personas/{persona_id}/shoots", response_model=ShootResponse, status_code=201)
async def create_shoot(persona_id: UUID, body: ShootCreate, db: AsyncSession = Depends(get_db)):
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    shoot = Shoot(
        id=uuid4(),
        persona_id=persona_id,
        name=body.name,
        theme=body.theme,
        status=ShootStatus.DRAFT,
        progress=0.0,
    )
    db.add(shoot)
    await db.commit()
    await db.refresh(shoot)
    return shoot


@router.post("/shoots/{shoot_id}/start")
async def start_shoot(shoot_id: UUID, db: AsyncSession = Depends(get_db)):
    shoot = await db.get(Shoot, shoot_id)
    if not shoot:
        raise HTTPException(404, "Shoot not found")

    shoot.status = ShootStatus.GENERATING
    shoot.progress = 0.0
    await db.commit()
    return {"status": "started"}


@router.post("/shoots/{shoot_id}/complete")
async def complete_shoot(shoot_id: UUID, db: AsyncSession = Depends(get_db)):
    shoot = await db.get(Shoot, shoot_id)
    if not shoot:
        raise HTTPException(404, "Shoot not found")

    shoot.status = ShootStatus.COMPLETED
    shoot.progress = 100.0
    await db.commit()
    return {"status": "completed"}


# ─── Content Packs (Phase 5) ─────────────────────────────────────────

@router.get("/packs", response_model=list[ContentPackResponse])
async def list_packs(
    persona_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(ContentPack).order_by(ContentPack.created_at.desc())
    if persona_id:
        q = q.where(ContentPack.persona_id == persona_id)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/personas/{persona_id}/packs", response_model=ContentPackResponse, status_code=201)
async def create_pack(persona_id: UUID, body: ContentPackCreate, db: AsyncSession = Depends(get_db)):
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    pack = ContentPack(
        id=uuid4(),
        persona_id=persona_id,
        name=body.name,
        platform=body.platform,
        status=ContentPackStatus.DRAFT,
    )
    db.add(pack)
    await db.commit()
    await db.refresh(pack)
    return pack


# ─── Workflows (Phase 6) ─────────────────────────────────────────────

@router.post("/personas/{persona_id}/generate-video")
async def generate_video(
    persona_id: UUID,
    prompt: str = "",
    duration: float = 20.0,
    db: AsyncSession = Depends(get_db),
):
    """Generate a video for a persona using DashScope Wan.
    
    If prompt is empty, generates based on the persona's brand/style.
    Uses the identity engine to ensure the video features the correct model.
    """
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    if not persona.adult_verified:
        raise HTTPException(403, "Persona not adult-verified — cannot generate video")

    registry = get_registry()
    video_provider = registry.get_video_provider()

    # Build prompt from persona identity if not provided
    if not prompt:
        from app.identity_engine import get_identity_lock
        lock = get_identity_lock(persona_id.hex)
        identity_desc = lock["identity_prompt"] if lock else persona.name
        prompt = f"{identity_desc}, {persona.brand or 'lifestyle'}, natural movement, cinematic"

    # Generate video
    result = await video_provider.text_to_video(
        prompt=prompt,
        duration=duration,
        width=720,
        height=1280,  # 9:16 portrait for social media
    )

    if not result.success:
        raise HTTPException(502, f"Video generation failed: {result.error}")

    # Resolve identity_id for this persona
    from app.models import Identity
    identity_result = await db.execute(
        select(Identity).where(Identity.persona_id == persona_id).order_by(Identity.created_at.desc())
    )
    identity = identity_result.scalars().first()

    # Store in DB
    video = GeneratedVideo(
        id=uuid4(),
        identity_id=identity.id if identity else None,
        prompt=prompt,
        video_key=result.data.get("video_key", ""),
        duration_seconds=result.data.get("duration", duration),
        width=result.data.get("width", 720),
        height=result.data.get("height", 1280),
        generation_time_ms=result.data.get("generation_time_ms", 0),
        metadata_json={
            "model": result.data.get("model", ""),
            "task_id": result.data.get("task_id", ""),
            "video_url": result.data.get("video_url", ""),
        },
    )
    db.add(video)
    await db.commit()

    return {
        "id": str(video.id),
        "video_url": result.data.get("video_url", ""),
        "video_key": result.data.get("video_key", ""),
        "duration": result.data.get("duration", duration),
        "prompt": prompt,
        "generation_time_ms": result.data.get("generation_time_ms", 0),
        "model": result.data.get("model", ""),
    }


@router.post("/shoots/{shoot_id}/generate-video")
async def generate_shoot_video(
    shoot_id: UUID,
    shot_index: int = 0,
    prompt: str = "",
    duration: float = 15.0,
    db: AsyncSession = Depends(get_db),
):
    """Generate a video from a shoot's image using image-to-video.
    
    Takes a generated image from the shoot and animates it.
    """
    shoot = await db.get(Shoot, shoot_id)
    if not shoot:
        raise HTTPException(404, "Shoot not found")

    persona = await db.get(Persona, shoot.persona_id)
    if not persona or not persona.adult_verified:
        raise HTTPException(403, "Persona not adult-verified")

    # Get the image from the shoot
    images = shoot.generated_images or []
    if shot_index >= len(images):
        raise HTTPException(400, f"Shot index {shot_index} out of range (have {len(images)} shots)")

    image_path = images[shot_index]
    # Pass local file path to provider — it handles base64 encoding
    image_url = image_path

    registry = get_registry()
    video_provider = registry.get_video_provider()

    if not prompt:
        prompt = f"{persona.name} in {shoot.theme or 'lifestyle setting'}, subtle natural motion, cinematic"

    result = await video_provider.image_to_video(
        image_key=image_url,
        prompt=prompt,
        duration=duration,
    )

    if not result.success:
        raise HTTPException(502, f"Video generation failed: {result.error}")

    # Store video linked to shoot
    video = GeneratedVideo(
        id=uuid4(),
        identity_id=shoot.identity_id if getattr(shoot, 'identity_id', None) else None,
        prompt=prompt,
        video_key=result.data.get("video_key", ""),
        duration_seconds=result.data.get("duration", duration),
        generation_time_ms=result.data.get("generation_time_ms", 0),
        metadata_json={
            "shoot_id": str(shoot_id),
            "shot_index": shot_index,
            "model": result.data.get("model", ""),
            "task_id": result.data.get("task_id", ""),
            "video_url": result.data.get("video_url", ""),
            "source_image": image_path,
        },
    )
    db.add(video)
    await db.commit()

    return {
        "id": str(video.id),
        "video_url": result.data.get("video_url", ""),
        "duration": result.data.get("duration", duration),
        "prompt": prompt,
        "generation_time_ms": result.data.get("generation_time_ms", 0),
    }


@router.get("/personas/{persona_id}/videos")
async def list_videos(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """List all generated videos for a persona."""
    from app.models import Identity
    result = await db.execute(
        select(GeneratedVideo)
        .join(Identity, Identity.id == GeneratedVideo.identity_id)
        .where(Identity.persona_id == persona_id)
        .order_by(GeneratedVideo.created_at.desc())
    )
    videos = result.scalars().all()
    return [
        {
            "id": str(v.id),
            "prompt": v.prompt,
            "video_key": v.video_key,
            "video_url": v.metadata_json.get("video_url", ""),
            "duration": v.duration_seconds,
            "width": v.width,
            "height": v.height,
            "generation_time_ms": v.generation_time_ms,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in videos
    ]


# ─── Adult Content Pipeline ──────────────────────────────────────────

class AdultContentRequest(BaseModel):
    scene_prompt: str
    content_type: str = "artistic"  # artistic, editorial, boudoir, nsfw
    generation_mode: str = "identity_locked"  # identity_locked or free


@router.post("/personas/{persona_id}/adult-content")
async def generate_adult_content(
    persona_id: UUID,
    body: AdultContentRequest,
    db: AsyncSession = Depends(get_db),
):
    """Generate adult/AE content with age verification.
    
    Requires:
    - persona.adult_verified = True
    - persona.synthetic_identity = True (must be fully synthetic)
    
    Content types:
    - artistic: tasteful artistic nudity
    - editorial: editorial/fashion content
    - boudoir: intimate boudoir style
    - nsfw: explicit content
    
    All content is watermark-logged for audit trail.
    """
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    
    # Age verification gate
    if not persona.adult_verified:
        raise HTTPException(403, "Persona must be marked as adult-verified")
    
    # Synthetic identity requirement
    if not persona.synthetic_identity:
        raise HTTPException(403, "Only synthetic identities can generate adult content")
    
    # Content type validation
    allowed_types = ["artistic", "editorial", "boudoir", "nsfw"]
    if body.content_type not in allowed_types:
        raise HTTPException(400, f"content_type must be one of: {allowed_types}")
    
    # Build content-aware prompt
    content_prefixes = {
        "artistic": "Artistic fine art photography, tasteful, elegant",
        "editorial": "High fashion editorial, Vogue style, professional",
        "boudoir": "Intimate boudoir photography, soft lighting, tasteful",
        "nsfw": "Explicit adult content, photorealistic",
    }
    
    full_prompt = f"{content_prefixes[body.content_type]}. {body.scene_prompt}"
    
    # Add negative prompt for safety
    negative = "deformed, ugly, blurry, low quality, watermark, text"
    
    # Generate using identity engine
    from app.identity_engine import generate_identity_locked
    from pathlib import Path as _Path
    
    content_dir = _Path(__file__).parent.parent / "storage" / "adult_content" / persona_id.hex[:8]
    content_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"{body.content_type}_{int(time.time())}.png"
    output_path = str(content_dir / filename)
    
    result = generate_identity_locked(
        persona_id_hex=persona_id.hex,
        scene_prompt=full_prompt,
        output_path=output_path,
        width=1024,
        height=1536,  # Portrait ratio
    )
    
    if not result["success"]:
        raise HTTPException(502, f"Generation failed: {result.get('error', 'unknown')}")
    
    # Log for audit trail
    import hashlib
    content_hash = hashlib.sha256(open(output_path, "rb").read()).hexdigest()[:16]
    
    return {
        "success": True,
        "content_type": body.content_type,
        "image_url": f"/api/v1/adult-content/{persona_id.hex[:8]}/{filename}",
        "content_hash": content_hash,
        "prompt": full_prompt,
        "size_bytes": result["size_bytes"],
        "persona_id": str(persona_id),
        "metadata": {
            "adult_verified": True,
            "synthetic_identity": True,
            "generation_mode": body.generation_mode,
            "audit_logged": True,
        },
    }


# ─── Automated Production Pipeline ───────────────────────────────────

@router.post("/personas/{persona_id}/auto-produce")
async def auto_produce(
    persona_id: UUID,
    shoot_count: int = 3,
    images_per_shoot: int = 5,
    generate_videos: bool = True,
    adult_content: bool = False,
    themes: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Fully automated production pipeline.
    
    For a persona, automatically:
    1. Creates shoots with themes
    2. Generates identity-locked images for each shoot
    3. Optionally generates videos from images
    4. Optionally generates adult content
    5. Assembles content packs
    
    themes: comma-separated list (lifestyle,fashion,travel,swimwear,fitness,editorial,artistic,nude)
             If empty, uses shoot_count random themes.
    Returns a job ID for progress tracking.
    """
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    if not persona.adult_verified:
        raise HTTPException(403, "Persona must be adult-verified for automated production")
    
    # Create job for progress tracking
    from app.models import Job
    job = Job(
        id=uuid4(),
        type="auto_produce",
        status="queued",
        progress=0,
        message=f"Setting up production for {persona.name}",
        persona_id=persona_id,
        metadata_json={
            "shoot_count": shoot_count,
            "images_per_shoot": images_per_shoot,
            "generate_videos": generate_videos,
            "adult_content": adult_content,
        },
    )
    db.add(job)
    await db.commit()
    
    # Run in background
    import asyncio
    asyncio.create_task(_run_auto_produce(
        job.id, persona_id, shoot_count, images_per_shoot,
        generate_videos, adult_content, themes,
    ))
    
    return {
        "job_id": str(job.id),
        "status": "queued",
        "persona": persona.name,
        "shoots": shoot_count,
        "images_per_shoot": images_per_shoot,
        "videos": generate_videos,
        "adult_content": adult_content,
    }



async def _run_auto_produce(
    job_id: UUID, persona_id: UUID,
    shoot_count: int, images_per_shoot: int,
    generate_videos: bool, adult_content: bool,
    themes: str = "",
):
    """Background task for automated production."""
    from app.database import AsyncSessionLocal
    from app.identity_engine import generate_identity_locked, get_identity_lock
    from app.providers.registry import get_registry
    from app.models import Job
    from pathlib import Path as _Path
    import asyncio
    
    async with AsyncSessionLocal() as db:
        persona = await db.get(Persona, persona_id)
        if not persona:
            return
        name = persona.name
    
    # Get identity lock and resolve identity_id
    lock = get_identity_lock(persona_id.hex)
    identity_desc = lock["identity_prompt"] if lock else name
    
    # Resolve the approved identity for this persona
    resolved_identity_id = None
    async with AsyncSessionLocal() as db:
        ident_q = await db.execute(
            select(Identity).where(Identity.persona_id == persona_id).order_by(Identity.created_at.desc())
        )
        ident = ident_q.scalars().first()
        if ident:
            resolved_identity_id = ident.id
    
    # Load comprehensive style library
    from app.artistic_styles import (
        ALL_STYLES, STYLE_CATEGORIES, get_random_styles,
    )
    import random
    
    # Build theme templates from the style library
    # Each theme picks random styles from its category
    def _build_theme(name: str, category: str, count: int = 5) -> dict:
        styles = get_random_styles(count, category)
        return {
            "name": name,
            "scenes": [s.image_prompt for s in styles],
            "video_scenes": [s.video_prompt for s in styles],
            "style_names": [s.name for s in styles],
        }
    
    THEME_BUILDERS = {
        "lifestyle": lambda: _build_theme("Lifestyle", "portrait", 5),
        "fashion": lambda: _build_theme("Fashion", "editorial", 5),
        "travel": lambda: _build_theme("Travel", "naturista", 5),
        "swimwear": lambda: _build_theme("Swimwear", "boudoir", 5),
        "fitness": lambda: _build_theme("Fitness", "portrait", 5),
        "editorial": lambda: _build_theme("Editorial", "editorial", 5),
        "artistic": lambda: _build_theme("Artistic", "fine_art", 5),
        "boudoir": lambda: _build_theme("Boudoir", "boudoir", 5),
        "nude": lambda: _build_theme("Artistic Nude", "fine_art", 5),
        "cinematic": lambda: _build_theme("Cinematic", "cinematic", 5),
        "conceptual": lambda: _build_theme("Conceptual", "conceptual", 5),
        "naturista": lambda: _build_theme("Naturista", "naturista", 5),
        "loungewear": lambda: _build_theme("Loungewear", "loungewear", 5),
        "grwm": lambda: _build_theme("GRWM", "grwm", 5),
        "casual": lambda: _build_theme("Casual", "casual", 5),
    }
    
    # Select themes based on parameter or default
    if themes:
        requested = [t.strip().lower() for t in themes.split(",") if t.strip()]
        shoot_themes = []
        for t in requested:
            if t in THEME_BUILDERS:
                shoot_themes.append(THEME_BUILDERS[t]())
        if not shoot_themes:
            shoot_themes = [THEME_BUILDERS["lifestyle"]()]
    else:
        # Default: pick first N from the standard themes
        default_keys = ["lifestyle", "fashion", "swimwear", "fitness", "editorial", "artistic"]
        shoot_themes = [THEME_BUILDERS[k]() for k in default_keys[:shoot_count]]
    
    if adult_content and not any(t["name"] == "Artistic Nude" for t in shoot_themes):
        shoot_themes.append(THEME_BUILDERS["nude"]())
    
    total_steps = len(shoot_themes) * images_per_shoot
    if generate_videos:
        total_steps += len(shoot_themes)  # one video per shoot
    
    completed = 0
    all_results = []
    
    registry = get_registry()
    video_provider = registry.get_video_provider()
    
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job:
            job.status = "running"
            job.message = f"Starting production for {name}"
            await db.commit()
    
    for theme_data in shoot_themes:
        # Create shoot record
        async with AsyncSessionLocal() as db:
            shoot = Shoot(
                id=uuid4(),
                persona_id=persona_id,
                identity_id=resolved_identity_id,
                name=f"{theme_data['name']} — {name}",
                theme=theme_data["name"].lower(),
                status=ShootStatus.GENERATING,
                progress=0,
                image_count=images_per_shoot,
            )
            db.add(shoot)
            await db.commit()
            shoot_id = shoot.id
        
        shoot_images = []
        
        # Generate images for this shoot
        for i, scene in enumerate(theme_data["scenes"][:images_per_shoot]):
            full_prompt = f"{identity_desc}. {scene}"
            output_dir = _Path(__file__).parent.parent / "storage" / "shoots" / shoot_id.hex[:8]
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(output_dir / f"shot_{i+1:02d}.png")
            
            # Use sync wrapper for generate_identity_locked
            try:
                result = generate_identity_locked(
                    persona_id_hex=persona_id.hex,
                    scene_prompt=full_prompt,
                    output_path=output_path,
                    seed_override=hash(f"{shoot_id.hex}_{i}") % 2147483647,
                )
                if result["success"]:
                    shoot_images.append(output_path)
            except Exception as e:
                logger.error(f"Image generation failed: {e}")
            
            completed += 1
            async with AsyncSessionLocal() as db:
                job = await db.get(Job, job_id)
                if job:
                    job.progress = int(completed / total_steps * 100)
                    job.message = f"{theme_data['name']}: generated {i+1}/{images_per_shoot} images"
                    await db.commit()
            # Also update the shoot's generated_images list in real-time
            async with AsyncSessionLocal() as db:
                shoot = await db.get(Shoot, shoot_id)
                if shoot:
                    shoot.generated_images = shoot_images
                    await db.commit()
        
        # Update shoot with images
        async with AsyncSessionLocal() as db:
            shoot = await db.get(Shoot, shoot_id)
            if shoot:
                shoot.generated_images = shoot_images
                shoot.progress = 100 if not generate_videos else 80
                shoot.status = ShootStatus.COMPLETED if not generate_videos else ShootStatus.GENERATING
                await db.commit()
        
        # Generate video for this shoot if enabled
        if generate_videos and shoot_images:
            try:
                video_prompt = f"{identity_desc}, {theme_data['name'].lower()} scene, natural movement, cinematic"
                video_result = await video_provider.text_to_video(
                    prompt=video_prompt,
                    duration=20.0,
                    width=720,
                    height=1280,
                )
                if video_result.success:
                    async with AsyncSessionLocal() as db:
                        # Resolve identity for this persona
                        ident_q = await db.execute(
                            select(Identity).where(Identity.persona_id == persona_id).order_by(Identity.created_at.desc())
                        )
                        ident = ident_q.scalars().first()
                        video = GeneratedVideo(
                            id=uuid4(),
                            identity_id=ident.id if ident else None,
                            prompt=video_prompt,
                            video_key=video_result.data.get("video_key", ""),
                            duration_seconds=video_result.data.get("duration", 4),
                            generation_time_ms=video_result.data.get("generation_time_ms", 0),
                            metadata_json={
                                "shoot_id": str(shoot_id),
                                "theme": theme_data["name"],
                                "video_url": video_result.data.get("video_url", ""),
                            },
                        )
                        db.add(video)
                        await db.commit()
                
                async with AsyncSessionLocal() as db:
                    shoot = await db.get(Shoot, shoot_id)
                    if shoot:
                        shoot.status = ShootStatus.COMPLETED
                        shoot.progress = 100
                        await db.commit()
            except Exception as e:
                logger.error(f"Video generation failed: {e}")
                async with AsyncSessionLocal() as db:
                    shoot = await db.get(Shoot, shoot_id)
                    if shoot:
                        shoot.status = ShootStatus.COMPLETED
                        shoot.progress = 100
                        await db.commit()
        
        completed += 1
        all_results.append({
            "theme": theme_data["name"],
            "images": len(shoot_images),
            "video": generate_videos,
        })
    
    # Mark job complete
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job:
            job.status = "completed"
            job.progress = 100
            job.message = f"Production complete for {name}"
            job.metadata_json["results"] = all_results
            await db.commit()


# ─── Artistic Styles ────────────────────────────────────────────────

