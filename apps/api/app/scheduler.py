"""The clock — the one thing in this app that runs without being asked.

Every division in `app/divisions.py` declares a cadence. Until this module
existed, not one of them was real: a `ScheduledPost` row was written with a
`scheduled_at`, and nothing ever read it back. That is why `week_events`
reported `"not scheduled"` in its own gap field, and it was true.

Three states, kept separate on purpose — the same "credentials are not
permission" split the publisher already makes:

  * **`SCHEDULER_ENABLED` off** — the loop does not run. Nothing ticks.
  * **enabled, no publisher** — it ticks, finds the due posts, publishes none,
    and reports `blocked` with the reason. It does *not* retry-storm: a due post
    stays `scheduled` so that it can still go out when credentials arrive.
  * **enabled, publisher armed** — due posts go out, one per tick by default.

A post whose window has passed by more than `SCHEDULER_MAX_LATENESS_SECONDS` is
marked `missed`, never published late. A six-hour-old calendar slot is not a
post that is late; it is a post whose moment has gone, and shipping it now puts
the wrong thing in front of fans at the wrong time.

This module publishes nothing on its own authority. It resolves the publisher
through the same `get_publisher()` the route uses, so `FANVUE_PUBLISH_ENABLED`
still governs — an armed clock with an unarmed publisher is a `blocked` tick.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ScheduledPost
from app.providers.publish import (
    PublishDisabled,
    PublishError,
    PublishNotConfigured,
    get_publisher,
)
from app.publishing import PublishRefused, publish_post

logger = logging.getLogger(__name__)

_loop_task: asyncio.Task | None = None

STATE: dict = {
    "enabled": False,
    "running": False,
    "interval_seconds": 0,
    "ticks": 0,
    "last_tick_at": None,
    "next_tick_at": None,
    "due_now": 0,
    "published_total": 0,
    "failed_total": 0,
    "missed_total": 0,
    "blocked": "",
    "blocked_detail": "",
    "last_error": "",
    "last_actions": [],
}


def _as_aware(value: datetime | None) -> datetime | None:
    """SQLite's `DateTime(timezone=True)` does not preserve the offset.

    Every timestamp comes back naive, and comparing a naive `scheduled_at` to an
    aware `datetime.now(timezone.utc)` raises `TypeError: can't compare
    offset-naive and offset-aware datetimes` — which would fail every tick, for
    every post, with a message that names neither the post nor the reason. So a
    naive value is read as UTC, which is what the column is written as.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _resolve_publisher(publisher_factory):
    """The publisher, or the reason there is none — never an exception upward."""
    factory = publisher_factory or get_publisher
    try:
        return factory(), ""
    except PublishNotConfigured as exc:
        return None, f"not_configured: {exc}"
    except PublishDisabled as exc:
        return None, f"disabled: {exc}"
    except PublishError as exc:
        return None, f"error: {exc}"


async def tick(
    *,
    session_factory=None,
    publisher_factory=None,
    now: datetime | None = None,
) -> dict:
    """One pass of the clock. Returns what it found and what it did.

    Pure enough to call directly in a test: pass a `session_factory` bound to
    the test engine and a `publisher_factory` returning a fake.
    """
    settings = get_settings()
    moment = now or datetime.now(timezone.utc)
    max_lateness = timedelta(seconds=settings.SCHEDULER_MAX_LATENESS_SECONDS)
    max_per_tick = max(1, settings.SCHEDULER_MAX_PER_TICK)

    if session_factory is None:
        from app.database import AsyncSessionLocal

        session_factory = AsyncSessionLocal

    actions: list[dict] = []
    published = failed = missed = 0

    async with session_factory() as db:  # type: AsyncSession
        rows = (
            await db.execute(
                select(ScheduledPost)
                .where(ScheduledPost.status == "scheduled")
                .where(ScheduledPost.scheduled_at <= moment)
                .order_by(ScheduledPost.scheduled_at)
            )
        ).scalars().all()

        due_rows = [r for r in rows if _as_aware(r.scheduled_at) is not None]
        STATE["due_now"] = len(due_rows)

        cutoff = moment - max_lateness
        overdue = [r for r in due_rows if _as_aware(r.scheduled_at) < cutoff]
        fresh = [r for r in due_rows if r not in overdue]

        # Missed posts are closed out first and unconditionally — the clock can
        # do that without permission, because the outcome is "not posted".
        for row in overdue:
            row.status = "missed"
            row.metadata_json = {
                **(row.metadata_json or {}),
                "missed_reason": (
                    f"due {_as_aware(row.scheduled_at).isoformat()}, more than "
                    f"{settings.SCHEDULER_MAX_LATENESS_SECONDS}s before this tick"
                ),
            }
            missed += 1
            actions.append({"post_id": row.id, "action": "missed"})
        if overdue:
            await db.commit()

        if not fresh:
            STATE.update(last_actions=actions)
            STATE["missed_total"] += missed
            return {
                "due": len(due_rows),
                "published": 0,
                "failed": 0,
                "missed": missed,
                "blocked": "",
                "actions": actions,
            }

        publisher, blocked = _resolve_publisher(publisher_factory)
        if publisher is None:
            # Nothing is attempted and nothing is written: the due posts stay
            # `scheduled` so they can still go out once the gate opens.
            STATE["blocked"] = blocked.split(":", 1)[0]
            STATE["blocked_detail"] = blocked
            STATE.update(last_actions=actions)
            STATE["missed_total"] += missed
            for row in fresh:
                actions.append({"post_id": row.id, "action": "blocked"})
            return {
                "due": len(due_rows),
                "published": 0,
                "failed": 0,
                "missed": missed,
                "blocked": blocked,
                "actions": actions,
            }

        STATE["blocked"] = ""
        STATE["blocked_detail"] = ""

        for row in fresh[:max_per_tick]:
            try:
                await publish_post(db, row.id, audience="subscribers", publisher=publisher)
                published += 1
                actions.append({"post_id": row.id, "action": "published"})
            except PublishRefused as refused:
                # 502 already wrote `failed` on the row inside publish_post;
                # 409 means the post cannot go out as it stands. Either way this
                # is not a published post and it is not counted as one.
                if refused.status_code == 502:
                    failed += 1
                    actions.append({"post_id": row.id, "action": "failed", "detail": refused.detail})
                else:
                    actions.append({
                        "post_id": row.id, "action": "refused",
                        "status": refused.status_code, "detail": refused.detail,
                    })
            except Exception as exc:  # noqa: BLE001 — a tick must never die
                failed += 1
                STATE["last_error"] = f"{type(exc).__name__}: {exc}"
                actions.append({"post_id": row.id, "action": "error", "detail": str(exc)})

    STATE["published_total"] += published
    STATE["failed_total"] += failed
    STATE["missed_total"] += missed
    STATE["last_actions"] = actions
    return {
        "due": len(due_rows),
        "published": published,
        "failed": failed,
        "missed": missed,
        "blocked": "",
        "actions": actions,
    }


async def _loop() -> None:
    settings = get_settings()
    interval = max(5, settings.SCHEDULER_INTERVAL_SECONDS)
    while True:
        try:
            await tick()
            STATE["last_error"] = ""
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the loop outlives any bad tick
            STATE["last_error"] = f"{type(exc).__name__}: {exc}"
            logger.exception("scheduler_tick_failed")
        STATE["ticks"] += 1
        STATE["last_tick_at"] = datetime.now(timezone.utc).isoformat()
        STATE["next_tick_at"] = (
            datetime.now(timezone.utc) + timedelta(seconds=interval)
        ).isoformat()
        await asyncio.sleep(interval)


async def start() -> bool:
    """Start the loop if the operator enabled it. Returns whether it started."""
    global _loop_task
    settings = get_settings()
    STATE["enabled"] = bool(settings.SCHEDULER_ENABLED)
    STATE["interval_seconds"] = settings.SCHEDULER_INTERVAL_SECONDS
    if not settings.SCHEDULER_ENABLED:
        return False
    if _loop_task is not None and not _loop_task.done():
        return True
    _loop_task = asyncio.create_task(_loop())
    STATE["running"] = True
    logger.info("scheduler_started interval=%ss", settings.SCHEDULER_INTERVAL_SECONDS)
    return True


async def stop() -> None:
    global _loop_task
    if _loop_task is not None:
        _loop_task.cancel()
        try:
            await _loop_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _loop_task = None
    STATE["running"] = False


def status() -> dict:
    """What the clock is doing, in the same honest voice as the divisions view."""
    settings = get_settings()
    enabled = bool(settings.SCHEDULER_ENABLED)
    running = _loop_task is not None and not _loop_task.done()
    return {
        "enabled": enabled,
        "running": running,
        "interval_seconds": settings.SCHEDULER_INTERVAL_SECONDS,
        "max_per_tick": settings.SCHEDULER_MAX_PER_TICK,
        "max_lateness_seconds": settings.SCHEDULER_MAX_LATENESS_SECONDS,
        "ticks": STATE["ticks"],
        "last_tick_at": STATE["last_tick_at"],
        "next_tick_at": STATE["next_tick_at"],
        "due_now": STATE["due_now"],
        "published_total": STATE["published_total"],
        "failed_total": STATE["failed_total"],
        "missed_total": STATE["missed_total"],
        "blocked": STATE["blocked"],
        "blocked_detail": STATE["blocked_detail"],
        "last_error": STATE["last_error"],
        "last_actions": STATE["last_actions"],
        "note": (
            "Nothing is scheduled while SCHEDULER_ENABLED is false — the "
            "calendar is a list, not a queue."
            if not enabled
            else "The clock is armed. Due posts publish through the same path as "
                 "the manual route, including its rule that a post is only "
                 "marked posted on a real platform id."
        ),
    }


def _reset_state_for_tests() -> None:
    """Put the singleton back to its import state. Test-only."""
    for key, value in {
        "ticks": 0, "last_tick_at": None, "next_tick_at": None, "due_now": 0,
        "published_total": 0, "failed_total": 0, "missed_total": 0,
        "blocked": "", "blocked_detail": "", "last_error": "", "last_actions": [],
    }.items():
        STATE[key] = value
