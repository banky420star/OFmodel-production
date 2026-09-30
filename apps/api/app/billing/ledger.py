"""The double-entry ledger.

Every movement of simulated money is a transaction with two or more entries,
and the invariant is checked before anything is written:

    sum(debits) == sum(credits)

Sign convention, stated once so nothing downstream has to guess:
`user:<id>:available` is a **liability of the platform to the user**. A credit
increases what they can spend; a debit decreases it. So the wallet balance is

    balance = sum(credits) - sum(debits)   on that account

which is the same expression `wallet_balance_from_ledger` uses to recompute the
balance from the entries — that is the point. `wallets.balance_minor` is a
cache of this number, and `GET /system/ledger/integrity` proves the two agree.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    LedgerAccount, LedgerEntry, LedgerTransaction, Wallet, utcnow,
)

# System account codes. They exist so every posting balances; nothing reads a
# balance from them.
SYS_PROMO = "sys:promo_expense"
SYS_CLEARING = "sys:processor_clearing"
SYS_REVENUE_PPV = "sys:revenue:ppv"
SYS_REVENUE_SUBSCRIPTION = "sys:revenue:subscription"

_SYSTEM_LABELS = {
    SYS_PROMO: "Promotional credit given away",
    SYS_CLEARING: "Money received from the processor, not yet recognised",
    SYS_REVENUE_PPV: "Revenue from pay-per-view unlocks",
    SYS_REVENUE_SUBSCRIPTION: "Revenue from subscriptions",
}


class LedgerError(Exception):
    """Raised for a structurally invalid posting. Nothing is written."""


@dataclass(frozen=True)
class Entry:
    account: str
    direction: str   # "debit" | "credit"
    amount_minor: int


def user_available(user_id: str) -> str:
    return f"user:{user_id}:available"


def _user_id_from_account(code: str) -> str | None:
    parts = code.split(":")
    if len(parts) == 3 and parts[0] == "user" and parts[2] == "available":
        return parts[1]
    return None


async def ensure_account(db: AsyncSession, code: str, *, label: str = "") -> None:
    """Create a ledger account if it does not exist. Idempotent."""
    exists = (
        await db.execute(select(LedgerAccount.id).where(LedgerAccount.code == code))
    ).scalar_one_or_none()
    if exists is not None:
        return
    kind = "system" if code.startswith("sys:") else "user"
    db.add(LedgerAccount(
        code=code,
        kind=kind,
        label=label or _SYSTEM_LABELS.get(code, ""),
    ))
    await db.flush()


async def post_transaction(
    db: AsyncSession,
    *,
    kind: str,
    entries: list[Entry],
    idempotency_key: str | None = None,
    memo: str = "",
    metadata: dict | None = None,
) -> tuple[LedgerTransaction, bool]:
    """Write one balanced transaction. Returns (transaction, created).

    `created` is False when `idempotency_key` matched an existing transaction —
    the caller gets the original back rather than a second charge. That is the
    behaviour a double-clicked unlock needs, so it is a return value and not an
    exception.
    """
    if idempotency_key:
        existing = (
            await db.execute(
                select(LedgerTransaction)
                .where(LedgerTransaction.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False

    if len(entries) < 2:
        raise LedgerError("a transaction needs at least two entries")

    total_debit = 0
    total_credit = 0
    for entry in entries:
        if entry.direction not in ("debit", "credit"):
            raise LedgerError(f"unknown direction {entry.direction!r}")
        if not isinstance(entry.amount_minor, int) or entry.amount_minor <= 0:
            raise LedgerError(
                f"amount must be a positive integer of minor units, got "
                f"{entry.amount_minor!r}"
            )
        if entry.direction == "debit":
            total_debit += entry.amount_minor
        else:
            total_credit += entry.amount_minor

    if total_debit != total_credit:
        raise LedgerError(
            f"unbalanced transaction: debits {total_debit} != credits {total_credit}"
        )

    for entry in entries:
        await ensure_account(db, entry.account)

    txn = LedgerTransaction(
        kind=kind,
        idempotency_key=idempotency_key,
        memo=memo,
        metadata_json=metadata or {},
    )
    db.add(txn)
    try:
        await db.flush()
    except IntegrityError:
        # Two concurrent requests passed the lookup above and both tried to
        # write the same key. The database is the arbiter; the loser returns the
        # winner's transaction instead of a second charge.
        await db.rollback()
        if idempotency_key:
            existing = (
                await db.execute(
                    select(LedgerTransaction)
                    .where(LedgerTransaction.idempotency_key == idempotency_key)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing, False
        raise

    for entry in entries:
        db.add(LedgerEntry(
            transaction_id=txn.id,
            account_code=entry.account,
            direction=entry.direction,
            amount_minor=entry.amount_minor,
        ))

    # Keep each touched wallet in step with its entries, in the same
    # transaction. A wallet that drifts is detectable, not silent.
    deltas: dict[str, int] = {}
    for entry in entries:
        user_id = _user_id_from_account(entry.account)
        if user_id is None:
            continue
        delta = entry.amount_minor if entry.direction == "credit" else -entry.amount_minor
        deltas[user_id] = deltas.get(user_id, 0) + delta

    for user_id, delta in deltas.items():
        await _apply_wallet_delta(db, user_id, delta)

    await db.flush()
    return txn, True


async def _apply_wallet_delta(db: AsyncSession, user_id: str, delta: int) -> None:
    wallet = (
        await db.execute(select(Wallet).where(Wallet.user_id == user_id))
    ).scalar_one_or_none()
    if wallet is None:
        db.add(Wallet(user_id=user_id, balance_minor=delta))
        await db.flush()
        return
    wallet.balance_minor = (wallet.balance_minor or 0) + delta
    wallet.updated_at = utcnow()


async def wallet_balance_from_ledger(db: AsyncSession, user_id: str) -> int:
    """Recompute a balance from the entries — the source of truth."""
    code = user_available(user_id)
    rows = (
        await db.execute(
            select(LedgerEntry.direction, func.sum(LedgerEntry.amount_minor))
            .where(LedgerEntry.account_code == code)
            .group_by(LedgerEntry.direction)
        )
    ).all()
    credits = debits = 0
    for direction, total in rows:
        if direction == "credit":
            credits = int(total or 0)
        else:
            debits = int(total or 0)
    return credits - debits


async def integrity_report(db: AsyncSession) -> dict:
    """Prove the books balance, and that no wallet has drifted from them.

    Two independent checks, because they fail differently:
      * an unbalanced *transaction* means the posting logic wrote something
        structurally wrong;
      * a drifted *wallet* means the cache and the entries disagree — the
        entries are right.
    """
    signed = case(
        (LedgerEntry.direction == "debit", LedgerEntry.amount_minor),
        else_=-LedgerEntry.amount_minor,
    )
    unbalanced = (
        await db.execute(
            select(LedgerEntry.transaction_id)
            .group_by(LedgerEntry.transaction_id)
            .having(func.sum(signed) != 0)
        )
    ).all()

    wallets = (await db.execute(select(Wallet))).scalars().all()
    drifted = []
    for wallet in wallets:
        expected = await wallet_balance_from_ledger(db, wallet.user_id)
        if expected != (wallet.balance_minor or 0):
            drifted.append({
                "user_id": wallet.user_id,
                "cached_minor": wallet.balance_minor or 0,
                "from_ledger_minor": expected,
            })

    return {
        "balanced": not unbalanced and not drifted,
        "unbalanced_transactions": [str(row[0]) for row in unbalanced],
        "drifted_wallets": drifted,
        "wallet_count": len(wallets),
    }
