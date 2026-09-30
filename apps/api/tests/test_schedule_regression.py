"""Regression tests for the schedule calendar.

`ScheduledPost.persona_id` is a String(36) column — the only persona foreign key
in the models that is *not* a UUID(as_uuid=True) column (Persona.id,
ContentPack.persona_id and Shoot.persona_id all are). Rows are written with
str(persona_id), so they hold the dashed spelling.

`GET /personas/{id}/schedule` bound a UUID object against that String column and
matched nothing, so it returned an empty list for every persona no matter how
many posts were scheduled — a silent empty result rather than an error.

This also explains a false positive from `PRAGMA foreign_key_check`:
personas.id stores dashless CHAR(32) while scheduled_posts.persona_id stores
dashed, so the constraint never matches textually even though every row's
persona exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models import Persona, ScheduledPost


async def _persona_with_posts(db, n: int = 3) -> tuple[Persona, list[str]]:
    """A persona plus posts written exactly the way the routes write them."""
    persona = Persona(id=uuid4(), name=f"sched-{uuid4().hex[:8]}")
    db.add(persona)
    await db.commit()

    now = datetime.now(timezone.utc)
    ids = []
    for i in range(n):
        post = ScheduledPost(
            id=str(uuid4()),
            # The write path: a dashed string, not a UUID object.
            persona_id=str(persona.id),
            platform="instagram",
            content_type="image",
            caption=f"post {i}",
            scheduled_at=now + timedelta(days=i),
            status="scheduled",
        )
        db.add(post)
        ids.append(post.id)
    await db.commit()
    return persona, ids


async def test_schedule_endpoint_returns_scheduled_posts(client, db):
    """The read must find rows written with str(persona_id)."""
    persona, post_ids = await _persona_with_posts(db, n=3)

    resp = await client.get(f"/api/v1/personas/{persona.id}/schedule")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 3, f"expected 3 scheduled posts, got {len(body)}"
    assert {p["id"] for p in body} == set(post_ids)
    # Ordered by scheduled_at.
    assert [p["scheduled_at"] for p in body] == sorted(p["scheduled_at"] for p in body)


async def test_schedule_endpoint_accepts_dashless_uuid(client, db):
    """UUID() accepts the dashless hex spelling too — both must reach the rows."""
    persona, _ = await _persona_with_posts(db, n=2)

    resp = await client.get(f"/api/v1/personas/{persona.id.hex}/schedule")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 2


async def test_schedule_is_empty_for_persona_without_posts(client, db):
    """Empty is still correct for a persona that genuinely has no posts."""
    persona, _ = await _persona_with_posts(db, n=0)

    resp = await client.get(f"/api/v1/personas/{persona.id}/schedule")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_smart_schedule_all_writes_dashed_persona_id(client, db):
    """smart_schedule_all bound p.id (a UUID object) to the same String column.

    Whatever it stored had to be readable back by the schedule endpoint, so pin
    that the two halves agree.
    """
    from app.models import ContentPack

    persona = Persona(id=uuid4(), name=f"sched-all-{uuid4().hex[:8]}", status="active")
    db.add(persona)
    await db.commit()

    pack = ContentPack(
        id=uuid4(),                     # ContentPack.id is a UUID column
        persona_id=persona.id,          # ContentPack.persona_id is a real UUID column
        name="pack",
        status="assembled",             # ContentPackStatus: draft|assembled|published|failed
        images=["key1.jpg"],
    )
    db.add(pack)
    await db.commit()

    resp = await client.post("/api/v1/schedule/smart-all")
    assert resp.status_code == 200, resp.text

    listed = await client.get(f"/api/v1/personas/{persona.id}/schedule")
    assert listed.status_code == 200, listed.text
    posts = listed.json()
    assert posts, "smart-all created posts that the schedule read cannot find"
    assert all(p["persona_id"] == str(persona.id) for p in posts)
