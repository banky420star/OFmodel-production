"""Persona Studio — Complete API routes for all 14 phases."""

from __future__ import annotations
import json
import time
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Persona, Identity, ReferenceDataset, Workflow, WorkflowStep,
    TrainingJob, QAResult, GeneratedImage, GeneratedVideo, GeneratedVoice,
    Shoot, ContentPack, ScheduledPost, AnalyticsSnapshot, Forecast,
    PersonaStatus, IdentityStatus, WorkflowStatus, ShootStatus,
    ContentPackStatus, QAStatus,
)
from app.schemas import (
    PersonaCreate, PersonaResponse, IdentityResponse,
    ShootCreate, ShootResponse, ContentPackCreate, ContentPackResponse,
    QAResponse, WorkflowResponse, WorkflowStepResponse,
    AnalyticsSnapshotResponse, ForecastResponse, ForecastScenario,
    ScheduledPostResponse, HealthCheck, SystemHealth, AppearanceProfile,
)
from app.workflows.engine import workflow_engine
from app.workflows.persona_flow import (
    create_persona_handler, generate_candidates_handler,
    approve_identity_handler, build_reference_dataset_handler,
    train_lora_handler, validate_identity_handler,
    create_voice_handler, activate_persona_handler,
)
from app.workflows.content_flow import (
    plan_shoot_handler, generate_shoot_images_handler,
    generate_shoot_videos_handler, generate_voiceover_handler,
    quality_check_handler, assemble_pack_handler,
    generate_captions_handler, finalize_pack_handler,
)
from app.providers.registry import get_registry

router = APIRouter(tags=["persona-studio"])


# ─── Dashboard (Phase 14) ────────────────────────────────────────────

@router.get("/dashboard/summary")
async def dashboard_summary(db: AsyncSession = Depends(get_db)):
    """Aggregate dashboard metrics from the database."""
    # Active personas
    personas_result = await db.execute(select(Persona))
    personas = personas_result.scalars().all()
    active_personas = [p for p in personas if p.status in (PersonaStatus.ACTIVE, PersonaStatus.BUILDING)]

    # Shoots
    shoots_result = await db.execute(select(Shoot))
    all_shoots = list(shoots_result.scalars().all())
    active_shoots = [s for s in all_shoots if s.status in (ShootStatus.DRAFT, ShootStatus.GENERATING)]

    # Get persona names for shoots
    persona_map = {str(p.id): p for p in personas}

    # Packs
    packs_result = await db.execute(select(ContentPack))
    packs = packs_result.scalars().all()

    # Analytics totals
    analytics_result = await db.execute(select(AnalyticsSnapshot))
    analytics = analytics_result.scalars().all()
    total_revenue = sum(a.revenue for a in analytics) if analytics else 0
    total_followers = sum(a.followers for a in analytics[-8:]) if analytics else 0
    avg_engagement = (
        sum(a.engagement_rate for a in analytics) / len(analytics)
        if analytics else 0
    )

    # Health
    registry = get_registry()
    health = registry.health_report()
    services_online = sum(1 for v in health.values() if v.get("status") == "green")
    services_total = len(health)

    # Build health checks array for frontend
    health_checks = [
        {"service": k, "status": v["status"]}
        for k, v in health.items()
    ]

    # Build shoots list for frontend
    shoots_list = [
        {
            "id": str(s.id),
            "name": s.name,
            "status": s.status.value if hasattr(s.status, 'value') else str(s.status),
            "asset_type": "image",
            "progress": s.progress or 0,
            "image_count": s.image_count or 0,
            "generated_images": s.generated_images or [],
            "theme": s.theme or "",
            "persona_name": persona_map.get(str(s.persona_id), None) and persona_map[str(s.persona_id)].name or "Unknown",
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in all_shoots[:5]
    ]

    # Build persona list for frontend
    personas_list = [
        {
            "id": str(p.id),
            "name": p.name,
            "age": p.age or 0,
            "status": p.status.value if hasattr(p.status, 'value') else str(p.status),
            "brand": p.brand or "",
            "identity_score": None,
            "identity_status": None,
            "packs_count": 0,
            "avatar_url": p.avatar_url or "",
            "shoots_count": len([s for s in all_shoots if str(s.persona_id) == str(p.id)]),
        }
        for p in personas
    ]

    # Attention items (personas needing review)
    attention = []
    for p in personas:
        if p.status == PersonaStatus.BUILDING:
            attention.append({"id": str(p.id), "name": p.name, "type": "building", "status": "warning"})

    return {
        "active_models": len([p for p in active_personas if p.status == PersonaStatus.ACTIVE]),
        "training_models": len([p for p in active_personas if p.status == PersonaStatus.BUILDING]),
        "total_models": len(personas),
        "total_packs": len(packs),
        "total_shoots": len(all_shoots),
        "revenue": round(total_revenue, 2),
        "followers": total_followers,
        "engagement_rate": round(avg_engagement, 4),
        "health": {
            "online": services_online,
            "total": services_total,
            "checks": health_checks,
        },
        "attention_items": attention,
        "shoots": shoots_list,
        "personas": personas_list,
    }


# ─── Personas (Phase 1) ─────────────────────────────────────────────

@router.get("/personas", response_model=list[PersonaResponse])
async def list_personas(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(Persona).order_by(Persona.created_at.desc())
    if status:
        q = q.where(Persona.status == status)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/personas", response_model=PersonaResponse, status_code=201)
async def create_persona(body: PersonaCreate, db: AsyncSession = Depends(get_db)):
    persona = Persona(
        id=uuid4(),
        name=body.name,
        status=PersonaStatus.BUILDING,
        appearance=body.appearance.model_dump() if body.appearance else None,
        personality=body.personality,
        voice_style=body.voice_style,
        brand=body.brand,
        publishing_frequency=body.publishing_frequency,
        metadata_json={},
    )
    db.add(persona)
    await db.commit()
    await db.refresh(persona)

    # Run the workflow in the background
    import asyncio
    asyncio.create_task(_run_persona_workflow(persona.id))

    return persona


async def _run_persona_workflow(persona_id: UUID):
    """Run the persona creation workflow in the background."""
    from app.database import AsyncSessionLocal
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        persona = await db.get(Persona, persona_id)
        if not persona:
            return

        input_data = {
            "persona_id": str(persona_id),
            "persona_name": persona.name,
            "name": persona.name,
            "age": persona.age,
            "appearance": persona.appearance or {},
            "personality": persona.personality or [],
            "voice_style": persona.voice_style or "",
            "brand": persona.brand or "",
            "publishing_frequency": persona.publishing_frequency or "",
            "adult_verified": True,
            "synthetic_identity": True,
        }

        workflow = await workflow_engine.create_workflow(
            name=f"persona_creation_{persona.name}",
            workflow_type="persona_creation",
            persona_id=persona_id,
            input_data=input_data,
            steps=[
                {"name": "generate_candidates", "step_type": "generate_candidates"},
                {"name": "approve_identity", "step_type": "approve_identity"},
                {"name": "build_reference_dataset", "step_type": "build_reference_dataset"},
                {"name": "train_lora", "step_type": "train_lora"},
                {"name": "validate_identity", "step_type": "validate_identity"},
                {"name": "create_voice", "step_type": "create_voice"},
                {"name": "activate_persona", "step_type": "activate_persona"},
            ],
        )

    # Register step handlers
    workflow_engine.register_step("generate_candidates", generate_candidates_handler)
    workflow_engine.register_step("approve_identity", approve_identity_handler)
    workflow_engine.register_step("build_reference_dataset", build_reference_dataset_handler)
    workflow_engine.register_step("train_lora", train_lora_handler)
    workflow_engine.register_step("validate_identity", validate_identity_handler)
    workflow_engine.register_step("create_voice", create_voice_handler)
    workflow_engine.register_step("activate_persona", activate_persona_handler)

    # Run workflow (opens its own session)
    await workflow_engine.run_workflow(workflow.id)

    # Update persona status after workflow completes
    async with AsyncSessionLocal() as db:
        persona = await db.get(Persona, persona_id)
        if persona and persona.status == PersonaStatus.BUILDING:
            persona.status = PersonaStatus.ACTIVE
            await db.commit()


@router.get("/personas/{persona_id}", response_model=PersonaResponse)
async def get_persona(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    return persona


# ─── Analytics (Phase 11) ────────────────────────────────────────────

class ManualAnalyticsInput(BaseModel):
    followers: int = 0
    engagement_rate: float = 0.0
    revenue: float = 0.0
    platform: str = "instagram"


@router.get("/personas/{persona_id}/analytics", response_model=list[AnalyticsSnapshotResponse])
async def get_analytics(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AnalyticsSnapshot)
        .where(AnalyticsSnapshot.persona_id == persona_id)
        .order_by(AnalyticsSnapshot.snapshot_date.desc())
    )
    return [
        AnalyticsSnapshotResponse(
            id=a.id, persona_id=a.persona_id, snapshot_date=a.snapshot_date,
            platform=a.platform, followers=a.followers, likes=a.likes,
            comments=a.comments, shares=a.shares, views=a.views,
            engagement_rate=a.engagement_rate, revenue=a.revenue, costs=a.costs,
        )
        for a in result.scalars().all()
    ]


@router.post("/personas/{persona_id}/analytics/generate")
async def generate_analytics(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """Generate mock analytics data for the last 90 days."""
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    now = datetime.now(timezone.utc)
    base_followers = random.randint(500, 5000)
    base_revenue = random.uniform(500, 5000)

    for day_offset in range(90):
        date = now - timedelta(days=90 - day_offset)
        growth = 1 + (day_offset * 0.003) + random.uniform(-0.01, 0.02)
        snap = AnalyticsSnapshot(
            id=uuid4(),
            persona_id=persona_id,
            snapshot_date=date,
            platform="all",
            followers=int(base_followers * growth),
            likes=random.randint(50, 500),
            comments=random.randint(10, 100),
            shares=random.randint(5, 50),
            views=random.randint(500, 5000),
            engagement_rate=round(random.uniform(0.02, 0.08), 4),
            revenue=round(base_revenue * growth * random.uniform(0.8, 1.2), 2),
            costs=round(random.uniform(100, 500), 2),
        )
        db.add(snap)

    await db.commit()
    return {"status": "generated", "days": 90, "source": "demo"}


@router.post("/personas/{persona_id}/analytics/sync")
async def sync_instagram_analytics(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """Sync real Instagram analytics into the database.

    Pulls real data from the Instagram Graph API and stores it as
    AnalyticsSnapshot records. Returns the synced data.
    """
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    registry = get_registry()
    ig = registry.get_instagram_provider()
    if not ig:
        raise HTTPException(
            400,
            "Instagram not configured. Set INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID in .env"
        )

    try:
        analytics = await ig.sync_analytics()
    except Exception as e:
        raise HTTPException(502, f"Instagram API error: {e}")

    # Store as AnalyticsSnapshot
    now = datetime.now(timezone.utc)
    snap = AnalyticsSnapshot(
        id=uuid4(),
        persona_id=persona_id,
        snapshot_date=now,
        platform="instagram",
        followers=analytics.profile.followers_count,
        likes=analytics.total_likes,
        comments=analytics.total_comments,
        shares=analytics.total_shares,
        views=analytics.total_impressions,
        engagement_rate=analytics.avg_engagement_rate / 100,  # convert percentage to decimal
        revenue=0,  # Instagram doesn't provide revenue
        costs=0,
    )
    db.add(snap)
    await db.commit()

    return {
        "status": "synced",
        "source": "instagram",
        "synced_at": analytics.synced_at,
        "profile": {
            "username": analytics.profile.username,
            "followers": analytics.profile.followers_count,
            "following": analytics.profile.follows_count,
            "media_count": analytics.profile.media_count,
        },
        "metrics": {
            "total_reach": analytics.total_reach,
            "total_impressions": analytics.total_impressions,
            "total_likes": analytics.total_likes,
            "total_comments": analytics.total_comments,
            "total_saves": analytics.total_saves,
            "total_shares": analytics.total_shares,
            "engagement_rate": analytics.avg_engagement_rate,
        },
        "recent_posts": [
            {
                "id": m.media_id,
                "type": m.media_type,
                "caption": m.caption,
                "likes": m.like_count,
                "comments": m.comments_count,
                "reach": m.reach,
                "saved": m.saved,
            }
            for m in analytics.recent_media[:10]
        ],
    }


@router.post("/personas/{persona_id}/analytics/manual")
async def manual_analytics_entry(
    persona_id: UUID,
    body: ManualAnalyticsInput,
    db: AsyncSession = Depends(get_db),
):
    """Manually enter real analytics data (for platforms without API access)."""
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    snap = AnalyticsSnapshot(
        id=uuid4(),
        persona_id=persona_id,
        snapshot_date=datetime.now(timezone.utc),
        platform=body.platform,
        followers=body.followers,
        likes=0,
        comments=0,
        shares=0,
        views=0,
        engagement_rate=body.engagement_rate,
        revenue=body.revenue,
        costs=0,
    )
    db.add(snap)
    await db.commit()

    return {"status": "saved", "source": "manual"}


# ─── Forecasts (Phase 12) ────────────────────────────────────────────

@router.get("/personas/{persona_id}/forecasts", response_model=list[ForecastResponse])
async def get_forecasts(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Forecast).where(Forecast.persona_id == persona_id).order_by(Forecast.created_at.desc())
    )
    forecasts = []
    for f in result.scalars().all():
        scenarios = []
        for scenario_name in ["conservative", "base", "aggressive"]:
            factor = {"conservative": 0.7, "base": 1.0, "aggressive": 1.3}[scenario_name]
            scenarios.append(ForecastScenario(
                scenario=scenario_name,
                horizon_months=f.horizon_months,
                monthly_followers=[int(v * factor) for v in (f.projected_followers or [])],
                monthly_revenue=[round(v * factor, 2) for v in (f.projected_revenue or [])],
                monthly_costs=[round(v * factor, 2) for v in (f.projected_costs or [])],
                monthly_engagement=[round(v * factor, 4) for v in (f.projected_engagement or [])],
                break_even_month=f.metadata_json.get("break_even_month"),
            ))
        forecasts.append(ForecastResponse(
            id=f.id, persona_id=f.persona_id, horizon_months=f.horizon_months,
            scenarios=scenarios, model_version=f.model_version, created_at=f.created_at,
        ))
    return forecasts


@router.post("/personas/{persona_id}/forecasts/generate")
async def generate_forecast(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """Generate deterministic 24-month forecast."""
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    # Get latest analytics
    snap_result = await db.execute(
        select(AnalyticsSnapshot)
        .where(AnalyticsSnapshot.persona_id == persona_id)
        .order_by(AnalyticsSnapshot.snapshot_date.desc())
        .limit(1)
    )
    latest = snap_result.scalar_one_or_none()

    base_followers = latest.followers if latest else 1000
    base_revenue = latest.revenue if latest else 1000

    # Deterministic 24-month projection
    followers = []
    revenue = []
    costs = []
    engagement = []
    monthly_growth = 0.05  # 5% monthly growth
    monthly_cost = 300.0   # base cost

    for month in range(24):
        factor = (1 + monthly_growth) ** month
        f = int(base_followers * factor)
        r = round(base_revenue * factor * random.uniform(0.9, 1.1), 2)
        c = round(monthly_cost + (f * 0.01), 2)  # cost scales with followers
        e = round(random.uniform(0.03, 0.06), 4)

        followers.append(f)
        revenue.append(r)
        costs.append(c)
        engagement.append(e)

    # Find break-even month
    break_even = None
    for i, (r, c) in enumerate(zip(revenue, costs)):
        if r > c:
            break_even = i + 1
            break

    forecast = Forecast(
        id=uuid4(),
        persona_id=persona_id,
        forecast_date=datetime.now(timezone.utc),
        horizon_months=24,
        projected_followers=followers,
        projected_revenue=revenue,
        projected_costs=costs,
        projected_engagement=engagement,
        model_version="deterministic_v1",
        metadata_json={"break_even_month": break_even},
    )
    db.add(forecast)
    await db.commit()

    return ForecastResponse(
        id=forecast.id,
        persona_id=forecast.persona_id,
        horizon_months=24,
        scenarios=[
            ForecastScenario(
                scenario="base",
                horizon_months=24,
                monthly_followers=followers,
                monthly_revenue=revenue,
                monthly_costs=costs,
                monthly_engagement=engagement,
                break_even_month=break_even,
            )
        ],
        model_version="deterministic_v1",
        created_at=forecast.created_at,
    )


# ─── Identities (Phase 2) ───────────────────────────────────────────

@router.get("/personas/{persona_id}/identities", response_model=list[IdentityResponse])
async def list_identities(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Identity).where(Identity.persona_id == persona_id).order_by(Identity.created_at.desc())
    )
    return result.scalars().all()


@router.post("/personas/{persona_id}/identities/{identity_id}/approve")
async def approve_identity(persona_id: UUID, identity_id: UUID, db: AsyncSession = Depends(get_db)):
    identity = await db.get(Identity, identity_id)
    if not identity or identity.persona_id != persona_id:
        raise HTTPException(404, "Identity not found")

    # Deactivate other identities
    others = await db.execute(
        select(Identity).where(Identity.persona_id == persona_id, Identity.id != identity_id)
    )
    for other in others.scalars().all():
        other.status = IdentityStatus.REJECTED

    identity.status = IdentityStatus.APPROVED
    await db.commit()
    return {"status": "approved", "identity_id": str(identity_id)}


# ─── Identity Lock (Consistent Identity) ─────────────────────────────

@router.get("/personas/{persona_id}/identity-lock")
async def get_identity_lock(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get the identity-lock seed and prompt for consistent image generation."""
    from sqlalchemy import text
    pid_hex = persona_id.hex
    result = await db.execute(
        text("SELECT seed, identity_prompt, negative_prompt, style_tags FROM identity_locks WHERE persona_id = :pid"),
        {"pid": pid_hex},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(404, "No identity lock for this persona")
    return {
        "persona_id": str(persona_id),
        "seed": row[0],
        "identity_prompt": row[1],
        "negative_prompt": row[2],
        "style_tags": row[3] if isinstance(row[3], list) else __import__('json').loads(row[3] or '[]'),
    }


@router.post("/personas/{persona_id}/generate-locked-image")
async def generate_locked_image(
    persona_id: UUID,
    scene_prompt: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Generate an image using the identity-locked seed + prompt.
    
    Combines the identity base prompt with a scene prompt,
    using the locked seed to ensure the same face every time.
    """
    from sqlalchemy import text

    pid_hex = persona_id.hex
    result = await db.execute(
        text("SELECT seed, identity_prompt, negative_prompt FROM identity_locks WHERE persona_id = :pid"),
        {"pid": pid_hex},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(404, "No identity lock for this persona")

    # Use identity engine for consistent face generation
    from app.identity_engine import generate_identity_locked
    
    persona = await db.get(Persona, persona_id)
    pid_hex = persona_id.hex
    avatar_dir = Path(__file__).parent.parent / "storage" / "avatars"
    filename = f"{persona.name.lower()}_locked.png"
    output_path = str(avatar_dir / filename)
    
    result = generate_identity_locked(
        persona_id_hex=pid_hex,
        scene_prompt=scene_prompt or "portrait, natural lighting, photorealistic",
        output_path=output_path,
    )
    
    if not result["success"]:
        raise HTTPException(502, f"Generation failed: {result.get('error', 'unknown')}")
    
    return {
        "seed": result["seed"],
        "prompt": result["prompt"],
        "avatar_url": f"/api/v1/avatars/{filename}",
        "size_bytes": result["size_bytes"],
        "latency_ms": result["latency_ms"],
    }


# ─── Gallery ─────────────────────────────────────────────────────────

import glob as glob_mod


@router.get("/personas/{persona_id}/gallery")
async def get_persona_gallery(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get all gallery images for a persona (avatar + variations)."""
    from sqlalchemy import text
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    name_lower = persona.name.lower()
    gallery_dir = Path(__file__).parent.parent / "storage" / "gallery"
    avatar_dir = Path(__file__).parent.parent / "storage" / "avatars"

    images = []

    # Avatar (main profile)
    avatar_path = avatar_dir / f"{name_lower}.jpg"
    if avatar_path.exists():
        images.append({
            "url": f"/api/v1/avatars/{name_lower}.jpg",
            "label": "Profile",
            "type": "avatar",
        })

    # Gallery variations
    pattern = f"{name_lower}_*.png"
    for f in sorted(gallery_dir.glob(pattern)):
        label = f.stem.replace(f"{name_lower}_", "").replace("_", " ").title()
        images.append({
            "url": f"/api/v1/gallery/{f.name}",
            "label": label,
            "type": "variation",
        })

    return {
        "persona_id": str(persona_id),
        "name": persona.name,
        "count": len(images),
        "images": images,
    }


# ─── Shoots (Phase 4) ────────────────────────────────────────────────

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

@router.get("/workflows", response_model=list[WorkflowResponse])
async def list_workflows(
    persona_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(Workflow).order_by(Workflow.created_at.desc())
    if persona_id:
        q = q.where(Workflow.persona_id == persona_id)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/workflows/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: UUID, db: AsyncSession = Depends(get_db)):
    workflow = await db.get(Workflow, workflow_id)
    if not workflow:
        raise HTTPException(404, "Workflow not found")
    return workflow


@router.get("/workflows/{workflow_id}/steps", response_model=list[WorkflowStepResponse])
async def get_workflow_steps(workflow_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(WorkflowStep).where(WorkflowStep.workflow_id == workflow_id).order_by(WorkflowStep.order)
    )
    return result.scalars().all()


# ─── Jobs (Phase 13) ─────────────────────────────────────────────────

@router.get("/jobs")
async def list_jobs(
    persona_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    from app.models import Job
    q = select(Job).order_by(Job.created_at.desc())
    if persona_id:
        q = q.where(Job.persona_id == persona_id)
    result = await db.execute(q)
    jobs = result.scalars().all()
    return [
        {
            "id": str(j.id),
            "persona_id": str(j.persona_id),
            "job_type": j.type,
            "status": j.status,
            "progress": j.progress,
            "current_step": j.metadata_json.get("current_step") if j.metadata_json else None,
            "total_steps": j.metadata_json.get("total_steps") if j.metadata_json else None,
            "result": j.metadata_json.get("result") if j.metadata_json else None,
            "error": j.message,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "updated_at": j.updated_at.isoformat() if j.updated_at else None,
        }
        for j in jobs
    ]


@router.get("/jobs/{job_id}")
async def get_job(job_id: UUID, db: AsyncSession = Depends(get_db)):
    from app.models import Job
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {
        "id": str(job.id),
        "persona_id": str(job.persona_id),
        "job_type": job.type,
        "status": job.status,
        "progress": job.progress,
        "current_step": job.metadata_json.get("current_step") if job.metadata_json else None,
        "total_steps": job.metadata_json.get("total_steps") if job.metadata_json else None,
        "result": job.metadata_json.get("result") if job.metadata_json else None,
        "error": job.message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


# ─── QA (Phase 10) ───────────────────────────────────────────────────

@router.get("/personas/{persona_id}/qa", response_model=list[QAResponse])
async def list_qa_results(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(QAResult).where(QAResult.persona_id == persona_id).order_by(QAResult.created_at.desc())
    )
    return result.scalars().all()


# ─── Scheduling (Phase 12) ───────────────────────────────────────────

@router.get("/personas/{persona_id}/schedule", response_model=list[ScheduledPostResponse])
async def get_schedule(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ScheduledPost).where(ScheduledPost.persona_id == persona_id).order_by(ScheduledPost.scheduled_at)
    )
    return result.scalars().all()


@router.post("/personas/{persona_id}/schedule/generate")
async def generate_schedule(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    now = datetime.now(timezone.utc)
    platforms = ["instagram", "tiktok", "youtube"]
    posts = []

    for day_offset in range(30):
        date = now + timedelta(days=day_offset)
        if date.weekday() < 5:  # Weekdays only
            for platform in random.sample(platforms, k=min(2, len(platforms))):
                post = ScheduledPost(
                    id=uuid4(),
                    persona_id=persona_id,
                    platform=platform,
                    scheduled_at=date.replace(hour=random.choice([9, 12, 15, 18]), minute=0),
                    status="scheduled",
                )
                db.add(post)
                posts.append(post)

    await db.commit()
    return {"status": "generated", "posts": len(posts)}


# ─── Autopilot ──────────────────────────────────────────────────────

@router.post("/personas/{persona_id}/autopilot")
async def toggle_autopilot(
    persona_id: UUID,
    mode: str = "off",
    db: AsyncSession = Depends(get_db),
):
    """Toggle autopilot mode for a persona."""
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    # Store mode in metadata
    if not persona.metadata_json:
        persona.metadata_json = {}
    persona.metadata_json["autopilot"] = mode
    await db.commit()

    return {"autopilot": mode, "persona_id": str(persona_id)}


# ─── Video Generation (Phase 8) ──────────────────────────────────────

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


@router.post("/personas/{persona_id}/batch-adult-content")
async def batch_generate_adult_content(
    persona_id: UUID,
    scenes: list[str],
    content_type: str = "artistic",
    db: AsyncSession = Depends(get_db),
):
    """Batch generate multiple adult content scenes.
    
    Returns a job ID for polling progress.
    """
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    if not persona.adult_verified or not persona.synthetic_identity:
        raise HTTPException(403, "Must be adult-verified synthetic identity")
    
    from app.models import Job
    job = Job(
        id=uuid4(),
        type="batch_adult_content",
        status="queued",
        progress=0,
        message=f"Generating {len(scenes)} {content_type} scenes",
        persona_id=persona_id,
        metadata_json={
            "scenes": scenes,
            "content_type": content_type,
            "total": len(scenes),
        },
    )
    db.add(job)
    await db.commit()
    
    # Run in background
    import asyncio
    asyncio.create_task(_run_batch_adult(job.id, persona_id, scenes, content_type))
    
    return {
        "job_id": str(job.id),
        "status": "queued",
        "total_scenes": len(scenes),
        "content_type": content_type,
    }


async def _run_batch_adult(job_id: UUID, persona_id: UUID, scenes: list[str], content_type: str):
    """Background task for batch adult content generation."""
    from app.database import AsyncSessionLocal
    from app.identity_engine import generate_identity_locked
    from pathlib import Path as _Path
    
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if not job:
            return
        job.status = "running"
        await db.commit()
    
    content_dir = _Path(__file__).parent.parent / "storage" / "adult_content" / persona_id.hex[:8]
    content_dir.mkdir(parents=True, exist_ok=True)
    
    content_prefixes = {
        "artistic": "Artistic fine art photography, tasteful, elegant",
        "editorial": "High fashion editorial, Vogue style, professional",
        "boudoir": "Intimate boudoir photography, soft lighting, tasteful",
        "nsfw": "Explicit adult content, photorealistic",
    }
    
    results = []
    for i, scene in enumerate(scenes):
        full_prompt = f"{content_prefixes[content_type]}. {scene}"
        filename = f"{content_type}_{i+1:02d}_{int(time.time())}.png"
        output_path = str(content_dir / filename)
        
        result = generate_identity_locked(
            persona_id_hex=persona_id.hex,
            scene_prompt=full_prompt,
            output_path=output_path,
            width=1024,
            height=1536,
        )
        
        results.append({
            "scene": scene,
            "success": result["success"],
            "url": f"/api/v1/adult-content/{persona_id.hex[:8]}/{filename}" if result["success"] else None,
        })
        
        # Update progress
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            if job:
                job.progress = int((i + 1) / len(scenes) * 100)
                job.message = f"Generated {i+1}/{len(scenes)} scenes"
                await db.commit()
    
    # Mark complete
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job:
            job.status = "completed"
            job.progress = 100
            succeeded = len([r for r in results if r.get("success")])
            job.message = f"Generated {succeeded}/{len(scenes)} scenes"
            job.metadata_json["results"] = results
            await db.commit()


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

@router.get("/styles")
async def list_styles(category: str = ""):
    """List available artistic styles for content production.
    
    Returns all 100+ styles grouped by category, or filtered to one category.
    """
    from app.artistic_styles import STYLE_CATEGORIES, ALL_STYLES, count_styles
    
    if category and category in STYLE_CATEGORIES:
        styles = STYLE_CATEGORIES[category]
        return {
            "category": category,
            "count": len(styles),
            "styles": [
                {
                    "name": s.name,
                    "category": s.category,
                    "image_prompt": s.image_prompt,
                    "video_prompt": s.video_prompt,
                    "lighting": s.lighting,
                    "mood": s.mood,
                }
                for s in styles
            ],
        }
    
    return {
        "total": count_styles(),
        "categories": {cat: len(styles) for cat, styles in STYLE_CATEGORIES.items()},
        "styles": [
            {"name": s.name, "category": s.category, "mood": s.mood}
            for s in ALL_STYLES
        ],
    }


# ─── Health ──────────────────────────────────────────────────────────

@router.get("/health")
async def health_check():
    """System health check across all providers."""
    registry = get_registry()
    health = registry.health_report()
    all_green = all(v.get("status") == "green" for v in health.values())

    return {
        "ok": all_green,
        "status": "healthy" if all_green else "degraded",
        "providers": health,
    }


# ─── Fan Chat (Revenue Layer) ────────────────────────────────────────

@router.get("/fans")
async def list_fans(
    persona_id: str | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List fans with spending and engagement data."""
    from app.models import Fan
    q = select(Fan).order_by(Fan.total_spent.desc())
    if persona_id:
        q = q.where(Fan.persona_id == persona_id)
    if status:
        q = q.where(Fan.status == status)
    result = await db.execute(q)
    fans = result.scalars().all()
    return [
        {
            "id": str(f.id),
            "persona_id": str(f.persona_id),
            "username": f.username,
            "display_name": f.display_name,
            "platform": f.platform,
            "status": f.status,
            "subscription_tier": f.subscription_tier,
            "total_spent": f.total_spent,
            "ppv_purchases": f.ppv_purchases,
            "tips_given": f.tips_given,
            "messages_sent": f.messages_sent,
            "messages_received": f.messages_received,
            "fan_score": f.fan_score,
            "tags": f.tags,
            "last_active": f.last_active.isoformat() if f.last_active else None,
            "last_message_at": f.last_message_at.isoformat() if f.last_message_at else None,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in fans
    ]


@router.post("/fans")
async def create_fan(
    persona_id: str = Query(...),
    username: str = Query(...),
    display_name: str = Query(""),
    platform: str = Query("onlyfans"),
    db: AsyncSession = Depends(get_db),
):
    """Register a new fan."""
    from app.models import Fan
    fan = Fan(
        persona_id=UUID(persona_id),
        username=username,
        display_name=display_name or username,
        platform=platform,
    )
    db.add(fan)
    await db.commit()
    await db.refresh(fan)
    return {"id": str(fan.id), "username": fan.username, "status": "created"}


@router.get("/fans/{fan_id}/messages")
async def list_fan_messages(
    fan_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Get chat history for a fan."""
    from app.models import ChatMessage, Fan
    fan_uuid = UUID(fan_id)
    fan = await db.get(Fan, fan_uuid)
    if not fan:
        raise HTTPException(404, "Fan not found")
    
    q = (
        select(ChatMessage)
        .where(ChatMessage.fan_id == fan_uuid)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(q)
    messages = result.scalars().all()
    messages.reverse()  # oldest first
    
    return [
        {
            "id": str(m.id),
            "direction": m.direction,
            "content": m.content,
            "message_type": m.message_type,
            "is_ai_generated": m.is_ai_generated,
            "is_ppv": m.is_ppv,
            "ppv_price": m.ppv_price,
            "sentiment": m.sentiment,
            "intent": m.intent,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]


@router.post("/fans/{fan_id}/reply")
async def auto_reply(
    fan_id: str,
    message: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Generate and send an AI reply to a fan message."""
    from app.models import Fan, ChatMessage
    from app.chat_engine import generate_chat_reply
    
    fan = await db.get(Fan, UUID(fan_id))
    if not fan:
        raise HTTPException(404, "Fan not found")
    
    persona = await db.get(Persona, fan.persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    
    # Save inbound message
    inbound = ChatMessage(
        fan_id=fan.id,
        persona_id=fan.persona_id,
        direction="inbound",
        content=message,
        message_type="text",
    )
    db.add(inbound)
    fan.messages_sent = (fan.messages_sent or 0) + 1
    fan.last_message_at = datetime.now(timezone.utc)
    
    # Get conversation history
    history_q = (
        select(ChatMessage)
        .where(ChatMessage.fan_id == fan.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(10)
    )
    history_result = await db.execute(history_q)
    history = [
        {"direction": m.direction, "content": m.content}
        for m in history_result.scalars().all()
    ]
    history.reverse()
    
    # Calculate days since last active
    days_since = 0
    if fan.last_active:
        days_since = (datetime.now(timezone.utc) - fan.last_active).days
    
    # Generate AI reply
    reply = await generate_chat_reply(
        persona_name=persona.name,
        brand=persona.brand or "lifestyle",
        personality=json.dumps(persona.personality) if persona.personality else "friendly, flirty",
        voice_style=persona.voice_style or "casual English",
        fan_message=message,
        conversation_history=history,
        fan_total_spent=fan.total_spent or 0,
        fan_ppv_purchases=fan.ppv_purchases or 0,
        fan_messages_sent=fan.messages_sent or 0,
        days_since_last_active=days_since,
    )
    
    # Save outbound message
    outbound = ChatMessage(
        fan_id=fan.id,
        persona_id=fan.persona_id,
        direction="outbound",
        content=reply.text,
        message_type="text",
        is_ai_generated=True,
        sentiment=reply.sentiment,
        intent=reply.intent,
    )
    db.add(outbound)
    fan.messages_received = (fan.messages_received or 0) + 1
    
    # Update fan score
    from app.chat_engine import _score_fan
    fan.fan_score = _score_fan(
        fan.total_spent or 0,
        fan.ppv_purchases or 0,
        fan.messages_sent or 0,
        days_since,
    )
    
    await db.commit()
    
    return {
        "reply": reply.text,
        "intent": reply.intent,
        "sentiment": reply.sentiment,
        "suggests_ppv": reply.suggests_ppv,
        "ppv_prompt": reply.ppv_prompt,
        "fan_score": fan.fan_score,
    }


@router.post("/fans/{fan_id}/ppv")
async def send_ppv(
    fan_id: str,
    content_key: str = Query(...),
    price: float = Query(...),
    caption: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    """Send a PPV message to a fan."""
    from app.models import Fan, ChatMessage
    
    fan = await db.get(Fan, UUID(fan_id))
    if not fan:
        raise HTTPException(404, "Fan not found")
    
    ppv_msg = ChatMessage(
        fan_id=fan.id,
        persona_id=fan.persona_id,
        direction="outbound",
        content=caption or "exclusive content 🔒",
        message_type="ppv",
        is_ppv=True,
        ppv_price=price,
        metadata_json={"content_key": content_key},
    )
    db.add(ppv_msg)
    await db.commit()
    
    return {"status": "sent", "ppv_price": price, "fan": fan.username}


@router.post("/fans/mass-message")
async def mass_message(
    persona_id: str = Query(...),
    message_type: str = Query("welcome"),
    fan_ids: list[str] | None = Query(None),
    custom_message: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    """Send a mass message to multiple fans."""
    from app.models import Fan, ChatMessage
    from app.chat_engine import generate_mass_message
    
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    
    # Get target fans
    if fan_ids:
        q = select(Fan).where(Fan.id.in_(fan_ids))
    else:
        q = select(Fan).where(Fan.persona_id == persona_id).where(Fan.status == "active")
    
    result = await db.execute(q)
    fans = result.scalars().all()
    
    sent = 0
    for fan in fans:
        msg_text = await generate_mass_message(
            persona_name=persona.name,
            brand=persona.brand or "lifestyle",
            personality=json.dumps(persona.personality) if persona.personality else "friendly",
            voice_style=persona.voice_style or "casual",
            message_type=message_type,
            fan_name=fan.display_name or fan.username,
            custom_context=custom_message,
        )
        
        msg = ChatMessage(
            fan_id=fan.id,
            persona_id=persona_id,
            direction="outbound",
            content=msg_text,
            message_type="text",
            is_ai_generated=True,
        )
        db.add(msg)
        sent += 1
    
    await db.commit()
    return {"sent": sent, "message_type": message_type}


@router.get("/fans/analytics")
async def fan_analytics(
    persona_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Aggregate fan metrics for the dashboard."""
    from app.models import Fan, ChatMessage
    from sqlalchemy import func
    
    q = select(Fan)
    if persona_id:
        q = q.where(Fan.persona_id == persona_id)
    result = await db.execute(q)
    fans = result.scalars().all()
    
    if not fans:
        return {
            "total_fans": 0,
            "total_revenue": 0,
            "avg_fan_score": 0,
            "whales": 0,
            "at_risk": 0,
            "new_this_week": 0,
            "top_fans": [],
        }
    
    total_spent = sum(f.total_spent or 0 for f in fans)
    avg_score = sum(f.fan_score or 0 for f in fans) / len(fans)
    whales = len([f for f in fans if (f.total_spent or 0) > 100])
    at_risk = len([f for f in fans if f.status == "inactive"])
    
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    new_this_week = len([f for f in fans if f.created_at and f.created_at > week_ago])
    
    # Top 10 fans by spending
    sorted_fans = sorted(fans, key=lambda f: f.total_spent or 0, reverse=True)[:10]
    
    return {
        "total_fans": len(fans),
        "total_revenue": round(total_spent, 2),
        "avg_fan_score": round(avg_score, 1),
        "whales": whales,
        "at_risk": at_risk,
        "new_this_week": new_this_week,
        "top_fans": [
            {
                "username": f.username,
                "total_spent": f.total_spent,
                "fan_score": f.fan_score,
                "status": f.status,
            }
            for f in sorted_fans
        ],
    }


# ─── Mailboxes (Per-Persona AI Mailboxes) ─────────────────────────

@router.get("/mailboxes")
async def list_mailboxes(db: AsyncSession = Depends(get_db)):
    """List all persona mailboxes with stats."""
    from app.models import Fan, ChatMessage

    personas_result = await db.execute(select(Persona).order_by(Persona.created_at.desc()))
    personas = personas_result.scalars().all()

    mailboxes = []
    for p in personas:
        # Fan count for this persona
        fans_q = select(Fan).where(Fan.persona_id == p.id)
        fans_result = await db.execute(fans_q)
        fans = fans_result.scalars().all()

        # Message count
        msgs_q = select(func.count()).where(ChatMessage.persona_id == p.id)
        msgs_count = (await db.execute(msgs_q)).scalar() or 0

        # Unread (inbound messages newer than last outbound)
        last_outbound_q = (
            select(func.max(ChatMessage.created_at))
            .where(ChatMessage.persona_id == p.id, ChatMessage.direction == "outbound")
        )
        last_outbound = (await db.execute(last_outbound_q)).scalar()

        if last_outbound:
            unread_q = (
                select(func.count()).where(
                    ChatMessage.persona_id == p.id,
                    ChatMessage.direction == "inbound",
                    ChatMessage.created_at > last_outbound,
                )
            )
            unread = (await db.execute(unread_q)).scalar() or 0
        else:
            # All inbound are unread if no outbound yet
            unread_q = (
                select(func.count()).where(
                    ChatMessage.persona_id == p.id,
                    ChatMessage.direction == "inbound",
                )
            )
            unread = (await db.execute(unread_q)).scalar() or 0

        # Revenue from this persona's fans
        revenue = sum(f.total_spent or 0 for f in fans)

        # Last message time
        last_msg_q = (
            select(func.max(ChatMessage.created_at))
            .where(ChatMessage.persona_id == p.id)
        )
        last_msg = (await db.execute(last_msg_q)).scalar()

        mailboxes.append({
            "persona_id": str(p.id),
            "persona_name": p.name,
            "avatar_url": p.avatar_url or "",
            "brand": p.brand or "",
            "status": p.status.value if hasattr(p.status, 'value') else str(p.status),
            "fan_count": len(fans),
            "message_count": msgs_count,
            "unread_count": unread,
            "revenue": round(revenue, 2),
            "last_message_at": last_msg.isoformat() if last_msg else None,
        })

    return mailboxes


@router.get("/mailboxes/{persona_id}")
async def get_mailbox(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get a specific persona's mailbox with fan threads."""
    from app.models import Fan, ChatMessage

    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    # Get all fans for this persona
    fans_q = select(Fan).where(Fan.persona_id == persona_id).order_by(Fan.total_spent.desc())
    fans_result = await db.execute(fans_q)
    fans = fans_result.scalars().all()

    # Build thread list: for each fan, get their latest message + unread count
    threads = []
    for fan in fans:
        # Latest message
        latest_q = (
            select(ChatMessage)
            .where(ChatMessage.fan_id == fan.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        latest_result = await db.execute(latest_q)
        latest = latest_result.scalar_one_or_none()

        # Unread inbound messages for this fan
        last_outbound_q = (
            select(func.max(ChatMessage.created_at))
            .where(ChatMessage.fan_id == fan.id, ChatMessage.direction == "outbound")
        )
        last_outbound = (await db.execute(last_outbound_q)).scalar()

        if last_outbound:
            unread_q = (
                select(func.count()).where(
                    ChatMessage.fan_id == fan.id,
                    ChatMessage.direction == "inbound",
                    ChatMessage.created_at > last_outbound,
                )
            )
        else:
            unread_q = (
                select(func.count()).where(
                    ChatMessage.fan_id == fan.id,
                    ChatMessage.direction == "inbound",
                )
            )
        unread = (await db.execute(unread_q)).scalar() or 0

        threads.append({
            "fan_id": str(fan.id),
            "username": fan.username,
            "display_name": fan.display_name,
            "platform": fan.platform,
            "subscription_tier": fan.subscription_tier,
            "total_spent": fan.total_spent or 0,
            "fan_score": fan.fan_score or 0,
            "tags": fan.tags or [],
            "last_message": {
                "content": latest.content if latest else "",
                "direction": latest.direction if latest else "",
                "is_ai_generated": latest.is_ai_generated if latest else False,
                "created_at": latest.created_at.isoformat() if latest and latest.created_at else None,
            } if latest else None,
            "unread_count": unread,
        })

    # Sort threads: unread first, then by last message time
    def _sort_key(t):
        unread = -t["unread_count"]
        lm = t.get("last_message")
        ts = lm.get("created_at", "") if lm else ""
        return (unread, ts or "")
    threads.sort(key=_sort_key)

    return {
        "persona_id": str(persona_id),
        "persona_name": persona.name,
        "avatar_url": persona.avatar_url or "",
        "brand": persona.brand or "",
        "total_fans": len(fans),
        "total_revenue": round(sum(f.total_spent or 0 for f in fans), 2),
        "threads": threads,
    }


@router.post("/mailboxes/{persona_id}/send")
async def send_as_persona(
    persona_id: UUID,
    fan_id: str = Query(...),
    content: str = Query(...),
    message_type: str = Query("text"),
    db: AsyncSession = Depends(get_db),
):
    """Send a message as the persona to a fan (operator override)."""
    from app.models import Fan, ChatMessage

    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    fan = await db.get(Fan, UUID(fan_id))
    if not fan or fan.persona_id != persona_id:
        raise HTTPException(404, "Fan not found in this persona's mailbox")

    msg = ChatMessage(
        fan_id=fan.id,
        persona_id=persona_id,
        direction="outbound",
        content=content,
        message_type=message_type,
        is_ai_generated=False,
    )
    db.add(msg)
    fan.messages_received = (fan.messages_received or 0) + 1
    fan.last_message_at = datetime.now(timezone.utc)
    await db.commit()

    return {"status": "sent", "message_id": str(msg.id)}


@router.get("/system/health")
async def system_health():
    """Detailed system health including provider status."""
    registry = get_registry()
    return {
        "providers": registry.health_report(),
        "environment": "development",
    }
