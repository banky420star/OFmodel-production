"""Persona Studio — fan-facing authentication.

Cookie sessions, not JWTs. Three reasons, all local to this project:

  * no JWT library is installed here, and the rule is stdlib-only;
  * a session that can be *revoked* is a row write, which is what "sign out"
    actually needs to mean;
  * the Next.js rewrite makes the API same-origin, so an HttpOnly cookie rides
    along automatically with no Authorization-header plumbing in the client.

Password hashing is `hashlib.scrypt` from the standard library — memory-hard,
zero new dependencies. The encoded form records its own parameters
(`scrypt$n$r$p$<salt>$<hash>`), so raising the cost factor later does not
invalidate hashes already in the database.

**Nothing here is global middleware, and that is deliberate.** `require_user`
is applied per-route. The operator API that already exists has no auth and must
keep behaving exactly as it does today; hanging a dependency off the whole app
would silently lock the operator out of their own tool. See FAN_SLICE_TODO.md
§Flags — the operator API staying open is a known, recorded gap.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import AppUser, AuditEvent, AuthSession, FanAccount, utcnow

SESSION_COOKIE = "ps_session"

# Cost parameters for new hashes. n=2^14 with r=8 is ~16 MB per hash — slow
# enough to make offline guessing expensive, fast enough that signup stays
# responsive.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


# ── password hashing ──────────────────────────────────────────────────

def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def hash_password(password: str) -> str:
    """Encode a password for storage. Format: scrypt$n$r$p$salt$hash."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check against a stored hash. Never raises on bad input."""
    try:
        scheme, n, r, p, salt_b64, hash_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = _unb64(hash_b64)
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=_unb64(salt_b64),
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
    except Exception:
        # A malformed stored hash is a rejection, not a crash. Returning False
        # here is the safe direction: it can only deny a login.
        return False
    return hmac.compare_digest(digest, expected)


# ── sessions ──────────────────────────────────────────────────────────

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; comparing them to an aware `now`
    raises TypeError. Normalise on read rather than trusting the driver."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def issue_session(
    db: AsyncSession, user: AppUser, user_agent: str = ""
) -> str:
    """Create a session row and return the raw token for the cookie.

    The raw token is returned exactly once and never stored — only its SHA-256
    goes in the database.
    """
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(
        user_id=user.id,
        token_hash=_token_hash(token),
        expires_at=utcnow() + timedelta(days=settings.SESSION_TTL_DAYS),
        user_agent=(user_agent or "")[:512],
    ))
    await db.flush()
    return token


async def resolve_session(db: AsyncSession, token: str | None) -> AppUser | None:
    """Return the signed-in user for a cookie token, or None."""
    if not token:
        return None
    row = (
        await db.execute(
            select(AuthSession).where(AuthSession.token_hash == _token_hash(token))
        )
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    expires_at = _aware(row.expires_at)
    if expires_at is not None and expires_at < utcnow():
        return None
    user = await db.get(AppUser, row.user_id)
    if user is None or user.status != "active":
        return None
    return user


async def revoke_session(db: AsyncSession, token: str | None) -> bool:
    if not token:
        return False
    row = (
        await db.execute(
            select(AuthSession).where(AuthSession.token_hash == _token_hash(token))
        )
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = utcnow()
    return True


# ── cookie helpers ────────────────────────────────────────────────────

def set_session_cookie(response: Response, token: str) -> None:
    """The one place cookie attributes are decided.

    HttpOnly: JS cannot read it, so an XSS cannot exfiltrate the session.
    SameSite=lax: the fan site is same-origin with the API via the Next
    rewrite, and lax still sends the cookie on top-level navigation back.
    `secure` is off only because this runs over plain http on localhost — it
    must be turned on the moment this is reachable over a network.
    """
    settings = get_settings()
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.SESSION_TTL_DAYS * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE, path="/")


# ── FastAPI dependencies ──────────────────────────────────────────────

async def require_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> AppUser:
    """The signed-in user, or 401. Applied per-route, never globally."""
    user = await resolve_session(db, request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(401, "Sign in required")
    return user


async def require_fan(
    user: AppUser = Depends(require_user), db: AsyncSession = Depends(get_db)
) -> FanAccount:
    """The fan relationship behind the signed-in user, or 409.

    A user with no persona assigned is a half-built signup; that is a server
    state problem, not an auth problem, so it is 409 rather than 401.
    """
    link = (
        await db.execute(
            select(FanAccount).where(FanAccount.user_id == user.id).limit(1)
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(409, "This account has no assigned persona")
    return link


# ── audit ─────────────────────────────────────────────────────────────

async def audit(
    db: AsyncSession,
    action: str,
    *,
    user_id: str | None = None,
    actor: str = "system",
    object_type: str = "",
    object_id: str = "",
    detail: dict | None = None,
) -> None:
    """Write one audit row. Flushed, not committed — the caller owns the
    transaction, so an audit row can never describe something that rolled back.
    """
    db.add(AuditEvent(
        user_id=user_id,
        actor=actor,
        action=action,
        object_type=object_type,
        object_id=str(object_id or ""),
        detail=detail or {},
    ))
    await db.flush()
