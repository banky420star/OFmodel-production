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
    assert data["overall"] in ("green", "yellow", "red")
    assert len(data["checks"]) > 0


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
    assert resp.status_code == 200
    data = resp.json()
    assert "Ava" in data["name"]
    assert data["age"] == 24
    assert data["status"] == "active"


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
        "name": "Summer Vibes",
        "theme": "beach",
        "image_count": 5,
    })
    assert resp.status_code == 200
    assert resp.json()["theme"] == "beach"


@pytest.mark.asyncio
async def test_create_and_assemble_content_pack(client):
    create_resp = await client.post("/api/v1/personas", json={
        "name": _uniq("Iris"), "age": 23, "adult_verified": True, "synthetic_identity": True,
    })
    persona_id = create_resp.json()["id"]

    # Create pack
    pack_resp = await client.post(f"/api/v1/personas/{persona_id}/packs", json={
        "name": "Sunday at Home",
        "platform": "instagram",
    })
    assert pack_resp.status_code == 200
    pack_id = pack_resp.json()["id"]

    # Assemble
    assemble_resp = await client.post(f"/api/v1/packs/{pack_id}/assemble")
    assert assemble_resp.status_code == 200


@pytest.mark.asyncio
async def test_generate_analytics(client):
    create_resp = await client.post("/api/v1/personas", json={
        "name": _uniq("Analytics_Test"), "age": 24, "adult_verified": True, "synthetic_identity": True,
    })
    persona_id = create_resp.json()["id"]

    resp = await client.post(f"/api/v1/personas/{persona_id}/analytics/generate")
    assert resp.status_code == 200
    assert resp.json()["days"] == 90

    # Get analytics
    resp = await client.get(f"/api/v1/personas/{persona_id}/analytics")
    assert resp.status_code == 200
    assert len(resp.json()) == 90


@pytest.mark.asyncio
async def test_generate_forecast(client):
    create_resp = await client.post("/api/v1/personas", json={
        "name": _uniq("Forecast_Test"), "age": 24, "adult_verified": True, "synthetic_identity": True,
    })
    persona_id = create_resp.json()["id"]

    resp = await client.post(f"/api/v1/personas/{persona_id}/forecasts/generate")
    assert resp.status_code == 200
    forecast_id = resp.json()["forecast_id"]

    resp = await client.get(f"/api/v1/personas/{persona_id}/forecasts")
    assert resp.status_code == 200
    forecasts = resp.json()
    assert len(forecasts) >= 1
    assert len(forecasts[0]["scenarios"]) == 3  # conservative, base, aggressive


@pytest.mark.asyncio
async def test_autopilot_toggle(client):
    create_resp = await client.post("/api/v1/personas", json={
        "name": _uniq("Autopilot_Test"), "age": 24, "adult_verified": True, "synthetic_identity": True,
    })
    persona_id = create_resp.json()["id"]

    resp = await client.post(f"/api/v1/personas/{persona_id}/autopilot?mode=on")
    assert resp.status_code == 200
    assert resp.json()["autopilot"] == "on"


@pytest.mark.asyncio
async def test_workflow_list(client):
    resp = await client.get("/api/v1/workflows")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
