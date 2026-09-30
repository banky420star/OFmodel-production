"""The only real-money number this app can produce, and the traps around it.

Everything else that looks like revenue here is simulated: `get_processor()`
resolves to `fake` and hard-errors for anything else, `POST /fan/wallet/topup`
says "no card is collected and no real charge happens", and the dashboard's
`revenue` field is read from an `AnalyticsSnapshot` row that empty in a fresh
database. So a Fanvue sale is the one real dollar observable from this codebase
— and it is observable *only* through the endpoints pinned here, because
nothing else reads it back.

Three things are worth being pedantic about, and each has a test below:

  * a missing figure is `None`, never `0`. "We earned nothing" and "Fanvue did
    not tell us" are different answers and only one is good news;
  * a 401 must not render as zeros either — an expired token is not a report
    that the account earned nothing;
  * `by_source.messages` is carried through, because paid DMs are the revenue
    engine on this platform and a subscription-only reading badly understates it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from app.providers.publish import PublishFailed
from app.providers.publish.fanvue import (
    FanvuePublisher,
    earnings_totals,
    _iso,
)

CREATOR = "aaaa1111-bbbb-2222-cccc-333344445555"

# The platform's own unit: **USD cents**, per the spec's wording on every money
# field. So this fixture is what Fanvue sends, and the assertions below are what
# the app must show — dollars. Writing the fixture in dollars instead is how the
# 100× bug lived: the test and the code shared one wrong assumption and agreed
# with each other perfectly.
SUMMARY = {
    "totals": {
        "allTime": {"gross": 123450.0, "net": 98760.0},
        "thisMonth": {
            "gross": 20000.0, "net": 16000.0,
            "previousMonthGross": 15000.0, "previousMonthNet": 12000.0,
        },
    },
    "breakdownBySource": {
        "subs": {"gross": 6000.0, "net": 4800.0},
        "messages": {"gross": 90000.0, "net": 72000.0},
        "posts": {"gross": 27450.0, "net": 21960.0},
        "tips": {"gross": 0.0, "net": 0.0},
    },
    "period": {"startDate": "2026-09-01", "endDate": "2026-09-30"},
}


# ── the unit ─────────────────────────────────────────────────────────

def test_a_cent_is_not_a_dollar():
    """The one conversion, at the smallest size that shows it.

    Read as dollars, $1.00 of earnings arrives as 100.0 — a hundredfold
    overstatement that renders as a very good month. Nothing else in this file
    would catch it, because a fixture in dollars is self-consistent.
    """
    flat = earnings_totals({"totals": {"allTime": {"gross": 100.0, "net": 250.0}}})
    assert flat["all_time"] == {"gross": 1.0, "net": 2.5}


def test_the_currency_is_stated_because_the_platform_documents_it():
    """The summary response carries no currency field, and the unit is USD per
    the spec — so the reading says USD rather than leaving the web app to prefix
    the Rand symbol it uses everywhere else."""
    from app.providers.publish.fanvue import CURRENCY

    assert CURRENCY == "USD"
    assert earnings_totals(SUMMARY)["currency"] == "USD"


# ── flattening ───────────────────────────────────────────────────────

def test_totals_are_flattened_from_fanvues_nesting():
    totals = earnings_totals(SUMMARY)

    assert totals["all_time"] == {"gross": 1234.5, "net": 987.6}
    assert totals["this_month"] == {"gross": 200.0, "net": 160.0}
    assert totals["previous_month"] == {"gross": 150.0, "net": 120.0}


def test_the_paid_message_breakdown_survives():
    """Paid DMs are the revenue engine; dropping this field hides the money."""
    totals = earnings_totals(SUMMARY)

    assert totals["by_source"]["messages"] == {"gross": 900.0, "net": 720.0}
    assert totals["by_source"]["messages"]["net"] > totals["by_source"]["subs"]["net"], (
        "the fixture is meant to be message-led, matching how this platform earns"
    )


def test_a_genuine_zero_is_kept_as_zero():
    """Tips at 0.0 means nobody tipped. That is information, not absence."""
    assert earnings_totals(SUMMARY)["by_source"]["tips"] == {"gross": 0.0, "net": 0.0}


def test_a_source_fanvue_omits_is_none_not_zero():
    """`referrals` is absent from the fixture — reporting 0.0 would invent it."""
    totals = earnings_totals(SUMMARY)

    assert totals["by_source"]["referrals"] == {"gross": None, "net": None}
    assert totals["by_source"]["renewals"] == {"gross": None, "net": None}


def test_an_empty_summary_does_not_invent_zero_earnings():
    """A shape change upstream must not read as "you earned nothing"."""
    totals = earnings_totals({})

    assert totals["all_time"] == {"gross": None, "net": None}
    assert totals["this_month"] == {"gross": None, "net": None}
    assert all(v["gross"] is None for v in totals["by_source"].values())


def test_a_non_numeric_figure_is_None_rather_than_a_crash():
    totals = earnings_totals({"totals": {"allTime": {"gross": "n/a", "net": 500.0}}})

    assert totals["all_time"] == {"gross": None, "net": 5.0}


# ── date handling ────────────────────────────────────────────────────

def test_a_naive_datetime_is_sent_as_utc():
    """Fanvue resolves a naive datetime against *its* clock, moving a day edge."""
    assert _iso(datetime(2026, 9, 1)) == "2026-09-01T00:00:00+00:00"


def test_an_aware_datetime_keeps_its_offset():
    from datetime import timedelta

    ist = timezone(timedelta(hours=5, minutes=30))
    assert _iso(datetime(2026, 9, 1, tzinfo=ist)) == "2026-09-01T00:00:00+05:30"


def test_a_string_passes_through_untouched():
    assert _iso("2026-09-01T00:00:00Z") == "2026-09-01T00:00:00Z"


# ── the request itself ───────────────────────────────────────────────

def _publisher(handlers) -> FanvuePublisher:
    def handler(request: httpx.Request) -> httpx.Response:
        fn = handlers.get((request.method, request.url.path))
        if fn is None:
            return httpx.Response(404, json={"error": f"unrouted {request.url.path}"})
        return fn(request)

    return FanvuePublisher(
        client_id="client-abc",
        access_token="token-abc",
        creator_uuid=CREATOR,
        api_base="https://api.fanvue.test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_the_summary_reads_the_token_scoped_endpoint_with_the_window():
    seen = {}

    def summary(request):
        seen["path"] = request.url.path
        seen["query"] = dict(request.url.params)
        seen["version"] = request.headers.get("x-fanvue-api-version")
        return httpx.Response(200, json=SUMMARY)

    publisher = _publisher({("GET", "/v1/insights/earnings/summary"): summary})

    body = await publisher.earnings_summary(start="2026-09-01T00:00:00Z")

    # The payload as Fanvue sent it — still cents. `earnings_summary` is the
    # transport, and the one conversion lives on the flattening boundary
    # (`earnings_totals`), so this value is deliberately *not* 987.6 here.
    assert body["totals"]["allTime"]["net"] == 98760.0
    assert seen["path"] == "/v1/insights/earnings/summary"
    assert seen["query"]["startDate"] == "2026-09-01T00:00:00Z"
    assert "endDate" not in seen["query"], "an unset window must be omitted, not sent empty"
    assert seen["version"], "every Fanvue call needs the version header"


async def test_a_window_omits_the_parameter_rather_than_sending_an_empty_date():
    seen = {}

    publisher = _publisher({
        ("GET", "/v1/insights/earnings/summary"): lambda r: (
            seen.update(dict(r.url.params)) or httpx.Response(200, json={})
        ),
    })

    await publisher.earnings_summary()

    assert "startDate" not in seen and "endDate" not in seen


async def test_a_refused_summary_raises_rather_than_returning_an_empty_one():
    """An error must not be shaped like "no earnings"."""
    publisher = _publisher({
        ("GET", "/v1/insights/earnings/summary"): lambda r: httpx.Response(403, json={"error": "no"}),
    })

    with pytest.raises(PublishFailed) as exc:
        await publisher.earnings_summary()

    assert "403" in str(exc.value)


async def test_the_transaction_list_reads_its_own_endpoint():
    seen = {}

    def rows(request):
        seen["query"] = dict(request.url.params)
        return httpx.Response(200, json={"data": [{"source": "message", "gross": 9.0, "net": 7.2}]})

    publisher = _publisher({("GET", "/v1/insights/earnings"): rows})

    body = await publisher.earnings(source="message")

    assert body["data"][0]["source"] == "message"
    assert seen["query"]["source"] == "message", "a str source must be wrapped, not iterated"


# ── the transaction rows ─────────────────────────────────────────────

ROWS = {
    "data": [
        {  # a paid message: what the fan paid is bigger than what was kept
            "date": "2026-09-04T10:00:00Z",
            "source": "message",
            "gross": 1000.0,
            "net": 800.0,
            "total": 1120.0,
            "currency": "BRL",
            "transactionOrderId": "ord-1",
            "transactionOrderStatus": "pendingBalance",
        },
        {  # a creator reward — nobody bought anything
            "date": "2026-09-05T10:00:00Z",
            "source": "referral",
            "gross": 500.0,
            "net": 500.0,
            "total": 500.0,
            "transactionOrderStatus": "availableForPayout",
        },
        {  # a refund: negative, and it reverses an order
            "date": "2026-09-06T10:00:00Z",
            "source": "refund",
            "gross": -1000.0,
            "net": -1000.0,
            "total": -1120.0,
            "reversedTransactionOrderId": "ord-1",
        },
    ]
}


def test_the_rows_are_dollars_and_keep_the_three_amounts_apart():
    """`total`, `gross` and `net` answer three different questions — what the fan
    paid, the creator's price, what the creator kept — and all three arrive in
    cents. Collapsing them, or reading any of them as dollars, is the 100× bug
    wearing a different field name."""
    from app.providers.publish.fanvue import earnings_rows

    paid, reward, refund = earnings_rows(ROWS)
    assert (paid["fan_paid"], paid["gross"], paid["net"]) == (11.2, 10.0, 8.0)
    assert (reward["fan_paid"], reward["net"]) == (5.0, 5.0)
    assert (refund["fan_paid"], refund["net"]) == (-11.2, -10.0)
    assert paid["status"] == "pendingBalance"
    assert refund["reverses"] == "ord-1"


def test_a_creator_reward_is_marked_as_not_fan_money():
    """Referral, affiliate and giveaway rows are rewards Fanvue pays the creator.
    Summed into revenue they inflate it with money no fan spent — and on this
    platform they are the rows most likely to be large early, when a subscription
    count is zero."""
    from app.providers.publish.fanvue import earnings_rows

    paid, reward, refund = earnings_rows(ROWS)
    assert paid["source_is_fan_money"] is True
    assert reward["source_is_fan_money"] is False
    # A refund is fan money — a returning of it, and negative. Excluding it
    # would report a refund month as a good one.
    assert refund["source_is_fan_money"] is True


def test_a_row_never_carries_the_fans_local_currency():
    """Fanvue puts the fan's own currency on the row and calls it informational
    only, while the amounts beside it are already USD. Carrying it as the row's
    unit is how a dollar figure comes to be shown as a Brazilian sale."""
    from app.providers.publish.fanvue import CURRENCY, earnings_rows

    paid = earnings_rows(ROWS)[0]
    assert "currency" not in paid
    assert CURRENCY == "USD"


def test_a_row_with_no_amounts_is_still_a_row():
    """A transaction that happened but was reported without a size is not the
    same as no transaction. Dropping it would understate the count; zeroing it
    would invent a free sale."""
    from app.providers.publish.fanvue import earnings_rows

    rows = earnings_rows({"data": [{"source": "message", "date": "2026-09-07T00:00:00Z"}]})
    assert len(rows) == 1
    assert rows[0]["fan_paid"] is None
    assert rows[0]["net"] is None


def test_an_empty_or_misshapen_page_is_no_rows_not_a_crash():
    from app.providers.publish.fanvue import earnings_rows

    assert earnings_rows({}) == []
    assert earnings_rows({"data": None}) == []
    assert earnings_rows({"data": []}) == []
    assert earnings_rows({"data": ["not-a-row", None]}) == []


# ── the route ────────────────────────────────────────────────────────

@pytest.fixture
def route():
    from app.routes.publish import publish_earnings

    return publish_earnings


@pytest.fixture
def no_publisher(monkeypatch):
    import app.providers.publish as publish_pkg

    def raiser():
        raise publish_pkg.PublishNotConfigured("FANVUE_CLIENT_ID is empty.")

    monkeypatch.setattr(publish_pkg, "get_publisher", raiser)


async def test_unconfigured_says_so_and_claims_no_totals(route, no_publisher):
    result = await route(days=30)

    assert result["state"] == "not_configured"
    assert result["totals"] is None, "no publisher means no number — not zeros"
    assert "no real revenue is observable" in result["note"]


async def test_configured_returns_flattened_totals(route, monkeypatch):
    import app.providers.publish as publish_pkg

    publisher = _publisher({
        ("GET", "/v1/insights/earnings/summary"): lambda r: httpx.Response(200, json=SUMMARY),
    })
    monkeypatch.setattr(publish_pkg, "get_publisher", lambda: publisher)

    result = await route(days=30)

    assert result["state"] == "ok"
    assert result["totals"]["all_time"]["net"] == 987.6
    assert "real money" in result["note"]


async def test_an_erroring_platform_is_not_rendered_as_zero_earnings(route, monkeypatch):
    """The trap: a 401 token expiry must never look like "you earned nothing"."""
    import app.providers.publish as publish_pkg

    publisher = _publisher({
        ("GET", "/v1/insights/earnings/summary"): lambda r: httpx.Response(401, json={}),
    })
    monkeypatch.setattr(publish_pkg, "get_publisher", lambda: publisher)

    result = await route(days=30)

    assert result["state"] == "error"
    assert result["totals"] is None
    assert "401" in result["detail"]


async def test_a_publisher_without_the_endpoint_reports_unsupported(route, monkeypatch):
    """If a second platform is ever wired, it may have no earnings at all."""
    import app.providers.publish as publish_pkg

    class Bare:
        name = "bare"

    monkeypatch.setattr(publish_pkg, "get_publisher", lambda: Bare())

    result = await route(days=30)

    assert result["state"] == "unsupported"
    assert result["totals"] is None


async def test_the_window_reaches_the_platform_as_a_real_start_date(route, monkeypatch):
    import app.providers.publish as publish_pkg

    seen = {}

    def summary(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=SUMMARY)

    publisher = _publisher({("GET", "/v1/insights/earnings/summary"): summary})
    monkeypatch.setattr(publish_pkg, "get_publisher", lambda: publisher)

    await route(days=7)

    assert "startDate" in seen
    start = datetime.fromisoformat(seen["startDate"])
    assert start.tzinfo is not None, "an offset-less date is read on Fanvue's clock"
    assert (datetime.now(timezone.utc) - start).days == 7
