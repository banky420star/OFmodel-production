"""Persona Studio — the signup packet's two honesty properties.

The packet is the supported way to create a platform account by hand: it hands a
person a real inbox, a generated password, the display name and bio, and the
platform's own signup URL. Two things about it have been wrong before, and both
are cheap to keep wrong silently, so both are pinned here.

1. **It leaked the password into `steps`.** The password was returned as its own
   field *and* spelled out inside the human-readable step list, so the same live
   credential appeared twice in every serialization of the response — in any log
   line, in any packet dumped to a file, and on screen in any UI that renders
   `steps` as text (which is what a step list is for).
2. **Its `note` pointed at a section that does not exist.** It read "see the
   auto-signup section for why" long after the only auto-signup UI in the app
   was removed — and separately, it asserted that automated account creation is
   not implemented, which was false while the auto-signup routes were
   registered and unguarded. They now refuse by default (see
   `test_auto_signup_gate.py`), and the note says what is actually true of the
   packet rather than what is true of the app's route table.
"""

from __future__ import annotations

import base64
import uuid

import pytest

from app.models import Persona, SocialAccount


async def _seed(db, *, email: str = "", metadata: dict | None = None) -> SocialAccount:
    persona = Persona(name=f"Packet_{uuid.uuid4().hex[:6]}", age=26)
    db.add(persona)
    await db.flush()
    account = SocialAccount(
        id=str(uuid.uuid4()),
        persona_id=str(persona.id),
        platform="instagram",
        username="packettest",
        status="approved",
        email=email,
        metadata_json=metadata or {},
    )
    db.add(account)
    await db.commit()
    return account


@pytest.mark.asyncio
async def test_steps_carry_no_password_but_the_field_does(db, client):
    account = await _seed(db)

    resp = await client.get(f"/api/v1/social-accounts/{account.id}/signup-packet")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    password = body["password"]
    assert password, "the packet must hand a person a password to type"

    steps_text = "\n".join(body["steps"])
    assert password not in steps_text, (
        "the password must not be inlined into `steps` — it is already returned "
        "in its own field, and a step list gets rendered, logged and dumped"
    )
    assert body["email_password"] not in steps_text or not body["email_password"], (
        "the inbox password must not be inlined into `steps` either"
    )


@pytest.mark.asyncio
async def test_steps_name_the_fields_instead_of_repeating_the_values(db, client):
    """Redaction has to leave usable instructions, not just remove text."""
    account = await _seed(
        db,
        email="packet@example.invalid",
        metadata={"email_password": "inbox-secret", "email_token": "tok"},
    )

    body = (await client.get(
        f"/api/v1/social-accounts/{account.id}/signup-packet"
    )).json()

    steps_text = "\n".join(body["steps"]).lower()
    assert "'password' field" in steps_text
    assert "'email' field" in steps_text
    assert "inbox-secret" not in "\n".join(body["steps"])


@pytest.mark.asyncio
async def test_the_note_points_at_nothing_that_does_not_exist(db, client):
    account = await _seed(db)

    note = (await client.get(
        f"/api/v1/social-accounts/{account.id}/signup-packet"
    )).json()["note"]

    assert "auto-signup section" not in note, (
        "there is no auto-signup section in the app; the only one was removed"
    )
    assert "auto-signup section for why" not in note
    # What the packet must actually promise about itself.
    assert "nothing here contacts" in note.lower()


@pytest.mark.asyncio
async def test_an_existing_password_is_returned_unchanged(db, client):
    """A reload must not rotate the password a person may already have typed."""
    account = await _seed(db)
    account.password_hash = base64.b64encode(b"already-set-pw").decode()
    await db.commit()

    resp = await client.get(f"/api/v1/social-accounts/{account.id}/signup-packet")
    body = resp.json()
    assert body["password"] == "already-set-pw"
    assert body["password_created_now"] is False


@pytest.mark.asyncio
async def test_has_inbox_is_false_when_no_inbox_was_generated(db, client):
    account = await _seed(db)
    body = (await client.get(
        f"/api/v1/social-accounts/{account.id}/signup-packet"
    )).json()
    assert body["has_inbox"] is False
    assert body["email"] == ""
