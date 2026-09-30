"""The whole book: what can ship, what it has earned, and the one thing to do next.

Three layers already exist and none of them has ever been asked at the same
time. `app/earnings.py` knows the only real money this app can read.
`app/readiness.py` knows the five gates between the studio and a first payment.
`app/divisions.py` knows which production units can act right now. Each answers
its own question well, and a manager screen that showed any one of them was
still not showing the business.

So this module is deliberately an *aggregation* and not a fourth opinion. Every
number here is read from one of those three or from the calendar. Nothing is
estimated, nothing is projected, and no figure is invented to fill a shape.

Three rules carry over from the modules it joins, because they are the whole
reason those modules look the way they do:

1. **Unknown is never zero.** A ledger that could not be read reports `None`,
   and `state` says which kind of unknown it was — "earned nothing" and "the
   platform did not answer" are different facts and only one is bad news.

2. **A slot is not inventory.** A scheduled row ships only if it carries a price
   *and* media that is really on disk. `can_ship` is computed from that, not
   from a count of rows, because a calendar of placeholders looks identical to a
   calendar of product in any count of slots. Measured on live storage: 449
   directories, 338 files, not one of them a usable image.

3. **The next action is the first blocker, not a new ranking.** The five gates
   are already ordered by what an operator would fix first, and each carries its
   own `next_step`. Restating that ordering here would be a second rule that can
   drift from the first, so this module takes the first gate that is not done and
   reports its own sentence.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.earnings import headline, real_earnings
from app.models import ContentPack, Persona, ScheduledPost, Shoot, SocialAccount
from app.publishing import media_on_disk, publisher_classes
from app.readiness import monetization_readiness


def _post_media_state(post: ScheduledPost) -> tuple[bool, str]:
    """Whether every media key on a post resolves to a usable file.

    Every key, not any: the publisher refuses a partial set, on the grounds that
    three photos where one is a broken placeholder is a different product from
    the one that was scheduled. A view that accepted "at least one good file"
    would call that post shippable and be wrong in exactly the case that has
    already burned this codebase once.
    """
    keys = [k for k in (post.media_keys or []) if k]
    if not keys:
        return False, "no media key on the post"
    reasons: list[str] = []
    for key in keys:
        usable, reason = media_on_disk(key)
        if not usable:
            reasons.append(f"{key}: {reason}")
    if reasons:
        return False, reasons[0]
    return True, ""


def _persona_row(
    persona: Persona,
    *,
    posts: list[ScheduledPost],
    accounts: list[SocialAccount],
    publishable: set[str],
    shoots_done: int,
    packs: int,
) -> dict:
    """One model, as a business rather than a worklist.

    The roster next door answers "which account needs what next". This answers
    what the account is *for*: whether anything this persona owns could earn.
    """
    connected = sorted({a.platform for a in accounts
                        if a.status in ("active", "approved") and a.platform in publishable})

    # Only slots on a platform this app can publish to are counted at all. A
    # priced row on instagram looks like inventory in a count of slots and can
    # never ship, which is the false-green the readiness content gate already
    # had to be repaired for once — and the same mistake is available here. The
    # rows on platforms with no adapter are reported separately as stranded.
    scheduled = [p for p in posts if p.status == "scheduled"]
    on_platform = [p for p in scheduled if p.platform in publishable]
    stranded = len(scheduled) - len(on_platform)
    priced = [p for p in on_platform if p.ppv_price is not None]

    shippable: list[ScheduledPost] = []
    for post in priced:
        ok, _reason = _post_media_state(post)
        if ok:
            shippable.append(post)

    # What is missing, in the order an operator would fix it. Each slug has a
    # sentence beside it in `_BLOCKER_SENTENCE` so the UI never invents one.
    blocked_by: list[str] = []
    if not connected:
        blocked_by.append("no_publishable_account")
    if not on_platform:
        blocked_by.append("nothing_scheduled")
    else:
        if not priced:
            blocked_by.append("nothing_priced")
        if not shippable:
            blocked_by.append("media_not_on_disk")

    can_ship = bool(connected) and bool(shippable)
    priced_value = sum(p.ppv_price for p in shippable if p.ppv_price)

    return {
        "persona_id": str(persona.id),
        "name": persona.name,
        "status": persona.status.value if hasattr(persona.status, "value") else str(persona.status),
        "adult_verified": bool(persona.adult_verified),
        "synthetic_identity": bool(persona.synthetic_identity),
        "publishable_accounts": connected,
        "content": {"shoots_completed": shoots_done, "packs": packs},
        "calendar": {
            "scheduled": len(on_platform),
            "priced": len(priced),
            "shippable": len(shippable),
            # Counted, not dropped: a slot this persona owns that can never go
            # out is the reason its calendar is not the problem it looks like.
            "stranded": stranded,
        },
        # What the shelf would sell for, if every shippable slot sold. Not a
        # forecast and not revenue — the sum of the prices on the posts, which
        # is a fact about the calendar.
        "shelf_value": round(priced_value, 2),
        "can_ship": can_ship,
        "blocked_by": blocked_by,
        # The sentences, so the UI renders a reason rather than a slug. Kept
        # beside the slugs rather than instead of them: a caller that wants to
        # switch on the blocker still can.
        "blocked_by_detail": [_BLOCKER_SENTENCE[s] for s in blocked_by],
    }


_BLOCKER_SENTENCE = {
    "no_publishable_account": (
        "No account on a platform this app can publish to. Approve or connect "
        "one — Fanvue is the only platform with an adapter."
    ),
    "nothing_scheduled": "Nothing on the calendar. Run the smart scheduler for this persona.",
    "nothing_priced": (
        "The calendar carries slots but no price, so every post would go out "
        "free. Set DEFAULT_PPV_PRICE and re-run the scheduler."
    ),
    "media_not_on_disk": (
        "The slots are priced but their media does not resolve to a usable file, "
        "so the publisher would refuse them. Generate a shoot first."
    ),
}


async def business_state(db: AsyncSession, *, days: int = 30, timeout: float | None = 5.0) -> dict:
    """The whole book in one read. Never raises.

    Read-only by construction: it resolves media (which stats and reads file
    headers), but it writes nothing, publishes nothing, and contacts no platform
    except the one earnings read, which is bounded by `timeout` so a slow API
    cannot hold a manager screen open.
    """
    gates = await monetization_readiness(db)

    # --- money ---------------------------------------------------------
    reading = await real_earnings(days, timeout=timeout)
    money = {
        "state": reading["state"],
        "provider": reading["provider"],
        "detail": reading["detail"],
        "note": reading["note"],
        "headline": headline(reading["totals"]),
        # The one flag a UI should switch on. Everything else about money is
        # either unknown or simulated, and this says which.
        "is_real": reading["state"] == "ok",
    }

    # --- production and calendar ---------------------------------------
    publishable = set(publisher_classes())

    personas = list((await db.execute(select(Persona))).scalars().all())
    persona_ids = [str(p.id) for p in personas]
    uuid_ids = [p.id for p in personas]

    accounts: list[SocialAccount] = []
    posts: list[ScheduledPost] = []
    if persona_ids:
        accounts = list((await db.execute(
            select(SocialAccount).where(SocialAccount.persona_id.in_(persona_ids))
        )).scalars().all())
        # ScheduledPost.persona_id is String(36) while Persona.id is a UUID
        # column — the same filtered view needs the dashed string here and the
        # UUID object two lines up. Binding the wrong one raises from the bind
        # processor rather than matching nothing, which is at least loud.
        posts = list((await db.execute(
            select(ScheduledPost).where(ScheduledPost.persona_id.in_(persona_ids))
        )).scalars().all())

    shoots_by_persona: dict[str, int] = {}
    packs_by_persona: dict[str, int] = {}
    if uuid_ids:
        for shoot in (await db.execute(
            select(Shoot).where(Shoot.persona_id.in_(uuid_ids))
        )).scalars().all():
            key = str(shoot.persona_id)
            done = (shoot.status.value if hasattr(shoot.status, "value") else str(shoot.status)) == "completed"
            shoots_by_persona[key] = shoots_by_persona.get(key, 0) + (1 if done else 0)
        for pack in (await db.execute(
            select(ContentPack).where(ContentPack.persona_id.in_(uuid_ids))
        )).scalars().all():
            key = str(pack.persona_id)
            packs_by_persona[key] = packs_by_persona.get(key, 0) + 1

    rows = [
        _persona_row(
            persona,
            posts=[p for p in posts if str(p.persona_id) == str(persona.id)],
            accounts=[a for a in accounts if str(a.persona_id) == str(persona.id)],
            publishable=publishable,
            shoots_done=shoots_by_persona.get(str(persona.id), 0),
            packs=packs_by_persona.get(str(persona.id), 0),
        )
        for persona in personas
    ]
    # A business view sorts by what can earn, not alphabetically.
    rows.sort(key=lambda r: (not r["can_ship"], -r["shelf_value"], r["name"].lower()))

    # Slots on a platform no adapter exists for can never ship whatever they
    # carry. Named rather than folded into a count, for the same reason the
    # content gate names them: hiding them flatters the number.
    stranded = [p for p in posts if p.platform not in publishable and p.status == "scheduled"]

    # --- the one next action -------------------------------------------
    blockers = [c for c in gates["checks"] if not c["done"]]
    if blockers:
        first = blockers[0]
        next_action = {
            "key": first["key"],
            "label": first["label"],
            "detail": first["next_step"],
            "gates_open": len(gates["checks"]) - len(blockers),
            "gates_total": len(gates["checks"]),
            "also_blocked": [b["key"] for b in blockers[1:]],
        }
    else:
        next_action = {
            "key": "watch_the_ledger",
            "label": "Nothing is blocking a post",
            "detail": (
                "All five gates are clear, so a post can be published and charged "
                "for. That is not the same as anyone having paid: the only real "
                "money figure is the ledger below."
            ),
            "gates_open": len(gates["checks"]),
            "gates_total": len(gates["checks"]),
            "also_blocked": [],
        }

    # When the blocker is content, say *who* to produce for — the gate cannot
    # know that, and "generate a shoot" with no model named is a task with no
    # subject.
    if next_action["key"] == "content_ready" and rows:
        needs = [r["name"] for r in rows if not r["can_ship"]]
        if needs:
            next_action["for_personas"] = needs

    return {
        "money": money,
        "gates": gates,
        "personas": rows,
        "stranded_slots": {
            "count": len(stranded),
            "platforms": sorted({p.platform for p in stranded}),
            "note": (
                "Scheduled rows on platforms this app has no publishing adapter "
                "for. They cannot ship whatever media they carry, and they are "
                "excluded from every shippable count above."
            ),
        },
        "summary": {
            "personas": len(rows),
            "personas_that_can_ship": len([r for r in rows if r["can_ship"]]),
            "accounts": len(accounts),
            "accounts_live": len([a for a in accounts if a.status == "active"]),
            "scheduled_posts": len(posts),
            "shippable_posts": sum(r["calendar"]["shippable"] for r in rows),
            "shelf_value": round(sum(r["shelf_value"] for r in rows), 2),
        },
        "next_action": next_action,
        "note": (
            "Everything here is read from configuration, the calendar, or the "
            "platform's own books. `shelf_value` is the sum of prices on posts "
            "that could actually ship — it is what the shelf would sell for, not "
            "what anyone has paid. The only real-money figure is "
            "`money.headline`, and it is None until the platform is connected."
        ),
    }
