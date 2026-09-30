"""Persona Studio — a build must not hold SQLite's writer slot while it works.

The reference-dataset step writes the identity lock, then spends about 75
minutes turning nine views into images (measured: 16.5 s per SDXL step on this
machine, plus the avatar bootstrap). `execute_step` commits only after the
handler returns, so without an explicit commit in between, that one uncommitted
write holds SQLite's single writer slot for the whole build.

WAL is what makes this easy to miss: readers are never blocked, so the UI keeps
polling happily and the build looks healthy. Writers are blocked — measured, the
next writer waits out the entire 30 s busy_timeout and then fails with "database
is locked". Every other write in the app fails with it: the Job progress the
Create Model page draws its step list from, a second persona build, and whatever
the operator does in the UI while waiting.

This is checked from a *separate connection*, because a check on the step's own
session would pass either way — session-local state is visible to itself, which
is precisely how the old single-shared-connection pool hid the problem.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

import app.identity_engine as identity_engine
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import Identity, IdentityStatus, Persona, PersonaStatus, persona_storage_hex
from app.workflows.persona_flow import build_reference_dataset_handler

# The step's own busy_timeout is 30 s. A test that waits it out is a test nobody
# runs, so the probe waits 5 s: long enough that a free writer never trips it,
# short enough that a held lock fails the run quickly.
PROBE_TIMEOUT_SECONDS = 5


def _db_path() -> str:
    return get_settings().DATABASE_URL.split("///")[-1]


async def _probe_while_generating(storage_hex: str, observed: dict) -> None:
    """Ask, from another connection, what the build is holding right now."""
    con = sqlite3.connect(_db_path(), timeout=PROBE_TIMEOUT_SECONDS)
    try:
        # 1. Is the identity lock committed, or only visible to its own session?
        observed["lock_rows_visible"] = con.execute(
            "SELECT count(*) FROM identity_locks WHERE persona_id = ?", (storage_hex,)
        ).fetchone()[0]

        # 2. Can anything else write at all? A statement matching no rows still
        #    has to take the write lock, so this measures the lock and nothing
        #    else — it cannot pass by finding no work to do.
        con.execute("UPDATE jobs SET progress = progress WHERE 0")
        con.commit()
        observed["writer_slot_free"] = True
    except sqlite3.OperationalError as exc:
        observed["writer_slot_free"] = False
        observed["error"] = str(exc)
    finally:
        con.close()


@pytest.mark.asyncio
async def test_another_writer_is_not_blocked_while_the_build_generates():
    observed: dict = {}
    # A name of the test's own so the avatar and dataset it writes are findable
    # and removable, rather than joining the placeholders already in storage/.
    name = f"WriteLock_{uuid.uuid4().hex[:8]}"
    persona_id = uuid.uuid4()
    storage_hex = persona_storage_hex(persona_id)
    dataset_dir = Path(__file__).parent.parent / "storage" / "datasets" / storage_hex[:8]
    avatar_path = Path(__file__).parent.parent / "storage" / "avatars" / f"{name.lower()}.jpg"

    real_generate = identity_engine.generate_identity_locked
    calls = []

    async def probe_then_generate(*args, **kwargs):
        # Probed on the first view only: that is the moment the build has just
        # written the lock and is about to generate for the next hour.
        if not calls:
            calls.append(1)
            await _probe_while_generating(storage_hex, observed)
        return await real_generate(*args, **kwargs)

    identity_engine.generate_identity_locked = probe_then_generate
    try:
        async with AsyncSessionLocal() as db:
            persona = Persona(
                id=persona_id,
                name=name,
                age=24,
                description="write-lock probe",
                status=PersonaStatus.BUILDING,
                adult_verified=True,
            )
            db.add(persona)
            identity = Identity(
                id=uuid.uuid4(),
                persona_id=persona_id,
                name=f"{name} v1",
                status=IdentityStatus.READY,
            )
            db.add(identity)
            await db.commit()

            result = await build_reference_dataset_handler(
                workflow_id=uuid.uuid4(),
                step_id=uuid.uuid4(),
                input_data={
                    "persona_id": str(persona_id),
                    "identity_id": str(identity.id),
                    "persona_name": name,
                },
                db=db,
            )

        assert "error" not in result, f"the step failed: {result}"
        assert result["total_images"] >= 1

        # The lock was committed before generation started, so it is both
        # visible to, and safe from, anyone else.
        assert observed.get("lock_rows_visible") == 1, (
            "the identity lock was written but not committed: another connection "
            f"cannot see it while the build generates ({observed})"
        )
        assert observed.get("writer_slot_free") is True, (
            "the build held SQLite's writer lock while generating, so every other "
            f"write in the app fails: {observed.get('error')}"
        )
    finally:
        identity_engine.generate_identity_locked = real_generate
        for path in (avatar_path,):
            path.unlink(missing_ok=True)
        if dataset_dir.exists():
            for image in dataset_dir.glob("ref_*.png"):
                image.unlink()
            with __import__("contextlib").suppress(OSError):
                dataset_dir.rmdir()
