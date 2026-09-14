"""Always-on social worker — real platform-API engagement loop.

Runs inside the API lifespan next to the manager heartbeat. For every social
account whose platform has an OFFICIAL API and a stored credential, it
periodically performs one real action:

  sync_profile  — pull the account's real numbers (followers / posts) through
                  the official API and persist them on the account row.
  check         — authenticated reachability probe; failures land in
                  last_sync_result and the account shows exactly why.

Honesty rules:
- Only platforms in PLATFORM_CAPABILITIES with has_official_api are driven.
  Fansly / OnlyFans: no official API → no client, ever.
- A sync either really happened or is recorded as failed with the platform's
  own error. Nothing is invented to look busy.
- Credentials live on the account row (api_token / metadata_json); they are
  stored exactly as the existing socials routes store them. Secret rotation
  is an operator action.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SocialAccount
from app.providers.social_apis import (
    PLATFORM_CAPABILITIES,
    FanvueClient,
    XClient,
    ApiStatus,
)

logger = logging.getLogger("persona.social_worker")

# Per-account cadence: one action per platform per cycle, spaced by the
# worker interval (minutes). Defaults align with free-tier budgets.
INTERVAL_SECONDS = 300          # one pass every 5 minutes
PER_CYCLE_BUDGET = 25           # max accounts touched per pass


def platform_client(account: SocialAccount):
    """Build the official-API client for an account, or None when the
    platform has no official API / no credential is stored."""
    if not account.api_token:
        return None
    platform = (account.platform or "").lower()
    if platform == "fanvue":
        return FanvueClient(account.api_token)
    if platform in ("twitter", "x"):
        return XClient(account.api_token)
    return None  # gate-level platforms: separate credential flow, not yet connected


def _account_has_api(account: SocialAccount) -> bool:
    cap = PLATFORM_CAPABILITIES.get((account.platform or "").lower())
    return bool(cap and cap.get("has_official_api"))


def _record(account: SocialAccount, ok: bool, detail: str) -> None:
    account.api_connected = ok
    account.metadata_json = {
        **(account.metadata_json or {}),
        "last_sync_result": {"ok": ok, "detail": detail,
                             "at": datetime.now(timezone.utc).isoformat()},
    }


async def _sync_account(db: AsyncSession, account: SocialAccount) -> dict:
    """One real API action for one account. Returns a ledger row."""
    platform = (account.platform or "").lower()
    cap = PLATFORM_CAPABILITIES.get(platform, {})

    if not cap.get("has_official_api"):
        result = {"ok": False, "action": "unsupported",
                  "detail": cap.get("restricted_note") or "no official API"}
        _record(account, False, result["detail"])
        return {**result, "account": account.username}

    client = platform_client(account)
    if client is None:
        result = {"ok": False, "action": "not_configured",
                  "detail": "no API credential stored for this account yet"}
        _record(account, False, result["detail"])
        return {**result, "account": account.username}

    status: ApiStatus = await client.check()
    if not status.ok:
        _record(account, False, f"{status.status_kind}: {status.detail}")
        return {"ok": False, "action": "check", "account": account.username,
                "detail": f"{status.status_kind}: {status.detail}"}

    # Authenticated — do the real profile sync.
    snap = await client.profile()
    if not snap.ok:
        _record(account, False, snap.error)
        return {"ok": False, "action": "sync_profile", "account": account.username,
                "detail": snap.error}

    # Fanvue snapshot shape: chat sample; X: /users/me payload.
    data = snap.data or {}
    me = data.get("me") or {}
    if me:
        account.followers = int(me.get("followers_count") or me.get("public_metrics", {}).get("followers_count") or account.followers or 0)
        account.posts_count = int(me.get("tweet_count") or me.get("public_metrics", {}).get("tweet_count") or account.posts_count or 0)
        account.api_connected = True

    _record(account, True, f"sync ok via {platform} official API")
    return {"ok": True, "action": "sync_profile", "account": account.username,
            "detail": f"{platform} official API reachable"}


async def worker_pass(db: AsyncSession) -> dict:
    """One pass: drive every API-capable account once. Returns the pass ledger."""
    res = await db.execute(select(SocialAccount).order_by(SocialAccount.created_at))
    accounts = list(res.scalars().all())

    eligible = [a for a in accounts if _account_has_api(a) and a.status in ("active", "approved")]
    if not eligible:
        return {"ran": False, "reason": "no accounts with official-API platforms", "results": []}

    results: list[dict] = []
    for account in eligible[:PER_CYCLE_BUDGET]:
        try:
            row = await _sync_account(db, account)
        except Exception as exc:  # never kill the pass for one account
            row = {"ok": False, "account": account.username,
                   "detail": f"worker exception: {exc}", "action": "exception"}
            _record(account, False, row["detail"])
        results.append(row)

    await db.commit()
    succeeded = sum(1 for r in results if r.get("ok"))
    return {
        "ran": True,
        "total": len(accounts),
        "eligible": len(eligible),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }


async def social_worker_loop() -> None:
    """The always-on loop. DB-state driven, sleeps INTERVAL_SECONDS between passes."""
    from app.database import AsyncSessionLocal

    while True:
        try:
            import asyncio
            await asyncio.sleep(INTERVAL_SECONDS)
            async with AsyncSessionLocal() as db:
                summary = await worker_pass(db)
                if summary.get("ran") and (summary.get("failed") or summary.get("succeeded")):
                    logger.info("social worker pass: %s", summary)
        except Exception:  # noqa: BLE001 — the loop must survive anything
            logger.exception("social worker cycle failed")


async def status(db: AsyncSession) -> dict:
    """What the worker sees right now — for the UI panel."""
    res = await db.execute(select(SocialAccount))
    accounts = list(res.scalars().all())

    platforms = []
    for pid, cap in PLATFORM_CAPABILITIES.items():
        n = sum(1 for a in accounts if (a.platform or "").lower() == pid)
        platforms.append({
            "id": pid,
            "label": cap["label"],
            "has_official_api": cap.get("has_official_api", False),
            "auth": cap.get("auth", ""),
            "credential_source": cap.get("credential_source", ""),
            "restricted_note": cap.get("restricted_note", ""),
            "flags": cap.get("flags", {}),
            "accounts": n,
        })

    by_platform = {}
    for a in accounts:
        by_platform.setdefault((a.platform or "").lower(), []).append({
            "id": a.id,
            "username": a.username,
            "status": a.status,
            "api_connected": a.api_connected,
            "has_token": bool(a.api_token),
            "last_sync": (a.metadata_json or {}).get("last_sync_result"),
        })

    return {"platforms": platforms, "accounts": by_platform}
