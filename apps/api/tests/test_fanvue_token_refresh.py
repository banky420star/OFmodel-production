"""An access token that rotates must have somewhere to go.

`FanvuePublisher.refresh()` shipped with a docstring that named the exact
failure it was guarding against — "a token rotated inside a provider instance
and never saved is the failure mode where publishing works until the process
restarts and then silently stops" — and then no caller did it. Nothing called
`refresh()` at all: not on a 401, not on a schedule, not from the publisher
factory. A live line would publish fine, then 401 an hour later, and the error
would read as a revoked account.

Two halves are pinned here, because either alone is still broken:

* the publisher **rotates and retries once** on a 401, and
* the rotated pair **reaches durable storage** through the factory's hook.

The token store's own edge cases matter for the same reason: a store that
cannot be read returns "no tokens", so a torn or unreadable file is
indistinguishable from "never configured" — and that is what makes the write
atomic and the read tolerant rather than the reverse.
"""

from __future__ import annotations

import json
import stat

import httpx
import pytest

from app import token_store
from app.providers.publish import (
    PublishDisabled,
    PublishNotConfigured,
    get_publisher,
)
from app.providers.publish.fanvue import FanvuePublisher

CREATOR = "9f8e7d6c-5b4a-3928-1716-050403020100"
ME = "/v1/users/me"


# ── the store ────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    """A store rooted at this test's own tmp dir.

    conftest's `isolate_storage` already keeps the suite out of the real
    `storage/`, so this is not what stops a real account being read. It is here
    because the file-level assertions — mode 0600, no temp files left behind —
    need to look inside the directory the store actually wrote to.
    """
    monkeypatch.setattr(token_store, "store_path", lambda: tmp_path / "oauth_tokens.json")
    return token_store


def test_a_store_with_no_file_reads_as_no_tokens(store):
    assert store.load_tokens("fanvue") == {}


def test_tokens_round_trip(store):
    store.save_tokens("fanvue", {"access_token": "a1", "refresh_token": "r1"})

    assert store.load_tokens("fanvue") == {"access_token": "a1", "refresh_token": "r1"}
    assert store.load_tokens("tiktok") == {}, "providers must not see each other"


def test_the_file_is_owner_only(store, tmp_path):
    """These are live credentials; 0644 would be a world-readable token."""
    store.save_tokens("fanvue", {"access_token": "a1"})

    mode = stat.S_IMODE((tmp_path / "oauth_tokens.json").stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_a_corrupt_store_is_empty_rather_than_fatal(store, tmp_path):
    """`store_path()` is read at app start; raising here would stop the boot."""
    (tmp_path / "oauth_tokens.json").write_text("{not json")

    assert store.load_tokens("fanvue") == {}


def test_a_save_does_not_drop_another_providers_tokens(store):
    store.save_tokens("tiktok", {"access_token": "t"})
    store.save_tokens("fanvue", {"access_token": "f"})

    assert store.load_tokens("tiktok") == {"access_token": "t"}
    assert store.load_tokens("fanvue") == {"access_token": "f"}


def test_saving_leaves_no_temp_files_behind(store, tmp_path):
    store.save_tokens("fanvue", {"access_token": "a1"})

    assert sorted(p.name for p in tmp_path.iterdir()) == ["oauth_tokens.json"]


# ── the publisher rotates on 401 ─────────────────────────────────────

def _publisher(handlers, *, refresh_token="r1", **kwargs) -> FanvuePublisher:
    """A publisher whose every leg — API and auth — goes through MockTransport.

    `auth_url` is pointed at the mock host too, so a `refresh()` that reached
    its own client would still be caught here rather than hitting the network.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        fn = handlers.get((request.method, request.url.path))
        if fn is None:
            return httpx.Response(
                404, json={"error": f"unrouted {request.method} {request.url.path}"}
            )
        return fn(request)

    return FanvuePublisher(
        client_id="client-abc",
        refresh_token=refresh_token,
        access_token="stale",
        creator_uuid=CREATOR,
        api_base="https://api.fanvue.test",
        auth_url="https://api.fanvue.test/oauth2/token",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def _expiring_then_ok(*, new_refresh="r2"):
    """First call 401s, the refresh hands back a pair, the retry succeeds."""
    seen = {"me_calls": 0, "refresh_body": None}

    def me(request):
        seen["me_calls"] += 1
        if request.headers.get("authorization") == "Bearer fresh":
            return httpx.Response(200, json={"uuid": CREATOR})
        return httpx.Response(401, json={"error": "token expired"})

    def refresh(request):
        seen["refresh_body"] = dict(
            p.split("=", 1) for p in request.content.decode().split("&") if "=" in p
        )
        return httpx.Response(200, json={
            "access_token": "fresh", "refresh_token": new_refresh, "expires_in": 3600,
        })

    return seen, {("GET", ME): me, ("POST", "/oauth2/token"): refresh}


async def test_a_401_is_rotated_and_retried_once():
    seen, handlers = _expiring_then_ok()
    publisher = _publisher(handlers)

    ok, detail = await publisher.health_check()

    assert ok is True, f"the retry should have succeeded, got {detail!r}"
    assert seen["me_calls"] == 2, "one original call, one retry — no more"
    assert publisher.access_token == "fresh"
    assert seen["refresh_body"]["grant_type"] == "refresh_token"
    assert seen["refresh_body"]["refresh_token"] == "r1"


async def test_the_rotated_pair_reaches_the_persistence_hook():
    """The hook is the whole point — a rotation nobody saves is the bug."""
    _, handlers = _expiring_then_ok()
    saved = []
    publisher = _publisher(handlers, on_tokens_refreshed=lambda a, r: saved.append((a, r)))

    await publisher.health_check()

    assert saved == [("fresh", "r2")], (
        "the new pair must be handed to the caller exactly once, after both "
        "values are updated — a rotation that reaches memory only dies with "
        "the process"
    )


async def test_a_refresh_response_without_a_new_refresh_token_keeps_the_old_one():
    """Fanvue rotates, but a non-rotating response must not blank the field."""
    seen = {"me_calls": 0}

    def me(request):
        seen["me_calls"] += 1
        if request.headers.get("authorization") == "Bearer fresh":
            return httpx.Response(200, json={"uuid": CREATOR})
        return httpx.Response(401, json={})

    publisher = _publisher({
        ("GET", ME): me,
        ("POST", "/oauth2/token"): lambda r: httpx.Response(200, json={"access_token": "fresh"}),
    })

    await publisher.health_check()

    assert publisher.refresh_token == "r1", "the old refresh token is still the live one"


async def test_a_refused_refresh_returns_the_original_401_and_does_not_loop():
    """A revoked account must surface as a 401, not as an endless rotation."""
    seen = {"me_calls": 0, "refreshes": 0}

    def me(request):
        seen["me_calls"] += 1
        return httpx.Response(401, json={"error": "revoked"})

    def refresh(request):
        seen["refreshes"] += 1
        return httpx.Response(400, json={"error": "invalid_grant"})

    publisher = _publisher({("GET", ME): me, ("POST", "/oauth2/token"): refresh})

    ok, detail = await publisher.health_check()

    assert ok is False
    assert "401" in detail, "the account's own error is the useful one here"
    assert seen["refreshes"] == 1
    assert seen["me_calls"] == 1, "no retry once the refresh itself failed"


async def test_with_no_refresh_token_a_401_is_just_a_401():
    """Nothing to rotate with — the failure must be the call's, not the store's."""
    seen = {"refreshes": 0}

    def refresh(request):
        seen["refreshes"] += 1
        return httpx.Response(200, json={"access_token": "fresh"})

    publisher = _publisher(
        {("GET", ME): lambda r: httpx.Response(401, json={}),
         ("POST", "/oauth2/token"): refresh},
        refresh_token="",
    )

    ok, detail = await publisher.health_check()

    assert ok is False and "401" in detail
    assert seen["refreshes"] == 0


async def test_a_successful_call_is_not_retried():
    """The retry must be reachable only on a 401."""
    seen = {"me_calls": 0}

    def me(request):
        seen["me_calls"] += 1
        return httpx.Response(200, json={"uuid": CREATOR})

    publisher = _publisher({("GET", ME): me})

    assert (await publisher.health_check())[0] is True
    assert seen["me_calls"] == 1


# ── the factory wires the two halves together ────────────────────────

@pytest.fixture
def settings(store, monkeypatch):
    """The real cached Settings, with Fanvue configured but publishing disarmed.

    `get_settings()` is `@lru_cache`d, so `monkeypatch.setenv` after first
    import is invisible; the attributes are set on the cached instance instead.
    """
    from app.config import get_settings

    live = get_settings()
    for field, value in {
        "FANVUE_CLIENT_ID": "client-abc",
        "FANVUE_CLIENT_SECRET": "",
        "FANVUE_ACCESS_TOKEN": "env-token",
        "FANVUE_REFRESH_TOKEN": "env-refresh",
        "FANVUE_CREATOR_UUID": CREATOR,
        "FANVUE_PUBLISH_ENABLED": True,
    }.items():
        monkeypatch.setattr(live, field, value, raising=False)
    return live


def test_the_factory_prefers_a_stored_token_over_the_env_one(settings, store):
    """A rotated pair is newer than the `.env` line it replaced."""
    store.save_tokens("fanvue", {"access_token": "stored", "refresh_token": "stored-r"})

    publisher = get_publisher()

    assert publisher.access_token == "stored"
    assert publisher.refresh_token == "stored-r"
    assert publisher._on_tokens_refreshed is not None, (
        "the publisher must be built with a hook, or a rotation is lost"
    )


def test_with_nothing_stored_the_env_token_is_used(settings, store):
    publisher = get_publisher()

    assert publisher.access_token == "env-token"
    assert publisher.refresh_token == "env-refresh"


def test_the_factory_hook_actually_writes_the_rotated_pair(settings, store):
    """End to end through the factory's own closure, not a hand-made lambda."""
    publisher = get_publisher()
    publisher.access_token, publisher.refresh_token = "rotated", "rotated-r"

    publisher._on_tokens_refreshed(publisher.access_token, publisher.refresh_token)

    stored = store.load_tokens("fanvue")
    assert stored["access_token"] == "rotated"
    assert stored["refresh_token"] == "rotated-r"
    assert stored["obtained_at"], "when it was obtained is what makes it judgeable later"


def test_a_stored_token_alone_is_enough_to_configure(settings, store, monkeypatch):
    """The `.env` token may be blank once a refresh token has been stored."""
    monkeypatch.setattr(settings, "FANVUE_ACCESS_TOKEN", "", raising=False)
    monkeypatch.setattr(settings, "FANVUE_REFRESH_TOKEN", "", raising=False)
    store.save_tokens("fanvue", {"access_token": "stored", "refresh_token": "r"})

    assert get_publisher().access_token == "stored"


def test_with_neither_store_nor_env_it_says_which_two_places_it_looked(settings, store, monkeypatch):
    monkeypatch.setattr(settings, "FANVUE_ACCESS_TOKEN", "", raising=False)

    with pytest.raises(PublishNotConfigured) as exc:
        get_publisher()

    assert "oauth_tokens.json" in str(exc.value)
    assert "FANVUE_ACCESS_TOKEN" in str(exc.value)


def test_the_arm_switch_still_beats_a_stored_token(settings, store, monkeypatch):
    """Credentials are not permission — the store must not arm publishing."""
    store.save_tokens("fanvue", {"access_token": "stored", "refresh_token": "r"})
    monkeypatch.setattr(settings, "FANVUE_PUBLISH_ENABLED", False, raising=False)

    with pytest.raises(PublishDisabled):
        get_publisher()


def test_an_unwritable_store_does_not_fail_the_call_it_was_rotating_for(
    settings, monkeypatch, caplog
):
    """Losing the save must not lose the post — but it must be said out loud.

    The hook runs inside `refresh()`, which runs inside `_request`'s 401 retry.
    If a read-only disk could raise through it, a credential problem would turn
    into a failed publish of content that is otherwise ready to go.
    """
    publisher = get_publisher()

    def explode(*_args, **_kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(token_store, "save_tokens", explode)

    with caplog.at_level("ERROR"):
        publisher._on_tokens_refreshed("rotated", "rotated-r")  # must not raise

    assert "could not be persisted" in caplog.text
    assert "stop at the next restart" in caplog.text, (
        "the log has to name the consequence, not just the errno — this is the "
        "only signal that the line will 401 from the next boot onward"
    )
