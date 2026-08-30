"""Persona Studio — MinIO / S3 storage helper."""

from __future__ import annotations
from minio import Minio
from app.config import get_settings


_settings = None
_client = None


def get_storage_client() -> Minio:
    global _settings, _client
    settings = get_settings()
    if _client is None or _settings != settings:
        _client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        _settings = settings
        # Ensure bucket exists
        if not _client.bucket_exists(settings.MINIO_BUCKET):
            _client.make_bucket(settings.MINIO_BUCKET)
    return _client


def get_bucket() -> str:
    return get_settings().MINIO_BUCKET
