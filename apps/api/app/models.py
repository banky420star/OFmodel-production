"""Persona Studio — SQLAlchemy models for all 14 phases."""

from __future__ import annotations

import uuid
import secrets
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Text, Boolean, Integer, Float, DateTime,
    ForeignKey, JSON, Enum as SAEnum, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy import select
import enum

def build_identity_prompt(
    persona: Persona, identity: Identity | None = None,
) -> str:
    """Build the canonical identity-prompt text for a persona.

    This is what gets stored in the identity lock and reused across every
    identity-locked generation so the face stays consistent.
    """
    appearance = persona.appearance or {}
    hair = appearance.get("hair", "long blonde")
    hair_length = appearance.get("hair_length", "long")
    hair_colour = appearance.get("hair_colour", "blonde")
    eyes = appearance.get("eye_colour", "blue")
    facial = appearance.get("facial_characteristics", "")
    skin = appearance.get("skin_characteristics", "")
    body = appearance.get("body_characteristics", "")
    height = appearance.get("height_profile", "5'7\"")
    features = appearance.get("identifying_synthetic_features", "distinctive smile, high cheekbones")

    bits = [
        f"portrait of a {persona.age}-year-old woman",
        f"with {hair_length} {hair_colour} hair",
        f"{eyes} eyes",
    ]
    if facial:
        bits.append(facial)
    if skin:
        bits.append(skin)
    if body:
        bits.append(body)
    if height:
        bits.append(f"{height} height")
    if features:
        bits.append(features)
    bits.append(f"confident and authentic expression")
    bits.append(f"{persona.brand or 'lifestyle'} aesthetic")
    bits.append("natural lighting, photorealistic")
    return ", ".join(bits)


def build_identity_negative_prompt() -> str:
    return (
        "deformed, blurry, low quality, cartoon, anime, drawing, ugly, "
        "extra limbs, malformed, bad anatomy, watermark, text"
    )


def build_identity_style_tags(persona: Persona) -> list[str]:
    brand = (persona.brand or "lifestyle").lower().replace(" ", "_")
    return ["luxury", brand, "photorealistic"][:3]


from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


# ─── Enums ────────────────────────────────────────────────────────────

class PersonaStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"
    ARCHIVED = "archived"


class IdentityLockStatus(str, enum.Enum):
    AWAITING_IDENTITY_APPROVAL = "awaiting_identity_approval"
    ACTIVE = "active"
    FAILED = "failed"


class IdentityStatus(str, enum.Enum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    TRAINING = "training"
    READY = "ready"
    FAILED = "failed"


def identity_lock_status_from_identity_status(status: IdentityStatus) -> IdentityLockStatus:
    """Map the canonical identity state to the identity-lock lifecycle.

    A persona only has a usable identity lock after its canonical identity
    is approved and the build QA has passed.
    """
    if status == IdentityStatus.READY:
        return IdentityLockStatus.ACTIVE
    if status in (IdentityStatus.CANDIDATE, IdentityStatus.APPROVED, IdentityStatus.TRAINING):
        return IdentityLockStatus.AWAITING_IDENTITY_APPROVAL
    return IdentityLockStatus.FAILED


class WorkflowStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class WorkflowStepStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ShootStatus(str, enum.Enum):
    DRAFT = "draft"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class ContentPackStatus(str, enum.Enum):
    DRAFT = "draft"
    ASSEMBLED = "assembled"
    PUBLISHED = "published"
    FAILED = "failed"


class QAStatus(str, enum.Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    REVIEW = "review"


# ─── Persona (Phase 3) ────────────────────────────────────────────────

class Persona(Base):
    __tablename__ = "personas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(128), nullable=False, unique=True)
    age = Column(Integer, default=24)
    description = Column(Text, default="")
    # Defaults to False. It used to default to True, which meant every persona
    # was recorded as adult-verified the moment it was created — the record
    # claimed a verification that the adult gate (ADULT_CONTENT_ENABLED, and an
    # adult-capable provider) then refused. A field that gates access must not
    # be true until someone says so.
    adult_verified = Column(Boolean, default=False, nullable=False)
    synthetic_identity = Column(Boolean, default=True)
    status = Column(SAEnum(PersonaStatus), default=PersonaStatus.DRAFT)
    avatar_url = Column(String(512), default="")
    appearance = Column(JSON, default=dict)
    personality = Column(JSON, default=list)
    voice_style = Column(String(128), default="")
    brand = Column(String(128), default="")
    publishing_frequency = Column(String(64), default="")
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    identities = relationship("Identity", back_populates="persona", cascade="all, delete-orphan")
    shoots = relationship("Shoot", back_populates="persona", cascade="all, delete-orphan")
    content_packs = relationship("ContentPack", back_populates="persona", cascade="all, delete-orphan")
    fans = relationship("Fan", back_populates="persona", cascade="all, delete-orphan")


# ─── Identity (Phase 3) ───────────────────────────────────────────────

class Identity(Base):
    __tablename__ = "identities"
    __table_args__ = (
        Index("ix_identity_persona_status", "persona_id", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    persona_id = Column(UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(128), nullable=False)
    status = Column(SAEnum(IdentityStatus), default=IdentityStatus.CANDIDATE)
    reference_images = Column(JSON, default=list)  # list of S3/MinIO keys
    lora_model_path = Column(String(512), default="")
    embedding_vector = Column(JSON, default=list)  # face embedding
    consistency_score = Column(Float, default=0.0)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    persona = relationship("Persona", back_populates="identities")


# ─── Reference Dataset (Phase 3) ──────────────────────────────────────

class ReferenceDataset(Base):
    __tablename__ = "reference_datasets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    identity_id = Column(UUID(as_uuid=True), ForeignKey("identities.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(256), default="default")
    image_keys = Column(JSON, default=list)  # list of object storage keys
    total_images = Column(Integer, default=0)
    quality_score = Column(Float, default=0.0)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)


# ─── Workflow Engine (Phase 4) ────────────────────────────────────────

class Workflow(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        Index("ix_workflow_status", "status"),
        Index("ix_workflow_persona", "persona_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(256), nullable=False)
    persona_id = Column(UUID(as_uuid=True), ForeignKey("personas.id", ondelete="SET NULL"))
    status = Column(SAEnum(WorkflowStatus), default=WorkflowStatus.PENDING)
    workflow_type = Column(String(128), nullable=False)  # shoot, content_pack, training, etc.
    current_step = Column(String(128), default="")
    input_data = Column(JSON, default=dict)
    output_data = Column(JSON, default=dict)
    error_message = Column(Text, default="")
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    steps = relationship("WorkflowStep", back_populates="workflow", cascade="all, delete-orphan",
                         order_by="WorkflowStep.order")


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        Index("ix_workflow_step_status", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(128), nullable=False)
    step_type = Column(String(128), nullable=False)  # provider_call, transform, qa_check, etc.
    order = Column(Integer, nullable=False)
    status = Column(SAEnum(WorkflowStepStatus), default=WorkflowStepStatus.PENDING)
    input_data = Column(JSON, default=dict)
    output_data = Column(JSON, default=dict)
    error_message = Column(Text, default="")
    provider_name = Column(String(128), default="")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    workflow = relationship("Workflow", back_populates="steps")


# ─── Image Generation (Phase 5) ───────────────────────────────────────

class GeneratedImage(Base):
    __tablename__ = "generated_images"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="SET NULL"))
    identity_id = Column(UUID(as_uuid=True), ForeignKey("identities.id", ondelete="SET NULL"))
    prompt = Column(Text, default="")
    negative_prompt = Column(Text, default="")
    image_key = Column(String(512), default="")  # MinIO key
    thumbnail_key = Column(String(512), default="")
    seed = Column(Integer, default=0)
    width = Column(Integer, default=1024)
    height = Column(Integer, default=1024)
    steps = Column(Integer, default=30)
    cfg_scale = Column(Float, default=7.0)
    sampler = Column(String(64), default="euler_a")
    generation_time_ms = Column(Integer, default=0)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)


# ─── LoRA Training (Phase 6) ─────────────────────────────────────────

class TrainingJob(Base):
    __tablename__ = "training_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    identity_id = Column(UUID(as_uuid=True), ForeignKey("identities.id", ondelete="CASCADE"), nullable=False)
    dataset_id = Column(UUID(as_uuid=True), ForeignKey("reference_datasets.id", ondelete="SET NULL"))
    status = Column(SAEnum(WorkflowStatus), default=WorkflowStatus.PENDING)
    model_type = Column(String(64), default="lora")
    rank = Column(Integer, default=16)
    epochs = Column(Integer, default=10)
    learning_rate = Column(Float, default=1e-4)
    batch_size = Column(Integer, default=4)
    output_path = Column(String(512), default="")
    metrics = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


# ─── Video (Phase 8) ──────────────────────────────────────────────────

class GeneratedVideo(Base):
    __tablename__ = "generated_videos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="SET NULL"))
    identity_id = Column(UUID(as_uuid=True), ForeignKey("identities.id", ondelete="SET NULL"))
    prompt = Column(Text, default="")
    video_key = Column(String(512), default="")
    thumbnail_key = Column(String(512), default="")
    duration_seconds = Column(Float, default=0.0)
    width = Column(Integer, default=1024)
    height = Column(Integer, default=576)
    fps = Column(Integer, default=24)
    generation_time_ms = Column(Integer, default=0)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)


# ─── Voice (Phase 8) ──────────────────────────────────────────────────

class GeneratedVoice(Base):
    __tablename__ = "generated_voices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="SET NULL"))
    identity_id = Column(UUID(as_uuid=True), ForeignKey("identities.id", ondelete="SET NULL"))
    text = Column(Text, default="")
    voice_key = Column(String(512), default="")
    voice_id = Column(String(128), default="")
    duration_seconds = Column(Float, default=0.0)
    sample_rate = Column(Integer, default=22050)
    format = Column(String(16), default="wav")
    generation_time_ms = Column(Integer, default=0)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)


# ─── Shoot (Phase 9) ──────────────────────────────────────────────────

class Shoot(Base):
    __tablename__ = "shoots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    persona_id = Column(UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    identity_id = Column(UUID(as_uuid=True), ForeignKey("identities.id", ondelete="SET NULL"))
    name = Column(String(256), default="")
    status = Column(SAEnum(ShootStatus), default=ShootStatus.DRAFT)
    theme = Column(String(256), default="")
    prompt_template = Column(Text, default="")
    image_count = Column(Integer, default=10)
    generated_images = Column(JSON, default=list)  # list of GeneratedImage IDs
    progress = Column(Float, default=0.0)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    persona = relationship("Persona", back_populates="shoots")


# ─── Content Pack (Phase 9) ───────────────────────────────────────────

class ContentPack(Base):
    __tablename__ = "content_packs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    persona_id = Column(UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(256), default="")
    status = Column(SAEnum(ContentPackStatus), default=ContentPackStatus.DRAFT)
    platform = Column(String(64), default="all")  # instagram, tiktok, youtube, all
    images = Column(JSON, default=list)  # list of image keys
    videos = Column(JSON, default=list)  # list of video keys
    voiceovers = Column(JSON, default=list)  # list of voice keys
    captions = Column(JSON, default=list)  # text captions
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    published_at = Column(DateTime(timezone=True), nullable=True)

    persona = relationship("Persona", back_populates="content_packs")


# ─── QA Results (Phase 7) ─────────────────────────────────────────────

class QAResult(Base):
    __tablename__ = "qa_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    identity_id = Column(UUID(as_uuid=True), ForeignKey("identities.id", ondelete="CASCADE"))
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="SET NULL"))
    qa_type = Column(String(64), nullable=False)  # consistency, quality, diversity
    status = Column(SAEnum(QAStatus), default=QAStatus.PENDING)
    score = Column(Float, default=0.0)
    threshold = Column(Float, default=0.8)
    details = Column(JSON, default=dict)
    images_checked = Column(Integer, default=0)
    passed_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=utcnow)


# ─── Schedule (Phase 10) ──────────────────────────────────────────────

class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"
    __table_args__ = {"extend_existing": True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    persona_id = Column(String(36), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    content_pack_id = Column(String(36), ForeignKey("content_packs.id", ondelete="SET NULL"))
    platform = Column(String(64), nullable=False)  # onlyfans, instagram, tiktok, fanvue, fansly
    content_type = Column(String(32), default="image")  # image, video, text, ppv, bundle
    title = Column(String(256), default="")
    caption = Column(Text, default="")
    media_keys = Column(JSON, default=list)  # list of file keys
    ppv_price = Column(Float, nullable=True)  # null = free post
    tags = Column(JSON, default=list)
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    posted_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(32), default="scheduled")  # draft, scheduled, posted, failed
    post_url = Column(String(512), default="")
    engagement_data = Column(JSON, default=dict)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ─── Analytics (Phase 11) ─────────────────────────────────────────────

class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"
    __table_args__ = (
        Index("ix_analytics_persona_date", "persona_id", "snapshot_date"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    persona_id = Column(UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    snapshot_date = Column(DateTime(timezone=True), nullable=False)
    platform = Column(String(64), default="all")
    followers = Column(Integer, default=0)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    views = Column(Integer, default=0)
    engagement_rate = Column(Float, default=0.0)
    revenue = Column(Float, default=0.0)
    costs = Column(Float, default=0.0)
    metadata_json = Column(JSON, default=dict)


# ─── Forecast (Phase 12) ──────────────────────────────────────────────

class Forecast(Base):
    __tablename__ = "forecasts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    persona_id = Column(UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    forecast_date = Column(DateTime(timezone=True), nullable=False)
    horizon_months = Column(Integer, default=24)
    projected_followers = Column(JSON, default=list)  # monthly projections
    projected_revenue = Column(JSON, default=list)
    projected_costs = Column(JSON, default=list)
    projected_engagement = Column(JSON, default=list)
    model_version = Column(String(64), default="v1")
    confidence_intervals = Column(JSON, default=dict)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)


def persona_storage_hex(persona_id) -> str:
    """Dashless 32-char UUID hex — the canonical identity_locks key.

    The seven legacy rows in the live database are keyed by the full UUID hex,
    so both the ORM and the sync image engine must address personas by this
    exact form. Shoot/dataset storage directories use the 8-char prefix; that
    is a storage-layout concern and never a database key.

    Note: models.py imports SQLAlchemy's UUID type, so the stdlib class is
    referenced as uuid.UUID here.
    """
    return uuid.UUID(str(persona_id)).hex


class Job(Base):
    """Background job with progress tracking for frontend polling."""
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type = Column(String(64), nullable=False)  # build_persona, generate_shoot, etc.
    status = Column(String(32), default="queued")  # queued, running, completed, failed
    progress = Column(Integer, default=0)  # 0-100
    message = Column(Text, nullable=True)
    persona_id = Column(UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"), nullable=True)
    shoot_id = Column(UUID(as_uuid=True), ForeignKey("shoots.id", ondelete="SET NULL"), nullable=True)
    pack_id = Column(UUID(as_uuid=True), ForeignKey("content_packs.id", ondelete="SET NULL"), nullable=True)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ─── Identity Lock (Consistent Identity) ─────────────────────────────


class IdentityLock(Base):
    """Canonical identity lock for a persona.

    This is the durable artifact that every image-generation path depends on.
    The table pre-exists in local dev SQLite as a TEXT-keyed table keyed by the
    persona's storage hex. This model maps onto that table so the async API can
    read/write it through the same database the rest of the system uses.

    SQL representation (local dev):
        CREATE TABLE identity_locks (
            persona_id TEXT PRIMARY KEY,   -- storage hex, NOT the full UUID hex
            seed INTEGER NOT NULL,
            identity_prompt TEXT NOT NULL,
            negative_prompt TEXT NOT NULL DEFAULT '',
            style_tags TEXT NOT NULL DEFAULT '[]',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """
    __tablename__ = "identity_locks"

    persona_id = Column(String(32), primary_key=True)
    seed = Column(Integer, nullable=False)
    identity_prompt = Column(Text, nullable=False)
    negative_prompt = Column(Text, nullable=False, default="")
    style_tags = Column(JSON, nullable=False, default=list)
    # Plain string, not SAEnum: the live table predates this column and SQLite
    # stores it as TEXT; an Enum column would fail against the reconciled table.
    status = Column(String(64), nullable=False, default=IdentityLockStatus.AWAITING_IDENTITY_APPROVAL.value)
    identity_id = Column(String(32), nullable=True)  # dashless UUID hex of the approved Identity
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


def _deterministic_lock_seed(persona_id: UUID, identity_id: UUID | None = None) -> int:
    """Deterministic seed derived from the persona (and optionally the chosen identity).

    Uses md5 rather than hash() because Python salts str hashing per process —
    hash() would give a different seed after every API restart.
    """
    import hashlib

    h = hashlib.md5(f"identity_lock:{persona_id}:{identity_id}".encode()).hexdigest()
    return int(h[:8], 16) % (2**31)


async def ensure_identity_lock(
    persona: Persona,
    db: AsyncSession,
    identity_id: UUID | str | None = None,
) -> IdentityLock:
    """Ensure this persona has a durable identity lock, creating it if needed.

    The lock is keyed by the persona's full UUID hex so the async ORM path and
    the sync image engine address the same row.

    Lifecycle: a newly created lock is AWAITING_IDENTITY_APPROVAL and only
    becomes ACTIVE once the persona's canonical identity reaches READY. The
    seed of an existing lock is never regenerated — it is the identity anchor
    for every image already produced for that persona.
    """
    storage_hex = persona_storage_hex(persona.id)

    identity: Identity | None = None
    if identity_id:
        identity = await db.get(Identity, uuid.UUID(str(identity_id)))

    result = await db.execute(
        select(IdentityLock).where(IdentityLock.persona_id == storage_hex)
    )
    lock = result.scalar_one_or_none()

    wanted_status = (
        IdentityLockStatus.ACTIVE
        if identity and identity.status == IdentityStatus.READY
        else IdentityLockStatus.AWAITING_IDENTITY_APPROVAL
    )

    if lock is None:
        lock = IdentityLock(
            persona_id=storage_hex,
            seed=_deterministic_lock_seed(persona.id),
            identity_prompt=build_identity_prompt(persona),
            negative_prompt=build_identity_negative_prompt(),
            style_tags=build_identity_style_tags(persona),
            status=wanted_status.value,
            identity_id=identity.id.hex if identity else None,
        )
        db.add(lock)
        await db.flush()
    elif identity:
        # Explicit identity: sync the lock to that identity's lifecycle.
        lock.status = wanted_status.value
        lock.identity_id = identity.id.hex
        lock.identity_prompt = build_identity_prompt(persona)
    elif lock.status == IdentityLockStatus.AWAITING_IDENTITY_APPROVAL.value:
        # No explicit identity — activate the lock only if the persona's
        # canonical identity has reached READY. Reads never downgrade.
        ready = await db.scalar(
            select(Identity).where(
                Identity.persona_id == persona.id,
                Identity.status == IdentityStatus.READY,
            )
        )
        if ready:
            lock.status = IdentityLockStatus.ACTIVE.value
            lock.identity_id = ready.id.hex
            lock.identity_prompt = build_identity_prompt(persona)

    return lock


async def reconcile_identity_locks(engine) -> None:
    """Bring a legacy identity_locks table in line with the current ORM model.

    SQLite's create_all only creates missing tables, never missing columns, so
    a table created before status/identity_id/updated_at existed makes every
    new lock INSERT fail. This runs at startup after create_all, is idempotent,
    and keeps legacy rows readable.

    Legacy policy: a pre-existing lock row was the identity-approval artifact
    of the old build path (real seed + canonical prompt), so it is backfilled
    as active and keeps its original seed. New personas go through the strict
    AWAITING_IDENTITY_APPROVAL → ACTIVE lifecycle instead.
    """
    from sqlalchemy import text

    async with engine.begin() as conn:
        exists = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='identity_locks'")
        )
        if not exists.scalar():
            return

        cols = {
            row[1]
            for row in (await conn.execute(text("PRAGMA table_info(identity_locks)"))).fetchall()
        }

        if "status" not in cols:
            await conn.execute(text(
                "ALTER TABLE identity_locks ADD COLUMN status VARCHAR(64) "
                "NOT NULL DEFAULT 'awaiting_identity_approval'"
            ))
        if "identity_id" not in cols:
            await conn.execute(text("ALTER TABLE identity_locks ADD COLUMN identity_id CHAR(32)"))
        if "updated_at" not in cols:
            # SQLite rejects non-constant DEFAULT in ALTER TABLE; backfill after.
            await conn.execute(text("ALTER TABLE identity_locks ADD COLUMN updated_at TIMESTAMP"))
            await conn.execute(text("UPDATE identity_locks SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL"))

        if "status" not in cols:
            # Legacy rows were created by the old build path with an approved
            # identity — the lock itself was the approval artifact.
            await conn.execute(text("UPDATE identity_locks SET status = 'active'"))
        if "identity_id" not in cols:
            await conn.execute(text(
                "UPDATE identity_locks SET identity_id = ("
                "  SELECT i.id FROM identities i"
                "  WHERE i.persona_id = identity_locks.persona_id"
                "    AND i.status IN ('approved', 'ready')"
                "  ORDER BY i.created_at DESC LIMIT 1"
                ") WHERE identity_id IS NULL"
            ))


def persona_ready_for_production(persona: Persona) -> tuple[bool, str]:
    """Return (ok, reason) for whether this persona can produce content.

    Production is blocked until the persona has an active identity lock.
    """
    if not persona.adult_verified:
        return False, "Persona is not adult-verified"
    if not persona.synthetic_identity:
        return False, "Persona is not a synthetic identity"
    return True, ""


# ─── Fan Chat (Revenue Layer) ─────────────────────────────────────────

class Fan(Base):
    """A fan/subscriber on the creator's platform."""
    __tablename__ = "fans"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    persona_id = Column(String(36), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    username = Column(String(128), nullable=False)
    display_name = Column(String(256), default="")
    platform = Column(String(32), default="onlyfans")  # onlyfans, fanvue, fansly, custom
    status = Column(String(32), default="active")  # active, inactive, banned, vip
    subscription_tier = Column(String(64), default="free")  # free, standard, premium, vip
    total_spent = Column(Float, default=0.0)
    ppv_purchases = Column(Integer, default=0)
    tips_given = Column(Float, default=0.0)
    messages_sent = Column(Integer, default=0)
    messages_received = Column(Integer, default=0)
    last_active = Column(DateTime(timezone=True), nullable=True)
    last_message_at = Column(DateTime(timezone=True), nullable=True)
    fan_score = Column(Float, default=0.0)  # 0-100, predicted spend potential
    tags = Column(JSON, default=list)  # ["whale", "new", "at_risk", "engaged"]
    notes = Column(Text, default="")  # operator notes
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    persona = relationship("Persona", back_populates="fans")
    messages = relationship("ChatMessage", back_populates="fan", cascade="all, delete-orphan")


class ChatMessage(Base):
    """A message in the fan chat system."""
    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    fan_id = Column(String(36), ForeignKey("fans.id", ondelete="CASCADE"), nullable=False)
    persona_id = Column(String(36), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    direction = Column(String(16), nullable=False)  # inbound (fan→model), outbound (model→fan)
    content = Column(Text, nullable=False)
    message_type = Column(String(32), default="text")  # text, image, video, ppv, tip, system
    is_ai_generated = Column(Boolean, default=False)
    is_ppv = Column(Boolean, default=False)
    ppv_price = Column(Float, default=0.0)
    ppv_unlocked = Column(Boolean, default=False)
    sentiment = Column(Float, nullable=True)  # -1 to 1, AI-analyzed
    intent = Column(String(64), nullable=True)  # greeting, question, flirt, purchase, complaint, custom_request
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    fan = relationship("Fan", back_populates="messages")


# ─── Social Accounts (Platform Signup + Approval) ──────────────────────

class SocialAccount(Base):
    """A social media account for a persona — requires operator approval before going live.
    
    Production flow:
    1. Operator requests account (draft → pending_approval)
    2. Generate temp email via mail.tm for signup
    3. Operator approves → account gets created on platform
    4. Profile data synced from persona (avatar, bio, display name)
    5. Content auto-posted from content packs
    """
    __tablename__ = "social_accounts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    persona_id = Column(String(36), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(32), nullable=False)  # instagram, facebook, onlyfans, tiktok, twitter, fanvue, fansly
    username = Column(String(128), nullable=False)
    display_name = Column(String(256), default="")
    email = Column(String(256), default="")  # signup email (temp email from mail.tm)
    password_hash = Column(String(512), default="")  # encrypted platform password
    profile_url = Column(String(512), default="")
    bio = Column(Text, default="")
    profile_image_url = Column(String(512), default="")  # synced from persona avatar
    status = Column(String(32), default="draft")  # draft, pending_approval, approved, signup_in_progress, active, rejected, suspended
    signup_step = Column(String(64), default="")  # email_generated, signup_started, email_verified, profile_complete, api_connected
    signup_progress = Column(Integer, default=0)  # 0-100 percent
    approval_notes = Column(Text, default="")  # operator notes on approval/rejection
    approved_by = Column(String(128), default="")
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, default="")
    last_posted_at = Column(DateTime(timezone=True), nullable=True)
    posts_count = Column(Integer, default=0)
    followers = Column(Integer, default=0)
    following = Column(Integer, default=0)
    api_connected = Column(Boolean, default=False)
    api_token = Column(String(512), default="")  # platform API token (encrypted)
    email_account_id = Column(String(256), default="")  # mail.tm account ID
    email_password = Column(String(256), default="")  # mail.tm email password
    email_token = Column(Text, default="")  # mail.tm JWT auth token
    email_domain = Column(String(256), default="")  # mail.tm domain used
    metadata_json = Column(JSON, default=dict)  # email tokens, platform-specific data
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    persona = relationship("Persona", back_populates="social_accounts")


# Add social_accounts relationship to Persona
Persona.social_accounts = relationship("SocialAccount", back_populates="persona", cascade="all, delete-orphan")


# ─── Team invites ─────────────────────────────────────────────────────

class TeamInvite(Base):
    """An invite for another human to use this Persona Studio instance.

    IMPORTANT — this is a roster/onboarding record, not access control. This
    app has no user table, no login, and no session layer, so nothing here
    gates a route: any client that can reach the API can call it whether or
    not it holds an invite. Redeeming an invite records who accepted and when,
    ready for a future auth layer to enforce. It does not currently restrict
    anything, and the UI says so rather than implying otherwise.
    """
    __tablename__ = "team_invites"
    __table_args__ = (
        Index("ix_team_invites_token", "token", unique=True),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token = Column(String(64), nullable=False, default=lambda: secrets.token_urlsafe(32))
    email = Column(String(256), default="")
    role = Column(String(32), default="viewer")  # operator | viewer
    invited_by = Column(String(128), default="admin")
    status = Column(String(32), default="pending")  # pending, accepted, revoked, expired
    note = Column(Text, default="")
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


# ─── Display joins ────────────────────────────────────────────────────

def pick_canonical_identity(candidates: list, anchor_hex: str | None):
    """The canonical-identity rule, in exactly one place.

    In order of authority:
      1. the identity the persona's lock points at (`identity_locks.identity_id`,
         the dashless hex of the approved Identity) — the same anchor every
         image-generation path uses, so the score shown is the score that is
         actually governing generation;
      2. else the newest identity past CANDIDATE (a persona mid-build has
         candidates but no settled identity yet);
      3. else the newest identity of any status.

    `candidates` must already be ordered oldest→newest. Extracted so the batched
    summary the UI reads and the on-demand re-evaluation pick the *same* identity
    — if they could disagree, a persona could show one identity's score while a
    re-check promoted a different one.
    """
    identity = None
    if anchor_hex:
        identity = next((i for i in candidates if i.id.hex == anchor_hex), None)
    if identity is None and candidates:
        settled = [i for i in candidates if i.status != IdentityStatus.CANDIDATE]
        identity = (settled or candidates)[-1]
    return identity


async def load_canonical_identity(db: AsyncSession, persona_id):
    """The canonical identity for one persona, loaded. See the rule above."""
    pid = persona_id if isinstance(persona_id, uuid.UUID) else uuid.UUID(str(persona_id))

    candidates = (
        await db.execute(
            select(Identity)
            .where(Identity.persona_id == pid)
            # Tie-broken on id, for the reason the batched version documents: a
            # build writes its candidates in one commit and their created_at
            # values can tie to the microsecond.
            .order_by(Identity.created_at, Identity.id)
        )
    ).scalars().all()

    anchor = (
        await db.execute(
            select(IdentityLock.identity_id).where(
                IdentityLock.persona_id == persona_storage_hex(pid)
            )
        )
    ).scalar_one_or_none()

    return pick_canonical_identity(list(candidates), anchor)


async def persona_identity_summary(
    db: AsyncSession, persona_ids: list
) -> dict[str, dict]:
    """Identity state and pack count per persona — the fields the UI already reads.

    `PersonaResponse` declares `identity_status`, `identity_score` and
    `packs_count`, and the Models list, the dashboard and the persona detail page
    all read them. `Persona` has none of those columns, so every response carried
    the field defaults — None, None, 0 — and no page could ever show them. The
    values live in two other tables, and nothing joined them, which is why a
    persona could finish a build with a trained, applied LoRA and still render as
    an unidentified model with no score.

    Canonical identity, in order of authority:
      1. the identity the persona's lock points at (`identity_locks.identity_id`,
         the dashless hex of the approved Identity) — the same anchor every
         image-generation path uses, so the score shown is the score that is
         actually governing generation;
      2. else the newest identity past CANDIDATE (a persona mid-build has
         candidates but no settled identity yet);
      3. else the newest identity of any status.

    Batched deliberately: one query per table for the whole page, not per persona.
    """
    from sqlalchemy import func

    ids = [p if isinstance(p, uuid.UUID) else uuid.UUID(str(p)) for p in persona_ids]
    if not ids:
        return {}

    identities = (
        await db.execute(
            select(Identity)
            .where(Identity.persona_id.in_(ids))
            # Tie-broken on id: a build writes its candidate identities in one
            # commit, so their created_at values can be identical to the
            # microsecond, and ordering on that alone leaves "the newest" up to
            # the database. Deterministic, if arbitrary, beats stable-looking.
            .order_by(Identity.created_at, Identity.id)
        )
    ).scalars().all()

    by_persona: dict[str, list[Identity]] = {}
    for identity in identities:
        by_persona.setdefault(str(identity.persona_id), []).append(identity)

    locked = {
        row[0]: row[1]
        for row in (
            await db.execute(
                select(IdentityLock.persona_id, IdentityLock.identity_id).where(
                    IdentityLock.persona_id.in_([persona_storage_hex(i) for i in ids])
                )
            )
        ).all()
        if row[1]
    }

    pack_counts = {
        str(row[0]): row[1]
        for row in (
            await db.execute(
                select(ContentPack.persona_id, func.count(ContentPack.id))
                .where(ContentPack.persona_id.in_(ids))
                .group_by(ContentPack.persona_id)
            )
        ).all()
    }

    summary: dict[str, dict] = {}
    for persona_id in ids:
        candidates = by_persona.get(str(persona_id), [])
        identity = pick_canonical_identity(
            candidates, locked.get(persona_storage_hex(persona_id))
        )

        # Whether the adapter's training base matches the checkpoint the image
        # path will render on. Only meaningful when an adapter exists — an
        # identity with no LoRA has no base to check, and reporting "unknown"
        # there would invent a problem rather than report one.
        lora_state = lora_note = ""
        if identity is not None and (identity.lora_model_path or ""):
            from app.config import get_settings
            from app.lora_base import base_mismatch

            verdict = base_mismatch(
                (identity.metadata_json or {}).get("lora_training_base"),
                get_settings().COMFYUI_CHECKPOINT,
            )
            lora_state, lora_note = verdict["state"], verdict["reason"]

        summary[str(persona_id)] = {
            "identity_status": (
                identity.status.value
                if identity is not None and hasattr(identity.status, "value")
                else (str(identity.status) if identity is not None else None)
            ),
            "identity_score": identity.consistency_score if identity is not None else None,
            "packs_count": pack_counts.get(str(persona_id), 0),
            "lora_base_state": lora_state,
            "lora_base_note": lora_note,
        }
    return summary


# ═══════════════════════════════════════════════════════════════════════
#  Fan-facing monetization slice
# ═══════════════════════════════════════════════════════════════════════
#
# Everything below is new. Two rules hold throughout, and both are load-bearing:
#
#   1. NEW TABLES ONLY. `init_db()` calls `create_all`, which creates missing
#      tables but never ALTERs an existing one. That is why the character sheet
#      and the fan link are side tables rather than columns added to `personas`
#      and `fans` — a new column there would silently never exist, and every
#      read would return the Python-side default with no error to notice.
#
#   2. MONEY IS INTEGER MINOR UNITS, NEVER Float. The existing
#      `Fan.total_spent` / `ppv_purchases` / `subscription_tier` and
#      `ChatMessage.ppv_unlocked` are kept as *derived caches*, written in the
#      same transaction as the ledger entries that justify them, so the existing
#      operator dashboard keeps working. Nothing reads them as authority for
#      access — `billing.entitlements.check_access` asks the ledger and the
#      entitlements table.


def _uuid() -> str:
    return str(uuid.uuid4())


# ─── Identity: the login ──────────────────────────────────────────────

class AppUser(Base):
    """A human with a login on the fan-facing site.

    Distinct from `Fan`, which is a *relationship* (one persona ↔ one
    subscriber). A user is the account that authenticates. The two are joined by
    `FanAccount` because one person may follow more than one persona, and
    because `fans` is a pre-existing table this slice must not alter.

    `date_of_birth` is stored as given, ISO `YYYY-MM-DD`. It is the input to the
    18+ check at signup and the record behind `adult_verified_at`. It is
    self-attested: this is a declared date, not a verified identity. See
    FAN_SLICE_TODO.md §Flags.
    """
    __tablename__ = "app_users"

    id = Column(String(36), primary_key=True, default=_uuid)
    email = Column(String(256), nullable=False, unique=True, index=True)
    # Encoded "scrypt$n$r$p$<salt_b64>$<hash_b64>" — see app/auth.py.
    password_hash = Column(String(512), nullable=False)
    display_name = Column(String(256), default="")
    date_of_birth = Column(String(10), default="")
    is_adult_confirmed = Column(Boolean, default=False, nullable=False)
    adult_verified_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(32), default="active")  # active | suspended | closed
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AuthSession(Base):
    """A logged-in browser session.

    Only the SHA-256 of the cookie token is stored, never the token itself, so a
    leak of this table does not hand over usable sessions. Revocation is a row
    write — which is the whole reason this is a session table and not a signed
    token.
    """
    __tablename__ = "auth_sessions"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), ForeignKey("app_users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    user_agent = Column(String(512), default="")
    created_at = Column(DateTime(timezone=True), default=utcnow)


class FanAccount(Base):
    """The link between a login and the fan relationship it owns."""
    __tablename__ = "fan_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "fan_id", name="uq_fan_account_user_fan"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), ForeignKey("app_users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    fan_id = Column(String(36), ForeignKey("fans.id", ondelete="CASCADE"),
                    nullable=False, index=True)
    persona_id = Column(String(36), ForeignKey("personas.id", ondelete="CASCADE"),
                        nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)


# ─── The character sheet ──────────────────────────────────────────────

class PersonaCharacter(Base):
    """Who the persona is, in her own words.

    `Persona.personality` is a list of adjectives and `voice_style` is a short
    string; neither can carry a backstory. This table is what the chat system
    prompt is actually built from, so a reply can reference something specific
    about her instead of generic filler.
    """
    __tablename__ = "persona_characters"

    id = Column(String(36), primary_key=True, default=_uuid)
    persona_id = Column(String(36), ForeignKey("personas.id", ondelete="CASCADE"),
                        nullable=False, unique=True, index=True)
    backstory = Column(Text, default="")
    speech_style = Column(Text, default="")
    catchphrases = Column(JSON, default=list)
    likes = Column(JSON, default=list)
    dislikes = Column(JSON, default=list)
    # What she will not do or discuss. Kept explicit rather than left to the
    # model's judgement, so an operator can point at it.
    boundaries = Column(Text, default="")
    example_dialogue = Column(JSON, default=list)  # [{"fan": ..., "her": ...}]
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class FanConversation(Base):
    """One chat thread, and the provider it is pinned to.

    Rotation between the two live LLMs is per *conversation*, not per message:
    a thread sticks to the provider it started on so the voice never changes
    mid-thread. Character consistency is the thing being sold, and two models
    in one conversation is the fastest way to lose it. See
    app/providers/fallback_llm.py.
    """
    __tablename__ = "fan_conversations"

    id = Column(String(36), primary_key=True, default=_uuid)
    fan_id = Column(String(36), ForeignKey("fans.id", ondelete="CASCADE"),
                    nullable=False, index=True)
    persona_id = Column(String(36), ForeignKey("personas.id", ondelete="CASCADE"),
                        nullable=False)
    served_by = Column(String(64), default="")  # provider pinned at first reply
    status = Column(String(32), default="open")  # open | closed
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ─── Money ────────────────────────────────────────────────────────────

class Wallet(Base):
    """A fan's simulated cash balance.

    `balance_minor` is a *cache* of the ledger. The entries are the truth, and
    both are written in one transaction. `GET /system/ledger/integrity`
    recomputes this from the entries so the two cannot drift unnoticed.
    """
    __tablename__ = "wallets"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), ForeignKey("app_users.id", ondelete="CASCADE"),
                     nullable=False, unique=True, index=True)
    currency = Column(String(8), default="USD")
    balance_minor = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class LedgerAccount(Base):
    """A named bucket money moves between, e.g. `user:<id>:available`.

    System accounts (`sys:*`) are created on demand and never carry a balance
    that anyone reads — they exist so every posting balances.
    """
    __tablename__ = "ledger_accounts"
    __table_args__ = (
        UniqueConstraint("code", name="uq_ledger_account_code"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    code = Column(String(128), nullable=False, index=True)
    kind = Column(String(32), default="user")  # user | system
    label = Column(String(256), default="")
    created_at = Column(DateTime(timezone=True), default=utcnow)


class LedgerTransaction(Base):
    """One balanced posting, made of two or more `LedgerEntry` rows."""
    __tablename__ = "ledger_transactions"

    id = Column(String(36), primary_key=True, default=_uuid)
    kind = Column(String(48), nullable=False)  # signup_grant|topup|subscription|ppv_unlock
    # Prevents the same economic event from posting twice. A double-clicked
    # unlock produces one transaction, not two.
    idempotency_key = Column(String(200), nullable=True, unique=True)
    memo = Column(String(512), default="")
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class LedgerEntry(Base):
    """One side of a double-entry posting.

    Invariant, enforced in `billing/ledger.py` before anything is written:
    per transaction, sum(debit) == sum(credit).
    """
    __tablename__ = "ledger_entries"

    id = Column(String(36), primary_key=True, default=_uuid)
    transaction_id = Column(String(36),
                            ForeignKey("ledger_transactions.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    account_code = Column(String(128), nullable=False, index=True)
    direction = Column(String(8), nullable=False)  # debit | credit
    amount_minor = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Payment(Base):
    """A record of the processor call that funded a top-up or subscription.

    With PAYMENT_PROCESSOR=fake this records a simulated charge and nothing
    leaves the machine. No card number, no expiry, no CVC is collected or
    stored anywhere in this codebase.
    """
    __tablename__ = "payments"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), ForeignKey("app_users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    processor = Column(String(32), default="fake")
    processor_ref = Column(String(128), default="")
    amount_minor = Column(Integer, nullable=False)
    currency = Column(String(8), default="USD")
    kind = Column(String(32), default="topup")  # topup | subscription | ppv
    status = Column(String(32), default="succeeded")  # succeeded|declined|refunded
    failure_reason = Column(String(256), default="")
    created_at = Column(DateTime(timezone=True), default=utcnow)


# ─── Access ───────────────────────────────────────────────────────────

class SubscriptionPlan(Base):
    """A recurring tier. `rank` is what content gates are compared against."""
    __tablename__ = "subscription_plans"
    __table_args__ = (
        UniqueConstraint("code", name="uq_subscription_plan_code"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    code = Column(String(64), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    price_minor = Column(Integer, nullable=False, default=0)
    period_days = Column(Integer, default=30)
    rank = Column(Integer, default=1)  # higher unlocks more
    perks = Column(Text, default="")
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Subscription(Base):
    """An active or past subscription held by a user."""
    __tablename__ = "subscriptions"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), ForeignKey("app_users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    fan_id = Column(String(36), ForeignKey("fans.id", ondelete="CASCADE"),
                    nullable=True)
    plan_code = Column(String(64), nullable=False)
    plan_rank = Column(Integer, default=1)
    status = Column(String(32), default="active")  # active | expired | cancelled
    started_at = Column(DateTime(timezone=True), default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Product(Base):
    """A purchasable item — a photoset, a video, a single image.

    `is_adult` defaults to False and stays False in this slice: the DOB check
    is self-attested, which is not age assurance, so nothing here should be
    reachable only by an adult. See FAN_SLICE_TODO.md §Flags.
    """
    __tablename__ = "products"

    id = Column(String(36), primary_key=True, default=_uuid)
    persona_id = Column(String(36), ForeignKey("personas.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    title = Column(String(256), nullable=False)
    description = Column(Text, default="")
    price_minor = Column(Integer, nullable=False, default=0)
    kind = Column(String(32), default="photoset")  # photoset | video | image | bundle
    # A subscriber at this rank or above gets it without paying again. 0 means
    # PPV only.
    min_tier_rank = Column(Integer, default=0)
    is_adult = Column(Boolean, default=False, nullable=False)
    status = Column(String(32), default="published")  # draft | published | archived
    cover_path = Column(String(512), default="")
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProductMedia(Base):
    """One file inside a product, addressed by a path under `storage/`."""
    __tablename__ = "product_media"

    id = Column(String(36), primary_key=True, default=_uuid)
    product_id = Column(String(36), ForeignKey("products.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    rel_path = Column(String(512), nullable=False)
    caption = Column(String(512), default="")
    position = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Entitlement(Base):
    """Proof of purchase.

    The unique constraint is the second half of the idempotency story: even if
    two unlock requests raced past the ledger's idempotency key, the database
    will not admit two entitlements for the same user, product and kind.
    """
    __tablename__ = "entitlements"
    __table_args__ = (
        UniqueConstraint("user_id", "product_id", "kind",
                         name="uq_entitlement_user_product_kind"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), ForeignKey("app_users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    product_id = Column(String(36), ForeignKey("products.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    kind = Column(String(32), default="ppv")  # ppv | subscription
    source_transaction_id = Column(String(36), default="")
    granted_at = Column(DateTime(timezone=True), default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=True)


class AccessEvent(Base):
    """Every attempt to reach paid content, allowed or denied.

    Denials are recorded too. An access log that only contains successes cannot
    answer "did anyone try", which is the question it exists for.
    """
    __tablename__ = "access_events"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), nullable=True, index=True)
    product_id = Column(String(36), nullable=True, index=True)
    allowed = Column(Boolean, nullable=False)
    reason = Column(String(64), default="")  # entitled | no_entitlement | not_signed_in
    created_at = Column(DateTime(timezone=True), default=utcnow)


# ─── Audit ────────────────────────────────────────────────────────────

class AuditEvent(Base):
    """The spine of the slice: every step writes one of these.

    Registration, age confirmation, sign-in, top-up, unlock, content served —
    each lands here, and `GET /fan/activity` reads it back. This is what makes
    "every step is logged" a fact about the system rather than a claim in a
    README.
    """
    __tablename__ = "audit_events"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), nullable=True, index=True)
    actor = Column(String(64), default="system")  # system | user | operator
    action = Column(String(64), nullable=False, index=True)
    object_type = Column(String(64), default="")
    object_id = Column(String(64), default="")
    detail = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)

