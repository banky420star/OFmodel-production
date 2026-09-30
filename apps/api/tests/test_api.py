"""Persona Studio — API integration tests."""

import pytest
import uuid

def _uniq(name: str) -> str:
    return f"{name}_{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_health_check(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    # New format: {"status": "healthy"/"degraded", "providers": {...}}
    assert data["status"] in ("healthy", "degraded")
    assert len(data["providers"]) > 0


@pytest.mark.asyncio
async def test_create_persona(client):
    resp = await client.post("/api/v1/personas", json={
        "name": _uniq("Ava"),
        "age": 24,
        "description": "Luxury lifestyle model",
        "adult_verified": True,
        "synthetic_identity": True,
        "personality": ["confident", "playful"],
        "brand": "luxury lifestyle",
        "voice_style": "South African English",
        "publishing_frequency": "5 packs/week",
    })
    assert resp.status_code in (200, 201)
    data = resp.json()
    assert "Ava" in data["name"]
    assert data["age"] == 24
    # Status is "building" initially (workflow runs async), becomes "active" after
    assert data["status"] in ("active", "building")


@pytest.mark.asyncio
async def test_create_persona_persists_requested_fields(client):
    """POST /personas must store what it was asked to store.

    Regression: the handler built Persona(name=..., status=..., appearance=...)
    and omitted age, description, adult_verified and synthetic_identity, so the
    request's values were silently replaced by the column defaults. It stayed
    invisible while adult_verified defaulted to True — the payload said True and
    the default said True, so the assertion below passed by coincidence. The
    values here are deliberately unlike the defaults (age 31 not 24,
    adult_verified False not True) so a dropped field fails loudly.
    """
    resp = await client.post("/api/v1/personas", json={
        "name": _uniq("RoundTrip"),
        "age": 31,
        "description": "distinctive description that must survive the round trip",
        "adult_verified": False,
        "synthetic_identity": True,
    })
    assert resp.status_code in (200, 201)
    data = resp.json()
    assert data["age"] == 31
    assert data["description"] == "distinctive description that must survive the round trip"
    assert data["adult_verified"] is False
    assert data["synthetic_identity"] is True

    # And it must be stored, not just echoed — read it back from the API.
    got = await client.get(f"/api/v1/personas/{data['id']}")
    assert got.status_code == 200
    stored = got.json()
    assert stored["age"] == 31
    assert stored["description"] == "distinctive description that must survive the round trip"
    assert stored["adult_verified"] is False


@pytest.mark.asyncio
async def test_create_persona_rejects未成年(client):
    resp = await client.post("/api/v1/personas", json={
        "name": "Underage",
        "age": 17,
    })
    assert resp.status_code == 422  # validation error


@pytest.mark.asyncio
async def test_list_personas(client):
    # Create one first
    await client.post("/api/v1/personas", json={
        "name": _uniq("Luna"), "age": 25, "adult_verified": True, "synthetic_identity": True,
    })
    resp = await client.get("/api/v1/personas")
    assert resp.status_code == 200
    personas = resp.json()
    assert len(personas) >= 1


@pytest.mark.asyncio
async def test_get_persona(client):
    create_resp = await client.post("/api/v1/personas", json={
        "name": _uniq("Nova"), "age": 22, "adult_verified": True, "synthetic_identity": True,
    })
    persona_id = create_resp.json()["id"]
    resp = await client.get(f"/api/v1/personas/{persona_id}")
    assert resp.status_code == 200
    assert "Nova" in resp.json()["name"]


@pytest.mark.asyncio
async def test_create_shoot(client):
    create_resp = await client.post("/api/v1/personas", json={
        "name": _uniq("Stella"), "age": 26, "adult_verified": True, "synthetic_identity": True,
    })
    persona_id = create_resp.json()["id"]
    resp = await client.post(f"/api/v1/personas/{persona_id}/shoots", json={
        "name": "Summer Campaign",
        "theme": "beach lifestyle",
    })
    assert resp.status_code in (200, 201)
    data = resp.json()
    assert data["name"] == "Summer Campaign"
    assert data["status"] in ("queued", "draft")


@pytest.mark.asyncio
async def test_create_and_assemble_content_pack(client):
    create_resp = await client.post("/api/v1/personas", json={
        "name": _uniq("Crystal"), "age": 23, "adult_verified": True, "synthetic_identity": True,
    })
    persona_id = create_resp.json()["id"]
    resp = await client.post(f"/api/v1/personas/{persona_id}/packs", json={
        "name": "Holiday Pack",
        "platform": "instagram",
    })
    assert resp.status_code in (200, 201)
    data = resp.json()
    assert data["name"] == "Holiday Pack"


@pytest.mark.asyncio
async def test_demo_generate_endpoints_removed(client):
    """The synthetic analytics/forecast generators were deleted (de-demo).

    Real data comes from Instagram sync or manual entry — a call to a
    synthetic generator must 404, never fabricate numbers.
    """
    create_resp = await client.post("/api/v1/personas", json={
        "name": _uniq("Zara"), "age": 24, "adult_verified": True, "synthetic_identity": True,
    })
    persona_id = create_resp.json()["id"]
    resp = await client.post(f"/api/v1/personas/{persona_id}/analytics/generate")
    assert resp.status_code == 404
    resp = await client.post(f"/api/v1/personas/{persona_id}/forecasts/generate")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_autopilot_toggle(client):
    create_resp = await client.post("/api/v1/personas", json={
        "name": _uniq("Ivy"), "age": 22, "adult_verified": True, "synthetic_identity": True,
    })
    persona_id = create_resp.json()["id"]
    resp = await client.post(f"/api/v1/personas/{persona_id}/autopilot?mode=aggressive")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_workflow_list(client):
    resp = await client.get("/api/v1/workflows")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
