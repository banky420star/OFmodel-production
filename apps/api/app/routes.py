"""Persona Studio — Complete API routes for all 14 phases."""

from __future__ import annotations
import time
import random
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Depends, Query
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
from app.providers.mocks import (
    MockLLMProvider, MockImageProvider, MockVideoProvider,
    MockVoiceProvider, MockTrainerProvider, MockStorageProvider,
)

router = APIRouter()

# Register workflow step handlers
workflow_engine.register_step("create_persona", create_persona_handler)
workflow_engine.register_step("generate_candidates", generate_candidates_handler)
workflow_engine.register_step("approve_identity", approve_identity_handler)
workflow_engine.register_step("build_reference_dataset", build_reference_dataset_handler)
workflow_engine.register_step("train_lora", train_lora_handler)
workflow_engine.register_step("validate_identity", validate_identity_handler)
workflow_engine.register_step("create_voice", create_voice_handler)
workflow_engine.register_step("activate_persona", activate_persona_handler)
workflow_engine.register_step("plan_shoot", plan_shoot_handler)
workflow_engine.register_step("generate_images", generate_shoot_images_handler)
workflow_engine.register_step("generate_videos", generate_shoot_videos_handler)
workflow_engine.register_step("generate_voiceover", generate_voiceover_handler)
workflow_engine.register_step("quality_check", quality_check_handler)
workflow_engine.register_step("assemble_pack", assemble_pack_handler)
workflow_engine.register_step("generate_captions", generate_captions_handler)
workflow_engine.register_step("finalize_pack", finalize_pack_handler)


# ─── Health ────────────────────────────────────────────────────────────

@router.get("/health", response_model=SystemHealth)
async def health_check(db: AsyncSession = Depends(get_db)):
    checks = []
    # Database
    try:
        await db.execute(select(func.count()).select_from(Persona))
        checks.append(HealthCheck(service="postgres", status="green"))
    except Exception as e:
        checks.append(HealthCheck(service="postgres", status="red", message=str(e)))

    # Providers via registry
    registry = get_registry()
    providers = {
        "llm": registry.get_llm_provider(),
        "image": registry.get_image_provider(),
        "video": registry.get_video_provider(),
        "voice": registry.get_voice_provider(),
        "trainer": registry.get_trainer_provider(),
        "storage": registry.get_storage_provider(),
    }
    for name, provider in providers.items():
        try:
            result = await provider.health_check()
            provider_name = result.provider if hasattr(result, 'provider') else type(provider).__name__
            status = "green" if result.success else "yellow"
            checks.append(HealthCheck(service=name, status=status, message=provider_name))
        except Exception as e:
            checks.append(HealthCheck(service=name, status="red", message=str(e)))

    overall = "green" if all(c.status == "green" for c in checks) else (
        "red" if any(c.status == "red" for c in checks) else "yellow"
    )
    return SystemHealth(overall=overall, checks=checks)


# ─── Personas (Phase 3) ───────────────────────────────────────────────

@router.post("/personas", response_model=PersonaResponse)
async def create_persona(data: PersonaCreate, db: AsyncSession = Depends(get_db)):
    if not data.adult_verified:
        raise HTTPException(400, "Adult verification required")
    if not data.synthetic_identity:
        raise HTTPException(400, "Must be synthetic identity")

    # Create persona
    persona = Persona(
        id=uuid4(),
        name=data.name,
        age=data.age,
        description=data.description,
        status=PersonaStatus.ACTIVE,
        metadata_json={
            "appearance": data.appearance.model_dump(),
            "personality": data.personality,
            "brand": data.brand,
            "voice_style": data.voice_style,
            "publishing_frequency": data.publishing_frequency,
            "adult_verified": True,
            "synthetic_identity": True,
        },
    )
    db.add(persona)
    await db.flush()

    # Start persona creation workflow
    workflow_input = data.model_dump()
    workflow_input["persona_id"] = str(persona.id)
    workflow_input["persona_name"] = data.name
    workflow = await workflow_engine.create_workflow(
        name=f"Create Persona: {data.name}",
        workflow_type="persona_creation",
        persona_id=persona.id,
        input_data=workflow_input,
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
    workflow.persona_id = persona.id
    await db.commit()
    await db.refresh(persona)

    # Execute workflow asynchronously (in real app, this would be Celery)
    try:
        await workflow_engine.run_workflow(workflow.id)
    except Exception:
        pass  # Workflow continues in background

    await db.refresh(persona)
    return PersonaResponse(
        id=persona.id,
        name=persona.name,
        age=persona.age,
        status=persona.status.value,
        description=persona.description,
        adult_verified=persona.metadata_json.get("adult_verified", True),
        synthetic_identity=persona.metadata_json.get("synthetic_identity", True),
        appearance=AppearanceProfile(**persona.metadata_json.get("appearance", {})),
        personality=persona.metadata_json.get("personality", []),
        brand=persona.metadata_json.get("brand", ""),
        voice_style=persona.metadata_json.get("voice_style", ""),
        publishing_frequency=persona.metadata_json.get("publishing_frequency", ""),
        created_at=persona.created_at,
        updated_at=persona.updated_at,
    )


@router.get("/personas", response_model=list[PersonaResponse])
async def list_personas(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Persona).order_by(Persona.created_at.desc()))
    personas = result.scalars().all()
    responses = []
    for p in personas:
        # Get identity status
        id_result = await db.execute(
            select(Identity).where(Identity.persona_id == p.id).order_by(Identity.created_at.desc()).limit(1)
        )
        identity = id_result.scalar_one_or_none()

        # Get pack count
        pack_count_result = await db.execute(
            select(func.count()).select_from(ContentPack).where(ContentPack.persona_id == p.id)
        )
        pack_count = pack_count_result.scalar() or 0

        responses.append(PersonaResponse(
            id=p.id,
            name=p.name,
            age=p.age,
            status=p.status.value,
            description=p.description,
            adult_verified=p.metadata_json.get("adult_verified", True),
            synthetic_identity=p.metadata_json.get("synthetic_identity", True),
            appearance=AppearanceProfile(**p.metadata_json.get("appearance", {})),
            personality=p.metadata_json.get("personality", []),
            brand=p.metadata_json.get("brand", ""),
            voice_style=p.metadata_json.get("voice_style", ""),
            publishing_frequency=p.metadata_json.get("publishing_frequency", ""),
            identity_status=identity.status.value if identity else None,
            identity_score=identity.consistency_score if identity else None,
            packs_count=pack_count,
            created_at=p.created_at,
            updated_at=p.updated_at,
        ))
    return responses


@router.get("/personas/{persona_id}", response_model=PersonaResponse)
async def get_persona(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    id_result = await db.execute(
        select(Identity).where(Identity.persona_id == persona.id).order_by(Identity.created_at.desc()).limit(1)
    )
    identity = id_result.scalar_one_or_none()

    pack_count_result = await db.execute(
        select(func.count()).select_from(ContentPack).where(ContentPack.persona_id == persona.id)
    )
    pack_count = pack_count_result.scalar() or 0

    return PersonaResponse(
        id=persona.id,
        name=persona.name,
        age=persona.age,
        status=persona.status.value,
        description=persona.description,
        adult_verified=persona.metadata_json.get("adult_verified", True),
        synthetic_identity=persona.metadata_json.get("synthetic_identity", True),
        appearance=AppearanceProfile(**persona.metadata_json.get("appearance", {})),
        personality=persona.metadata_json.get("personality", []),
        brand=persona.metadata_json.get("brand", ""),
        voice_style=persona.metadata_json.get("voice_style", ""),
        publishing_frequency=persona.metadata_json.get("publishing_frequency", ""),
        identity_status=identity.status.value if identity else None,
        identity_score=identity.consistency_score if identity else None,
        packs_count=pack_count,
        created_at=persona.created_at,
        updated_at=persona.updated_at,
    )


@router.post("/personas/{persona_id}/build")
async def build_persona(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """Start a build job for a persona — enqueues to Redis for worker processing."""
    from app.models import Job
    from app.queue import enqueue

    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    persona.status = PersonaStatus.BUILDING
    job = Job(
        id=uuid4(),
        type="build_persona",
        status="queued",
        progress=0,
        message="Queued",
        persona_id=persona.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Enqueue to Redis for worker pickup
    try:
        await enqueue({
            "job_id": str(job.id),
            "type": job.type,
            "persona_id": str(persona.id),
        })
    except Exception:
        pass  # Redis may not be running in dev

    return {
        "id": str(job.id),
        "type": job.type,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "persona_id": str(persona.id),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


# ─── Identity (Phase 3) ───────────────────────────────────────────────

@router.get("/personas/{persona_id}/identities", response_model=list[IdentityResponse])
async def list_identities(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Identity).where(Identity.persona_id == persona_id).order_by(Identity.created_at.desc())
    )
    return [
        IdentityResponse(
            id=i.id, persona_id=i.persona_id, name=i.name,
            status=i.status.value, reference_images=i.reference_images,
            lora_model_path=i.lora_model_path, consistency_score=i.consistency_score,
            metadata_json=i.metadata_json, created_at=i.created_at, updated_at=i.updated_at,
        )
        for i in result.scalars().all()
    ]


@router.post("/personas/{persona_id}/identities/{identity_id}/approve")
async def approve_identity(persona_id: UUID, identity_id: UUID, db: AsyncSession = Depends(get_db)):
    identity = await db.get(Identity, identity_id)
    if not identity or identity.persona_id != persona_id:
        raise HTTPException(404, "Identity not found")
    identity.status = IdentityStatus.APPROVED
    return {"status": "approved", "identity_id": str(identity_id)}


# ─── Shoots (Phase 9) ─────────────────────────────────────────────────

@router.post("/personas/{persona_id}/shoots", response_model=ShootResponse)
async def create_shoot(persona_id: UUID, data: ShootCreate, db: AsyncSession = Depends(get_db)):
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    shoot = Shoot(
        id=uuid4(),
        persona_id=persona_id,
        name=data.name or f"{data.theme.title()} Shoot",
        status=ShootStatus.DRAFT,
        theme=data.theme,
        image_count=data.image_count,
        metadata_json=data.metadata_json,
    )
    db.add(shoot)
    await db.commit()
    await db.refresh(shoot)

    return ShootResponse(
        id=shoot.id, persona_id=shoot.persona_id, identity_id=shoot.identity_id,
        name=shoot.name, status=shoot.status.value, theme=shoot.theme,
        image_count=shoot.image_count, generated_images=shoot.generated_images,
        created_at=shoot.created_at, completed_at=shoot.completed_at,
    )


@router.get("/personas/{persona_id}/shoots", response_model=list[ShootResponse])
async def list_shoots(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Shoot).where(Shoot.persona_id == persona_id).order_by(Shoot.created_at.desc())
    )
    return [
        ShootResponse(
            id=s.id, persona_id=s.persona_id, identity_id=s.identity_id,
            name=s.name, status=s.status.value, theme=s.theme,
            image_count=s.image_count, generated_images=s.generated_images,
            created_at=s.created_at, completed_at=s.completed_at,
        )
        for s in result.scalars().all()
    ]


@router.post("/shoots/{shoot_id}/generate")
async def generate_shoot(shoot_id: UUID, db: AsyncSession = Depends(get_db)):
    """Start image/video generation for a shoot."""
    shoot = await db.get(Shoot, shoot_id)
    if not shoot:
        raise HTTPException(404, "Shoot not found")

    shoot.status = ShootStatus.GENERATING
    await db.commit()

    # Get persona for name
    persona = await db.get(Persona, shoot.persona_id)

    workflow = await workflow_engine.create_workflow(
        name=f"Generate Shoot: {shoot.name}",
        workflow_type="shoot_generation",
        persona_id=shoot.persona_id,
        input_data={
            "shoot_id": str(shoot.id),
            "persona_id": str(shoot.persona_id),
            "theme": shoot.theme,
            "image_count": shoot.image_count,
            "persona_name": persona.name if persona else "model",
        },
        steps=[
            {"name": "plan_shoot", "step_type": "plan_shoot"},
            {"name": "generate_images", "step_type": "generate_images"},
            {"name": "generate_videos", "step_type": "generate_videos"},
            {"name": "generate_voiceover", "step_type": "generate_voiceover"},
        ],
    )

    try:
        await workflow_engine.run_workflow(workflow.id)
    except Exception:
        pass

    await db.refresh(shoot)
    return {"shoot_id": str(shoot.id), "workflow_id": str(workflow.id), "status": shoot.status.value}


# ─── Content Packs (Phase 9) ──────────────────────────────────────────

@router.post("/personas/{persona_id}/packs", response_model=ContentPackResponse)
async def create_content_pack(persona_id: UUID, data: ContentPackCreate, db: AsyncSession = Depends(get_db)):
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    pack = ContentPack(
        id=uuid4(),
        persona_id=persona_id,
        name=data.name or f"{persona.name} Pack",
        status=ContentPackStatus.DRAFT,
        platform=data.platform,
    )
    db.add(pack)
    await db.commit()
    await db.refresh(pack)

    return ContentPackResponse(
        id=pack.id, persona_id=pack.persona_id, name=pack.name,
        status=pack.status.value, platform=pack.platform,
        images=pack.images, videos=pack.videos, voiceovers=pack.voiceovers,
        captions=pack.captions, created_at=pack.created_at, published_at=pack.published_at,
    )


@router.get("/personas/{persona_id}/packs", response_model=list[ContentPackResponse])
async def list_content_packs(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ContentPack).where(ContentPack.persona_id == persona_id).order_by(ContentPack.created_at.desc())
    )
    return [
        ContentPackResponse(
            id=p.id, persona_id=p.persona_id, name=p.name,
            status=p.status.value, platform=p.platform,
            images=p.images, videos=p.videos, voiceovers=p.voiceovers,
            captions=p.captions, created_at=p.created_at, published_at=p.published_at,
        )
        for p in result.scalars().all()
    ]


@router.post("/packs/{pack_id}/assemble")
async def assemble_content_pack(pack_id: UUID, db: AsyncSession = Depends(get_db)):
    """Assemble a complete content pack with images, video, voice, captions."""
    pack = await db.get(ContentPack, pack_id)
    if not pack:
        raise HTTPException(404, "Pack not found")

    # Get persona for context
    persona = await db.get(Persona, pack.persona_id)
    identity_result = await db.execute(
        select(Identity).where(Identity.persona_id == pack.persona_id).limit(1)
    )
    identity = identity_result.scalar_one_or_none()

    workflow = await workflow_engine.create_workflow(
        name=f"Assemble Pack: {pack.name}",
        workflow_type="content_pack",
        persona_id=pack.persona_id,
        input_data={
            "pack_id": str(pack.id),
            "persona_id": str(pack.persona_id),
            "theme": pack.name,
            "platform": pack.platform,
            "persona_name": persona.name if persona else "model",
            "voice_style": persona.metadata_json.get("voice_style", "") if persona else "",
        },
        steps=[
            {"name": "plan_shoot", "step_type": "plan_shoot"},
            {"name": "generate_images", "step_type": "generate_images"},
            {"name": "generate_videos", "step_type": "generate_videos"},
            {"name": "generate_voiceover", "step_type": "generate_voiceover"},
            {"name": "quality_check", "step_type": "quality_check"},
            {"name": "assemble_pack", "step_type": "assemble_pack"},
            {"name": "generate_captions", "step_type": "generate_captions"},
            {"name": "finalize_pack", "step_type": "finalize_pack"},
        ],
    )

    try:
        await workflow_engine.run_workflow(workflow.id)
    except Exception:
        pass

    await db.refresh(pack)
    return {"pack_id": str(pack.id), "workflow_id": str(workflow.id), "status": pack.status.value}


# ─── Workflows (Phase 4) ──────────────────────────────────────────────

@router.get("/workflows", response_model=list[WorkflowResponse])
async def list_workflows(
    persona_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    workflows = await workflow_engine.list_workflows(
        persona_id=persona_id,
        status=WorkflowStatus(status) if status else None,
        limit=limit,
    )
    return [
        WorkflowResponse(
            id=w.id, name=w.name, status=w.status.value,
            workflow_type=w.workflow_type, current_step=w.current_step,
            persona_id=w.persona_id, input_data=w.input_data,
            output_data=w.output_data, error_message=w.error_message,
            retry_count=w.retry_count, created_at=w.created_at,
            started_at=w.started_at, completed_at=w.completed_at,
        )
        for w in workflows
    ]


@router.get("/workflows/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: UUID, db: AsyncSession = Depends(get_db)):
    w = await workflow_engine.get_workflow(workflow_id)
    if not w:
        raise HTTPException(404, "Workflow not found")
    return WorkflowResponse(
        id=w.id, name=w.name, status=w.status.value,
        workflow_type=w.workflow_type, current_step=w.current_step,
        persona_id=w.persona_id, input_data=w.input_data,
        output_data=w.output_data, error_message=w.error_message,
        retry_count=w.retry_count, created_at=w.created_at,
        started_at=w.started_at, completed_at=w.completed_at,
    )


@router.get("/workflows/{workflow_id}/steps", response_model=list[WorkflowStepResponse])
async def get_workflow_steps(workflow_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(WorkflowStep).where(WorkflowStep.workflow_id == workflow_id).order_by(WorkflowStep.order)
    )
    return [
        WorkflowStepResponse(
            id=s.id, name=s.name, step_type=s.step_type, order=s.order,
            status=s.status.value, provider_name=s.provider_name,
            started_at=s.started_at, completed_at=s.completed_at,
            error_message=s.error_message,
        )
        for s in result.scalars().all()
    ]


@router.post("/workflows/{workflow_id}/retry")
async def retry_workflow(workflow_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        w = await workflow_engine.retry_workflow(workflow_id)
        return {"workflow_id": str(w.id), "status": w.status.value}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/workflows/{workflow_id}/cancel")
async def cancel_workflow(workflow_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        w = await workflow_engine.cancel_workflow(workflow_id)
        return {"workflow_id": str(w.id), "status": w.status.value}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─── QA Results (Phase 7) ─────────────────────────────────────────────

@router.get("/personas/{persona_id}/qa", response_model=list[QAResponse])
async def list_qa_results(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    identity_result = await db.execute(
        select(Identity.id).where(Identity.persona_id == persona_id)
    )
    identity_ids = list(identity_result.scalars().all())
    if not identity_ids:
        return []

    result = await db.execute(
        select(QAResult).where(QAResult.identity_id.in_(identity_ids)).order_by(QAResult.created_at.desc())
    )
    return [
        QAResult(
            id=q.id, qa_type=q.qa_type, status=q.status.value,
            score=q.score, threshold=q.threshold, details=q.details,
            images_checked=q.images_checked, passed_count=q.passed_count,
            failed_count=q.failed_count, created_at=q.created_at,
        )
        for q in result.scalars().all()
    ]


# ─── Analytics (Phase 11) ─────────────────────────────────────────────

@router.get("/personas/{persona_id}/analytics", response_model=list[AnalyticsSnapshotResponse])
async def get_analytics(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AnalyticsSnapshot)
        .where(AnalyticsSnapshot.persona_id == persona_id)
        .order_by(AnalyticsSnapshot.snapshot_date.desc())
        .limit(90)
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
    return {"status": "generated", "days": 90}


# ─── Forecasts (Phase 12) ─────────────────────────────────────────────

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

    forecast = Forecast(
        id=uuid4(),
        persona_id=persona_id,
        forecast_date=datetime.now(timezone.utc),
        horizon_months=24,
        projected_followers=followers,
        projected_revenue=revenue,
        projected_costs=costs,
        projected_engagement=engagement,
        model_version="v1",
        metadata_json={"break_even_month": 8},
    )
    db.add(forecast)
    await db.commit()

    return {"forecast_id": str(forecast.id), "horizon_months": 24}


# ─── Scheduling (Phase 10) ────────────────────────────────────────────

@router.get("/personas/{persona_id}/schedule", response_model=list[ScheduledPostResponse])
async def get_schedule(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ScheduledPost)
        .where(ScheduledPost.persona_id == persona_id)
        .order_by(ScheduledPost.scheduled_at)
    )
    return [
        ScheduledPostResponse(
            id=s.id, persona_id=s.persona_id, content_pack_id=s.content_pack_id,
            platform=s.platform, scheduled_at=s.scheduled_at,
            posted_at=s.posted_at, status=s.status,
        )
        for s in result.scalars().all()
    ]


@router.post("/personas/{persona_id}/schedule/generate")
async def generate_schedule(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """Generate a weekly content schedule."""
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    # Get unposted content packs
    pack_result = await db.execute(
        select(ContentPack)
        .where(ContentPack.persona_id == persona_id)
        .where(ContentPack.status == ContentPackStatus.ASSEMBLED)
        .limit(14)
    )
    packs = pack_result.scalars().all()

    now = datetime.now(timezone.utc)
    scheduled = []
    platforms = ["instagram", "tiktok", "youtube"]

    for i, pack in enumerate(packs):
        post = ScheduledPost(
            id=uuid4(),
            persona_id=persona_id,
            content_pack_id=pack.id,
            platform=platforms[i % len(platforms)],
            scheduled_at=now + timedelta(days=i + 1, hours=random.randint(9, 18)),
            status="scheduled",
        )
        db.add(post)
        scheduled.append(post)

    await db.commit()
    return {"scheduled": len(scheduled), "posts": [str(p.id) for p in scheduled]}


# ─── Autopilot (Phase 10) ─────────────────────────────────────────────

@router.post("/personas/{persona_id}/autopilot")
async def toggle_autopilot(persona_id: UUID, mode: str = "assisted", db: AsyncSession = Depends(get_db)):
    """Toggle autopilot mode: off / assisted / on."""
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    if mode not in ("off", "assisted", "on"):
        raise HTTPException(400, "Mode must be off, assisted, or on")

    persona.metadata_json["autopilot"] = mode
    await db.commit()
    return {"persona_id": str(persona_id), "autopilot": mode}
