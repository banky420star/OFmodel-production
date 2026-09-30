"""Persona Studio — the manager view must not claim a signup happened.

A roster that renders every account as "signed up" because a row exists is the
same manufactured-success problem as the fan-reply endpoints, at the scale of a
whole page: it would tell an operator to move on when nothing exists on the
platform. These tests pin the derived state — the evidence an account really
has, the blockers it really has, and the one next human action — and pin that
`request-all` builds a worklist rather than touching a platform.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models import Persona, SocialAccount
from app.routes.manager import PLATFORMS


async def _seed_persona(db, name="Ava", **kwargs):
    persona = Persona(id=uuid.uuid4(), name=name, age=25, **kwargs)
    db.add(persona)
    # Committed, not just flushed: the endpoints below run in their own session
    # (the get_db override opens a new one), so an uncommitted row is invisible
    # to them and every request would 404 on a persona that plainly exists.
    await db.commit()
    return persona


async def _seed_account(db, persona, platform="instagram", status="pending_approval",
                        username="ava", **kwargs):
    account = SocialAccount(
        id=str(uuid.uuid4()), persona_id=str(persona.id), platform=platform,
        username=username, display_name=persona.name, status=status, **kwargs,
    )
    db.add(account)
    await db.commit()
    return account


# ── what an account's state means ────────────────────────────────────

def test_a_pending_request_is_not_a_signup():
    """The most common row in the database is a request nobody has acted on.
    It must read as work to do, not as progress."""
    from app.routes.manager import _account_state

    account = SocialAccount(
        id=str(uuid.uuid4()), persona_id=str(uuid.uuid4()), platform="tiktok",
        username="ava", status="pending_approval",
    )
    state = _account_state(account)

    assert state["next_action"] == "approve"
    assert "no_operator_approval" in state["blockers"]
    assert "no_signup_email" in state["blockers"]
    assert "not_live" in state["blockers"]
    assert state["packet_ready"] is False
    assert state["has_inbox"] is False


def test_an_approved_account_with_no_inbox_asks_for_one_first():
    from app.routes.manager import _account_state

    account = SocialAccount(
        id=str(uuid.uuid4()), persona_id=str(uuid.uuid4()), platform="instagram",
        username="ava", status="approved",
    )
    assert _account_state(account)["next_action"] == "generate_email"


def test_an_approved_account_with_an_inbox_opens_the_packet():
    from app.routes.manager import _account_state

    account = SocialAccount(
        id=str(uuid.uuid4()), persona_id=str(uuid.uuid4()), platform="instagram",
        username="ava", status="approved", email="ava@mail.tm",
    )
    state = _account_state(account)
    assert state["next_action"] == "open_packet"
    assert "no_signup_email" not in state["blockers"], "the inbox it has is enough"
    assert "no_platform_password" in state["blockers"]


def test_a_ready_packet_is_flagged_ready():
    """Inbox + password staged means a person can open the packet and submit the
    platform's form — the only thing left is the part this app cannot do."""
    from app.routes.manager import _account_state

    account = SocialAccount(
        id=str(uuid.uuid4()), persona_id=str(uuid.uuid4()), platform="instagram",
        username="ava", status="approved", email="ava@mail.tm",
        email_token="jwt", password_hash="cGFzcw==",
    )
    state = _account_state(account)
    assert state["packet_ready"] is True
    assert state["next_action"] == "signup"
    assert state["has_password"] is True


def test_a_live_account_is_idle_only_when_nothing_is_pending():
    from app.routes.manager import _account_state

    account = SocialAccount(
        id=str(uuid.uuid4()), persona_id=str(uuid.uuid4()), platform="instagram",
        username="ava", status="active", email="a@b.c", password_hash="cGFzcw==",
        api_connected=False,
    )
    assert _account_state(account)["next_action"] == "connect_api"
    account.api_connected = True
    assert _account_state(account)["next_action"] == "operating"


def test_a_rejected_account_has_no_work_left():
    from app.routes.manager import _account_state

    account = SocialAccount(
        id=str(uuid.uuid4()), persona_id=str(uuid.uuid4()), platform="fansly",
        username="ava", status="rejected",
    )
    state = _account_state(account)
    assert state["next_action"] == "closed"
    assert state["blockers"] == ["rejected"]


# ── an inbox that exists must read as existing ───────────────────────

def test_an_inbox_stored_in_the_token_column_reads_as_present():
    from app.routes.manager import _account_state

    account = SocialAccount(
        id=str(uuid.uuid4()), persona_id=str(uuid.uuid4()), platform="tiktok",
        username="ava", status="approved", email="ava@mail.tm",
        email_token="jwt-in-the-column",
    )
    assert _account_state(account)["has_inbox"] is True


def test_an_inbox_stored_in_metadata_reads_as_present_too():
    """`generate_account_email` wrote the token only to `metadata_json` while
    the column of the same name stayed empty. Rows created then are still in the
    database, and an inbox that reads as absent is how an operator ends up
    generating a second one — or believing a ready packet is not ready."""
    from app.routes.manager import _account_state

    account = SocialAccount(
        id=str(uuid.uuid4()), persona_id=str(uuid.uuid4()), platform="tiktok",
        username="ava", status="approved", email="ava@mail.tm",
        metadata_json={"email_token": "jwt-in-metadata"},
    )
    assert _account_state(account)["has_inbox"] is True


def test_an_account_with_no_inbox_at_all_says_so():
    from app.routes.manager import _account_state

    account = SocialAccount(
        id=str(uuid.uuid4()), persona_id=str(uuid.uuid4()), platform="tiktok",
        username="ava", status="approved", email="ava@mail.tm",
    )
    assert _account_state(account)["has_inbox"] is False


# ── the roster endpoint ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_roster_reports_missing_platforms(db, client):
    persona = await _seed_persona(db, "RosterA")
    await _seed_account(db, persona, platform="instagram")

    resp = await client.get(f"/api/v1/manager/roster?persona_id={persona.id}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["summary"]["models"] == 1
    row = body["roster"][0]
    assert row["platforms_covered"] == 1
    assert "tiktok" in row["platforms_missing"]
    assert "instagram" not in row["platforms_missing"]
    assert row["accounts_needing_work"] == 1
    assert body["summary"]["not_requested"] == len(PLATFORMS) - 1


@pytest.mark.asyncio
async def test_roster_surfaces_a_failed_build_warning(db, client):
    """A build that failed its LoRA step leaves the persona ACTIVE and usable —
    so the manager page is the only place the failure is visible."""
    persona = await _seed_persona(db, "WarnedA")
    persona.metadata_json = {"warnings": [
        {"step": "train_lora", "message": "LoRA training failed: out of memory"},
    ]}
    await db.commit()

    body = (await client.get(
        f"/api/v1/manager/roster?persona_id={persona.id}"
    )).json()

    assert body["roster"][0]["warnings"] == ["LoRA training failed: out of memory"]


@pytest.mark.asyncio
async def test_roster_never_calls_a_pending_row_signed_up(db, client):
    persona = await _seed_persona(db, "PendingA")
    await _seed_account(db, persona, platform="tiktok", status="pending_approval")

    body = (await client.get(
        f"/api/v1/manager/roster?persona_id={persona.id}"
    )).json()

    assert body["summary"]["live"] == 0
    assert body["summary"]["needing_work"] == 1

    # The note has to be true, not just reassuring. It used to read "Manual
    # signup only. No process here creates a platform account" — while
    # POST /social-accounts/{id}/auto-signup was registered and unguarded. It
    # now states the operational rule (a row is not an account) and points at
    # the real state of automated signup, which is a refused-by-default gate.
    note = body["note"].lower()
    assert "never because a row exists" in note
    assert "auto_signup_enabled" in note
    assert "signup packet" in note
    assert "no process here creates" not in note, (
        "this claim is false while the auto-signup routes are registered"
    )


@pytest.mark.asyncio
async def test_roster_platform_filter_narrows_accounts(db, client):
    """The roster table is session-shared, so every assertion about counts is
    scoped to this test's own persona. An unscoped count here would pass or
    fail depending on what other tests happened to leave behind."""
    persona = await _seed_persona(db, "FilterA")
    await _seed_account(db, persona, platform="instagram", username="f1")
    await _seed_account(db, persona, platform="tiktok", username="f2")

    body = (await client.get(
        f"/api/v1/manager/roster?persona_id={persona.id}&platform=tiktok"
    )).json()

    platforms = [a["platform"] for r in body["roster"] for a in r["accounts"]]
    assert platforms == ["tiktok"], "the filter must hide the other platforms' rows"
    assert body["summary"]["accounts"] == 1


@pytest.mark.asyncio
async def test_roster_rejects_a_malformed_persona_id(client):
    resp = await client.get("/api/v1/manager/roster?persona_id=not-a-uuid")
    assert resp.status_code == 400


# ── request-all builds a worklist, nothing else ──────────────────────

@pytest.mark.asyncio
async def test_request_all_files_one_request_per_missing_platform(db, client):
    persona = await _seed_persona(db, "AllA")

    resp = await client.post(f"/api/v1/manager/personas/{persona.id}/request-all")
    assert resp.status_code == 200
    body = resp.json()

    assert body["created_count"] == len(PLATFORMS)
    assert sorted(c["platform"] for c in body["created"]) == sorted(PLATFORMS)
    assert all(c["status"] == "pending_approval" for c in body["created"])
    assert "any platform" in body["note"]

    rows = (await db.execute(
        text("SELECT status, email, password_hash FROM social_accounts "
             "WHERE persona_id = :pid"),
        {"pid": str(persona.id)},
    )).fetchall()
    assert len(rows) == len(PLATFORMS)
    # Nothing was contacted: no inbox, no credential, no platform state.
    assert all(r[0] == "pending_approval" for r in rows)
    assert all(not r[1] and not r[2] for r in rows)


@pytest.mark.asyncio
async def test_request_all_skips_platforms_already_covered(db, client):
    persona = await _seed_persona(db, "SkipA")
    await _seed_account(db, persona, platform="instagram", status="active")

    body = (await client.post(
        f"/api/v1/manager/personas/{persona.id}/request-all"
    )).json()

    assert body["created_count"] == len(PLATFORMS) - 1
    assert [s["platform"] for s in body["skipped"]] == ["instagram"]
    assert "active" in body["skipped"][0]["reason"]


@pytest.mark.asyncio
async def test_the_same_handle_is_used_across_platforms(db, client):
    """Handles are namespaced per site, so a model manager wants the *same*
    handle everywhere. An earlier version de-duplicated globally and produced
    `zara`, `zara2`, `zara3` ... purely because the loop walked the platform
    list — nothing had collided."""
    persona = await _seed_persona(db, "Ava Rose")

    body = (await client.post(
        f"/api/v1/manager/personas/{persona.id}/request-all"
    )).json()

    by_platform = {c["platform"]: c["username"] for c in body["created"]}
    # The handle is the persona's name normalized for that platform, and
    # nothing more — the point is that no `2`, `3`, `4` suffix crept in.
    for platform, username in by_platform.items():
        assert not any(ch.isdigit() for ch in username), (platform, username)
    assert by_platform["instagram"] == "ava.rose"
    assert by_platform["tiktok"] == "avarose"
    assert by_platform["instagram"] == by_platform["fanvue"] == by_platform["fansly"]


@pytest.mark.asyncio
async def test_a_second_account_on_one_platform_gets_a_distinct_handle(db, client):
    """The collision that does matter: two requests on the *same* platform must
    not both claim one handle."""
    persona = await _seed_persona(db, "CollideA")
    await _seed_account(db, persona, platform="tiktok", username="collidea",
                        status="rejected")

    body = (await client.post(
        f"/api/v1/manager/personas/{persona.id}/request-all?platforms=tiktok"
    )).json()

    # The rejected row freed the platform, but its handle is still in use.
    assert body["created_count"] == 1
    assert body["created"][0]["username"] != "collidea"


@pytest.mark.asyncio
async def test_platform_specific_handle_rules(db, client):
    """Instagram/Fanvue/Fansly allow dots; TikTok and X do not."""
    from app.models import Persona
    from app.routes.manager import _username_for

    persona = Persona(id=uuid.uuid4(), name="Ava Rose", age=25)
    taken: set[str] = set()
    assert _username_for(persona, "instagram", taken) == "ava.rose"
    assert _username_for(persona, "tiktok", taken) == "avarose"
    assert _username_for(persona, "twitter", taken) == "avarose"


@pytest.mark.asyncio
async def test_request_all_accepts_a_subset(db, client):
    persona = await _seed_persona(db, "SubsetA")

    body = (await client.post(
        f"/api/v1/manager/personas/{persona.id}/request-all?platforms=tiktok,fanvue"
    )).json()

    assert sorted(c["platform"] for c in body["created"]) == ["fanvue", "tiktok"]


@pytest.mark.asyncio
async def test_request_all_refuses_an_unknown_platform(db, client):
    persona = await _seed_persona(db, "BadPlatformA")

    resp = await client.post(
        f"/api/v1/manager/personas/{persona.id}/request-all?platforms=myspace"
    )
    assert resp.status_code == 400
    assert "myspace" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_request_all_404s_on_an_unknown_persona(client):
    resp = await client.post(f"/api/v1/manager/personas/{uuid.uuid4()}/request-all")
    assert resp.status_code == 404


# ── the inbox writer stores where readers look ───────────────────────

@pytest.mark.asyncio
async def test_generate_email_persists_the_inbox_where_readers_look(
    db, client, monkeypatch,
):
    """The four inbox values have declared columns, and the handler was writing
    them only into metadata_json. Anything reading the column then saw no inbox
    on an account that had one. Pinned here because the fix is invisible from
    the response body — the endpoint returned the address either way.
    """
    from app.providers import email as email_module
    from app.providers.email import TempEmail

    async def _fake_create_temp_email(persona_name, prefix=""):
        return TempEmail(
            address=f"{prefix}9999@example.test", password="inbox-pw",
            token="jwt-token", account_id="acct-1", domain="example.test",
        )

    monkeypatch.setattr(email_module, "create_temp_email", _fake_create_temp_email)

    persona = await _seed_persona(db, "InboxA")
    account = await _seed_account(db, persona, platform="tiktok", status="approved")

    resp = await client.post(f"/api/v1/social-accounts/{account.id}/generate-email")
    assert resp.status_code == 200, resp.text

    await db.refresh(account)
    assert account.email == "tiktok9999@example.test"
    assert account.email_token == "jwt-token", "the column must carry the token"
    assert account.email_password == "inbox-pw"
    assert account.email_account_id == "acct-1"
    assert account.email_domain == "example.test"
    # Kept as well, so nothing that already reads metadata breaks.
    assert account.metadata_json["email_token"] == "jwt-token"
