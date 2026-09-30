"""Publishing a produced pack to a real platform.

This is the piece the codebase has never had. Everything else in the line ends
at this API's own database: `ContentPack` → `Product` is a local listing, and
`/social-accounts/{id}/post` writes a row and reports `posted` without a
platform client anywhere in the file. A pack that is priced and published into
the fan surface is sellable *here*; this package is what makes it exist
somewhere a person who is not on this machine can buy it.

Two rules, both learned from this codebase's own documented failures:

* **No local-only success.** A provider that cannot reach the platform raises;
  it never writes a row and returns `ok`. `app/delivery.py` documents the
  endpoints this rule was written to kill.
* **Credentials are not permission.** `FANVUE_PUBLISH_ENABLED` is a separate
  switch from the token, so a credential copied in for a read-only check cannot
  silently become authority to post.

Only one adapter exists — Fanvue, the only platform in this category with a
public publishing API and rules that permit a disclosed AI persona. The
Playwright auto-signup path (`providers/browser_signup.py`) is deliberately not
part of this: publishing through an official API on an account the operator
created is a different act from automating a platform's signup flow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# The platform this package can post to. There is exactly one adapter here (see
# the module docstring), and naming it separately matters for the states where
# no publisher object exists: "not configured" still has to answer *which*
# platform's price floor and rules apply, and a caller that cannot get that
# answer will hardcode its own.
PROVIDER_NAME = "fanvue"


class PublishError(Exception):
    """Base for every publishing failure. Never raised as a bare Exception."""


class PublishNotConfigured(PublishError):
    """No credentials for the platform — the caller records `blocked_gate`."""


class PublishDisabled(PublishError):
    """Credentials exist but the operator has not armed publishing."""


class PublishFailed(PublishError):
    """The platform was reached and refused, or the request failed."""


@dataclass
class PublishResult:
    """What a publish attempt actually did.

    `ok=False` always carries `error`; there is no partial-success state where
    a post is assumed to exist. `post_uuid` is populated only from the
    platform's own response, never synthesised locally.
    """

    ok: bool
    post_uuid: str = ""
    error: str = ""
    detail: dict = field(default_factory=dict)


class PublishProvider:
    """A platform that a finished pack can be posted to.

    Kept as a plain class with the same shape as `providers/base.py`'s ABCs but
    without the inheritance, because these adapters are constructed from
    settings rather than resolved through the capability registry — the registry
    keys on `<CAPABILITY>_PROVIDER`, and there is exactly one implementation
    here.
    """

    name = "publish"

    # Whether this platform can attach a price to a post. Declared by the
    # adapter rather than kept as a list of platform names elsewhere, so it
    # cannot drift from the code that actually sends the price. It exists
    # because "no price" is not a neutral default on a platform that sells:
    # it publishes the post for free, silently, which is how a calendar of
    # sellable content can earn nothing while looking healthy.
    supports_price = False

    # The lowest price this platform accepts, in the same minor units the
    # publisher takes. Zero on a platform that cannot charge, where the floor
    # means nothing.
    min_price_minor = 0

    async def health_check(self) -> tuple[bool, str]:
        """Return `(reachable, detail)`. Must not raise."""
        raise NotImplementedError

    async def create_post(
        self,
        *,
        text: str,
        media_paths: list,
        price_minor: int | None = None,
        audience: str = "subscribers",
        publish_at=None,
    ) -> PublishResult:
        """Post `text` with `media_paths` attached.

        `price_minor` is in cents, matching the platform's own unit and the
        ledger's minor units — no conversion happens anywhere in this path, so
        there is no place for a factor-of-100 to hide.
        """
        raise NotImplementedError


def get_publisher() -> PublishProvider:
    """Build the configured publisher, or raise `PublishNotConfigured`.

    Resolution order is deliberate: configuration first, then the arm switch.
    An operator who has not connected an account gets "not configured"; one who
    has connected but not armed gets "disabled". Both are distinct from a
    successful post, and neither is a silent no-op.

    Tokens resolve **stored-then-configured**: a pair rotated at runtime is
    newer than the `.env` line it came from, so the store wins when it has one.
    The publisher is built with a hook that writes a fresh pair back, which is
    the half that was missing — see `app/token_store.py`.
    """
    from app.config import get_settings

    settings = get_settings()
    if not getattr(settings, "FANVUE_CLIENT_ID", ""):
        raise PublishNotConfigured(
            "FANVUE_CLIENT_ID is empty. Fanvue uses OAuth 2.0 with no static API "
            "keys, so the publisher needs an authorized token, not just an "
            "account — see app/providers/publish/fanvue.py for the flow."
        )

    from app import token_store

    stored = token_store.load_tokens(token_store.PROVIDER_FANVUE)
    access_token = stored.get("access_token") or settings.FANVUE_ACCESS_TOKEN
    refresh_token = stored.get("refresh_token") or settings.FANVUE_REFRESH_TOKEN

    if not access_token:
        raise PublishNotConfigured(
            "No Fanvue access token: neither storage/oauth_tokens.json nor "
            "FANVUE_ACCESS_TOKEN has one. Complete the OAuth authorization "
            "(PKCE) and set the token before publishing."
        )
    if not getattr(settings, "FANVUE_CREATOR_UUID", ""):
        raise PublishNotConfigured(
            "FANVUE_CREATOR_UUID is empty. Every media and post path is keyed by "
            "the creator's own user UUID; there is no discovery fallback."
        )
    if not getattr(settings, "FANVUE_PUBLISH_ENABLED", False):
        raise PublishDisabled(
            "Credentials are present but FANVUE_PUBLISH_ENABLED is False. "
            "Publishing is armed separately from being configured on purpose: "
            "set it to true only when the line should actually post."
        )

    from app.providers.publish.fanvue import FanvuePublisher

    def _persist(access: str, refresh: str) -> None:
        try:
            token_store.save_tokens(
                token_store.PROVIDER_FANVUE,
                {
                    "access_token": access,
                    "refresh_token": refresh,
                    "obtained_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except OSError as exc:
            # Not fatal to this call — the request in flight still has a good
            # token — but it is exactly the silent-stop failure mode, so it is
            # logged loudly rather than swallowed. The next process start will
            # fall back to the `.env` pair and 401 when that has expired too.
            logger.error(
                "rotated Fanvue token could not be persisted (%s); publishing "
                "will stop at the next restart",
                exc,
            )

    return FanvuePublisher(
        client_id=settings.FANVUE_CLIENT_ID,
        client_secret=settings.FANVUE_CLIENT_SECRET,
        access_token=access_token,
        refresh_token=refresh_token,
        creator_uuid=settings.FANVUE_CREATOR_UUID,
        api_base=settings.FANVUE_API_BASE,
        auth_url=settings.FANVUE_AUTH_URL,
        api_version=settings.FANVUE_API_VERSION,
        on_tokens_refreshed=_persist,
    )


async def publisher_state() -> dict:
    """Can this deployment post right now, and if not, which of the three is it.

    `routes/publish.py` renders this as `/publish/status` and `app/readiness.py`
    reads it to decide whether money can start arriving. One implementation, two
    callers — "configured" and "armed" judged separately in two places is how
    one of them starts reporting ready while the other is still blocked.

    Never raises. An unconfigured publisher is an answer about the deployment,
    not a failure of the request, and an endpoint that 500s because nobody has
    connected an account yet is an endpoint operators learn to ignore.

    The four states are distinct for a reason — each is a different next action:

      * `not_configured` — no client id, no token, or no creator uuid.
      * `disabled`       — everything present, `FANVUE_PUBLISH_ENABLED` off.
      * `unhealthy`      — armed, but the token did not authenticate.
      * `ready`          — armed and the platform answered.
    """
    try:
        publisher = get_publisher()
    except PublishNotConfigured as exc:
        return {
            "ready": False,
            "state": "not_configured",
            "provider": PROVIDER_NAME,
            "configured": False,
            "armed": False,
            "detail": str(exc),
        }
    except PublishDisabled as exc:
        # Configured but not armed: the credentials are all there, so this is a
        # deliberate choice by the operator rather than a missing prerequisite.
        return {
            "ready": False,
            "state": "disabled",
            "provider": PROVIDER_NAME,
            "configured": True,
            "armed": False,
            "detail": str(exc),
        }
    except PublishError as exc:
        return {
            "ready": False,
            "state": "error",
            "provider": PROVIDER_NAME,
            "configured": False,
            "armed": False,
            "detail": str(exc),
        }

    healthy, detail = await publisher.health_check()
    return {
        "ready": healthy,
        "state": "ready" if healthy else "unhealthy",
        "provider": publisher.name,
        "configured": True,
        "armed": True,
        "detail": detail,
    }


__all__ = [
    "PROVIDER_NAME",
    "PublishError",
    "PublishNotConfigured",
    "PublishDisabled",
    "PublishFailed",
    "PublishResult",
    "PublishProvider",
    "get_publisher",
    "publisher_state",
]
