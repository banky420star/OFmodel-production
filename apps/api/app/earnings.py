"""The one place the app asks whether any real money has arrived.

Two rules, and both exist because of failures this codebase already carries:

* **Never render an unreadable figure as zero.** "We earned nothing" and "the
  platform did not answer" are different facts, and only one of them is bad
  news. A 401 from an expired token is a token problem, not a $0.00 month.
* **Never present a local number as payment.** The internal `/fan` wallet is
  simulated end to end — `billing/processor.py::get_processor()` resolves to
  `fake` and raises for anything else — so nothing in this database can be
  evidence that a person paid. `AnalyticsSnapshot.revenue` is the figure
  Instagram and TikTok syncs write, and those APIs do not report revenue either
  (their sync paths hardcode 0). Fanvue's own ledger, after their fee, is the
  only real-money reading this app can produce.

Extracted rather than written twice in `routes/publish.py` and
`routes/analytics.py`: the dashboard and the earnings endpoint have to agree
about what "unknown" means, and two copies of that judgement is how one of them
starts quietly reporting zeros while the other stays honest.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# The states a reading can be in. `ok` is the only one that carries figures.
#   not_configured — no credentials for any platform that reports earnings
#   disabled       — credentials present, posting not armed
#   unsupported    — a connected platform that exposes no earnings endpoint
#   error          — the platform was reached and the read failed
#   ok             — read from the platform's own books
#
# Each one is a different next action, which is the whole reason they are
# separate values rather than a boolean plus a string.
UNKNOWN_NOTE = (
    "no real revenue is observable through this app until the platform is "
    "connected — the wallet and ledger elsewhere in this app are simulated"
)


def _blank(state: str, *, provider: str = "", detail: str = "") -> dict:
    """A reading with no figures.

    `totals` is None, never a zeroed structure: a caller that renders this as
    "$0.00" has to do it deliberately, and `state` tells it not to.
    """
    return {
        "state": state,
        "provider": provider,
        "detail": detail,
        "totals": None,
        "note": UNKNOWN_NOTE,
    }


async def real_earnings(days: int = 30, *, timeout: float | None = None) -> dict:
    """What the connected platform has actually paid, or why that is unknown.

    Never raises. "Not configured" is an answer about the deployment rather than
    a failure of the request, and an endpoint that 500s because nobody has
    connected an account yet is an endpoint an operator learns to ignore.

    `timeout` is for callers on a request path a human is waiting on — the
    dashboard passes a short one so a slow platform cannot hold a page render
    open for the module's default thirty seconds.
    """
    from app.providers.publish import (
        PublishDisabled,
        PublishError,
        PublishNotConfigured,
        get_publisher,
    )
    from app.providers.publish.fanvue import earnings_totals

    try:
        publisher = get_publisher()
    except PublishNotConfigured as exc:
        return _blank("not_configured", detail=str(exc))
    except PublishDisabled as exc:
        return _blank("disabled", detail=str(exc))
    except PublishError as exc:
        return _blank("error", detail=str(exc))

    reader = getattr(publisher, "earnings_summary", None)
    if reader is None:
        return _blank(
            "unsupported",
            provider=publisher.name,
            detail=f"{publisher.name} exposes no earnings endpoint",
        )

    start = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        if timeout is None:
            summary = await reader(start=start)
        else:
            summary = await reader(start=start, timeout=timeout)
    except TypeError:
        # An adapter whose `earnings_summary` has no `timeout` parameter. Falling
        # back to the unbounded call is better than losing the reading, and it is
        # a `TypeError` rather than a silent wrong answer.
        try:
            summary = await reader(start=start)
        except PublishError as exc:
            return _blank("error", provider=publisher.name, detail=str(exc))
    except PublishError as exc:
        return _blank("error", provider=publisher.name, detail=str(exc))

    return {
        "state": "ok",
        "provider": publisher.name,
        "detail": f"earnings from {start.date().isoformat()} to now",
        "totals": earnings_totals(summary),
        # No raw passthrough. `earnings_totals` converts Fanvue's USD cents to
        # dollars, and the unconverted payload sitting beside its own converted
        # output is a 100× trap with a friendly name: a caller that reached for
        # `summary` instead of `totals` would get a number that looks like the
        # same one. It had no consumers, so the field is gone rather than
        # renamed — nothing here can be read in two units.
        "note": (
            "The platform's own accounting, net of its fee. This is real money; "
            "the wallet and ledger elsewhere in this app are simulated. Watch "
            "by_source.messages — paid DMs are the revenue engine here, so a "
            "subscription-only reading understates it."
        ),
    }


def headline(totals: dict | None) -> dict:
    """The numbers worth putting on a dashboard, and nothing invented.

    `net` is what reached the creator after the platform's fee, `gross` is
    before it, and both are **None** when the platform did not report them — so
    a caller cannot chart a missing figure as a zero without doing it on
    purpose.

    Three fields exist for the renderer rather than the accountant:

    * `currency` — the code the figures are in, or `""` when a caller passed a
      structure that does not carry one. A UI that prefixes "R" to everything
      must not prefix it to a dollar figure, and the only way it can know is if
      this says so. `earnings_totals` always sets it (`fanvue.CURRENCY` is
      documented in the platform's spec), so `""` means the structure did not
      come from there.
    * `by_source` — where the money came from, largest first. On this platform
      paid DMs are the revenue engine, so a single all-in figure hides the one
      number worth acting on.
    * `sources_total` — the sum of the sources the platform *did* report. It is
      `None` when none were reported, so a caller cannot show a partial split as
      if it were the whole ledger.
    * `over_time` — the platform's own series, oldest bucket first. A real one:
      it is the platform's ledger bucketed by day or week, not a projection, and
      it is the only series this app can draw that is not invented. Empty when
      the platform reported none, which is why a renderer must handle empty
      rather than assume a line exists.
    """
    all_time = (totals or {}).get("all_time") or {}
    this_month = (totals or {}).get("this_month") or {}
    by_source = (totals or {}).get("by_source") or {}

    net_by_source = {
        name: (by_source.get(name) or {}).get("net")
        for name in by_source
        if (by_source.get(name) or {}).get("net") is not None
    }
    known = [value for value in net_by_source.values() if value is not None]

    return {
        "gross": all_time.get("gross"),
        "net": all_time.get("net"),
        "this_month_net": this_month.get("net"),
        "previous_month_net": ((totals or {}).get("previous_month") or {}).get("net"),
        "currency": (totals or {}).get("currency") or "",
        "by_source": dict(
            sorted(net_by_source.items(), key=lambda item: item[1], reverse=True)
        ),
        "sources_total": round(sum(known), 2) if known else None,
        "over_time": list((totals or {}).get("over_time") or []),
        # What one bucket of `over_time` is — day or week — and the window it
        # covers. A chart of a series whose bucket size is unknown cannot say
        # whether a gap is a quiet day or a week that was never reported.
        "period": (totals or {}).get("period") or {},
    }
