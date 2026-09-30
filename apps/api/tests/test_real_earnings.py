"""Does anything in the app tell the truth about whether money has arrived?

Measured 2026-09-30: `GET /dashboard/summary` returned `"revenue": 0` for a
studio whose real revenue was **unknown**. That number is
`sum(AnalyticsSnapshot.revenue)` — and nothing that writes it can report money:

  * `billing/processor.py::get_processor()` resolves to `fake` and raises for any
    other value, so the `/fan` ledger cannot be evidence a person paid;
  * the Instagram sync hardcodes `revenue=0` and so does the TikTok one, because
    neither API reports revenue at all.

The only real ledger is the connected platform's own, read by
`FanvuePublisher.earnings_summary`. These tests pin the two rules that make the
dashboard honest: a recorded figure is labelled as recorded, and an unreadable
real figure is `None` — never `0`.
"""

from __future__ import annotations

import pytest

from app import earnings as earnings_module
from app.config import get_settings
from app.earnings import headline, real_earnings
from app.providers.publish import (
    PublishDisabled,
    PublishFailed,
    PublishNotConfigured,
)

# Fanvue reports every amount in **USD cents**, so this fixture is written in the
# platform's own unit: each figure is 100× the dollar amount the assertions below
# expect. That is deliberate — the shape the code reads and the unit it reads it
# in are the two things that can be wrong here, and a fixture written in dollars
# cannot tell you which one broke. `test_cents_are_not_dollars` pins the
# conversion on its own.
SUMMARY = {
    "totals": {
        "allTime": {"gross": 125000.0, "net": 100000.0},
        "thisMonth": {
            "gross": 18000.0,
            "net": 14400.0,
            "previousMonthGross": 9000.0,
            "previousMonthNet": 7200.0,
        },
    },
    "breakdownBySource": {
        "messages": {"gross": 90000.0, "net": 72000.0},
        "subs": {"gross": 35000.0, "net": 28000.0},
    },
    "overTime": [
        {"periodStart": "2026-09-01T00:00:00Z", "gross": 6000.0, "net": 4800.0},
        {"periodStart": "2026-09-02T00:00:00Z", "gross": 3500.0, "net": 2800.0},
    ],
    "period": {"startDate": "2026-09-01", "endDate": "2026-09-30", "granularity": "day"},
}


class _FakePublisher:
    """A configured publisher whose books we control."""

    name = "fanvue"

    def __init__(self, *, summary=SUMMARY, raises=None, accepts_timeout=True):
        self._summary = summary
        self._raises = raises
        self._accepts_timeout = accepts_timeout
        self.calls: list[dict] = []

    async def earnings_summary(self, **kwargs):
        if not self._accepts_timeout:
            kwargs.pop("timeout", None)
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._summary


def _publisher(monkeypatch, publisher):
    monkeypatch.setattr("app.providers.publish.get_publisher", lambda: publisher)
    return publisher


def _raising(monkeypatch, exc):
    def _get():
        raise exc

    monkeypatch.setattr("app.providers.publish.get_publisher", _get)


# ── the state machine ────────────────────────────────────────────────────


async def test_no_credentials_reads_as_not_configured(monkeypatch):
    _raising(monkeypatch, PublishNotConfigured("FANVUE_CLIENT_ID is empty"))
    result = await real_earnings()
    assert result["state"] == "not_configured"
    assert result["totals"] is None
    assert "simulated" in result["note"]


async def test_credentials_without_the_arm_switch_read_as_disabled(monkeypatch):
    """A different next action from `not_configured`, so a different state."""
    _raising(monkeypatch, PublishDisabled("FANVUE_PUBLISH_ENABLED is False"))
    result = await real_earnings()
    assert result["state"] == "disabled"
    assert result["totals"] is None


async def test_a_platform_with_no_ledger_reads_as_unsupported(monkeypatch):
    class _Silent:
        name = "somewhere"

    _publisher(monkeypatch, _Silent())
    result = await real_earnings()
    assert result["state"] == "unsupported"
    assert result["provider"] == "somewhere"
    assert result["totals"] is None


async def test_a_failed_read_is_an_error_and_never_zero(monkeypatch):
    """A 401 from an expired token is a token problem. Rendered as zeros it
    reads as a month with no sales, which is a different and wrong answer."""
    _publisher(monkeypatch, _FakePublisher(raises=PublishFailed("HTTP 401 from Fanvue")))
    result = await real_earnings()
    assert result["state"] == "error"
    assert "401" in result["detail"]
    assert result["totals"] is None


async def test_a_readable_ledger_returns_the_platforms_own_numbers(monkeypatch):
    publisher = _publisher(monkeypatch, _FakePublisher())
    result = await real_earnings()
    assert result["state"] == "ok"
    assert result["provider"] == "fanvue"
    assert result["totals"]["all_time"] == {"gross": 1250.0, "net": 1000.0}
    assert result["totals"]["by_source"]["messages"]["net"] == 720.0
    # The window is the caller's, not the adapter's default.
    assert "start" in publisher.calls[0]


async def test_the_timeout_is_passed_through_when_given(monkeypatch):
    publisher = _publisher(monkeypatch, _FakePublisher())
    await real_earnings(days=7, timeout=6.0)
    assert publisher.calls[0]["timeout"] == 6.0


async def test_an_adapter_without_a_timeout_parameter_still_reads(monkeypatch):
    """`TypeError` from an older adapter signature must not lose the reading —
    losing it would turn a working ledger into "unknown"."""
    publisher = _publisher(monkeypatch, _FakePublisher(accepts_timeout=False))
    result = await real_earnings(timeout=6.0)
    assert result["state"] == "ok"
    assert "timeout" not in publisher.calls[0]


async def test_a_failed_read_after_a_timeout_fallback_is_still_an_error(monkeypatch):
    _publisher(
        monkeypatch,
        _FakePublisher(raises=PublishFailed("HTTP 500"), accepts_timeout=False),
    )
    result = await real_earnings(timeout=6.0)
    assert result["state"] == "error"
    assert result["totals"] is None


# ── the headline figures ─────────────────────────────────────────────────


def test_the_headline_reads_the_totals_it_was_given():
    """`headline` takes the *flattened* totals `earnings_totals` produces — the
    same object `real_earnings` hands it — so this checks the two compose rather
    than each on its own."""
    from app.providers.publish.fanvue import earnings_totals

    flat = earnings_totals(SUMMARY)
    assert (flat["all_time"], flat["this_month"], flat["previous_month"]) == (
        {"gross": 1250.0, "net": 1000.0},
        {"gross": 180.0, "net": 144.0},
        {"gross": 90.0, "net": 72.0},
    )
    assert headline(flat)["net"] == 1000.0
    assert headline(flat)["this_month_net"] == 144.0
    assert headline(flat)["gross"] == 1250.0
    assert headline(flat)["previous_month_net"] == 72.0


def test_a_missing_figure_is_none_and_never_zero():
    """The whole point. `0.0` on a dashboard means "we earned nothing"; `None`
    means "we do not know", and only one of those is a reason to relax."""
    blank = headline(None)
    assert blank["gross"] is None
    assert blank["net"] is None
    assert blank["this_month_net"] is None
    assert blank["previous_month_net"] is None
    assert blank["sources_total"] is None
    assert blank["by_source"] == {}

    partial = headline({"all_time": {"net": 12.5}})
    assert partial["net"] == 12.5
    assert partial["gross"] is None
    assert partial["this_month_net"] is None


# ── the unit, and where the money came from ──────────────────────────────


def test_cents_are_not_dollars():
    """The bug this file was extended to catch, pinned at the smallest size.

    Fanvue reports USD cents (the spec says so on every money field). Read as
    dollars, a $1.00 earning arrives as 100.0 — a hundredfold overstatement that
    renders as a very good month, and no other test here would notice, because
    a fixture written in dollars is self-consistent.
    """
    from app.providers.publish.fanvue import earnings_totals

    cents = {
        "totals": {"allTime": {"gross": 100.0, "net": 100.0}, "thisMonth": {"net": 250.0}},
        "breakdownBySource": {"messages": {"net": 100.0}},
        "overTime": [{"periodStart": "2026-09-01T00:00:00Z", "gross": 100.0, "net": 100.0}],
    }
    flat = earnings_totals(cents)
    assert flat["all_time"] == {"gross": 1.0, "net": 1.0}
    assert flat["this_month"]["net"] == 2.5
    assert flat["by_source"]["messages"]["net"] == 1.0
    assert flat["over_time"][0]["net"] == 1.0

    # And a figure the platform did not report stays unknown through the
    # conversion rather than becoming zero cents.
    assert earnings_totals({"totals": {"allTime": {}}})["all_time"] == {
        "gross": None,
        "net": None,
    }
    # Sub-cent precision survives: a hundred rows of half a cent is fifty cents.
    assert earnings_totals({"totals": {"thisMonth": {"net": 0.5}}})["this_month"]["net"] == 0.005


def test_the_currency_is_the_platforms_reported_one():
    """The spec documents the unit as USD and the summary response carries no
    currency field, so the reading states USD rather than leaving a renderer to
    guess — the guess this app would otherwise make is Rand."""
    from app.providers.publish.fanvue import CURRENCY, _currency_code, earnings_totals

    assert CURRENCY == "USD"
    assert earnings_totals(SUMMARY)["currency"] == "USD"

    # A code the platform *does* state still wins, so a future response that
    # reports in something else is not silently relabelled.
    assert earnings_totals({**SUMMARY, "currency": "eur"})["currency"] == "EUR"
    assert earnings_totals({"totals": {"currency": "gbp"}})["currency"] == "GBP"

    # `_currency_code` itself still invents nothing: it answers about the field
    # it was handed, and "" is its honest answer for a field that is not a code.
    assert _currency_code("usd") == "USD"
    assert _currency_code(" usd ") == "USD"
    assert _currency_code("") == ""
    assert _currency_code(None) == ""
    assert _currency_code("dollars") == ""
    assert _currency_code("US") == ""
    assert _currency_code(840) == ""


def test_the_breakdown_names_its_biggest_source_first():
    """Paid DMs are the revenue engine on this platform. A rendering that lists
    `subs` first points the operator at the smaller number."""
    from app.providers.publish.fanvue import earnings_totals

    head = headline(earnings_totals(SUMMARY))
    assert list(head["by_source"]) == ["messages", "subs"]
    assert head["by_source"]["messages"] == 720.0
    assert head["sources_total"] == 1000.0


def test_a_breakdown_of_nothing_known_is_not_dressed_up_as_a_zero():
    """Sources the platform did not put a net figure on are dropped rather than
    listed as 0, and the sum of nothing known is None — the same rule as every
    other figure here. A *reported* zero is kept, because that one is a fact."""
    unknown = headline({"by_source": {"messages": {"gross": 5.0}, "subs": {"net": None}}})
    assert unknown["by_source"] == {}
    assert unknown["sources_total"] is None

    reported_zero = headline({"by_source": {"messages": {"net": 0.0}}})
    assert reported_zero["by_source"] == {"messages": 0.0}
    assert reported_zero["sources_total"] == 0.0


async def test_the_only_unit_a_reading_carries_is_dollars(monkeypatch):
    """`real_earnings` used to hand back the platform's raw payload beside its
    own converted totals. Two structures of the same numbers in two units is a
    100× trap with a friendly name — a caller reaching for the raw one gets a
    figure that looks like the one it wanted."""
    _publisher(monkeypatch, _FakePublisher())
    result = await real_earnings()
    assert "summary" not in result
    # Everything it does carry is the converted reading.
    assert result["totals"]["all_time"]["net"] == 1000.0


def test_the_series_is_the_platforms_own_and_in_the_same_unit():
    """The dashboard drew a hardcoded sparkline over a manufactured axis because
    there was no series to draw. There is one — Fanvue buckets its own ledger by
    day or week — and it arrives converted, so a line and the headline above it
    are in the same unit."""
    from app.providers.publish.fanvue import earnings_totals

    head = headline(earnings_totals(SUMMARY))
    assert [point["net"] for point in head["over_time"]] == [48.0, 28.0]
    assert head["over_time"][0]["period_start"] == "2026-09-01T00:00:00Z"

    # The order the platform sent is the order kept: it is a time series, and
    # re-sorting it would be inventing an axis.
    flat = earnings_totals(
        {
            "overTime": [
                {"periodStart": "2026-09-02T00:00:00Z", "gross": 200.0, "net": 100.0},
                {"periodStart": "2026-09-01T00:00:00Z", "gross": 400.0, "net": 300.0},
            ]
        }
    )
    assert [p["net"] for p in flat["over_time"]] == [1.0, 3.0]


def test_a_series_point_with_no_time_is_dropped_not_plotted_at_zero():
    """A point the platform did not timestamp has no x-position. Charting it at
    an invented one puts a real amount on a date it did not happen. A reported
    amount is kept even when it is small; only a missing *time* disqualifies."""
    from app.providers.publish.fanvue import earnings_totals

    series = earnings_totals(
        {
            "overTime": [
                {"gross": 500.0, "net": 400.0},  # no periodStart
                "not-a-point",
                {"periodStart": "2026-09-03T00:00:00Z", "gross": 0.0, "net": 0.0},
            ]
        }
    )["over_time"]
    assert [p["period_start"] for p in series] == ["2026-09-03T00:00:00Z"]
    assert series[0]["net"] == 0.0  # a reported zero is a fact, and kept


def test_no_series_is_an_empty_list_and_never_an_invented_one():
    from app.providers.publish.fanvue import earnings_totals

    assert headline(None)["over_time"] == []
    assert earnings_totals({"totals": {"allTime": {"net": 100.0}}})["over_time"] == []
    assert earnings_totals({"overTime": None})["over_time"] == []


def test_the_series_says_what_one_bucket_is():
    """A gap in a series whose bucket size is unknown is ambiguous — a quiet day
    and a week that was never reported look the same. The period travels with
    the series so a renderer can name its own axis."""
    from app.providers.publish.fanvue import earnings_totals

    assert headline(earnings_totals(SUMMARY))["period"]["granularity"] == "day"
    # Absent is `{}`, not a guessed default: "we do not know the bucket size"
    # must not render as "these are days".
    assert headline(earnings_totals({"totals": {}}))["period"] == {}
    assert headline(None)["period"] == {}


# ── the dashboard ────────────────────────────────────────────────────────


async def _dashboard(client):
    resp = await client.get("/api/v1/dashboard/summary")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_the_dashboard_labels_what_its_revenue_number_counts(client, monkeypatch):
    """It was a bare `revenue` that read as "the studio's revenue". It is the
    recorded analytics figure, and now it says so."""
    _raising(monkeypatch, PublishNotConfigured("nothing connected"))
    body = await _dashboard(client)
    assert body["revenue"] == 0
    assert "not payments" in body["revenue_note"]
    assert "real_revenue" in body["revenue_note"]


async def test_the_dashboard_does_not_render_an_unreadable_ledger_as_zero(client, monkeypatch):
    _raising(monkeypatch, PublishNotConfigured("nothing connected"))
    body = await _dashboard(client)
    assert body["real_revenue_state"] == "not_configured"
    assert body["real_revenue"]["gross"] is None
    assert body["real_revenue"]["net"] is None
    assert body["real_revenue"]["this_month_net"] is None
    assert body["real_revenue"]["currency"] == ""


async def test_the_dashboard_reports_real_money_when_there_is_a_ledger(client, monkeypatch):
    _publisher(monkeypatch, _FakePublisher())
    body = await _dashboard(client)
    assert body["real_revenue_state"] == "ok"
    assert body["real_revenue"]["net"] == 1000.0
    assert body["real_revenue"]["this_month_net"] == 144.0


async def test_the_dashboard_carries_the_reason_it_could_not_read(client, monkeypatch):
    """State alone is not actionable — the operator needs the platform's own
    words to know whether to re-authorize or wait."""
    _publisher(monkeypatch, _FakePublisher(raises=PublishFailed("HTTP 401 from Fanvue")))
    body = await _dashboard(client)
    assert body["real_revenue_state"] == "error"
    assert "401" in body["real_revenue_detail"]


# ── one rule, two callers ────────────────────────────────────────────────


async def test_the_earnings_endpoint_and_the_dashboard_agree(client, monkeypatch):
    """They render the same judgement. Two copies of "what does unknown mean" is
    how one of them starts reporting zeros while the other stays honest."""
    for publisher in (_FakePublisher(), _FakePublisher(raises=PublishFailed("HTTP 500"))):
        _publisher(monkeypatch, publisher)
        endpoint = (await client.get("/api/v1/publish/earnings")).json()
        dashboard = await _dashboard(client)
        assert endpoint["state"] == dashboard["real_revenue_state"]
        assert (endpoint["totals"] is None) == (
            dashboard["real_revenue"]["net"] is None
        )


async def test_the_read_is_shared_rather_than_reimplemented():
    """A source-level check, because the drift this guards against is exactly
    two implementations that each pass their own tests."""
    import inspect

    from app.routes import analytics, publish

    assert "get_publisher" not in inspect.getsource(publish.publish_earnings)
    assert "get_publisher" not in inspect.getsource(analytics.dashboard_summary)
    assert "real_earnings" in inspect.getsource(publish.publish_earnings)
    assert "real_earnings" in inspect.getsource(analytics.dashboard_summary)


def test_the_dashboard_timeout_is_shorter_than_the_publishers_default():
    """A dashboard render must not hang on someone else's API for thirty
    seconds. If the two ever cross, this fails rather than the page."""
    from app.providers.publish.fanvue import _API_TIMEOUT
    from app.routes.analytics import _EARNINGS_TIMEOUT_SECONDS

    assert 0 < _EARNINGS_TIMEOUT_SECONDS < _API_TIMEOUT


def test_unknown_is_a_word_not_a_number():
    """Every state except `ok` must carry `totals=None`. A state machine that
    can return figures for a failed read is a state machine nobody can trust."""
    for state in ("not_configured", "disabled", "unsupported", "error"):
        blank = earnings_module._blank(state)
        assert blank["state"] == state
        assert blank["totals"] is None


async def test_the_live_configuration_is_unconfigured_and_says_so(client):
    """No monkeypatching: this is what the app reports today, with `FANVUE_*`
    empty and no price stated. It must be "we cannot tell", not 0."""
    body = await _dashboard(client)
    assert body["real_revenue_state"] in {
        "not_configured", "disabled", "error", "unsupported"
    }
    assert body["real_revenue"]["net"] is None
