"""Persona Studio — an outbound message must not claim to have been sent.

Three endpoints answered `{"status": "sent"}` after writing a local row — one
with the docstring "Generate and send an AI reply" — and incremented
`messages_sent`, the counter that feeds the fan score. Nothing in this API can
transmit a message, so the claim was manufactured by the write itself. These
tests pin the honest behaviour: a draft is not counted, only a human approval
moves the counter, and no response says "sent" unless a person said so.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.delivery import (
    DISCARDED,
    DRAFT,
    SENT,
    approve_draft,
    create_draft,
    delivery_state,
    discard_draft,
    list_drafts,
)


async def _seed_fan(db, persona_id=None, messages_sent=0):
    persona_id = persona_id or uuid.uuid4()
    fan_id = str(uuid.uuid4())
    await db.execute(
        text(
            "INSERT INTO fans (id, persona_id, username, display_name, status, "
            "total_spent, ppv_purchases, messages_sent, messages_received, created_at) "
            "VALUES (:id, :pid, 'fan1', 'Fan One', 'active', 100.0, 2, :sent, 3, :now)"
        ),
        {"id": fan_id, "pid": str(persona_id), "sent": messages_sent,
         "now": datetime.now(timezone.utc)},
    )
    await db.flush()
    return fan_id, persona_id


async def _messages(db, fan_id):
    rows = (await db.execute(
        text("SELECT id, direction, content, metadata_json FROM chat_messages "
             "WHERE fan_id = :fid ORDER BY created_at"),
        {"fid": fan_id},
    )).fetchall()
    return rows


async def _fan_counter(db, fan_id):
    return (await db.execute(
        text("SELECT messages_sent FROM fans WHERE id = :fid"), {"fid": fan_id}
    )).scalar_one()


@pytest.mark.asyncio
async def test_a_draft_is_not_counted_as_sent(db):
    fan_id, persona_id = await _seed_fan(db)

    draft = await create_draft(
        db, fan_id=fan_id, persona_id=persona_id, content="hey you",
    )

    assert draft["delivery"]["status"] == DRAFT
    assert draft["delivery"]["requires_human_send"] is True
    assert await _fan_counter(db, fan_id) == 0, (
        "writing a draft must not move the counter that reads as delivery evidence"
    )


@pytest.mark.asyncio
async def test_approval_records_the_send_and_counts_once(db):
    fan_id, persona_id = await _seed_fan(db)
    draft = await create_draft(
        db, fan_id=fan_id, persona_id=persona_id, content="hey you",
    )

    result = await approve_draft(
        db, fan_id=fan_id, message_id=draft["message_id"], approved_by="bank",
    )

    assert result["ok"]
    assert result["delivery"]["status"] == SENT
    assert result["delivery"]["approved_by"] == "bank"
    assert result["delivery"]["sent_at"]
    assert result["delivery"]["channel"] == "manual"
    assert await _fan_counter(db, fan_id) == 1
    assert result["fan_score"] is not None, "the score is recomputed from a real send"


@pytest.mark.asyncio
async def test_approving_twice_does_not_double_count(db):
    fan_id, persona_id = await _seed_fan(db)
    draft = await create_draft(
        db, fan_id=fan_id, persona_id=persona_id, content="hi",
    )
    await approve_draft(db, fan_id=fan_id, message_id=draft["message_id"],
                        approved_by="bank")

    second = await approve_draft(db, fan_id=fan_id, message_id=draft["message_id"],
                                 approved_by="bank")

    assert second["ok"] is False
    assert "already" in second["error"]
    assert await _fan_counter(db, fan_id) == 1


@pytest.mark.asyncio
async def test_editing_before_approval_stores_what_was_sent(db):
    fan_id, persona_id = await _seed_fan(db)
    draft = await create_draft(
        db, fan_id=fan_id, persona_id=persona_id, content="original draft",
    )

    result = await approve_draft(
        db, fan_id=fan_id, message_id=draft["message_id"],
        approved_by="bank", content="what I actually typed",
    )

    assert result["content"] == "what I actually typed"
    assert result["delivery"]["edited_before_send"] is True
    stored = (await db.execute(
        text("SELECT content FROM chat_messages WHERE id = :mid"),
        {"mid": draft["message_id"]},
    )).scalar_one()
    assert stored == "what I actually typed"


@pytest.mark.asyncio
async def test_discard_keeps_the_row_but_clears_pending(db):
    fan_id, persona_id = await _seed_fan(db)
    draft = await create_draft(
        db, fan_id=fan_id, persona_id=persona_id, content="nope",
    )

    result = await discard_draft(
        db, fan_id=fan_id, message_id=draft["message_id"], reason="too forward",
    )

    assert result["ok"]
    assert result["delivery"]["status"] == DISCARDED
    assert result["delivery"]["discard_reason"] == "too forward"
    assert await _fan_counter(db, fan_id) == 0
    assert await list_drafts(db, fan_id=fan_id) == [], "a discarded draft is not pending"
    assert len(await _messages(db, fan_id)) == 1, "the row is kept, not deleted"


@pytest.mark.asyncio
async def test_a_discarded_draft_cannot_be_approved(db):
    fan_id, persona_id = await _seed_fan(db)
    draft = await create_draft(
        db, fan_id=fan_id, persona_id=persona_id, content="nope",
    )
    await discard_draft(db, fan_id=fan_id, message_id=draft["message_id"])

    result = await approve_draft(db, fan_id=fan_id, message_id=draft["message_id"],
                                 approved_by="bank")
    assert result["ok"] is False
    assert "discarded" in result["error"]
    assert await _fan_counter(db, fan_id) == 0


@pytest.mark.asyncio
async def test_approval_is_scoped_to_the_fan(db):
    """A message id from another fan's thread must not be approvable."""
    fan_a, persona_id = await _seed_fan(db)
    fan_b, _ = await _seed_fan(db, persona_id=persona_id)
    draft = await create_draft(
        db, fan_id=fan_a, persona_id=persona_id, content="for A only",
    )

    result = await approve_draft(db, fan_id=fan_b, message_id=draft["message_id"],
                                 approved_by="bank")
    assert result["ok"] is False
    assert await _fan_counter(db, fan_a) == 0


@pytest.mark.asyncio
async def test_inbound_messages_cannot_be_approved(db):
    fan_id, persona_id = await _seed_fan(db)
    inbound_id = str(uuid.uuid4())
    await db.execute(
        text("INSERT INTO chat_messages (id, fan_id, persona_id, direction, content, "
             "message_type, created_at) VALUES (:id, :fid, :pid, 'inbound', 'hi', 'text', :now)"),
        {"id": inbound_id, "fid": fan_id, "pid": str(persona_id),
         "now": datetime.now(timezone.utc)},
    )

    result = await approve_draft(db, fan_id=fan_id, message_id=inbound_id,
                                 approved_by="bank")
    assert result["ok"] is False
    assert "inbound" in result["error"]


@pytest.mark.asyncio
async def test_the_queue_lists_only_pending_drafts(db):
    fan_id, persona_id = await _seed_fan(db)
    pending = await create_draft(
        db, fan_id=fan_id, persona_id=persona_id, content="pending one",
    )
    done = await create_draft(
        db, fan_id=fan_id, persona_id=persona_id, content="already sent",
    )
    await approve_draft(db, fan_id=fan_id, message_id=done["message_id"],
                        approved_by="bank")

    queue = await list_drafts(db, fan_id=fan_id)
    assert [d["message_id"] for d in queue] == [pending["message_id"]]


@pytest.mark.asyncio
async def test_rows_from_before_delivery_state_are_unknown_not_drafts(db):
    """Every message written before this module existed claims nothing. Whether
    it was sent is genuinely unknown, and the API must say that rather than
    guess in either direction."""
    fan_id, persona_id = await _seed_fan(db)
    legacy_id = str(uuid.uuid4())
    await db.execute(
        text("INSERT INTO chat_messages (id, fan_id, persona_id, direction, content, "
             "message_type, created_at) VALUES (:id, :fid, :pid, 'outbound', 'legacy', 'text', :now)"),
        {"id": legacy_id, "fid": fan_id, "pid": str(persona_id),
         "now": datetime.now(timezone.utc)},
    )

    assert await list_drafts(db, fan_id=fan_id) == []
    state = delivery_state(None)
    assert state["status"] == "unknown"
    assert "unknown" in state["note"]


def test_delivery_state_reads_a_json_string():
    raw = json.dumps({"delivery": {"status": "draft"}})
    assert delivery_state(raw)["status"] == "draft"
