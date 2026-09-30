"""The inbox: the revenue engine on this platform, read and never written.

Fanvue's own documentation and every operator account agree that paid DMs, not
subscriptions, carry the money. So the studio needs to see its inbox — and the
whole reason this file exists is the second half of that sentence: seeing is all
it does. These tests hold three properties:

  * **a failure is never an empty inbox.** "No fan has written" is comfortable
    and false when the truth is "the token does not carry `read:chat`". The two
    are different states with different fixes.
  * **a paid message carries no amount.** The chat record has no price field, so
    the paid signal is its type. A number invented from a type would be a
    fabricated revenue figure that looks precise.
  * **nothing here can write.** The publisher's chat methods are `GET`s and the
    requested scopes exclude `write:chat`.
"""

from __future__ import annotations

import inspect

from app.conversations import real_conversations
from app.providers.publish import (
    PublishDisabled,
    PublishFailed,
    PublishNotConfigured,
)

CHATS = {
    "data": [
        {
            "createdAt": "2026-09-01",
            "lastMessageAt": "2026-09-30T09:00:00Z",
            "isRead": False,
            "isMuted": False,
            "unreadMessagesCount": 3,
            "online": True,
            "user": {
                "uuid": "1111aaaa-2222-bbbb-3333-444455556666",
                "handle": "fan_one",
                "displayName": "Fan One",
                "isTopSpender": True,
            },
            "lastMessage": {
                "text": "unlock please",
                "type": "CHAT_TEXT_REPLY",
                "senderRole": "FAN",
            },
        },
        {
            "createdAt": "2026-08-01",
            "lastMessageAt": "2026-08-02T09:00:00Z",
            # Manually marked unread by the creator: `isRead` is false and the
            # count is 0. A list driven by the count would show neither.
            "isRead": False,
            "isMuted": True,
            "unreadMessagesCount": 0,
            "online": False,
            "user": {"uuid": "2222bbbb-3333-cccc-4444-555566667777", "handle": "fan_two"},
            "lastMessage": {"text": None, "type": "LOCKED_MESSAGE_UNLOCKED", "senderRole": "CREATOR"},
        },
    ],
    "nextCursor": "cur-1",
    "total": 2,
}

MESSAGES = {
    "data": [
        {"uuid": "m1", "text": "hi", "sentAt": "2026-09-30T08:00:00Z", "type": "CHAT_TEXT_REPLY",
         "status": "SENT", "isRead": True, "sender": {"uuid": "u1", "handle": "fan_one"}},
        {"uuid": "m2", "text": None, "sentAt": "2026-09-30T09:00:00Z", "type": "LOCKED_MESSAGE_UNLOCKED",
         "status": "SENT", "isRead": False, "sender": {"uuid": "c1", "handle": "the_creator"},
         "hasMedia": True, "repliedMessage": {"uuid": "m1"}},
        {"uuid": "m3", "text": "thanks!", "sentAt": "2026-09-30T09:05:00Z", "type": "TIP",
         "status": "SENT", "isRead": False, "sender": {"uuid": "u1", "handle": "fan_one"}},
    ]
}

COUNTS = {
    "unreadChatsCount": 1,
    "unreadMessagesCount": 3,
    "unreadNotifications": {"newTip": 1},
}


class _FakePublisher:
    """A connected publisher whose inbox we control."""

    name = "fanvue"

    def __init__(self, *, chats=CHATS, counts=COUNTS, chats_raise=None, counts_raise=None,
                 accepts_timeout=True):
        self._chats = chats
        self._counts = counts
        self._chats_raise = chats_raise
        self._counts_raise = counts_raise
        self._accepts_timeout = accepts_timeout
        self.calls: list[dict] = []

    async def chats(self, **kwargs):
        if not self._accepts_timeout:
            kwargs.pop("timeout", None)
        self.calls.append(kwargs)
        if self._chats_raise is not None:
            raise self._chats_raise
        return self._chats

    async def unread_counts(self, **kwargs):
        if self._counts_raise is not None:
            raise self._counts_raise
        return self._counts


def _publisher(monkeypatch, publisher):
    monkeypatch.setattr("app.providers.publish.get_publisher", lambda: publisher)
    return publisher


def _raising(monkeypatch, exc):
    def _get():
        raise exc

    monkeypatch.setattr("app.providers.publish.get_publisher", _get)


# ── the shape of a reading ──────────────────────────────────────────────


def test_the_paid_types_are_the_two_the_platform_charges_for():
    from app.providers.publish.fanvue import PAID_MESSAGE_TYPES

    assert PAID_MESSAGE_TYPES == {"LOCKED_MESSAGE_UNLOCKED", "TIP"}


def test_a_paid_message_is_named_and_never_priced():
    """The chat record carries no price field. Attaching an amount to a paid
    signal would produce a revenue figure nothing reported — the worst kind,
    because it looks precise."""
    from app.providers.publish.fanvue import message_rows

    rows = {row["uuid"]: row for row in message_rows(MESSAGES)}
    assert rows["m2"]["is_paid"] is True
    assert rows["m3"]["is_paid"] is True
    assert rows["m1"]["is_paid"] is False
    for row in rows.values():
        assert not any("price" in key or "amount" in key for key in row), (
            f"a message row grew a money field: {sorted(row)}"
        )


def test_the_two_unread_signals_are_kept_apart():
    """`is_read` is authoritative for badges and `unread_messages_count` is zero
    for a chat the creator hand-marked unread. A list derived from either alone
    is wrong in a specific way, so both are carried."""
    from app.providers.publish.fanvue import chat_rows

    first, second = chat_rows(CHATS)
    assert first["is_read"] is False and first["unread_messages"] == 3
    # The case the count alone cannot express.
    assert second["is_read"] is False and second["unread_messages"] == 0
    assert second["is_muted"] is True


def test_a_chat_row_carries_the_fan_and_the_last_message():
    from app.providers.publish.fanvue import chat_rows

    first = chat_rows(CHATS)[0]
    assert first["handle"] == "fan_one"
    assert first["display_name"] == "Fan One"
    assert first["is_top_spender"] is True
    assert first["last_message_text"] == "unlock please"
    assert first["last_message_from_creator"] is False
    assert chat_rows(CHATS)[1]["last_message_from_creator"] is True


def test_misshapen_rows_are_skipped_not_crashed_on():
    """A shape change upstream must produce a thin row, never a 500 — this is
    the page an operator opens when something already looks wrong."""
    from app.providers.publish.fanvue import chat_rows, message_rows

    assert chat_rows({}) == []
    assert chat_rows({"data": None}) == []
    assert chat_rows({"data": ["nope", None]}) == []
    assert message_rows({"data": ["nope", None]}) == []
    assert message_rows({}) == []

    # An empty object is still a row: it is a conversation the platform listed,
    # and dropping it would undercount the inbox. Its fields are None, not "".
    rows = chat_rows({"data": [{}]})
    assert len(rows) == 1
    assert rows[0]["handle"] is None
    assert rows[0]["is_read"] is False
    assert rows[0]["last_message_is_paid"] is False


# ── the state machine ───────────────────────────────────────────────────


async def test_no_credentials_reads_as_not_configured(monkeypatch):
    _raising(monkeypatch, PublishNotConfigured("FANVUE_CLIENT_ID is empty"))
    result = await real_conversations()
    assert result["state"] == "not_configured"
    assert result["chats"] is None
    assert result["counts"] is None


async def test_credentials_without_the_arm_switch_read_as_disabled(monkeypatch):
    _raising(monkeypatch, PublishDisabled("FANVUE_PUBLISH_ENABLED is False"))
    result = await real_conversations()
    assert result["state"] == "disabled"
    assert result["chats"] is None


async def test_a_platform_with_no_chat_api_reads_as_unsupported(monkeypatch):
    class _Silent:
        name = "somewhere"

    _publisher(monkeypatch, _Silent())
    result = await real_conversations()
    assert result["state"] == "unsupported"
    assert result["provider"] == "somewhere"


async def test_a_failed_read_is_never_an_empty_inbox(monkeypatch):
    """The whole point of the module. An empty list means "nobody has written",
    which is the comfortable answer and the wrong one."""
    _publisher(monkeypatch, _FakePublisher(chats_raise=PublishFailed("HTTP 500 from Fanvue")))
    result = await real_conversations()
    assert result["state"] == "error"
    assert result["chats"] is None
    assert result["counts"] is None
    assert "500" in result["detail"]


async def test_a_token_without_the_chat_scope_says_so_and_says_what_to_do(monkeypatch):
    """403 is not 401. The token is fine; the grant is narrower than the code
    needs, and only the operator can widen it."""
    _publisher(
        monkeypatch,
        _FakePublisher(
            chats_raise=PublishFailed(
                "chat list failed: HTTP 403 from Fanvue. "
                '{"message":"missing required scope: read:chat"}'
            )
        ),
    )
    result = await real_conversations()
    assert result["state"] == "forbidden"
    assert "read:chat" in result["detail"]
    assert "econnect" in result["detail"], "the detail must name the action"
    assert result["chats"] is None


async def test_a_403_that_is_not_about_scope_stays_an_error(monkeypatch):
    """A creator who revoked the app also gets a 403. Re-authorizing with another
    scope does not fix that, so the two must not share a state."""
    _publisher(
        monkeypatch,
        _FakePublisher(chats_raise=PublishFailed("chat list failed: HTTP 403 from Fanvue. revoked")),
    )
    result = await real_conversations()
    assert result["state"] == "error"


async def test_a_readable_inbox_returns_the_platforms_own_conversations(monkeypatch):
    publisher = _publisher(monkeypatch, _FakePublisher())
    result = await real_conversations()
    assert result["state"] == "ok"
    assert result["provider"] == "fanvue"
    assert [c["handle"] for c in result["chats"]] == ["fan_one", "fan_two"]
    assert result["counts"]["unreadMessagesCount"] == 3
    assert "size" in publisher.calls[0]


async def test_the_reader_states_that_it_can_not_send(monkeypatch):
    """Read on every state, not only the successful one: what this cannot do is
    the property that matters most about it."""
    _raising(monkeypatch, PublishNotConfigured("nothing connected"))
    assert (await real_conversations())["can_send"] is False

    _publisher(monkeypatch, _FakePublisher())
    assert (await real_conversations())["can_send"] is False


async def test_a_count_failure_does_not_lose_the_conversations(monkeypatch):
    """The two halves fail independently. Losing a readable conversation list
    because a badge count failed would be throwing away the money path over a
    number the page can also render as unknown."""
    _publisher(monkeypatch, _FakePublisher(counts_raise=PublishFailed("HTTP 502")))
    result = await real_conversations()
    assert result["state"] == "ok"
    assert result["chats"]
    assert result["counts"] is None
    assert "unread counts unavailable" in result["detail"]


async def test_the_timeout_is_passed_through_and_an_older_adapter_still_reads(monkeypatch):
    publisher = _publisher(monkeypatch, _FakePublisher())
    await real_conversations(timeout=6.0)
    assert publisher.calls[0]["timeout"] == 6.0

    publisher = _publisher(monkeypatch, _FakePublisher(accepts_timeout=False))
    result = await real_conversations(timeout=6.0)
    assert result["state"] == "ok"
    assert "timeout" not in publisher.calls[0]


async def test_the_limit_is_clamped_to_what_the_platform_accepts(monkeypatch):
    """Fanvue 400s above 50, so a caller asking for 500 must not send 500."""
    publisher = _publisher(monkeypatch, _FakePublisher())
    await real_conversations(limit=500)
    assert publisher.calls[0]["size"] == 50


# ── what this cannot do ─────────────────────────────────────────────────


def test_the_chat_methods_are_all_reads():
    """A source-level check on the boundary itself. If a `POST` appears among
    these, the claim in every docstring above stops being true."""
    from app.providers.publish.fanvue import FanvuePublisher

    source = inspect.getsource(FanvuePublisher)
    chat_section = source[source.index("# ── conversations"):source.index("# ── token refresh")]
    assert "POST" not in chat_section
    assert "PUT" not in chat_section
    assert "PATCH" not in chat_section
    # The word appears in `chat_messages`'s docstring, where it explains why the
    # parameter is absent. What must never appear is the JSON key, which is what
    # sending it would require.
    assert '"markAsRead"' not in chat_section
    assert "markAsRead=" not in chat_section


def test_the_app_asks_for_read_chat_and_not_write_chat():
    """`write:chat` is the authority to speak to a paying fan as the creator.
    Nothing in this app does, so nothing in this app should hold it."""
    from app.providers.publish.fanvue import DEFAULT_SCOPES

    assert "read:chat" in DEFAULT_SCOPES
    assert "write:chat" not in DEFAULT_SCOPES
    # And every scope requested is one a method actually uses.
    assert "read:self" in DEFAULT_SCOPES
    assert "write:post" in DEFAULT_SCOPES


async def test_the_route_answers_and_names_the_platform(client):
    """Without monkeypatching: this is the operator's machine today. It must
    answer with a state, not 500 — the state is the whole value of the page."""
    resp = await client.get("/api/v1/publish/conversations")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] in {"not_configured", "disabled", "error", "unsupported", "forbidden"}
    assert body["chats"] is None
    assert body["can_send"] is False


async def test_the_route_takes_a_limit(client, monkeypatch):
    _publisher(monkeypatch, _FakePublisher())
    resp = await client.get("/api/v1/publish/conversations?limit=500")
    assert resp.status_code == 200
    assert len(resp.json()["chats"]) == 2


# ── the consent URL the scope change changes ────────────────────────────


def test_every_requested_scope_reaches_the_authorize_url():
    """The scope list is what the operator consents to. `read:chat` was added
    with the reader that uses it, and the URL is where it has to show up — a
    scope that never reaches the consent screen is a scope the app can never
    use, and the chat surface would then be permanently `forbidden` with no
    visible reason."""
    from app.providers.publish.fanvue import build_authorize_url, DEFAULT_SCOPES

    url = build_authorize_url(
        client_id="cid", redirect_uri="https://example.test/cb", state="s", challenge="c"
    )
    for scope in DEFAULT_SCOPES:
        assert scope in url.replace("%3A", ":"), f"{scope} never reaches the consent URL"
    assert "read:chat" in DEFAULT_SCOPES
