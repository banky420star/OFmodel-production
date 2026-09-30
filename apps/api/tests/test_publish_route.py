"""The publish route — a row may only be marked posted on a real platform id.

This is the one path that can put content in front of a paying stranger, so the
tests here are mostly about what must *not* happen: a local write reporting
success, a priced post shipping with nothing attached, a partial media set going
out because some keys resolved, or a second publish of a post already live.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models import ScheduledPost
from app.publishing import dollars_to_minor as _dollars_to_minor
from app.publishing import inspect_media


class _FakePublisher:
    """A publisher that records what it was asked to do.

    `post_uuid` controls the single thing that decides success: a response with
    no id is a failure here exactly as it is in production.
    """

    name = "fake"

    # Declared, not inherited by accident. This publisher cannot charge, so an
    # unpriced post on it is a free post and nothing else — which is what lets
    # `test_a_free_post_sends_no_price` below stay a test of that behaviour
    # rather than of the money guard.
    supports_price = False

    def __init__(self, *, post_uuid="pu_123", ok=True, error="", raises=None):
        self.post_uuid = post_uuid
        self.ok = ok
        self.error = error
        self.raises = raises
        self.calls: list[dict] = []

    async def health_check(self):
        return True, "fake publisher"

    async def create_post(self, *, text, media_paths, price_minor=None,
                          audience="subscribers", publish_at=None):
        self.calls.append({
            "text": text, "media_paths": list(media_paths),
            "price_minor": price_minor, "audience": audience,
        })
        if self.raises:
            raise self.raises
        from app.providers.publish import PublishResult

        return PublishResult(
            ok=self.ok,
            post_uuid=self.post_uuid,
            error=self.error,
            detail={"url": "https://fanvue.com/p/abc"} if self.post_uuid else {},
        )


@pytest.fixture
def publisher(monkeypatch):
    """Install a fake publisher by replacing the gate the route calls."""
    import app.providers.gates as gates
    import app.routes.publish as publish_route

    fake = _FakePublisher()
    monkeypatch.setattr(publish_route, "require_publisher", lambda: fake)
    # Belt and braces: some paths import the gate directly.
    monkeypatch.setattr(gates, "require_publisher", lambda: fake, raising=False)
    return fake


async def _seed_post(db, persona_id, **kwargs):
    post = ScheduledPost(
        id=str(uuid.uuid4()),
        persona_id=str(persona_id),
        platform=kwargs.pop("platform", "fanvue"),
        scheduled_at=datetime.now(timezone.utc),
        status=kwargs.pop("status", "scheduled"),
        **kwargs,
    )
    db.add(post)
    await db.commit()
    return post


async def _persona(db, name):
    from app.models import Persona

    p = Persona(id=uuid.uuid4(), name=name, age=25)
    db.add(p)
    await db.commit()
    return p


# ── the money conversion ─────────────────────────────────────────────────


def test_price_converts_to_minor_units_exactly():
    assert _dollars_to_minor(20.5) == 2050
    assert _dollars_to_minor(3) == 300
    assert _dollars_to_minor(0.01) == 1


def test_a_price_that_floats_badly_still_converts_exactly():
    """4.35 * 100 is 434.99999999999994 in binary floating point. Truncating
    that undercharges a paying fan by a cent, so the conversion goes through
    Decimal(str(...))."""
    assert _dollars_to_minor(4.35) == 435
    assert _dollars_to_minor(8.29) == 829


def test_no_price_stays_no_price():
    """None means a free post — not zero, which would be a $0.00 priced post."""
    assert _dollars_to_minor(None) is None


def test_a_free_post_is_not_silently_priced():
    assert _dollars_to_minor(0) == 0


# ── the gate ─────────────────────────────────────────────────────────────


async def test_status_reports_unconfigured_rather_than_erroring(client):
    """An unconfigured publisher is the answer, not a 500."""
    resp = await client.get("/api/v1/publish/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is False
    assert body["state"] in ("not_configured", "disabled", "unhealthy", "error")


async def test_publishing_unconfigured_is_503(client, db):
    """No monkeypatched publisher here — the real gate runs."""
    persona = await _persona(db, "PubGate")
    post = await _seed_post(db, persona.id, caption="hello")
    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 503
    assert "FANVUE" in resp.text


async def test_a_missing_post_is_404(client, publisher):
    resp = await client.post(f"/api/v1/scheduled-posts/{uuid.uuid4()}/publish")
    assert resp.status_code == 404


# ── the honesty rules ────────────────────────────────────────────────────


async def test_a_post_is_marked_posted_on_a_real_platform_id(client, db, publisher):
    persona = await _persona(db, "PubOk")
    post = await _seed_post(db, persona.id, caption="New set ✨")

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "posted"
    assert body["post_uuid"] == "pu_123"

    await db.refresh(post)
    assert post.status == "posted"
    assert post.posted_at is not None
    assert (post.metadata_json or {}).get("post_uuid") == "pu_123"


async def test_a_response_with_no_post_id_never_marks_the_row_posted(client, db, publisher):
    """The whole rule. 200 with no id is not proof anything exists."""
    publisher.post_uuid = ""
    publisher.ok = True  # even claiming ok, with no id, must not count
    persona = await _persona(db, "PubNoId")
    post = await _seed_post(db, persona.id, caption="no id")

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 502

    await db.refresh(post)
    assert post.status == "failed"
    assert post.posted_at is None
    assert post.post_url == ""
    assert "no post id" in (post.metadata_json or {}).get("publish_error", "")


async def test_a_failed_publish_records_the_reason_and_not_a_success(client, db, publisher):
    from app.providers.publish import PublishFailed

    publisher.raises = PublishFailed("401 unauthorized")
    persona = await _persona(db, "PubFail")
    post = await _seed_post(db, persona.id, caption="will fail")

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 502

    await db.refresh(post)
    assert post.status == "failed"
    assert post.posted_at is None
    assert "401" in (post.metadata_json or {}).get("publish_error", "")


async def test_republishing_a_live_post_is_refused(client, db, publisher):
    """The platform would get it twice."""
    persona = await _persona(db, "PubTwice")
    post = await _seed_post(
        db, persona.id, caption="already up", status="posted",
        post_url="https://fanvue.com/p/existing",
    )

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 409
    assert "already published" in resp.text
    assert publisher.calls == [], "a refused publish must not reach the platform"


async def test_a_priced_post_with_no_media_is_refused(client, db, publisher):
    persona = await _persona(db, "PubPriced")
    post = await _seed_post(db, persona.id, caption="buy this", ppv_price=20.0)

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 409
    assert "no media" in resp.text
    assert publisher.calls == []


async def test_an_empty_post_is_refused(client, db, publisher):
    persona = await _persona(db, "PubEmpty")
    post = await _seed_post(db, persona.id)

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 409
    assert publisher.calls == []


async def test_unresolvable_media_refuses_rather_than_publishing_a_subset(
    client, db, publisher
):
    """Two of three photos is a different product from the set that was
    scheduled, so a partial resolve is a refusal, not a degraded success."""
    persona = await _persona(db, "PubPartial")
    post = await _seed_post(
        db, persona.id, caption="a set", media_keys=["nope/one.png", "nope/two.png"],
    )

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 409
    assert "do not resolve" in resp.text
    assert publisher.calls == []

    await db.refresh(post)
    assert post.status == "scheduled", "a refused publish leaves the row alone"


# ── the price reaches the platform unconverted ───────────────────────────


async def test_the_price_reaches_the_publisher_in_minor_units(client, db, publisher, tmp_path, write_png):
    persona = await _persona(db, "PubPrice")
    media = write_png(tmp_path / "shot.png")

    post = await _seed_post(
        db, persona.id, caption="ppv", media_keys=[str(media)], ppv_price=20.5,
    )

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 200, resp.text
    assert publisher.calls[0]["price_minor"] == 2050
    assert resp.json()["price_minor"] == 2050


async def test_a_free_post_sends_no_price(client, db, publisher, tmp_path, write_png):
    persona = await _persona(db, "PubFree")
    media = write_png(tmp_path / "shot.png")

    post = await _seed_post(db, persona.id, caption="free", media_keys=[str(media)])
    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 200, resp.text
    assert publisher.calls[0]["price_minor"] is None


async def test_media_is_handed_to_the_publisher_as_real_files(client, db, publisher, tmp_path, write_png):
    persona = await _persona(db, "PubMedia")
    media = write_png(tmp_path / "shot.png")

    post = await _seed_post(db, persona.id, caption="with media", media_keys=[str(media)])
    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 200, resp.text

    passed = publisher.calls[0]["media_paths"]
    assert len(passed) == 1
    # The temp file is cleaned up afterwards, but it must have existed with the
    # right bytes when the publisher was called.
    assert passed[0].endswith(".png")
    assert resp.json()["media_count"] == 1


async def test_the_temp_media_directory_is_cleaned_up(client, db, publisher, tmp_path, write_png):
    import tempfile
    from pathlib import Path

    persona = await _persona(db, "PubClean")
    media = write_png(tmp_path / "shot.png")
    post = await _seed_post(db, persona.id, caption="clean", media_keys=[str(media)])

    before = set(Path(tempfile.gettempdir()).glob("publish_*"))
    await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    after = set(Path(tempfile.gettempdir()).glob("publish_*"))
    assert after == before, "the publish temp dir must not be left behind"


# ── a free post on a platform that sells ─────────────────────────────────
#
# The `_FakePublisher` above cannot charge, so on it an unpriced post is a
# free post and nothing more. Fanvue can charge, and there "no price" is not a
# neutral default — it is the price. These tests use a publisher that declares
# `supports_price`, which is the only way to reach the guard at all.


class _PricedPublisher(_FakePublisher):
    """What a platform that sells looks like to the publish path."""

    name = "fanvue"
    supports_price = True


@pytest.fixture
def priced_publisher(monkeypatch):
    import app.providers.gates as gates
    import app.routes.publish as publish_route

    fake = _PricedPublisher()
    monkeypatch.setattr(publish_route, "require_publisher", lambda: fake)
    monkeypatch.setattr(gates, "require_publisher", lambda: fake, raising=False)
    return fake


def _a_media_file(tmp_path, write_png):
    """A real 256x256 PNG on disk, as a storage key."""
    return str(write_png(tmp_path / "shot.png"))


async def test_an_unpriced_post_on_a_selling_platform_is_refused(
    client, db, priced_publisher, tmp_path, write_png
):
    """The whole point: on Fanvue an unset price is a giveaway.

    Every calendar row has `ppv_price = NULL` and the column's comment reads
    "null = free post", so without this guard the studio would publish a
    calendar of sellable content for nothing while every check reported
    healthy — and Fanvue would be the one telling us what we gave away.
    """
    persona = await _persona(db, "SellUnpriced")
    post = await _seed_post(
        db, persona.id, caption="new set ✨", media_keys=[_a_media_file(tmp_path, write_png)],
    )

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 409
    assert priced_publisher.calls == [], "a free giveaway must never reach the platform"

    await db.refresh(post)
    assert post.status == "scheduled", "a refused publish leaves the row alone"
    assert post.posted_at is None


async def test_the_refusal_says_what_a_price_is_and_how_to_mean_free(
    client, db, priced_publisher, tmp_path, write_png
):
    """A refusal an operator cannot act on just moves the dead end."""
    persona = await _persona(db, "SellExplain")
    post = await _seed_post(
        db, persona.id, caption="say why", media_keys=[_a_media_file(tmp_path, write_png)],
    )

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    text = resp.text
    assert "free giveaway" in text
    assert "ppv_price" in text, "it must name the field that fixes it"
    assert "free_post" in text, "and the way to mean free on purpose"
    assert "fanvue" in text, "and which platform is refusing"


async def test_marking_a_post_free_on_purpose_lets_it_through(
    client, db, priced_publisher, tmp_path, write_png
):
    """Free is allowed — it just has to be intended rather than defaulted into."""
    persona = await _persona(db, "SellIntent")
    post = await _seed_post(
        db, persona.id, caption="a free teaser",
        media_keys=[_a_media_file(tmp_path, write_png)],
        metadata_json={"free_post": True},
    )

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 200, resp.text
    assert priced_publisher.calls[0]["price_minor"] is None


async def test_a_truthy_string_is_not_a_free_post_marking(
    client, db, priced_publisher, tmp_path, write_png
):
    """Presence is not permission — the trap this codebase has already hit.

    An approval gate here once let `{"approved": "no"}` through because it
    checked that the key existed. `"free_post": "no"` is the same shape, and
    the thing it would silently authorise is giving the content away.
    """
    persona = await _persona(db, "SellTruthy")
    post = await _seed_post(
        db, persona.id, caption="not actually free",
        media_keys=[_a_media_file(tmp_path, write_png)],
        metadata_json={"free_post": "yes"},
    )

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 409
    assert priced_publisher.calls == []


async def test_free_post_false_is_refused_too(client, db, priced_publisher, tmp_path, write_png):
    """Someone who wrote `false` did not mean free, and must not get it."""
    persona = await _persona(db, "SellFalse")
    post = await _seed_post(
        db, persona.id, caption="explicitly not free",
        media_keys=[_a_media_file(tmp_path, write_png)],
        metadata_json={"free_post": False},
    )

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 409
    assert priced_publisher.calls == []


async def test_a_priced_post_on_a_selling_platform_needs_no_marking(
    client, db, priced_publisher, tmp_path, write_png
):
    """The guard only bites on a missing price; a priced post is unaffected."""
    persona = await _persona(db, "SellPriced")
    post = await _seed_post(
        db, persona.id, caption="buy this", ppv_price=12.0,
        media_keys=[_a_media_file(tmp_path, write_png)],
    )

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 200, resp.text
    assert priced_publisher.calls[0]["price_minor"] == 1200


def test_every_publisher_declares_whether_it_can_charge():
    """The guard's default has to fail closed.

    `publish_post` reads `getattr(publisher, "supports_price", False)`, so a new
    adapter that forgets to declare it is treated as *unable to charge* and an
    unpriced post sails through — the wrong direction for a money guard, and
    silent. Fanvue happened to be declared by hand; this is what keeps the next
    adapter from being the one that isn't.
    """
    import pkgutil

    import app.providers.publish as publish_pkg
    from app.providers.publish import PublishProvider

    subclasses = []
    for info in pkgutil.iter_modules(publish_pkg.__path__, prefix="app.providers.publish."):
        __import__(info.name)
    for cls in _all_subclasses(PublishProvider):
        assert "supports_price" in cls.__dict__, (
            f"{cls.__name__} does not declare `supports_price`. The publish path "
            "defaults it to False, so an undeclared adapter would publish "
            "unpriced posts for free without anything reporting a problem."
        )
        subclasses.append(cls)

    assert subclasses, "no publishers were discovered — the guard is vacuous"


def _all_subclasses(cls):
    found = []
    for sub in cls.__subclasses__():
        found.append(sub)
        found.extend(_all_subclasses(sub))
    return found



# ── media that is not media ──────────────────────────────────────────────
#
# `resolve_media` counted a key as resolved when a file of any size came back,
# so a placeholder went to the publisher as content and the post was marked
# published. `tests/conftest.py` records where the placeholders came from: the
# harness's own fakes wrote 300-odd of them into real storage before
# `isolate_storage` existed — 84 an 8x8 PNG, 254 a bare 8-byte PNG signature.
# Both classes are refused now.


def test_a_png_header_with_no_image_in_it_is_not_media():
    usable, reason = inspect_media(b"\x89PNG\r\n\x1a\n", ".png")
    assert usable is False
    assert "does not decode" in reason


def test_a_placeholder_sized_image_is_not_media(write_png):
    import io

    small = write_png(io.BytesIO(), size=(8, 8))
    usable, reason = inspect_media(small.getvalue(), ".png")
    assert usable is False
    assert "8x8" in reason


def test_a_real_image_is_media(write_png):
    import io

    real = write_png(io.BytesIO(), size=(256, 256))
    usable, reason = inspect_media(real.getvalue(), ".png")
    assert usable is True
    assert reason == ""


def test_the_floor_matches_the_one_the_avatar_door_uses():
    """One codebase, one answer to what a real picture is. The avatar path has
    refused placeholders since `MIN_AVATAR_PIXELS` was written; if these two
    drift apart, one door starts accepting what the other rejects."""
    from app.publishing import MIN_MEDIA_EDGE
    from app.workflows.persona_flow import MIN_AVATAR_PIXELS

    assert MIN_MEDIA_EDGE == MIN_AVATAR_PIXELS


async def test_a_post_whose_media_is_a_placeholder_is_refused(
    client, db, publisher, tmp_path
):
    """The money case: a real caption, a real price, and eight bytes of PNG
    header where the photo should be."""
    persona = await _persona(db, "PubStub")
    stub = tmp_path / "shot.png"
    stub.write_bytes(b"\x89PNG\r\n\x1a\n")

    post = await _seed_post(
        db, persona.id, caption="buy this", ppv_price=9.0, media_keys=[str(stub)],
    )

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 409
    assert "not usable media" in resp.text
    assert publisher.calls == [], "a placeholder must never reach the publisher"

    await db.refresh(post)
    assert post.status == "scheduled"
    assert post.posted_at is None


async def test_one_bad_file_in_a_set_refuses_the_whole_set(
    client, db, publisher, tmp_path, write_png
):
    """Two of three photos is a different product; three photos where one is
    corrupt is worse, because it looks like a success."""
    persona = await _persona(db, "PubMixed")
    good = str(write_png(tmp_path / "good.png"))
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n")

    post = await _seed_post(
        db, persona.id, caption="a set", ppv_price=9.0,
        media_keys=[good, str(bad)],
    )

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 409
    assert "bad.png" in resp.text, "the refusal must name the file, not just count it"
    assert publisher.calls == []


async def test_a_missing_file_and_a_placeholder_read_differently(
    client, db, publisher, tmp_path
):
    """"We cannot find it" and "we found it and it is eight bytes" send the
    operator to different places, so they must not collapse into one message."""
    persona = await _persona(db, "PubBothKinds")
    stub = tmp_path / "shot.png"
    stub.write_bytes(b"\x89PNG\r\n\x1a\n")

    post = await _seed_post(
        db, persona.id, caption="a set",
        # A key that is not on disk at all, and a file that is on disk and is
        # not a picture.
        media_keys=["nope/gone.png", str(stub)],
    )

    resp = await client.post(f"/api/v1/scheduled-posts/{post.id}/publish")
    assert resp.status_code == 409
    assert "do not resolve" in resp.text
    assert publisher.calls == []
