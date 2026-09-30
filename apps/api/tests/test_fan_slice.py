"""The whole fan slice, end to end, in one place.

This is the test that answers "does the product actually work": a stranger
signs up, is handed a persona, talks to her, gets a balance, is refused content
they have not paid for, pays for it, gets it, is charged exactly once, and can
read back everything that happened.

Two things it deliberately checks that a happy-path test would skip:

  * that the **refusals** happen (402 before payment, 401 without a session),
    because an entitlement system that only ever says yes is not one;
  * that the money in the ledger **agrees with itself** at the end, via the same
    integrity report an operator would run.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.models import Entitlement, LedgerTransaction, Product, ProductMedia


class ScriptedLLM:
    """A provider that answers in a fixed voice, and can be switched off.

    Registered through the real registry, so the chat path under test is the
    production one — registry → chain → adapter — with only the network call
    replaced.
    """

    name = "scripted"

    def __init__(self, reply="hey! been thinking about you 💕"):
        self.reply = reply
        self.down = False
        self.calls = 0

    async def complete(self, system_prompt, user_prompt, schema=None,
                       temperature=0.7, max_tokens=2048):
        from app.providers.base import ProviderResult

        self.calls += 1
        if self.down:
            return ProviderResult(False, None, error="scripted provider is off",
                                  provider="scripted")
        return ProviderResult(True, {"content": self.reply, "model": "scripted"},
                              provider="scripted", latency_ms=1)

    async def health_check(self):
        from app.providers.base import ProviderResult
        return ProviderResult(not self.down, {"ok": not self.down}, provider="scripted")


@pytest_asyncio.fixture
async def persona(assigned_persona):
    yield assigned_persona


@pytest_asyncio.fixture
async def fan(client, persona):
    """A brand-new signed-in fan, with a session cookie set on `client`.

    A fresh account per test, not a shared one: the test database is
    session-scoped, so a fixed email would mean the second test to run gets a
    409 and inherits a balance the first one had already spent. The point of
    this fixture is that every test starts at a known 500.

    Signup also exercises the real thing — persona assignment, the signup
    grant, the cookie — rather than seeding a user row directly.
    """
    email = f"slice-{uuid.uuid4().hex[:12]}@example.com"
    response = await client.post("/api/v1/auth/signup", json={
        "email": email,
        "password": "correct-horse-battery",
        "display_name": "Slice Fan",
        "date_of_birth": "1996-04-02",
    })
    assert response.status_code in (200, 201), response.text
    return response.json()


@pytest_asyncio.fixture
async def product(db, persona):
    """A three-image product on the assigned persona, with real rows behind it."""
    existing = (await db.execute(
        select(Product).where(Product.persona_id == persona["id"],
                              Product.title == "Slice Test Set")
    )).scalar_one_or_none()
    if existing is not None:
        return existing

    row = Product(
        persona_id=persona["id"],
        title="Slice Test Set",
        description="three images",
        price_minor=300,
        kind="photoset",
        is_adult=False,
        status="published",
    )
    db.add(row)
    await db.flush()
    for index in range(3):
        db.add(ProductMedia(product_id=row.id, rel_path=f"slice/{index}.png",
                            position=index))
    await db.commit()
    return row


# ── identity and disclosure ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_fan_gets_a_named_persona_and_an_ai_disclosure(client, fan):
    response = await client.get("/api/v1/fan/persona")
    assert response.status_code == 200
    body = response.json()

    assert body["name"] == "Testpersona"
    assert body["bio"], "she needs a character sheet, not an empty string"
    # The disclosure is the product decision, so it is asserted, not assumed.
    assert body["disclosure_is_ai"] is True
    assert "AI" in body["disclosure"]
    assert "not a human being" in body["disclosure"]
    assert "simulated" in body["money_notice"].lower()


# ── chat ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_round_trip_persists_both_sides(client, fan, registry_override):
    llm = registry_override("llm", ScriptedLLM("hey you 🙂 what have you been up to"))

    sent = await client.post("/api/v1/fan/messages", json={"text": "hey! what's up"})
    assert sent.status_code == 200, sent.text
    body = sent.json()

    assert body["reply"] == "hey you 🙂 what have you been up to"
    assert body["is_ai_generated"] is True
    assert body["served_by"] == "scripted", "who answered must be recorded"
    assert llm.calls == 1

    thread = (await client.get("/api/v1/fan/thread")).json()["messages"]
    assert [m["direction"] for m in thread] == ["inbound", "outbound"]
    assert thread[0]["content"] == "hey! what's up"
    assert thread[1]["content"] == body["reply"]
    assert thread[1]["served_by"] == "scripted"


@pytest.mark.asyncio
async def test_chat_returns_503_and_writes_nothing_when_every_provider_is_down(
    client, fan, registry_override
):
    """The central honesty property of this slice.

    A fan who is told "she can't reply right now" has had a bad moment. A fan
    handed a placeholder line and told it was her has been lied to, and the
    transcript would look identical either way. So: 503, and no rows.
    """
    llm = registry_override("llm", ScriptedLLM())
    llm.down = True

    before = (await client.get("/api/v1/fan/thread")).json()["messages"]

    response = await client.post("/api/v1/fan/messages", json={"text": "you there?"})
    assert response.status_code == 503
    assert "can't reply" in response.json()["detail"]

    after = (await client.get("/api/v1/fan/thread")).json()["messages"]
    assert after == before == [], "a failed reply must not be recorded as one"

    # And a blank message is refused before any provider is bothered.
    assert (await client.post("/api/v1/fan/messages", json={"text": "   "})).status_code == 422


# ── money ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_signup_credit_lands_in_the_wallet(client, fan):
    body = (await client.get("/api/v1/fan/wallet")).json()
    assert body["balance_minor"] == fan["signup_credit_minor"] == 500
    assert body["simulated"] is True
    assert body["notice"], "simulated money must say so on the wallet itself"
    assert any(row["amount_minor"] == 500 for row in body["statement"])


@pytest.mark.asyncio
async def test_topup_adds_funds_and_is_listed_on_the_statement(client, fan):
    response = await client.post("/api/v1/fan/wallet/topup", json={"amount_minor": 1000})
    assert response.status_code == 200, response.text
    assert response.json()["balance_minor"] == 1500

    rows = (await client.get("/api/v1/fan/wallet")).json()["statement"]
    assert {row["kind"] for row in rows} >= {"topup", "signup_grant"}


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [0, -500, 100_001])
async def test_topup_refuses_nonsense_amounts(client, fan, amount):
    response = await client.post("/api/v1/fan/wallet/topup", json={"amount_minor": amount})
    assert response.status_code in (400, 402, 422)
    assert (await client.get("/api/v1/fan/wallet")).json()["balance_minor"] == 500


# ── content, locked and unlocked ──────────────────────────────────────

@pytest.mark.asyncio
async def test_locked_content_says_it_is_locked(client, fan, product):
    listed = (await client.get("/api/v1/fan/products")).json()["products"]
    row = next(p for p in listed if p["id"] == str(product.id))

    assert row["unlocked"] is False
    assert row["price_minor"] == 300
    assert row["media_count"] == 3
    assert row["reason"], "the UI needs a reason to render, not just a boolean"

    assert (await client.get(f"/api/v1/fan/content/{product.id}")).status_code == 402
    assert (await client.get(
        f"/api/v1/fan/content/{product.id}/media/0"
    )).status_code == 402


@pytest.mark.asyncio
async def test_unlock_charges_once_even_when_clicked_twice(client, fan, product):
    first = await client.post(f"/api/v1/fan/products/{product.id}/unlock")
    assert first.status_code == 200, first.text
    assert first.json()["balance_minor"] == 200, "500 - 300"

    second = await client.post(f"/api/v1/fan/products/{product.id}/unlock")
    assert second.status_code == 200
    assert second.json()["already_owned"] is True
    assert second.json()["balance_minor"] == 200, "the second click must be free"

    wallet = (await client.get("/api/v1/fan/wallet")).json()
    unlock_rows = [row for row in wallet["statement"] if row["kind"] == "ppv_unlock"]
    assert len(unlock_rows) == 1, "one purchase, one ledger line"


@pytest.mark.asyncio
async def test_unlock_is_refused_when_the_balance_is_short(client, fan, db, persona):
    expensive = Product(
        persona_id=persona["id"], title="Too Expensive", price_minor=999_00,
        kind="bundle", status="published",
    )
    db.add(expensive)
    await db.commit()

    response = await client.post(f"/api/v1/fan/products/{expensive.id}/unlock")
    assert response.status_code == 402
    assert (await client.get("/api/v1/fan/wallet")).json()["balance_minor"] == 500


@pytest.mark.asyncio
async def test_media_is_served_after_unlock_and_only_with_a_session(client, fan, product):
    await client.post(f"/api/v1/fan/products/{product.id}/unlock")

    content = await client.get(f"/api/v1/fan/content/{product.id}")
    assert content.status_code == 200
    assert len(content.json()["media"]) == 3
    assert content.json()["disclosure"]

    # The file itself is missing from this test's storage, which is a 404 — not
    # a 402 and not a 200. What is being asserted is the *entitlement* check
    # passed, since it runs before the file is ever opened.
    head = await client.get(f"/api/v1/fan/content/{product.id}/media/0")
    assert head.status_code in (404, 200)

    # And the same URL is a 401 for anyone without a session.
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get(
            f"/api/v1/fan/content/{product.id}/media/0"
        )).status_code == 401


@pytest.mark.asyncio
async def test_subscription_unlocks_the_high_tier_product(client, fan, db, persona):
    from app.models import SubscriptionPlan

    plan = (await db.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.code == "vip")
    )).scalar_one_or_none()
    if plan is None:
        plan = SubscriptionPlan(code="vip", name="VIP", price_minor=500, rank=2)
        db.add(plan)
        await db.flush()

    premium = Product(
        persona_id=persona["id"], title="VIP Only", price_minor=50_000,
        kind="bundle", min_tier_rank=2, status="published",
    )
    db.add(premium)
    await db.commit()

    # Locked while unsubscribed, despite the 500 credit.
    listed = (await client.get("/api/v1/fan/products")).json()["products"]
    assert next(p for p in listed if p["id"] == str(premium.id))["unlocked"] is False

    sub = await client.post("/api/v1/fan/subscription", json={"plan_code": "vip"})
    assert sub.status_code == 200, sub.text
    assert sub.json()["balance_minor"] == 0, "500 - 500"

    listed = (await client.get("/api/v1/fan/products")).json()["products"]
    row = next(p for p in listed if p["id"] == str(premium.id))
    assert row["unlocked"] is True
    assert (await client.get(f"/api/v1/fan/content/{premium.id}")).status_code == 200


@pytest.mark.asyncio
async def test_plans_are_listed_with_prices(client, fan):
    plans = (await client.get("/api/v1/fan/subscription/plans")).json()
    assert plans["simulated"] is True
    assert plans["plans"], "an empty plan list makes the subscribe call unreachable"
    for plan in plans["plans"]:
        assert plan["price_minor"] > 0 and plan["price_display"]


# ── the record ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_activity_is_a_real_record_of_what_happened(client, fan, product, registry_override):
    registry_override("llm", ScriptedLLM("hi again"))
    await client.post("/api/v1/fan/messages", json={"text": "hello"})
    await client.post(f"/api/v1/fan/wallet/topup", json={"amount_minor": 500})

    # Try the locked content first, so the refusal is on the record too. Without
    # this the test would only ever log the happy path and the assertion below
    # would pass by never running.
    denied = await client.get(f"/api/v1/fan/content/{product.id}")
    assert denied.status_code == 402

    await client.post(f"/api/v1/fan/products/{product.id}/unlock")

    activity = (await client.get("/api/v1/fan/activity")).json()
    actions = {event["action"] for event in activity["events"]}
    assert {"signup", "age_verified", "fan_message", "topup", "ppv_unlock"} <= actions

    # The denied access attempt is on the record too, not just the successes.
    assert any(not row["allowed"] for row in activity["accesses"]), (
        "a refusal that is not logged is a refusal nobody can audit"
    )


@pytest.mark.asyncio
async def test_the_ledger_balances_after_the_whole_slice(client, fan, product, db):
    await client.post("/api/v1/fan/wallet/topup", json={"amount_minor": 1000})
    await client.post(f"/api/v1/fan/products/{product.id}/unlock")
    await client.post(f"/api/v1/fan/products/{product.id}/unlock")  # the double click

    response = await client.get("/api/v1/system/ledger/integrity")
    assert response.status_code == 200
    report = response.json()
    assert report["balanced"] is True, report
    assert report["unbalanced_transactions"] == []
    assert report["drifted_wallets"] == []

    # Every transaction in the database balances, checked directly rather than
    # only through the endpoint that reports on itself.
    from app.models import LedgerEntry

    signed = func.sum(
        __import__("sqlalchemy").case(
            (LedgerEntry.direction == "debit", LedgerEntry.amount_minor),
            else_=-LedgerEntry.amount_minor,
        )
    )
    unbalanced = (await db.execute(
        select(LedgerEntry.transaction_id)
        .group_by(LedgerEntry.transaction_id)
        .having(signed != 0)
    )).all()
    assert unbalanced == []


@pytest.mark.asyncio
async def test_entitlements_are_unique_per_product(client, fan, product, db):
    await client.post(f"/api/v1/fan/products/{product.id}/unlock")
    await client.post(f"/api/v1/fan/products/{product.id}/unlock")

    # Scoped to this test's user: `product` is shared across the file, so an
    # unscoped count would be every test's unlocks added together.
    count = (await db.execute(
        select(func.count()).select_from(Entitlement).where(
            Entitlement.product_id == str(product.id),
            Entitlement.user_id == fan["user"]["id"],
        )
    )).scalar()
    assert count == 1, "one product, one entitlement, however many clicks"

    transactions = (await db.execute(
        select(func.count()).select_from(LedgerTransaction).where(
            LedgerTransaction.idempotency_key
            == f"ppv:{fan['user']['id']}:{product.id}"
        )
    )).scalar()
    assert transactions == 1, "and one ledger posting behind it"


@pytest.mark.asyncio
async def test_no_route_serves_content_to_an_anonymous_visitor(client):
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        # /fan/subscription/plans is deliberately absent: it is a public price
        # list, because a visitor should be able to see what a subscription
        # costs before handing over an email address.
        for path in ("/api/v1/fan/persona", "/api/v1/fan/thread", "/api/v1/fan/wallet",
                     "/api/v1/fan/products", "/api/v1/fan/activity",
                     "/api/v1/fan/content/x"):
            assert (await anon.get(path)).status_code == 401, path
        assert (await anon.post("/api/v1/fan/messages",
                                json={"text": "hi"})).status_code == 401
