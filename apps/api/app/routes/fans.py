"""Persona Studio — fan management routes."""

from __future__ import annotations
import json
import time
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Depends, Query, Form
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Fan, ChatMessage, Persona
from app.chat_engine import generate_chat_reply, _score_fan

router = APIRouter()


async def find_fan(db: AsyncSession, fan_id: str):
    """Find a fan by ID using raw SQL (ORM UUID type broken on Python 3.9 + SQLite)."""
    result = await db.execute(
        text("SELECT id, persona_id, username, display_name, platform, status, "
             "subscription_tier, total_spent, ppv_purchases, tips_given, "
             "messages_sent, messages_received, last_active, last_message_at, "
             "fan_score, tags, notes, metadata_json, created_at, updated_at "
             "FROM fans WHERE id = :fid"),
        {"fid": fan_id},
    )
    row = result.fetchone()
    if not row:
        return None
    from types import SimpleNamespace
    cols = ["id","persona_id","username","display_name","platform","status",
            "subscription_tier","total_spent","ppv_purchases","tips_given",
            "messages_sent","messages_received","last_active","last_message_at",
            "fan_score","tags","notes","metadata_json","created_at","updated_at"]
    return SimpleNamespace(**dict(zip(cols, row)))


async def query_fan_messages(db: AsyncSession, fan_id: str, limit: int = 50, direction: str = None):
    """Query chat messages for a fan using raw SQL."""
    where = "WHERE fan_id = :fid"
    params = {"fid": fan_id, "limit": limit}
    if direction:
        where += " AND direction = :dir"
        params["dir"] = direction
    result = await db.execute(
        text(f"SELECT id, fan_id, persona_id, direction, content, message_type, "
             f"is_ai_generated, is_ppv, ppv_price, ppv_unlocked, sentiment, intent, "
             f"metadata_json, created_at FROM chat_messages {where} "
             f"ORDER BY created_at DESC LIMIT :limit"),
        params,
    )
    from types import SimpleNamespace
    msg_cols = ["id","fan_id","persona_id","direction","content","message_type",
                "is_ai_generated","is_ppv","ppv_price","ppv_unlocked","sentiment",
                "intent","metadata_json","created_at"]
    rows = result.fetchall()
    msgs = [SimpleNamespace(**dict(zip(msg_cols, r))) for r in rows]
    msgs.reverse()
    return msgs


# ─── Dashboard (Phase 14) ────────────────────────────────────────────


    return {"id": str(fan.id), "username": fan.username, "status": "created"}


@router.get("/fans/{fan_id}/messages")
async def list_fan_messages(
    fan_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Get chat history for a fan."""
    fan = await find_fan(db, fan_id)
    if not fan:
        raise HTTPException(404, "Fan not found")
    
    messages = await query_fan_messages(db, fan_id, limit)
    return [
        {
            "id": str(m.id),
            "direction": m.direction,
            "content": m.content,
            "message_type": m.message_type,
            "is_ai_generated": m.is_ai_generated,
            "is_ppv": m.is_ppv,
            "ppv_price": m.ppv_price,
            "sentiment": m.sentiment,
            "intent": m.intent,
            "created_at": m.created_at.isoformat() if hasattr(m.created_at, 'isoformat') else str(m.created_at) if m.created_at else None,
        }
        for m in messages
    ]


@router.post("/fans/{fan_id}/reply")
async def auto_reply(
    fan_id: str,
    message: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Generate and send an AI reply to a fan message."""
    from app.chat_engine import generate_chat_reply
    from uuid import uuid4
    
    fan = await find_fan(db, fan_id)
    if not fan:
        raise HTTPException(404, "Fan not found")
    
    persona = await db.get(Persona, UUID(fan.persona_id) if isinstance(fan.persona_id, str) else fan.persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    
    # Save inbound message via raw SQL
    msg_id = str(uuid4())
    now = datetime.now(timezone.utc)
    await db.execute(
        text("INSERT INTO chat_messages (id, fan_id, persona_id, direction, content, message_type, created_at) "
             "VALUES (:id, :fan_id, :persona_id, 'inbound', :content, 'text', :now)"),
        {"id": msg_id, "fan_id": fan.id, "persona_id": fan.persona_id, "content": message, "now": now},
    )
    # Update fan stats via raw SQL
    new_sent = (fan.messages_sent or 0) + 1
    await db.execute(
        text("UPDATE fans SET messages_sent = :sent, last_message_at = :now WHERE id = :fid"),
        {"sent": new_sent, "now": now, "fid": fan.id},
    )
    
    # Get conversation history via raw SQL
    history_msgs = await query_fan_messages(db, fan_id, 10)
    history = [{"direction": m.direction, "content": m.content} for m in history_msgs]
    history.reverse()
    
    # Calculate days since last active
    days_since = 0
    if fan.last_active:
        days_since = (datetime.now(timezone.utc) - fan.last_active).days
    
    # Generate AI reply
    reply = await generate_chat_reply(
        persona_name=persona.name,
        brand=persona.brand or "lifestyle",
        personality=json.dumps(persona.personality) if persona.personality else "friendly, flirty",
        voice_style=persona.voice_style or "casual English",
        fan_message=message,
        conversation_history=history,
        fan_total_spent=fan.total_spent or 0,
        fan_ppv_purchases=fan.ppv_purchases or 0,
        fan_messages_sent=fan.messages_sent or 0,
        days_since_last_active=days_since,
    )
    
    # Save outbound message via raw SQL
    out_id = str(uuid4())
    await db.execute(
        text("INSERT INTO chat_messages (id, fan_id, persona_id, direction, content, message_type, "
             "is_ai_generated, sentiment, intent, created_at) "
             "VALUES (:id, :fan_id, :persona_id, 'outbound', :content, 'text', 1, :sentiment, :intent, :now)"),
        {"id": out_id, "fan_id": fan.id, "persona_id": fan.persona_id, "content": reply.text,
         "sentiment": reply.sentiment, "intent": reply.intent, "now": now},
    )
    # Update fan stats and score via raw SQL
    new_received = (fan.messages_received or 0) + 1
    from app.chat_engine import _score_fan
    new_score = _score_fan(fan.total_spent or 0, fan.ppv_purchases or 0, new_sent, days_since)
    await db.execute(
        text("UPDATE fans SET messages_received = :recv, fan_score = :score WHERE id = :fid"),
        {"recv": new_received, "score": new_score, "fid": fan.id},
    )
    
    await db.commit()
    
    return {
        "reply": reply.text,
        "intent": reply.intent,
        "sentiment": reply.sentiment,
        "suggests_ppv": reply.suggests_ppv,
        "ppv_prompt": reply.ppv_prompt,
        "fan_score": new_score,
    }


@router.post("/fans/{fan_id}/ppv")
async def send_ppv(
    fan_id: str,
    content_key: str = Query(...),
    price: float = Query(...),
    caption: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    """Send a PPV message to a fan."""
    from uuid import uuid4
    
    fan = await find_fan(db, fan_id)
    if not fan:
        raise HTTPException(404, "Fan not found")
    
    msg_id = str(uuid4())
    now = datetime.now(timezone.utc)
    await db.execute(
        text("INSERT INTO chat_messages (id, fan_id, persona_id, direction, content, message_type, "
             "is_ppv, ppv_price, metadata_json, created_at) "
             "VALUES (:id, :fan_id, :persona_id, 'outbound', :content, 'ppv', 1, :price, :meta, :now)"),
        {"id": msg_id, "fan_id": fan.id, "persona_id": fan.persona_id,
         "content": caption or 'exclusive content', "price": price,
         "meta": json.dumps({"content_key": content_key}), "now": now},
    )
    await db.commit()
    
    return {"status": "sent", "ppv_price": price, "fan": fan.username}


@router.post("/fans/mass-message")
async def mass_message(
    persona_id: str = Query(...),
    message_type: str = Query("welcome"),
    fan_ids: list[str] | None = Query(None),
    custom_message: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    """Send a mass message to multiple fans."""
    from app.chat_engine import generate_mass_message
    from uuid import uuid4
    
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    
    # Get target fans via raw SQL
    if fan_ids:
        placeholders = ', '.join([':f' + str(i) for i in range(len(fan_ids))])
        fan_params = {"f" + str(i): fid for i, fid in enumerate(fan_ids)}
        fans_result = await db.execute(
            text(f"SELECT id, display_name, username FROM fans WHERE id IN ({placeholders})"), fan_params)
    else:
        fans_result = await db.execute(
            text("SELECT id, display_name, username FROM fans WHERE persona_id = :pid AND status = 'active'"),
            {"pid": persona_id})
    fans = fans_result.fetchall()
    
    now = datetime.now(timezone.utc)
    sent = 0
    for fan_id_val, display_name, username in fans:
        msg_text = await generate_mass_message(
            persona_name=persona.name,
            brand=persona.brand or "lifestyle",
            personality=json.dumps(persona.personality) if persona.personality else "friendly",
            voice_style=persona.voice_style or "casual",
            message_type=message_type,
            fan_name=display_name or username,
            custom_context=custom_message,
        )
        msg_id = str(uuid4())
        await db.execute(
            text("INSERT INTO chat_messages (id, fan_id, persona_id, direction, content, message_type, is_ai_generated, created_at) "
                 "VALUES (:id, :fan_id, :pid, 'outbound', :content, 'text', 1, :now)"),
            {"id": msg_id, "fan_id": fan_id_val, "pid": persona_id, "content": msg_text, "now": now},
        )
        sent += 1
    
    await db.commit()
    return {"sent": sent, "message_type": message_type}


@router.get("/fans/analytics")
async def fan_analytics(
    persona_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Aggregate fan metrics for the dashboard."""
    from app.models import Fan, ChatMessage
    from sqlalchemy import func
    
    q = select(Fan)
    if persona_id:
        q = q.where(Fan.persona_id == persona_id)
    result = await db.execute(q)
    fans = result.scalars().all()
    
    if not fans:
        return {
            "total_fans": 0,
            "total_revenue": 0,
            "avg_fan_score": 0,
            "whales": 0,
            "at_risk": 0,
            "new_this_week": 0,
            "top_fans": [],
        }
    
    total_spent = sum(f.total_spent or 0 for f in fans)
    avg_score = sum(f.fan_score or 0 for f in fans) / len(fans)
    whales = len([f for f in fans if (f.total_spent or 0) > 100])
    at_risk = len([f for f in fans if f.status == "inactive"])
    
    week_ago = datetime.utcnow() - timedelta(days=7)
    new_this_week = len([f for f in fans if f.created_at and f.created_at > week_ago])
    
    # Top 10 fans by spending
    sorted_fans = sorted(fans, key=lambda f: f.total_spent or 0, reverse=True)[:10]
    
    return {
        "total_fans": len(fans),
        "total_revenue": round(total_spent, 2),
        "avg_fan_score": round(avg_score, 1),
        "whales": whales,
        "at_risk": at_risk,
        "new_this_week": new_this_week,
        "top_fans": [
            {
                "username": f.username,
                "total_spent": f.total_spent,
                "fan_score": f.fan_score,
                "status": f.status,
            }
            for f in sorted_fans
        ],
    }


# ─── Mailboxes (Per-Persona AI Mailboxes) ─────────────────────────

@router.get("/mailboxes")
async def list_mailboxes(db: AsyncSession = Depends(get_db)):
    """List all persona mailboxes with stats."""
    from app.models import Fan, ChatMessage

    personas_result = await db.execute(select(Persona).order_by(Persona.created_at.desc()))
    personas = personas_result.scalars().all()

    mailboxes = []
    for p in personas:
        # Fan count for this persona
        fans_q = select(Fan).where(Fan.persona_id == p.id)
        fans_result = await db.execute(fans_q)
        fans = fans_result.scalars().all()

        # Message count
        msgs_q = select(func.count()).where(ChatMessage.persona_id == p.id)
        msgs_count = (await db.execute(msgs_q)).scalar() or 0

        # Unread (inbound messages newer than last outbound)
        last_outbound_q = (
            select(func.max(ChatMessage.created_at))
            .where(ChatMessage.persona_id == p.id, ChatMessage.direction == "outbound")
        )
        last_outbound = (await db.execute(last_outbound_q)).scalar()

        if last_outbound:
            unread_q = (
                select(func.count()).where(
                    ChatMessage.persona_id == p.id,
                    ChatMessage.direction == "inbound",
                    ChatMessage.created_at > last_outbound,
                )
            )
            unread = (await db.execute(unread_q)).scalar() or 0
        else:
            # All inbound are unread if no outbound yet
            unread_q = (
                select(func.count()).where(
                    ChatMessage.persona_id == p.id,
                    ChatMessage.direction == "inbound",
                )
            )
            unread = (await db.execute(unread_q)).scalar() or 0

        # Revenue from this persona's fans
        revenue = sum(f.total_spent or 0 for f in fans)

        # Last message time
        last_msg_q = (
            select(func.max(ChatMessage.created_at))
            .where(ChatMessage.persona_id == p.id)
        )
        last_msg = (await db.execute(last_msg_q)).scalar()

        mailboxes.append({
            "persona_id": str(p.id),
            "persona_name": p.name,
            "avatar_url": p.avatar_url or "",
            "brand": p.brand or "",
            "status": p.status.value if hasattr(p.status, 'value') else str(p.status),
            "fan_count": len(fans),
            "message_count": msgs_count,
            "unread_count": unread,
            "revenue": round(revenue, 2),
            "last_message_at": last_msg.isoformat() if hasattr(last_msg, 'isoformat') else str(last_msg) if last_msg else None,
        })

    return mailboxes


@router.get("/mailboxes/{persona_id}")
async def get_mailbox(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get a specific persona's mailbox with fan threads."""
    from app.models import Fan, ChatMessage

    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    # Get all fans for this persona
    fans_q = select(Fan).where(Fan.persona_id == persona_id).order_by(Fan.total_spent.desc())
    fans_result = await db.execute(fans_q)
    fans = fans_result.scalars().all()

    # Build thread list: for each fan, get their latest message + unread count
    threads = []
    for fan in fans:
        # Latest message
        latest_q = (
            select(ChatMessage)
            .where(ChatMessage.fan_id == fan.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        latest_result = await db.execute(latest_q)
        latest = latest_result.scalar_one_or_none()

        # Unread inbound messages for this fan
        last_outbound_q = (
            select(func.max(ChatMessage.created_at))
            .where(ChatMessage.fan_id == fan.id, ChatMessage.direction == "outbound")
        )
        last_outbound = (await db.execute(last_outbound_q)).scalar()

        if last_outbound:
            unread_q = (
                select(func.count()).where(
                    ChatMessage.fan_id == fan.id,
                    ChatMessage.direction == "inbound",
                    ChatMessage.created_at > last_outbound,
                )
            )
        else:
            unread_q = (
                select(func.count()).where(
                    ChatMessage.fan_id == fan.id,
                    ChatMessage.direction == "inbound",
                )
            )
        unread = (await db.execute(unread_q)).scalar() or 0

        threads.append({
            "fan_id": str(fan.id),
            "username": fan.username,
            "display_name": fan.display_name,
            "platform": fan.platform,
            "subscription_tier": fan.subscription_tier,
            "total_spent": fan.total_spent or 0,
            "fan_score": fan.fan_score or 0,
            "tags": fan.tags or [],
            "last_message": {
                "content": latest.content if latest else "",
                "direction": latest.direction if latest else "",
                "is_ai_generated": latest.is_ai_generated if latest else False,
                "created_at": (latest.created_at.isoformat() if hasattr(latest.created_at, 'isoformat') else str(latest.created_at)) if latest and latest.created_at else None,
            } if latest else None,
            "unread_count": unread,
        })

    # Sort threads: unread first, then by last message time
    def _sort_key(t):
        unread = -t["unread_count"]
        lm = t.get("last_message")
        ts = lm.get("created_at", "") if lm else ""
        return (unread, ts or "")
    threads.sort(key=_sort_key)

    return {
        "persona_id": str(persona_id),
        "persona_name": persona.name,
        "avatar_url": persona.avatar_url or "",
        "brand": persona.brand or "",
        "total_fans": len(fans),
        "total_revenue": round(sum(f.total_spent or 0 for f in fans), 2),
        "threads": threads,
    }


@router.post("/mailboxes/{persona_id}/send")
async def send_as_persona(
    persona_id: UUID,
    fan_id: str = Query(...),
    content: str = Query(...),
    message_type: str = Query("text"),
    db: AsyncSession = Depends(get_db),
):
    """Send a message as the persona to a fan (operator override)."""
    from uuid import uuid4

    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    fan = await find_fan(db, fan_id)
    if not fan or str(fan.persona_id) != str(persona_id):
        raise HTTPException(404, "Fan not found in this persona's mailbox")

    msg_id = str(uuid4())
    now = datetime.now(timezone.utc)
    await db.execute(
        text("INSERT INTO chat_messages (id, fan_id, persona_id, direction, content, message_type, is_ai_generated, created_at) "
             "VALUES (:id, :fan_id, :pid, 'outbound', :content, :mt, 0, :now)"),
        {"id": msg_id, "fan_id": fan.id, "pid": persona_id, "content": content, "mt": message_type, "now": now},
    )
    await db.execute(
        text("UPDATE fans SET messages_received = messages_received + 1, last_message_at = :now WHERE id = :fid"),
        {"now": now, "fid": fan.id},
    )
    await db.commit()

    return {"status": "sent", "message_id": msg_id}


# ─── Social Accounts (Platform Signup + Approval) ────────────────────

