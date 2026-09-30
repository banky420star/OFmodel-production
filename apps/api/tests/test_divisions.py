"""Divisions — the studio's operating structure has to tell the truth about itself.

The failure this layer could have is the one the whole app is built to refuse:
a page of seven confident headings over six things that do not run. So these
pin the three claims that make the view worth having — maturity is set by the
entrypoint existing, readiness is resolved from the real provider registry
rather than asserted, and every cadence that has no scheduler says so.
"""

from __future__ import annotations

import re
import uuid

from app.divisions import BY_KEY, DIVISIONS, LIVE, PARTIAL, UNBUILT, divisions_status
from app.models import ContentPack, Persona, ScheduledPost, Shoot, SocialAccount


async def _seed_persona(db, name="DivA", **kwargs):
    persona = Persona(id=uuid.uuid4(), name=name, age=25, **kwargs)
    db.add(persona)
    await db.commit()
    return persona


def _row(body, key):
    return next(r for r in body["divisions"] if r["key"] == key)


# ── the registry is honest ───────────────────────────────────────────────


def test_the_seven_divisions_from_the_chart_all_exist():
    assert [d.key for d in DIVISIONS] == [
        "shoots",
        "photo_shoot_planner",
        "look_planner",
        "video_production",
        "socials",
        "week_events",
        "live_posts",
    ]


def test_every_division_declares_what_it_owns_and_produces():
    for division in DIVISIONS:
        assert division.owns, f"{division.key} must say what it does"
        assert division.produces, f"{division.key} must name its artifact"
        assert division.cadence, f"{division.key} must state its cadence"


def test_anything_short_of_live_must_name_the_gap():
    """A `partial` or `unbuilt` division with no `gap` is a heading pretending
    to be a feature — the operator cannot tell what to do about it."""
    for division in DIVISIONS:
        if division.maturity in (PARTIAL, UNBUILT):
            assert division.gap, f"{division.key} is {division.maturity} but names no gap"


def test_a_live_division_names_a_real_entrypoint():
    for division in DIVISIONS:
        if division.maturity == LIVE:
            assert division.routes, f"{division.key} is live but has no route"
            assert division.workflow or division.routes, division.key


def test_maturity_values_are_from_the_declared_set():
    for division in DIVISIONS:
        assert division.maturity in (LIVE, PARTIAL, UNBUILT), division.key


def test_the_registry_lookup_matches_the_list():
    assert set(BY_KEY) == {d.key for d in DIVISIONS}


# ── readiness is resolved, not asserted ──────────────────────────────────


async def test_divisions_status_is_readable_without_a_persona(db):
    body = await divisions_status(db)
    assert len(body["divisions"]) == len(DIVISIONS)
    assert body["summary"]["total"] == len(DIVISIONS)


async def test_the_capabilities_come_from_the_registry(db):
    """The test harness fakes every provider, so every declared capability must
    resolve. If this fails the view is inventing a blocker that is not there."""
    body = await divisions_status(db)
    for row in body["divisions"]:
        if "publish" in row["requires"]:
            continue  # publish is not a registry capability — checked separately
        assert row["capabilities_missing"] == [], (
            f"{row['key']} reports missing {row['capabilities_missing']} "
            "although the harness has every provider overridden"
        )


async def test_a_division_with_no_requirements_is_never_blocked_on_capabilities(db):
    body = await divisions_status(db)
    socials = _row(body, "socials")
    assert socials["requires"] == []
    assert socials["capabilities_missing"] == []
    assert socials["can_act"] is True


async def test_live_posts_is_blocked_because_the_operator_has_no_credentials(db):
    """The honest headline: the one division that touches revenue cannot run.

    It is blocked and the reason names what is missing. If someone later puts
    Fanvue credentials in .env this test should fail loudly, because the
    division really did become runnable and the note about it is now wrong.
    """
    body = await divisions_status(db)
    live_posts = _row(body, "live_posts")
    assert live_posts["publish_ready"] is False
    assert live_posts["can_act"] is False
    assert live_posts["blocked_by"], "a blocked division must say why"
    assert any("FANVUE" in b for b in live_posts["blocked_by"])
    # And only the specific reason — the generic "unconfigured: publish" line
    # would sit directly above the sentence naming the missing credential.
    assert not any(b.startswith("unconfigured: publish") for b in live_posts["blocked_by"])


async def test_a_division_can_act_only_when_nothing_blocks_it(db):
    body = await divisions_status(db)
    for row in body["divisions"]:
        assert row["can_act"] is (not row["blocked_by"] and row["maturity"] != UNBUILT)


async def test_the_summary_counts_blocked_and_actionable_separately(db):
    body = await divisions_status(db)
    summary = body["summary"]
    assert summary["can_act"] + summary["blocked"] == summary["total"]


async def test_the_note_never_claims_a_timer_runs_when_it_does_not(db):
    """The clock exists now, so the old flat claim ("nothing runs on a timer")
    is only true while SCHEDULER_ENABLED is false — and the note must key off the
    real switch rather than asserting either state blindly."""
    body = await divisions_status(db)
    assert body["scheduler"]["enabled"] is False
    assert "SCHEDULER_ENABLED is false" in body["note"]
    assert "nothing here runs on a timer" in body["note"]


async def test_the_note_flips_when_the_clock_is_armed(db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "SCHEDULER_ENABLED", True, raising=False)
    body = await divisions_status(db)
    assert body["scheduler"]["enabled"] is True
    assert "clock is armed" in body["note"]
    assert "nothing here runs on a timer" not in body["note"]


async def test_a_missed_post_is_counted_not_dropped(db):
    """A closed-out slot has its own status, and a view that only counts
    `scheduled` and `posted` would make it vanish."""
    persona = await _seed_persona(db, "MissedA")
    db.add(ScheduledPost(
        id=str(uuid.uuid4()), persona_id=str(persona.id), platform="fanvue",
        scheduled_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc),
        status="missed",
    ))
    await db.commit()

    counts = _row(await divisions_status(db, str(persona.id)), "week_events")["counts"]
    assert counts["missed"] == 1


async def test_a_count_that_cannot_be_read_says_so_instead_of_reporting_zero(db):
    """The bug this pins: a swallowed exception made every count read 0, and a
    page reporting "0 shoots" over a broken query is a lie told in the same
    voice as the truth. A failed count must be visible."""
    body = await divisions_status(db)
    for row in body["divisions"]:
        assert row["counts_error"] == "", f"{row['key']}: {row['counts_error']}"


async def test_scoping_uses_a_uuid_for_uuid_columns_and_a_string_for_text_ones(db):
    """Shoot/ContentPack key on a UUID column; ScheduledPost/SocialAccount on
    String(36). Passing the wrong form to a UUID column raises rather than
    matching nothing, so one filtered view needs both."""
    persona = await _seed_persona(db, "CoerceA")
    db.add(Shoot(id=uuid.uuid4(), persona_id=persona.id, name="s", status="draft"))
    db.add(ContentPack(id=uuid.uuid4(), persona_id=persona.id, name="p", status="draft"))
    db.add(SocialAccount(
        id=str(uuid.uuid4()), persona_id=str(persona.id), platform="fanvue",
        username="ca", status="active",
    ))
    await db.commit()

    body = await divisions_status(db, str(persona.id))
    assert _row(body, "shoots")["counts"]["shoots"] == 1
    assert _row(body, "video_production")["counts"]["packs"] == 1
    assert _row(body, "socials")["counts"]["accounts"] == 1


# ── counts read the real rows ────────────────────────────────────────────


async def test_counts_reflect_seeded_rows_scoped_to_the_persona(db):
    persona = await _seed_persona(db, "CountA")
    db.add(Shoot(id=uuid.uuid4(), persona_id=persona.id, name="s", status="draft"))
    db.add(Shoot(id=uuid.uuid4(), persona_id=persona.id, name="s2", status="completed"))
    db.add(ScheduledPost(
        id=str(uuid.uuid4()), persona_id=str(persona.id), platform="fanvue",
        scheduled_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        status="scheduled",
    ))
    await db.commit()

    body = await divisions_status(db, str(persona.id))
    assert _row(body, "shoots")["counts"] == {"shoots": 2, "completed": 1}
    assert _row(body, "photo_shoot_planner")["counts"] == {"planned": 1}
    assert _row(body, "week_events")["counts"]["scheduled"] == 1


async def test_enum_statuses_group_by_their_value_not_their_repr(db):
    """`class ShootStatus(str, Enum)` makes `str(ShootStatus.DRAFT)` equal
    `"ShootStatus.DRAFT"`, so a naive group-by keyed every count under a name no
    lookup matches and the whole page read zero. Seed one draft and one
    completed and require both to be found."""
    persona = await _seed_persona(db, "EnumA")
    db.add(Shoot(id=uuid.uuid4(), persona_id=persona.id, name="a", status="draft"))
    db.add(Shoot(id=uuid.uuid4(), persona_id=persona.id, name="b", status="completed"))
    await db.commit()

    counts = _row(await divisions_status(db, str(persona.id)), "shoots")["counts"]
    assert counts["completed"] == 1, "a completed shoot was not counted as completed"


async def test_each_division_only_reports_the_numbers_it_owns(db):
    """A shared counts dict would put account totals under the shoot planner and
    read as if the planner had produced them."""
    body = await divisions_status(db)
    for row in body["divisions"]:
        assert set(row["counts"]).isdisjoint({"accounts", "packs", "posted"}) or row["key"] in (
            "socials", "video_production", "week_events", "live_posts",
        ), row["key"]


async def test_persona_scoping_does_not_leak_other_personas(db):
    mine = await _seed_persona(db, "ScopeMine")
    theirs = await _seed_persona(db, "ScopeTheirs")
    db.add(Shoot(id=uuid.uuid4(), persona_id=theirs.id, name="not-mine", status="draft"))
    await db.commit()

    body = await divisions_status(db, str(mine.id))
    assert _row(body, "shoots")["counts"]["shoots"] == 0
    assert _row(body, "look_planner")["counts"] == {"personas": 1}


# ── the route ────────────────────────────────────────────────────────────


async def test_route_returns_the_divisions(client):
    resp = await client.get("/api/v1/manager/divisions")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["divisions"]) == len(DIVISIONS)
    assert body["summary"]["total"] == len(DIVISIONS)


async def test_route_rejects_a_malformed_persona_id(client):
    resp = await client.get("/api/v1/manager/divisions?persona_id=not-a-uuid")
    assert resp.status_code == 400


async def test_route_scopes_to_a_persona(client, db):
    persona = await _seed_persona(db, "RouteA")
    resp = await client.get(f"/api/v1/manager/divisions?persona_id={persona.id}")
    assert resp.status_code == 200
    assert _row(resp.json(), "look_planner")["counts"] == {"personas": 1}


async def test_route_never_503s_on_an_unconfigured_division(client):
    """The screen exists to show breakage, so it must not break. live_posts is
    unconfigured in this harness and the endpoint still answers 200."""
    resp = await client.get("/api/v1/manager/divisions")
    assert resp.status_code == 200
    live_posts = _row(resp.json(), "live_posts")
    assert live_posts["can_act"] is False
    assert live_posts["gap"]


async def test_route_counts_are_scoped_to_the_test_persona(client, db):
    persona = await _seed_persona(db, "RouteB")
    db.add(ContentPack(id=uuid.uuid4(), persona_id=persona.id, name="p", status="draft"))
    db.add(SocialAccount(
        id=str(uuid.uuid4()), persona_id=str(persona.id), platform="fanvue",
        username="rb", status="active",
    ))
    await db.commit()

    body = (await client.get(f"/api/v1/manager/divisions?persona_id={persona.id}")).json()
    assert _row(body, "socials")["counts"] == {"accounts": 1, "live": 1}
    assert _row(body, "video_production")["counts"] == {"packs": 1}


# ── the routes it names have to exist, and have to be the right ones ─────

_PARAM = re.compile(r"\{[^}]+\}")
_API_PREFIX = "/api/v1"


def _live_route_index() -> set[tuple[str, str]]:
    """Every mounted route as (METHOD, shape-normalized path).

    Normalized because parameter *names* legitimately differ — the registry
    writes `{id}` where the app declares `{persona_id}` — so comparing literal
    strings reports drift where there is none, and (worse) accepts a path that
    merely happens to share a spelling. Shape is the honest comparison.

    The `/api/v1` prefix is stripped because the registry names routes the way
    the app declares them, without the mount prefix. Matching on the full
    OpenAPI path instead reports *every* route as missing — which is how this
    test failed the first time it ran, and is exactly the false alarm a drift
    guard must not have.
    """
    from app.main import app

    index: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        if path.startswith(_API_PREFIX):
            path = path[len(_API_PREFIX):]
        shape = _PARAM.sub("{}", path)
        for method in operations:
            index.add((method.upper(), shape))
    return index


def test_every_route_named_in_the_registry_exists():
    """The registry is the operator's map of what runs. A route named here that
    the app does not serve sends them looking for a lever that is not mounted —
    and it reads as authoritative precisely because everything around it is
    measured. This is a drift guard, not a style check: it is the test that
    would have caught `live_posts` naming `/fan/products/{id}/unlock` (real
    path: `{product_id}`) beside a gap string claiming the division had no route
    at all while `POST /scheduled-posts/{post_id}/publish` was mounted.
    """
    index = _live_route_index()
    missing: list[str] = []
    for division in DIVISIONS:
        for named in division.routes:
            method, _, path = named.partition(" ")
            if (method.upper(), _PARAM.sub("{}", path)) not in index:
                missing.append(f"{division.key}: {named}")
    assert not missing, "divisions name routes the app does not serve: " + "; ".join(missing)


def test_the_publishing_division_points_at_the_platform_publish_route():
    """`live_posts` owns "puts content in front of a paying stranger, on a
    platform the studio does not own." The route that does that is the publish
    route, and it has to be the one named."""
    assert "POST /scheduled-posts/{post_id}/publish" in BY_KEY["live_posts"].routes


def test_the_publishing_division_does_not_point_at_the_simulated_wallet():
    """`POST /fan/products/{product_id}/unlock` is real code and would satisfy
    the existence check above — but it unlocks against this app's own `fake`
    processor, so it is not a way to reach a paying stranger on a platform the
    studio does not own. Naming it here is how the one division whose job is
    revenue came to advertise the simulated path and deny the real one.
    """
    routes = BY_KEY["live_posts"].routes
    assert not any("fan/products" in route for route in routes), (
        "the publishing division is pointing at the simulated in-app wallet"
    )
