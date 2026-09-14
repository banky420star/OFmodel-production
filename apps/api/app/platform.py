"""Fanvue Platform Manager v0 — inventory ladder + shortage-driven planning.

Per ARCHITECTURE_VISION.md invariant 3 (Fanvue-first) and the content-tier
ladder: PUBLIC / SUBSCRIBER / PREMIUM. The planner compares READY inventory
per tier against content-type-aware minimums derived from the account's
posting targets, and opens ShootOrders for shortages. Compliance is a gate,
not a suggestion: no plan runs without AI disclosure + verified KYC.

Honesty rules carried over from the rest of the system:
- inventory counts are real rows, not targets;
- is_mock provenance propagates from generation into inventory;
- OnlyFans accounts are recorded but planning returns `restricted` — that
  subsystem is out of scope until its own permitted workflows exist.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlatformAccount, ContentInventoryItem, ShootOrder, Persona

logger = logging.getLogger(__name__)

TIERS = ("public", "subscriber", "premium")
CONTENT_TYPES = ("image", "video")

RESTRICTED_PLATFORMS = {"onlyfans"}  # planned separately; no automation here


class ComplianceError(Exception):
    """Raised when planning is attempted on a non-compliant account."""


def _min_for(tier: str, content_type: str, acct: PlatformAccount) -> int:
    """Ready-units minimum before the planner orders more. Derived from the
    account's own cadence targets — content-type-aware (video needs a smaller
    buffer because it's slower to produce and post)."""
    if tier == "public":
        if content_type == "image":
            return max(2, int(round((acct.target_public_posts_per_day or 1.0) * 2)))
        return 1
    if tier == "subscriber":
        if content_type == "image":
            return max(2, int(round((acct.target_subscriber_posts_per_day or 2.0) * 2)))
        return 1
    # premium
    if content_type == "image":
        return 5
    return max(1, int(round((acct.target_premium_items_per_week or 3.0) / 2)))


def _shortage(ready: int, minimum: int) -> int:
    return max(0, minimum - ready)


def _aid(account_id) -> _uuid.UUID:
    """Normalize account ids: UUID columns need UUID values, not str."""
    return account_id if isinstance(account_id, _uuid.UUID) else _uuid.UUID(str(account_id))


async def inventory_counts(db: AsyncSession, account_id) -> dict[str, dict[str, int]]:
    """Real ready/posted counts per tier × content_type."""
    aid = _aid(account_id)
    res = await db.execute(
        select(
            ContentInventoryItem.tier,
            ContentInventoryItem.content_type,
            ContentInventoryItem.status,
            func.count(ContentInventoryItem.id),
        )
        .where(ContentInventoryItem.account_id == aid)
        .group_by(ContentInventoryItem.tier, ContentInventoryItem.content_type, ContentInventoryItem.status)
    )
    counts: dict[str, dict[str, int]] = {t: {c: 0 for c in CONTENT_TYPES} for t in TIERS}
    posted: dict[str, dict[str, int]] = {t: {c: 0 for c in CONTENT_TYPES} for t in TIERS}
    for tier, ctype, status, n in res.all():
        if tier in counts and ctype in counts[tier]:
            if status == "ready":
                counts[tier][ctype] = n
            elif status == "posted":
                posted[tier][ctype] = n
    return {"ready": counts, "posted": posted}


def check_compliance(acct: PlatformAccount) -> dict[str, Any]:
    """Fanvue AI-creator gate: disclosure + KYC + an operator of record."""
    problems = []
    if not acct.is_ai_disclosed:
        problems.append("AI-creator disclosure is OFF — Fanvue requires AI content to be disclosed")
    if not (acct.ai_disclosure_text or "").strip():
        problems.append("No AI-disclosure text set for the profile")
    if acct.kyc_status != "verified":
        problems.append(f"KYC status is '{acct.kyc_status}' — creator must be identity-verified")
    if not (acct.consent_owner or "").strip():
        problems.append("No consent_owner (verified human operating this account) recorded")
    return {"compliant": not problems, "problems": problems}


async def open_orders(db: AsyncSession, account_id) -> list[ShootOrder]:
    aid = _aid(account_id)
    res = await db.execute(
        select(ShootOrder)
        .where(ShootOrder.account_id == aid)
        .where(ShootOrder.status.in_(["open", "queued"]))
        .order_by(ShootOrder.created_at.desc())
    )
    return list(res.scalars().all())


async def plan_inventory(db: AsyncSession, account_id) -> dict[str, Any]:
    """Compute shortages for every tier × content_type and open ShootOrders.

    Dedupe: a cell with an existing open/queued order is skipped (the order
    already covers the shortage). Returns the full plan either way — planning
    is read-only except for the orders it creates.
    """
    aid = _aid(account_id)
    acct = await db.get(PlatformAccount, aid)
    if not acct:
        raise ValueError("account not found")

    if acct.platform in RESTRICTED_PLATFORMS:
        return {
            "account_id": str(account_id),
            "platform": acct.platform,
            "planning_allowed": False,
            "restricted": True,
            "reason": (
                f"{acct.platform} is a restricted verified-creator surface: planning/automation "
                "is not built for it (no crawling, no automation beyond permitted workflows). "
                "Use Fanvue for AI-creator monetization."
            ),
        }

    gate = check_compliance(acct)
    if not gate["compliant"]:
        return {
            "account_id": str(account_id),
            "platform": acct.platform,
            "planning_allowed": False,
            "restricted": False,
            "compliance": gate,
            "reason": "Compliance gate: fix the listed items before inventory planning runs.",
        }

    counts = await inventory_counts(db, account_id)
    existing = await open_orders(db, account_id)
    covered = {(o.tier, o.content_type) for o in existing}

    plan_rows = []
    orders_created: list[ShootOrder] = []
    for tier in TIERS:
        for ctype in CONTENT_TYPES:
            minimum = _min_for(tier, ctype, acct)
            ready = counts["ready"][tier][ctype]
            shortage = _shortage(ready, minimum)
            has_order = (tier, ctype) in covered
            row = {
                "tier": tier,
                "content_type": ctype,
                "ready": ready,
                "minimum": minimum,
                "shortage": shortage,
                "open_order_exists": has_order,
            }
            if shortage > 0 and not has_order:
                order = ShootOrder(
                    account_id=aid,
                    persona_id=acct.persona_id,
                    tier=tier,
                    content_type=ctype,
                    units_requested=shortage,
                    reason=(
                        f"{tier}/{ctype} inventory {ready} below minimum {minimum} "
                        f"(targets: public {acct.target_public_posts_per_day}/day, "
                        f"subscriber {acct.target_subscriber_posts_per_day}/day, "
                        f"premium {acct.target_premium_items_per_week}/week)"
                    ),
                    status="open",
                )
                db.add(order)
                orders_created.append(order)
                row["order_created"] = True
            plan_rows.append(row)

    if orders_created:
        await db.flush()

    return {
        "account_id": str(account_id),
        "platform": acct.platform,
        "planning_allowed": True,
        "restricted": False,
        "compliance": gate,
        "targets": {
            "public_posts_per_day": acct.target_public_posts_per_day,
            "subscriber_posts_per_day": acct.target_subscriber_posts_per_day,
            "premium_items_per_week": acct.target_premium_items_per_week,
        },
        "cells": plan_rows,
        "orders_created": [
            {
                "id": str(o.id), "tier": o.tier, "content_type": o.content_type,
                "units_requested": o.units_requested, "reason": o.reason,
            }
            for o in orders_created
        ],
        "posted": counts["posted"],
    }


def default_ai_disclosure(handle: str) -> str:
    return (
        f"{handle} is a virtual AI-generated creator. All content on this profile "
        "is AI-generated and disclosed as such in line with platform AI-content rules."
    )


async def sync_gallery_to_inventory(db: AsyncSession, account_id) -> dict[str, Any]:
    """Register the persona's real generated gallery/avatar images as ready
    PUBLIC inventory. Dedupe by asset_key — re-syncs add nothing new. Existing
    items are never duplicated or destroyed."""
    from pathlib import Path

    acct = await db.get(PlatformAccount, _aid(account_id))
    if not acct:
        raise ValueError("account not found")
    persona = await db.get(Persona, acct.persona_id)
    if not persona:
        raise ValueError("persona not found")

    storage = Path(__file__).resolve().parent.parent / "storage"
    candidates: list[tuple[str, str]] = []  # (asset_key, label)

    avatar = storage / "avatars" / f"{persona.name.lower()}.jpg"
    if avatar.exists():
        candidates.append((f"/api/v1/avatars/{avatar.name}", "Profile"))

    gallery_dir = storage / "gallery"
    if gallery_dir.exists():
        for f in sorted(gallery_dir.glob(f"{persona.name.lower()}_*.png")):
            candidates.append((f"/api/v1/gallery/{f.name}", f.stem))

    existing_res = await db.execute(
        select(ContentInventoryItem.asset_key).where(ContentInventoryItem.account_id == _aid(account_id))
    )
    existing = set(existing_res.scalars().all())

    added = 0
    for key, label in candidates:
        if key in existing:
            continue
        db.add(ContentInventoryItem(
            account_id=_aid(account_id),
            persona_id=acct.persona_id,
            tier="public",
            content_type="image",
            asset_key=key,
            source_type="generated",
            is_mock=False,
            caption=label,
            status="ready",
            metadata_json={"synced_from": "gallery", "synced_at": datetime.now(timezone.utc).isoformat()},
        ))
        added += 1

    await db.flush()
    return {
        "account_id": str(account_id),
        "persona": persona.name,
        "found": len(candidates),
        "added": added,
        "already_registered": len(candidates) - added,
    }


async def register_post(db: AsyncSession, item_id) -> dict[str, Any]:
    """Mark a ready item posted (the manual/permitted publish action)."""
    item = await db.get(ContentInventoryItem, _aid(item_id))
    if not item:
        raise ValueError("inventory item not found")
    if item.status == "posted":
        raise ValueError("item already posted")
    item.status = "posted"
    item.posted_at = datetime.now(timezone.utc)
    await db.flush()
    counts = await inventory_counts(db, item.account_id)
    shortage_now = {
        t: _shortage(counts["ready"][t][c], 0) for t in TIERS for c in CONTENT_TYPES
    }
    return {
        "item_id": str(item.id),
        "status": item.status,
        "posted_at": item.posted_at.isoformat(),
        "ready_counts": counts["ready"],
        "note": "Run inventory planning to open orders for any tier now below minimum.",
    }


async def fulfil_order_from_shoot(db: AsyncSession, order_id, shoot_id) -> dict[str, Any]:
    """When a queued order's shoot produces accepted images, they enter the
    ladder via inventory registration; this link marks fulfilment progress."""
    order = await db.get(ShootOrder, str(order_id))
    if not order:
        raise ValueError("order not found")
    order.shoot_id = str(shoot_id)
    order.status = "queued"
    await db.flush()
    return {"order_id": str(order.id), "status": order.status, "shoot_id": str(shoot_id)}
