"""Persona Studio — the model manager view.

One screen answering one question: **what does a person have to do next, for
every model, on every platform?**

The roster here is derived, never asserted. An account is not "signed up"
because a row exists — it is signed up when the platform has it, which no
process in this app can observe. So each account carries the *evidence* it
actually has (an inbox generated, a password staged, an approval recorded) and
a `blockers` list naming what is missing. `next_action` is the single next
human step for that account, and it is the whole point of the page.

`POST /manager/personas/{id}/request-all` files the local request rows for every
platform the persona has no account on. That is the honest form of "sign up for
all the sites": it builds the worklist. It does not contact a platform, and it
does not move any account out of `pending_approval`. The supported way to turn
one of those rows into a real account is the manual signup packet — see the
module note in `app/routes/socials.py`.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Persona, SocialAccount

router = APIRouter()

# The platforms the manager tracks, in the order a launch usually wants them.
PLATFORMS: tuple[str, ...] = (
    "instagram", "tiktok", "twitter", "facebook",
    "onlyfans", "fansly", "fanvue",
)

# Platforms where a person must submit the signup form themselves. All of them,
# currently — the list exists so a future read-only integration (TikTok's
# Display API, say) can be marked differently without touching the UI.
MANUAL_SIGNUP = set(PLATFORMS)


def _next_action(account: SocialAccount, has_password: bool) -> tuple[str, str]:
    """The one next step for this account, and why.

    Returns (action, detail). `action` is a stable slug the UI switches on; the
    detail is the sentence a person reads.
    """
    status = account.status
    if status == "rejected":
        return "closed", "Rejected — re-request this account if it is wanted after all"
    if status == "pending_approval":
        return "approve", "Approve or reject the request before anything else happens"
    if status == "approved":
        if not account.email:
            return "generate_email", "Generate the signup inbox, then complete the packet"
        if not has_password:
            return "open_packet", "Open the signup packet and submit the platform's form by hand"
        return "signup", "Submit the platform's signup form by hand, then save the credentials"
    if status in ("signup_in_progress", "signup_started"):
        return "finish_signup", "Finish the manual signup and save the credentials"
    if status == "active":
        if not account.api_connected:
            return "connect_api", "Live — connect an API for analytics if the platform offers one"
        return "operating", "Live and connected — nothing pending"
    if status == "suspended":
        return "review", "Suspended at the platform — decide whether to appeal or drop it"
    return "request", "No account requested yet on this platform"


def _blockers(account: SocialAccount, has_password: bool) -> list[str]:
    """What is missing, in the order it must be fixed. Empty means nothing is."""
    out: list[str] = []
    if account.status == "rejected":
        return ["rejected"]
    if account.status == "pending_approval":
        out.append("no_operator_approval")
    if not account.email:
        out.append("no_signup_email")
    if not has_password:
        out.append("no_platform_password")
    if not account.display_name:
        out.append("no_display_name")
    if not account.bio:
        out.append("no_bio")
    if account.status != "active":
        out.append("not_live")
    return out


def _inbox_token(account: SocialAccount) -> str:
    """The mail.tm token for this account's signup inbox, from wherever it is.

    `generate_account_email` wrote only to `metadata_json` while the column of
    the same name sat empty, so rows created before that fix carry their token
    in metadata and rows created after carry it in both. Reading one and not the
    other reports "no inbox" for half the accounts that have one.
    """
    return account.email_token or (account.metadata_json or {}).get("email_token", "") or ""


def _account_state(account: SocialAccount) -> dict:
    """One account, as the manager needs to see it. Never claims a platform
    state this app cannot verify."""
    password_staged = bool(account.password_hash)
    credentials_saved = password_staged and bool(account.username)
    action, detail = _next_action(account, password_staged)

    # The packet is what a person works from, so the page links straight to it
    # when there is something to submit.
    packet_ready = bool(account.email) and password_staged

    return {
        "account_id": str(account.id),
        "platform": account.platform,
        "username": account.username,
        "display_name": account.display_name or account.username,
        "email": account.email or "",
        "status": account.status,
        "manual_signup": account.platform in MANUAL_SIGNUP,
        "signup_step": account.signup_step or "",
        "packet_ready": packet_ready,
        "has_inbox": bool(account.email and _inbox_token(account)),
        "has_password": password_staged,
        "credentials_saved": credentials_saved,
        "approved_by": account.approved_by or "",
        "api_connected": bool(account.api_connected),
        "profile_url": account.profile_url or "",
        "followers": account.followers or 0,
        "posts_count": account.posts_count or 0,
        "blockers": _blockers(account, password_staged),
        "next_action": action,
        "next_action_detail": detail,
        "last_posted_at": (
            account.last_posted_at.isoformat() if account.last_posted_at else None
        ),
    }


async def _roster_for(personas: list[Persona], db: AsyncSession) -> list[dict]:
    """Personas with their per-platform account state, warnings, and next work."""
    if not personas:
        return []

    persona_ids = [str(p.id) for p in personas]
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.persona_id.in_(persona_ids))
    )
    by_persona: dict[str, list[SocialAccount]] = {pid: [] for pid in persona_ids}
    for account in result.scalars().all():
        by_persona.setdefault(str(account.persona_id), []).append(account)

    roster: list[dict] = []
    for persona in personas:
        accounts = by_persona.get(str(persona.id), [])
        states = [_account_state(a) for a in accounts]
        states.sort(key=lambda s: (PLATFORMS.index(s["platform"])
                                   if s["platform"] in PLATFORMS else len(PLATFORMS)))

        meta = persona.metadata_json or {}
        # A build that failed a step leaves its warning on the persona record.
        # The manager has to see it, or a model looks finished while its
        # identity is enforced by the locked avatar alone.
        warnings = [
            w if isinstance(w, str) else w.get("message", str(w))
            for w in (meta.get("warnings") or [])
        ]

        covered = {s["platform"] for s in states}
        missing = [p for p in PLATFORMS if p not in covered]
        pending = [s for s in states if s["next_action"] not in ("operating", "closed")]

        roster.append({
            "persona_id": str(persona.id),
            "name": persona.name,
            "status": persona.status.value if hasattr(persona.status, "value") else str(persona.status),
            "adult_verified": bool(persona.adult_verified),
            "synthetic_identity": bool(persona.synthetic_identity),
            "warnings": warnings,
            "accounts": states,
            "platforms_covered": len(covered),
            "platforms_missing": missing,
            "accounts_needing_work": len(pending),
            "next_action": pending[0]["next_action_detail"] if pending else (
                f"Request accounts on {len(missing)} remaining platforms"
                if missing else "Nothing pending"
            ),
        })

    roster.sort(key=lambda r: (-r["accounts_needing_work"], r["name"].lower()))
    return roster


@router.get("/manager/roster")
async def manager_roster(
    persona_id: str | None = None,
    platform: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Every model, every platform, and the next human action for each.

    `platform` narrows the accounts *within* each persona as well as which
    personas have any — a manager asking "who still needs TikTok?" wants the
    other platforms out of the way.
    """
    query = select(Persona).order_by(Persona.created_at.desc())
    if persona_id:
        try:
            query = query.where(Persona.id == UUID(persona_id))
        except ValueError:
            raise HTTPException(400, f"'{persona_id}' is not a persona id")

    personas = list((await db.execute(query)).scalars().all())
    roster = await _roster_for(personas, db)

    if platform:
        for row in roster:
            row["accounts"] = [a for a in row["accounts"] if a["platform"] == platform]
            row["accounts_needing_work"] = len(
                [a for a in row["accounts"] if a["next_action"] not in ("operating", "closed")]
            )
        roster = [r for r in roster if r["accounts"]]

    all_accounts = [a for row in roster for a in row["accounts"]]
    by_action: dict[str, int] = {}
    for account in all_accounts:
        by_action[account["next_action"]] = by_action.get(account["next_action"], 0) + 1

    return {
        "platforms": list(PLATFORMS),
        "roster": roster,
        "summary": {
            "models": len(roster),
            "accounts": len(all_accounts),
            "live": len([a for a in all_accounts if a["status"] == "active"]),
            "needing_work": len(
                [a for a in all_accounts if a["next_action"] not in ("operating", "closed")]
            ),
            "not_requested": sum(len(r["platforms_missing"]) for r in roster),
            "by_next_action": by_action,
        },
        "note": (
            "An account counts as live when the platform has it and a person "
            "says so — never because a row exists here. This roster records "
            "intent and readiness; it verifies nothing and contacts nothing. "
            "Automated signup is refused unless AUTO_SIGNUP_ENABLED is set; the "
            "supported path is each account's signup packet."
        ),
    }


@router.get("/manager/divisions")
async def manager_divisions(
    persona_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """The studio's operating structure: what it makes, and what is stopping it.

    The roster above answers "which account needs what next". This answers the
    larger question — which divisions exist, what each owns, whether each can
    act *right now* against the providers actually configured, and what it has
    produced. Read-only: it resolves providers but never raises for one that is
    missing, because a screen whose job is to show breakage cannot itself break.
    """
    from app.divisions import divisions_status

    if persona_id:
        try:
            UUID(persona_id)
        except ValueError:
            raise HTTPException(400, f"'{persona_id}' is not a persona id")
    return await divisions_status(db, persona_id)


@router.get("/manager/business")
async def manager_business(
    days: int = Query(30, ge=1, le=365, description="Earnings window"),
    db: AsyncSession = Depends(get_db),
):
    """The whole book: what can ship, what it has earned, and the one next action.

    The roster answers "which account needs what next" and the divisions view
    answers "which production units can act". Neither mentions money, and money
    is the question. This joins them — see `app/business.py` for why it
    aggregates rather than forms a fourth opinion.

    Read-only. The single outbound call is the platform's earnings read, bounded
    so a slow API cannot hold a manager screen open.
    """
    from app.business import business_state

    return await business_state(db, days=days)


def _username_for(persona: Persona, platform: str, taken: set[str]) -> str:
    """A platform-appropriate handle, unique among that platform's accounts.

    Platforms have different rules (Instagram allows dots, TikTok does not), so
    the base is normalized per platform. The de-duplication is per *platform*
    too: handles are namespaced per site, so `zara` on TikTok does not collide
    with `zara` on Instagram — and a manager wants the same handle everywhere,
    not `zara2`, `zara3`, `zara4` because the loop happened to walk the list.
    """
    import re

    key = f"{platform}:{persona.name.lower()}"
    if platform in ("instagram", "fanvue", "fansly"):
        base = re.sub(r"[^a-z0-9.]", "", persona.name.lower().replace(" ", ".")) or "model"
    else:
        base = re.sub(r"[^a-z0-9]", "", persona.name.lower()) or "model"

    candidate = base
    suffix = 1
    while f"{platform}:{candidate}" in taken:
        suffix += 1
        candidate = f"{base}{suffix}"
    taken.add(f"{platform}:{candidate}")
    return candidate[:30]


@router.post("/manager/personas/{persona_id}/request-all")
async def request_all_platforms(
    persona_id: UUID,
    platforms: str = Query("", description="Comma-separated subset; empty means all"),
    operator: str = Query("admin"),
    db: AsyncSession = Depends(get_db),
):
    """File a local account request for every platform this persona lacks.

    This *is* the worklist step, and nothing more: it writes rows in
    `pending_approval`, which is what makes each platform show up on the
    manager page with its own next action. It contacts no platform, creates no
    account, and generates no inbox — approval and the signup packet do that.
    """
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    wanted = [p.strip() for p in platforms.split(",") if p.strip()] or list(PLATFORMS)
    unknown = [p for p in wanted if p not in PLATFORMS]
    if unknown:
        raise HTTPException(
            400,
            f"Unknown platform(s): {', '.join(unknown)}. Known: {', '.join(PLATFORMS)}",
        )

    existing = (await db.execute(
        select(SocialAccount).where(SocialAccount.persona_id == str(persona_id))
    )).scalars().all()
    taken = {f"{a.platform}:{a.username}" for a in existing}
    have = {a.platform for a in existing if a.status != "rejected"}

    created: list[dict] = []
    skipped: list[dict] = []
    for platform in wanted:
        if platform in have:
            skipped.append({
                "platform": platform,
                "reason": f"already has an account row ('{next(a.status for a in existing if a.platform == platform)}')",
            })
            continue

        username = _username_for(persona, platform, taken)
        account = SocialAccount(
            id=str(uuid4()),
            persona_id=str(persona_id),
            platform=platform,
            username=username,
            display_name=persona.name,
            bio=persona.brand or f"{persona.name} — content creator",
            status="pending_approval",
        )
        db.add(account)
        created.append({
            "account_id": account.id,
            "platform": platform,
            "username": username,
            "status": "pending_approval",
        })

    if created:
        await db.commit()

    return {
        "persona_id": str(persona_id),
        "persona_name": persona.name,
        "created": created,
        "skipped": skipped,
        "created_count": len(created),
        "skipped_count": len(skipped),
        "operator": operator,
        "status": "requests_filed",
        "note": (
            "Requests filed locally. Nothing was sent to any platform and no "
            "account exists anywhere yet — approve each one, then complete its "
            "signup packet by hand."
        ),
    }
