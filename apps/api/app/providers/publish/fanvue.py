"""Fanvue creator API — media upload and post creation.

Fanvue is the one platform in this category that (a) has a real public
publishing API and (b) explicitly permits a disclosed AI persona as a
first-class account. Both matter: a persona that is *only* sellable on a
surface this app controls earns nothing, and a persona posted to a platform
whose rules forbid it is a banned account waiting to happen.

The API is OAuth 2.0 only — authorization code with PKCE and rotating refresh
tokens, no static keys — and every path is keyed by the creator's own user
UUID. Posting is three calls in sequence:

    1. POST   /v1/creators/{uuid}/media/uploads            → upload session
    2. GET    /v1/creators/{uuid}/media/uploads/{id}/parts/urls
       PUT    <each signed URL>                            → the bytes
       PATCH  /v1/creators/{uuid}/media/uploads/{id}       → finalize
    3. POST   /v1/creators/{uuid}/posts                    → the post

Endpoints verified against https://api.fanvue.com/docs/openapi-v1.json
(189 paths); the versioned header `X-Fanvue-API-Version` is required on every
request — Fanvue versions by header, not by path.

Two units get confused in this kind of integration, so both are pinned here:
prices are in **cents** (Fanvue's `price` minimum is 300, i.e. $3.00, and it
requires media), and part numbers are **1-based**.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
import math
import secrets
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.providers.publish import (
    PublishFailed,
    PublishResult,
    PublishProvider,
)

logger = logging.getLogger(__name__)

# Fanvue refuses a priced post below $3.00 and refuses a price with no media at
# all. Checked here rather than at the call site so the refusal is one place.
MIN_PRICE_MINOR = 300

# Every money figure Fanvue *reports* is in USD cents, and the spec says so on
# each field: "Pre-fee earnings, in USD cents", "Creator's cut after Fanvue fees,
# in USD cents", and on the per-transaction rows "converted to USD cents".
#
# The summary response has no top-level currency field at all, so the unit is
# documented here or it is nowhere — and a figure read as dollars when it is
# cents overstates by 100×, which on a dashboard is indistinguishable from a
# very good month. `earnings_totals` is the one boundary that divides by 100.
#
# Note this is the *reporting* currency, not the account's: per-transaction rows
# carry a `currency` field that is "the local currency the fan originally paid
# in" and is explicitly "informational only" while the amounts beside it are
# already USD. Reading that field as the unit of its own row would label a
# dollar figure as BRL — see `FanvuePublisher.earnings`.
CURRENCY = "USD"

# The authorization endpoint. The token endpoint is a constructor argument
# (`auth_url`) because it is the one a test injects a transport for; this one is
# only ever navigated to by a browser, so it stays a constant with a settings
# override at the route that builds the URL.
DEFAULT_AUTHORIZE_URL = "https://auth.fanvue.com/oauth2/auth"

# The scopes this app can actually use, and nothing else.
#
# Taken from the published spec's own list (read:self, read:creator, write:media,
# write:post, read:insights, …). Each one is load-bearing:
#   read:self     — what `health_check` reads (GET /v1/users/me). Without it the
#                   "is this token alive" answer is a 403 rather than a 401 and
#                   every diagnosis downstream starts from the wrong fact.
#   read:creator  — the creator record the media and post paths hang off.
#   write:media   — upload.   write:post — publish.
#   read:insights — the earnings endpoints. The only scope here that is not
#                   about producing, and the only one that can answer whether any
#                   money arrived. A grant without it can post forever and never
#                   be able to say what it earned.
#   read:chat     — the conversation list and messages. Paid DMs are this
#                   platform's revenue engine, so the inbox is where the money
#                   is; `unread_counts`/`chats`/`chat_messages` read it and
#                   nothing writes it. **`write:chat` is absent on purpose** —
#                   nothing in this app sends a message, and a scope that can
#                   speak to a paying fan as the creator is not something to
#                   hold against the day it might be wanted.
#
# Requesting more than this would be asking for authority the code does not use,
# which is how a leaked token does damage nobody can explain afterwards. Every
# scope above is exercised by a method in this file.
DEFAULT_SCOPES = (
    "read:self",
    "read:creator",
    "write:media",
    "write:post",
    "read:insights",
    "read:chat",
)


def pkce_pair() -> tuple[str, str]:
    """`(code_verifier, code_challenge)` — S256.

    The verifier is 43–128 unreserved characters per RFC 7636; 64 bytes of
    `token_urlsafe` lands in the middle of that. The challenge is the SHA-256 of
    the verifier, base64url, unpadded — padding is the usual reason a correct
    verifier is refused, because `=` is not an unreserved character.
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    challenge: str,
    scopes=DEFAULT_SCOPES,
    authorize_url: str = DEFAULT_AUTHORIZE_URL,
) -> str:
    """The URL the operator's browser is sent to.

    The spec at api.fanvue.com/docs does not document PKCE parameters — its
    schemas list client_id, response_type, state and scope only. They are sent
    anyway: the platform's own OAuth description names PKCE, and a server that
    does not implement it ignores an unknown query parameter rather than
    refusing the request. Leaving them out is the one version of this that fails
    closed for a reason nobody can debug.
    """
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": " ".join(scopes),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{authorize_url}?{urlencode(query)}"


async def exchange_code(
    *,
    token_url: str,
    client_id: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    client_secret: str = "",
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Trade an authorization code for the first token pair.

    `client_secret` is sent only when there is one, matching `refresh()`: a
    public client authenticates with PKCE alone, a confidential one with the
    secret as well, and sending an empty secret to a public client's token
    endpoint is a 401 that reads like bad credentials.

    Returns the raw token body. Persisting is the caller's job — the same
    division as `refresh()`, and for the same reason.
    """
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    if client_secret:
        payload["client_secret"] = client_secret

    if client is not None:
        response = await client.post(token_url, data=payload, timeout=_API_TIMEOUT)
    else:
        async with httpx.AsyncClient() as own_client:
            response = await own_client.post(token_url, data=payload, timeout=_API_TIMEOUT)

    if response.status_code >= 400:
        body = response.text[:600] if response.text else ""
        raise PublishFailed(
            f"authorization code exchange failed: HTTP {response.status_code} "
            f"from Fanvue's token endpoint. {body}"
        )
    body = response.json()
    if "access_token" not in body:
        raise PublishFailed(f"token endpoint returned no access_token: {body}")
    return body


# 1.6 GB, the documented ceiling for a single media object.
MAX_MEDIA_BYTES = 1_610_612_736

AUDIENCES = ("subscribers", "followers-and-subscribers")

# Generous but finite: a 1.5 MB SDXL plate uploads in well under a second, and
# the S3 part PUT is the slow leg on a bad connection.
_API_TIMEOUT = 30.0
_UPLOAD_TIMEOUT = 180.0


def _media_type_for(path: Path) -> str:
    """Fanvue's four media kinds, derived from the file itself."""
    suffix = path.suffix.lower()
    if suffix in {".mp4", ".mov", ".m4v", ".webm"}:
        return "video"
    if suffix in {".mp3", ".wav", ".m4a", ".aac", ".flac"}:
        return "audio"
    if suffix in {".pdf", ".txt", ".md"}:
        return "document"
    return "image"


def _content_type_for(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _iso(value) -> str:
    """Fanvue's date params are ISO 8601 and offset-aware; a naive datetime is
    resolved against the server's clock, which moves a day boundary."""
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _num(value):
    """A number from Fanvue's JSON, or None. Absent is not zero."""
    return float(value) if isinstance(value, (int, float)) else None


def _money(value):
    """A reported amount — USD cents — as dollars, or None when absent.

    The only cents-to-dollars conversion in the codebase. Every other figure this
    app holds is dollars: the wallet (`Wallet.currency` is USD), the per-post
    price the operator states, and every number the web app renders. So a caller
    that skipped this step would be wrong by two decimal places, uniformly, in
    the direction that reads as success.

    Deliberately not `round`: rounding belongs where a total is summed, not on
    each contributing figure, or a hundred half-cent rows stop adding up.
    """
    amount = _num(value)
    return None if amount is None else amount / 100


def _find_uuid(body) -> str:
    """The first UUID-shaped value under one of the known id keys, or "".

    Walks nested objects and lists, because a self endpoint is as likely to
    answer `{"user": {"uuid": …}}` as a flat record.

    The value must *look* like a UUID — 36 characters, four dashes. That check
    is what makes the tolerance safe rather than a guess: a record whose
    `userId` is a handle, or whose `uuid` is some other resource's key, yields
    "" instead of a plausible-looking string that would silently key every
    upload to the wrong account.
    """
    if isinstance(body, dict):
        for key in FanvuePublisher._UUID_KEYS:
            value = body.get(key)
            if isinstance(value, str) and _looks_like_uuid(value):
                return value
        for value in body.values():
            if isinstance(value, (dict, list)):
                found = _find_uuid(value)
                if found:
                    return found
    elif isinstance(body, list):
        for item in body:
            found = _find_uuid(item)
            if found:
                return found
    return ""


def _looks_like_uuid(value: str) -> bool:
    return len(value) == 36 and value.count("-") == 4


def _currency_code(value) -> str:
    """A three-letter currency code, or "" when the value is not one.

    Deliberately not defaulted here. Fanvue's earnings figures reach a UI that
    prefixes its own symbol, and the app's own money (`Wallet.currency`) is USD
    while the web app renders Rand — so a default in this function would print
    one currency's symbol in front of another's number. "" means "this field did
    not state a code", which is a fact about the field, not about the money.

    The unit of Fanvue's figures is a separate question, and it is answered by
    `CURRENCY` from the spec rather than by this field, which in practice does
    not exist on the summary response.
    """
    if isinstance(value, str):
        code = value.strip().upper()
        if len(code) == 3 and code.isalpha():
            return code
    return ""


def earnings_totals(summary: dict) -> dict:
    """Flatten an earnings summary to the numbers a dashboard actually shows.

    The nesting is Fanvue's and may change; a caller reading
    `totals.allTime.net` in six places is six places to fix. Every figure is
    `None` when the branch is missing rather than `0` — "we earned nothing" and
    "the platform did not tell us" are different answers, and only one of them
    is good news.

    **Every amount is converted from Fanvue's USD cents to dollars here**, and
    this is the only place that happens (`_money`). A caller therefore never
    sees a cent, and never has to know it was one.

    `by_source.messages` is the one to watch: on this platform paid DMs are the
    revenue engine, and a subscription-only reading understates it badly.
    """
    totals = summary.get("totals") or {}
    all_time = totals.get("allTime") or {}
    month = totals.get("thisMonth") or {}
    by_source = summary.get("breakdownBySource") or {}

    def _pair(node):
        node = node or {}
        return {"gross": _money(node.get("gross")), "net": _money(node.get("net"))}

    return {
        # Which currency the figures above are in. The platform's own field is
        # read first so a future response that states one wins, and `CURRENCY`
        # is the fallback because the spec documents the unit even when the
        # payload omits the field. Either way a renderer never has to guess, and
        # the guess it would otherwise make is Rand.
        "currency": _currency_code(summary.get("currency") or totals.get("currency"))
        or CURRENCY,
        "all_time": _pair(all_time),
        "this_month": _pair(month),
        "previous_month": {
            "gross": _money(month.get("previousMonthGross")),
            "net": _money(month.get("previousMonthNet")),
        },
        "by_source": {
            name: _pair(by_source.get(name))
            for name in ("subs", "messages", "posts", "tips", "referrals", "renewals", "other")
        },
        # The series the platform will hand over, bucketed day or week. Kept in
        # the same converted unit as everything else — a chart drawn from raw
        # cents next to a headline in dollars is a chart nobody can read. Each
        # point keeps the bucket's own start time; a bucket the platform
        # reported without one is dropped rather than plotted at an invented
        # x-position.
        "over_time": [
            {
                "period_start": point.get("periodStart"),
                "gross": _money(point.get("gross")),
                "net": _money(point.get("net")),
            }
            for point in (summary.get("overTime") or [])
            if isinstance(point, dict) and point.get("periodStart")
        ],
        "period": summary.get("period"),
    }


# What produced a row, from `EarningSource`. The three that are *not* fan money
# are called out because a revenue total that includes them is not revenue:
# referral, affiliate and giveaway rows are creator rewards that no fan paid for.
NOT_FAN_PAID = frozenset({"referral", "affiliate", "giveaway"})


def earnings_rows(payload: dict) -> list[dict]:
    """Flatten a page of `GET /v1/insights/earnings` into dollars.

    The same boundary as `earnings_totals`, for the same reason: the endpoint
    answers in USD cents and every figure this app holds is dollars, so a caller
    handed the raw rows has to remember which — and the one that forgets is off
    by 100× in the direction that looks like success.

    Three amounts per row, and they are genuinely different questions:

    * `fan_paid` — `total` in the spec, what left the fan's card: the creator's
      price plus the fan's tax plus their side of the transaction fee. This is
      the figure to use when a number has to match a statement.
    * `gross` — the creator's price the earning is calculated from.
    * `net` — what the creator keeps after Fanvue's fee.

    `currency` is deliberately **not** carried through as the row's unit. Fanvue
    puts the fan's local currency there and says it is "informational only" while
    the amounts beside it are already USD — so a row labelled from that field
    would present a converted dollar figure as, say, a Brazilian sale. The code
    that matters for these amounts is `CURRENCY`.

    `fan_paid` is `None` where the platform did not report `total`, and rows are
    returned even when every amount is missing: a transaction that happened but
    whose size is unknown is not the same as no transaction.
    """
    rows = payload.get("data") if isinstance(payload, dict) else None
    flat: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        source = row.get("source")
        flat.append(
            {
                "date": row.get("date"),
                "source": source,
                "source_is_fan_money": source not in NOT_FAN_PAID,
                "fan_paid": _money(row.get("total")),
                "gross": _money(row.get("gross")),
                "net": _money(row.get("net")),
                "status": row.get("transactionOrderStatus"),
                "order_id": row.get("transactionOrderId"),
                "reverses": row.get("reversedTransactionOrderId"),
            }
        )
    return flat


# The message types that mean a fan was charged for something. Everything else
# in the enum is conversation, marketing or platform housekeeping.
#
# There is no amount here, and that is not an omission: Fanvue's message record
# carries no price field, so a paid message is *visible* as a paid message and
# its size is only knowable from the earnings rows. A number invented from a
# message type would be the worst kind of fabricated revenue figure — one that
# looks precise.
PAID_MESSAGE_TYPES = frozenset({"LOCKED_MESSAGE_UNLOCKED", "TIP"})


def chat_rows(payload: dict) -> list[dict]:
    """Flatten a page of `GET /v1/chats` into what a conversation list needs.

    Fanvue's chat record carries the fan, the last message and two different
    unread signals, and picking the wrong one produces a badge that is wrong in
    a specific way:

    * `is_read` — authoritative for badge state. False when unread messages
      exist **or** when the creator manually marked the chat unread.
    * `unread_messages_count` — zero in that second case, so a list driven by
      the count alone shows a chat the creator flagged as read-looking.

    Both are kept and neither is derived from the other.

    `last_message_is_paid` is inferred from the message *type* only. No price is
    attached, because the platform does not report one here.
    """
    items = payload.get("data") if isinstance(payload, dict) else None
    rows: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        fan = item.get("user") or {}
        last = item.get("lastMessage") or {}
        rows.append(
            {
                "user_uuid": fan.get("uuid"),
                "handle": fan.get("handle"),
                "display_name": fan.get("displayName"),
                "is_top_spender": bool(fan.get("isTopSpender")),
                "is_read": bool(item.get("isRead")),
                "is_muted": bool(item.get("isMuted")),
                "unread_messages": item.get("unreadMessagesCount") or 0,
                "online": bool(item.get("online")),
                "last_message_at": item.get("lastMessageAt"),
                "last_message_text": last.get("text"),
                "last_message_type": last.get("type"),
                "last_message_is_paid": last.get("type") in PAID_MESSAGE_TYPES,
                "last_message_from_creator": last.get("senderRole") == "CREATOR",
            }
        )
    return rows


def message_rows(payload: dict) -> list[dict]:
    """Flatten a page of `GET /v1/chats/{userUuid}/messages`.

    The same shape as `chat_rows` one level down: what was said, who said it, and
    whether it was the paid kind. `is_paid` is again derived from the message
    type and carries no amount.
    """
    items = payload.get("data") if isinstance(payload, dict) else None
    rows: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        sender = item.get("sender") or {}
        rows.append(
            {
                "uuid": item.get("uuid"),
                "text": item.get("text"),
                "sent_at": item.get("sentAt"),
                "type": item.get("type"),
                "is_paid": item.get("type") in PAID_MESSAGE_TYPES,
                "status": item.get("status"),
                "is_read": bool(item.get("isRead")),
                "sender_uuid": sender.get("uuid"),
                "sender_handle": sender.get("handle"),
                "has_media": bool(item.get("hasMedia")),
                "replied_to": (item.get("repliedMessage") or {}).get("uuid"),
            }
        )
    return rows


class FanvuePublisher(PublishProvider):
    """Upload media and create posts on one connected Fanvue creator account."""

    name = "fanvue"

    # Fanvue posts take a `price` (in cents, minimum 300) and a post with a
    # price is a paid post. So an unpriced post here is a giveaway, not a
    # default — `app/publishing.py` refuses one unless it is marked free on
    # purpose.
    supports_price = True

    # The floor, declared where the price is sent rather than restated at each
    # caller. `app/publishing.py` reads it to refuse a price Fanvue would
    # reject, and the scheduler reads it to refuse a stated default that is
    # below what the platform accepts — one number, two guards.
    min_price_minor = MIN_PRICE_MINOR

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str = "",
        access_token: str,
        refresh_token: str = "",
        creator_uuid: str,
        api_base: str = "https://api.fanvue.com",
        auth_url: str = "https://auth.fanvue.com/oauth2/token",
        api_version: str = "2025-06-26",
        client: httpx.AsyncClient | None = None,
        on_tokens_refreshed: Callable[[str, str], None] | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.creator_uuid = creator_uuid
        self.api_base = api_base.rstrip("/")
        self.auth_url = auth_url
        self.api_version = api_version
        # Injected in tests so nothing here can reach the network by accident.
        self._client = client
        # Called with (access_token, refresh_token) after a rotation. Persisting
        # is the caller's job and this is the only moment the new pair exists
        # outside the request, so the hook is the difference between a refresh
        # that lasts and one that dies with the process.
        self._on_tokens_refreshed = on_tokens_refreshed

    # ── plumbing ──────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "X-Fanvue-API-Version": self.api_version,
        }

    def _url(self, path: str) -> str:
        return f"{self.api_base}{path}"

    async def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        kwargs.setdefault("timeout", _API_TIMEOUT)
        if self._client is not None:
            return await self._client.request(
                method, self._url(path), headers=self._headers(), **kwargs
            )
        async with httpx.AsyncClient() as client:
            return await client.request(
                method, self._url(path), headers=self._headers(), **kwargs
            )

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """One authenticated call, with the token never leaked into the error.

        A 401 with a refresh token in hand is the ordinary end of an access
        token's life, not a finding — so it is rotated and retried, exactly
        once. Retrying is safe here because every call this wraps carries a
        `json` body; there is no read stream to have been consumed by the first
        attempt. The retry is bounded at one so a genuinely revoked account
        surfaces as a 401 instead of a refresh loop.
        """
        response = await self._send(method, path, **kwargs)
        if response.status_code != 401 or not self.refresh_token:
            return response
        try:
            await self.refresh()
        except (PublishFailed, httpx.HTTPError) as exc:
            # The original 401 is the more useful answer: the refresh itself
            # failing means the account needs re-authorizing, which is a
            # decision for the operator and not something a retry can fix.
            logger.warning("Fanvue token refresh failed, returning the 401: %s", exc)
            return response
        return await self._send(method, path, **kwargs)

    @staticmethod
    def _failure(response: httpx.Response, what: str) -> PublishFailed:
        """Turn a non-2xx into an error that says what the platform said.

        The body is included because Fanvue returns a structured reason; the
        Authorization header is not, because this string reaches logs and the
        audit trail.
        """
        body = response.text[:600] if response.text else ""
        return PublishFailed(
            f"{what} failed: HTTP {response.status_code} from Fanvue. {body}"
        )

    # ── health ────────────────────────────────────────────────────────

    async def health_check(self) -> tuple[bool, str]:
        """`GET /v1/users/me` — proves the token works, not just that a host resolves."""
        try:
            response = await self._request("GET", "/v1/users/me")
        except httpx.HTTPError as exc:
            return False, f"could not reach Fanvue: {exc}"
        if response.status_code == 401:
            return False, "Fanvue returned 401 — the access token is expired or revoked"
        if response.status_code >= 400:
            return False, f"Fanvue returned HTTP {response.status_code}"
        return True, "authenticated"

    # The keys a self-describing user record could plausibly carry its own id
    # under, most specific first, and deliberately **without a bare `id`** — on a
    # record that had both, `id` would win over `creatorUuid` and point every
    # upload at the wrong resource. This is tolerance, not knowledge: the
    # published spec excerpt we can read documents `/v1/agencies/*` schemas but
    # not the `/me` response, so the shape is unverified and an unrecognised one
    # answers "" rather than guessing. An empty answer costs the operator one
    # copy-paste; a wrong one builds every media and post path against a
    # different account and 404s.
    _UUID_KEYS = ("uuid", "creatorUuid", "creatorUUID", "userUuid", "userId")

    async def creator_uuid_of_token(self) -> str:
        """The creator UUID this token belongs to, or "" if the response does not
        say. Read from the token itself rather than trusted from configuration —
        that is the whole point: configuration is the thing being checked."""
        try:
            response = await self._request("GET", "/v1/users/me")
        except httpx.HTTPError:
            return ""
        if response.status_code >= 400:
            return ""
        try:
            body = response.json()
        except ValueError:
            return ""
        return _find_uuid(body)

    # ── earnings ──────────────────────────────────────────────────────
    #
    # Read-only, and the only place in the app that can say whether money is
    # coming in. The app's own `/fan` ledger is simulated (the sole payment
    # processor is `fake`), so a Fanvue sale is the one real dollar this
    # codebase can observe — and it is observed here or nowhere, because
    # nothing else reads it back.

    async def earnings_summary(
        self,
        *,
        start=None,
        end=None,
        granularity="day",
        timezone_name=None,
        timeout: float | None = None,
    ) -> dict:
        """`GET /v1/insights/earnings/summary` — totals and a breakdown by source.

        Token-scoped rather than keyed by `creator_uuid`: both forms exist, and
        this one cannot drift from whatever uuid the token actually belongs to.

        `timeout` overrides the module default for callers that cannot afford to
        wait — a dashboard request must not hang for thirty seconds on someone
        else's API just to render a figure it can also render as unknown.
        """
        params: dict = {}
        if start:
            params["startDate"] = _iso(start)
        if end:
            params["endDate"] = _iso(end)
        if granularity:
            params["granularity"] = granularity
        if timezone_name:
            params["timezone"] = timezone_name

        extra = {} if timeout is None else {"timeout": timeout}
        response = await self._request(
            "GET", "/v1/insights/earnings/summary", params=params, **extra
        )
        if response.status_code >= 400:
            raise self._failure(response, "earnings summary")
        return response.json()

    async def earnings(
        self, *, start=None, end=None, source=None, cursor=None, size: int = 100
    ) -> dict:
        """`GET /v1/insights/earnings` — one row per transaction, as Fanvue sent it.

        This is the transport: the payload comes back in Fanvue's USD cents and
        is returned unchanged, so **a caller must flatten it through
        `earnings_rows`** rather than reading these fields directly. That is the
        one place the per-row `total`/`gross`/`net` become dollars, and it is
        also what turns `source` into `source_is_fan_money` — referral,
        affiliate and giveaway rows are creator rewards that no fan paid for, so
        summing them as revenue overstates what anyone actually bought.

        `transactionOrderStatus` (`availableForPayout` vs `pendingBalance`) is
        what lets "earned" and "payable" be told apart instead of conflated.
        """
        params: dict = {}
        if start:
            params["startDate"] = _iso(start)
        if end:
            params["endDate"] = _iso(end)
        if source:
            params["source"] = [source] if isinstance(source, str) else list(source)
        if cursor:
            params["cursor"] = cursor
        if size:
            params["size"] = size

        response = await self._request("GET", "/v1/insights/earnings", params=params)
        if response.status_code >= 400:
            raise self._failure(response, "earnings")
        return response.json()

    # ── conversations (read-only) ─────────────────────────────────────
    #
    # Paid DMs are this platform's revenue engine, so a studio that can publish
    # but cannot see its inbox is watching the wrong end of the business. These
    # four calls are the reading half. **Nothing here sends, replies, marks
    # anything read, or changes any state** — the account's own inbox is not
    # something an unattended process should be able to alter, and `write:chat`
    # is deliberately not among the scopes this app requests.

    async def unread_counts(self, *, timeout: float | None = None) -> dict:
        """`GET /v1/chats/unread` — three counts, no conversation content.

        `timeout` is for callers on a request path a human is waiting on; the
        module default is thirty seconds and a dashboard must not spend them.
        """
        extra = {} if timeout is None else {"timeout": timeout}
        response = await self._request("GET", "/v1/chats/unread", **extra)
        if response.status_code >= 400:
            raise self._failure(response, "unread counts")
        return response.json()

    async def chats(
        self,
        *,
        cursor: str | None = None,
        size: int = 50,
        filter: list[str] | str | None = None,
        search: str | None = None,
        sort_by: str | None = None,
        timeout: float | None = None,
    ) -> dict:
        """`GET /v1/chats` — the conversation list, most recently active first.

        `size` is capped at 50 by the platform; passing more is a 400, so it is
        clamped here rather than left for the caller to discover.
        """
        params: dict = {"size": max(1, min(int(size), 50))}
        if cursor:
            params["cursor"] = cursor
        if filter:
            params["filter"] = [filter] if isinstance(filter, str) else list(filter)
        if search:
            params["search"] = search
        if sort_by:
            params["sortBy"] = sort_by

        extra = {} if timeout is None else {"timeout": timeout}
        response = await self._request("GET", "/v1/chats", params=params, **extra)
        if response.status_code >= 400:
            raise self._failure(response, "chat list")
        return response.json()

    async def chat_messages(self, user_uuid: str, *, limit: int = 50) -> dict:
        """`GET /v1/chats/{userUuid}/messages` — one conversation's messages.

        `markAsRead` is left at the platform's default of `false` and is not a
        parameter here. Retrieving messages is a read; marking them read is a
        change to the creator's inbox that would make an unread fan look
        answered. That side effect has no business happening as a by-product of
        a dashboard load.
        """
        params = {"limit": max(1, min(int(limit), 50))}
        response = await self._request(
            "GET", f"/v1/chats/{user_uuid}/messages", params=params
        )
        if response.status_code >= 400:
            raise self._failure(response, "chat messages")
        return response.json()

    async def chat_media(self, user_uuid: str) -> dict:
        """`GET /v1/chats/{userUuid}/media` — media shared in one conversation."""
        response = await self._request("GET", f"/v1/chats/{user_uuid}/media")
        if response.status_code >= 400:
            raise self._failure(response, "chat media")
        return response.json()

    # ── token refresh ─────────────────────────────────────────────────

    async def refresh(self) -> dict:
        """Exchange the refresh token for a new pair.

        Returns the new tokens; **the caller persists them**. Refreshing does
        not write to settings or the database, because a token rotated inside a
        provider instance and never saved is the failure mode where publishing
        works until the process restarts and then silently stops.

        `on_tokens_refreshed`, when the publisher was built with one, is called
        with the new pair the moment it exists — that hook is what actually
        closes the loop, and `get_publisher()` wires it to the token store.
        """
        if not self.refresh_token:
            raise PublishFailed("no refresh token is configured — re-authorize")
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
        }
        if self.client_secret:
            payload["client_secret"] = self.client_secret

        # The injected client is used when there is one — otherwise a test that
        # injects a transport to keep the API leg off the network would still
        # reach the real auth host from here, and pass or fail on its latency.
        if self._client is not None:
            response = await self._client.post(
                self.auth_url, data=payload, timeout=_API_TIMEOUT
            )
        else:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.auth_url, data=payload, timeout=_API_TIMEOUT
                )
        if response.status_code >= 400:
            raise self._failure(response, "token refresh")
        body = response.json()
        if "access_token" not in body:
            raise PublishFailed(f"token refresh returned no access_token: {body}")
        self.access_token = body["access_token"]
        if body.get("refresh_token"):
            self.refresh_token = body["refresh_token"]
        if self._on_tokens_refreshed is not None:
            self._on_tokens_refreshed(self.access_token, self.refresh_token)
        return body

    # ── media upload ──────────────────────────────────────────────────

    async def upload_media(self, path) -> str:
        """Upload one file and return its `mediaUuid`.

        Follows Fanvue's session flow exactly: open a session, ask for signed
        part URLs, PUT the bytes, then finalize with the ETags the storage
        backend returned. The finalize call is what makes the media usable — a
        post referencing a session that was never finalized is rejected, which
        is why the ETag of every part is required rather than optional.
        """
        path = Path(path)
        if not path.is_file():
            raise PublishFailed(f"media file does not exist: {path}")
        data = path.read_bytes()
        if not data:
            raise PublishFailed(f"media file is empty: {path}")
        if len(data) > MAX_MEDIA_BYTES:
            raise PublishFailed(
                f"{path.name} is {len(data)} bytes, over Fanvue's "
                f"{MAX_MEDIA_BYTES}-byte single-media limit"
            )

        media_type = _media_type_for(path)
        session_path = f"/v1/creators/{self.creator_uuid}/media/uploads"

        response = await self._request(
            "POST",
            session_path,
            json={
                "name": path.stem,
                "filename": path.name,
                "mediaType": media_type,
                "sizeBytes": len(data),
            },
        )
        if response.status_code >= 400:
            raise self._failure(response, f"opening an upload for {path.name}")
        session = response.json()
        for key in ("mediaUuid", "uploadId"):
            if not session.get(key):
                raise PublishFailed(f"upload session had no {key}: {session}")

        part_size = int(session.get("partSize") or len(data))
        total_parts = int(session.get("totalParts") or math.ceil(len(data) / part_size))

        urls_response = await self._request(
            "GET",
            f"{session_path}/{session['uploadId']}/parts/urls",
            params={"from": 1, "to": total_parts},
        )
        if urls_response.status_code >= 400:
            raise self._failure(urls_response, "fetching part URLs")
        parts = urls_response.json().get("parts") or []
        if len(parts) != total_parts:
            raise PublishFailed(
                f"asked for {total_parts} part URLs, got {len(parts)} — refusing to "
                "finalize an upload with missing parts"
            )

        uploaded: list[dict] = []
        for part in parts:
            number = int(part["partNumber"])
            chunk = data[(number - 1) * part_size : number * part_size]
            put = await self._put_part(part["url"], chunk, path)
            if put.status_code >= 400:
                raise self._failure(put, f"uploading part {number} of {path.name}")
            etag = put.headers.get("etag") or put.headers.get("ETag")
            if not etag:
                raise PublishFailed(
                    f"part {number} of {path.name} uploaded but returned no ETag; "
                    "Fanvue cannot finalize the upload without it"
                )
            # Capitalised keys: this is what the OpenAPI schema declares for the
            # finalize body, not the camelCase the other endpoints use.
            uploaded.append({"PartNumber": number, "ETag": etag.strip('"')})

        finalize = await self._request(
            "PATCH",
            f"{session_path}/{session['uploadId']}",
            json={"parts": uploaded},
        )
        if finalize.status_code >= 400:
            raise self._failure(finalize, f"finalizing the upload of {path.name}")

        return str(session["mediaUuid"])

    async def _put_part(self, url: str, chunk: bytes, path: Path) -> httpx.Response:
        """PUT one part to the signed URL.

        The signed URL is absolute and must be used verbatim — re-signing it or
        rebasing it onto `api_base` is what makes an S3 upload 403.
        """
        content_type = _content_type_for(path)
        if self._client is not None:
            return await self._client.put(
                url,
                content=chunk,
                headers={"Content-Type": content_type},
                timeout=_UPLOAD_TIMEOUT,
            )
        async with httpx.AsyncClient() as client:
            return await client.put(
                url,
                content=chunk,
                headers={"Content-Type": content_type},
                timeout=_UPLOAD_TIMEOUT,
            )

    # ── posting ───────────────────────────────────────────────────────

    async def create_post(
        self,
        *,
        text: str,
        media_paths: list | None = None,
        price_minor: int | None = None,
        audience: str = "subscribers",
        publish_at: datetime | None = None,
    ) -> PublishResult:
        """Upload `media_paths`, then create the post that references them.

        Returns `PublishResult(ok=False)` with a reason instead of raising for
        the two cases that are the caller's mistake rather than the platform's:
        an invalid audience, and a price the platform will reject. Everything
        else raises — a network failure must not be indistinguishable from a
        refusal.
        """
        media_paths = list(media_paths or [])

        if audience not in AUDIENCES:
            return PublishResult(
                ok=False,
                error=f"audience must be one of {AUDIENCES}, got {audience!r}",
            )
        if price_minor is not None:
            if price_minor < MIN_PRICE_MINOR:
                return PublishResult(
                    ok=False,
                    error=(
                        f"price_minor {price_minor} is below Fanvue's "
                        f"{MIN_PRICE_MINOR}-cent minimum for a priced post"
                    ),
                )
            if not media_paths:
                return PublishResult(
                    ok=False,
                    error="a priced post must carry media — Fanvue rejects a "
                          "paid post with none",
                )
        if not text and not media_paths:
            return PublishResult(
                ok=False, error="a post needs text or media; refusing to post nothing"
            )

        media_uuids: list[str] = []
        for path in media_paths:
            media_uuids.append(await self.upload_media(path))

        payload: dict = {"audience": audience}
        if text:
            payload["text"] = text
        if media_uuids:
            payload["mediaUuids"] = media_uuids
            # The first uploaded item is the preview, so the post shows the
            # same cover the local `Product` uses.
            payload["mediaPreviewUuid"] = media_uuids[0]
        if price_minor is not None:
            payload["price"] = price_minor
        if publish_at is not None:
            payload["publishAt"] = publish_at.isoformat()

        response = await self._request(
            "POST", f"/v1/creators/{self.creator_uuid}/posts", json=payload
        )
        if response.status_code == 401:
            raise PublishFailed(
                "Fanvue returned 401 creating the post — the access token is "
                "expired or revoked; refresh it and retry"
            )
        if response.status_code >= 400:
            raise self._failure(response, "creating the post")

        body = response.json()
        post_uuid = body.get("uuid", "")
        if not post_uuid:
            # The post may exist; the response is simply not what was expected.
            # Reporting ok=True here would be a guess, so it is a failure with
            # the response attached.
            raise PublishFailed(f"Fanvue returned no post uuid: {body}")

        return PublishResult(
            ok=True,
            post_uuid=str(post_uuid),
            detail={
                "media_uuids": media_uuids,
                "audience": audience,
                "price": price_minor,
                "published_at": body.get("publishedAt"),
                "publish_at": body.get("publishAt"),
            },
        )
