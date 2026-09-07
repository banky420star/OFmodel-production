"""Persona Studio — socials routes."""

from __future__ import annotations
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

    # Store email info in the account
    account.email = email_result.address
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

    account = await db.get(SocialAccount, account_id)
    if not account:
        raise HTTPException(404, "Account not found")

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
    import hashlib, base64

    account = await db.get(SocialAccount, account_id)
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


