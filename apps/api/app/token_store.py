"""Where a rotated OAuth token goes so it outlives the process that rotated it.

`FanvuePublisher.refresh()` already returns the new pair and says what has to
happen to it — "the caller persists them" — and nothing did. That is a quiet
failure with a specific shape: publishing works, the access token expires an
hour later, and every call from then on returns 401, which reads like a revoked
account rather than a token that was rotated in memory and dropped.

Tokens live in `storage/oauth_tokens.json`, keyed by provider, mode 0600. A file
rather than a row because `get_publisher()` is a plain sync factory called from
a request handler, the scheduler and the divisions report alike; handing it a
database session would ripple through every one of those call sites to store an
operator secret that is not application data. `SocialAccount.metadata_json` is
the existing home for a *connected social account's* tokens — that is where
TikTok's flow writes, and it stays right for a flow that runs per persona.
Fanvue's credentials are workspace-level, so they get a workspace-level store.

Nothing here is encrypted: the file is the same trust level as the `.env` it
replaces, and it is written 0600 from the moment it is created rather than
chmod'ed afterwards. Loading never raises — a corrupt store must not stop the
app from booting, and the fallback is the `.env` value it was overlaying.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

FILENAME = "oauth_tokens.json"

PROVIDER_FANVUE = "fanvue"


def store_path() -> Path:
    """Resolved at call time so tests can point it at a tmp_path."""
    from app import paths

    return paths.STORAGE_ROOT / FILENAME


def _read_all() -> dict:
    path = store_path()
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        logger.warning("could not read %s: %s", path, exc)
        return {}
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        # A truncated write or a hand-edit. Reporting it as empty is the useful
        # answer; raising here would take down whatever was starting up.
        logger.warning("%s is not valid JSON; treating it as empty", path)
        return {}
    return data if isinstance(data, dict) else {}


def load_tokens(provider: str) -> dict:
    """The stored tokens for one provider, or `{}`.

    Empty is the honest answer for "never stored" and for "unreadable" both —
    the caller falls back to configuration, which is where these came from
    before anything was ever rotated.
    """
    record = _read_all().get(provider)
    return record if isinstance(record, dict) else {}


def _write_all(data: dict) -> None:
    """Write the whole store 0600, via a sibling temp file.

    The temp-and-replace is not decoration: a crash mid-write would otherwise
    leave a truncated file, which `_read_all` reports as *no tokens at all* —
    so a torn write would silently drop the refresh token rather than fail.
    """
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{FILENAME}.")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        # mkstemp already creates 0600; set it again because the umask on some
        # platforms is not the only thing that can widen a fresh file.
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save_tokens(provider: str, tokens: dict) -> None:
    """Replace one provider's record. Creates the file 0600 if it is not there."""
    data = _read_all()
    data[provider] = tokens
    _write_all(data)


def clear_tokens(provider: str) -> None:
    """Forget one provider's tokens — for when a refresh is refused for good."""
    data = _read_all()
    if provider in data:
        del data[provider]
        _write_all(data)


# ── the in-flight half of an authorization handshake ─────────────────────
#
# A PKCE verifier and a CSRF nonce exist only between `connect` and `callback` —
# one operator's browser round-trip. They are persisted anyway, because that
# round-trip is not necessarily one process: an operator who restarts the API
# between the two (or whose editor hot-reloads it) would otherwise come back to a
# "state check failed" page with a grant that was perfectly valid, and the only
# way to explain it is that the server forgot.
#
# Kept under a separate top-level key from the tokens, and never read by
# `load_tokens`. A pending handshake is not a grant: `get_publisher()` must not
# be able to find an access token in a record that was written before any code
# was ever exchanged, which is exactly what a shared namespace would eventually
# do the first time a field was named the same on both sides.

PENDING_KEY = "pending"


def save_pending(provider: str, record: dict) -> None:
    data = _read_all()
    pending = data.get(PENDING_KEY)
    if not isinstance(pending, dict):
        pending = {}
    pending[provider] = record
    data[PENDING_KEY] = pending
    _write_all(data)


def load_pending(provider: str) -> dict:
    """The stored handshake for one provider, or `{}`."""
    pending = _read_all().get(PENDING_KEY)
    if not isinstance(pending, dict):
        return {}
    record = pending.get(provider)
    return record if isinstance(record, dict) else {}


def clear_pending(provider: str) -> None:
    data = _read_all()
    pending = data.get(PENDING_KEY)
    if isinstance(pending, dict) and provider in pending:
        del pending[provider]
        data[PENDING_KEY] = pending
        _write_all(data)
