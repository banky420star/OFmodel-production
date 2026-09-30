"""The double-entry ledger — the invariants that make money trustworthy.

These are the tests that matter most, because every other guarantee in the fan
slice rests on them: an unlock that charges twice, or a posting that balances
only by accident, would be invisible in the UI and wrong in the database.
"""

from __future__ import annotations

import pytest

from app.billing.ledger import (
    Entry, LedgerError, SYS_CLEARING, SYS_PROMO, ensure_account,
    integrity_report, post_transaction, user_available, wallet_balance_from_ledger,
)
from app.models import Wallet


def _entries(user_id: str, amount: int, system: str = SYS_PROMO) -> list[Entry]:
    return [
        Entry(account=system, direction="debit", amount_minor=amount),
        Entry(account=user_available(user_id), direction="credit", amount_minor=amount),
    ]


@pytest.mark.asyncio
async def test_balanced_posting_lands(db):
    await ensure_account(db, SYS_PROMO)
    await ensure_account(db, SYS_CLEARING)
    db.add(Wallet(user_id="u1", balance_minor=0))
    await db.flush()

    txn, created = await post_transaction(
        db, kind="promo", entries=_entries("u1", 500), idempotency_key="k1"
    )
    await db.commit()

    assert created is True
    assert txn.id

    balance = await wallet_balance_from_ledger(db, "u1")
    assert balance == 500

    wallet = (await db.execute(
        Wallet.__table__.select().where(Wallet.user_id == "u1")
    )).first()
    # The cache and the entries agree, which is the whole point of the cache.
    assert wallet.balance_minor == 500


@pytest.mark.asyncio
async def test_replaying_an_idempotency_key_returns_the_same_transaction(db):
    await ensure_account(db, SYS_PROMO)
    db.add(Wallet(user_id="u2", balance_minor=0))
    await db.flush()

    first, created_first = await post_transaction(
        db, kind="promo", entries=_entries("u2", 500), idempotency_key="once"
    )
    await db.commit()

    second, created_second = await post_transaction(
        db, kind="promo", entries=_entries("u2", 500), idempotency_key="once"
    )
    await db.commit()

    assert created_first is True
    assert created_second is False, "a replayed key must not post a second time"
    assert second.id == first.id
    assert await wallet_balance_from_ledger(db, "u2") == 500, "balance moved twice"


@pytest.mark.asyncio
async def test_unbalanced_posting_is_refused_and_writes_nothing(db):
    await ensure_account(db, SYS_PROMO)

    with pytest.raises(LedgerError) as excinfo:
        await post_transaction(
            db,
            kind="promo",
            entries=[
                Entry(account=SYS_PROMO, direction="debit", amount_minor=100),
                Entry(account=user_available("u3"), direction="credit", amount_minor=50),
            ],
            idempotency_key="bad",
        )
    assert "100" in str(excinfo.value) and "50" in str(excinfo.value)

    from sqlalchemy import func, select
    from app.models import LedgerTransaction

    # Counted rather than assumed zero: the database is session-scoped, so
    # other tests' committed transactions are visible here.
    count = (await db.execute(
        select(func.count()).select_from(LedgerTransaction)
        .where(LedgerTransaction.idempotency_key == "bad")
    )).scalar()
    assert count == 0, "a refused posting must leave no rows behind"


@pytest.mark.asyncio
async def test_single_entry_is_not_a_transaction(db):
    with pytest.raises(LedgerError):
        await post_transaction(
            db, kind="promo",
            entries=[Entry(account=SYS_PROMO, direction="debit", amount_minor=10)],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [0, -1, -100])
async def test_non_positive_amounts_are_refused(db, amount):
    with pytest.raises(LedgerError):
        await post_transaction(
            db, kind="promo",
            entries=[
                Entry(account=SYS_PROMO, direction="debit", amount_minor=amount),
                Entry(account=user_available("u4"), direction="credit", amount_minor=amount),
            ],
        )


@pytest.mark.asyncio
async def test_integrity_report_is_clean_after_real_activity(db):
    await ensure_account(db, SYS_PROMO)
    db.add(Wallet(user_id="u5", balance_minor=0))
    await db.flush()

    await post_transaction(
        db, kind="promo", entries=_entries("u5", 500), idempotency_key="a"
    )
    await post_transaction(
        db, kind="promo", entries=_entries("u5", 250), idempotency_key="b"
    )
    await db.commit()

    report = await integrity_report(db)
    assert report["balanced"] is True
    assert report["unbalanced_transactions"] == []
    assert report["drifted_wallets"] == []


@pytest.mark.asyncio
async def test_integrity_report_notices_a_drifted_wallet(db):
    """The report must be able to fail, or it proves nothing."""
    await ensure_account(db, SYS_PROMO)
    wallet = Wallet(user_id="u6", balance_minor=0)
    db.add(wallet)
    await db.flush()

    await post_transaction(
        db, kind="promo", entries=_entries("u6", 500), idempotency_key="c"
    )
    await db.commit()

    # Corrupt the cache the way a lost transaction would, without touching the
    # entries. The entries are still truthful; the cache now lies.
    wallet.balance_minor = 999
    await db.commit()

    report = await integrity_report(db)
    assert report["balanced"] is False
    assert [row["user_id"] for row in report["drifted_wallets"]] == ["u6"]
    drift = report["drifted_wallets"][0]
    assert drift["cached_minor"] == 999
    assert drift["from_ledger_minor"] == 500
