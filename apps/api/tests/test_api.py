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
async def test_generate_analytics(client):
    create_resp = await client.post("/api/v1/personas", json={
        "name": _uniq("Zara"), "age": 24, "adult_verified": True, "synthetic_identity": True,
    })
    persona_id = create_resp.json()["id"]
    resp = await client.post(f"/api/v1/personas/{persona_id}/analytics/generate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "generated"
    assert data["days"] == 90


@pytest.mark.asyncio
async def test_generate_forecast(client):
    create_resp = await client.post("/api/v1/personas", json={
        "name": _uniq("Maya"), "age": 25, "adult_verified": True, "synthetic_identity": True,
    })
    persona_id = create_resp.json()["id"]
    # Generate analytics first (needed for forecast base)
    await client.post(f"/api/v1/personas/{persona_id}/analytics/generate")
    resp = await client.post(f"/api/v1/personas/{persona_id}/forecasts/generate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["horizon_months"] == 24
    assert len(data["scenarios"]) > 0


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
