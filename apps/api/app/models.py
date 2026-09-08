"""Persona Studio — SQLAlchemy models for all 14 phases."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Text, Boolean, Integer, Float, DateTime,
    ForeignKey, JSON, Enum as SAEnum, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import enum

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


class IdentityStatus(str, enum.Enum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    TRAINING = "training"
    READY = "ready"
    FAILED = "failed"


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
