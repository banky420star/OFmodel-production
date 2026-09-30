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

    # Store email info in the account. The columns are the declared home for
    # these four values and were being left empty in favour of metadata_json, so
    # every reader that looked at `account.email_token` saw no inbox on an
    # account that plainly had one — the packet said "ready" while the roster
    # said "no inbox". Metadata is kept as well: rows written before this fix
    # still carry their token there, and readers accept either.
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


# ─── Manual signup packet ────────────────────────────────────────────
#
# Everything up to the signup form is prepared here — a real mail.tm inbox, a
# generated password, the display name and bio — and handed to a person who
# submits the form themselves. Nothing in this route contacts the platform, and
# nothing in it claims an account exists: `account.status` only changes when an
# operator moves it (approve → activate), never as a result of this packet.
#
# This is the supported path. Note that `POST /social-accounts/{id}/auto-signup`
# and `/auto-signup-all`, with `app/providers/browser_signup.py` behind them,
# still exist and *do* create real accounts (anti-automation flags, mail.tm
# inbox-code bypass) if called directly. They are not reachable from the web app
# and are refused by default — see `AUTO_SIGNUP_ENABLED` in `app/config.py`. Do
# not describe the app as unable to create accounts while those routes are
# registered; deleting them is a separate decision.

SIGNUP_URLS = {
    "instagram": "https://www.instagram.com/accounts/emailsignup/",
    "facebook": "https://www.facebook.com/reg/",
    "onlyfans": "https://onlyfans.com/sign-up",
    "tiktok": "https://www.tiktok.com/signup",
    "twitter": "https://x.com/i/flow/signup",
    "fanvue": "https://www.fanvue.com/signup",
    "fansly": "https://fansly.com/signup",
}


@router.get("/social-accounts/{account_id}/signup-packet")
async def get_signup_packet(account_id: UUID, db: AsyncSession = Depends(get_db)):
    """Everything a person needs to complete one platform signup by hand.

    Reuses the same real plumbing as the rest of the app: the mail.tm inbox from
    generate-email, and the credentials store. The password is returned in clear
    because a human has to type it into the platform's form — it is base64 in the
    database (see store_credentials), not encrypted, so this exposes nothing that
    a DB read would not.
    """
    import base64
    import secrets as _pw_secrets

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")

    persona = await db.get(Persona, UUID(str(account.persona_id)))
    meta = account.metadata_json or {}

    # Generate the password on first request so the packet is stable across
    # reloads — same base64 storage the credentials route writes.
    password = ""
    created_password = False
    if account.password_hash:
        try:
            password = base64.b64decode(account.password_hash.encode()).decode()
        except Exception:
            password = account.password_hash  # stored unencoded by an older path
    else:
        password = _pw_secrets.token_urlsafe(12)
        account.password_hash = base64.b64encode(password.encode()).decode()
        created_password = True

    # The inbox is optional: a human can also finish signup with their own email.
    inbox_address = account.email or ""
    inbox_password = meta.get("email_password", "") or ""
    has_inbox = bool(inbox_address and meta.get("email_token"))

    # Steps deliberately do NOT spell out either password. There are two
    # credentials in this packet — the new platform password and the mail.tm
    # inbox password — and both are already returned as their own fields, which
    # is where a UI should read them from. Inlining them here as well meant the
    # same secret appeared twice in every serialization of this response: any
    # log line, any packet dumped to a file, and any UI that renders `steps` as
    # text (which is what a step list is for) displayed a live credential on
    # screen. One field, one place to redact; a step list that reads as
    # instructions and carries no secrets.
    steps = [
        f"Open the signup page: {SIGNUP_URLS.get(account.platform, '(unknown platform)')}",
        (
            "Use the email address in the 'email' field"
            if inbox_address
            else "Use your own email address — no inbox is attached to this account"
        ),
        "Use the password in the 'password' field",
    ]
    if inbox_password:
        steps.append(
            "Read the verification mail by signing in to mail.tm with the "
            "'email' and 'email_password' fields"
        )
    else:
        steps.append("Read the verification mail in whatever inbox you used")
    steps.append(
        "After the account exists, click 'Save credentials', then approve and "
        "activate it here — the app never sets those states for you"
    )

    if created_password:
        await db.commit()

    return {
        "account_id": str(account.id),
        "persona_name": persona.name if persona else "",
        "platform": account.platform,
        "signup_url": SIGNUP_URLS.get(account.platform, ""),
        "manual": True,
        "username": account.username,
        "display_name": account.display_name or account.username,
        "bio": account.bio or "",
        "email": inbox_address,
        "email_password": inbox_password,
        "has_inbox": has_inbox,
        "password": password,
        "password_created_now": created_password,
        "status": account.status,
        "steps": steps,
        "note": (
            "Nothing here contacts {platform}. A person submits this form, and "
            "this packet is the supported way to do it — no part of this flow "
            "creates the account for you.".format(platform=account.platform)
        ),
    }


@router.post("/social-accounts/{account_id}/post")
async def post_to_social_account(
    account_id: UUID,
    content_pack_id: str = Query(...),
    caption: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    """Queue content from a content pack for a social account.

    This does not post. There is no platform client in this API — no Instagram
    `media_publish`, no TikTok `video.publish` (that scope is deliberately
    absent, see `providers/tiktok.py:35`) — so nothing here can transmit
    anything to anywhere.

    It used to answer `{"status": "posted"}` and bump `posts_count` and
    `last_posted_at` after writing a single local row. Those two fields read as
    delivery evidence and `posts_count` feeds the operator dashboard, so the
    write manufactured the proof of its own success. That is the same shape of
    fabrication `app/delivery.py` was written to remove for fan messages; this
    endpoint had not been given the same treatment.

    What it does now is write a `ScheduledPost` and say so. `posted_at`,
    `posts_count` and `last_posted_at` stay untouched until a real publish
    provider returns success — that is the only event that can move a row to
    `posted` and record a platform `post_url`.
    """
    from app.models import SocialAccount, ContentPack

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")
    if account.status != "active":
        raise HTTPException(400, f"Account is '{account.status}', must be active to post")

    pack = await db.get(ContentPack, UUID(content_pack_id))
    if not pack:
        raise HTTPException(404, "Content pack not found")

    # Store the scheduled post. A `post_payload` dict used to be assembled here
    # and discarded, which made the handler read as though it dispatched one.
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
    await db.commit()

    return {
        "status": "scheduled",
        "posted": False,
        "platform": account.platform,
        "username": account.username,
        "content_pack": pack.name,
        "post_id": str(scheduled.id),
        "note": (
            "Queued only. Nothing was transmitted — this API has no platform "
            "client, so no post exists anywhere until a real publish provider "
            "runs. `posts_count` and `last_posted_at` are unchanged."
        ),
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


@router.post("/social-accounts/{account_id}/auto-signup")
async def auto_signup_account(
    account_id: UUID,
    headless: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    """
    Automated browser signup for a social platform.

    REFUSED BY DEFAULT (AUTO_SIGNUP_ENABLED=false). This drives Playwright
    against a live platform using anti-automation flags and a mail.tm
    disposable-inbox code bypass, and it creates a real account when it
    succeeds — so it is gated rather than merely unused. Several user-facing
    strings in this app state that no process here creates an account; this
    refusal is what makes that true in the shipped configuration.

    The supported path is the manual signup packet. See `AUTO_SIGNUP_ENABLED`
    in app/config.py for what turning this on means.
    """
    from app.config import get_settings
    from app.models import SocialAccount

    if not get_settings().AUTO_SIGNUP_ENABLED:
        raise HTTPException(
            403,
            "Automated platform signup is disabled. It creates real accounts on "
            "real platforms; set AUTO_SIGNUP_ENABLED=true in .env to enable it "
            "deliberately. The supported path is the manual signup packet "
            "(GET /social-accounts/{id}/signup-packet).",
        )

    # Imported only past the guard: with signup disabled, the Playwright module
    # is never loaded at all.
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
            # Column or metadata: rows created before the write fixed above carry
            # their token only in metadata_json, and passing "" would have this
            # path hit the email-code wall on an account that has an inbox.
            email_token=account.email_token or (account.metadata_json or {}).get("email_token", ""),
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
            "session_cookies": result.session_cookies[:5] if result.session_cookies else [],
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


@router.post("/social-accounts/auto-signup-all")
async def auto_signup_all_pending(
    platform: str = Query(""),
    headless: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    """
    Run automated signup for all pending accounts of a given platform.

    REFUSED BY DEFAULT (AUTO_SIGNUP_ENABLED=false) for the same reason as the
    single-account route above — this is the larger-scale version of the same
    act, and the guard is checked before anything is read or written.
    """
    from app.config import get_settings
    from app.models import SocialAccount

    if not get_settings().AUTO_SIGNUP_ENABLED:
        raise HTTPException(
            403,
            "Automated platform signup is disabled. It creates real accounts on "
            "real platforms; set AUTO_SIGNUP_ENABLED=true in .env to enable it "
            "deliberately. The supported path is the manual signup packet "
            "(GET /social-accounts/{id}/signup-packet).",
        )

    # Imported only past the guard: with signup disabled, the Playwright module
    # is never loaded at all.
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
