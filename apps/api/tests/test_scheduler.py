"""The clock — the part of the studio that acts without being asked.

Two things must stay true or this module becomes the most dangerous file in the
repo: it publishes nothing on its own authority (the publisher's own arm switch
still governs), and it never publishes a post late enough to be wrong. These pin
both, plus the datetime trap that would otherwise fail every tick silently.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app import scheduler
from app.config import get_settings
from app.models import Persona, ScheduledPost

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)


class _FakePublisher:
    name = "fake"

    def __init__(self, *, post_uuid="pu_1", ok=True, error=""):
        self.post_uuid = post_uuid
        self.ok = ok
        self.error = error
        self.calls: list[dict] = []

    async def health_check(self):
        return True, "fake"

    async def create_post(self, *, text, media_paths, price_minor=None,
                          audience="subscribers", publish_at=None):
        from app.providers.publish import PublishResult

        self.calls.append({"text": text, "price_minor": price_minor})
        return PublishResult(ok=self.ok, post_uuid=self.post_uuid,
                             error=self.error, detail={})


@pytest_asyncio.fixture(autouse=True)
async def _clean_state():
    """Reset everything the module keeps at import/process scope.

    Three things outlive a test and each breaks the next one differently: the
    STATE singleton (totals accumulate), the loop task (a leaked one makes
    `start()` a no-op), and committed `ScheduledPost` rows — which outlive the
    `db` fixture's rollback and, because the clock selects by *status*, get
    picked up by the next test's tick (oldest first), so its assertions end up
    describing a post another test made.
    """
    from sqlalchemy import delete

    from app.database import AsyncSessionLocal

    await scheduler.stop()
    scheduler._reset_state_for_tests()
    async with AsyncSessionLocal() as session:
        await session.execute(delete(ScheduledPost))
        await session.commit()

    yield

    await scheduler.stop()
    scheduler._reset_state_for_tests()


async def _persona(db, name=None):
    # `personas.name` is UNIQUE and a committed row outlives the `db` fixture's
    # rollback, so two tests reusing a name collide instead of starting clean.
    p = Persona(id=uuid.uuid4(), name=name or f"Sched-{uuid.uuid4().hex[:8]}", age=25)
    db.add(p)
    await db.commit()
    return p


async def _post(db, persona, *, due_in_seconds=0, caption="hi", status="scheduled", **kw):
    post = ScheduledPost(
        id=str(uuid.uuid4()),
        persona_id=str(persona.id),
        platform="fanvue",
        caption=caption,
        status=status,
        scheduled_at=NOW + timedelta(seconds=due_in_seconds),
        **kw,
    )
    db.add(post)
    await db.commit()
    return post


# ── the naive-datetime trap ──────────────────────────────────────────────


def test_a_stored_timestamp_is_read_back_as_utc():
    """SQLite drops the offset, so every `scheduled_at` is naive on read. A tick
    that compares it to an aware `now()` raises TypeError and fails — for every
    post, every minute, naming neither the post nor the reason."""
    naive = datetime(2026, 9, 30, 12, 0, 0)
    assert scheduler._as_aware(naive).tzinfo is timezone.utc
    aware = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
    assert scheduler._as_aware(aware) is aware
    assert scheduler._as_aware(None) is None


# ── off by default ───────────────────────────────────────────────────────


def test_the_scheduler_is_off_unless_switched_on():
    settings = get_settings()
    assert settings.SCHEDULER_ENABLED is False


async def test_start_does_nothing_when_disabled():
    assert await scheduler.start() is False
    body = scheduler.status()
    assert body["enabled"] is False
    assert body["running"] is False


def test_status_says_the_calendar_is_not_a_queue_when_off():
    body = scheduler.status()
    assert "not a queue" in body["note"]


# ── nothing due ──────────────────────────────────────────────────────────


async def test_a_future_post_is_not_due(db):
    persona = await _persona(db)
    await _post(db, persona, due_in_seconds=3600)
    result = await scheduler.tick(now=NOW, publisher_factory=lambda: _FakePublisher())
    assert result["due"] == 0
    assert result["published"] == 0


async def test_no_due_posts_is_a_quiet_tick(db):
    result = await scheduler.tick(now=NOW, publisher_factory=lambda: _FakePublisher())
    assert result == {"due": 0, "published": 0, "failed": 0, "missed": 0,
                      "blocked": "", "actions": []}


# ── credentials are not permission ───────────────────────────────────────


async def test_without_a_publisher_it_reports_blocked_and_publishes_nothing(db):
    from app.providers.publish import PublishNotConfigured

    persona = await _persona(db)
    post = await _post(db, persona, due_in_seconds=-30)

    def _raise():
        raise PublishNotConfigured("FANVUE_CLIENT_ID is empty")

    result = await scheduler.tick(now=NOW, publisher_factory=_raise)
    assert result["published"] == 0
    assert result["blocked"].startswith("not_configured")

    # The post is untouched: still `scheduled`, so it can go out once the gate
    # opens within the lateness window.
    await db.refresh(post)
    assert post.status == "scheduled"
    assert scheduler.status()["blocked"] == "not_configured"


async def test_an_armed_clock_with_an_unarmed_publisher_is_also_blocked(db):
    """FANVUE_PUBLISH_ENABLED is a separate switch from the credentials, and the
    clock does not collapse the two."""
    from app.providers.publish import PublishDisabled

    persona = await _persona(db)
    await _post(db, persona, due_in_seconds=-30)

    def _raise():
        raise PublishDisabled("FANVUE_PUBLISH_ENABLED is False")

    result = await scheduler.tick(now=NOW, publisher_factory=_raise)
    assert result["published"] == 0
    assert result["blocked"].startswith("disabled")


# ── late is not the same as missed ───────────────────────────────────────


async def test_a_post_past_its_window_is_missed_not_published(db):
    """The safety rule: a six-hour-old slot is not late, it is gone. Publishing
    it now would put the wrong post in front of fans at the wrong time."""
    persona = await _persona(db)
    stale = await _post(db, persona, due_in_seconds=-(7 * 3600))

    fake = _FakePublisher()
    result = await scheduler.tick(now=NOW, publisher_factory=lambda: fake)

    assert result["missed"] == 1
    assert result["published"] == 0
    assert fake.calls == []
    await db.refresh(stale)
    assert stale.status == "missed"
    assert "missed_reason" in (stale.metadata_json or {})


async def test_a_post_inside_its_window_still_publishes(db):
    persona = await _persona(db)
    fresh = await _post(db, persona, due_in_seconds=-300)

    fake = _FakePublisher()
    result = await scheduler.tick(now=NOW, publisher_factory=lambda: fake)

    assert result["published"] == 1
    await db.refresh(fresh)
    assert fresh.status == "posted"
    assert (fresh.metadata_json or {}).get("post_uuid") == "pu_1"


async def test_missing_a_post_does_not_need_a_publisher(db):
    """Closing out a missed slot is an outcome of "not posted", so the clock may
    do it without permission — it must not sit there blocked forever."""
    persona = await _persona(db)
    stale = await _post(db, persona, due_in_seconds=-(7 * 3600))

    def _raise():
        from app.providers.publish import PublishNotConfigured
        raise PublishNotConfigured("nothing configured")

    result = await scheduler.tick(now=NOW, publisher_factory=_raise)
    assert result["missed"] == 1
    await db.refresh(stale)
    assert stale.status == "missed"


# ── the honesty rule survives the clock ──────────────────────────────────


async def test_a_response_with_no_post_id_is_not_counted_as_published(db):
    """The clock must not be the place the no-post-id rule is forgotten."""
    persona = await _persona(db)
    post = await _post(db, persona, due_in_seconds=-10)

    fake = _FakePublisher(post_uuid="", ok=True)
    result = await scheduler.tick(now=NOW, publisher_factory=lambda: fake)

    assert result["published"] == 0
    assert result["failed"] == 1
    await db.refresh(post)
    assert post.status == "failed"
    assert post.posted_at is None


async def test_a_refused_post_is_not_counted_as_published(db):
    persona = await _persona(db)
    post = await _post(db, persona, due_in_seconds=-10, caption="", ppv_price=20.0)

    fake = _FakePublisher()
    result = await scheduler.tick(now=NOW, publisher_factory=lambda: fake)

    assert result["published"] == 0
    assert fake.calls == []
    assert any(a["action"] == "refused" for a in result["actions"])
    await db.refresh(post)
    assert post.status == "scheduled"


# ── one per tick, oldest first ───────────────────────────────────────────


async def test_only_max_per_tick_posts_go_out_in_one_pass(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "SCHEDULER_MAX_PER_TICK", 1, raising=False)

    persona = await _persona(db)
    first = await _post(db, persona, due_in_seconds=-300, caption="first")
    second = await _post(db, persona, due_in_seconds=-200, caption="second")

    fake = _FakePublisher()
    result = await scheduler.tick(now=NOW, publisher_factory=lambda: fake)

    assert result["published"] == 1
    assert len(fake.calls) == 1
    assert fake.calls[0]["text"] == "first", "the oldest due post goes first"
    await db.refresh(first)
    await db.refresh(second)
    assert first.status == "posted"
    assert second.status == "scheduled", "the rest wait for the next tick"


async def test_totals_accumulate_across_ticks(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "SCHEDULER_MAX_PER_TICK", 1, raising=False)

    persona = await _persona(db)
    await _post(db, persona, due_in_seconds=-300, caption="a")
    await _post(db, persona, due_in_seconds=-200, caption="b")

    fake = _FakePublisher()
    await scheduler.tick(now=NOW, publisher_factory=lambda: fake)
    await scheduler.tick(now=NOW + timedelta(seconds=60), publisher_factory=lambda: fake)

    assert scheduler.status()["published_total"] == 2


# ── a tick never dies ────────────────────────────────────────────────────


async def test_a_publisher_that_explodes_does_not_kill_the_clock(db):
    persona = await _persona(db)
    post = await _post(db, persona, due_in_seconds=-10)

    class _Boom:
        name = "boom"

        async def create_post(self, **_kw):
            raise RuntimeError("connection reset")

    result = await scheduler.tick(now=NOW, publisher_factory=lambda: _Boom())
    assert result["failed"] == 1
    assert "connection reset" in scheduler.status()["last_error"]
    await db.refresh(post)
    assert post.status == "scheduled", "an unexpected error must not fake a status"


# ── the route ────────────────────────────────────────────────────────────


async def test_the_status_route_never_errors(client):
    resp = await client.get("/api/v1/scheduler/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert "note" in body


async def test_the_tick_route_runs_a_pass(client):
    resp = await client.post("/api/v1/scheduler/tick")
    assert resp.status_code == 200
    body = resp.json()
    assert body["due"] == 0
    assert body["published"] == 0


# ── arming and disarming the loop ────────────────────────────────────────


async def test_enabling_the_setting_starts_a_loop_that_can_be_stopped(monkeypatch):
    """The lifespan's whole contract: SCHEDULER_ENABLED true must arm a task,
    and `stop()` must leave nothing running behind it."""
    import asyncio

    monkeypatch.setattr(get_settings(), "SCHEDULER_ENABLED", True, raising=False)
    monkeypatch.setattr(get_settings(), "SCHEDULER_INTERVAL_SECONDS", 5, raising=False)

    assert await scheduler.start() is True
    assert scheduler.status()["running"] is True
    assert scheduler.status()["interval_seconds"] == 5

    # One tick happens immediately, before the first sleep.
    await asyncio.sleep(0.2)
    assert scheduler.status()["ticks"] >= 1

    await scheduler.stop()
    assert scheduler.status()["running"] is False


async def test_starting_twice_does_not_run_two_loops(monkeypatch):
    monkeypatch.setattr(get_settings(), "SCHEDULER_ENABLED", True, raising=False)
    assert await scheduler.start() is True
    first = scheduler._loop_task
    assert await scheduler.start() is True
    assert scheduler._loop_task is first
    await scheduler.stop()
