"""The Fanvue publisher, and the promise that it fails closed.

This is the first code in the project that can put a persona in front of a
paying stranger on a platform the operator does not own. So the tests are
weighted toward the refusals rather than the happy path: an unconfigured
publisher must say "not configured", a configured-but-unarmed one must say
"disabled", and a platform error must never come back as `ok=True`.

Everything runs against `httpx.MockTransport`, so no test here can reach the
network even by accident.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.providers.publish import (
    PublishDisabled,
    PublishFailed,
    PublishNotConfigured,
    get_publisher,
)
from app.providers.publish.fanvue import MIN_PRICE_MINOR, FanvuePublisher

CREATOR = "11111111-2222-3333-4444-555555555555"


# ── the fail-closed ladder ───────────────────────────────────────────
#
# get_settings() is lru_cached, so environment variables set inside a test are
# invisible — the cached instance was built at first import. The fields are
# patched on that instance instead, which is the same seam
# tests/test_adult_gate.py uses. Each test removes one piece and asserts the
# *specific* refusal, because "it raised something" would still pass if the arm
# switch were wired to the wrong field.

@pytest.fixture
def fanvue_settings(monkeypatch):
    """A fully configured, fully armed publisher — each test un-sets one field.

    `get_publisher()` also overlays `storage/oauth_tokens.json` on these
    settings. That file needs no fixture here: `token_store.store_path()` reads
    `paths.STORAGE_ROOT` at call time, and conftest's `isolate_storage` already
    points it at a session tmp dir — so these tests cannot pick up a real
    authorized account from the developer's own machine.
    """
    settings = get_settings()
    for name, value in (
        ("FANVUE_CLIENT_ID", "client-abc"),
        ("FANVUE_CLIENT_SECRET", ""),
        ("FANVUE_ACCESS_TOKEN", "token-abc"),
        ("FANVUE_REFRESH_TOKEN", ""),
        ("FANVUE_CREATOR_UUID", CREATOR),
        ("FANVUE_PUBLISH_ENABLED", True),
    ):
        monkeypatch.setattr(settings, name, value, raising=False)
    return settings


def test_no_credentials_is_not_configured(fanvue_settings, monkeypatch):
    monkeypatch.setattr(fanvue_settings, "FANVUE_CLIENT_ID", "", raising=False)
    with pytest.raises(PublishNotConfigured):
        get_publisher()


def test_a_client_id_alone_is_still_not_configured(fanvue_settings, monkeypatch):
    monkeypatch.setattr(fanvue_settings, "FANVUE_ACCESS_TOKEN", "", raising=False)
    with pytest.raises(PublishNotConfigured) as excinfo:
        get_publisher()
    assert "FANVUE_ACCESS_TOKEN" in str(excinfo.value)


def test_credentials_without_the_creator_uuid_are_not_configured(fanvue_settings, monkeypatch):
    monkeypatch.setattr(fanvue_settings, "FANVUE_CREATOR_UUID", "", raising=False)
    with pytest.raises(PublishNotConfigured) as excinfo:
        get_publisher()
    assert "FANVUE_CREATOR_UUID" in str(excinfo.value)


def test_a_token_is_not_permission(fanvue_settings, monkeypatch):
    """Configured but unarmed must be `disabled`, never a silent success."""
    monkeypatch.setattr(fanvue_settings, "FANVUE_PUBLISH_ENABLED", False, raising=False)
    with pytest.raises(PublishDisabled):
        get_publisher()


def test_armed_and_configured_builds_a_publisher(fanvue_settings):
    publisher = get_publisher()

    assert isinstance(publisher, FanvuePublisher)
    assert publisher.creator_uuid == CREATOR
    assert publisher.access_token == "token-abc"


def test_the_route_gate_separates_unconfigured_from_unarmed(fanvue_settings, monkeypatch):
    """503 vs 409. Collapsing them sends an operator looking for a missing token
    that is already in .env."""
    from fastapi import HTTPException

    from app.providers.gates import require_publisher

    assert isinstance(require_publisher(), FanvuePublisher)

    monkeypatch.setattr(fanvue_settings, "FANVUE_PUBLISH_ENABLED", False, raising=False)
    with pytest.raises(HTTPException) as armed:
        require_publisher()
    assert armed.value.status_code == 409

    monkeypatch.setattr(fanvue_settings, "FANVUE_ACCESS_TOKEN", "", raising=False)
    with pytest.raises(HTTPException) as configured:
        require_publisher()
    assert configured.value.status_code == 503


def test_the_default_is_off_and_unconfigured():
    """The shipped defaults, read from the model itself rather than a .env.

    An operator's .env is not part of this assertion; the point is that a
    checkout with no Fanvue entries cannot post.
    """
    from app.config import Settings

    defaults = Settings.model_fields
    assert defaults["FANVUE_PUBLISH_ENABLED"].default is False
    assert defaults["FANVUE_CLIENT_ID"].default == ""
    assert defaults["FANVUE_ACCESS_TOKEN"].default == ""


# ── request-level behaviour ──────────────────────────────────────────

def _routes(handlers):
    """Route by (method, path). Anything unrouted is a loud 404.

    Handlers are synchronous on purpose: httpx.MockTransport will not await a
    coroutine handler here, and a test that silently 404s because its handler
    returned a coroutine would be worse than one that fails.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        fn = handlers.get((request.method, request.url.path))
        if fn is None:
            return httpx.Response(
                404, json={"error": f"unrouted {request.method} {request.url.path}"}
            )
        return fn(request)
    return handler


def _publisher(handlers) -> FanvuePublisher:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_routes(handlers)))
    return FanvuePublisher(
        client_id="client-abc",
        access_token="token-abc",
        creator_uuid=CREATOR,
        api_base="https://api.fanvue.test",
        client=client,
    )


def _plate(tmp_path: Path, name: str = "plate.png", size: int = 64) -> Path:
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * size)
    return path


async def test_every_request_carries_the_version_header():
    """Fanvue versions by header; a missing one is a 400 on every call."""
    seen = {}

    def me(request):
        seen.update(dict(request.headers))
        return httpx.Response(200, json={"uuid": CREATOR})

    publisher = _publisher({("GET", "/v1/users/me"): me})

    ok, _ = await publisher.health_check()

    assert ok is True
    assert seen["x-fanvue-api-version"]
    assert seen["authorization"] == "Bearer token-abc"


async def test_an_expired_token_reads_as_unhealthy_not_as_up():
    publisher = _publisher({
        ("GET", "/v1/users/me"): lambda r: httpx.Response(401, json={"error": "expired"}),
    })

    ok, detail = await publisher.health_check()

    assert ok is False
    assert "401" in detail


# ── media upload ─────────────────────────────────────────────────────

def _upload_handlers(*, etag: str | None = '"etag-1"', parts: int = 1):
    uploads = f"/v1/creators/{CREATOR}/media/uploads"
    handlers = {
        ("POST", uploads): lambda r: httpx.Response(200, json={
            "mediaUuid": "media-1", "uploadId": "upload-1",
            "partSize": 10_000_000, "maxParts": 100, "totalParts": parts,
        }),
        ("GET", f"{uploads}/upload-1/parts/urls"): lambda r: httpx.Response(200, json={
            "partSize": 10_000_000,
            "parts": [{"partNumber": i, "url": f"https://s3.fanvue.test/part{i}"}
                      for i in range(1, parts + 1)],
        }),
        ("PATCH", f"{uploads}/upload-1"): lambda r: httpx.Response(200, json={"status": "complete"}),
    }
    for i in range(1, parts + 1):
        headers = {"ETag": etag} if etag else {}
        handlers[("PUT", f"/part{i}")] = lambda r, h=headers: httpx.Response(200, headers=h)
    return handlers


async def test_upload_follows_the_session_flow(tmp_path):
    seen = {"patched": None}

    def patch(request):
        seen["patched"] = json.loads(request.content)
        return httpx.Response(200, json={"status": "complete"})

    handlers = _upload_handlers()
    handlers[("PATCH", f"/v1/creators/{CREATOR}/media/uploads/upload-1")] = patch
    publisher = _publisher(handlers)

    media_uuid = await publisher.upload_media(_plate(tmp_path))

    assert media_uuid == "media-1"
    # Capitalised keys, quote stripped off the ETag — what the OpenAPI schema
    # declares for the finalize body.
    assert seen["patched"] == {"parts": [{"PartNumber": 1, "ETag": "etag-1"}]}


async def test_a_part_without_an_etag_refuses_rather_than_finalizing(tmp_path):
    """Finalizing without ETags would upload bytes the platform cannot assemble."""
    publisher = _publisher(_upload_handlers(etag=None))

    with pytest.raises(PublishFailed) as excinfo:
        await publisher.upload_media(_plate(tmp_path))
    assert "ETag" in str(excinfo.value)


async def test_a_missing_file_refuses_to_upload(tmp_path):
    publisher = _publisher({})
    with pytest.raises(PublishFailed):
        await publisher.upload_media(tmp_path / "nope.png")


# ── posting ──────────────────────────────────────────────────────────

async def test_a_price_below_the_platform_minimum_is_refused():
    publisher = _publisher({})
    result = await publisher.create_post(
        text="hi", media_paths=["x.png"], price_minor=MIN_PRICE_MINOR - 1,
    )

    assert result.ok is False
    assert "minimum" in result.error


async def test_a_priced_post_without_media_is_refused():
    """Fanvue rejects it; refusing here keeps the failure local and named."""
    publisher = _publisher({})
    result = await publisher.create_post(text="hi", price_minor=900)

    assert result.ok is False
    assert "media" in result.error


async def test_posting_nothing_is_refused():
    publisher = _publisher({})
    result = await publisher.create_post(text="", media_paths=[])

    assert result.ok is False


async def test_an_unknown_audience_is_refused():
    publisher = _publisher({})
    result = await publisher.create_post(
        text="hi", media_paths=["x.png"], audience="everyone",
    )

    assert result.ok is False
    assert "audience" in result.error


async def test_a_post_carries_the_price_through_unconverted(tmp_path):
    """Cents in, cents out. A /100 here would silently sell a $9 set for $0.09."""
    sent = {}

    def create(request):
        sent.update(json.loads(request.content))
        return httpx.Response(201, json={
            "uuid": "post-1", "publishedAt": "2026-09-30T10:00:00Z",
        })

    handlers = _upload_handlers()
    handlers[("POST", f"/v1/creators/{CREATOR}/posts")] = create
    publisher = _publisher(handlers)

    result = await publisher.create_post(
        text="New set is up", media_paths=[_plate(tmp_path)], price_minor=900,
    )

    assert result.ok is True
    assert result.post_uuid == "post-1"
    assert sent["price"] == 900, "the price must not be converted on the way out"
    assert sent["audience"] == "subscribers"
    assert sent["text"] == "New set is up"
    assert sent["mediaUuids"] == ["media-1"]
    assert sent["mediaPreviewUuid"] == "media-1"


async def test_a_401_while_posting_raises_rather_than_reporting_ok(tmp_path):
    handlers = _upload_handlers()
    handlers[("POST", f"/v1/creators/{CREATOR}/posts")] = (
        lambda r: httpx.Response(401, json={"error": "unauthorized"})
    )
    publisher = _publisher(handlers)

    with pytest.raises(PublishFailed) as excinfo:
        await publisher.create_post(text="hi", media_paths=[_plate(tmp_path)])
    assert "401" in str(excinfo.value)


async def test_a_response_with_no_post_uuid_is_not_a_success(tmp_path):
    """A 201 with an unexpected body still must not be reported as posted."""
    handlers = _upload_handlers()
    handlers[("POST", f"/v1/creators/{CREATOR}/posts")] = (
        lambda r: httpx.Response(201, json={"something": "else"})
    )
    publisher = _publisher(handlers)

    with pytest.raises(PublishFailed):
        await publisher.create_post(text="hi", media_paths=[_plate(tmp_path)])
