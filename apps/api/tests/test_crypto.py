"""Tests for credential encryption at rest (app.crypto)."""

from contextlib import contextmanager

from cryptography.fernet import Fernet

import pytest

from app import crypto
from app.crypto import decrypt_value, encrypt_value


def test_encrypt_roundtrip():
    secret = "S3cret-P@ss wörk 12"
    stored = encrypt_value(secret)
    assert stored != secret
    assert stored.startswith("enc1:")
    assert secret not in stored
    assert decrypt_value(stored) == secret


def test_encrypt_empty_value_passthrough():
    assert encrypt_value("") == ""
    assert encrypt_value(None) == ""
    assert decrypt_value("") == ""
    assert decrypt_value(None) == ""


def test_legacy_plaintext_fallback():
    assert decrypt_value("plaintext-password") == "plaintext-password"


def test_legacy_base64_looking_value_passthrough():
    # Legacy base64-obfuscated and base64-looking plaintext values are returned
    # raw — decrypt only ever touches enc1:-tagged ciphertext.
    assert decrypt_value("Zm9vYmFy") == "Zm9vYmFy"
    assert decrypt_value("dGVzdDEyMzQ=") == "dGVzdDEyMzQ="


def test_bad_ciphertext_does_not_raise():
    # Prefix present but payload is garbage (e.g. key rotated) -> "" not 500
    assert decrypt_value("enc1:not-a-valid-fernet-token") == ""


@contextmanager
def _with_fernet_key(monkeypatch, **settings_kwargs):
    """Point app.crypto at a fresh settings instance (patches the reference
    crypto.py actually calls, not just the app.config attribute)."""
    from app.config import Settings
    import app.crypto as crypto_mod

    monkeypatch.setattr(
        crypto, "get_settings", lambda: Settings(**settings_kwargs)
    )
    crypto_mod._fernet = None
    try:
        yield
    finally:
        crypto_mod._fernet = None


def test_key_rotation_via_setting(monkeypatch):
    key = Fernet.generate_key().decode()
    with _with_fernet_key(monkeypatch, ENCRYPTION_KEY=key):
        stored = encrypt_value("rotated-key-secret")
        assert decrypt_value(stored) == "rotated-key-secret"
        # Dev-fallback ciphertext written earlier no longer decrypts
        assert decrypt_value("enc1:not-from-this-key") == ""


def test_passphrase_setting_derives_working_key(monkeypatch):
    with _with_fernet_key(monkeypatch, ENCRYPTION_KEY="some-long-passphrase-2024"):
        stored = encrypt_value("passphrase-derived")
        assert decrypt_value(stored) == "passphrase-derived"


def test_production_without_key_fails_fast(monkeypatch):
    from app.config import Settings
    import app.crypto as crypto_mod

    monkeypatch.setattr(
        crypto_mod, "get_settings",
        lambda: Settings(ENVIRONMENT="production", ENCRYPTION_KEY=""),
    )
    crypto_mod._fernet = None
    try:
        with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
            encrypt_value("anything")
    finally:
        crypto_mod._fernet = None  # restore dev-fallback fernet for other tests