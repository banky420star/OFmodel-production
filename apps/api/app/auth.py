"""Persona Studio — API auth gate.

A minimal bearer-token gate for the operator console (persona creation, fan
chat, stored platform credentials, adult-content generation).

This is a gate, not a user system: no user table, no sessions, no OAuth.
A single shared operator token (``API_AUTH_TOKEN``) is compared against the
``Authorization: Bearer <token>`` header. When ``API_AUTH_TOKEN`` is unset or
empty, auth is DISABLED so local dev and the pytest suite work unchanged.
"""

import secrets

from fastapi import Header, HTTPException, Query, status

from app.config import get_settings

_UNAUTHORIZED_DETAIL = "Unauthorized — check API token in Settings"


def _matches(candidate: str | None, expected: str) -> bool:
    """Constant-time compare; encode() both sides so non-ASCII can't raise."""
    if not candidate:
        return False
    return secrets.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _bearer_token(authorization: str | None) -> str | None:
    scheme, _, credentials = (authorization or "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return credentials or None


async def require_api_token(
    authorization: str | None = Header(default=None),
) -> None:
    """FastAPI dependency enforcing the operator bearer token.

    - No ``API_AUTH_TOKEN`` configured -> auth disabled, request passes.
    - Otherwise the request must carry ``Authorization: Bearer <token>``
      matching ``API_AUTH_TOKEN`` (constant-time compare).
    """
    expected = get_settings().API_AUTH_TOKEN
    if not expected:
        return

    if not _matches(_bearer_token(authorization), expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_UNAUTHORIZED_DETAIL,
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_media_token(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None, description="Auth token for <img> tags"),
) -> None:
    """Auth gate for browser-served media (avatars, gallery, shoots, adult content).

    ``<img>`` tags cannot send ``Authorization`` headers, so these routes also
    accept the operator token as a ``?token=`` query parameter. TRADEOFF: the
    token therefore appears in image URLs (browser history, server logs).
    Acceptable for a single-operator gate; do not reuse the operator token for
    anything else. API clients should prefer the Bearer header.
    """
    expected = get_settings().API_AUTH_TOKEN
    if not expected:
        return

    if _matches(_bearer_token(authorization), expected) or _matches(token, expected):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_UNAUTHORIZED_DETAIL,
        headers={"WWW-Authenticate": "Bearer"},
    )