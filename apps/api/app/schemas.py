"""Persona Studio — Pydantic schemas for all domain objects."""

from __future__ import annotations
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ─── Appearance ────────────────────────────────────────────────────────

class AppearanceProfile(BaseModel):
    hair: str = "long blonde"
    hair_length: str = "long"
    hair_colour: str = "blonde"
    eye_colour: str = "blue"
    facial_characteristics: str = ""
    skin_characteristics: str = ""
    body_characteristics: str = ""
    height_profile: str = "5'7\""
    identifying_synthetic_features: str = "distinctive smile, high cheekbones"
    style_preferences: list[str] = Field(default_factory=lambda: ["luxury", "lifestyle"])


# ─── Persona ───────────────────────────────────────────────────────────

class PersonaCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    age: int = Field(..., ge=18, le=99)
    description: str = ""
    adult_verified: bool = True
    synthetic_identity: bool = True
    appearance: AppearanceProfile = Field(default_factory=AppearanceProfile)
    personality: list[str] = Field(default_factory=lambda: ["confident", "playful"])
    brand: str = "luxury lifestyle"
    voice_style: str = "South African English"
    publishing_frequency: str = "5 packs/week"
    metadata_json: dict = Field(default_factory=dict)


class PersonaResponse(BaseModel):
    id: UUID
    name: str
    age: int
    status: str
    description: str
    adult_verified: bool
    synthetic_identity: bool
    appearance: AppearanceProfile
    personality: list[str]
    brand: str
    voice_style: str
    publishing_frequency: str
    identity_status: str | None = None
    identity_score: float | None = None
    packs_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ─── Identity ──────────────────────────────────────────────────────────

class IdentityCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    metadata_json: dict = Field(default_factory=dict)


class IdentityCandidate(BaseModel):
    id: UUID
    name: str
    status: str
    consistency_score: float
    metadata_json: dict = Field(default_factory=dict)
    created_at: datetime

    class Config:
        from_attributes = True


class IdentityResponse(BaseModel):
    id: UUID
    persona_id: UUID
    name: str
    status: str
    reference_images: list[str]
    lora_model_path: str
    consistency_score: float
    metadata_json: dict
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ─── Reference Dataset ─────────────────────────────────────────────────

class ReferenceDatasetResponse(BaseModel):
    id: UUID
    identity_id: UUID
    name: str
    total_images: int
    quality_score: float
    created_at: datetime

    class Config:
        from_attributes = True


# ─── Workflow ──────────────────────────────────────────────────────────

class WorkflowResponse(BaseModel):
    id: UUID
    name: str
    status: str
    workflow_type: str
    current_step: str
    persona_id: UUID | None
    input_data: dict
    output_data: dict
    error_message: str
    retry_count: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    class Config:
        from_attributes = True


class WorkflowStepResponse(BaseModel):
    id: UUID
    name: str
    step_type: str
    order: int
    status: str
    provider_name: str
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str

    class Config:
        from_attributes = True


# ─── Shoot ─────────────────────────────────────────────────────────────

class ShootCreate(BaseModel):
    name: str = ""
    theme: str = ""
    prompt_template: str = ""
    image_count: int = Field(default=10, ge=1, le=50)
    identity_id: UUID | None = None
    metadata_json: dict = Field(default_factory=dict)


class ShootPlan(BaseModel):
    concept: str
    objective: str
    location: str
    lighting: str
    wardrobe: str
    camera_style: str
    image_shots: list[dict[str, Any]] = Field(default_factory=list)
    video_shots: list[dict[str, Any]] = Field(default_factory=list)
    scripts: list[str] = Field(default_factory=list)
    continuity_notes: list[str] = Field(default_factory=list)
    expected_assets: int = 0


class ShootResponse(BaseModel):
    id: UUID
    persona_id: UUID
    identity_id: UUID | None
    name: str
    status: str
    theme: str
    image_count: int
    generated_images: list
    created_at: datetime
    completed_at: datetime | None

    class Config:
        from_attributes = True


# ─── Content Pack ──────────────────────────────────────────────────────

class ContentPackCreate(BaseModel):
    name: str = ""
    platform: str = "all"
    metadata_json: dict = Field(default_factory=dict)


class ContentPackResponse(BaseModel):
    id: UUID
    persona_id: UUID
    name: str
    status: str
    platform: str
    images: list
    videos: list
    voiceovers: list
    captions: list
    quality_score: float = 0.0
    created_at: datetime
    published_at: datetime | None

    class Config:
        from_attributes = True


# ─── QA ────────────────────────────────────────────────────────────────

class QAResponse(BaseModel):
    id: UUID
    qa_type: str
    status: str
    score: float
    threshold: float
    details: dict
    images_checked: int
    passed_count: int
    failed_count: int
    created_at: datetime

    class Config:
        from_attributes = True


# ─── Analytics ─────────────────────────────────────────────────────────

class AnalyticsSnapshotResponse(BaseModel):
    id: UUID
    persona_id: UUID
    snapshot_date: datetime
    platform: str
    followers: int
    likes: int
    comments: int
    shares: int
    views: int
    engagement_rate: float
    revenue: float
    costs: float

    class Config:
        from_attributes = True


# ─── Forecast ──────────────────────────────────────────────────────────

class ForecastScenario(BaseModel):
    scenario: str = "base"  # conservative, base, aggressive
    horizon_months: int = 24
    monthly_followers: list[int] = Field(default_factory=list)
    monthly_revenue: list[float] = Field(default_factory=list)
    monthly_costs: list[float] = Field(default_factory=list)
    monthly_engagement: list[float] = Field(default_factory=list)
    break_even_month: int | None = None
    confidence_intervals: dict = Field(default_factory=dict)


class ForecastResponse(BaseModel):
    id: UUID
    persona_id: UUID
    horizon_months: int
    scenarios: list[ForecastScenario]
    model_version: str
    created_at: datetime

    class Config:
        from_attributes = True


# ─── Scheduled Post ────────────────────────────────────────────────────

class ScheduledPostResponse(BaseModel):
    id: UUID
    persona_id: UUID
    content_pack_id: UUID | None
    platform: str
    scheduled_at: datetime
    posted_at: datetime | None
    status: str

    class Config:
        from_attributes = True


# ─── Audit ─────────────────────────────────────────────────────────────

class AuditEventResponse(BaseModel):
    id: UUID
    timestamp: datetime
    actor: str
    persona_id: UUID | None
    workflow_id: UUID | None
    action: str
    details: dict = Field(default_factory=dict)

    class Config:
        from_attributes = True


# ─── Health ────────────────────────────────────────────────────────────

class HealthCheck(BaseModel):
    service: str
    status: str  # green, yellow, red
    message: str = ""
    latency_ms: float = 0


class SystemHealth(BaseModel):
    overall: str
    checks: list[HealthCheck]
