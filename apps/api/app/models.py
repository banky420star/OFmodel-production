"""Persona Studio — SQLAlchemy models for all 14 phases."""

from __future__ import annotations

import uuid
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
    adult_verified = Column(Boolean, default=True)
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
