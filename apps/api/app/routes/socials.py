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
from app.crypto import encrypt_value, decrypt_value
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
            SocialAccount.persona_id == UUID(persona_id),
            SocialAccount.platform == platform,
            SocialAccount.username == username,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Account @{username} on {platform} already exists for this persona")

    account = SocialAccount(
        id=uuid4(),
        persona_id=UUID(persona_id),
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

    account = await db.get(SocialAccount, account_id)
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

    account = await db.get(SocialAccount, account_id)
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

    account = await db.get(SocialAccount, account_id)
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

    account = await db.get(SocialAccount, account_id)
    if not account:
        raise HTTPException(404, "Account not found")

    persona = await db.get(Persona, account.persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    try:
        email_result = await create_temp_email(
            persona_name=persona.name,
            prefix=account.platform,
        )
    except Exception as e:
        raise HTTPException(502, f"Failed to create email: {e}")

    # Store email info in the account (password + JWT token encrypted at rest)
    account.email = email_result.address
    account.email_password = encrypt_value(email_result.password)
    account.email_token = encrypt_value(email_result.token)
    account.metadata_json = {
        **(account.metadata_json or {}),
        "email_account_id": email_result.account_id,
        "email_password": encrypt_value(email_result.password),
        "email_token": encrypt_value(email_result.token),
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

    # Stored encrypted (legacy plaintext/base64 values decrypt as-is)
    token = decrypt_value(account.email_token or '')
    if not token:
        meta = account.metadata_json or {}
        token = decrypt_value(meta.get("email_token", ""))
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

    account = await db.get(SocialAccount, account_id)
    if not account:
        raise HTTPException(404, "Account not found")

    persona = await db.get(Persona, account.persona_id)
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

    account = await db.get(SocialAccount, str(account_id))
    if not account:
        raise HTTPException(404, "Account not found")

    # Encrypted at rest (Fernet); decrypt_value on read, legacy values fall back.
    # Refuse rather than truncate: password_hash is String(512), so a ciphertext
    # longer than that would be lost (Postgres raises, SQLite silently truncates).
    encrypted = encrypt_value(platform_password)
    if len(encrypted) > 512:
        raise HTTPException(400, "Password too long to store encrypted (max ~200 characters)")
    account.password_hash = encrypted
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

    account = await db.get(SocialAccount, account_id)
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
        persona_id=account.persona_id,
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
        persona = await db.get(Persona, account.persona_id)
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

    account = await db.get(SocialAccount, account_id)
    if not account:
        raise HTTPException(404, "Account not found")

    result = await db.execute(
        select(ScheduledPost)
        .where(ScheduledPost.persona_id == account.persona_id)
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

    # Get or generate a password (stored encrypted; legacy values fall back)
    password = decrypt_value(account.password_hash or '')
    if not password:
        password = _secrets.token_urlsafe(12)
        account.password_hash = encrypt_value(password)

    # Update status
    account.status = "signup_in_progress"
    account.signup_step = "signup_started"
    await db.commit()

    # Run the browser automation
    try:
        result = await auto_signup(
            platform=account.platform,
            email=account.email,
            password=password,
            display_name=account.display_name or account.username,
            username=account.username,
            headless=headless,
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
        account.metadata_json = {
            **(account.metadata_json or {}),
            "signup_result": result.status,
            "signup_message": result.message,
            "signup_screenshot": result.screenshot_path,
            "signup_at": datetime.now(timezone.utc).isoformat(),
            # Session cookies encrypted at rest (JSON blob, first 5 cookies)
            "session_cookies": encrypt_value(json.dumps(
                result.session_cookies[:5] if result.session_cookies else []
            )),
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

        password = decrypt_value(account.password_hash or '')
        if not password:
            password = _secrets.token_urlsafe(12)
            account.password_hash = encrypt_value(password)

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
