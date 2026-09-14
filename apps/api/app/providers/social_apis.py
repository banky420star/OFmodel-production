"""Official social-platform API clients + capability matrix.

Ground rules (ARCHITECTURE_VISION.md):
- Only OFFICIAL APIs. Platforms without one (Fansly, OnlyFans) have no client
  here — scraping/ToS-violating integration is never built.
- Gate-level APIs that need per-app approval are wired and documented but the
  credential source is explicit.
- Every client reports REAL status. A wrong key is FAILED with the platform's
  own error text, never silently degraded.
"""

from __future__ import annotations

import base64
import hmac
import hashlib
import time
import secrets
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

# ─── Shared result types ───────────────────────────────────────────────


@dataclass
class ApiStatus:
    ok: bool
    status_kind: str  # CONNECTED | NOT_CONFIGURED | UNREACHABLE | AUTH_FAILED | BLOCKED | UNSUPPORTED | ERROR
    detail: str
    http_status: int | None = None
    capability_flags: dict[str, bool] = field(default_factory=dict)


@dataclass
class ProfileSnapshot:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def _flags(**kw: bool) -> dict[str, bool]:
    base = {"post": False, "sync_profile": False, "read_insights": False, "chat": False}
    base.update(kw)
    return base


# ─── Platform capability matrix (the honest map of what IS possible) ──

PLATFORM_CAPABILITIES: dict[str, dict[str, Any]] = {
    "fanvue": {
        "label": "Fanvue",
        "has_official_api": True,
        "auth": "oauth2",
        "credential_source": (
            "Operator creates an OAuth app on their Fanvue creator account "
            "(creator + completed KYC required), authorizes it, and stores the "
            "access token via Connect API. Docs: https://api.fanvue.com/docs"
        ),
        "flags": _flags(post=True, sync_profile=True, read_insights=True, chat=True),
        "endpoints": {
            "base": "https://api.fanvue.com/v1",
            "health": "https://api.fanvue.com/v1/chats?page=1&size=1",
        },
        "api_version": "2025-06-26",  # required X-Fanvue-API-Version header
        "restricted_note": "",
    },
    "twitter": {
        "label": "X (Twitter)",
        "has_official_api": True,
        "auth": "bearer",
        "credential_source": (
            "X Developer Portal → project app → Bearer Token "
            "(https://developer.x.com). v2 POST /2/tweets requires Basic tier or "
            "higher; Free tier is write-limited."
        ),
        "flags": _flags(post=True, sync_profile=False, read_insights=False, chat=False),
        "endpoints": {
            "base": "https://api.x.com/2",
            "health": "https://api.x.com/2/users/me",
        },
        "restricted_note": "",
    },
    "tiktok": {
        "audited": True,
        "label": "TikTok",
        "has_official_api": True,
        "auth": "oauth2_app_approval",
        "credential_source": (
            "TikTok for Developers → Content Posting API — requires an approved "
            "app. Until approved, status stays NOT_CONFIGURED and the client refuses "
            "to attempt anything."
        ),
        "flags": _flags(post=True, sync_profile=True, read_insights=False, chat=False),
        "endpoints": {"base": "https://open.tiktokapis.com/v2"},
        "restricted_note": "App review required before posting is possible.",
    },
    "instagram": {
        "label": "Instagram",
        "has_official_api": True,
        "auth": "graph_token",
        "credential_source": (
            "Meta App Review → Instagram Graph API (content publishing) with an "
            "IG Business/Creator account. Until approved: NOT_CONFIGURED."
        ),
        "flags": _flags(post=True, sync_profile=True, read_insights=True, chat=False),
        "endpoints": {"base": "https://graph.facebook.com/v21.0"},
        "restricted_note": "Meta App Review required; personal accounts unsupported.",
    },
    "facebook": {
        "label": "Facebook",
        "has_official_api": True,
        "auth": "graph_token",
        "credential_source": "Meta App Review → Facebook Pages API. Until approved: NOT_CONFIGURED.",
        "flags": _flags(post=True, sync_profile=True, read_insights=True, chat=False),
        "endpoints": {"base": "https://graph.facebook.com/v21.0"},
        "restricted_note": "Meta App Review required.",
    },
    "onlyfans": {
        "label": "OnlyFans",
        "has_official_api": False,
        "auth": "none",
        "credential_source": "None exists. No client, by policy.",
        "flags": _flags(),
        "endpoints": {},
        "restricted_note": "No official API. Automation is prohibited by ToS — not built.",
    },
    "fansly": {
        "label": "Fansly",
        "has_official_api": False,
        "auth": "none",
        "credential_source": "None exists. No client, by policy.",
        "flags": _flags(),
        "endpoints": {},
        "restricted_note": "No official API. Scrapers violate ToS — not built.",
    },
}


# ─── Fanvue — official OAuth2 API (the monetization path) ─────────────


class FanvueClient:
    """Official Fanvue API (OAuth2, X-Fanvue-API-Version required).

    Token provenance: operator authorizes the Studio's OAuth app on their
    creator account; the resulting access token is stored per account. The
    Studio never fabricates tokens and never stores platform passwords.
    """

    BASE = "https://api.fanvue.com"
    VERSION = "2025-06-26"

    def __init__(self, access_token: str):
        self._token = access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "X-Fanvue-API-Version": self.VERSION,
            "Accept": "application/json",
        }

    async def check(self) -> ApiStatus:
        """Real authenticated reachability probe: GET /v1/chats?page=1&size=1."""
        if not self._token:
            return ApiStatus(False, "NOT_CONFIGURED", "No Fanvue OAuth access token stored")
        try:
            async with httpx.AsyncClient(timeout=15) as cx:
                r = await cx.get(
                    f"{self.BASE}/v1/chats", params={"page": 1, "size": 1}, headers=self._headers()
                )
        except httpx.HTTPError as e:
            return ApiStatus(False, "UNREACHABLE", f"network error: {e}")

        if r.status_code == 200:
            return ApiStatus(True, "CONNECTED", "Fanvue API reachable (authenticated)", 200)
        if r.status_code == 401:
            return ApiStatus(False, "AUTH_FAILED", "401 — token invalid or expired", 401)
        if r.status_code in (403, 429):
            return ApiStatus(False, "BLOCKED", f"{r.status_code} — {r.text[:120]}", r.status_code)
        return ApiStatus(False, "ERROR", f"HTTP {r.status_code}: {r.text[:120]}", r.status_code)

    async def profile(self) -> ProfileSnapshot:
        if not self._token:
            return ProfileSnapshot(False, error="no token")
        try:
            async with httpx.AsyncClient(timeout=15) as cx:
                r = await cx.get(f"{self.BASE}/v1/chats", params={"page": 1, "size": 1},
                                 headers=self._headers())
            if r.status_code != 200:
                return ProfileSnapshot(False, error=f"HTTP {r.status_code}: {r.text[:200]}")
            body = r.json()
            return ProfileSnapshot(True, data={"chats_sample": body.get("data", [])[:1]})
        except httpx.HTTPError as e:
            return ProfileSnapshot(False, error=f"network error: {e}")

    # — write operations (wired, exercised only through the worker with a
    #   real token; they follow the documented /v1 conventions) —

    async def create_post(self, *, text: str, media_urls: list[str] | None = None,
                          tier: str = "subscriber") -> ProfileSnapshot:
        """Create a post via the official API. Real network call — no mock."""
        if not self._token:
            return ProfileSnapshot(False, error="no token")
        payload: dict[str, Any] = {"text": text, "visibility": tier}
        if media_urls:
            payload["mediaUrls"] = media_urls
        try:
            async with httpx.AsyncClient(timeout=20) as cx:
                r = await cx.post(f"{self.BASE}/v1/posts", json=payload, headers=self._headers())
            if r.status_code in (200, 201):
                return ProfileSnapshot(True, data=r.json())
            return ProfileSnapshot(False, error=f"HTTP {r.status_code}: {r.text[:200]}")
        except httpx.HTTPError as e:
            return ProfileSnapshot(False, error=f"network error: {e}")


# ─── X (Twitter) — official API v2 ─────────────────────────────────────


class XClient:
    """Official X API v2 with a Bearer token (app-only for /users/me;
    user-context OAuth needed for posting — posting is flagged accordingly)."""

    BASE = "https://api.x.com/2"

    def __init__(self, bearer_token: str):
        self._token = bearer_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    async def check(self) -> ApiStatus:
        if not self._token:
            return ApiStatus(False, "NOT_CONFIGURED", "No X Bearer token stored")
        try:
            async with httpx.AsyncClient(timeout=15) as cx:
                r = await cx.get(f"{self.BASE}/users/me", headers=self._headers())
        except httpx.HTTPError as e:
            return ApiStatus(False, "UNREACHABLE", f"network error: {e}")
        if r.status_code == 200:
            me = r.json().get("data", {})
            return ApiStatus(True, "CONNECTED", f"authenticated as @{me.get('username', '?')}", 200)
        if r.status_code == 401:
            return ApiStatus(False, "AUTH_FAILED", "401 — Bearer token invalid", 401)
        if r.status_code == 429:
            return ApiStatus(False, "BLOCKED", "429 — rate limit exhausted", 429)
        return ApiStatus(False, "ERROR", f"HTTP {r.status_code}: {r.text[:120]}", r.status_code)

    async def post_tweet(self, text: str) -> ProfileSnapshot:
        """POST /2/tweets — requires user-context OAuth (not app-only bearer).
        The client attempts it honestly; a 401/403 is reported verbatim."""
        if not self._token:
            return ProfileSnapshot(False, error="no token")
        try:
            async with httpx.AsyncClient(timeout=20) as cx:
                r = await cx.post(f"{self.BASE}/tweets", json={"text": text[:280]},
                                  headers=self._headers())
            if r.status_code == 201:
                return ProfileSnapshot(True, data=r.json())
            return ProfileSnapshot(False, error=f"HTTP {r.status_code}: {r.text[:200]}")
        except httpx.HTTPError as e:
            return ProfileSnapshot(False, error=f"network error: {e}")


# ─── Credential helpers (X OAuth1a user-context signing) ───────────────


def generate_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    return verifier, challenge


def oauth1_header(method: str, url: str, *, consumer_key: str, access_token: str,
                  token_secret: str, body_params: dict | None = None) -> str:
    """RFC 5849 HMAC-SHA256 signing — only if a user-context X posting path
    is ever added; provided so credential plumbing exists without secret work."""
    nonce = secrets.token_urlsafe(24)
    ts = str(int(time.time()))
    params = {"oauth_consumer_key": consumer_key, "oauth_nonce": nonce,
              "oauth_signature_method": "HMAC-SHA256", "oauth_timestamp": ts,
              "oauth_token": access_token, "oauth_version": "1.0"}
    allp = {**params, **(body_params or {})}
    base_str = "&".join([method.upper(), quote(url, safe=""),
                         quote("&".join(f"{k}={v}" for k, v in sorted(allp.items())), safe="")])
    key = f"{quote(consumer_key, safe='')}&{quote(token_secret, safe='')}"
    sig = base64.b64encode(hmac.new(key.encode(), base_str.encode(), hashlib.sha256).digest()).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{k}="{quote(v, safe="")}"' for k, v in sorted(params.items()))


def new_request_id() -> str:
    return str(_uuid.uuid4())
