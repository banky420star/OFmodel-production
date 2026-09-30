"""Publishing — the HTTP face of `app.publishing`.

This is the only path in the app that puts content in front of someone on a
platform the studio does not own, so it is the one place where a false success
costs real money and real trust. The rule that governs it lives in
`app/publishing.py` and is shared with the scheduler:

    **`status = "posted"` is written only when the platform returned a post id.**

Two distinctions the route keeps deliberately separate, because collapsing them
sends an operator hunting for the wrong problem:

  * **503 — not configured.** No credentials at all.
  * **409 — configured but not armed**, or the post cannot be published as it
    stands (already posted, priced with no media, media that does not resolve).
    These are states of *this post*, not of the deployment.

The scheduler's 30 generated rows currently carry no media at all, so most of
them will correctly land in the second bucket. That is the honest answer to
"why is nothing published": the calendar has slots, not content.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.providers.gates import require_publisher
from app.publishing import PublishRefused, publish_post

router = APIRouter()


@router.get("/publish/status")
async def publish_status():
    """Can the studio post right now, and if not, exactly what is missing.

    Never raises: an unconfigured publisher is the answer, not an error. The
    UI reads this to explain the `live_posts` division rather than showing it
    as a generic failure.

    The probe itself lives in `app.providers.publish.publisher_state` because
    the monetization readiness gate asks the same question, and two answers to
    "is this armed" is how one surface starts reporting ready while the other
    is still blocked.
    """
    from app.providers.publish import publisher_state

    return await publisher_state()


@router.get("/publish/earnings")
async def publish_earnings(
    days: int = Query(30, ge=1, le=365, description="how far back to summarise"),
):
    """What the connected platform has actually paid — its books, not ours.

    Never raises, for the same reason `publish/status` does not: "not
    configured" is an answer about the deployment, not a failure of the
    request. `state` says which.

    This is the only real-money figure the app can produce. The internal
    `/fan` wallet is simulated end to end — `get_processor()` resolves to
    `fake` and raises for anything else — so the ledger in this database
    cannot be evidence that anyone paid. `GET /v1/insights/earnings/summary`
    is Fanvue's own accounting, after their fee, and is the number worth
    watching.

    The reading itself lives in `app.earnings` because the dashboard asks the
    same question, and two copies of "what does unknown mean" is how one of
    them starts reporting zeros while the other stays honest.
    """
    from app.earnings import real_earnings

    return await real_earnings(days)


@router.get("/publish/conversations")
async def publish_conversations(limit: int = 25):
    """The platform's inbox — read-only, and it says so in its own response.

    Paid DMs are Fanvue's revenue engine, so this is the surface closest to
    where money is actually made: a fan who writes and does not get answered is
    a sale that did not happen. It is a *reading* and nothing else — no reply,
    no marking read, no state changed on the account.

    Every state other than `ok` carries `chats: None` rather than an empty list,
    because "nobody has written" and "we could not ask" are different facts and
    an empty list is the more comfortable one. `forbidden` is separated from
    `error`: a token issued before this app requested `read:chat` refuses every
    chat call, and the fix for that is re-connecting the platform, not retrying.
    """
    from app.conversations import real_conversations

    return await real_conversations(limit=limit)


@router.get("/monetization/readiness")
async def monetization_readiness(db: AsyncSession = Depends(get_db)):
    """The five gates between this studio and a first real payment.

    Read-only and measured. This replaces the constants the monetization page
    used to render as "launch gates" — two of which were `true` unconditionally
    and one of which was inferred from a row existing rather than from any
    disclosure being present.
    """
    from app.readiness import monetization_readiness as _readiness

    return await _readiness(db)


@router.post("/scheduled-posts/{post_id}/publish")
async def publish_scheduled_post(
    post_id: str,
    audience: str = Query("subscribers"),
    db: AsyncSession = Depends(get_db),
):
    """Publish one scheduled post to the platform, and record what happened.

    503 when nothing is configured; 409 when this post cannot go out as it
    stands. On success the row carries the platform's own post id, which is the
    only evidence that anything was published.
    """
    publisher = require_publisher()

    try:
        return await publish_post(db, post_id, audience=audience, publisher=publisher)
    except PublishRefused as refused:
        raise HTTPException(refused.status_code, refused.detail) from refused


# ── the clock (app/scheduler.py) ─────────────────────────────────────────
# These live beside the publish route rather than in their own module because
# the clock exists to drive this path and nothing else. `status` is read-only
# and safe to call always; `tick` is the loop's own body, exposed so an operator
# can run one pass without waiting for the interval.


@router.get("/scheduler/status")
async def scheduler_status():
    """What the clock is doing — and whether it is doing anything at all."""
    from app import scheduler

    return scheduler.status()


@router.post("/scheduler/tick")
async def scheduler_tick():
    """Run one pass of the clock now.

    Safe to call at any time: with no publisher it publishes nothing and reports
    `blocked`, and with no due posts it does nothing at all. When a publisher is
    armed and a post is due, this publishes for real — it is the loop's own
    body, not a rehearsal.
    """
    from app import scheduler

    return await scheduler.tick()
