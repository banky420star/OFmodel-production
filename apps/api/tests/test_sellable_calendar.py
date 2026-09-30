"""Can the calendar produce anything a fan could actually pay for?

The measured answer used to be no, and not because of any one bug. The
scheduler hardcoded `["instagram", "tiktok", "youtube"]` — none of which this
app can post to, and none of which permit a disclosed AI adult persona — while
`fanvue`, the one adapter that exists, appeared nowhere in the file. And no
production code ever assigned `ScheduledPost.ppv_price`, so every row carried
the column's own "null = free post" and the whole calendar was a giveaway.

The tests here pin the two halves of the repair: a platform that sells needs a
price stated before a slot is written, and a platform that cannot sell keeps
behaving exactly as it did (free, and not refused for it).

The price itself is never invented. `DEFAULT_PPV_PRICE` is unset by default and
unset means *unstated*, which is a refusal — not zero.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.config import get_settings
from app.models import ContentPack, Persona, ScheduledPost, SocialAccount
from app.publishing import (
    PublishRefused,
    platform_supports_price,
    publisher_classes,
    resolved_price_minor,
)


def _stated_price(monkeypatch, price):
    """Set the operator's stated price on the *cached* settings instance.

    `get_settings()` is `lru_cache`d, so the object the routes read was built
    at first import — `monkeypatch.setenv` after that is invisible to it.
    """
    monkeypatch.setattr(get_settings(), "DEFAULT_PPV_PRICE", price, raising=False)


# ── which platforms sell ─────────────────────────────────────────────────


def test_fanvue_is_discovered_as_a_publisher_that_sells():
    classes = publisher_classes()
    assert "fanvue" in classes, "the adapter registry lost the one adapter there is"
    assert classes["fanvue"].supports_price is True


def test_a_platform_with_no_adapter_is_not_assumed_to_sell():
    """Fails safe: an unknown platform cannot charge, so nothing is refused."""
    assert platform_supports_price("myspace") is False
    assert platform_supports_price("instagram") is False


# ── resolving a price ────────────────────────────────────────────────────


def test_a_platform_that_cannot_charge_has_no_price():
    assert resolved_price_minor("instagram", 20.0) is None


def test_a_selling_platform_takes_the_stated_price():
    assert resolved_price_minor("fanvue", 20.0) == 2000


def test_a_selling_platform_with_no_stated_price_refuses():
    """The rule. Unset is not zero — a post that goes out free is not a post
    that was priced at nothing."""
    with pytest.raises(PublishRefused) as exc:
        resolved_price_minor("fanvue", None)
    assert "fanvue" in exc.value.detail
    assert "none has been stated" in exc.value.detail


def test_a_price_below_the_platform_floor_is_refused_before_it_is_scheduled():
    """Fanvue would reject it at publish time — after the calendar had already
    promised the slot."""
    with pytest.raises(PublishRefused) as exc:
        resolved_price_minor("fanvue", 1.00)
    assert "$3.00" in exc.value.detail


def test_the_floor_itself_is_accepted():
    assert resolved_price_minor("fanvue", 3.0) == 300


def test_the_floor_lives_on_the_adapter():
    """One number, read by the guard and by the adapter that enforces it."""
    assert publisher_classes()["fanvue"].min_price_minor == 300


def test_a_blank_price_setting_reads_as_unstated():
    """`DEFAULT_PPV_PRICE=` in a .env is a parse error if read literally, which
    would turn "not decided yet" into a crash on boot."""
    from app.config import Settings

    assert Settings(DEFAULT_PPV_PRICE="").DEFAULT_PPV_PRICE is None
    assert Settings(DEFAULT_PPV_PRICE="8.00").DEFAULT_PPV_PRICE == 8.0


# ── the calendar ─────────────────────────────────────────────────────────


async def _persona_ready_to_schedule(db, *, platform: str, images=("a.png", "b.png")):
    persona = Persona(id=uuid4(), name=f"sell-{uuid4().hex[:8]}", status="active")
    db.add(persona)
    await db.commit()

    db.add(ContentPack(
        id=uuid4(),
        persona_id=persona.id,          # ContentPack.persona_id is a UUID column
        name="pack",
        status="assembled",
        images=list(images),
    ))
    db.add(SocialAccount(
        id=str(uuid4()),
        persona_id=str(persona.id),     # SocialAccount.persona_id is a String column
        platform=platform,
        username=f"{platform}-acct",
        status="active",                # only active/approved count as connected
    ))
    await db.commit()
    return persona


async def _schedule(client, persona):
    resp = await client.post(f"/api/v1/personas/{persona.id}/schedule/smart")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _calendar(client, persona):
    resp = await client.get(f"/api/v1/personas/{persona.id}/schedule")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_a_selling_calendar_with_no_stated_price_schedules_nothing(
    client, db, monkeypatch
):
    """The headline. Without a price there is no sellable slot, and the run
    says so rather than filling the calendar with free posts."""
    _stated_price(monkeypatch, None)
    persona = await _persona_ready_to_schedule(db, platform="fanvue")

    body = await _schedule(client, persona)

    assert body["status"] == "unpriceable"
    assert body["posts"] == 0
    assert "fanvue" in body["unpriceable"]
    assert await _calendar(client, persona) == []


async def test_an_unpriceable_run_leaves_the_existing_calendar_alone(
    client, db, monkeypatch
):
    """It used to delete every scheduled row before discovering it could not
    write replacements, which is a calendar wiped by a run that produced
    nothing."""
    _stated_price(monkeypatch, None)
    persona = await _persona_ready_to_schedule(db, platform="fanvue")

    db.add(ScheduledPost(
        id=str(uuid4()),
        persona_id=str(persona.id),
        platform="fanvue",
        caption="already booked",
        scheduled_at=datetime.now(timezone.utc),
        status="scheduled",
    ))
    await db.commit()

    await _schedule(client, persona)

    calendar = await _calendar(client, persona)
    assert [p["caption"] for p in calendar] == ["already booked"]


async def test_a_stated_price_turns_fanvue_slots_into_inventory(
    client, db, monkeypatch
):
    """The point of the whole exercise: slots a fan would have to pay for."""
    _stated_price(monkeypatch, 8.0)
    persona = await _persona_ready_to_schedule(db, platform="fanvue")

    body = await _schedule(client, persona)

    assert body["status"] == "scheduled"
    assert body["posts"] > 0
    assert body["priced_posts"] == body["posts"]
    assert body["price_minor"] == {"fanvue": 800}

    calendar = await _calendar(client, persona)
    assert calendar
    assert all(p["platform"] == "fanvue" for p in calendar)
    assert {p["ppv_price"] for p in calendar} == {8.0}
    # The media is real, so the post could actually ship.
    assert all(p["media_keys"] for p in calendar)


async def test_a_platform_that_cannot_charge_still_gets_free_slots(
    client, db, monkeypatch
):
    """Instagram behaviour is unchanged: free, and not refused for it. The
    guard only exists where a price is possible."""
    _stated_price(monkeypatch, None)
    persona = await _persona_ready_to_schedule(db, platform="instagram")

    body = await _schedule(client, persona)

    assert body["status"] == "scheduled"
    assert body["posts"] > 0
    assert body["priced_posts"] == 0

    calendar = await _calendar(client, persona)
    assert all(p["ppv_price"] is None for p in calendar)


async def test_a_mixed_calendar_prices_only_the_platform_that_sells(
    client, db, monkeypatch
):
    """The real shape: a persona connected to both. The selling platform's
    slots carry a price, the others stay free, and neither is blocked."""
    _stated_price(monkeypatch, 5.0)
    persona = await _persona_ready_to_schedule(db, platform="instagram")
    db.add(SocialAccount(
        id=str(uuid4()),
        persona_id=str(persona.id),
        platform="fanvue",
        username="fanvue-acct",
        status="approved",
    ))
    await db.commit()

    body = await _schedule(client, persona)

    assert body["status"] == "scheduled"
    assert body["priced_posts"] > 0
    assert body["posts"] > body["priced_posts"], "the free platform still gets slots"
    assert body["unpriceable"] == {}

    calendar = await _calendar(client, persona)
    priced = [p for p in calendar if p["platform"] == "fanvue"]
    free = [p for p in calendar if p["platform"] != "fanvue"]
    assert priced and free
    assert {p["ppv_price"] for p in priced} == {5.0}
    assert {p["ppv_price"] for p in free} == {None}


async def test_nothing_connected_writes_nothing_rather_than_guessing_platforms(
    client, db, monkeypatch
):
    """The removed fallback.

    `active_platforms` used to be `... if socials else ["instagram", "tiktok"]`,
    so a persona with no connected account got 30 days of slots for two platforms
    the operator never mentioned — and this app has no publishing adapter for
    either, so not one of them could ever ship. The run reported
    `status: "scheduled"` with a post count, which is indistinguishable from real
    inventory on a calendar.

    A refusal is the honest answer, and it must not be a refusal that writes.
    """
    _stated_price(monkeypatch, 5.0)
    from uuid import uuid4 as _uuid4

    from app.models import Persona

    # A persona with content and *no* SocialAccount row at all. The content
    # guard runs before the platform one, so without a pack this would stop at
    # `no_content` and never reach the branch under test.
    persona = Persona(id=_uuid4(), name=f"unconnected-{_uuid4().hex[:8]}", status="active")
    db.add(persona)
    await db.commit()
    db.add(ContentPack(
        id=_uuid4(),
        persona_id=persona.id,
        name="pack",
        status="assembled",
        images=["a.png", "b.png"],
    ))
    await db.commit()

    body = await _schedule(client, persona)

    assert body["status"] == "no_platform"
    assert body["posts"] == 0
    assert "fanvue" in body["message"], "the refusal must name the platform that would work"
    assert await _calendar(client, persona) == []


async def test_the_scheduler_does_not_schedule_a_platform_it_cannot_price(
    client, db, monkeypatch
):
    """A stated-but-too-low price blocks that platform's slots rather than
    writing rows Fanvue would refuse at publish time — and it says why."""
    _stated_price(monkeypatch, 0.50)
    persona = await _persona_ready_to_schedule(db, platform="fanvue")

    body = await _schedule(client, persona)

    assert body["status"] == "unpriceable"
    assert "$3.00" in body["unpriceable"]["fanvue"]
    assert await _calendar(client, persona) == []


# ── media that exists on disk but is not content ─────────────────────────
#
# The scheduler used to build a slot for every file it found under a shoot
# directory. Measured on live storage: 338 files, none of them a usable image.
# Every one became a slot that could only ever be refused at publish time.


async def _persona_with_shoot(db, *, dir_name: str | None, files: dict):
    """A persona, a Shoot row, and a directory of files under storage/shoots."""
    from app import paths as app_paths
    from app.models import Shoot

    persona = Persona(id=uuid4(), name=f"shoot-{uuid4().hex[:8]}", status="active")
    db.add(persona)
    await db.commit()

    shoot_id = uuid4()
    if dir_name is None:
        dir_name = str(shoot_id)[:8]
        db.add(Shoot(id=shoot_id, persona_id=persona.id, name="New Shoot", status="DRAFT"))
        await db.commit()

    shoot_dir = app_paths.STORAGE_ROOT / "shoots" / dir_name
    shoot_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (shoot_dir / name).write_bytes(data)
    return persona, shoot_dir


async def test_a_shoot_of_placeholder_files_schedules_nothing(client, db, monkeypatch):
    """The measured case: directories full of files that are not pictures."""
    _stated_price(monkeypatch, None)
    persona, _ = await _persona_with_shoot(
        db,
        dir_name=None,
        files={"shot_00.png": b"\x89PNG\r\n\x1a\n", "shot_01.png": b"\x89PNG\r\n\x1a\n"},
    )
    db.add(SocialAccount(
        id=str(uuid4()), persona_id=str(persona.id),
        platform="instagram", username="x", status="active",
    ))
    await db.commit()

    body = await _schedule(client, persona)

    assert body["status"] == "no_content"
    assert body["posts"] == 0
    assert body["skipped_media_count"] == 2
    assert "shot_00.png" in body["skipped_media"][0]
    assert "does not decode" in body["skipped_media"][0]
    assert await _calendar(client, persona) == []


async def test_a_shoot_of_real_images_does_schedule(client, db, monkeypatch, write_png):
    """The other side of the same check: real pictures still schedule."""
    _stated_price(monkeypatch, None)
    import io

    real = write_png(io.BytesIO(), size=(256, 256)).getvalue()
    persona, _ = await _persona_with_shoot(
        db, dir_name=None, files={"shot_00.png": real, "shot_01.png": real},
    )
    db.add(SocialAccount(
        id=str(uuid4()), persona_id=str(persona.id),
        platform="instagram", username="x", status="active",
    ))
    await db.commit()

    body = await _schedule(client, persona)

    assert body["status"] == "scheduled"
    assert body["posts"] > 0
    assert body["skipped_media_count"] == 0


async def test_shoot_directories_no_record_claims_are_reported(client, db, monkeypatch):
    """"There is no content" and "there is content here that nothing can
    attribute to a persona" send the operator to different places.

    Measured 2026-09-30: 449 directories under storage/shoots, one Shoot row.
    """
    _stated_price(monkeypatch, None)
    persona, _ = await _persona_with_shoot(db, dir_name="aaaaaaaa", files={})
    db.add(SocialAccount(
        id=str(uuid4()), persona_id=str(persona.id),
        platform="instagram", username="x", status="active",
    ))
    await db.commit()

    body = await _schedule(client, persona)

    assert body["status"] == "no_content"
    assert body["unattributed_shoot_dirs"] >= 1
