"""Persona Studio — outbound message delivery state.

Nothing in this API transmits a message to a platform. There is no OnlyFans,
Fansly or Fanvue client here and no automated account access, so every outbound
row in `chat_messages` is a *record*, and the only question the record can
honestly answer is whether a human has sent it yet.

Before this module, three endpoints wrote a local row and then answered
`{"status": "sent"}` — one with the docstring "Generate and send an AI reply" —
while incrementing `messages_sent`, a counter that reads as delivery evidence and
feeds the fan score. The write itself fabricated the evidence.

Two states, and the API reports which one a message is in:

    draft       generated, waiting for a human to send it. Not counted.
    sent        a human confirmed they sent it. Counted, with the time.
    discarded   rejected, kept so a rejected draft is not left looking pending.

State lives in `chat_messages.metadata_json` rather than in new columns: the
table is written through raw SQL, and the project's alembic setup has no
versions and is configured for postgres, so a column change cannot be applied to
the live SQLite database by any path that exists here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

DRAFT = "draft"
SENT = "sent"
DISCARDED = "discarded"


def _load_metadata(raw) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw:
        try:
            loaded = json.loads(raw)
            return loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def create_draft(
    db: AsyncSession,
    *,
    fan_id: str,
    persona_id,
    content: str,
    message_type: str = "text",
    is_ai_generated: bool = True,
    intent: str = "",
    sentiment: float | None = None,
    extra: dict | None = None,
) -> dict:
    """Write an outbound message as a DRAFT. Deliberately does not count it.

    Callers must not report this as sent. `delivery.status` is the field that
    says what actually happened to the message, and it starts as `draft`.
    """
    message_id = str(uuid4())
    now = datetime.now(timezone.utc)
    delivery = {
        "status": DRAFT,
        "channel": "",          # filled in at approval: manual, api, ...
        "sent_at": None,
        "approved_by": "",
        "requires_human_send": True,
    }
    metadata = {**(extra or {}), "delivery": delivery}

    await db.execute(
        text(
            "INSERT INTO chat_messages (id, fan_id, persona_id, direction, content, message_type, "
            "is_ai_generated, sentiment, intent, metadata_json, created_at) "
            "VALUES (:id, :fan_id, :pid, 'outbound', :content, :mt, :ai, :sentiment, :intent, :meta, :now)"
        ),
        {
            "id": message_id, "fan_id": fan_id, "pid": str(persona_id),
            "content": content, "mt": message_type,
            "ai": 1 if is_ai_generated else 0,
            "sentiment": sentiment, "intent": intent,
            "meta": json.dumps(metadata), "now": now,
        },
    )
    return {
        "message_id": message_id,
        "content": content,
        "delivery": delivery,
    }


async def _get_outbound_message(db: AsyncSession, fan_id: str, message_id: str):
    result = await db.execute(
        text(
            "SELECT id, fan_id, persona_id, direction, content, metadata_json "
            "FROM chat_messages WHERE id = :mid AND fan_id = :fid"
        ),
        {"mid": message_id, "fid": fan_id},
    )
    return result.fetchone()


async def approve_draft(
    db: AsyncSession,
    *,
    fan_id: str,
    message_id: str,
    approved_by: str,
    content: str = "",
    channel: str = "manual",
) -> dict:
    """Record that a human sent this message, optionally with edits.

    Only here does `messages_sent` move: the counter answers "how many messages
    has this persona sent this fan", and until a person sends one the answer is
    unchanged. `content` replaces the draft body when the operator edited it —
    what was sent is what gets stored.
    """
    row = await _get_outbound_message(db, fan_id, message_id)
    if row is None:
        return {"ok": False, "error": "message not found for this fan"}

    _id, _fan, persona_id, direction, stored_content, raw_meta = row
    if direction != "outbound":
        return {"ok": False, "error": f"message is {direction}, not an outbound draft"}

    metadata = _load_metadata(raw_meta)
    delivery = _load_metadata(metadata.get("delivery"))
    if delivery.get("status") == SENT:
        return {"ok": False, "error": "message was already recorded as sent",
                "delivery": delivery}
    if delivery.get("status") == DISCARDED:
        return {"ok": False, "error": "message was discarded", "delivery": delivery}

    now = datetime.now(timezone.utc)
    final_content = content.strip() or stored_content
    delivery.update({
        "status": SENT,
        "channel": channel,
        "sent_at": now.isoformat(),
        "approved_by": approved_by,
        "requires_human_send": False,
        "edited_before_send": bool(content.strip()) and content.strip() != stored_content,
    })
    metadata["delivery"] = delivery

    await db.execute(
        text(
            "UPDATE chat_messages SET content = :content, metadata_json = :meta "
            "WHERE id = :mid"
        ),
        {"content": final_content, "meta": json.dumps(metadata), "mid": message_id},
    )

    fan_row = (await db.execute(
        text("SELECT total_spent, ppv_purchases, messages_sent, last_active FROM fans WHERE id = :fid"),
        {"fid": fan_id},
    )).fetchone()
    total_spent = (fan_row[0] if fan_row else 0) or 0
    ppv_purchases = (fan_row[1] if fan_row else 0) or 0
    messages_sent = ((fan_row[2] if fan_row else 0) or 0) + 1
    days_since = 0
    if fan_row and fan_row[3]:
        last_active = fan_row[3]
        if isinstance(last_active, str):
            try:
                last_active = datetime.fromisoformat(last_active)
            except ValueError:
                last_active = None
        if last_active is not None:
            if last_active.tzinfo is None:
                last_active = last_active.replace(tzinfo=timezone.utc)
            days_since = (now - last_active).days

    from app.chat_engine import _score_fan

    fan_score = _score_fan(total_spent, ppv_purchases, messages_sent, days_since)
    await db.execute(
        text("UPDATE fans SET messages_sent = :sent, fan_score = :score WHERE id = :fid"),
        {"sent": messages_sent, "score": fan_score, "fid": fan_id},
    )

    logger.info(
        "outbound_message_recorded_sent",
        message_id=message_id, fan_id=fan_id, channel=channel, approved_by=approved_by,
    )
    return {
        "ok": True,
        "message_id": message_id,
        "content": final_content,
        "delivery": delivery,
        "fan_score": fan_score,
    }


async def discard_draft(
    db: AsyncSession,
    *,
    fan_id: str,
    message_id: str,
    reason: str = "",
) -> dict:
    """Mark a draft rejected. Kept, so it cannot be mistaken for a pending draft."""
    row = await _get_outbound_message(db, fan_id, message_id)
    if row is None:
        return {"ok": False, "error": "message not found for this fan"}

    _id, _fan, _pid, direction, _content, raw_meta = row
    if direction != "outbound":
        return {"ok": False, "error": f"message is {direction}, not an outbound draft"}

    metadata = _load_metadata(raw_meta)
    delivery = _load_metadata(metadata.get("delivery"))
    if delivery.get("status") == SENT:
        return {"ok": False, "error": "message was already recorded as sent"}

    delivery.update({
        "status": DISCARDED,
        "discarded_at": datetime.now(timezone.utc).isoformat(),
        "discard_reason": reason,
        "requires_human_send": False,
    })
    metadata["delivery"] = delivery
    await db.execute(
        text("UPDATE chat_messages SET metadata_json = :meta WHERE id = :mid"),
        {"meta": json.dumps(metadata), "mid": message_id},
    )
    return {"ok": True, "message_id": message_id, "delivery": delivery}


async def list_drafts(
    db: AsyncSession,
    *,
    persona_id: str | None = None,
    fan_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Outbound messages awaiting a human send — the approval queue.

    Reads `delivery.status` out of metadata_json. Rows written before this
    module existed have no delivery block at all; they are reported as
    `unknown` rather than as drafts, because whether they were sent is exactly
    the thing that was never recorded.
    """
    clauses = ["direction = 'outbound'"]
    params: dict = {"limit": limit}
    if persona_id:
        clauses.append("persona_id = :pid")
        params["pid"] = persona_id
    if fan_id:
        clauses.append("fan_id = :fid")
        params["fid"] = fan_id

    result = await db.execute(
        text(
            "SELECT id, fan_id, persona_id, content, message_type, intent, "
            "metadata_json, created_at FROM chat_messages "
            f"WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT :limit"
        ),
        params,
    )

    drafts: list[dict] = []
    for row in result.fetchall():
        metadata = _load_metadata(row[6])
        delivery = _load_metadata(metadata.get("delivery"))
        status = delivery.get("status") or "unknown"
        if status != DRAFT:
            continue
        drafts.append({
            "message_id": row[0],
            "fan_id": row[1],
            "persona_id": row[2],
            "content": row[3],
            "message_type": row[4],
            "intent": row[5],
            "delivery": delivery,
            "created_at": row[7].isoformat() if hasattr(row[7], "isoformat") else str(row[7] or ""),
        })
    return drafts


def delivery_state(raw_metadata) -> dict:
    """The delivery block for a message, or an honest `unknown`."""
    delivery = _load_metadata(_load_metadata(raw_metadata).get("delivery"))
    if not delivery:
        return {
            "status": "unknown",
            "note": (
                "Written before delivery state was recorded, so whether this "
                "message was actually sent is unknown."
            ),
        }
    return delivery
