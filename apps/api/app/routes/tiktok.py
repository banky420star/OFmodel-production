"""TikTok Login Kit OAuth — connect a persona's TikTok account.

Flow:
  1. GET  /tiktok/connect?account_id=<uuid>      → returns the authorize URL
  2. operator's browser approves on tiktok.com
  3. GET  /tiktok/callback?code=&state=          → stores tokens, marks connected
  4. GET  /social-accounts/{id}/tiktok/status    → live token check
  5. POST /social-accounts/{id}/tiktok/disconnect

The access token is per-account and stored on SocialAccount.api_token. Note
that this codebase has no encryption layer (no Fernet/keyring anywhere — see
the storage note in `_store_tokens`), so these are plaintext at rest exactly
like the mail.tm credentials already on that table.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AnalyticsSnapshot, SocialAccount
from app.providers.gates import require
from app.providers.registry import get_registry
from app.providers.tiktok import TikTokAPIClient, TikTokTokenExpired

router = APIRouter()

# Refresh this long before the real expiry so a sync never races the deadline.
_REFRESH_MARGIN = timedelta(minutes=5)

# Only ever offer read scopes. This integration does not post, follow, or like.
_SCOPES = ("user.info.basic", "user.info.profile", "user.info.stats", "video.list")


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _store_tokens(account: SocialAccount, tokens, scope: str) -> None:
    """Persist an OAuth token bundle onto the account.

    Plaintext, matching how this table already stores mail.tm credentials.
    `SocialAccount.api_token` is commented "(encrypted)" in models.py but no
    encryption exists in this codebase; adding one is a separate change.
    """
    now = datetime.now(timezone.utc)
    account.api_token = tokens.access_token
    account.api_connected = True
    account.metadata_json = {
        **(account.metadata_json or {}),
        "tiktok_refresh_token": tokens.refresh_token,
        "tiktok_open_id": tokens.open_id or account.metadata_json.get("tiktok_open_id", ""),
        "tiktok_scope": scope or tokens.scope,
        "tiktok_expires_at": (
            (now + timedelta(seconds=tokens.expires_in)).isoformat()
            if tokens.expires_in else ""
        ),
        "tiktok_refresh_expires_at": (
            (now + timedelta(seconds=tokens.refresh_expires_in)).isoformat()
            if tokens.refresh_expires_in else ""
        ),
        "tiktok_connected_at": now.isoformat(),
        "tiktok_nonce": "",  # consume the CSRF nonce
    }


async def account_api_client(
    account: SocialAccount, db: AsyncSession
) -> TikTokAPIClient:
    """Return a usable Display API client, refreshing the token if needed.

    Raises 409 when the account was never connected, 401 when the refresh token
    itself is dead (the operator must reconnect).
    """
    meta = account.metadata_json or {}
    if not account.api_token:
        raise HTTPException(
            409,
            f"@{account.username} has no TikTok authorization — connect it first",
        )

    expires_at = _parse_iso(meta.get("tiktok_expires_at", ""))
    if expires_at and datetime.now(timezone.utc) + _REFRESH_MARGIN >= expires_at:
        refresh_token = meta.get("tiktok_refresh_token", "")
        if not refresh_token:
            raise HTTPException(401, "TikTok token expired and no refresh token is stored")
        try:
            client = get_registry().resolve("tiktok")
            tokens = await client.refresh(refresh_token)
        except Exception as exc:
            account.api_connected = False
            await db.commit()
            raise HTTPException(401, f"TikTok token refresh failed — reconnect: {exc}")
        _store_tokens(account, tokens, meta.get("tiktok_scope", ""))
        await db.commit()

    return TikTokAPIClient(
        access_token=account.api_token,
        open_id=meta.get("tiktok_open_id", ""),
    )


@router.get("/tiktok/connect")
async def tiktok_connect(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Start the OAuth flow — returns the URL to send the operator's browser to."""
    # Fails closed with a 503 naming the missing env vars until the developer
    # app credentials exist.
    client = require("tiktok")["tiktok"]

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Social account not found")
    if account.platform != "tiktok":
        raise HTTPException(
            400, f"Account @{account.username} is on '{account.platform}', not tiktok"
        )

    # CSRF: TikTok echoes `state` back verbatim, so carry the account id in it
    # and pin the random half on the row to compare against on return.
    nonce = secrets.token_urlsafe(24)
    account.metadata_json = {**(account.metadata_json or {}), "tiktok_nonce": nonce}
    await db.commit()

    return {
        "authorize_url": client.authorize_url(state=f"{account.id}.{nonce}", scopes=_SCOPES),
        "account_id": str(account.id),
        "username": account.username,
    }


@router.get("/tiktok/callback", response_class=HTMLResponse)
async def tiktok_callback(
    code: str = Query(""),
    state: str = Query(""),
    error: str = Query(""),
    error_description: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    """TikTok redirects here after the operator approves (or refuses)."""
    if error:
        return HTMLResponse(
            _page("Authorization declined", error_description or error, ok=False),
            status_code=400,
        )

    if "." not in state:
        return HTMLResponse(_page("Invalid state", "Malformed OAuth state.", ok=False), status_code=400)
    account_id, _, nonce = state.partition(".")

    account = await db.get(SocialAccount, account_id)
    if not account:
        return HTMLResponse(_page("Unknown account", "No matching social account.", ok=False), status_code=404)

    stored_nonce = (account.metadata_json or {}).get("tiktok_nonce", "")
    if not stored_nonce or not secrets.compare_digest(stored_nonce, nonce):
        return HTMLResponse(
            _page(
                "State check failed",
                "This link is stale or was not started from Persona Studio. Start the "
                "connection again from the Socials page.",
                ok=False,
            ),
            status_code=400,
        )

    client = get_registry().resolve("tiktok")
    try:
        tokens = await client.exchange_code(code)
    except Exception as exc:
        return HTMLResponse(_page("Token exchange failed", str(exc), ok=False), status_code=502)

    _store_tokens(account, tokens, ",".join(_SCOPES))

    # Pull the profile immediately so the operator sees who they connected.
    display = account.username
    followers = 0
    try:
        api = TikTokAPIClient(access_token=tokens.access_token, open_id=tokens.open_id)
        profile = await api.get_profile()
        display = profile.display_name or display
        followers = profile.follower_count
        account.display_name = profile.display_name or account.display_name
        account.followers = profile.follower_count
        if profile.avatar_url:
            account.profile_image_url = profile.avatar_url
        if tokens.open_id:
            account.profile_url = f"https://www.tiktok.com/@{profile.display_name.lstrip('@')}" if profile.display_name else account.profile_url
    except Exception as exc:
        # The token is stored and the account is connected; a profile read
        # failure is worth reporting but not worth discarding the grant.
        display = f"{display} (profile read failed: {str(exc)[:80]})"

    await db.commit()

    return HTMLResponse(
        _page(
            "TikTok connected",
            f"@{display} — {followers:,} followers. You can close this window and "
            "return to Persona Studio.",
            ok=True,
        )
    )


@router.get("/social-accounts/{account_id}/tiktok/status")
async def tiktok_status(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Live check that the stored token still reads the connected account."""
    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Social account not found")

    meta = account.metadata_json or {}
    base = {
        "account_id": str(account.id),
        "username": account.username,
        "api_connected": bool(account.api_connected),
        "connected_at": meta.get("tiktok_connected_at", ""),
        "scope": meta.get("tiktok_scope", ""),
        "expires_at": meta.get("tiktok_expires_at", ""),
    }
    if not account.api_token:
        return {**base, "ok": False, "detail": "Not connected — no TikTok authorization stored"}

    try:
        api = await account_api_client(account, db)
        ok, name, followers = await api.health_check()
        return {**base, "ok": ok, "display_name": name, "followers": followers}
    except TikTokTokenExpired as exc:
        return {**base, "api_connected": False, "ok": False, "detail": str(exc)}
    except HTTPException as exc:
        return {**base, "api_connected": False, "ok": False, "detail": exc.detail}


@router.post("/social-accounts/{account_id}/tiktok/sync")
async def tiktok_sync_analytics(
    account_id: UUID,
    video_limit: int = Query(20, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """Pull the connected account's real TikTok stats into AnalyticsSnapshot.

    Note this is keyed on the social account, not the persona: unlike Instagram
    (one installation-wide token), each TikTok account carries its own grant.
    """
    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Social account not found")
    if account.platform != "tiktok":
        raise HTTPException(400, f"Account @{account.username} is not a tiktok account")

    api = await account_api_client(account, db)
    try:
        analytics = await api.sync_analytics(video_limit=video_limit)
    except TikTokTokenExpired as exc:
        account.api_connected = False
        await db.commit()
        raise HTTPException(401, f"TikTok authorization is no longer valid: {exc}")
    except Exception as exc:
        raise HTTPException(502, f"TikTok API error: {exc}")

    now = datetime.now(timezone.utc)
    snap = AnalyticsSnapshot(
        id=uuid4(),
        persona_id=account.persona_id,
        snapshot_date=now,
        platform="tiktok",
        followers=analytics.profile.follower_count,
        likes=analytics.total_likes,
        comments=analytics.total_comments,
        shares=analytics.total_shares,
        views=analytics.total_views,
        engagement_rate=analytics.avg_engagement_rate / 100,
        revenue=0,  # not available from the Display API
        costs=0,
        metadata_json={
            "source": "tiktok",
            "social_account_id": str(account.id),
            "open_id": analytics.profile.open_id,
            "video_count": analytics.profile.video_count,
            "following_count": analytics.profile.following_count,
            "likes_received_total": analytics.profile.likes_count,
        },
    )
    db.add(snap)

    # Keep the account row's own counters in step with the snapshot.
    account.followers = analytics.profile.follower_count
    account.posts_count = analytics.profile.video_count or account.posts_count
    await db.commit()

    return {
        "status": "synced",
        "source": "tiktok",
        "synced_at": analytics.synced_at,
        "profile": {
            "display_name": analytics.profile.display_name,
            "followers": analytics.profile.follower_count,
            "following": analytics.profile.following_count,
            "video_count": analytics.profile.video_count,
            "likes_received": analytics.profile.likes_count,
        },
        "metrics": {
            "total_views": analytics.total_views,
            "total_likes": analytics.total_likes,
            "total_comments": analytics.total_comments,
            "total_shares": analytics.total_shares,
            "total_engagement": analytics.total_engagement,
            "engagement_rate": analytics.avg_engagement_rate,
        },
        "recent_videos": [
            {
                "id": v.video_id,
                "title": v.title or v.description,
                "created_at": v.created_at,
                "share_url": v.share_url,
                "views": v.view_count,
                "likes": v.like_count,
                "comments": v.comment_count,
                "shares": v.share_count,
            }
            for v in analytics.recent_videos[:10]
        ],
    }


@router.post("/social-accounts/{account_id}/tiktok/disconnect")
async def tiktok_disconnect(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Drop the stored grant. TikTok has no revoke endpoint, so this is local."""
    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Social account not found")

    meta = dict(account.metadata_json or {})
    for key in (
        "tiktok_refresh_token", "tiktok_open_id", "tiktok_scope",
        "tiktok_expires_at", "tiktok_refresh_expires_at", "tiktok_connected_at",
        "tiktok_nonce",
    ):
        meta.pop(key, None)
    account.metadata_json = meta
    account.api_token = ""
    account.api_connected = False
    await db.commit()

    return {"status": "disconnected", "account_id": str(account.id)}


def _page(title: str, message: str, ok: bool) -> str:
    """Minimal self-contained result page for the OAuth redirect."""
    accent = "#d9fb71" if ok else "#ef4444"
    icon = "✓" if ok else "✕"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body style="margin:0;background:#0b0b0d;color:#e8e8ea;font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:grid;place-items:center;min-height:100vh">
  <div style="max-width:460px;padding:32px;text-align:center">
    <div style="width:48px;height:48px;border-radius:50%;background:{accent}1f;color:{accent};display:grid;place-items:center;font-size:24px;margin:0 auto 16px">{icon}</div>
    <h1 style="font-size:20px;margin:0 0 8px;font-weight:600">{title}</h1>
    <p style="margin:0;color:#9a9aa2">{message}</p>
  </div>
</body></html>"""
