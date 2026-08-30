"""Persona Studio — Schema validation tests."""

import pytest
from app.schemas import (
    PersonaCreate, AppearanceProfile, ShootCreate,
    ContentPackCreate, WorkflowResponse,
)


def test_appearance_profile_defaults():
    ap = AppearanceProfile()
    assert ap.hair == "long blonde"
    assert ap.eye_colour == "blue"
    assert isinstance(ap.style_preferences, list)


def test_persona_create_valid():
    p = PersonaCreate(name="Ava", age=24)
    assert p.name == "Ava"
    assert p.age == 24
    assert p.adult_verified is True
    assert p.synthetic_identity is True


def test_persona_create_rejects_underage():
    with pytest.raises(Exception):
        PersonaCreate(name="Test", age=17)


def test_persona_create_empty_name():
    with pytest.raises(Exception):
        PersonaCreate(name="", age=24)


def test_shoot_create_defaults():
    s = ShootCreate(theme="lifestyle")
    assert s.theme == "lifestyle"
    assert s.image_count == 10
    assert s.identity_id is None


def test_content_pack_create_defaults():
    c = ContentPackCreate()
    assert c.platform == "all"
    assert c.name == ""


def test_workflow_response_model():
    from datetime import datetime, timezone
    from uuid import uuid4
    wr = WorkflowResponse(
        id=uuid4(), name="test", status="pending",
        workflow_type="test", current_step="",
        persona_id=uuid4(), input_data={}, output_data={},
        error_message="", retry_count=0,
        created_at=datetime.now(timezone.utc),
        started_at=None, completed_at=None,
    )
    assert wr.status == "pending"
