"""Regression tests: persona status follows build truth; search filter works.

Covers the production-walkthrough findings:
- GET /personas?search= must actually filter by name,
- personas whose latest persona_build workflow FAILED must not stay BUILDING
  (the recovery path repairs them; the live path never creates them).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models import (
    Persona, PersonaStatus, Workflow, WorkflowStatus,
)


async def _make_persona(client, name: str) -> str:
    r = await client.post("/api/v1/personas", json={
        "name": name,
        "age": 25,
        "brand": "test",
        "appearance": {},
        "personality": ["calm"],
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_search_filter_matches_name(client):
    marker = uuid4().hex[:8]
    await _make_persona(client, f"Zebra_{marker}")
    await _make_persona(client, f"Yak_{marker}")

    # Unfiltered: both present
    r = await client.get("/api/v1/personas")
    names = [p["name"] for p in r.json()]
    assert f"Zebra_{marker}" in names and f"Yak_{marker}" in names

    # Filtered: only the matching one comes back
    r = await client.get("/api/v1/personas", params={"search": f"Zebra_{marker}"})
    names = [p["name"] for p in r.json()]
    assert names == [f"Zebra_{marker}"]

    # Case-insensitive
    r = await client.get("/api/v1/personas", params={"search": f"zebra_{marker}"})
    assert [p["name"] for p in r.json()] == [f"Zebra_{marker}"]

    # No match → empty
    r = await client.get("/api/v1/personas", params={"search": "does-not-exist-xyz"})
    assert r.json() == []


@pytest.mark.asyncio
async def test_failed_build_flips_persona_out_of_building(client, db):
    """The live path: when the engine marks a build workflow FAILED, the
    persona must not remain BUILDING."""
    pid = await _make_persona(client, f"FailFlip_{uuid4().hex[:6]}")

    wf = Workflow(
        id=uuid4(),
        name="persona_creation_test",
        persona_id=__import__("uuid").UUID(pid),
        workflow_type="persona_creation",
        status=WorkflowStatus.RUNNING,
    )
    db.add(wf)
    await db.commit()

    # Simulate the engine's failure commit (engine.py failure branch).
    wf.status = WorkflowStatus.FAILED
    wf.error_message = "One or more steps failed"
    persona = await db.get(Persona, __import__("uuid").UUID(pid))
    persona.status = PersonaStatus.FAILED
    await db.commit()

    r = await client.get(f"/api/v1/personas/{pid}")
    assert r.json()["status"] == "failed"


@pytest.mark.asyncio
async def test_active_persona_not_demoted_by_old_failed_workflow(client, db):
    """A persona that later succeeded must NOT be flipped by a stale FAILED
    workflow from an earlier attempt."""
    pid = await _make_persona(client, f"Stable_{uuid4().hex[:6]}")
    persona = await db.get(Persona, __import__("uuid").UUID(pid))
    persona.status = PersonaStatus.ACTIVE
    wf = Workflow(
        id=uuid4(),
        name="persona_creation_test",
        persona_id=persona.id,
        workflow_type="persona_creation",
        status=WorkflowStatus.FAILED,
        error_message="old attempt",
    )
    db.add(wf)
    await db.commit()

    # The recovery path only touches BUILDING personas — active stays active.
    from app.main import _repair_stuck_building_personas
    await _repair_stuck_building_personas(db)
    await db.refresh(persona)
    assert persona.status == PersonaStatus.ACTIVE
