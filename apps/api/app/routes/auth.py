"""Persona Studio — fan signup, login, logout.

Registration is where the age gate actually lives. The pre-existing
`POST /gate/age-confirm` sets a cookie for anyone who asks and records nothing;
this route captures a date of birth, refuses under-18s, and writes an audit row
that says when the check happened. That is a materially different thing, and the
UI says which one it is.

It is still *self-attested*. Nothing here verifies the date is real. See
FAN_SLICE_TODO.md §Flags.
"""

from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    audit, clear_session_cookie, hash_password, issue_session, require_user,
    resolve_session, revoke_session, set_session_cookie, verify_password,
)
from app.billing.service import grant_signup_credit
from app.config import get_settings
from app.database import get_db
from app.models import AppUser, Persona, PersonaStatus, utcnow

router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8
MIN_AGE = 18
MAX_AGE = 120


class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=1)
    display_name: str = ""
    date_of_birth: str  # ISO YYYY-MM-DD


class LoginRequest(BaseModel):
    email: str
    password: str


# ── helpers ───────────────────────────────────────────────────────────

def age_from(dob: str) -> int | None:
    """Whole years since `dob`, or None if it is not a usable date."""
    try:
        born = date.fromisoformat((dob or "").strip())
    except (ValueError, TypeError):
        return None
    today = date.today()
    if born > today:
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


async def _assign_persona(db: AsyncSession) -> Persona | None:
    """Which persona a new fan gets.

    A configured id wins. Otherwise the oldest persona that is actually
    finished (ACTIVE or READY) — not merely the first row, which would hand new
    fans a persona still mid-build with no identity and nothing to sell.
    """
    settings = get_settings()

    if settings.FAN_DEFAULT_PERSONA_ID:
        from uuid import UUID

        try:
            persona = await db.get(Persona, UUID(settings.FAN_DEFAULT_PERSONA_ID))
        except ValueError:
            raise HTTPException(
                500, "FAN_DEFAULT_PERSONA_ID is not a valid persona id"
            )
        if persona is None:
            raise HTTPException(
                500, "FAN_DEFAULT_PERSONA_ID points at a persona that does not exist"
            )
        return persona

    persona = (
        await db.execute(
            select(Persona)
            .where(Persona.status.in_([PersonaStatus.ACTIVE, PersonaStatus.READY]))
            .order_by(Persona.created_at, Persona.id)
            .limit(1)
        )
    ).scalars().first()
    return persona


def _public_user(user: AppUser) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "date_of_birth": user.date_of_birth,
        "is_adult_confirmed": bool(user.is_adult_confirmed),
        "adult_verified_at": (
            user.adult_verified_at.isoformat() if user.adult_verified_at else None
        ),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


# ── routes ────────────────────────────────────────────────────────────

@router.post("/auth/signup", status_code=201)
async def signup(
    payload: SignupRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Create a fan account. Refuses under-18s and records the check."""
    email = (payload.email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(422, "That does not look like an email address")

    if len(payload.password or "") < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            422, f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
        )

    age = age_from(payload.date_of_birth)
    if age is None:
        raise HTTPException(422, "Date of birth must be a real date, as YYYY-MM-DD")
    if age < MIN_AGE:
        # 403 rather than 422: the request is well-formed, the person is not
        # eligible. The message names the rule so it does not read as a bug.
        raise HTTPException(
            403,
            "You must be at least 18 to create an account. "
            "This site is for adults only.",
        )
    if age > MAX_AGE:
        raise HTTPException(422, "Date of birth is not plausible")

    existing = (
        await db.execute(select(AppUser.id).where(AppUser.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, "An account with that email already exists")

    persona = await _assign_persona(db)
    if persona is None:
        raise HTTPException(
            503,
            "No persona is available to assign. Finish building one, or set "
            "FAN_DEFAULT_PERSONA_ID.",
        )

    now = utcnow()
    user = AppUser(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=(payload.display_name or "").strip()[:256],
        date_of_birth=(payload.date_of_birth or "").strip(),
        is_adult_confirmed=True,
        adult_verified_at=now,
    )
    db.add(user)
    await db.flush()

    await audit(
        db, "signup", user_id=user.id, actor="user",
        object_type="app_user", object_id=user.id,
        detail={"email": email, "age": age},
    )
    await audit(
        db, "age_verified", user_id=user.id, actor="system",
        object_type="app_user", object_id=user.id,
        detail={
            "method": "self_attested_date_of_birth",
            "date_of_birth": user.date_of_birth,
            "age_at_verification": age,
            "assurance": "none — declared, not verified",
        },
    )

    # The Fan row is created with raw SQL, matching how fans.py already reads
    # and writes this table. `personas.id` is a UUID column and `fans.persona_id`
    # is a String FK onto it; the existing code passes the hex string through
    # and that is what the rest of the app expects to find.
    fan_id = str(__import__("uuid").uuid4())
    await db.execute(
        text(
            "INSERT INTO fans (id, persona_id, username, display_name, platform, "
            "status, subscription_tier, total_spent, ppv_purchases, tips_given, "
            "messages_sent, messages_received, fan_score, tags, notes, "
            "metadata_json, created_at, updated_at) VALUES (:id, :pid, :username, "
            ":display_name, 'fanvue', 'active', 'free', 0, 0, 0, 0, 0, 0, '[]', "
            "'', '{}', :now, :now)"
        ),
        {
            "id": fan_id,
            "pid": str(persona.id),
            "username": email.split("@")[0][:128],
            "display_name": user.display_name,
            "now": now,
        },
    )

    from app.models import FanAccount, Wallet
    db.add(FanAccount(user_id=user.id, fan_id=fan_id, persona_id=str(persona.id)))
    db.add(Wallet(user_id=user.id, balance_minor=0))
    await db.flush()

    settings = get_settings()
    grant = await grant_signup_credit(
        db, user_id=user.id, amount_minor=settings.SIGNUP_CREDIT_MINOR
    )

    token = await issue_session(db, user, request.headers.get("user-agent", ""))
    await db.commit()

    set_session_cookie(response, token)
    return {
        "user": _public_user(user),
        "persona": {
            "id": str(persona.id),
            "name": persona.name,
            "avatar_url": persona.avatar_url or "",
            "brand": persona.brand or "",
        },
        "fan_id": fan_id,
        "signup_credit_minor": grant["granted"],
        "simulated": True,
    }


@router.post("/auth/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    email = (payload.email or "").strip().lower()
    user = (
        await db.execute(select(AppUser).where(AppUser.email == email))
    ).scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        # One message for both cases. Saying "no such account" would turn this
        # endpoint into an email-enumeration oracle.
        raise HTTPException(401, "Email or password is incorrect")
    if user.status != "active":
        raise HTTPException(403, f"This account is {user.status}")

    await audit(db, "login", user_id=user.id, actor="user",
                object_type="app_user", object_id=user.id)
    token = await issue_session(db, user, request.headers.get("user-agent", ""))
    await db.commit()

    set_session_cookie(response, token)
    return {"user": _public_user(user)}


@router.post("/auth/logout")
async def logout(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    token = request.cookies.get("ps_session")
    user = await resolve_session(db, token)
    await revoke_session(db, token)
    if user is not None:
        await audit(db, "logout", user_id=user.id, actor="user",
                    object_type="app_user", object_id=user.id)
    await db.commit()

    clear_session_cookie(response)
    return {"signed_out": True}


@router.get("/auth/me")
async def me(
    user: AppUser = Depends(require_user), db: AsyncSession = Depends(get_db)
):
    from app.models import FanAccount

    link = (
        await db.execute(
            select(FanAccount).where(FanAccount.user_id == user.id).limit(1)
        )
    ).scalar_one_or_none()

    persona_payload = None
    fan_id = None
    if link is not None:
        fan_id = link.fan_id
        from uuid import UUID

        try:
            persona = await db.get(Persona, UUID(str(link.persona_id)))
        except ValueError:
            persona = None
        if persona is not None:
            persona_payload = {
                "id": str(persona.id),
                "name": persona.name,
                "avatar_url": persona.avatar_url or "",
                "brand": persona.brand or "",
            }

    return {
        "user": _public_user(user),
        "fan_id": fan_id,
        "persona": persona_payload,
    }
