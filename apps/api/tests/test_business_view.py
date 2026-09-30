"""Does the business view tell the truth about money, and about what can ship?

Measured 2026-09-30: the manager had two screens and neither mentioned money.
`/manager/roster` answered "which account needs what next" and
`/manager/divisions` answered "which production units can act" — both real, both
useful, and between them no operator could answer the question the business
actually has: *what can this studio sell, and what has it earned?*

These tests hold the join to the two properties that make it worth having, and
they are the same two rules the modules it joins are built on:

* **Unknown is never zero.** On the operator's machine today no platform is
  connected, so every money figure is `None`. A view that rendered an unreadable
  ledger as `$0.00` would report a business with no revenue when what it has is
  no *reading*, and those send an operator to different places.
* **A slot is not inventory.** A scheduled row counts as shippable only when it
  sits on a platform this app can publish to, carries a price, and its media
  resolves to a real file on disk. Live storage holds 338 files under
  `storage/shoots` and not one usable image, so any count that trusts a media
  key is a count of placeholders.

Rows are read back per persona rather than as deltas of a global total: the `db`
fixture rolls back its own writes but `_persona` commits, so other tests' rows
are still present and a "1 of 1" assertion would depend on pytest's order.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import paths
from app.models import Persona, ScheduledPost, SocialAccount
from app.publishing import media_on_disk, media_path_for


# ── helpers ──────────────────────────────────────────────────────────────


async def _read(client) -> dict:
    resp = await client.get("/api/v1/manager/business")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _row(client, persona_id) -> dict:
    body = await _read(client)
    found = [r for r in body["personas"] if r["persona_id"] == str(persona_id)]
    assert found, "the persona is missing from the view entirely"
    return found[0]


async def _persona(db) -> Persona:
    persona = Persona(id=uuid4(), name=f"biz-{uuid4().hex[:8]}", status="active")
    db.add(persona)
    await db.commit()
    return persona


def _account(persona, platform: str, status: str = "active") -> SocialAccount:
    return SocialAccount(
        id=str(uuid4()),
        persona_id=str(persona.id),
        platform=platform,
        username=f"{platform}-{uuid4().hex[:6]}",
        status=status,
    )


def _post(persona, *, platform="fanvue", media=(), price=5.0, status="scheduled") -> ScheduledPost:
    return ScheduledPost(
        id=str(uuid4()),
        persona_id=str(persona.id),
        platform=platform,
        media_keys=list(media),
        ppv_price=price,
        status=status,
        scheduled_at=datetime.now(timezone.utc),
    )


def _shoot_dir() -> "paths.Path":
    d = paths.STORAGE_ROOT / "shoots" / f"biz{uuid4().hex[:6]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _real_media(write_png, name: str = "shot_01.png") -> str:
    """A key in the shape the scheduler actually writes, pointing at a real file."""
    d = _shoot_dir()
    write_png(d / name, size=(256, 256))
    return f"storage/shoots/{d.name}/{name}"


def _placeholder_media(name: str = "shot_99.png") -> str:
    """Eight bytes of PNG signature — the shape of all 338 live files."""
    d = _shoot_dir()
    (d / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    return f"storage/shoots/{d.name}/{name}"


# ── money ────────────────────────────────────────────────────────────────


async def test_an_unreadable_ledger_is_never_rendered_as_zero(client):
    """The headline failure this module could commit. With no platform
    connected there is no reading — not a reading of zero."""
    money = (await _read(client))["money"]

    assert money["is_real"] is False
    assert money["state"] in ("not_configured", "disabled", "error", "unsupported")
    for field in ("gross", "net", "this_month_net", "previous_month_net", "sources_total"):
        assert money["headline"][field] is None, f"{field} was invented as a number"
    assert money["headline"]["by_source"] == {}
    assert money["headline"]["over_time"] == []
    assert money["detail"], "an unreadable ledger must say why it is unreadable"


async def test_the_view_answers_when_nothing_is_configured(client):
    """It must answer, not 500 — an operator reading this page needs the list of
    what is missing, which is precisely the case where nothing is configured."""
    body = await _read(client)
    assert {"money", "gates", "personas", "summary", "next_action"} <= set(body)
    assert body["summary"]["personas"] == len(body["personas"])


async def test_the_next_action_is_the_first_open_gate(client):
    """Ordering is not restated here: the gates already carry the order an
    operator would fix them in, and the first open one is the next action."""
    body = await _read(client)
    open_gates = [c["key"] for c in body["gates"]["checks"] if not c["done"]]
    action = body["next_action"]

    assert action["gates_total"] == len(body["gates"]["checks"])
    assert action["gates_open"] == len(body["gates"]["checks"]) - len(open_gates)
    if open_gates:
        assert action["key"] == open_gates[0]
        assert action["also_blocked"] == open_gates[1:]
        assert action["detail"], "a next action with no instruction is a red light with no lever"
    else:
        assert action["key"] == "watch_the_ledger"


async def test_when_the_blocker_is_content_the_view_names_the_models(client):
    """ "Generate a shoot" with no model named is a task with no subject, and the
    gate cannot know which personas are short — only the roster can."""
    body = await _read(client)
    if body["next_action"]["key"] != "content_ready":
        pytest.skip("content is not the first open gate on this deployment")
    short = [r["name"] for r in body["personas"] if not r["can_ship"]]
    assert body["next_action"].get("for_personas") == short
    assert short, "content is blocking, so at least one persona must be short"


# ── what can ship ────────────────────────────────────────────────────────


async def test_a_priced_post_with_placeholder_media_is_not_shippable(client, db):
    """The measured case: a key that resolves, a price, and eight bytes."""
    persona = await _persona(db)
    db.add(_account(persona, "fanvue"))
    db.add(_post(persona, media=[_placeholder_media()], price=5.0))
    await db.commit()

    row = await _row(client, persona.id)
    assert row["calendar"]["scheduled"] == 1
    assert row["calendar"]["priced"] == 1, "it is priced; that is not the question"
    assert row["calendar"]["shippable"] == 0
    assert row["can_ship"] is False
    assert "media_not_on_disk" in row["blocked_by"]
    assert row["shelf_value"] == 0, "a placeholder is not worth its price"


async def test_a_priced_post_with_real_media_on_a_connected_platform_can_ship(
    client, db, write_png
):
    """The other side of the same check, so the rule cannot be "always no"."""
    persona = await _persona(db)
    db.add(_account(persona, "fanvue"))
    db.add(_post(persona, media=[_real_media(write_png)], price=5.0))
    await db.commit()

    row = await _row(client, persona.id)
    assert row["calendar"]["shippable"] == 1
    assert row["can_ship"] is True
    assert row["blocked_by"] == []
    assert row["shelf_value"] == 5.0


async def test_a_post_with_one_broken_file_of_two_is_not_shippable(client, db, write_png):
    """The publisher refuses a partial media set, on the grounds that three
    photos where one is a placeholder is a different product from the one that
    was scheduled. A view accepting "at least one good file" would call it
    shippable and be wrong in exactly the case that has already burned this
    codebase once."""
    persona = await _persona(db)
    db.add(_account(persona, "fanvue"))
    db.add(_post(persona, media=[_real_media(write_png), _placeholder_media()], price=5.0))
    await db.commit()

    row = await _row(client, persona.id)
    assert row["calendar"]["priced"] == 1
    assert row["calendar"]["shippable"] == 0
    assert row["can_ship"] is False


async def test_a_priced_post_with_no_media_key_is_not_shippable(client, db):
    persona = await _persona(db)
    db.add(_account(persona, "fanvue"))
    db.add(_post(persona, media=[], price=5.0))
    await db.commit()

    row = await _row(client, persona.id)
    assert row["calendar"]["shippable"] == 0
    assert row["can_ship"] is False


async def test_a_platform_that_cannot_publish_is_not_a_way_to_sell(client, db, write_png):
    """An instagram row with real media and a price is exactly the false green:
    it looks like inventory and can never ship."""
    persona = await _persona(db)
    db.add(_account(persona, "instagram"))
    db.add(_post(persona, platform="instagram", media=[_real_media(write_png)], price=9.0))
    await db.commit()

    row = await _row(client, persona.id)
    assert row["publishable_accounts"] == []
    assert row["calendar"]["scheduled"] == 0, "an unshippable slot is not in the denominator"
    assert row["calendar"]["stranded"] == 1, "and it is not hidden either"
    assert row["calendar"]["shippable"] == 0
    assert row["can_ship"] is False
    assert "no_publishable_account" in row["blocked_by"]


async def test_a_posted_row_is_not_inventory(client, db, write_png):
    """It has already gone out. Counting it would report stock that is not there."""
    persona = await _persona(db)
    db.add(_account(persona, "fanvue"))
    db.add(_post(persona, media=[_real_media(write_png)], price=5.0, status="posted"))
    await db.commit()

    row = await _row(client, persona.id)
    assert row["calendar"]["scheduled"] == 0
    assert row["can_ship"] is False


async def test_the_shelf_value_is_the_prices_not_a_forecast(client, db, write_png):
    """Three shippable posts at $4 are $12 of shelf, and that is all it says."""
    persona = await _persona(db)
    db.add(_account(persona, "fanvue"))
    for _ in range(3):
        db.add(_post(persona, media=[_real_media(write_png)], price=4.0))
    await db.commit()

    row = await _row(client, persona.id)
    assert row["calendar"]["shippable"] == 3
    assert row["shelf_value"] == 12.0


async def test_stranded_slots_are_named_and_excluded(client, db):
    """The 42 legacy rows. They are reported, and they move no shippable count."""
    persona = await _persona(db)
    db.add(_post(persona, platform="tiktok", media=[], price=None))
    await db.commit()

    body = await _read(client)
    assert body["stranded_slots"]["count"] >= 1
    assert "tiktok" in body["stranded_slots"]["platforms"]
    row = await _row(client, persona.id)
    assert row["calendar"]["shippable"] == 0


# ── the media rule this view depends on ──────────────────────────────────


def test_a_storage_key_resolves_whether_or_not_it_carries_the_prefix():
    """Both shapes are live. `routes/schedule.py` and `routes/content.py` write
    `storage/shoots/...` while older callers write `shoots/...`, and joining the
    prefixed one straight to STORAGE_ROOT landed on
    `apps/api/storage/storage/...` — a directory that has never existed. Every
    media key this app writes resolved to nothing."""
    prefixed = "storage/shoots/abcd1234/shot_01.png"
    bare = "shoots/abcd1234/shot_01.png"
    assert media_path_for(prefixed) == media_path_for(bare)
    assert media_path_for(prefixed) == paths.STORAGE_ROOT / "shoots" / "abcd1234" / "shot_01.png"


def test_a_key_is_not_stripped_by_letters_alone():
    """A path segment that merely starts with the same letters is left alone."""
    assert media_path_for("storagey/thing.png") == paths.STORAGE_ROOT / "storagey" / "thing.png"


def test_an_empty_key_has_no_path_and_is_refused():
    assert media_path_for("") is None
    usable, reason = media_on_disk("")
    assert usable is False
    assert reason


def test_media_on_disk_refuses_a_placeholder_and_accepts_a_real_file(write_png):
    d = _shoot_dir()
    bad = d / "bad.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n")
    good = d / "good.png"
    write_png(good, size=(256, 256))

    assert media_on_disk(f"storage/shoots/{d.name}/bad.png")[0] is False
    assert media_on_disk(f"storage/shoots/{d.name}/good.png")[0] is True
    assert media_on_disk(f"storage/shoots/{d.name}/missing.png")[0] is False
