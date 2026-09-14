"""Persona Studio — Fanvue Platform Manager routes (v0).

Compliance-gated monetization inventory: AI-disclosed platform accounts,
the PUBLIC/SUBSCRIBER/PREMIUM ladder, and shortage-driven shoot orders.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database import get_db
from app.models import PlatformAccount, ContentInventoryItem, ShootOrder, Persona
from app import platform as pm

router = APIRouter()


class PlatformAccountIn(BaseModel):
    persona_id: str
    platform: str = "fanvue"
    handle: str = ""
    subscription_price: float = Field(0.0, ge=0)
    is_ai_disclosed: bool = False
    ai_disclosure_text: str = ""
    kyc_status: str = "not_started"
    consent_owner: str = ""
    target_public_posts_per_day: float = Field(1.0, ge=0)
    target_subscriber_posts_per_day: float = Field(2.0, ge=0)
    target_premium_items_per_week: float = Field(3.0, ge=0)


class PlatformAccountPatch(BaseModel):
    handle: str | None = None
    status: str | None = None
    is_ai_disclosed: bool | None = None
    ai_disclosure_text: str | None = None
    kyc_status: str | None = None
    consent_owner: str | None = None
    subscription_price: float | None = None
    target_public_posts_per_day: float | None = None
    target_subscriber_posts_per_day: float | None = None
    target_premium_items_per_week: float | None = None


@router.post("/platform/accounts")
async def create_platform_account(body: PlatformAccountIn, db: AsyncSession = Depends(get_db)):
    """Register a monetization account for a persona (Fanvue-first)."""
    persona = await db.get(Persona, UUID(body.persona_id))
    if not persona:
        raise HTTPException(404, "Persona not found")
    if body.platform not in ("fanvue", "onlyfans", "fansly"):
        raise HTTPException(400, "platform must be one of: fanvue, onlyfans, fansly")

    acct = PlatformAccount(
        persona_id=persona.id,
        platform=body.platform,
        handle=body.handle or persona.name.lower(),
        subscription_price=body.subscription_price,
        is_ai_disclosed=body.is_ai_disclosed,
        ai_disclosure_text=body.ai_disclosure_text,
        kyc_status=body.kyc_status,
        consent_owner=body.consent_owner,
        target_public_posts_per_day=body.target_public_posts_per_day,
        target_subscriber_posts_per_day=body.target_subscriber_posts_per_day,
        target_premium_items_per_week=body.target_premium_items_per_week,
        status="onboarding" if body.platform == "fanvue" else "pending_setup",
    )
    # v0 convenience: default disclosure for fanvue accounts that did not set one.
    if body.platform == "fanvue" and not acct.ai_disclosure_text and acct.is_ai_disclosed:
        acct.ai_disclosure_text = pm.default_ai_disclosure(acct.handle)
    db.add(acct)
    await db.commit()
    await db.refresh(acct)

    return {
        "id": str(acct.id),
        "persona_id": str(acct.persona_id),
        "platform": acct.platform,
        "handle": acct.handle,
        "status": acct.status,
        "is_ai_disclosed": acct.is_ai_disclosed,
        "ai_disclosure_text": acct.ai_disclosure_text,
        "kyc_status": acct.kyc_status,
        "compliance": pm.check_compliance(acct),
        "message": (
            "Fanvue account created. Complete: AI disclosure, KYC, and consent owner "
            "to unlock inventory planning."
            if acct.platform == "fanvue" else
            f"{acct.platform} recorded. Planning is restricted for this platform."
        ),
    }


@router.get("/platform/accounts")
async def list_platform_accounts(platform: str | None = None, db: AsyncSession = Depends(get_db)):
    q = select(PlatformAccount).order_by(PlatformAccount.created_at.desc())
    if platform:
        q = q.where(PlatformAccount.platform == platform)
    res = await db.execute(q)
    out = []
    for a in res.scalars().all():
        out.append({
            "id": str(a.id),
            "persona_id": str(a.persona_id),
            "platform": a.platform,
            "handle": a.handle,
            "status": a.status,
            "is_ai_disclosed": a.is_ai_disclosed,
            "ai_disclosure_text": a.ai_disclosure_text,
            "kyc_status": a.kyc_status,
            "consent_owner": a.consent_owner,
            "subscription_price": a.subscription_price,
            "subscriber_count": a.subscriber_count,
            "targets": {
                "public_posts_per_day": a.target_public_posts_per_day,
                "subscriber_posts_per_day": a.target_subscriber_posts_per_day,
                "premium_items_per_week": a.target_premium_items_per_week,
            },
            "compliance": pm.check_compliance(a),
            "created_at": a.created_at.isoformat() if a.created_at else None,
        })
    return out


@router.get("/platform/accounts/{account_id}")
async def get_platform_account(account_id: UUID, db: AsyncSession = Depends(get_db)):
    acct = await db.get(PlatformAccount, account_id)
    if not acct:
        raise HTTPException(404, "Account not found")
    counts = await pm.inventory_counts(db, acct.id)
    gate = pm.check_compliance(acct)
    return {
        "id": str(acct.id),
        "persona_id": str(acct.persona_id),
        "platform": acct.platform,
        "handle": acct.handle,
        "status": acct.status,
        "is_ai_disclosed": acct.is_ai_disclosed,
        "ai_disclosure_text": acct.ai_disclosure_text,
        "kyc_status": acct.kyc_status,
        "consent_owner": acct.consent_owner,
        "subscription_price": acct.subscription_price,
        "subscriber_count": acct.subscriber_count,
        "targets": {
            "public_posts_per_day": acct.target_public_posts_per_day,
            "subscriber_posts_per_day": acct.target_subscriber_posts_per_day,
            "premium_items_per_week": acct.target_premium_items_per_week,
        },
        "compliance": gate,
        "inventory": counts,
        "restricted": acct.platform in pm.RESTRICTED_PLATFORMS,
    }


@router.patch("/platform/accounts/{account_id}")
async def patch_platform_account(account_id: UUID, body: PlatformAccountPatch, db: AsyncSession = Depends(get_db)):
    acct = await db.get(PlatformAccount, account_id)
    if not acct:
        raise HTTPException(404, "Account not found")
    data = body.model_dump(exclude_none=True)
    for k, v in data.items():
        setattr(acct, k, v)
    if acct.platform == "fanvue" and acct.is_ai_disclosed and not (acct.ai_disclosure_text or "").strip():
        acct.ai_disclosure_text = pm.default_ai_disclosure(acct.handle)
    await db.commit()
    return {
        "id": str(acct.id),
        "compliance": pm.check_compliance(acct),
        "message": "Updated.",
    }


@router.get("/platform/accounts/{account_id}/inventory")
async def get_inventory(account_id: UUID, tier: str | None = None, db: AsyncSession = Depends(get_db)):
    acct = await db.get(PlatformAccount, account_id)
    if not acct:
        raise HTTPException(404, "Account not found")
    q = (
        select(ContentInventoryItem)
        .where(ContentInventoryItem.account_id == account_id)
        .order_by(ContentInventoryItem.created_at.desc())
    )
    if tier:
        q = q.where(ContentInventoryItem.tier == tier)
    res = await db.execute(q)
    return [
        {
            "id": str(i.id),
            "tier": i.tier,
            "content_type": i.content_type,
            "asset_key": i.asset_key,
            "source_type": i.source_type,
            "is_mock": i.is_mock,
            "caption": i.caption,
            "status": i.status,
            "posted_at": i.posted_at.isoformat() if i.posted_at else None,
            "shoot_id": str(i.shoot_id) if i.shoot_id else None,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }
        for i in res.scalars().all()
    ]


class InventoryItemIn(BaseModel):
    tier: str = Field(..., pattern="^(public|subscriber|premium)$")
    content_type: str = Field("image", pattern="^(image|video)$")
    asset_key: str = ""
    source_type: str = Field("generated", pattern="^(generated|uploaded)$")
    is_mock: bool = False
    caption: str = ""


@router.post("/platform/accounts/{account_id}/inventory")
async def add_inventory_item(account_id: UUID, body: InventoryItemIn, db: AsyncSession = Depends(get_db)):
    acct = await db.get(PlatformAccount, account_id)
    if not acct:
        raise HTTPException(404, "Account not found")
    item = ContentInventoryItem(
        account_id=acct.id,
        persona_id=acct.persona_id,
        tier=body.tier,
        content_type=body.content_type,
        asset_key=body.asset_key,
        source_type=body.source_type,
        is_mock=body.is_mock,
        caption=body.caption,
        status="ready",
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return {"id": str(item.id), "tier": item.tier, "status": item.status, "is_mock": item.is_mock}


@router.post("/platform/accounts/{account_id}/inventory/sync-gallery")
async def sync_gallery(account_id: UUID, db: AsyncSession = Depends(get_db)):
    """Register the persona's real generated images as ready PUBLIC inventory."""
    acct = await db.get(PlatformAccount, account_id)
    if not acct:
        raise HTTPException(404, "Account not found")
    try:
        result = await pm.sync_gallery_to_inventory(db, acct.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await db.commit()
    return result


@router.post("/platform/inventory/plan")
async def plan_inventory(account_id: UUID = Query(...), db: AsyncSession = Depends(get_db)):
    """Shortage-driven planning: opens ShootOrders for tiers below minimum.
    Compliance-gated; dedupes against already-open orders."""
    try:
        result = await pm.plan_inventory(db, account_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    await db.commit()
    return result


@router.get("/platform/accounts/{account_id}/orders")
async def list_orders(account_id: UUID, status: str | None = None, db: AsyncSession = Depends(get_db)):
    q = (
        select(ShootOrder)
        .where(ShootOrder.account_id == account_id)
        .order_by(ShootOrder.created_at.desc())
    )
    if status:
        q = q.where(ShootOrder.status == status)
    res = await db.execute(q)
    return [
        {
            "id": str(o.id),
            "tier": o.tier,
            "content_type": o.content_type,
            "units_requested": o.units_requested,
            "units_fulfilled": o.units_fulfilled,
            "reason": o.reason,
            "status": o.status,
            "shoot_id": str(o.shoot_id) if o.shoot_id else None,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in res.scalars().all()
    ]


@router.post("/platform/inventory/{item_id}/post")
async def post_inventory_item(item_id: UUID, db: AsyncSession = Depends(get_db)):
    """Register a real publish of a ready item (permitted workflow)."""
    try:
        result = await pm.register_post(db, item_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await db.commit()
    return result
