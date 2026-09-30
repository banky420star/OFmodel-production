"""The inbox, which is where this platform's money is — read-only, and only read.

Fanvue's own documentation and every operator account of it say the same thing:
paid DMs, not subscriptions, are the revenue engine. So a studio wired to that
platform that can publish and cannot see its inbox is optimised on the wrong
side of the business.

This module reads. It does not send, reply, or mark anything read, and there is
no code path here that could — the publisher's chat methods are `GET` only and
`app/providers/publish/fanvue.py` requests no `write:chat` scope. That is a
deliberate boundary rather than an unfinished one: replying to a paying fan as
the creator is an outward-facing act with a person on the other end, and it is
not something an unattended reader should be able to do by accident.

Same rules as `app/earnings.py`, for the same reasons:

* **Never render an unreadable inbox as an empty one.** "No fans have written"
  and "we could not ask" are different facts, and an empty list is the more
  comfortable one. Every failure is a state with a reason.
* **Never present a number the platform did not give.** A paid message is
  visible as a paid message (`LOCKED_MESSAGE_UNLOCKED`, `TIP`) and its *size* is
  only knowable from the earnings rows — so this module reports the signal and
  refuses to attach an amount to it.
"""

from __future__ import annotations

# The states an inbox reading can be in, and each is a different next action.
#   not_configured — no credentials at all
#   disabled       — credentials present, publishing not armed
#   unsupported    — a connected platform with no chat endpoint
#   forbidden      — the token is valid but was not granted `read:chat`
#   error          — the platform was reached and the read failed
#   ok             — read from the platform
#
# `forbidden` is separated from `error` because the fix is different in kind:
# an error is a retry, and this is a re-authorization with a wider scope. Fanvue
# answers 403 for a scoped-out endpoint and 401 for a dead token, and collapsing
# the two sends the operator to refresh a token that is working fine.
UNKNOWN_NOTE = (
    "the inbox has not been read — nothing here can see a fan's messages until "
    "the platform is connected, and this app never sends one"
)


def _blank(state: str, *, provider: str = "", detail: str = "") -> dict:
    """A reading with no conversations.

    `chats`, `messages` and `counts` are all `None` rather than empty structures.
    An empty list reads as "nobody has written"; `None` with a state reads as
    "we do not know", and only the second one is true here.
    """
    return {
        "state": state,
        "provider": provider,
        "detail": detail,
        "counts": None,
        "chats": None,
        "note": UNKNOWN_NOTE,
        # Stated on every reading, not only the failures: the reader's most
        # important property is what it cannot do.
        "can_send": False,
    }


def _scope_refused(detail: str) -> bool:
    """Whether the platform's own words say the *scope* is missing.

    Matched on the response body rather than the status code alone, because a
    403 also covers a creator who has revoked the app entirely — and that one is
    not fixed by re-authorizing with another scope.
    """
    lowered = detail.lower()
    return "scope" in lowered or "forbidden" in lowered or "permission" in lowered


async def real_conversations(*, limit: int = 25, timeout: float | None = None) -> dict:
    """The inbox as the platform reports it, or why that is unknown.

    Never raises, for the same reason `real_earnings` never raises: an endpoint
    that 500s because nobody has connected an account is an endpoint an operator
    learns to ignore, and then the one time it matters they are not looking.

    `limit` bounds how many conversations are returned, not how many exist.
    """
    from app.providers.publish import (
        PublishDisabled,
        PublishError,
        PublishNotConfigured,
        get_publisher,
    )
    from app.providers.publish.fanvue import chat_rows

    try:
        publisher = get_publisher()
    except PublishNotConfigured as exc:
        return _blank("not_configured", detail=str(exc))
    except PublishDisabled as exc:
        return _blank("disabled", detail=str(exc))
    except PublishError as exc:
        return _blank("error", detail=str(exc))

    reader = getattr(publisher, "chats", None)
    if reader is None:
        return _blank(
            "unsupported",
            provider=publisher.name,
            detail=f"{publisher.name} exposes no chat endpoint",
        )

    # The counts are worth having even when the conversation list fails, so they
    # are read first and their failure is not fatal to the whole reading.
    counts, counts_error = None, ""
    counter = getattr(publisher, "unread_counts", None)
    if counter is not None:
        try:
            counts = await counter()
        except PublishError as exc:
            counts_error = str(exc)

    try:
        if timeout is None:
            page = await reader(size=max(1, min(int(limit), 50)))
        else:
            # A caller on a request path a human is waiting on passes a short
            # one; the publisher's own default is thirty seconds.
            page = await reader(size=max(1, min(int(limit), 50)), timeout=timeout)
    except TypeError:
        try:
            page = await reader(size=max(1, min(int(limit), 50)))
        except PublishError as exc:
            state, why = _classify(exc)
            return _blank(state, provider=publisher.name, detail=why)
    except PublishError as exc:
        state, why = _classify(exc)
        return _blank(state, provider=publisher.name, detail=why)

    chats = chat_rows(page)
    reading = {
        "state": "ok",
        "provider": publisher.name,
        "detail": f"{len(chats)} conversation(s) from Fanvue",
        "counts": counts,
        "chats": chats,
        "note": (
            "Read from Fanvue's chat API. `unread_messages` is zero for a chat "
            "the creator marked unread by hand, so `is_read` is the badge. A "
            "paid message is identified by its type and carries no amount — the "
            "money is in the earnings rows, not here."
        ),
        "can_send": False,
    }
    if counts_error:
        # Said rather than swallowed: the two halves of this reading can fail
        # independently, and a page that shows a conversation list with no
        # counts should say which one broke.
        reading["detail"] += f" (unread counts unavailable: {counts_error[:200]})"
    return reading


def _classify(exc: Exception) -> tuple[str, str]:
    """`(state, detail)` for a failed read.

    A 403 caused by a token that never carried `read:chat` is the one failure
    here whose fix is a *re-authorization* rather than a retry, so it gets its
    own state. The scope list this app asks for includes `read:chat`; a token
    issued before that was added will 403 on every chat call until the operator
    connects again.
    """
    detail = str(exc)
    if "403" in detail and _scope_refused(detail):
        return "forbidden", (
            f"{detail} — the token does not carry `read:chat`. Reconnect the "
            "platform to grant it; a token issued before that scope was "
            "requested will keep refusing every chat call."
        )
    return "error", detail
