"""Auth — password hashing, the 18+ gate, and session lifecycle.

Two of these are the security-relevant ones and are easy to get subtly wrong:

  * **Under-18 refusal.** The check must read the date of birth, not trust a
    client flag, and it must refuse before creating any row.
  * **Login failure is one message.** "No such email" and "wrong password" must
    be indistinguishable, or the endpoint becomes an account-enumeration
    oracle — a real leak, given the email column is what identifies a fan.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.auth import (
    SESSION_COOKIE, hash_password, issue_session, resolve_session,
    revoke_session, verify_password,
)
from app.models import AppUser, AuthSession, utcnow

GOOD_DOB = "1996-04-02"
UNDER_18_DOB = "2015-01-01"


@pytest_asyncio.fixture(autouse=True)
async def _persona(assigned_persona):
    """Every test in this module needs a persona to be assignable."""
    yield assigned_persona


def _signup(email="fan@example.com", password="correct-horse", dob=GOOD_DOB):
    return {"email": email, "password": password, "display_name": "Fan", "date_of_birth": dob}


# ── password hashing ──────────────────────────────────────────────────

def test_password_round_trip():
    encoded = hash_password("correct-horse")
    assert verify_password("correct-horse", encoded) is True
    assert verify_password("wrong-horse", encoded) is False


def test_hash_is_salted_per_call():
    a, b = hash_password("same"), hash_password("same")
    assert a != b, "identical passwords must not produce identical hashes"
    assert verify_password("same", a) and verify_password("same", b)


def test_verify_never_raises_on_a_malformed_hash():
    """A corrupt stored hash denies the login; it must not 500 the endpoint."""
    for junk in ("", "not-a-hash", "scrypt$bad", "bcrypt$1$2$3$4$5", None):
        assert verify_password("anything", junk) is False


def test_stored_hash_does_not_contain_the_password():
    assert "correct-horse" not in hash_password("correct-horse")


# ── signup ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_signup_succeeds_and_issues_a_session(client):
    response = await client.post("/api/v1/auth/signup", json=_signup())
    assert response.status_code in (200, 201), response.text

    body = response.json()
    assert body["user"]["email"] == "fan@example.com"
    assert body["persona"]["name"], "a fan must be assigned a persona by name"
    assert body["simulated"] is True
    assert SESSION_COOKIE in response.cookies

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "fan@example.com"


@pytest.mark.asyncio
async def test_under_18_is_refused(client):
    response = await client.post(
        "/api/v1/auth/signup", json=_signup(email="kid@example.com", dob=UNDER_18_DOB)
    )
    assert response.status_code == 403
    # A sentence, not a status code with a stack trace behind it.
    assert "18" in response.json()["detail"]


@pytest.mark.asyncio
async def test_refused_signup_creates_no_user(client, db):
    await client.post(
        "/api/v1/auth/signup", json=_signup(email="kid2@example.com", dob=UNDER_18_DOB)
    )
    found = (await db.execute(
        select(AppUser).where(AppUser.email == "kid2@example.com")
    )).scalar_one_or_none()
    assert found is None, "the refusal must happen before anything is written"


@pytest.mark.asyncio
async def test_duplicate_email_is_a_conflict(client):
    first = await client.post("/api/v1/auth/signup", json=_signup(email="dupe@example.com"))
    assert first.status_code in (200, 201)
    second = await client.post("/api/v1/auth/signup", json=_signup(email="dupe@example.com"))
    assert second.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,why", [
    ({"email": "not-an-email", "password": "correct-horse", "date_of_birth": GOOD_DOB}, "email"),
    ({"email": "ok@example.com", "password": "short", "date_of_birth": GOOD_DOB}, "password"),
    ({"email": "ok@example.com", "password": "correct-horse", "date_of_birth": "not-a-date"}, "dob"),
    ({"email": "ok@example.com", "password": "correct-horse", "date_of_birth": "1850-01-01"}, "ancient"),
])
async def test_bad_signup_input_is_a_422(client, payload, why):
    response = await client.post("/api/v1/auth/signup", json=payload)
    assert response.status_code == 422, f"{why}: {response.text}"


# ── login ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_and_logout(client):
    await client.post("/api/v1/auth/signup", json=_signup(email="login@example.com"))

    out = await client.post("/api/v1/auth/logout")
    assert out.status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 401, "logout must revoke"

    back = await client.post("/api/v1/auth/login", json={
        "email": "login@example.com", "password": "correct-horse",
    })
    assert back.status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 200


@pytest.mark.asyncio
async def test_login_failure_does_not_reveal_whether_the_account_exists(client):
    await client.post("/api/v1/auth/signup", json=_signup(email="real@example.com"))

    wrong_password = await client.post("/api/v1/auth/login", json={
        "email": "real@example.com", "password": "wrong-password",
    })
    no_such_account = await client.post("/api/v1/auth/login", json={
        "email": "nobody@example.com", "password": "wrong-password",
    })

    assert wrong_password.status_code == no_such_account.status_code == 401
    assert wrong_password.json() == no_such_account.json(), (
        "differing messages here enumerate accounts"
    )


# ── session lifecycle ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_anonymous_access_to_fan_routes_is_401(client):
    for path in ("/api/v1/fan/persona", "/api/v1/fan/wallet", "/api/v1/fan/products",
                 "/api/v1/fan/thread", "/api/v1/fan/activity"):
        assert (await client.get(path)).status_code == 401, path


@pytest.mark.asyncio
async def test_the_raw_token_is_never_stored(db):
    user = AppUser(email="token@example.com", password_hash=hash_password("x" * 12))
    db.add(user)
    await db.flush()

    token = await issue_session(db, user)
    await db.commit()

    rows = (await db.execute(
        select(AuthSession).where(AuthSession.user_id == user.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].token_hash != token
    assert token not in rows[0].token_hash

    assert await resolve_session(db, token) is not None
    assert await resolve_session(db, "not-a-real-token") is None
    assert await resolve_session(db, None) is None


@pytest.mark.asyncio
async def test_revoked_session_stops_resolving(db):
    user = AppUser(email="revoke@example.com", password_hash=hash_password("x" * 12))
    db.add(user)
    await db.flush()
    token = await issue_session(db, user)
    await db.commit()

    assert await revoke_session(db, token) is True
    await db.commit()

    assert await resolve_session(db, token) is None
    assert await revoke_session(db, token) is False, "revoking twice is not an error"


@pytest.mark.asyncio
async def test_expired_session_stops_resolving(db):
    user = AppUser(email="expire@example.com", password_hash=hash_password("x" * 12))
    db.add(user)
    await db.flush()
    token = await issue_session(db, user)
    await db.commit()

    row = (await db.execute(
        select(AuthSession).where(AuthSession.user_id == user.id)
    )).scalar_one()
    row.expires_at = utcnow() - timedelta(seconds=1)
    await db.commit()

    assert await resolve_session(db, token) is None
