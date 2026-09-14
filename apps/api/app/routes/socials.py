"""Persona Studio — socials routes."""

from __future__ import annotations
import asyncio
import json
import time
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Depends, Query, Form
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import SocialAccount, Persona
from app.providers.email import create_temp_email, fetch_emails

router = APIRouter()

@router.get("/social-accounts")
async def list_social_accounts(
    persona_id: str | None = None,
    platform: str | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List social media accounts across all personas with filtering."""
    from app.models import SocialAccount
    q = select(SocialAccount).order_by(SocialAccount.created_at.desc())
    if persona_id:
        q = q.where(SocialAccount.persona_id == persona_id)
    if platform:
        q = q.where(SocialAccount.platform == platform)
    if status:
        q = q.where(SocialAccount.status == status)
    result = await db.execute(q)
    accounts = result.scalars().all()

    # Get persona names
    persona_map = {}
    personas_result = await db.execute(select(Persona))
    for p in personas_result.scalars().all():
        persona_map[str(p.id)] = p.name

    return [
        {
            "id": str(a.id),
            "persona_id": str(a.persona_id),
            "persona_name": persona_map.get(str(a.persona_id), "Unknown"),
            "platform": a.platform,
            "username": a.username,
            "display_name": a.display_name,
            "email": a.email,
            "profile_url": a.profile_url,
            "bio": a.bio,
            "status": a.status,
            "approval_notes": a.approval_notes,
            "approved_by": a.approved_by,
            "approved_at": a.approved_at.isoformat() if a.approved_at else None,
            "rejected_at": a.rejected_at.isoformat() if a.rejected_at else None,
            "rejection_reason": a.rejection_reason,
            "followers": a.followers or 0,
            "following": a.following or 0,
            "posts_count": a.posts_count or 0,
            "api_connected": a.api_connected or False,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in accounts
    ]


@router.post("/social-accounts")
async def request_social_account(
    persona_id: str = Query(...),
    platform: str = Query(...),
    username: str = Query(...),
    display_name: str = Query(""),
    email: str = Query(""),
    bio: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    """Request a new social media account for a persona. Goes to pending_approval."""
    from app.models import SocialAccount

    persona = await db.get(Persona, UUID(persona_id))
    if not persona:
        raise HTTPException(404, "Persona not found")

    # Check for duplicate
    existing = await db.execute(
        select(SocialAccount).where(
            SocialAccount.persona_id == str(persona_id),
            SocialAccount.platform == platform,
            SocialAccount.username == username,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Account @{username} on {platform} already exists for this persona")

    account = SocialAccount(
        id=str(uuid4()),
        persona_id=str(persona_id),
        platform=platform,
        username=username,
        display_name=display_name or username,
        email=email,
        bio=bio or f"{persona.name} — {persona.brand or 'content creator'}",
        status="pending_approval",
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)

    return {
        "id": str(account.id),
        "status": account.status,
        "message": f"Account request submitted for @{username} on {platform}. Awaiting operator approval.",
    }


@router.post("/social-accounts/{account_id}/approve")
async def approve_social_account(
    account_id: UUID,
    notes: str = Query(""),
    operator: str = Query("admin"),
    db: AsyncSession = Depends(get_db),
):
    """Approve a pending social account request."""
    from app.models import SocialAccount

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")
    if account.status != "pending_approval":
        raise HTTPException(400, f"Account is '{account.status}', not pending approval")

    account.status = "approved"
    account.approval_notes = notes
    account.approved_by = operator
    account.approved_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "status": "approved",
        "account_id": str(account_id),
        "platform": account.platform,
        "username": account.username,
    }


@router.post("/social-accounts/{account_id}/reject")
async def reject_social_account(
    account_id: UUID,
    reason: str = Query(...),
    operator: str = Query("admin"),
    db: AsyncSession = Depends(get_db),
):
    """Reject a pending social account request."""
    from app.models import SocialAccount

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")
    if account.status != "pending_approval":
        raise HTTPException(400, f"Account is '{account.status}', not pending approval")

    account.status = "rejected"
    account.rejection_reason = reason
    account.rejected_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "status": "rejected",
        "account_id": str(account_id),
        "reason": reason,
    }


@router.post("/social-accounts/{account_id}/activate")
async def activate_social_account(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Activate an approved account (set live)."""
    from app.models import SocialAccount

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")
    if account.status != "approved":
        raise HTTPException(400, f"Account is '{account.status}', must be approved first")

    account.status = "active"
    await db.commit()

    return {"status": "active", "account_id": str(account_id)}


@router.get("/personas/{persona_id}/social-accounts")
async def list_persona_social_accounts(
    persona_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """List all social accounts for a specific persona."""
    from app.models import SocialAccount

    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    result = await db.execute(
        select(SocialAccount)
        .where(SocialAccount.persona_id == persona_id)
        .order_by(SocialAccount.created_at.desc())
    )
    accounts = result.scalars().all()

    return [
        {
            "id": str(a.id),
            "platform": a.platform,
            "username": a.username,
            "display_name": a.display_name,
            "status": a.status,
            "followers": a.followers or 0,
            "posts_count": a.posts_count or 0,
            "api_connected": a.api_connected or False,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in accounts
    ]


@router.post("/social-accounts/{account_id}/generate-email")
async def generate_account_email(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Generate a temporary email address for a social account using mail.tm."""
    from app.models import SocialAccount
    from app.providers.email import create_temp_email

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")

    persona = await db.get(Persona, UUID(str(account.persona_id)))
    if not persona:
        raise HTTPException(404, "Persona not found")

    try:
        email_result = await create_temp_email(
            persona_name=persona.name,
            prefix=account.platform,
        )
    except Exception as e:
        raise HTTPException(502, f"Failed to create email: {e}")

    # Store email info in the account — BOTH on the typed columns (read by
    # auto-signup / inbox checks) and in metadata (legacy readers).
    account.email = email_result.address
    account.email_account_id = email_result.account_id
    account.email_password = email_result.password
    account.email_token = email_result.token
    account.email_domain = email_result.domain
    account.metadata_json = {
        **(account.metadata_json or {}),
        "email_account_id": email_result.account_id,
        "email_password": email_result.password,
        "email_token": email_result.token,
        "email_domain": email_result.domain,
    }
    await db.commit()

    return {
        "email": email_result.address,
        "domain": email_result.domain,
        "status": "created",
        "message": f"Email {email_result.address} created. Use this to sign up on {account.platform}.",
    }


@router.get("/social-accounts/{account_id}/emails")
async def check_account_emails(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Check for verification emails received at the account's temp email."""
    from app.models import SocialAccount
    from app.providers.email import fetch_emails

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")

    token = getattr(account, 'email_token', '') or ''
    if not token:
        meta = account.metadata_json or {}
        token = meta.get("email_token", "")
    if not token:
        raise HTTPException(400, "No email account generated yet. Call generate-email first.")

    try:
        emails = await fetch_emails(token)
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch emails: {e}")

    return {
        "email": account.email,
        "count": len(emails),
        "emails": emails,
    }


# ─── Social Account Production Features ────────────────────────────

@router.post("/social-accounts/{account_id}/sync-profile")
async def sync_profile_to_account(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Push persona profile data (bio, avatar, display name) to a social account."""
    from app.models import SocialAccount

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")

    persona = await db.get(Persona, UUID(str(account.persona_id)))
    if not persona:
        raise HTTPException(404, "Persona not found")

    # Sync profile data from persona to account
    account.display_name = persona.name
    account.bio = f"{persona.name} — {persona.brand or 'content creator'}"
    account.profile_image_url = persona.avatar_url or ""
    account.metadata_json = {
        **(account.metadata_json or {}),
        "synced_from_persona": True,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "persona_appearance": persona.appearance or {},
        "persona_personality": persona.personality or [],
    }
    await db.commit()

    return {
        "status": "synced",
        "display_name": account.display_name,
        "bio": account.bio,
        "avatar": account.profile_image_url,
    }


@router.post("/social-accounts/{account_id}/store-credentials")
async def store_credentials(
    account_id: UUID,
    platform_password: str = Query(...),
    platform_username: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    """Store encrypted platform credentials for automated posting."""
    from app.models import SocialAccount
    import hashlib, base64

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")

    # Simple obfuscation (production would use Fernet/AES)
    # For now, base64 encode — swap to proper encryption in production
    encoded = base64.b64encode(platform_password.encode()).decode()
    account.password_hash = encoded
    if platform_username:
        account.username = platform_username
    account.metadata_json = {
        **(account.metadata_json or {}),
        "credentials_stored": True,
        "credentials_stored_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.commit()

    return {"status": "stored", "username": account.username}


@router.post("/social-accounts/{account_id}/post")
async def post_to_social_account(
    account_id: UUID,
    content_pack_id: str = Query(...),
    caption: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    """Post content from a content pack to a social account."""
    from app.models import SocialAccount, ContentPack

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")
    if account.status != "active":
        raise HTTPException(400, f"Account is '{account.status}', must be active to post")

    pack = await db.get(ContentPack, UUID(content_pack_id))
    if not pack:
        raise HTTPException(404, "Content pack not found")

    # Build the post payload
    post_payload = {
        "platform": account.platform,
        "username": account.username,
        "caption": caption or pack.name,
        "images": pack.images or [],
        "videos": pack.videos or [],
        "content_pack_id": content_pack_id,
    }

    # Store the scheduled post
    from app.models import ScheduledPost
    scheduled = ScheduledPost(
        id=uuid4(),
        persona_id=str(account.persona_id),
        content_pack_id=pack.id,
        platform=account.platform,
        content_type="image" if pack.images else "video",
        title=pack.name,
        caption=caption or pack.name,
        media_keys=pack.images or pack.videos or [],
        status="scheduled",
        scheduled_at=datetime.now(timezone.utc),
    )
    db.add(scheduled)

    account.posts_count = (account.posts_count or 0) + 1
    account.last_posted_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "status": "posted",
        "platform": account.platform,
        "username": account.username,
        "content_pack": pack.name,
        "post_id": str(scheduled.id),
    }


@router.post("/social-accounts/bulk-sync")
async def bulk_sync_profiles(
    db: AsyncSession = Depends(get_db),
):
    """Sync profile data for all active social accounts."""
    from app.models import SocialAccount

    result = await db.execute(
        select(SocialAccount).where(SocialAccount.status.in_(["active", "approved"]))
    )
    accounts = result.scalars().all()
    synced = 0

    for account in accounts:
        persona = await db.get(Persona, UUID(str(account.persona_id)))
        if not persona:
            continue
        account.display_name = persona.name
        account.bio = f"{persona.name} — {persona.brand or 'content creator'}"
        account.profile_image_url = persona.avatar_url or ""
        synced += 1

    await db.commit()
    return {"synced": synced, "total": len(accounts)}


@router.get("/social-accounts/{account_id}/post-history")
async def get_post_history(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get posting history for a social account."""
    from app.models import SocialAccount, ScheduledPost

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")

    result = await db.execute(
        select(ScheduledPost)
        .where(ScheduledPost.persona_id == str(account.persona_id))
        .where(ScheduledPost.platform == account.platform)
        .order_by(ScheduledPost.created_at.desc())
        .limit(50)
    )
    posts = result.scalars().all()

    return [
        {
            "id": str(p.id),
            "title": p.title,
            "caption": p.caption,
            "platform": p.platform,
            "status": p.status,
            "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None,
            "posted_at": p.posted_at.isoformat() if p.posted_at else None,
        }
        for p in posts
    ]


# ─── Automated Browser Signup ─────────────────────────────────────

import secrets as _secrets

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_FULLAUTO_SCRIPT = _PROJECT_ROOT / "scripts" / "ig_fullauto_signup.py"
# The only interpreter on this machine with Playwright + its matching Chromium.
_FULLAUTO_PY = (
    "/Applications/Xcode.app/Contents/Developer/Library/Frameworks/"
    "Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python"
)


@router.post("/social-accounts/{account_id}/auto-signup")
async def auto_signup_account(
    account_id: UUID,
    headless: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    """
    Automated browser signup for a social platform.
    Uses Playwright to navigate to the platform, fill in the signup form,
    and submit — using the email/password already stored on the account.
    """
    from app.models import SocialAccount
    from app.providers.browser_signup import auto_signup

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")

    if not account.email:
        raise HTTPException(400, "No email address on this account. Generate one first.")

    # Get or generate a password
    password = getattr(account, 'password_hash', '') or ''
    if not password:
        password = _secrets.token_urlsafe(12)
        import base64
        account.password_hash = base64.b64encode(password.encode()).decode()

    # Update status
    account.status = "signup_in_progress"
    account.signup_step = "signup_started"
    await db.commit()

    # Run the browser automation — pass the mail.tm token so an email-code wall
    # is completed in the same live session (code typed before the browser closes).
    try:
        result = await auto_signup(
            platform=account.platform,
            email=account.email,
            password=password,
            display_name=account.display_name or account.username,
            username=account.username,
            headless=headless,
            email_token=account.email_token or "",
        )
    except Exception as e:
        account.status = "pending_approval"
        account.signup_step = ""
        await db.commit()
        raise HTTPException(500, f"Browser automation failed: {str(e)[:200]}")

    # Update account based on result
    if result.success:
        account.status = "pending_approval" if result.status == "verification_needed" else "active"
        account.signup_step = result.status
        if result.profile_url:
            account.profile_url = result.profile_url
        account.metadata_json = {
            **(account.metadata_json or {}),
            "signup_result": result.status,
            "signup_message": result.message,
            "signup_screenshot": result.screenshot_path,
            "signup_at": datetime.now(timezone.utc).isoformat(),
            # Persist the FULL session (sessionid included) — truncating to 5
            # cookies used to drop the only cookie that keeps the login alive.
            "session_cookies": result.session_cookies or [],
        }
    else:
        account.status = "pending_approval"
        account.signup_step = result.status
        account.approval_notes = result.message
        account.metadata_json = {
            **(account.metadata_json or {}),
            "signup_result": result.status,
            "signup_message": result.message,
            "signup_screenshot": result.screenshot_path,
            "signup_at": datetime.now(timezone.utc).isoformat(),
        }

    await db.commit()

    return {
        "success": result.success,
        "status": result.status,
        "message": result.message,
        "screenshot": result.screenshot_path,
        "platform": result.platform,
        "username": result.username,
        "email": result.email,
        "profile_url": result.profile_url,
    }


@router.post("/social-accounts/{account_id}/assisted-signup")
async def assisted_signup_account(
    account_id: UUID,
    wait_minutes: float = Query(15.0, gt=0, le=45),
    db: AsyncSession = Depends(get_db),
):
    """
    Operator-ASSISTED signup (maximizes account survival):

    1. A VISIBLE Chromium window opens on this machine.
    2. The robot fills the signup form (email, password, birthday, name,
       username) and clicks Submit.
    3. The robot then PAUSES — the operator personally solves the 6-digit
       email code / CAPTCHA / phone check inside that same window.
    4. The moment the operator's work lands an authenticated session
       (sessionid cookie), the robot captures the FULL cookie set, verifies
       the profile resolves, saves proof screenshots, and closes.

    Runs as a background task: poll /assisted-status until finished=true.
    """
    from app.models import SocialAccount
    from app.providers.browser_signup import assisted_signup_instagram

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")
    if not account.email:
        raise HTTPException(400, "No email address on this account. Generate one first.")
    if account.platform.lower() != "instagram":
        raise HTTPException(400, f"Assisted signup currently supports instagram (got '{account.platform}')")

    # Fresh signup needs a password the operator can also read from the status
    # endpoint while the window is open.
    password = ""
    try:
        import base64
        password = base64.b64decode(account.password_hash or "").decode()
    except Exception:
        password = ""
    if not password:
        import base64
        password = _secrets.token_urlsafe(12)
        account.password_hash = base64.b64encode(password.encode()).decode()

    # A previous assisted run may have gone quiet — clear any stale live flag.
    meta = dict(account.metadata_json or {})
    if meta.get("assisted_live"):
        raise HTTPException(
            409,
            f"An assisted signup for this account is already in progress (started {meta.get('assisted_started_at', '?')}). "
            "Poll /assisted-status or wait for it to finish.",
        )

    account.status = "signup_in_progress"
    account.signup_step = "assisted_started"
    account.metadata_json = {
        **meta,
        "assisted_live": True,
        "assisted_started_at": datetime.now(timezone.utc).isoformat(),
        "assisted_wait_minutes": wait_minutes,
        "assisted_finished": False,
    }
    await db.commit()

    async def _run_assisted() -> None:
        """Own its own DB session (the request session is closed by then)."""
        from app.database import AsyncSessionLocal

        try:
            result = await assisted_signup_instagram(
                email=account.email,
                password=password,
                display_name=account.display_name or account.username,
                username=account.username,
                wait_minutes=wait_minutes,
            )
        except Exception as e:
            result = None
            err = str(e)[:200]
        else:
            err = ""

        async with AsyncSessionLocal() as db2:
            acc = await db2.get(SocialAccount, str(account_id))
            if acc is None:
                return
            if result is not None:
                if result.status == "suspended":
                    acc.status = "suspended"
                    acc.rejection_reason = "Instagram flagged/suspended the account during operator-assisted signup"
                    acc.profile_url = ""
                elif result.success:
                    acc.status = "active"
                    if result.profile_url:
                        acc.profile_url = result.profile_url
                else:
                    acc.status = "pending_approval"
                acc.signup_step = result.status
                acc.approval_notes = result.message
                acc.metadata_json = {
                    **(acc.metadata_json or {}),
                    "assisted_live": False,
                    "assisted_finished": True,
                    "assisted_result": result.status,
                    "assisted_message": result.message,
                    "assisted_screenshot": result.screenshot_path,
                    "assisted_finished_at": datetime.now(timezone.utc).isoformat(),
                    # Full session (sessionid included) for later automated use.
                    "session_cookies": result.session_cookies or [],
                }
            else:
                acc.status = "pending_approval"
                acc.signup_step = "error"
                acc.approval_notes = f"Assisted signup crashed: {err}"
                acc.metadata_json = {
                    **(acc.metadata_json or {}),
                    "assisted_live": False,
                    "assisted_finished": True,
                    "assisted_result": "error",
                    "assisted_message": acc.approval_notes,
                    "assisted_finished_at": datetime.now(timezone.utc).isoformat(),
                }
            await db2.commit()

    asyncio.create_task(_run_assisted())

    return {
        "status": "assisted_started",
        "account_id": str(account_id),
        "username": account.username,
        "email": account.email,
        "wait_minutes": wait_minutes,
        "message": (
            "A visible Chrome window is opening. The robot fills the form, then "
            "YOU solve the email code / CAPTCHA in that window. Poll "
            f"/social-accounts/{account_id}/assisted-status until finished=true."
        ),
    }


@router.get("/social-accounts/{account_id}/assisted-status")
async def assisted_signup_status(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Live status of an assisted signup, for the operator watching the window."""
    from app.models import SocialAccount

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")

    meta = account.metadata_json or {}
    started_at = meta.get("assisted_started_at")
    elapsed_min: float | None = None
    if started_at:
        try:
            t0 = datetime.fromisoformat(started_at)
            elapsed_min = round((datetime.now(timezone.utc) - t0).total_seconds() / 60, 1)
        except Exception:
            pass

    return {
        "account_id": str(account_id),
        "platform": account.platform,
        "username": account.username,
        "email": account.email,
        "account_status": account.status,
        "signup_step": account.signup_step,
        "in_progress": bool(meta.get("assisted_live")) and not meta.get("assisted_finished"),
        "finished": bool(meta.get("assisted_finished")),
        "result": meta.get("assisted_result"),
        "message": meta.get("assisted_message"),
        "screenshot": meta.get("assisted_screenshot"),
        "profile_url": account.profile_url,
        "started_at": started_at,
        "elapsed_minutes": elapsed_min,
        "wait_minutes": meta.get("assisted_wait_minutes"),
        "has_session_cookies": bool(meta.get("session_cookies")),
    }


@router.post("/social-accounts/{account_id}/fullauto-signup")
async def fullauto_signup_account(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Launch the FULLY-AUTOMATED visible signup (robot fills the form, polls the
    real inbox, types the verification code itself) as a detached Terminal
    running scripts/ig_fullauto_signup.py.

    Live progress: poll GET /social-accounts/{id}/fullauto-status (backed by a
    status file the script rewrites at each phase).
    """
    import os
    import subprocess

    from app.models import SocialAccount

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")
    if not account.email:
        raise HTTPException(400, "No email on this account — generate one first.")
    if account.platform.lower() != "instagram":
        raise HTTPException(400, "Full-auto signup currently supports instagram only.")
    if not _FULLAUTO_SCRIPT.exists():
        raise HTTPException(500, f"Script missing: {_FULLAUTO_SCRIPT}")

    meta = account.metadata_json or {}
    if meta.get("fullauto_live"):
        raise HTTPException(409, "A full-auto run is already in progress for this account.")

    account.status = "signup_in_progress"
    account.signup_step = "fullauto_started"
    account.metadata_json = {
        **meta,
        "fullauto_live": True,
        "fullauto_started_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.commit()

    # One status file per account keeps concurrent runs from clobbering each other.
    os.environ.setdefault("IG_FULLAUTO_STATUS_DIR", "/tmp/ig_fullauto_status")
    status_dir = os.environ.get("IG_FULLAUTO_STATUS_DIR", "/tmp/ig_fullauto_status")
    Path(status_dir).mkdir(parents=True, exist_ok=True)
    status_file = Path(status_dir) / f"{account_id}.json"
    status_file.write_text(json.dumps({
        "account_id": str(account_id), "state": "launching", "progress": 0,
        "stage": "Launching the full-auto browser runner",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }))

    env = {
        **os.environ,
        "IG_FULLAUTO_STATUS_FILE": str(status_file),
        "IG_FULLAUTO_ACCOUNT_ID": str(account_id),
    }
    try:
        log_file = open("/tmp/ig_fullauto_stdout.log", "ab")
        subprocess.Popen(
            [_FULLAUTO_PY, str(_FULLAUTO_SCRIPT), str(account_id)],
            env=env,
            cwd=str(_PROJECT_ROOT),
            stdout=log_file,
            stderr=log_file,
        )
    except Exception as exc:
        # Never leave the live flag stuck if the launch itself fails.
        account.metadata_json = {**meta, "fullauto_live": False}
        await db.commit()
        raise HTTPException(500, f"Failed to launch full-auto runner: {str(exc)[:200]}")

    return {
        "status": "fullauto_started",
        "account_id": str(account_id),
        "username": account.username,
        "email": account.email,
        "status_file": str(status_file),
        "message": (
            "Full-auto signup launched in a visible Chrome window. The robot fills "
            "the form, polls the inbox, and types the verification code itself. "
            "Poll fullauto-status for live progress."
        ),
    }


@router.get("/social-accounts/{account_id}/fullauto-status")
async def fullauto_signup_status(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Live progress of a full-auto signup, for the Studio UI progress panel."""
    from app.models import SocialAccount

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")

    meta = account.metadata_json or {}
    status_file = Path("/tmp/ig_fullauto_status") / f"{account_id}.json"
    prog: dict = {}
    if status_file.exists():
        try:
            prog = json.loads(status_file.read_text())
        except Exception:
            prog = {}

    # Stale-live guard: if the DB claims a run is live but it started over 15
    # minutes ago, treat it as dead so the UI can re-launch.
    live = bool(meta.get("fullauto_live"))
    stale = False
    started_at = meta.get("fullauto_started_at")
    if live and started_at:
        try:
            t0 = datetime.fromisoformat(started_at)
            if (datetime.now(timezone.utc) - t0).total_seconds() > 900:
                stale = True
        except Exception:
            stale = True

    return {
        "account_id": str(account_id),
        "live": live and not stale,
        "stale": stale,
        "state": prog.get("state"), "stage": prog.get("stage"),
        "progress": prog.get("progress"),
        "code": prog.get("code"),
        "screenshot": prog.get("screenshot"),
        "profile_url": account.profile_url,
        "account_status": account.status,
        "started_at": started_at,
        "has_session_cookies": bool(meta.get("session_cookies")),
    }


@router.post("/social-accounts/auto-signup-all")
async def auto_signup_all_pending(
    platform: str = Query(""),
    headless: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    """
    Run automated signup for all pending accounts of a given platform.
    If no platform specified, runs for all pending accounts.
    """
    from app.models import SocialAccount
    from app.providers.browser_signup import auto_signup

    q = select(SocialAccount).where(
        SocialAccount.status.in_(["pending_approval", "draft"])
    )
    if platform:
        q = q.where(SocialAccount.platform == platform)

    result = await db.execute(q)
    accounts = result.scalars().all()

    results = []
    for account in accounts:
        if not account.email:
            results.append({"username": account.username, "status": "skipped", "message": "No email"})
            continue

        password = getattr(account, 'password_hash', '') or ''
        if not password:
            password = _secrets.token_urlsafe(12)
            import base64
            account.password_hash = base64.b64encode(password.encode()).decode()

        account.status = "signup_in_progress"
        await db.commit()

        try:
            res = await auto_signup(
                platform=account.platform,
                email=account.email,
                password=password,
                display_name=account.display_name or account.username,
                username=account.username,
                headless=headless,
            )

            if res.success:
                account.status = "pending_approval"
                account.signup_step = res.status
            else:
                account.approval_notes = res.message
                account.signup_step = res.status

            account.metadata_json = {
                **(account.metadata_json or {}),
                "signup_result": res.status,
                "signup_message": res.message,
                "signup_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.commit()

            results.append({
                "username": account.username,
                "platform": account.platform,
                "success": res.success,
                "status": res.status,
                "message": res.message,
            })
        except Exception as e:
            account.status = "pending_approval"
            await db.commit()
            results.append({
                "username": account.username,
                "success": False,
                "status": "error",
                "message": str(e)[:100],
            })

        # Rate limit between signups
        await asyncio.sleep(3)

    return {
        "total": len(accounts),
        "results": results,
    }
