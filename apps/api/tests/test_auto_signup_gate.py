"""Persona Studio — automated platform signup is refused unless it is turned on.

`POST /social-accounts/{id}/auto-signup` and `/auto-signup-all` drive Playwright
against a live platform using anti-automation flags
(`--disable-blink-features=AutomationControlled`, `navigator.webdriver` deletion)
and a mail.tm disposable-inbox code bypass. When they work they create **real
accounts on real platforms**.

They were registered, reachable, and unguarded — one curl away — while several
user-facing strings in the app stated that no process here creates an account
(the manager roster's note, the signup packet's note, two module docstrings).
The button that called them was removed from the web app, but the endpoints
stayed. A removed button is not a control; this is: the routes now refuse before
they read an account or import the Playwright module, and
`AUTO_SIGNUP_ENABLED` defaults to False.

The refusal is the substance. Flipping the flag is an explicit act, and these
tests pin both sides of it so neither the guard nor the route underneath it can
rot silently.
"""

from __future__ import annotations

import sys
import types
import uuid

import pytest

from app.config import get_settings


class _Tripwire(RuntimeError):
    """Raised if anything reaches the real signup implementation."""


@pytest.fixture
def signup_tripwire(monkeypatch):
    """Replace the Playwright module so a live signup cannot start in a test.

    `browser_signup.auto_signup` would open a browser and submit a real signup
    form, so it must be impossible for a test to call it by accident. Installing
    this also proves the disabled routes never reach the import at all: with the
    guard in place the module object is never even looked up.
    """
    module = types.ModuleType("app.providers.browser_signup")

    async def auto_signup(*args, **kwargs):
        raise _Tripwire("automated signup implementation was reached")

    module.auto_signup = auto_signup
    monkeypatch.setitem(sys.modules, "app.providers.browser_signup", module)
    return module


def _enable(monkeypatch):
    monkeypatch.setattr(get_settings(), "AUTO_SIGNUP_ENABLED", True, raising=False)


# ── the default posture ──────────────────────────────────────────────

def test_the_flag_is_off_by_default():
    """A shipped .env that says nothing must not enable account creation."""
    assert get_settings().AUTO_SIGNUP_ENABLED is False


@pytest.mark.asyncio
async def test_single_account_signup_is_refused_by_default(client, signup_tripwire):
    resp = await client.post(f"/api/v1/social-accounts/{uuid.uuid4()}/auto-signup")
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert "AUTO_SIGNUP_ENABLED" in detail
    assert "signup-packet" in detail, "the refusal must name the supported path"


@pytest.mark.asyncio
async def test_signup_all_is_refused_by_default(client, signup_tripwire):
    resp = await client.post("/api/v1/social-accounts/auto-signup-all")
    assert resp.status_code == 403
    assert "AUTO_SIGNUP_ENABLED" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_the_refusal_happens_before_the_account_is_read(client, signup_tripwire):
    """Fail closed, not fail late.

    An unknown account id must still get the 403, not a 404. If the guard sat
    after the lookup, a request naming a real account would get one step closer
    to starting a browser before the refusal — and the refusal would depend on
    which id was sent.
    """
    resp = await client.post(f"/api/v1/social-accounts/{uuid.uuid4()}/auto-signup")
    assert resp.status_code == 403, "the guard must run before the account lookup"

    resp_all = await client.post("/api/v1/social-accounts/auto-signup-all")
    assert resp_all.status_code == 403, "the guard must run before the query"


# ── the route underneath is still intact ─────────────────────────────

@pytest.mark.asyncio
async def test_enabling_the_flag_lets_the_route_proceed(client, monkeypatch, signup_tripwire):
    """The guard must be the only thing blocking; not a broken route.

    With the flag on and an id that does not exist, the route gets past the
    guard and fails on the lookup — which is what proves the 403 above was the
    gate and not an import error or a dead endpoint. It stops there, so the
    tripwire is never reached and no account is touched.
    """
    _enable(monkeypatch)
    resp = await client.post(f"/api/v1/social-accounts/{uuid.uuid4()}/auto-signup")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Account not found"


@pytest.mark.asyncio
async def test_a_live_signup_still_cannot_start_in_a_test(client, monkeypatch, signup_tripwire):
    """The tripwire itself is load-bearing — prove it fires when reached.

    Without this, a future change that broke the wiring would make the tests
    above pass for the wrong reason.
    """
    import app.providers.browser_signup as module

    with pytest.raises(_Tripwire):
        await module.auto_signup(platform="instagram")
