"""Persona Studio — Social worker + official-API connection routes.

/status       — capability matrix + per-account API state (drives the UI panel)
/run-once     — execute one worker pass NOW (same code the loop runs)
/connect-api  — store an official-API credential on an account row
/disconnect   — remove the stored credential
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app import social_worker

logger = logging.getLogger("persona.social_worker")

router = APIRouter(prefix="/social-worker", tags=["social-worker"])


@router.get("/status")
async def worker_status(db: AsyncSession = Depends(get_db)):
    """Everything the UI panel needs: platform capability matrix + real
    per-account API state."""
    return await social_worker.status(db)


@router.post("/run-once")
async def run_once(db: AsyncSession = Depends(get_db)):
    """One worker pass right now — the exact code the always-on loop runs.
    Returns the pass ledger (what was attempted, what actually succeeded)."""
    summary = await social_worker.worker_pass(db)
    return summary


class ConnectApiIn(BaseModel):
    platform: str = Field(min_length=1)
    username: str = Field(min_length=1)
    token: str = Field(min_length=8, description="Official-API credential for this platform")
    token_kind: str = Field(default="oauth_access_token")


@router.post("/connect-api")
async def connect_api(body: ConnectApiIn, db: AsyncSession = Depends(get_db)):
    """Store an official-API credential on an account row.

    Only platforms with an official API are accepted — Fansly / OnlyFans are
    refused explicitly (no client exists, by policy). The token is stored on
    the account row the same way the existing socials routes store tokens;
    the worker validates it against the real platform immediately and records
    the honest result.
    """
    from sqlalchemy import select
    from app.models import SocialAccount
    from app.providers.social_apis import PLATFORM_CAPABILITIES

    platform = body.platform.lower().strip()
    cap = PLATFORM_CAPABILITIES.get(platform)
    if not cap or not cap.get("has_official_api"):
        raise HTTPException(
            400,
            detail=f"'{body.platform}' has no official API — the Studio does not integrate it. "
                   f"Platforms with official APIs: "
                   f"{', '.join(k for k, v in PLATFORM_CAPABILITIES.items() if v.get('has_official_api'))}",
        )

    res = await db.execute(
        select(SocialAccount)
        .where(SocialAccount.platform == platform)
        .where(SocialAccount.username == body.username)
    )
    account = res.scalar_one_or_none()
    if not account:
        raise HTTPException(404, detail=f"no {platform} account with username '{body.username}'")

    account.api_token = body.token
    account.metadata_json = {
        **(account.metadata_json or {}),
        "api_token_kind": body.token_kind,
        "api_platform": platform,
    }

    # Immediately validate against the real platform — honesty over optimism.
    client = social_worker.platform_client(account)
    status = await client.check() if client else None
    if status and status.ok:
        account.api_connected = True
    elif status:
        account.api_connected = False

    await db.commit()

    return {
        "account": account.username,
        "platform": platform,
        "connected": bool(status and status.ok),
        "validation": None if status is None else {
            "ok": status.ok,
            "status_kind": status.status_kind,
            "detail": status.detail,
            "http_status": status.http_status,
        },
    }


@router.post("/{account_id}/disconnect")
async def disconnect_api(account_id: str, db: AsyncSession = Depends(get_db)):
    """Remove the stored credential and mark the account not API-connected."""
    from app.models import SocialAccount

    account = await db.get(SocialAccount, account_id)
    if not account:
        raise HTTPException(404, detail="account not found")
    account.api_token = ""
    account.api_connected = False
    account.metadata_json = {
        **(account.metadata_json or {}),
        "api_token_kind": "",
        "last_sync_result": {"ok": False, "detail": "disconnected by operator",
                             "at": __import__("datetime").datetime.now(
                                 __import__("datetime").timezone.utc).isoformat()},
    }
    await db.commit()
    return {"account": account.username, "connected": False}
