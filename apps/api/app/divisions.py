"""Divisions — the studio's operating structure, as the studio can actually run it.

The manager screen answered one question (which platform account needs which
human step). That is a *worklist*, not a business: it says nothing about what
the studio makes, who makes it, how often, or what is stopping it. This module
is that missing layer — the seven divisions that own production work, each
declared with what it produces, which capability it needs, which route actually
runs it, and — the part that matters — **whether it can run right now**.

Three rules make this worth having instead of a static list of headings:

1. **A division is not "live" because a page exists for it.** `maturity` is one
   of `live`, `partial`, `unbuilt`, and it is set by whether the entrypoint is
   wired to a real provider, not by whether a route returns 200. A division
   that is `unbuilt` says so in `gap` and is never rendered as a spinner.

2. **Readiness is resolved, not asserted.** `requires` names capability keys and
   the gate answers with the configured provider or the reason it is missing, so
   "video production" is red here for exactly the reason it would be red at the
   route — one source of truth, not two that can disagree.

3. **The cadence is stated honestly, including when there is none.** Five of the
   seven are `on demand`: nothing drains the calendar, nothing publishes on a
   timer. A studio whose divisions all say "manual" is telling the operator the
   truth about where it stands, which is the only way the next thing to build is
   obvious.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ContentPack, Persona, ScheduledPost, Shoot, SocialAccount

# Maturity, and what each value commits to.
LIVE = "live"          # wired end to end: a route reaches a real provider
PARTIAL = "partial"    # the record and provider exist; the loop between them does not
UNBUILT = "unbuilt"    # named so it can be seen missing; nothing runs it yet


@dataclass(frozen=True)
class Division:
    """One producing unit. Frozen so the registry cannot be edited at runtime
    into something that disagrees with the code it points at."""

    key: str
    name: str
    owns: str
    produces: tuple[str, ...]
    cadence: str
    requires: tuple[str, ...]
    routes: tuple[str, ...]
    workflow: str
    maturity: str
    gap: str = ""


# The divisions, in the order the user drew them. Every route named here was
# read out of app/routes/ rather than assumed; every workflow names a handler
# that exists in app/workflows/content_flow.py.
DIVISIONS: tuple[Division, ...] = (
    Division(
        key="shoots",
        name="Shoots",
        owns="Turns a shoot plan into finished stills.",
        produces=("shoot", "generated_image"),
        cadence="on demand — a shoot renders when an operator starts it",
        requires=("image", "storage"),
        routes=("POST /personas/{id}/shoots", "POST /shoots/{id}/start"),
        workflow="content_flow.generate_shoot_images_handler",
        maturity=LIVE,
    ),
    Division(
        key="photo_shoot_planner",
        name="Photo shoot planner",
        owns="Decides what a shoot is before it renders: theme, framing, wardrobe, image count.",
        produces=("shoot_plan",),
        cadence="on demand",
        requires=("llm", "image"),
        routes=("POST /personas/{id}/shoots",),
        workflow="content_flow.plan_shoot_handler",
        maturity=PARTIAL,
        gap=(
            "A Shoot row already carries theme, prompt_template and image_count, "
            "and the handler can fill them from an objective — but no route calls "
            "it as a plan step, so the plan is typed in by hand rather than "
            "proposed and approved."
        ),
    ),
    Division(
        key="look_planner",
        name="Look planner",
        owns="Keeps one face across every plate: appearance, identity locks, styling.",
        produces=("identity", "style"),
        cadence="on demand, plus once at build time",
        requires=("image", "trainer"),
        routes=("POST /personas/{id}/identities/{iid}/approve", "POST /personas/{id}/generate-locked-image"),
        workflow="persona_flow (identity build)",
        maturity=PARTIAL,
        gap=(
            "Identity locking and the LoRA are real and enforced at generation. "
            "What is missing is a *planner*: artistic_styles.py holds style "
            "presets but nothing proposes a look schedule across a week, so "
            "every shoot re-decides wardrobe from scratch."
        ),
    ),
    Division(
        key="video_production",
        name="Video production",
        owns="Animates a finished still into a clip.",
        produces=("video",),
        cadence="on demand",
        requires=("image", "video", "storage"),
        routes=("POST /personas/{id}/generate-video", "POST /shoots/{id}/generate-video"),
        workflow="content_flow.generate_shoot_videos_handler",
        maturity=PARTIAL,
        gap=(
            "The provider is real (ComfyUI + SVD, image-to-video only) and the "
            "routes reach it. SVD has no text conditioning, so a clip cannot be "
            "directed by prompt — motion is a bucket id — and the graph is not "
            "yet wired into content_flow, which still expects a text-to-video "
            "shape. Clips are ~3 s at 8 fps."
        ),
    ),
    Division(
        key="socials",
        name="Socials",
        owns="Every platform account, and the single next human step on each.",
        produces=("social_account",),
        cadence="continuous — readiness changes as accounts progress",
        requires=(),
        routes=("GET /manager/roster", "POST /manager/personas/{id}/request-all"),
        workflow="",
        maturity=LIVE,
    ),
    Division(
        key="week_events",
        name="Week events",
        owns="The week's calendar: what goes out, where, and when.",
        produces=("scheduled_post",),
        cadence="per tick once SCHEDULER_ENABLED is true; off by default",
        requires=("llm", "publish"),
        routes=(
            "GET /personas/{id}/schedule",
            "POST /personas/{id}/schedule/generate",
            "POST /scheduler/tick",
        ),
        workflow="",
        maturity=PARTIAL,
        gap=(
            "Rows can be written and read back, so the calendar is real, and "
            "`POST /personas/{id}/schedule/generate` fills an empty week. The "
            "scheduler that fires a post when its time arrives is written too "
            "(app/scheduler.py) — it is the clock that is off: SCHEDULER_ENABLED "
            "is false by default, so nothing drains the week until an operator "
            "turns it on."
        ),
    ),
    Division(
        key="live_posts",
        name="Live posts",
        owns="Puts content in front of a paying stranger, on a platform the studio does not own.",
        produces=("published_post",),
        cadence="per tick once SCHEDULER_ENABLED is true; off by default",
        requires=("publish", "storage"),
        routes=(
            "POST /scheduled-posts/{post_id}/publish",
            "POST /scheduler/tick",
        ),
        workflow="app/providers/publish/fanvue.py",
        maturity=PARTIAL,
        gap=(
            "The publisher is written and refuses correctly when unconfigured "
            "(Fanvue is the only platform in this category with a public "
            "publishing API). The route exists — "
            "`POST /scheduled-posts/{post_id}/publish` — and "
            "`POST /scheduler/tick` can drive it unattended, but both need the "
            "operator's own OAuth credentials and neither moves without them: "
            "until those exist, this division is the one standing between the "
            "studio and any revenue at all."
        ),
    ),
)

BY_KEY: dict[str, Division] = {d.key: d for d in DIVISIONS}


def _status_key(value: object) -> str:
    """A group-by key that is the same whether the column holds an enum or text.

    `class ShootStatus(str, Enum)` means `str(ShootStatus.DRAFT)` is
    `"ShootStatus.DRAFT"`, not `"draft"` — the member's own `__str__` wins over
    `str`'s, so grouping on `str(status)` produced keys no `.get("draft")` would
    ever match and every count came back zero. `.value` is the value; a plain
    string column has no `.value` and is already what we want.
    """
    inner = getattr(value, "value", value)
    return str(inner).lower()


def _capability_state(required: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """(ready, missing) for a division's capabilities, without raising.

    Uses `resolve_optional` so an unconfigured division reports a reason rather
    than turning the whole status endpoint into a 503 — the point of this view is
    to show what is broken, so it must survive being asked about broken things.
    """
    if not required:
        return [], []
    from app.providers.registry import get_registry

    registry = get_registry()
    ready: list[str] = []
    missing: list[str] = []
    for capability in required:
        try:
            provider = registry.resolve_optional(capability)
        except Exception:
            provider = None
        (ready if provider is not None else missing).append(capability)
    return ready, missing


def _publish_state() -> tuple[bool, str]:
    """Whether the studio can actually post right now, and why not if it cannot.

    Deliberately re-derives the publisher's own ladder instead of calling
    `require_publisher()` — that raises HTTPException, which is right at a route
    and wrong here, where an unconfigured publisher is a fact to display.
    """
    from app.providers.publish import (
        PublishDisabled,
        PublishError,
        PublishNotConfigured,
        get_publisher,
    )

    try:
        publisher = get_publisher()
    except PublishNotConfigured as exc:
        return False, str(exc)
    except PublishDisabled as exc:
        return False, str(exc)
    except PublishError as exc:
        return False, str(exc)
    return True, publisher.name


async def _counts(db: AsyncSession, persona_id: str | None) -> tuple[dict[str, int], str]:
    """Live row counts per division, plus the reason if any could not be read.

    Scoping has a trap that is worth stating because it is invisible when it
    bites: `Shoot.persona_id` and `ContentPack.persona_id` are UUID columns and
    `ScheduledPost.persona_id` / `SocialAccount.persona_id` are `String(36)`, so
    the *same* filtered view needs a UUID for two tables and a string for the
    other two. Handing a UUID column a plain string does not match nothing — it
    raises `'str' object has no attribute 'hex'` from the bind processor.

    The failure is returned rather than swallowed. An earlier version caught
    every exception here and defaulted the counts to zero, which turned a
    genuinely broken query into a page confidently reporting "0 shoots" — the
    exact manufactured-calm this codebase refuses elsewhere. A count that cannot
    be read now says so in `counts_error`.
    """
    from uuid import UUID as _UUID

    pid_uuid = _UUID(persona_id) if persona_id else None
    pid_str = str(persona_id) if persona_id else None

    async def _grouped(column, id_column, id_value) -> dict[str, int]:
        query = select(column, func.count()).group_by(column)
        if id_value is not None:
            query = query.where(id_column == id_value)
        rows = await db.execute(query)
        return {_status_key(status): count for status, count in rows.all()}

    counts: dict[str, int] = {}
    try:
        shoots = await _grouped(Shoot.status, Shoot.persona_id, pid_uuid)
        counts["shoots"] = sum(shoots.values())
        counts["shoots_draft"] = shoots.get("draft", 0)
        counts["shoots_completed"] = shoots.get("completed", 0)

        packs = await _grouped(ContentPack.status, ContentPack.persona_id, pid_uuid)
        counts["packs"] = sum(packs.values())

        posts = await _grouped(ScheduledPost.status, ScheduledPost.persona_id, pid_str)
        counts["scheduled_posts"] = sum(posts.values())
        counts["scheduled"] = posts.get("scheduled", 0)
        counts["posted"] = posts.get("posted", 0)
        # A slot the clock closed out because its moment passed. Counted, not
        # dropped: an unlisted status is a post that vanished from the view.
        counts["missed"] = posts.get("missed", 0)

        accounts = await _grouped(SocialAccount.status, SocialAccount.persona_id, pid_str)
        counts["accounts"] = sum(accounts.values())
        counts["accounts_live"] = accounts.get("active", 0)

        people = select(func.count()).select_from(Persona)
        if pid_uuid is not None:
            people = people.where(Persona.id == pid_uuid)
        counts["personas"] = int((await db.execute(people)).scalar() or 0)
    except Exception as exc:
        return counts, f"counts unavailable: {type(exc).__name__}: {exc}"
    return counts, ""


def _division_payload(division: Division, counts: dict[str, int], counts_error: str = "") -> dict:
    ready, missing = _capability_state(division.requires)
    blocked_by: list[str] = []

    # `publish` gets its own more specific reason below, so the generic line
    # leaves it out — printing both said "unconfigured: publish" directly above
    # the sentence naming the credential that is actually missing.
    generic = [m for m in sorted(missing) if m != "publish"]
    if generic:
        blocked_by.append("unconfigured: " + ", ".join(generic))

    publish_ready, publish_note = (False, "")
    if "publish" in division.requires:
        publish_ready, publish_note = _publish_state()
        if not publish_ready:
            blocked_by.append(publish_note)

    # `can_act` is about the operator being able to *run* the division at all,
    # which is a different question from whether the division is fully wired.
    # An unbuilt division with every provider configured still cannot act.
    can_act = not blocked_by and division.maturity != UNBUILT

    payload = asdict(division)
    # asdict leaves these as tuples; JSON turns them into lists, so the direct
    # call and the route would otherwise disagree about the same field's type.
    payload["produces"] = list(division.produces)
    payload["requires"] = list(division.requires)
    payload["routes"] = list(division.routes)

    return {
        **payload,
        "capabilities_ready": ready,
        "capabilities_missing": missing,
        "publish_ready": publish_ready if "publish" in division.requires else None,
        "can_act": can_act,
        "blocked_by": blocked_by,
        "counts": _division_counts(division.key, counts),
        "counts_error": counts_error,
    }


def _division_counts(key: str, counts: dict[str, int]) -> dict[str, int]:
    """Only the numbers this division actually owns — a shared dict would leak
    account counts into the shoot planner and read as if it measured something."""
    if key == "shoots":
        return {"shoots": counts.get("shoots", 0), "completed": counts.get("shoots_completed", 0)}
    if key == "photo_shoot_planner":
        return {"planned": counts.get("shoots_draft", 0)}
    if key == "video_production":
        return {"packs": counts.get("packs", 0)}
    if key == "socials":
        return {"accounts": counts.get("accounts", 0), "live": counts.get("accounts_live", 0)}
    if key == "week_events":
        return {
            "scheduled": counts.get("scheduled", 0),
            "posted": counts.get("posted", 0),
            "missed": counts.get("missed", 0),
        }
    if key == "live_posts":
        return {"posted": counts.get("posted", 0)}
    if key == "look_planner":
        return {"personas": counts.get("personas", 0)}
    return {}


async def divisions_status(
    db: AsyncSession, persona_id: str | None = None
) -> dict:
    """Every division, with live readiness and counts. Read-only."""
    counts, counts_error = await _counts(db, persona_id)
    payloads = [_division_payload(d, counts, counts_error) for d in DIVISIONS]

    by_maturity: dict[str, int] = {}
    for row in payloads:
        by_maturity[row["maturity"]] = by_maturity.get(row["maturity"], 0) + 1

    actionable = [r for r in payloads if r["can_act"]]

    # The clock is the one division whose cadence is a live fact rather than a
    # static string, because it now exists but is off unless switched on. Asking
    # it directly is the difference between reporting "nothing runs on a timer"
    # and reporting that a timer is armed — the note below must not claim the
    # former when the latter is true.
    from app import scheduler as _scheduler

    clock = _scheduler.status()
    clock_on = bool(clock["enabled"])

    if clock_on:
        note = (
            "The clock is armed: due posts publish through the same path as the "
            "manual route, including its rule that a post is only marked posted "
            "on a real platform id. Posts whose window passed by more than "
            f"{clock['max_lateness_seconds']}s are marked 'missed', never "
            "published late. Nothing else here runs on a timer — the studio "
            "still produces only when a person acts."
        )
    else:
        note = (
            "A division is 'live' when a route reaches a real provider, never "
            "because this page exists. The clock exists but SCHEDULER_ENABLED is "
            "false, so nothing here runs on a timer and the studio produces only "
            "when a person acts. That is the honest state, and it is the gap "
            "this view exists to make visible."
        )

    return {
        "divisions": payloads,
        "summary": {
            "total": len(payloads),
            "can_act": len(actionable),
            "blocked": len(payloads) - len(actionable),
            "by_maturity": by_maturity,
            "scheduled_divisions": len(
                [r for r in payloads if not r["cadence"].startswith("on demand")
                 and not r["cadence"].startswith("continuous")
                 and not r["cadence"].startswith("not scheduled")]
            ),
        },
        "scheduler": clock,
        "note": note,
    }
