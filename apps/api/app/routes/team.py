"""Persona Studio — team invites.

Honest scope: this app has no authentication layer (no user table, no login,
no sessions). An invite therefore *records* who was invited and who accepted;
it does not gate any endpoint. Building something that looked like access
control without actually restricting anything would be worse than saying so
plainly — hence the `enforced: false` field on every response and the matching
copy in the UI.

When a real auth layer lands, these rows are the seed for it: role, email,
acceptance time and expiry are already tracked.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import TeamInvite
from app.config import get_settings

router = APIRouter()

VALID_ROLES = ("operator", "viewer")
DEFAULT_TTL_DAYS = 14

# Surfaced on every invite response so no caller can mistake this for auth.
_ENFORCED = False


def _serialize(invite: TeamInvite, accept_url: str = "") -> dict:
    expired = bool(invite.expires_at and _aware(invite.expires_at) < datetime.now(timezone.utc))
    return {
        "id": str(invite.id),
        "token": invite.token,
        "email": invite.email,
        "role": invite.role,
        "invited_by": invite.invited_by,
        "status": "expired" if (expired and invite.status == "pending") else invite.status,
        "note": invite.note,
        "accept_url": accept_url,
        "created_at": invite.created_at.isoformat() if invite.created_at else None,
        "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
        "accepted_at": invite.accepted_at.isoformat() if invite.accepted_at else None,
        "expired": expired,
        "enforced": _ENFORCED,
    }


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat them as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _accept_url(token: str) -> str:
    origin = get_settings().WEB_ORIGIN.rstrip("/")
    return f"{origin}/invite/{token}"


@router.post("/team/invites")
async def create_invite(
    email: str = Query(""),
    role: str = Query("viewer"),
    note: str = Query(""),
    invited_by: str = Query("admin"),
    ttl_days: int = Query(DEFAULT_TTL_DAYS, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """Create an invite link for another person."""
    if role not in VALID_ROLES:
        raise HTTPException(400, f"role must be one of {list(VALID_ROLES)}, got '{role}'")

    invite = TeamInvite(
        id=uuid4(),
        token=secrets.token_urlsafe(32),
        email=email.strip(),
        role=role,
        invited_by=invited_by,
        note=note,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(days=ttl_days),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    return {
        **_serialize(invite, _accept_url(invite.token)),
        "message": (
            f"Invite created for {email or 'a teammate'}. Send them the link — "
            "note this instance has no login, so the link records the invite "
            "but does not yet restrict access to the app."
        ),
    }


@router.get("/team/invites")
async def list_invites(
    status: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    """List invites, newest first."""
    q = select(TeamInvite).order_by(TeamInvite.created_at.desc())
    if status:
        q = q.where(TeamInvite.status == status)
    result = await db.execute(q)
    return [_serialize(i, _accept_url(i.token)) for i in result.scalars().all()]


@router.get("/team/invites/{token}")
async def get_invite(token: str, db: AsyncSession = Depends(get_db)):
    """Public — read an invite from its link so the page can show who it's for."""
    result = await db.execute(select(TeamInvite).where(TeamInvite.token == token))
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(404, "Invite not found — the link may be mistyped or revoked")
    return _serialize(invite, _accept_url(invite.token))


@router.post("/team/invites/{token}/accept")
async def accept_invite(token: str, db: AsyncSession = Depends(get_db)):
    """Redeem an invite — records acceptance. Grants nothing (see module docstring)."""
    result = await db.execute(select(TeamInvite).where(TeamInvite.token == token))
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(404, "Invite not found")
    if invite.status == "revoked":
        raise HTTPException(409, "This invite was revoked")
    if invite.status == "accepted":
        return {**_serialize(invite, _accept_url(invite.token)), "message": "Invite was already accepted."}
    if invite.expires_at and _aware(invite.expires_at) < datetime.now(timezone.utc):
        invite.status = "expired"
        await db.commit()
        raise HTTPException(410, "This invite has expired")

    invite.status = "accepted"
    invite.accepted_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(invite)

    return {
        **_serialize(invite, _accept_url(invite.token)),
        "message": (
            f"Welcome — recorded as {invite.role}. Reminder: this instance has no "
            "login yet, so this did not unlock anything; you already had access."
        ),
    }


@router.post("/team/invites/{invite_id}/revoke")
async def revoke_invite(invite_id: UUID, db: AsyncSession = Depends(get_db)):
    """Revoke a pending invite so its link stops working."""
    invite = await db.get(TeamInvite, invite_id)
    if not invite:
        raise HTTPException(404, "Invite not found")
    if invite.status == "accepted":
        raise HTTPException(409, "Invite was already accepted — revoke does nothing for it")

    invite.status = "revoked"
    await db.commit()
    return {"status": "revoked", "id": str(invite.id), "enforced": _ENFORCED}
