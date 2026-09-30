"""TikTok Login Kit + Display API integration tests.

Everything here runs offline: the provider's httpx client is replaced with a
stub, so no request ever leaves the process. The point is to pin the OAuth
token lifecycle, the error-code handling (TikTok reports failures with HTTP
200), the read-only scope set, and the honest-503 behaviour when the developer
app credentials are absent.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.config import get_settings
from app.models import AnalyticsSnapshot, Persona, SocialAccount, TeamInvite
from app.providers.registry import ProviderNotConfigured, get_registry
from app.providers.tiktok import (
    DEFAULT_SCOPES,
    TikTokAPIClient,
    TikTokAuthError,
    TikTokClient,
    TikTokTokenExpired,
)


# ── httpx stubs ───────────────────────────────────────────────────────

class _Resp:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _StubClient:
    """Stands in for httpx.AsyncClient — records calls, returns fixed bodies."""

    def __init__(self, post_resp=None, get_resp=None):
        self._post_resp = post_resp
        self._get_resp = get_resp
        self.calls: list[tuple] = []

    async def post(self, url, **kw):
        self.calls.append(("post", url, kw))
        return self._post_resp

    async def get(self, url, **kw):
        self.calls.append(("get", url, kw))
        return self._get_resp


def _app_client(post=None, get=None) -> TikTokClient:
    c = TikTokClient("key123", "secret456", "https://example.invalid/callback")
    c._client = _StubClient(post, get)
    return c


def _api_client(post=None, get=None) -> TikTokAPIClient:
    c = TikTokAPIClient(access_token="act.token", open_id="open-1")
    c._client = _StubClient(post, get)
    return c


_TOKEN_OK = {
    "access_token": "act.token",
    "refresh_token": "rft.token",
    "open_id": "open-1",
    "scope": "user.info.basic,video.list",
    "expires_in": 86400,
    "refresh_expires_in": 31536000,
    "token_type": "Bearer",
}


# ── OAuth: authorize URL + code exchange ──────────────────────────────

def test_authorize_url_carries_client_scope_redirect_and_state():
    url = _app_client().authorize_url(state="acct.nonce")
    assert url.startswith("https://www.tiktok.com/v2/auth/authorize/?")
    assert "client_key=key123" in url
    assert "response_type=code" in url
    assert "state=acct.nonce" in url
    assert "redirect_uri=https%3A%2F%2Fexample.invalid%2Fcallback" in url
    assert "video.list" in url


def test_scopes_are_read_only():
    """No publishing scope may ever be requested by this integration."""
    assert "video.list" in DEFAULT_SCOPES
    assert "user.info.basic" in DEFAULT_SCOPES
    for forbidden in ("video.publish", "video.upload", "user.manage"):
        assert forbidden not in DEFAULT_SCOPES


async def test_exchange_code_returns_tokens():
    client = _app_client(post=_Resp(_TOKEN_OK))
    tokens = await client.exchange_code("one-time-code")
    assert tokens.access_token == "act.token"
    assert tokens.refresh_token == "rft.token"
    assert tokens.open_id == "open-1"
    assert tokens.expires_in == 86400
    assert tokens.obtained_at  # stamped

    _, url, kw = client._client.calls[0]
    assert url.endswith("/v2/oauth/token/")
    assert kw["data"]["grant_type"] == "authorization_code"
    assert kw["data"]["code"] == "one-time-code"
    assert kw["data"]["client_secret"] == "secret456"


async def test_token_endpoint_reports_failure_with_http_200():
    """TikTok's OAuth errors arrive as HTTP 200 + error body, not 4xx."""
    client = _app_client(post=_Resp(
        {"error": "invalid_grant", "error_description": "authorization code expired"},
        status_code=200,
    ))
    with pytest.raises(TikTokAuthError, match="authorization code expired"):
        await client.exchange_code("stale")


async def test_refresh_returns_rotated_tokens():
    client = _app_client(post=_Resp({**_TOKEN_OK, "refresh_token": "rft.rotated"}))
    tokens = await client.refresh("rft.old")
    assert tokens.refresh_token == "rft.rotated"
    _, _, kw = client._client.calls[0]
    assert kw["data"]["grant_type"] == "refresh_token"


# ── Display API reads ─────────────────────────────────────────────────

async def test_get_profile_parses_counts():
    client = _api_client(get=_Resp({"data": {"user": {
        "open_id": "open-1", "display_name": "Test Persona", "avatar_url": "https://a/x.png",
        "follower_count": 4321, "following_count": 12, "likes_count": 9876, "video_count": 43,
    }}}))
    profile = await client.get_profile()
    assert profile.display_name == "Test Persona"
    assert (profile.follower_count, profile.following_count) == (4321, 12)
    assert profile.likes_count == 9876

    _, url, kw = client._client.calls[0]
    assert url.endswith("/v2/user/info/")
    assert kw["headers"]["Authorization"] == "Bearer act.token"


async def test_http_401_raises_token_expired():
    client = _api_client(get=_Resp({"error": {"code": "access_token_expired"}}, status_code=401))
    with pytest.raises(TikTokTokenExpired):
        await client.get_profile()


async def test_error_code_in_200_body_raises_token_expired():
    client = _api_client(get=_Resp({"error": {"code": "access_token_invalid", "message": "bad"}}))
    with pytest.raises(TikTokTokenExpired):
        await client.get_profile()


async def test_unknown_error_code_raises_auth_error():
    client = _api_client(get=_Resp({"error": {"code": "rate_limit_exceeded", "message": "slow down"}}))
    with pytest.raises(TikTokAuthError, match="rate_limit_exceeded"):
        await client.get_profile()


async def test_recent_videos_parses_and_converts_create_time():
    client = _api_client(post=_Resp({"data": {"videos": [{
        "id": "v1", "title": "first", "video_description": "desc",
        "create_time": 1767225600, "share_url": "https://t/v1", "duration": 15,
        "view_count": 1000, "like_count": 100, "comment_count": 10, "share_count": 5,
    }]}}))
    videos = await client.get_recent_videos(limit=5)
    assert len(videos) == 1
    assert videos[0].video_id == "v1"
    assert videos[0].view_count == 1000
    assert videos[0].created_at.startswith("2026-01-01")

    _, url, kw = client._client.calls[0]
    assert url.endswith("/v2/video/list/")
    assert kw["json"] == {"max_count": 5}


async def test_missing_video_scope_names_the_scope():
    client = _api_client(post=_Resp({"error": {"code": "scope_not_authorized"}}))
    with pytest.raises(TikTokAuthError, match="video.list"):
        await client.get_recent_videos()


async def test_sync_analytics_aggregates():
    client = _api_client()
    client._client = _StubClient(
        post_resp=_Resp({"data": {"videos": [
            {"id": "a", "view_count": 100, "like_count": 10, "comment_count": 2, "share_count": 1},
            {"id": "b", "view_count": 300, "like_count": 30, "comment_count": 4, "share_count": 5},
        ]}}),
        get_resp=_Resp({"data": {"user": {
            "open_id": "open-1", "display_name": "P",
            "follower_count": 100, "following_count": 0,
            "likes_count": 0, "video_count": 2,
        }}}),
    )
    stats = await client.sync_analytics()
    assert stats.total_views == 400
    assert stats.total_likes == 40
    assert stats.total_comments == 6
    assert stats.total_shares == 6
    assert stats.total_engagement == 52
    # (52 / 2 videos / 100 followers) * 100 = 26.0
    assert stats.avg_engagement_rate == 26.0
    assert stats.synced_at


async def test_health_check_reports_failure_without_raising():
    client = _api_client(get=_Resp({"error": {"code": "access_token_invalid"}}, status_code=401))
    ok, detail, followers = await client.health_check()
    assert ok is False
    assert followers == 0
    assert "expired" in detail.lower() or "revoked" in detail.lower()


# ── registry / gates wiring ───────────────────────────────────────────

def _clear_tiktok(monkeypatch, *, configured: bool):
    s = get_settings()
    monkeypatch.setattr(s, "TIKTOK_CLIENT_KEY", "k" if configured else "")
    monkeypatch.setattr(s, "TIKTOK_CLIENT_SECRET", "s" if configured else "")
    monkeypatch.setattr(s, "TIKTOK_REDIRECT_URI", "https://x/cb" if configured else "")
    get_registry()._instances.pop("tiktok", None)


def test_tiktok_capability_is_yellow_without_credentials(monkeypatch):
    _clear_tiktok(monkeypatch, configured=False)
    with pytest.raises(ProviderNotConfigured) as exc:
        get_registry().resolve("tiktok")
    assert "TIKTOK_CLIENT_KEY" in str(exc.value)
    # The old _require_env text hardcoded IMAGE_PROVIDER for every capability.
    assert "IMAGE_PROVIDER" not in str(exc.value)


def test_tiktok_capability_is_green_with_credentials(monkeypatch):
    _clear_tiktok(monkeypatch, configured=True)
    assert isinstance(get_registry().resolve("tiktok"), TikTokClient)


def test_health_report_lists_tiktok_with_env_hint(monkeypatch):
    _clear_tiktok(monkeypatch, configured=False)
    row = get_registry().health_report()["tiktok"]
    assert row["status"] == "yellow"
    assert row["configured"] is False
    assert "TIKTOK_CLIENT_KEY" in row["env_hint"]


def test_tiktok_is_optional_not_required():
    assert "tiktok" not in get_registry().required_capabilities()
    assert "instagram" not in get_registry().required_capabilities()


def test_gate_hint_names_tiktok_vars_not_a_fake_selector(monkeypatch):
    """tiktok has no <CAP>_PROVIDER setting — the hint must not invent one."""
    _clear_tiktok(monkeypatch, configured=False)
    from app.providers.gates import _fix_hint

    hint = _fix_hint("tiktok")
    assert "TIKTOK_CLIENT_KEY" in hint
    assert "TIKTOK_PROVIDER" not in hint


# ── endpoints ─────────────────────────────────────────────────────────

async def test_connect_endpoint_fails_closed_without_app_credentials(monkeypatch, client):
    _clear_tiktok(monkeypatch, configured=False)
    resp = await client.get(f"/api/v1/tiktok/connect?account_id={uuid4()}")
    assert resp.status_code == 503
    assert "TIKTOK_CLIENT_KEY" in resp.json()["detail"]


async def test_callback_rejects_malformed_state(client):
    resp = await client.get("/api/v1/tiktok/callback?code=x&state=no-dot-here")
    assert resp.status_code == 400
    assert "state" in resp.text.lower()


async def test_callback_rejects_unknown_account(client):
    resp = await client.get(f"/api/v1/tiktok/callback?code=x&state={uuid4()}.nonce")
    assert resp.status_code == 404


async def test_callback_reports_operator_declining(client):
    resp = await client.get("/api/v1/tiktok/callback?error=access_denied&error_description=nope")
    assert resp.status_code == 400
    assert "nope" in resp.text


async def test_callback_rejects_mismatched_nonce(client, db):
    """A stale or forged state must not be exchangeable."""
    persona = Persona(id=uuid4(), name=f"tt-nonce-{uuid4().hex[:6]}")
    db.add(persona)
    account = SocialAccount(
        id=str(uuid4()), persona_id=str(persona.id), platform="tiktok",
        username="persona_tt", status="active",
        metadata_json={"tiktok_nonce": "the-real-nonce"},
    )
    db.add(account)
    await db.commit()

    resp = await client.get(f"/api/v1/tiktok/callback?code=x&state={account.id}.wrong-nonce")
    assert resp.status_code == 400
    assert "state" in resp.text.lower()


async def test_status_endpoint_reports_unconnected_account(client, db):
    persona = Persona(id=uuid4(), name=f"tt-stat-{uuid4().hex[:6]}")
    db.add(persona)
    account = SocialAccount(
        id=str(uuid4()), persona_id=str(persona.id), platform="tiktok",
        username="persona_tt_status", status="active",
    )
    db.add(account)
    await db.commit()

    resp = await client.get(f"/api/v1/social-accounts/{account.id}/tiktok/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["api_connected"] is False
    assert body["ok"] is False
    assert "not connected" in body["detail"].lower()


async def test_sync_endpoint_refuses_account_without_grant(client, db):
    persona = Persona(id=uuid4(), name=f"tt-sync-{uuid4().hex[:6]}")
    db.add(persona)
    account = SocialAccount(
        id=str(uuid4()), persona_id=str(persona.id), platform="tiktok",
        username="persona_tt_sync", status="active",
    )
    db.add(account)
    await db.commit()

    resp = await client.post(f"/api/v1/social-accounts/{account.id}/tiktok/sync")
    assert resp.status_code == 409
    assert "connect it first" in resp.json()["detail"]


async def test_sync_endpoint_rejects_non_tiktok_account(client, db):
    persona = Persona(id=uuid4(), name=f"tt-wrong-{uuid4().hex[:6]}")
    db.add(persona)
    account = SocialAccount(
        id=str(uuid4()), persona_id=str(persona.id), platform="instagram",
        username="persona_ig", status="active",
    )
    db.add(account)
    await db.commit()

    resp = await client.post(f"/api/v1/social-accounts/{account.id}/tiktok/sync")
    assert resp.status_code == 400


# ── Instagram regression ──────────────────────────────────────────────

async def test_instagram_sync_reaches_provider_not_a_dict(client, db):
    """Regression: require() returns {capability: instance}. The route used to
    call .sync_analytics() on that dict and swallow the AttributeError into a
    502, so Instagram sync could never succeed even with valid credentials."""
    from tests.fakes import FakeInstagramProvider

    registry = get_registry()
    registry.force_override("instagram", FakeInstagramProvider(followers=1234))
    try:
        persona = Persona(id=uuid4(), name=f"ig-reg-{uuid4().hex[:6]}")
        db.add(persona)
        await db.commit()

        resp = await client.post(f"/api/v1/personas/{persona.id}/analytics/sync")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source"] == "instagram"
        assert body["profile"]["followers"] == 1234
        assert body["metrics"]["total_likes"] == 10
    finally:
        registry._overrides.pop("instagram", None)


# ── team invites ──────────────────────────────────────────────────────

async def test_invite_lifecycle(client):
    created = await client.post(
        "/api/v1/team/invites",
        params={"email": "mate@example.com", "role": "operator", "note": "hi"},
    )
    assert created.status_code == 200, created.text
    invite = created.json()
    token = invite["token"]
    assert invite["status"] == "pending"
    # The honest flag — this build has no login, so nothing is gated.
    assert invite["enforced"] is False
    assert invite["accept_url"].endswith(f"/invite/{token}")

    fetched = await client.get(f"/api/v1/team/invites/{token}")
    assert fetched.status_code == 200
    assert fetched.json()["role"] == "operator"

    accepted = await client.post(f"/api/v1/team/invites/{token}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["accepted_at"]

    again = await client.post(f"/api/v1/team/invites/{token}/accept")
    assert again.status_code == 200
    assert "already accepted" in again.json()["message"]


async def test_invite_rejects_unknown_token(client):
    assert (await client.get("/api/v1/team/invites/not-a-real-token")).status_code == 404


async def test_invite_rejects_invalid_role(client):
    resp = await client.post(
        "/api/v1/team/invites", params={"email": "x@example.com", "role": "superuser"}
    )
    assert resp.status_code == 400
    assert "role must be one of" in resp.json()["detail"]


async def test_revoked_invite_cannot_be_accepted(client, db):
    created = await client.post("/api/v1/team/invites", params={"email": "rev@example.com"})
    invite = created.json()

    revoked = await client.post(f"/api/v1/team/invites/{invite['id']}/revoke")
    assert revoked.status_code == 200

    blocked = await client.post(f"/api/v1/team/invites/{invite['token']}/accept")
    assert blocked.status_code == 409
    assert "revoked" in blocked.json()["detail"].lower()


async def test_expired_invite_cannot_be_accepted(client, db):
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select

    created = await client.post("/api/v1/team/invites", params={"email": "old@example.com"})
    token = created.json()["token"]

    result = await db.execute(select(TeamInvite).where(TeamInvite.token == token))
    row = result.scalar_one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db.commit()

    resp = await client.post(f"/api/v1/team/invites/{token}/accept")
    assert resp.status_code == 410
    assert "expired" in resp.json()["detail"].lower()


async def test_invite_list_includes_created(client):
    await client.post("/api/v1/team/invites", params={"email": "listed@example.com"})
    resp = await client.get("/api/v1/team/invites")
    assert resp.status_code == 200
    assert any(i["email"] == "listed@example.com" for i in resp.json())
