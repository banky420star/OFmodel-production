"""What stands between this studio and a first real payment, measured.

The monetization page's launch gates used to be constants. Two of the four
returned `done: True` regardless of the deployment, one was true whenever the
dashboard happened to load, and "synthetic identity declared" was inferred from
"a persona exists" — which is not evidence that any profile carries a
disclosure. A gate that cannot fail is decoration, and this is the one page
whose entire job is to say when money can start arriving.

Every check here is read from configuration or the database, and each carries
the next action that would clear it. Nothing is estimated and nothing is
promised: the numbers on this page are facts about now, and the only figure
that means money is the platform's own ledger (`app/earnings.py`).

The five gates are the five things that must all be true before a stranger can
pay for anything this studio makes:

  1. an account is connected and its token authenticates
  2. publishing is armed rather than merely configured
  3. a price is stated, and it is at or above the platform's floor
  4. there is scheduled content that actually carries media
  5. the clock is armed to publish it

They are ordered by what an operator would fix first, which is also the order
they appear in the UI.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _check(key: str, label: str, done: bool, detail: str, next_step: str = "") -> dict:
    """One gate. `next_step` is empty exactly when the gate is already clear."""
    return {
        "key": key,
        "label": label,
        "done": bool(done),
        "detail": detail,
        "next_step": "" if done else next_step,
    }


async def monetization_readiness(db: AsyncSession) -> dict:
    """The five gates, each measured — never asserted.

    Never raises. A gate whose measurement failed is reported as not-done with
    the reason, because "we could not tell" is not a pass, and a readiness page
    that 500s tells an operator less than one that says which piece is missing.
    """
    from app.config import get_settings
    from app.providers.publish import publisher_state

    state = await publisher_state()
    settings = get_settings()
    platform = state.get("provider") or "fanvue"

    checks: list[dict] = []

    # 1. Connected. `publisher_state` reports `configured` separately from
    #    `armed`, so "no token yet" and "token present, switch off" do not
    #    collapse into one message with one wrong next step.
    connected = bool(state.get("configured"))
    checks.append(
        _check(
            "platform_connected",
            "An account is connected",
            # Configured *and* the token authenticates. A publisher object that
            # exists but 401s is not connected in any sense a fan would
            # recognise, and `unhealthy` is exactly that state.
            connected and state.get("state") != "unhealthy",
            state.get("detail") or state.get("state", ""),
            "Authorize the platform: open GET /api/v1/fanvue/connect in a "
            "browser and sign in as the account owner. Fanvue issues no static "
            "API keys, so there is no token to paste without this step.",
        )
    )

    # 2. Armed. Deliberately separate from connected: credentials copied in for
    #    a read-only check must not silently become authority to post.
    armed = bool(state.get("armed"))
    checks.append(
        _check(
            "publishing_armed",
            "Publishing is armed",
            armed,
            state.get("detail") if armed else "FANVUE_PUBLISH_ENABLED is not true.",
            "Set FANVUE_PUBLISH_ENABLED=true once the line should actually "
            "post. Being configured is separate from being armed on purpose.",
        )
    )

    # 3. Priced. The floor check is the platform adapter's own, not a number
    #    restated here — a copied floor is a floor that drifts above the one
    #    the platform enforces, and the failure then lands at publish time.
    from app.publishing import PublishRefused, resolved_price_minor

    stated = getattr(settings, "DEFAULT_PPV_PRICE", None)
    try:
        price_minor = resolved_price_minor(platform, stated)
    except PublishRefused as exc:
        checks.append(
            _check(
                "price_stated",
                "A price is stated",
                False,
                exc.detail,
                "Set DEFAULT_PPV_PRICE in .env to the operator's default price "
                "per post, in dollars.",
            )
        )
        price_minor = None
    else:
        if price_minor is None:
            # Only reachable for a platform that cannot charge, which is not the
            # one this package ships. Reported rather than assumed.
            checks.append(
                _check(
                    "price_stated",
                    "A price is stated",
                    False,
                    f"{platform} does not support priced posts, so nothing "
                    "published there can earn.",
                    "This app can only sell through Fanvue.",
                )
            )
        else:
            checks.append(
                _check(
                    "price_stated",
                    "A price is stated",
                    True,
                    f"{platform} posts are priced at ${price_minor / 100:.2f}.",
                )
            )

    # 4. Content. Counted from the calendar, and a slot with no media is not
    #    content — the publisher refuses one, and `media_keys` is what the
    #    publisher reads. `priced_count` is reported because a calendar of
    #    free posts looks identical to a calendar of sellable ones in a count
    #    of slots.
    #
    #    Counted only over platforms this app has an adapter for, which is the
    #    correction that matters most on this gate. The calendar carries 42
    #    legacy rows on instagram/tiktok/youtube, and `publisher_classes()` has
    #    no entry for any of them — so they can never ship whatever they carry,
    #    and measuring against them said "0 of 42" about an operator's actual
    #    task. Worse in the other direction: `done` asked only whether *any*
    #    post had media and a price, so one priced Instagram row would have
    #    turned this gate green over a calendar from which nothing can be sold.
    #    A false green here is worse than a red, because it is the gate whose
    #    whole job is to say when money can start arriving.
    from app.models import ScheduledPost
    from app.publishing import publisher_classes

    publishable = set(publisher_classes())
    rows = (
        await db.execute(
            select(ScheduledPost).where(ScheduledPost.status == "scheduled")
        )
    ).scalars().all()
    on_platform = [post for post in rows if post.platform in publishable]
    stranded = len(rows) - len(on_platform)
    scheduled_count = len(on_platform)
    with_media = [post for post in on_platform if post.media_keys]
    priced_count = len([post for post in with_media if post.ppv_price is not None])

    detail = (
        f"{len(with_media)} of {scheduled_count} scheduled posts on a platform "
        f"this app can publish to carry media; {priced_count} carry a price."
    )
    if stranded:
        # Named rather than folded into the denominator. Hiding them would make
        # the count look clean; counting them would make the task look 42×
        # larger than it is.
        platforms = sorted({post.platform for post in rows if post.platform not in publishable})
        detail += (
            f" {stranded} further row(s) sit on {', '.join(platforms)}, where no "
            "publishing adapter exists — they cannot ship whatever media they carry."
        )
    checks.append(
        _check(
            "content_ready",
            "Scheduled posts carry media and a price",
            len(with_media) > 0 and priced_count > 0,
            detail,
            "Generate a shoot, then run the smart scheduler for a persona "
            "(POST /api/v1/personas/{id}/schedule/smart). A slot with no media "
            "is refused at publish time, so it is not content yet.",
        )
    )

    # 5. The clock.
    clock = bool(getattr(settings, "SCHEDULER_ENABLED", False))
    checks.append(
        _check(
            "clock_armed",
            "The publishing clock is armed",
            clock,
            (
                f"the scheduler ticks every {settings.SCHEDULER_INTERVAL_SECONDS}s"
                if clock
                else "SCHEDULER_ENABLED is false — the calendar is a list, not a queue."
            ),
            "Set SCHEDULER_ENABLED=true. Until then a post goes out only when "
            "POST /api/v1/scheduler/tick is called by hand.",
        )
    )

    blockers = [item["key"] for item in checks if not item["done"]]
    return {
        "ready": not blockers,
        "platform": platform,
        "checks": checks,
        "blockers": blockers,
        "note": (
            "Every gate here is read from configuration or the calendar. Passing "
            "all five means a post can be published and charged for; it does not "
            "mean anyone has paid. The only real-money figure is the platform's "
            "own ledger — see GET /api/v1/publish/earnings."
        ),
    }
