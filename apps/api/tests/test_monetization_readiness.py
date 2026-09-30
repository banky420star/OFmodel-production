"""Does the monetization page measure its gates, or assert them?

Measured 2026-09-30: `/monetization` rendered four "launch gates" and two of
them returned `done: True` unconditionally — "Human approval workflow" was a
literal, and "Rights and consent records" was `Boolean(summary?.health)`, true
whenever the dashboard happened to load. "Synthetic identity declared" was
inferred from a persona row existing, which is not evidence that any profile
carries a disclosure. A gate that cannot fail is decoration, on the one page
whose whole job is to say when money can start arriving.

These tests hold the replacement to the only property that matters: **every
gate flips with its own input.** A gate is checked in both directions, so a
hardcoded `True` cannot pass and a gate that silently stopped reading anything
cannot either.

The five inputs are independent on purpose — configuration for four of them and
the calendar for the fifth — so a deployment can be connected and unpriced, or
priced and unarmed, and read as exactly that.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import ScheduledPost

GATES = {
    "platform_connected",
    "publishing_armed",
    "price_stated",
    "content_ready",
    "clock_armed",
}


class _Publisher:
    """A publisher whose health is controllable, so `connected` can be toggled."""

    name = "fanvue"
    supports_price = True

    def __init__(self, *, healthy=True, detail="authenticated"):
        self._healthy = healthy
        self._detail = detail

    async def health_check(self):
        return self._healthy, self._detail


def _raise(monkeypatch, exc):
    def _get():
        raise exc

    monkeypatch.setattr("app.providers.publish.get_publisher", _get)


def _healthy(monkeypatch, **kwargs):
    monkeypatch.setattr("app.providers.publish.get_publisher", lambda: _Publisher(**kwargs))


def _setting(monkeypatch, name, value):
    monkeypatch.setattr(get_settings(), name, value, raising=False)


def _gate(body: dict, key: str) -> dict:
    found = [c for c in body["checks"] if c["key"] == key]
    assert found, f"{key} is not among the gates: {[c['key'] for c in body['checks']]}"
    return found[0]


async def _readiness(client) -> dict:
    resp = await client.get("/api/v1/monetization/readiness")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _counts(client) -> tuple[int, int, int]:
    """`(with_media, scheduled, priced)` from the content gate's own detail.

    The content tests measure deltas rather than absolutes. The `db` fixture
    rolls back at teardown but `_seed` commits, so rows from earlier tests in
    this session are still there — a test that asserted "1 of 1" would pass or
    fail on the order pytest happened to run in.

    The denominator is posts on a platform with a publishing adapter, which is
    why the phrase is spelled out — see the content gate in `app/readiness.py`
    for why counting the 42 stranded legacy rows would misstate the task.
    """
    import re

    detail = _gate(await _readiness(client), "content_ready")["detail"]
    match = re.match(
        r"(\d+) of (\d+) scheduled posts on a platform this app can publish to "
        r"carry media; (\d+) carry",
        detail,
    )
    assert match, f"content detail is not the expected shape: {detail!r}"
    return tuple(int(g) for g in match.groups())  # type: ignore[return-value]


async def _seed(db, *, media=("storage/shoots/a/shot_01.png",), price=5.0, status="scheduled",
                  platform="fanvue"):
    post = ScheduledPost(
        id=str(uuid.uuid4()),
        persona_id=str(uuid.uuid4()),
        platform=platform,
        media_keys=list(media),
        ppv_price=price,
        status=status,
        scheduled_at=datetime.now(timezone.utc),
    )
    db.add(post)
    await db.commit()
    return post


# ── the shape ────────────────────────────────────────────────────────────


async def test_the_five_gates_are_all_present_and_named(client):
    body = await _readiness(client)
    assert {c["key"] for c in body["checks"]} == GATES
    assert isinstance(body["ready"], bool)
    assert isinstance(body["blockers"], list)
    # Every gate is either done, or says what would make it done. A not-done
    # gate with no next step is a red light with no lever.
    for check in body["checks"]:
        assert check["detail"], f"{check['key']} reports no detail"
        assert check["done"] or check["next_step"], f"{check['key']} has no next step"


async def test_ready_is_exactly_the_absence_of_blockers(client):
    body = await _readiness(client)
    assert body["ready"] == (not body["blockers"])
    assert set(body["blockers"]) == {c["key"] for c in body["checks"] if not c["done"]}


# ── every gate flips with its own input ──────────────────────────────────


async def test_the_connected_gate_flips(client, monkeypatch):
    from app.providers.publish import PublishNotConfigured

    _raise(monkeypatch, PublishNotConfigured("FANVUE_CLIENT_ID is empty"))
    _setting(monkeypatch, "FANVUE_PUBLISH_ENABLED", True)
    assert _gate(await _readiness(client), "platform_connected")["done"] is False

    _healthy(monkeypatch)
    assert _gate(await _readiness(client), "platform_connected")["done"] is True


async def test_a_token_that_does_not_authenticate_is_not_connected(client, monkeypatch):
    """Configured is not connected. A publisher object that 401s is exactly what
    an expired token produces, and calling that 'connected' would send the
    operator to fix a different thing."""
    _setting(monkeypatch, "FANVUE_PUBLISH_ENABLED", True)
    _healthy(monkeypatch, healthy=False, detail="Fanvue returned 401 — the token is expired")
    check = _gate(await _readiness(client), "platform_connected")
    assert check["done"] is False
    assert "401" in check["detail"]


async def test_the_armed_gate_flips_independently_of_connected(client, monkeypatch):
    """The two are separate on purpose: a credential copied in for a read-only
    check must not read as authority to post."""
    from app.providers.publish import PublishDisabled

    _raise(monkeypatch, PublishDisabled("FANVUE_PUBLISH_ENABLED is False"))
    body = await _readiness(client)
    assert _gate(body, "platform_connected")["done"] is True
    assert _gate(body, "publishing_armed")["done"] is False

    _healthy(monkeypatch)
    body = await _readiness(client)
    assert _gate(body, "platform_connected")["done"] is True
    assert _gate(body, "publishing_armed")["done"] is True


async def test_the_price_gate_flips_and_names_the_floor(client, monkeypatch):
    _healthy(monkeypatch)
    _setting(monkeypatch, "FANVUE_PUBLISH_ENABLED", True)

    _setting(monkeypatch, "DEFAULT_PPV_PRICE", None)
    check = _gate(await _readiness(client), "price_stated")
    assert check["done"] is False
    assert "price" in check["detail"].lower()

    # Below Fanvue's $3.00 floor: the platform would refuse it at publish time,
    # so the gate has to refuse it now.
    _setting(monkeypatch, "DEFAULT_PPV_PRICE", 1.0)
    check = _gate(await _readiness(client), "price_stated")
    assert check["done"] is False
    assert "3.00" in check["detail"]

    _setting(monkeypatch, "DEFAULT_PPV_PRICE", 5.0)
    check = _gate(await _readiness(client), "price_stated")
    assert check["done"] is True
    assert "5.00" in check["detail"]


async def test_the_content_gate_needs_media_and_not_merely_a_slot(client, db, monkeypatch):
    """A scheduled row with no media is refused at publish time, so it is not
    content — and a calendar of them reads as a healthy calendar."""
    _healthy(monkeypatch)
    _setting(monkeypatch, "FANVUE_PUBLISH_ENABLED", True)
    _setting(monkeypatch, "DEFAULT_PPV_PRICE", 5.0)

    with_media, scheduled, priced = await _counts(client)

    # A slot with no media is not content: exactly what the 42 legacy calendar
    # rows are, and they read as a healthy calendar in a count of slots.
    await _seed(db, media=[])
    assert await _counts(client) == (with_media, scheduled + 1, priced)

    # Media but no price is content that cannot be charged for, so it does not
    # make the gate true either.
    await _seed(db, price=None)
    assert await _counts(client) == (with_media + 1, scheduled + 2, priced)

    before = await _counts(client)
    await _seed(db)
    assert await _counts(client) == (before[0] + 1, before[1] + 1, before[2] + 1)
    assert _gate(await _readiness(client), "content_ready")["done"] is True


async def test_a_posted_row_is_not_scheduled_content(client, db, monkeypatch):
    """It has already gone out. Counting it would report inventory that does not
    exist."""
    _healthy(monkeypatch)
    _setting(monkeypatch, "FANVUE_PUBLISH_ENABLED", True)
    _setting(monkeypatch, "DEFAULT_PPV_PRICE", 5.0)

    before = await _counts(client)
    await _seed(db, status="posted")
    assert await _counts(client) == before


async def test_a_post_on_a_platform_with_no_publisher_is_not_counted(client, db, monkeypatch):
    """The 42 legacy rows are on instagram/tiktok/youtube, and `publisher_classes()`
    has no entry for any of them.

    Counting them made this gate say "0 of 42" about an operator's content task,
    when every one of those rows is unshippable whatever media it carries. The
    count is over platforms this app can publish to; the stranded rows are named
    beside it instead of folded into the denominator.
    """
    _healthy(monkeypatch)
    _setting(monkeypatch, "FANVUE_PUBLISH_ENABLED", True)
    _setting(monkeypatch, "DEFAULT_PPV_PRICE", 5.0)

    before = await _counts(client)
    await _seed(db, platform="instagram")  # media and a price, and still unshippable
    assert await _counts(client) == before, "an unshippable row moved the denominator"

    detail = _gate(await _readiness(client), "content_ready")["detail"]
    assert "instagram" in detail, "the stranded rows must be named, not hidden"
    assert "no publishing adapter exists" in detail


async def test_the_gate_cannot_go_green_on_rows_no_publisher_can_ship(client, db, monkeypatch):
    """The failure that matters: `done` asked only whether any post carried media
    and a price. One priced Instagram row would have turned this green over a
    calendar from which nothing can be sold — a false green on the one gate whose
    job is to say when money can start arriving."""
    _healthy(monkeypatch)
    _setting(monkeypatch, "FANVUE_PUBLISH_ENABLED", True)
    _setting(monkeypatch, "DEFAULT_PPV_PRICE", 5.0)

    from app.models import ScheduledPost

    # This session's earlier tests committed fanvue rows through `_seed` (the `db`
    # fixture only rolls back its *own* writes), so they are retired here rather
    # than deleted — otherwise the gate would be green for a reason unrelated to
    # what this test is about.
    existing = (
        await db.execute(select(ScheduledPost).where(ScheduledPost.status == "scheduled"))
    ).scalars().all()
    for row in existing:
        row.status = "posted"
    await db.commit()

    await _seed(db, platform="instagram")
    await _seed(db, platform="tiktok")

    body = await _readiness(client)
    gate = _gate(body, "content_ready")
    assert gate["done"] is False, f"false green: {gate['detail']}"
    assert body["ready"] is False
    assert "content_ready" in body["blockers"]


async def test_the_clock_gate_flips(client, monkeypatch):
    _setting(monkeypatch, "SCHEDULER_ENABLED", False)
    assert _gate(await _readiness(client), "clock_armed")["done"] is False

    _setting(monkeypatch, "SCHEDULER_ENABLED", True)
    assert _gate(await _readiness(client), "clock_armed")["done"] is True


# ── all five, and the one that matters ───────────────────────────────────


async def test_all_five_clear_together_and_only_then(client, db, monkeypatch):
    """The property the page exists to report. Each input is set last in turn,
    and `ready` must stay False for every state but the last."""
    _setting(monkeypatch, "DEFAULT_PPV_PRICE", 5.0)
    _setting(monkeypatch, "SCHEDULER_ENABLED", False)
    _healthy(monkeypatch)
    await _seed(db)

    from app.providers.publish import PublishNotConfigured, PublishDisabled

    _raise(monkeypatch, PublishNotConfigured("nothing connected"))
    assert (await _readiness(client))["ready"] is False

    _raise(monkeypatch, PublishDisabled("not armed"))
    assert (await _readiness(client))["ready"] is False

    _healthy(monkeypatch)
    _setting(monkeypatch, "DEFAULT_PPV_PRICE", None)
    assert (await _readiness(client))["ready"] is False

    _setting(monkeypatch, "DEFAULT_PPV_PRICE", 5.0)
    assert (await _readiness(client))["ready"] is False  # clock still off

    _setting(monkeypatch, "SCHEDULER_ENABLED", True)
    body = await _readiness(client)
    assert body["ready"] is True
    assert body["blockers"] == []


async def test_nothing_is_ready_on_an_unconfigured_deployment(client):
    """No monkeypatching: this is the operator's machine today. The gate that
    would have been a hardcoded `True` is the one this catches."""
    body = await _readiness(client)
    assert body["ready"] is False
    assert body["blockers"]
    for check in body["checks"]:
        if not check["done"]:
            assert check["next_step"]


async def test_reading_the_gates_does_not_require_a_publisher_to_exist(client):
    """It must answer, not 500 — an operator reading a readiness page needs the
    list of what is missing, which is precisely the case where nothing exists."""
    resp = await client.get("/api/v1/monetization/readiness")
    assert resp.status_code == 200
    assert resp.json()["platform"] == "fanvue"


async def test_the_platform_is_named_even_when_no_publisher_can_be_built():
    """Without a name in the failure states a caller cannot tell which platform's
    floor and rules apply — and a caller without that answer hardcodes its own,
    which is the drift `resolved_price_minor` exists to prevent."""
    from app.providers.publish import publisher_state

    state = await publisher_state()
    assert state["provider"] == "fanvue"
    assert state["state"] in ("not_configured", "disabled", "unhealthy", "error")
    assert state["configured"] is False
    assert state["armed"] is False
