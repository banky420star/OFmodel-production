"""Symmetric encryption for credentials at rest (Fernet / AES-128-CBC + HMAC-SHA256).

Used to encrypt platform passwords, mail.tm email credentials, and session-cookie
metadata before they hit the database. String columns stay string columns — no
schema migration — ciphertext is simply stored in the existing text fields, tagged
with an `enc1:` prefix so it can be distinguished from legacy values.

Backward compatibility: values written before this module existed (plaintext, or
the old base64 "obfuscation") do not carry the prefix and are returned as-is —
the raw stored value is exactly what legacy consumers saw — so reads never 500.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

logger = logging.getLogger(__name__)

# Prefix marking values encrypted by this module. Fernet tokens themselves start
# with "gAAAAA"; the explicit prefix makes the version/unambiguity check trivial.
_PREFIX = "enc1:"

# Documented dev fallback so tests and local dev work with zero setup.
# NEVER rely on this in production — set the ENCRYPTION_KEY env var.
_DEV_FALLBACK_KEY_MATERIAL = "persona-studio-dev-encryption-key-do-not-use-in-production"

_fernet: Fernet | None = None


def _fernet_from_key_setting(key_setting: str) -> Fernet:
    """Accept either a raw Fernet key (32-byte url-safe b64) or any passphrase."""
    try:
        return Fernet(key_setting.encode())
    except (ValueError, binascii.Error):
        # Not a valid Fernet key — treat the setting as a passphrase and derive.
        derived = base64.urlsafe_b64encode(
            hashlib.sha256(key_setting.encode()).digest()
        )
        return Fernet(derived)


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key_setting = get_settings().ENCRYPTION_KEY
        if key_setting:
            _fernet = _fernet_from_key_setting(key_setting)
        else:
            settings = get_settings()
            if settings.ENVIRONMENT == "production":
                raise RuntimeError(
                    "ENCRYPTION_KEY must be set when ENVIRONMENT=production — "
                    "refusing to encrypt credentials with the dev fallback key."
                )
            logger.warning(
                "ENCRYPTION_KEY is not set — credentials are encrypted with a "
                "documented dev-only fallback key. Set ENCRYPTION_KEY before "
                "deploying to production."
            )
            _fernet = _fernet_from_key_setting(_DEV_FALLBACK_KEY_MATERIAL)
    return _fernet


def encrypt_value(plaintext: str | None) -> str:
    """Encrypt a credential for storage. Empty values pass through untouched."""
    if not plaintext:
        return ""
    token = _get_fernet().encrypt(plaintext.encode()).decode()
    return _PREFIX + token


def decrypt_value(value: str | None) -> str:
    """Decrypt a stored credential, falling back gracefully for legacy values.

    - empty string  -> empty string
    - `enc1:` tag   -> Fernet-decrypt (on failure — e.g. key rotated — return ""
                       and log a warning rather than raising)
    - anything else -> legacy plaintext/base64, returned as-is (the raw stored
                       value is what legacy code wrote and what consumers saw)
    """
    if not value:
        return ""
    if value.startswith(_PREFIX):
        try:
            return _get_fernet().decrypt(value[len(_PREFIX):].encode()).decode()
        except InvalidToken:
            logger.warning(
                "Stored credential could not be decrypted with the current "
                "ENCRYPTION_KEY (key rotated or dev fallback changed); "
                "returning empty value."
            )
            return ""
    return value