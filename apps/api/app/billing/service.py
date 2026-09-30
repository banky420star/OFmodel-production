"""The four money operations, each one transaction.

The shape is the same every time, and the order matters:

    processor  →  payment row  →  ledger posting  →  entitlement  →  cache  →  audit

Everything happens on one session and commits once, so there is no state in
which a fan has been charged with nothing granted, or granted something with no
posting behind it. If any step raises, the whole thing rolls back.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import audit
from app.billing.entitlements import grant_ppv
from app.billing.ledger import (
    SYS_CLEARING, SYS_PROMO, SYS_REVENUE_PPV, SYS_REVENUE_SUBSCRIPTION,
    Entry, LedgerError, post_transaction, user_available,
)
from app.billing.processor import get_processor
from app.models import Payment, Product, Subscription, SubscriptionPlan, utcnow


class MoneyError(Exception):
    """A money operation that could not complete. Nothing was written."""

    def __init__(self, message: str, *, code: str = "error"):
        self.code = code
        super().__init__(message)


async def grant_signup_credit(
    db: AsyncSession, *, user_id: str, amount_minor: int
) -> dict:
    """The promotional balance a new account starts with."""
    if amount_minor <= 0:
        return {"granted": 0, "transaction_id": ""}

    txn, _ = await post_transaction(
        db,
        kind="signup_grant",
        entries=[
            Entry(SYS_PROMO, "debit", amount_minor),
            Entry(user_available(user_id), "credit", amount_minor),
        ],
        idempotency_key=f"signup_grant:{user_id}",
        memo="Signup promotional credit (simulated)",
    )
    await audit(
        db, "signup_grant", user_id=user_id, actor="system",
        object_type="ledger_transaction", object_id=txn.id,
        detail={"amount_minor": amount_minor, "simulated": True},
    )
    return {"granted": amount_minor, "transaction_id": txn.id}


async def topup(db: AsyncSession, *, user_id: str, amount_minor: int) -> dict:
    """Simulated deposit: processor → payment row → ledger → wallet."""
    if not isinstance(amount_minor, int) or amount_minor <= 0:
        raise MoneyError("amount must be a positive whole number of cents")
    if amount_minor > 1_000_00:
        # Not a real limit, a guard: this is a simulated processor and a typo of
        # an extra zero should be refused rather than quietly booked.
        raise MoneyError("simulated top-ups are capped at 1000.00")

    processor = get_processor()
    charge = await processor.charge(
        amount_minor=amount_minor,
        currency="USD",
        user_id=user_id,
        kind="topup",
        reference=f"topup:{user_id}",
    )

    payment = Payment(
        user_id=user_id,
        processor=processor.name,
        processor_ref=charge.processor_ref,
        amount_minor=amount_minor,
        kind="topup",
        status="succeeded" if charge.ok else "declined",
        failure_reason=charge.failure_reason,
    )
    db.add(payment)
    await db.flush()

    if not charge.ok:
        await audit(
            db, "topup_declined", user_id=user_id, actor="user",
            object_type="payment", object_id=payment.id,
            detail={"amount_minor": amount_minor, "reason": charge.failure_reason},
        )
        raise MoneyError(charge.failure_reason or "payment declined", code="declined")

    txn, created = await post_transaction(
        db,
        kind="topup",
        entries=[
            Entry(SYS_CLEARING, "debit", amount_minor),
            Entry(user_available(user_id), "credit", amount_minor),
        ],
        # Keyed on the payment row, not the amount: topping up 500 twice in a
        # row is two legitimate deposits, but retrying the *same* charge is not.
        idempotency_key=f"topup:{payment.id}",
        memo="Simulated top-up",
        metadata={"payment_id": payment.id, "simulated": True},
    )

    await audit(
        db, "topup", user_id=user_id, actor="user",
        object_type="ledger_transaction", object_id=txn.id,
        detail={"amount_minor": amount_minor, "simulated": True, "created": created},
    )
    return {
        "amount_minor": amount_minor,
        "processor_ref": charge.processor_ref,
        "transaction_id": txn.id,
        "simulated": True,
    }


async def unlock_ppv(
    db: AsyncSession, *, user_id: str, product: Product, fan_id: str | None = None
) -> dict:
    """Buy one product. Charged once, no matter how many times it is clicked."""
    key = f"ppv:{user_id}:{product.id}"

    existing = (
        await db.execute(
            text("SELECT id FROM ledger_transactions WHERE idempotency_key = :k"),
            {"k": key},
        )
    ).fetchone()
    if existing is not None:
        # Already bought. Return the same shape without touching the balance.
        return {
            "already_owned": True,
            "product_id": product.id,
            "price_minor": 0,
            "transaction_id": existing[0],
            "simulated": True,
        }

    if product.price_minor <= 0:
        raise MoneyError("this product has no price", code="not_for_sale")

    balance = await _balance(db, user_id)
    if balance < product.price_minor:
        raise MoneyError(
            f"not enough balance: {balance} available, {product.price_minor} needed",
            code="insufficient_funds",
        )

    txn, _ = await post_transaction(
        db,
        kind="ppv_unlock",
        entries=[
            Entry(user_available(user_id), "debit", product.price_minor),
            Entry(SYS_REVENUE_PPV, "credit", product.price_minor),
        ],
        idempotency_key=key,
        memo=f"Unlock: {product.title}",
        metadata={"product_id": product.id, "simulated": True},
    )

    await grant_ppv(
        db, user_id=user_id, product_id=product.id, transaction_id=txn.id
    )
    await _bump_fan_cache(db, fan_id, product)
    await audit(
        db, "ppv_unlock", user_id=user_id, actor="user",
        object_type="product", object_id=product.id,
        detail={"price_minor": product.price_minor, "simulated": True},
    )

    return {
        "already_owned": False,
        "product_id": product.id,
        "price_minor": product.price_minor,
        "transaction_id": txn.id,
        "balance_minor": await _balance(db, user_id),
        "simulated": True,
    }


async def subscribe(
    db: AsyncSession, *, user_id: str, plan: SubscriptionPlan, fan_id: str | None = None
) -> dict:
    """Start (or restart) a subscription on a plan."""
    processor = get_processor()
    charge = await processor.charge(
        amount_minor=plan.price_minor,
        currency="USD",
        user_id=user_id,
        kind="subscription",
        reference=f"sub:{user_id}:{plan.code}",
    )

    payment = Payment(
        user_id=user_id,
        processor=processor.name,
        processor_ref=charge.processor_ref,
        amount_minor=plan.price_minor,
        kind="subscription",
        status="succeeded" if charge.ok else "declined",
        failure_reason=charge.failure_reason,
    )
    db.add(payment)
    await db.flush()

    if not charge.ok:
        await audit(
            db, "subscription_declined", user_id=user_id, actor="user",
            object_type="payment", object_id=payment.id,
            detail={"plan_code": plan.code, "reason": charge.failure_reason},
        )
        raise MoneyError(charge.failure_reason or "payment declined", code="declined")

    if plan.price_minor > 0:
        balance = await _balance(db, user_id)
        if balance < plan.price_minor:
            raise MoneyError(
                f"not enough balance: {balance} available, {plan.price_minor} needed",
                code="insufficient_funds",
            )
        txn, _ = await post_transaction(
            db,
            kind="subscription",
            entries=[
                Entry(user_available(user_id), "debit", plan.price_minor),
                Entry(SYS_REVENUE_SUBSCRIPTION, "credit", plan.price_minor),
            ],
            idempotency_key=f"sub:{payment.id}",
            memo=f"Subscription: {plan.name}",
            metadata={"plan_code": plan.code, "simulated": True},
        )
    else:
        txn = None

    expires_at = utcnow() + timedelta(days=plan.period_days or 30)
    subscription = Subscription(
        user_id=user_id,
        fan_id=fan_id,
        plan_code=plan.code,
        plan_rank=plan.rank,
        status="active",
        expires_at=expires_at,
    )
    db.add(subscription)
    await db.flush()

    await _set_fan_tier(db, fan_id, plan)
    await audit(
        db, "subscribe", user_id=user_id, actor="user",
        object_type="subscription_plan", object_id=plan.code,
        detail={"price_minor": plan.price_minor, "rank": plan.rank,
                "simulated": True, "expires_at": expires_at.isoformat()},
    )

    return {
        "plan_code": plan.code,
        "plan_rank": plan.rank,
        "price_minor": plan.price_minor,
        "expires_at": expires_at.isoformat(),
        "transaction_id": txn.id if txn is not None else "",
        "balance_minor": await _balance(db, user_id),
        "simulated": True,
    }


# ── helpers ───────────────────────────────────────────────────────────

async def _balance(db: AsyncSession, user_id: str) -> int:
    from app.models import Wallet

    wallet = (
        await db.execute(select(Wallet).where(Wallet.user_id == user_id))
    ).scalar_one_or_none()
    return (wallet.balance_minor or 0) if wallet else 0


async def _bump_fan_cache(db: AsyncSession, fan_id: str | None, product: Product) -> None:
    """Update the legacy counters on `fans` that the operator dashboard reads.

    These are derived values, written only here and only alongside the ledger
    entry that justifies them. Nothing gates access on them.
    """
    if not fan_id:
        return
    await db.execute(
        text(
            "UPDATE fans SET total_spent = COALESCE(total_spent, 0) + :amt, "
            "ppv_purchases = COALESCE(ppv_purchases, 0) + 1, updated_at = :now "
            "WHERE id = :fid"
        ),
        {"amt": product.price_minor / 100.0, "now": utcnow(), "fid": fan_id},
    )


async def _set_fan_tier(db: AsyncSession, fan_id: str | None, plan: SubscriptionPlan) -> None:
    if not fan_id:
        return
    await db.execute(
        text("UPDATE fans SET subscription_tier = :tier, updated_at = :now WHERE id = :fid"),
        {"tier": plan.code, "now": utcnow(), "fid": fan_id},
    )
