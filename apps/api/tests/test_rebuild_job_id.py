"""Persona Studio — the two ways to start a build must both return a job id.

`POST /personas/{id}/rebuild` exists for personas whose build ran before the
identity-lock pipeline existed. It returned `{"status": "building",
"persona_id": ...}` and nothing else, so a client had no id to poll: the Create
Model page's failure path ("Try again") had no way to follow the retry it had
just started. `POST /personas` returns `job_id`; this one must too, or the retry
is a black box.

The job itself is not run here — `spawn_job` is replaced, because a real build
loads an SDXL pipeline and generates for two hours.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import app.routes.personas as personas_routes
from app.models import Persona, PersonaStatus


@pytest.mark.asyncio
async def test_rebuild_returns_a_followable_job_id(client, db, monkeypatch):
    persona = Persona(name=f"Rebuild_{uuid.uuid4().hex[:6]}", age=25)
    db.add(persona)
    await db.commit()
    persona_id = persona.id

    spawned: list[dict] = []
    job_id = uuid.uuid4()

    async def _fake_spawn_job(session, *, job_type, persona_id=None, message="", **kwargs):
        spawned.append({"job_type": job_type, "persona_id": persona_id, "message": message})
        return SimpleNamespace(id=job_id)

    monkeypatch.setattr(personas_routes, "spawn_job", _fake_spawn_job)

    resp = await client.post(f"/api/v1/personas/{persona_id}/rebuild")
    assert resp.status_code == 200, resp.text
    payload = resp.json()

    assert payload["job_id"] == str(job_id), (
        "without a job_id the retry cannot be polled — the UI re-seeds its "
        "progress view from this field"
    )
    assert payload["persona_id"] == str(persona_id)
    assert payload["status"] == "building"

    assert spawned and spawned[0]["job_type"] == "persona_build"
    assert spawned[0]["persona_id"] == persona_id


@pytest.mark.asyncio
async def test_rebuild_moves_the_persona_into_building(client, db, monkeypatch):
    """The status change must be committed before the job is spawned — the
    spawned job reads the persona back from its own session."""
    persona = Persona(name=f"Rebuild_{uuid.uuid4().hex[:6]}", age=25)
    db.add(persona)
    await db.commit()

    seen_status: list[str] = []

    async def _fake_spawn_job(session, *, job_type, persona_id=None, message="", **kwargs):
        row = await session.get(Persona, persona_id)
        seen_status.append(row.status.value if hasattr(row.status, "value") else row.status)
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(personas_routes, "spawn_job", _fake_spawn_job)

    resp = await client.post(f"/api/v1/personas/{persona.id}/rebuild")
    assert resp.status_code == 200, resp.text
    assert seen_status == ["building"]


@pytest.mark.asyncio
async def test_rebuild_404s_on_an_unknown_persona(client):
    resp = await client.post(f"/api/v1/personas/{uuid.uuid4()}/rebuild")
    assert resp.status_code == 404
