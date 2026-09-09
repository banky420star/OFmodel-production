"""Persona Studio — API auth gate tests.

Auth must be DISABLED when API_AUTH_TOKEN is empty (the autouse conftest
fixture forces this) and ENABLED when it is set.
"""

import pytest

from app.config import get_settings


@pytest.mark.asyncio
async def test_auth_disabled_when_token_empty(client):
    """Default/dev mode: no token configured, all /api/v1 routes open."""
    assert get_settings().API_AUTH_TOKEN == ""
    resp = await client.get("/api/v1/personas")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_enabled_missing_token_401(monkeypatch, client):
    monkeypatch.setenv("API_AUTH_TOKEN", "testtoken")
    get_settings.cache_clear()
    resp = await client.get("/api/v1/personas")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Unauthorized — check API token in Settings"


@pytest.mark.asyncio
async def test_auth_enabled_wrong_token_401(monkeypatch, client):
    monkeypatch.setenv("API_AUTH_TOKEN", "testtoken")
    get_settings.cache_clear()
    resp = await client.get(
        "/api/v1/personas", headers={"Authorization": "Bearer wrongtoken"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_enabled_valid_token_200(monkeypatch, client):
    monkeypatch.setenv("API_AUTH_TOKEN", "testtoken")
    get_settings.cache_clear()
    resp = await client.get(
        "/api/v1/personas", headers={"Authorization": "Bearer testtoken"}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_non_ascii_credentials_no_typeerror(monkeypatch):
    """Non-ASCII credentials (arrive latin-1-decoded via ASGI) must 401, not 500.

    Tested at the dependency level: HTTP clients refuse to send such headers,
    but a raw ASGI server would deliver them decoded.
    """
    from fastapi import HTTPException

    from app.auth import require_api_token

    monkeypatch.setenv("API_AUTH_TOKEN", "testtoken")
    get_settings.cache_clear()
    with pytest.raises(HTTPException) as exc:
        await require_api_token("Bearer tøken✓")
    assert exc.value.status_code == 401
    # And the reverse: non-ASCII configured token must also compare safely.
    monkeypatch.setenv("API_AUTH_TOKEN", "tøken✓")
    get_settings.cache_clear()
    with pytest.raises(HTTPException) as exc2:
        await require_api_token("Bearer testtoken")
    assert exc2.value.status_code == 401


@pytest.mark.asyncio
async def test_media_route_gated_with_token(monkeypatch, client):
    """Media routes accept Bearer header or ?token= query param when auth is on."""
    monkeypatch.setenv("API_AUTH_TOKEN", "testtoken")
    get_settings.cache_clear()
    # No token -> 401 (before the 404 path)
    assert (await client.get("/api/v1/avatars/whatever.jpg")).status_code == 401
    # Query param
    q = await client.get("/api/v1/avatars/whatever.jpg?token=testtoken")
    assert q.status_code == 404  # passed the gate, file simply missing
    # Bearer header
    b = await client.get(
        "/api/v1/avatars/whatever.jpg", headers={"Authorization": "Bearer testtoken"}
    )
    assert b.status_code == 404


@pytest.mark.asyncio
async def test_media_route_open_without_token(client):
    assert (await client.get("/api/v1/avatars/whatever.jpg")).status_code == 404