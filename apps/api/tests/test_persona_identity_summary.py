"""Persona Studio — a model's identity state must reach the pages that show it.

`PersonaResponse` declares `identity_status`, `identity_score` and `packs_count`,
and the Models list, the dashboard and the persona detail page all read them. The
`Persona` table has none of those columns, so every response carried the field
defaults (None, None, 0) and a persona could finish a full build — trained LoRA,
validated identity, consistency score — and still render as an unknown model with
no score. Nothing joined the tables that hold the values.

`persona_identity_summary` is that join. The part worth pinning is *which*
identity it picks, because a persona accumulates several: candidates from the
generation step, an approved one, and the READY one the lock was anchored to.
Picking the wrong one is not a crash — it is a plausible-looking wrong number,
which is worse. The lock's identity is the one governing every image the persona
produces, so that is the one the score must come from.
"""

from __future__ import annotations

import uuid

import pytest

from app.database import AsyncSessionLocal
from app.models import (
    ContentPack, Identity, IdentityLock, IdentityStatus, Persona,
    PersonaStatus, persona_identity_summary, persona_storage_hex,
)


async def _persona_with_identities(db, name="Summary"):
    """A persona whose locked identity is neither the newest nor the best.

    The order is the point. An earlier version of this fixture put the locked
    identity newest *and* the newest settled one, so the fallback path returned
    the same number as the lock path and the test proved nothing — it passed
    with the lock lookup deliberately broken. Here the locked identity is the
    oldest of the three and scores lowest, so the two paths cannot agree:

        lock path     -> the identity generation is actually anchored to
        newest        -> the candidate (never should win)
        highest score -> the newer approved identity
        fallback      -> the newer approved identity
    """
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    persona = Persona(
        id=uuid.uuid4(), name=name, age=24, status=PersonaStatus.ACTIVE,
        adult_verified=True,
    )
    db.add(persona)

    canonical = Identity(
        id=uuid.uuid4(), persona_id=persona.id, name=f"{name} (canonical)",
        status=IdentityStatus.READY, consistency_score=0.72,
        created_at=base,
    )
    newer_approved = Identity(
        id=uuid.uuid4(), persona_id=persona.id, name=f"{name} (newer approved)",
        status=IdentityStatus.APPROVED, consistency_score=0.97,
        created_at=base + timedelta(hours=1),
    )
    newest_candidate = Identity(
        id=uuid.uuid4(), persona_id=persona.id, name=f"{name} (newest candidate)",
        status=IdentityStatus.CANDIDATE, consistency_score=0.99,
        created_at=base + timedelta(hours=2),
    )
    db.add_all([canonical, newer_approved, newest_candidate])

    # IdentityLock.identity_id is the dashless hex, not a UUID — the same key the
    # image engine addresses.
    db.add(IdentityLock(
        persona_id=persona_storage_hex(persona.id),
        seed=1, identity_prompt="x", negative_prompt="", style_tags=[],
        status="active", identity_id=canonical.id.hex,
    ))
    await db.commit()
    return persona, canonical, newer_approved, newest_candidate


@pytest.mark.asyncio
async def test_the_score_comes_from_the_locked_identity_not_the_newest_or_best(monkeypatch):
    import app.models as models

    async with AsyncSessionLocal() as db:
        persona, canonical, newer, newest = await _persona_with_identities(db)

        summary = (await persona_identity_summary(db, [persona.id]))[str(persona.id)]
        assert summary["identity_score"] == canonical.consistency_score == 0.72, (
            f"expected the locked identity's 0.72; got {summary['identity_score']} "
            f"(newer approved is {newer.consistency_score}, newest is {newest.consistency_score})"
        )
        assert summary["identity_status"] == "ready"
        assert summary["packs_count"] == 0

        # Prove the assertion above depends on the lock and not on the fallback:
        # break only the lock key and the answer must change. Without this the
        # test passed even when the lock lookup was dead, because the fixture
        # happened to order the same identity last.
        monkeypatch.setattr(models, "persona_storage_hex", lambda _pid: "0" * 32)
        without_lock = (await persona_identity_summary(db, [persona.id]))[str(persona.id)]
        assert without_lock["identity_score"] == newer.consistency_score == 0.97, (
            "with the lock unfindable the fallback should surface the newer settled "
            f"identity, not {without_lock['identity_score']} — if these match, this "
            "test cannot tell the two paths apart"
        )

        await db.delete(persona)
        await db.commit()


@pytest.mark.asyncio
async def test_without_a_lock_it_falls_back_past_the_candidates():
    """Mid-build: candidates exist, no lock yet, so show the newest settled one.

    A persona that has only candidates must still report something rather than
    None — the generation step's candidates are real identities with real scores.
    """
    async with AsyncSessionLocal() as db:
        persona = Persona(
            id=uuid.uuid4(), name="NoLock", age=24,
            status=PersonaStatus.BUILDING, adult_verified=True,
        )
        db.add(persona)
        settled = Identity(
            id=uuid.uuid4(), persona_id=persona.id, name="NoLock v1",
            status=IdentityStatus.APPROVED, consistency_score=0.91,
        )
        db.add(settled)
        db.add(Identity(
            id=uuid.uuid4(), persona_id=persona.id, name="NoLock candidate",
            status=IdentityStatus.CANDIDATE, consistency_score=0.99,
        ))
        await db.commit()

        summary = (await persona_identity_summary(db, [persona.id]))[str(persona.id)]

        assert summary["identity_score"] == 0.91, (
            "with no lock, prefer a settled identity over a candidate"
        )
        assert summary["identity_status"] == "approved"

        await db.delete(persona)
        await db.commit()


@pytest.mark.asyncio
async def test_packs_are_counted_per_persona_and_not_shared_between_them():
    async with AsyncSessionLocal() as db:
        with_packs = Persona(
            id=uuid.uuid4(), name="HasPacks", age=24,
            status=PersonaStatus.ACTIVE, adult_verified=True,
        )
        without = Persona(
            id=uuid.uuid4(), name="NoPacks", age=24,
            status=PersonaStatus.ACTIVE, adult_verified=True,
        )
        db.add_all([with_packs, without])
        await db.commit()

        db.add_all([
            ContentPack(id=uuid.uuid4(), persona_id=with_packs.id, name=f"pack {i}")
            for i in range(3)
        ])
        await db.commit()

        summary = await persona_identity_summary(db, [with_packs.id, without.id])

        assert summary[str(with_packs.id)]["packs_count"] == 3
        assert summary[str(without.id)]["packs_count"] == 0, (
            "a batched count must be attributed per persona, not totalled"
        )

        await db.delete(with_packs)
        await db.delete(without)
        await db.commit()


@pytest.mark.asyncio
async def test_a_persona_with_nothing_at_all_is_reported_as_empty_not_missing():
    """The dashboard calls this for every persona, including brand-new ones."""
    async with AsyncSessionLocal() as db:
        persona = Persona(id=uuid.uuid4(), name="Empty", age=24, adult_verified=True)
        db.add(persona)
        await db.commit()

        summary = (await persona_identity_summary(db, [persona.id]))[str(persona.id)]

        assert summary == {
            "identity_status": None,
            "identity_score": None,
            "packs_count": 0,
            # No adapter, so there is no training base to check. Empty rather
            # than a false `unknown` next to a model that has no LoRA at all.
            "lora_base_state": "",
            "lora_base_note": "",
        }

        await db.delete(persona)
        await db.commit()


@pytest.mark.asyncio
async def test_no_personas_makes_no_queries():
    async with AsyncSessionLocal() as db:
        assert await persona_identity_summary(db, []) == {}
