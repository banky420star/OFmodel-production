"""Social worker tests: official-API-only integration, honest sync state.

Covers:
- capability matrix honesty (Fansly / OnlyFans excluded by policy)
- platform_client builds official clients only
- worker pass with no credentials → not_configured, marked honestly
- worker pass performs real syncs for accounts with valid credentials
  (HTTP mocked at the boundary); account row stats + last_sync_result persist
- 401 → account marked not connected with the real error preserved
- accounts on API-less platforms are never eligible
"""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.models import SocialAccount
from app.social_worker import worker_pass, platform_client
from app.providers.social_apis import PLATFORM_CAPABILITIES, FanvueClient, XClient

pytestmark = pytest.mark.asyncio


def _mk_account(platform: str, username: str, token: str = "") -> SocialAccount:
    return SocialAccount(
        persona_id="00000000-0000-0000-0000-000000000000",
        platform=platform,
        username=username,
        email=f"{username}@example.com",
        status="active",
        api_token=token,
    )


def _mock_http(monkeypatch, handler):
    """Route every httpx.AsyncClient the clients create through MockTransport."""
    class _Cx(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    import app.providers.social_apis as sa
    monkeypatch.setattr(sa.httpx, "AsyncClient", _Cx)


# ─── capability matrix invariants ──────────────────────────────────────


def test_capability_matrix_honest():
    assert PLATFORM_CAPABILITIES["fanvue"]["has_official_api"] is True
    assert PLATFORM_CAPABILITIES["twitter"]["has_official_api"] is True
    assert PLATFORM_CAPABILITIES["onlyfans"]["has_official_api"] is False
    assert PLATFORM_CAPABILITIES["fansly"]["has_official_api"] is False


def test_platform_client_refuses_api_less_platforms():
    assert platform_client(_mk_account("fansly", "x", token="whatever")) is None
    assert platform_client(_mk_account("onlyfans", "y", token="whatever")) is None


def test_platform_client_builds_official_clients():
    assert isinstance(platform_client(_mk_account("fanvue", "a", token="t")), FanvueClient)
    assert isinstance(platform_client(_mk_account("twitter", "b", token="t")), XClient)
    # gate-level platforms use a separate credential flow — no client yet
    assert platform_client(_mk_account("instagram", "c", token="t")) is None


async def test_fanvue_check_connected(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.fanvue.com"
        assert request.headers["X-Fanvue-API-Version"] == "2025-06-26"
        return httpx.Response(200, json={"data": []})

    _mock_http(monkeypatch, handler)
    status = await FanvueClient("good-token").check()
    assert status.ok is True
    assert status.status_kind == "CONNECTED"


async def test_fanvue_check_auth_failed_honest(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_token"})

    _mock_http(monkeypatch, handler)
    status = await FanvueClient("bad-token").check()
    assert status.ok is False
    assert status.status_kind == "AUTH_FAILED"
    assert "401" in status.detail


# ─── worker pass behaviour ─────────────────────────────────────────────


async def test_worker_pass_marks_missing_credentials_honestly(db):
    db.add(_mk_account("fanvue", "nocred"))
    await db.commit()

    summary = await worker_pass(db)
    assert summary["ran"] is True
    assert summary["succeeded"] == 0
    row = summary["results"][0]
    assert row["action"] == "not_configured"
    assert row["ok"] is False


def _result_for(summary: dict, username: str) -> dict | None:
    """The pass ledger can contain accounts from earlier tests (the test DB
    persists within a session) — always select THIS test's account."""
    for r in summary.get("results", []):
        if r.get("account") == username:
            return r
    return None


async def test_worker_pass_real_sync_with_valid_token(db, monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chats":
            return httpx.Response(200, json={
                "data": [], "pagination": {"page": 1, "size": 1, "hasMore": False}})
        return httpx.Response(404)

    _mock_http(monkeypatch, handler)

    db.add(_mk_account("fanvue", "goodtok", token="valid"))
    await db.commit()

    summary = await worker_pass(db)
    assert summary["ran"] is True
    row = _result_for(summary, "goodtok")
    assert row is not None and row["ok"] is True

    acct = (await db.execute(
        select(SocialAccount).where(SocialAccount.username == "goodtok")
    )).scalar_one()
    assert acct.api_connected is True
    assert acct.metadata_json["last_sync_result"]["ok"] is True


async def test_worker_pass_marks_auth_failure_honestly(db, monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_token"})

    _mock_http(monkeypatch, handler)

    db.add(_mk_account("fanvue", "badtok", token="invalid"))
    await db.commit()

    summary = await worker_pass(db)
    assert summary["ran"] is True
    row = _result_for(summary, "badtok")
    assert row is not None
    assert row["detail"].startswith("AUTH_FAILED")

    acct = (await db.execute(
        select(SocialAccount).where(SocialAccount.username == "badtok")
    )).scalar_one()
    assert acct.api_connected is False
    assert "401" in acct.metadata_json["last_sync_result"]["detail"]


async def test_worker_pass_never_touches_api_less_platforms(db):
    db.add(_mk_account("fansly", "noscraper", token="whatever"))
    await db.commit()

    summary = await worker_pass(db)
    # fansly has no official API → this account is never eligible.
    # (The DB persists across tests, so other accounts may legitimately run;
    # ours must simply never appear in the ledger.)
    assert _result_for(summary, "noscraper") is None
