"""Fanvue OAuth — the step that turns "a correct publisher" into "a post".

Measured before this existed: `FanvuePublisher` could upload media, price a post
and read earnings, and had never once done any of it, because the access token
Fanvue requires is issued only through an authorization code flow that no route
in this app implemented. The publisher's own error message said so — "Complete
the OAuth authorization (PKCE)" — and pointed at no way to complete it.

These tests pin the four things that make the flow real: the authorize URL is
built with the scopes this app actually uses, the state nonce is checked and
single-use, the token is stored where `get_publisher()` reads it, and the
verification step tells the truth about whether the grant works.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from app import token_store
from app.config import get_settings
from app.providers.publish.fanvue import (
    DEFAULT_SCOPES,
    FanvuePublisher,
    _find_uuid,
    build_authorize_url,
    pkce_pair,
)

# Every scope the published spec lists (https://api.fanvue.com/docs/openapi-v1.json,
# securityScheme BearerAuth). Requesting anything outside this set is a typo that
# the platform silently ignores, which reads as a missing grant later.
_DOCUMENTED_SCOPES = frozenset({
    "read:self", "read:chat", "write:chat", "read:experience", "write:experience",
    "read:fan", "read:post", "write:post", "read:media", "write:media",
    "read:creator", "write:creator", "read:insights", "read:tracking_links",
    "write:tracking_links", "read:agency", "write:agency",
})

REDIRECT = "https://studio.example.test/api/v1/fanvue/callback"


def _settings(**fields):
    """Set fields on the *cached* settings instance — `get_settings()` is
    `lru_cache`d, so `monkeypatch.setenv` after first import is invisible."""
    for name, value in fields.items():
        setattr(get_settings(), name, value)
    return get_settings()


@pytest.fixture
def fanvue_settings(monkeypatch):
    """A configured-but-not-armed Fanvue, restored afterwards."""
    settings = get_settings()
    saved = {
        name: getattr(settings, name)
        for name in (
            "FANVUE_CLIENT_ID", "FANVUE_CLIENT_SECRET", "FANVUE_REDIRECT_URI",
            "FANVUE_CREATOR_UUID", "FANVUE_PUBLISH_ENABLED", "FANVUE_ACCESS_TOKEN",
            "FANVUE_REFRESH_TOKEN",
        )
    }
    monkeypatch.setattr(settings, "FANVUE_CLIENT_ID", "client-abc")
    monkeypatch.setattr(settings, "FANVUE_CLIENT_SECRET", "")
    monkeypatch.setattr(settings, "FANVUE_REDIRECT_URI", REDIRECT)
    monkeypatch.setattr(settings, "FANVUE_ACCESS_TOKEN", "")
    monkeypatch.setattr(settings, "FANVUE_REFRESH_TOKEN", "")
    monkeypatch.setattr(settings, "FANVUE_CREATOR_UUID", "")
    monkeypatch.setattr(settings, "FANVUE_PUBLISH_ENABLED", False)
    token_store.clear_tokens(token_store.PROVIDER_FANVUE)
    token_store.clear_pending(token_store.PROVIDER_FANVUE)
    yield settings
    for name, value in saved.items():
        setattr(settings, name, value)
    token_store.clear_tokens(token_store.PROVIDER_FANVUE)
    token_store.clear_pending(token_store.PROVIDER_FANVUE)


def _fake_exchange(body=None, *, raises=None):
    """Stand in for the token endpoint. Records the call it was given."""
    calls: list[dict] = []

    async def _exchange(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return body if body is not None else {
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "expires_in": 3600,
            "scope": " ".join(DEFAULT_SCOPES),
        }

    _exchange.calls = calls
    return _exchange


@pytest.fixture
def verified(monkeypatch):
    """Make the live verification read answer without touching the network."""
    def _install(*, reachable=True, detail="authenticated", creator="11111111-2222-3333-4444-555555555555"):
        async def health(_self):
            return reachable, detail

        async def uuid_of_token(_self):
            return creator

        monkeypatch.setattr(FanvuePublisher, "health_check", health)
        monkeypatch.setattr(FanvuePublisher, "creator_uuid_of_token", uuid_of_token)

    return _install


# ── PKCE ─────────────────────────────────────────────────────────────────


def test_the_challenge_is_the_sha256_of_the_verifier():
    """The one thing PKCE is: a challenge the server can check without ever
    having seen the verifier."""
    verifier, challenge = pkce_pair()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert challenge == expected


def test_the_verifier_and_challenge_are_in_the_shape_rfc7636_requires():
    """43–128 unreserved characters, and no padding on the challenge — `=` is
    the usual reason a correct verifier is refused."""
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    for value in (verifier, challenge):
        assert set(value) <= allowed, f"{value!r} has characters outside the unreserved set"
        assert "=" not in value
    assert verifier != challenge


def test_two_handshakes_do_not_share_a_verifier():
    assert pkce_pair()[0] != pkce_pair()[0]


def test_the_authorize_url_asks_for_exactly_the_documented_scopes():
    """A typo in a scope is not an error at the platform — it is a grant that is
    quietly missing a permission, discovered later as a 403."""
    assert set(DEFAULT_SCOPES) <= _DOCUMENTED_SCOPES


def test_the_authorize_url_carries_publishing_and_earnings():
    """`write:post`/`write:media` are what make a post possible; `read:insights`
    is the only scope that can answer whether any money arrived. A grant without
    them is authorized and useless."""
    assert {"write:post", "write:media", "read:insights"} <= set(DEFAULT_SCOPES)


def test_the_authorize_url_is_a_well_formed_code_request():
    url = build_authorize_url(
        client_id="client-abc", redirect_uri=REDIRECT, state="nonce-1", challenge="chal-1"
    )
    assert url.startswith("https://auth.fanvue.com/oauth2/auth?")
    assert "response_type=code" in url
    assert "client_id=client-abc" in url
    assert "code_challenge=chal-1" in url
    assert "code_challenge_method=S256" in url
    assert "state=nonce-1" in url
    # The redirect URI is sent as one parameter, not split on its own slashes.
    assert "redirect_uri=https%3A%2F%2Fstudio.example.test" in url


# ── connect ──────────────────────────────────────────────────────────────


async def test_connect_refuses_without_a_client_id(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "FANVUE_CLIENT_ID", "")
    monkeypatch.setattr(get_settings(), "FANVUE_REDIRECT_URI", REDIRECT)
    resp = await client.get("/api/v1/fanvue/connect")
    assert resp.status_code == 503
    assert "FANVUE_CLIENT_ID" in resp.text


async def test_connect_refuses_without_a_redirect_uri(client, fanvue_settings):
    """Without one that matches the client registration, Fanvue bounces the
    operator to an error page with no explanation — a worse answer than a 503."""
    _settings(FANVUE_REDIRECT_URI="")
    resp = await client.get("/api/v1/fanvue/connect")
    assert resp.status_code == 503
    assert "FANVUE_REDIRECT_URI" in resp.text


async def test_connect_returns_an_authorize_url_and_pins_the_nonce(client, fanvue_settings):
    resp = await client.get("/api/v1/fanvue/connect")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    pending = token_store.load_pending(token_store.PROVIDER_FANVUE)
    assert pending["code_verifier"]
    assert pending["redirect_uri"] == REDIRECT
    assert f"state={pending['nonce']}" in body["authorize_url"]
    assert "code_challenge=" in body["authorize_url"]


async def test_a_second_connect_invalidates_the_first(client, fanvue_settings):
    """Two tabs, one handshake. The older one must not be completable — the last
    authorization the operator started is the one they are looking at."""
    await client.get("/api/v1/fanvue/connect")
    first = token_store.load_pending(token_store.PROVIDER_FANVUE)

    await client.get("/api/v1/fanvue/connect")
    second = token_store.load_pending(token_store.PROVIDER_FANVUE)

    assert first["nonce"] != second["nonce"]
    assert first["code_verifier"] != second["code_verifier"]


# ── callback ─────────────────────────────────────────────────────────────


async def _start(client):
    resp = await client.get("/api/v1/fanvue/connect")
    assert resp.status_code == 200, resp.text
    return token_store.load_pending(token_store.PROVIDER_FANVUE)["nonce"]


async def test_callback_refuses_a_state_it_did_not_issue(client, fanvue_settings, monkeypatch):
    monkeypatch.setattr("app.routes.fanvue.exchange_code", _fake_exchange())
    await _start(client)

    resp = await client.get("/api/v1/fanvue/callback?code=c&state=not-the-nonce")

    assert resp.status_code == 400
    assert "State check failed" in resp.text
    assert token_store.load_tokens(token_store.PROVIDER_FANVUE) == {}


async def test_callback_refuses_a_state_when_no_handshake_was_started(client, fanvue_settings, monkeypatch):
    monkeypatch.setattr("app.routes.fanvue.exchange_code", _fake_exchange())
    resp = await client.get("/api/v1/fanvue/callback?code=c&state=anything")
    assert resp.status_code == 400
    assert token_store.load_tokens(token_store.PROVIDER_FANVUE) == {}


async def test_callback_stores_the_pair_where_the_publisher_reads_it(
    client, fanvue_settings, monkeypatch, verified
):
    """The whole point. `get_publisher()` resolves stored-then-configured, so a
    pair written here is the pair the next publish uses."""
    exchange = _fake_exchange()
    monkeypatch.setattr("app.routes.fanvue.exchange_code", exchange)
    verified()
    nonce = await _start(client)
    issued_verifier = token_store.load_pending(token_store.PROVIDER_FANVUE)["code_verifier"]

    resp = await client.get(f"/api/v1/fanvue/callback?code=the-code&state={nonce}")

    assert resp.status_code == 200, resp.text
    stored = token_store.load_tokens(token_store.PROVIDER_FANVUE)
    assert stored["access_token"] == "access-1"
    assert stored["refresh_token"] == "refresh-1"
    assert stored["obtained_at"]

    # The verifier the challenge was built from is the one sent back — the half
    # of PKCE that only works if it is the same one the challenge came from.
    assert exchange.calls[0]["code_verifier"] == issued_verifier
    assert exchange.calls[0]["code"] == "the-code"
    assert exchange.calls[0]["redirect_uri"] == REDIRECT
    assert exchange.calls[0]["client_id"] == "client-abc"


async def test_the_challenge_sent_is_the_s256_of_the_verifier_stored(client, fanvue_settings):
    """PKCE's one invariant, checked across the two calls that have to agree: the
    browser carries the challenge, the exchange carries the verifier, and the
    server accepts the pair only if the second hashes to the first."""
    body = (await client.get("/api/v1/fanvue/connect")).json()
    verifier = token_store.load_pending(token_store.PROVIDER_FANVUE)["code_verifier"]

    from urllib.parse import parse_qs, urlparse

    sent = parse_qs(urlparse(body["authorize_url"]).query)["code_challenge"][0]
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert sent == expected


async def test_callback_reports_a_creator_uuid_that_is_not_configured(
    client, fanvue_settings, monkeypatch, verified
):
    """Every upload and post path is keyed by the creator uuid and nothing in
    the app can discover it, so the one moment it is a single API call away is
    the moment to say it out loud."""
    monkeypatch.setattr("app.routes.fanvue.exchange_code", _fake_exchange())
    verified(creator="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    nonce = await _start(client)

    resp = await client.get(f"/api/v1/fanvue/callback?code=c&state={nonce}")

    assert "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" in resp.text
    assert "FANVUE_CREATOR_UUID" in resp.text


async def test_callback_warns_when_the_configured_creator_is_a_different_account(
    client, fanvue_settings, monkeypatch, verified
):
    """A token that works but belongs to another creator looks authorized and
    404s on every path — the failure mode this check exists to catch."""
    _settings(FANVUE_CREATOR_UUID="00000000-0000-0000-0000-000000000000")
    monkeypatch.setattr("app.routes.fanvue.exchange_code", _fake_exchange())
    verified(creator="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    nonce = await _start(client)

    resp = await client.get(f"/api/v1/fanvue/callback?code=c&state={nonce}")

    assert "different" in resp.text
    assert "00000000-0000-0000-0000-000000000000" in resp.text


async def test_callback_says_publishing_is_still_off(client, fanvue_settings, monkeypatch, verified):
    """A token is not permission. The arm switch is separate on purpose, and the
    operator has to be told so at the end of the flow rather than discovering it
    when a post is refused."""
    monkeypatch.setattr("app.routes.fanvue.exchange_code", _fake_exchange())
    verified()
    nonce = await _start(client)

    resp = await client.get(f"/api/v1/fanvue/callback?code=c&state={nonce}")

    assert "FANVUE_PUBLISH_ENABLED" in resp.text


async def test_the_code_is_not_claimed_to_work_when_verification_fails(
    client, fanvue_settings, monkeypatch, verified
):
    monkeypatch.setattr("app.routes.fanvue.exchange_code", _fake_exchange())
    verified(reachable=False, detail="Fanvue returned 401 — the access token is expired")
    nonce = await _start(client)

    resp = await client.get(f"/api/v1/fanvue/callback?code=c&state={nonce}")

    assert "Token stored" in resp.text
    assert "not verified" in resp.text
    assert "401" in resp.text


async def test_a_failed_exchange_stores_nothing(client, fanvue_settings, monkeypatch):
    from app.providers.publish import PublishFailed

    monkeypatch.setattr(
        "app.routes.fanvue.exchange_code",
        _fake_exchange(raises=PublishFailed("token endpoint said no")),
    )
    nonce = await _start(client)

    resp = await client.get(f"/api/v1/fanvue/callback?code=c&state={nonce}")

    assert resp.status_code == 502
    assert "token endpoint said no" in resp.text
    assert token_store.load_tokens(token_store.PROVIDER_FANVUE) == {}


async def test_the_nonce_is_single_use(client, fanvue_settings, monkeypatch, verified):
    """Replaying the same callback must not reuse a spent verifier."""
    monkeypatch.setattr("app.routes.fanvue.exchange_code", _fake_exchange())
    verified()
    nonce = await _start(client)

    first = await client.get(f"/api/v1/fanvue/callback?code=c&state={nonce}")
    assert first.status_code == 200

    second = await client.get(f"/api/v1/fanvue/callback?code=c&state={nonce}")
    assert second.status_code == 400
    assert "State check failed" in second.text


async def test_an_error_from_the_platform_is_reported_not_swallowed(client, fanvue_settings):
    resp = await client.get(
        "/api/v1/fanvue/callback?error=access_denied&error_description=The+operator+refused"
    )
    assert resp.status_code == 400
    assert "The operator refused" in resp.text


# ── status ───────────────────────────────────────────────────────────────


async def test_status_separates_configured_authorized_and_armed(client, fanvue_settings):
    """"No credentials" and "credentials but posting off" are different problems
    with different fixes; collapsing them sends the operator hunting for a token
    that is already there."""
    resp = await client.get("/api/v1/fanvue/status")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["authorized"] is False
    assert body["armed"] is False
    assert body["ok"] is False
    assert "connect" in body["detail"]


async def test_status_reports_a_live_unverified_when_the_token_is_dead(
    client, fanvue_settings, monkeypatch, verified
):
    token_store.save_tokens(
        token_store.PROVIDER_FANVUE, {"access_token": "stale", "refresh_token": "r"}
    )
    verified(reachable=False, detail="Fanvue returned 401 — the access token is expired")

    body = (await client.get("/api/v1/fanvue/status")).json()

    assert body["authorized"] is True
    assert body["ok"] is False
    assert "401" in body["detail"]
    assert body["token_source"] == "storage/oauth_tokens.json"


async def test_status_catches_a_token_pointed_at_the_wrong_creator(
    client, fanvue_settings, monkeypatch, verified
):
    """The exact shape of "looks fine, earns nothing"."""
    _settings(FANVUE_CREATOR_UUID="00000000-0000-0000-0000-000000000000")
    token_store.save_tokens(token_store.PROVIDER_FANVUE, {"access_token": "live"})
    verified(creator="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    body = (await client.get("/api/v1/fanvue/status")).json()

    assert body["ok"] is True
    assert body["creator_uuid_matches"] is False
    assert body["live_creator_uuid"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


async def test_status_points_at_the_arm_switch_when_everything_else_is_right(
    client, fanvue_settings, monkeypatch, verified
):
    _settings(FANVUE_CREATOR_UUID="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    token_store.save_tokens(token_store.PROVIDER_FANVUE, {"access_token": "live"})
    verified(creator="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    body = (await client.get("/api/v1/fanvue/status")).json()

    assert body["ok"] is True
    assert body["creator_uuid_matches"] is True
    assert "FANVUE_PUBLISH_ENABLED" in body["next_step"]


async def test_disconnect_forgets_the_grant(client, fanvue_settings, monkeypatch, verified):
    token_store.save_tokens(token_store.PROVIDER_FANVUE, {"access_token": "live"})
    token_store.save_pending(token_store.PROVIDER_FANVUE, {"nonce": "n", "code_verifier": "v"})

    resp = await client.post("/api/v1/fanvue/disconnect")
    assert resp.status_code == 200
    # It says what it does not do. Fanvue has no documented revoke endpoint, and
    # implying the token died on their side would be a lie.
    assert "not revoke" in resp.json()["detail"]

    assert token_store.load_tokens(token_store.PROVIDER_FANVUE) == {}
    assert token_store.load_pending(token_store.PROVIDER_FANVUE) == {}
    assert (await client.get("/api/v1/fanvue/status")).json()["authorized"] is False


# ── reading the creator uuid out of a self response ──────────────────────


def test_a_flat_record_yields_its_uuid():
    assert _find_uuid({"uuid": "11111111-2222-3333-4444-555555555555", "username": "z"}) == (
        "11111111-2222-3333-4444-555555555555"
    )


def test_a_nested_record_yields_its_uuid():
    assert _find_uuid({"user": {"creatorUuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}}) == (
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    )


def test_an_unrecognised_shape_yields_nothing_rather_than_a_guess():
    """An empty answer costs one copy-paste. A wrong one builds every upload
    path against a different account."""
    assert _find_uuid({"data": {"handle": "zarax"}}) == ""
    assert _find_uuid({}) == ""
    assert _find_uuid("not-a-dict") == ""


def test_a_handle_under_an_id_key_is_not_mistaken_for_a_uuid():
    assert _find_uuid({"userId": "zara-official"}) == ""


def test_an_explicit_key_wins_over_a_generic_one():
    """`id` is not in the list at all, so a record carrying both cannot return
    the wrong resource's key."""
    found = _find_uuid(
        {"id": "some-other-resource", "creatorUuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
    )
    assert found == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


# ── a handshake in flight is not a grant ────────────────────────────────


async def test_a_started_handshake_does_not_read_as_configured(
    client, fanvue_settings, monkeypatch
):
    """The reason `pending` is a separate namespace rather than fields on the
    token record. Between connect and callback there is a verifier sitting in
    the store; `get_publisher()` must not be able to mistake any of it for
    authority, or a half-finished authorization would read as an armed account."""
    from app.providers.publish import PublishNotConfigured, get_publisher

    await _start(client)
    assert token_store.load_pending(token_store.PROVIDER_FANVUE)["code_verifier"]
    assert token_store.load_tokens(token_store.PROVIDER_FANVUE) == {}

    with pytest.raises(PublishNotConfigured):
        get_publisher()
