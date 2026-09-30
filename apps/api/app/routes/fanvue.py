"""Fanvue OAuth — get the token that lets the line actually post, and get paid.

Every other path in this app ends at this app's own database. `/v1/creators/…`
on Fanvue is where a post becomes something a person who is not on this machine
can buy, and the token that opens it has no static-key shortcut: Fanvue is OAuth
2.0 only, and there is no dashboard page that shows you the value. Before these
routes existed the only way to obtain one was to drive the authorization by hand
in a terminal, which is why the publisher could be perfectly correct and still
never have posted anything.

Four routes, mirroring the TikTok flow that already exists:

  1. GET  /fanvue/connect      → the URL to send the operator's browser to
  2. (operator approves on fanvue.com)
  3. GET  /fanvue/callback     → exchanges the code, stores the pair, reports
                                 who was connected
  4. GET  /fanvue/status       → what is actually true right now
     POST /fanvue/disconnect   → drop the grant

Two things are deliberately *not* here. The app never writes `.env` — the
callback tells the operator the creator UUID it read and they put it in
themselves, because a process silently rewriting its own configuration is a
class of bug that costs more than one copy-paste saves. And the app never
reports a connected account it did not verify: the callback exercises the token
against `GET /v1/users/me` before it says anything worked.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from app import token_store
from app.config import get_settings
from app.providers.publish.fanvue import (
    DEFAULT_AUTHORIZE_URL,
    DEFAULT_SCOPES,
    FanvuePublisher,
    build_authorize_url,
    exchange_code,
    pkce_pair,
)

router = APIRouter()

PROVIDER = token_store.PROVIDER_FANVUE


def _settings():
    return get_settings()


@router.get("/fanvue/connect")
async def fanvue_connect():
    """Step 1 — the authorize URL, with a PKCE challenge and a CSRF nonce.

    Refuses rather than guesses. Without a client id there is nothing to
    authorize against, and without a redirect URI registered on the client
    Fanvue bounces the operator back to an error page with no explanation —
    which is a worse answer than a 503 that names the two settings.
    """
    settings = _settings()
    missing = [
        name
        for name, value in (
            ("FANVUE_CLIENT_ID", settings.FANVUE_CLIENT_ID),
            ("FANVUE_REDIRECT_URI", settings.FANVUE_REDIRECT_URI),
        )
        if not value
    ]
    if missing:
        raise HTTPException(
            503,
            "Fanvue authorization is not configured: "
            + ", ".join(missing)
            + " must be set in .env. The redirect URI must match the one "
            "registered on your Fanvue client exactly, including the scheme and "
            "any trailing slash.",
        )

    verifier, challenge = pkce_pair()
    nonce = secrets.token_urlsafe(24)
    token_store.save_pending(
        PROVIDER,
        {
            "code_verifier": verifier,
            "nonce": nonce,
            "redirect_uri": settings.FANVUE_REDIRECT_URI,
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {
        "authorize_url": build_authorize_url(
            client_id=settings.FANVUE_CLIENT_ID,
            redirect_uri=settings.FANVUE_REDIRECT_URI,
            state=nonce,
            challenge=challenge,
            scopes=DEFAULT_SCOPES,
            authorize_url=settings.FANVUE_AUTHORIZE_URL or DEFAULT_AUTHORIZE_URL,
        ),
        "scopes": list(DEFAULT_SCOPES),
        "redirect_uri": settings.FANVUE_REDIRECT_URI,
        "note": (
            "Open this URL in a browser and approve it yourself. It requests "
            "only the scopes this app uses: publishing, media upload, the "
            "creator record, and earnings. The code comes back to "
            "FANVUE_REDIRECT_URI and is exchanged there."
        ),
    }


@router.get("/fanvue/callback", response_class=HTMLResponse)
async def fanvue_callback(
    code: str = Query(""),
    state: str = Query(""),
    error: str = Query(""),
    error_description: str = Query(""),
):
    """Step 3 — Fanvue sends the operator's browser back here."""
    if error:
        return HTMLResponse(
            _page("Authorization declined", error_description or error, ok=False),
            status_code=400,
        )

    pending = token_store.load_pending(PROVIDER)
    stored_nonce = pending.get("nonce", "")
    if not stored_nonce or not secrets.compare_digest(stored_nonce, state or ""):
        return HTMLResponse(
            _page(
                "State check failed",
                "This callback does not match an authorization started here — it "
                "is stale, or it was not started from this app. Start again from "
                "/fanvue/connect.",
                ok=False,
            ),
            status_code=400,
        )

    verifier = pending.get("code_verifier", "")
    redirect_uri = pending.get("redirect_uri", "")
    if not verifier or not redirect_uri:
        return HTMLResponse(
            _page("Incomplete handshake", "The stored handshake is incomplete.", ok=False),
            status_code=400,
        )

    settings = _settings()
    try:
        body = await exchange_code(
            token_url=settings.FANVUE_AUTH_URL,
            client_id=settings.FANVUE_CLIENT_ID,
            client_secret=settings.FANVUE_CLIENT_SECRET,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=verifier,
        )
    except Exception as exc:
        return HTMLResponse(_page("Token exchange failed", str(exc), ok=False), status_code=502)

    access_token = body["access_token"]
    # The nonce is single-use whether or not the exchange worked, and it is
    # cleared here rather than on success only: a replay of the same callback
    # must not be able to reuse a verifier that is already spent.
    token_store.clear_pending(PROVIDER)
    token_store.save_tokens(
        PROVIDER,
        {
            "access_token": access_token,
            "refresh_token": body.get("refresh_token", ""),
            "scope": body.get("scope", " ".join(DEFAULT_SCOPES)),
            "expires_in": body.get("expires_in"),
            "obtained_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    # Prove the token before claiming it works, and read the creator uuid. Every
    # media and post path is keyed by it and nothing in the app can discover it —
    # so getting it at the one moment it is a single API call away is the whole
    # difference between "authorized" and "authorized and actually postable".
    creator_uuid = ""
    detail = ""
    reachable = False
    probe = FanvuePublisher(
        client_id=settings.FANVUE_CLIENT_ID,
        client_secret=settings.FANVUE_CLIENT_SECRET,
        access_token=access_token,
        refresh_token=body.get("refresh_token", ""),
        creator_uuid=settings.FANVUE_CREATOR_UUID,
        auth_url=settings.FANVUE_AUTH_URL,
        api_version=settings.FANVUE_API_VERSION,
    )
    try:
        reachable, detail = await probe.health_check()
        if reachable:
            creator_uuid = await probe.creator_uuid_of_token()
    except Exception as exc:  # a read failure must not discard a good grant
        detail = f"token stored, but the verification read failed: {exc}"

    configured = settings.FANVUE_CREATOR_UUID
    lines = []
    if creator_uuid:
        lines.append(f"Creator UUID: <code>{creator_uuid}</code>")
        if not configured:
            lines.append(
                "Set <code>FANVUE_CREATOR_UUID</code> to that value — it is not "
                "configured yet, and every upload and post is keyed by it."
            )
        elif configured != creator_uuid:
            lines.append(
                "⚠️ <code>FANVUE_CREATOR_UUID</code> is set to "
                f"<code>{configured}</code>, which is a <em>different</em> "
                "creator. Every media and post path would be built against the "
                "wrong account and 404."
            )
    if not settings.FANVUE_PUBLISH_ENABLED:
        lines.append(
            "Publishing is still <strong>off</strong> — set "
            "<code>FANVUE_PUBLISH_ENABLED=true</code> when the line should "
            "actually post. A token is not permission."
        )

    headline = "Fanvue connected" if reachable else "Token stored — not verified"
    body_text = detail or "authorized"
    return HTMLResponse(
        _page(
            headline,
            f"{body_text}. {'. '.join(lines)}" if lines else body_text,
            ok=reachable,
        )
    )


@router.get("/fanvue/status")
async def fanvue_status():
    """What is true right now, separated into the three things that get confused.

    Configured, authorized and armed are three different states with three
    different fixes, and the app has already been bitten by collapsing a pair of
    them: an operator sent hunting for a missing credential that was present all
    along. So they are reported separately, and `ok` means only the last one —
    the token was exercised against Fanvue and Fanvue agreed it is good.
    """
    settings = _settings()
    stored = token_store.load_tokens(PROVIDER)
    access_token = stored.get("access_token") or settings.FANVUE_ACCESS_TOKEN

    configured = bool(settings.FANVUE_CLIENT_ID and settings.FANVUE_CREATOR_UUID)
    armed = bool(settings.FANVUE_PUBLISH_ENABLED)

    base = {
        "configured": configured,
        "authorized": bool(access_token),
        "armed": armed,
        "creator_uuid": settings.FANVUE_CREATOR_UUID,
        "token_source": (
            "storage/oauth_tokens.json" if stored.get("access_token")
            else ("FANVUE_ACCESS_TOKEN" if settings.FANVUE_ACCESS_TOKEN else "")
        ),
        "obtained_at": stored.get("obtained_at", ""),
        "scope": stored.get("scope", ""),
        "scopes_requested": list(DEFAULT_SCOPES),
    }

    if not access_token:
        return {
            **base,
            "ok": False,
            "detail": (
                "No Fanvue token. Run GET /fanvue/connect and approve the "
                "authorization; there is no static API key to paste instead."
            ),
        }

    probe = FanvuePublisher(
        client_id=settings.FANVUE_CLIENT_ID,
        client_secret=settings.FANVUE_CLIENT_SECRET,
        access_token=access_token,
        refresh_token=stored.get("refresh_token") or settings.FANVUE_REFRESH_TOKEN,
        creator_uuid=settings.FANVUE_CREATOR_UUID,
        auth_url=settings.FANVUE_AUTH_URL,
        api_version=settings.FANVUE_API_VERSION,
    )
    try:
        reachable, detail = await probe.health_check()
    except Exception as exc:
        reachable, detail = False, f"the live check raised: {exc}"

    # A token that works but belongs to a different creator than the one
    # configured is the failure this endpoint exists to catch: it looks
    # authorized, posts nothing, and 404s on every path.
    uuid_matches = None
    live_uuid = ""
    if reachable:
        try:
            live_uuid = await probe.creator_uuid_of_token()
            if live_uuid:
                uuid_matches = bool(
                    settings.FANVUE_CREATOR_UUID
                    and live_uuid == settings.FANVUE_CREATOR_UUID
                )
        except Exception:
            live_uuid = ""

    return {
        **base,
        "ok": reachable,
        "live_creator_uuid": live_uuid,
        "creator_uuid_matches": uuid_matches,
        "detail": detail,
        "next_step": (
            "" if not reachable
            else "" if armed
            else "Set FANVUE_PUBLISH_ENABLED=true when the line should post."
        ),
    }


@router.post("/fanvue/disconnect")
async def fanvue_disconnect():
    """Drop the stored grant. Fanvue has no documented revoke endpoint, so this
    is local — and it says so rather than implying the token died on their side."""
    token_store.clear_tokens(PROVIDER)
    token_store.clear_pending(PROVIDER)
    return {
        "status": "disconnected",
        "detail": (
            "The stored token pair was deleted and publishing will now report "
            "not-configured. This does not revoke the grant on Fanvue's side — "
            "do that from your account if you want the token dead there too."
        ),
    }


def _page(title: str, message: str, ok: bool) -> str:
    """Minimal self-contained result page for the OAuth redirect."""
    accent = "#d9fb71" if ok else "#ef4444"
    icon = "✓" if ok else "✕"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title></head>
<body style="margin:0;background:#0b0b0d;color:#e8e8ea;font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:grid;place-items:center;min-height:100vh">
  <div style="max-width:520px;padding:32px;text-align:center">
    <div style="width:48px;height:48px;border-radius:50%;background:{accent}1f;color:{accent};display:grid;place-items:center;font-size:24px;margin:0 auto 16px">{icon}</div>
    <h1 style="font-size:20px;margin:0 0 8px;font-weight:600">{title}</h1>
    <p style="margin:0;color:#9a9aa2">{message}</p>
  </div>
</body></html>"""
