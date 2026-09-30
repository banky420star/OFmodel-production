"""Who is allowed to see what.

`check_access` is the only function that answers that question, and it answers
from the entitlements table and the ledger — never from the cached counters on
`fans`. The cached `Fan.subscription_tier` and `ChatMessage.ppv_unlocked` exist
so the pre-existing operator dashboard keeps rendering; they are not authority.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entitlement, Product, Subscription, utcnow


async def grant_ppv(
    db: AsyncSession,
    *,
    user_id: str,
    product_id: str,
    transaction_id: str = "",
    expires_at: datetime | None = None,
) -> Entitlement:
    """Grant a pay-per-view entitlement. Idempotent per (user, product)."""
    existing = (
        await db.execute(
            select(Entitlement).where(
                Entitlement.user_id == user_id,
                Entitlement.product_id == product_id,
                Entitlement.kind == "ppv",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    entitlement = Entitlement(
        user_id=user_id,
        product_id=product_id,
        kind="ppv",
        source_transaction_id=str(transaction_id or ""),
        expires_at=expires_at,
    )
    db.add(entitlement)
    await db.flush()
    return entitlement


async def has_ppv(db: AsyncSession, user_id: str, product_id: str) -> bool:
    row = (
        await db.execute(
            select(Entitlement).where(
                Entitlement.user_id == user_id,
                Entitlement.product_id == product_id,
                Entitlement.kind == "ppv",
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    if row.expires_at is None:
        return True
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > utcnow()


async def active_subscription(db: AsyncSession, user_id: str) -> Subscription | None:
    """The best subscription this user currently holds, if any.

    Expiry is checked here rather than trusted from `status`, because nothing
    runs on a schedule to flip an expired subscription's status — a row can sit
    at "active" indefinitely with the clock past `expires_at`.
    """
    rows = (
        await db.execute(
            select(Subscription)
            .where(Subscription.user_id == user_id, Subscription.status == "active")
            .order_by(Subscription.plan_rank.desc())
        )
    ).scalars().all()

    now = utcnow()
    for row in rows:
        expires_at = row.expires_at
        if expires_at is None:
            return row
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at > now:
            return row
    return None


async def check_access(
    db: AsyncSession, *, user_id: str | None, product: Product
) -> tuple[bool, str]:
    """(allowed, reason). The reason travels into the access log either way."""
    if not user_id:
        return False, "not_signed_in"

    if await has_ppv(db, user_id, product.id):
        return True, "entitled"

    subscription = await active_subscription(db, user_id)
    if subscription is not None and product.min_tier_rank:
        if (subscription.plan_rank or 0) >= product.min_tier_rank:
            return True, "subscription"

    return False, "no_entitlement"
