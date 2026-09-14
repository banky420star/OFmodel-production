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


# ─── Model Manager (event-driven persona control plane) ─────────────

class ManagerStatus(str, enum.Enum):
    INITIALIZING = "INITIALIZING"
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    PRODUCING = "PRODUCING"
    QA = "QA"
    SCHEDULING = "SCHEDULING"
    PUBLISHING = "PUBLISHING"
    ENGAGING = "ENGAGING"
    ANALYZING = "ANALYZING"
    WAITING = "WAITING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    BLOCKED = "BLOCKED"
    RETRYING = "RETRYING"
    ERROR = "ERROR"
    PAUSED = "PAUSED"


class ManagerTaskStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class AssetStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    QUEUED = "QUEUED"
    GENERATING = "GENERATING"
    GENERATED = "GENERATED"
    QA_PENDING = "QA_PENDING"
    QA_FAILED = "QA_FAILED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    ARCHIVED = "ARCHIVED"


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


# ─── Market Intelligence (v2 architecture) ───────────────────────────
# See ARCHITECTURE_VISION.md. Permitted public sources only — no platform
# that prohibits crawling is ever crawled. Blocked/failed sources are
# recorded with their real status; observations are strategy primitives,
# never source media or a creator's likeness.

class IntelSourceRun(Base):
    """One crawl attempt against one permitted public source."""
    __tablename__ = "intel_source_runs"
    __table_args__ = (Index("ix_intel_source_runs_source", "source"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source = Column(String(64), nullable=False, default="")  # google_news_rss, reddit_public, x_public, tiktok_public, own_account_analytics
    status = Column(String(32), nullable=False, default="running")  # running | ok | blocked | error | not_implemented
    niche = Column(String(128), default="general")
    query = Column(String(256), default="")
    items_seen = Column(Integer, default=0)
    observations_new = Column(Integer, default=0)
    detail = Column(JSON, default=dict)  # e.g. {"http_status": 403} — real reason, always
    started_at = Column(DateTime(timezone=True), default=utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class MarketObservation(Base):
    """A normalized strategy primitive extracted from a permitted public
    source (aggregate-level trend signal, no source media retained)."""
    __tablename__ = "market_observations"
    __table_args__ = (
        Index("ix_market_observations_topic", "topic_digest"),
        Index("ix_market_observations_niche", "niche"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_run_id = Column(UUID(as_uuid=True), ForeignKey("intel_source_runs.id", ondelete="CASCADE"), nullable=True)
    source = Column(String(64), default="")
    niche = Column(String(128), default="general")
    topic_digest = Column(String(32), nullable=False, default="")  # stable md5 of normalized topic
    topic = Column(Text, default="")  # headline/title text of the public signal
    strategy = Column(JSON, default=dict)  # extracted primitives: hook_style, format, posting_hour_utc, ...
    trend_score = Column(Float, default=0.0)
    signal_count = Column(Integer, default=1)
    observed_at = Column(DateTime(timezone=True), default=utcnow)


class StrategyHypothesis(Base):
    """A measurable experiment for OUR OWN models derived from aggregated
    market patterns — never a copy instruction. `experiment` defines the
    A/B comparison whose measured results drive strategy allocation."""
    __tablename__ = "strategy_hypotheses"
    __table_args__ = (Index("ix_strategy_hypotheses_status", "status"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    persona_id = Column(UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"), nullable=True)
    niche = Column(String(128), default="general")
    status = Column(String(32), nullable=False, default="open")  # open | testing | accepted | rejected
    statement = Column(Text, default="")
    pattern_digest = Column(String(32), default="")  # dedupe: one open hypothesis per pattern
    evidence = Column(JSON, default=dict)  # aggregated signals behind the hypothesis
    experiment = Column(JSON, default=dict)  # variant_a vs variant_b, metric, min sample
    result = Column(JSON, default=dict)  # filled by the measurement loop when tested
    created_at = Column(DateTime(timezone=True), default=utcnow)
    decided_at = Column(DateTime(timezone=True), nullable=True)


# ─── Fanvue Platform Manager (v0, 2026-09-13) ─────────────────────────

class PlatformAccount(Base):
    """A monetization platform account (Fanvue-first per ARCHITECTURE_VISION.md
    invariant 3). AI-disclosure and KYC are first-class compliance fields, not
    an afterthought — Fanvue permits fully AI creators only when disclosed."""
    __tablename__ = "platform_accounts"
    __table_args__ = (Index("ix_platform_accounts_platform", "platform"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    persona_id = Column(UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(32), nullable=False, default="fanvue")  # fanvue | onlyfans | ...
    handle = Column(String(128), default="")
    status = Column(String(32), nullable=False, default="pending_setup")
    # pending_setup | onboarding | active | paused | suspended

    # ── Compliance (Fanvue AI-creator requirements) ──
    is_ai_disclosed = Column(Boolean, nullable=False, default=False)  # AI-creator disclosure ON
    ai_disclosure_text = Column(Text, default="")  # the exact disclosure shown on the profile
    kyc_status = Column(String(32), nullable=False, default="not_started")
    # not_started | submitted | verified | rejected
    consent_owner = Column(String(128), default="")  # the verified human operating this account
    terms_version = Column(String(32), default="")  # platform ToS version acknowledged

    # ── Economics ──
    subscription_price = Column(Float, default=0.0)  # monthly USD
    currency = Column(String(8), default="USD")
    subscriber_count = Column(Integer, default=0)
    earnings_total = Column(Float, default=0.0)

    # ── Posting cadence targets (per day), used by inventory planning ──
    target_public_posts_per_day = Column(Float, default=1.0)
    target_subscriber_posts_per_day = Column(Float, default=2.0)
    target_premium_items_per_week = Column(Float, default=3.0)

    settings_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ContentInventoryItem(Base):
    """One item in a platform account's inventory ladder. `is_mock` provenance
    carries over from the asset pipeline — inventory counts stay honest even
    when the underlying generation fell back to the mock provider."""
    __tablename__ = "content_inventory"
    __table_args__ = (Index("ix_content_inventory_tier", "tier"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("platform_accounts.id", ondelete="CASCADE"), nullable=False)
    persona_id = Column(UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    tier = Column(String(16), nullable=False)  # public | subscriber | premium
    content_type = Column(String(16), nullable=False, default="image")  # image | video
    asset_key = Column(String(512), default="")  # storage key / URL of the real asset
    source_type = Column(String(32), nullable=False, default="generated")  # generated | uploaded
    is_mock = Column(Boolean, nullable=False, default=False)  # provenance from generation
    caption = Column(Text, default="")
    status = Column(String(32), nullable=False, default="ready")  # ready | posted | archived
    posted_at = Column(DateTime(timezone=True), nullable=True)
    shoot_id = Column(UUID(as_uuid=True), ForeignKey("shoots.id", ondelete="SET NULL"), nullable=True)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class ShootOrder(Base):
    """A shortage-driven production order. Created by the inventory planner
    when a tier falls below its minimum, executed by the Content Director
    (persona shoot pipeline). Tracks fulfilment honestly."""
    __tablename__ = "shoot_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("platform_accounts.id", ondelete="CASCADE"), nullable=False)
    persona_id = Column(UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    tier = Column(String(16), nullable=False)  # public | subscriber | premium
    content_type = Column(String(16), nullable=False, default="image")
    units_requested = Column(Integer, nullable=False, default=1)
    units_fulfilled = Column(Integer, nullable=False, default=0)
    reason = Column(Text, default="")  # the shortage that triggered this order
    status = Column(String(32), nullable=False, default="open")  # open | queued | fulfilled | cancelled
    shoot_id = Column(UUID(as_uuid=True), ForeignKey("shoots.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    fulfilled_at = Column(DateTime(timezone=True), nullable=True)


# ─── Model Manager domain (persistent control plane per persona) ──────

class ModelManager(Base):
    """One persistent manager per persona — the control brain.

    Status is an explicit work state (ManagerStatus), never a boolean.
    """
    __tablename__ = "model_managers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    persona_id = Column(UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False, unique=True)
    status = Column(SAEnum(ManagerStatus), default=ManagerStatus.INITIALIZING, nullable=False)
    autonomy_level = Column(Integer, default=2)  # 0=approve-all 1=auto-low-risk 2=full-auto
    current_objective = Column(String(512), default="")
    current_task_id = Column(UUID(as_uuid=True), nullable=True)  # → manager_tasks.id (no FK: circular dep breaks SQLite drop ordering)
    last_heartbeat_at = Column(DateTime(timezone=True), default=utcnow)
    next_wake_at = Column(DateTime(timezone=True), nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, default="")
    failure_count = Column(Integer, default=0)
    paused = Column(Integer, default=0)  # sqlite-safe boolean
    memory_json = Column(JSON, default=dict)  # structured operational memory
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    persona = relationship("Persona", backref="manager")


class ManagerTask(Base):
    """Durable ledger of every action a manager performs or plans."""
    __tablename__ = "manager_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    persona_id = Column(UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    manager_id = Column(UUID(as_uuid=True), ForeignKey("model_managers.id", ondelete="CASCADE"), nullable=False)
    task_type = Column(String(48), nullable=False)  # PLAN_CONTENT, CREATE_SHOOT, GENERATE_IMAGES, ...
    priority = Column(Integer, default=5)  # 1 (highest) .. 9
    status = Column(SAEnum(ManagerTaskStatus), default=ManagerTaskStatus.PENDING, nullable=False)
    source = Column(String(48), default="manager")  # manager | heartbeat | user | event | retry
    payload = Column(JSON, default=dict)
    result = Column(JSON, default=dict)
    error = Column(Text, default="")
    error_kind = Column(String(48), default="")  # TEMPORARY_PROVIDER_FAILURE | RATE_LIMIT | INVALID_REQUEST | IDENTITY_QA_FAILURE | CONTENT_QA_FAILURE
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    scheduled_for = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, default=0)
    provider = Column(String(64), default="")
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    manager = relationship(
        "ModelManager", backref="tasks",
        foreign_keys=[manager_id],
    )


class ManagerEvent(Base):
    """Append-only event log: decisions, state changes, blockers."""
    __tablename__ = "manager_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    persona_id = Column(UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    manager_id = Column(UUID(as_uuid=True), ForeignKey("model_managers.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(48), nullable=False)  # decision | state_change | blocker | error | info
    message = Column(Text, default="")
    data = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class ShotPlan(Base):
    """Per-shot production specification for a shoot (a real plan, not 6
    near-identical prompts)."""
    __tablename__ = "shot_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shoot_id = Column(UUID(as_uuid=True), ForeignKey("shoots.id", ondelete="CASCADE"), nullable=False)
    persona_id = Column(UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    shot_number = Column(Integer, nullable=False)
    shot_type = Column(String(48), default="portrait")  # portrait | full_body | detail | candid | product
    scene = Column(String(256), default="")
    composition = Column(String(256), default="")
    wardrobe = Column(String(256), default="")
    camera = Column(String(128), default="")
    lighting = Column(String(128), default="")
    pose = Column(String(128), default="")
    expression = Column(String(128), default="")
    background = Column(String(128), default="")
    aspect_ratio = Column(String(16), default="4:5")
    motion_prompt = Column(Text, default="")  # for video candidates
    generation_status = Column(SAEnum(AssetStatus), default=AssetStatus.PLANNED, nullable=False)
    qa_status = Column(String(32), default="")  # pending | passed | failed
    qa_json = Column(JSON, default=dict)
    asset_key = Column(String(512), default="")  # storage key once generated
    error = Column(Text, default="")
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    shoot = relationship("Shoot", backref="shot_plans")
