"""Persona Studio — Local filesystem storage provider.

Stores files on the local hard drive instead of MinIO/S3.
Files are saved under STORAGE_DIR (default: ./storage/).
"""

from __future__ import annotations
import asyncio
import os
from pathlib import Path

from app.providers.base import StorageProvider, ProviderResult


class FileSystemStorageProvider(StorageProvider):
    """Local filesystem storage — no external services needed."""

    def __init__(self, base_dir: str = ""):
        self._base = Path(base_dir or os.getenv("STORAGE_DIR", "./storage")).resolve()
        self._base.mkdir(parents=True, exist_ok=True)
        self._provider = "filesystem"

    def _path(self, key: str) -> Path:
        return self._base / key

    def _upload_sync(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def _download_sync(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise FileNotFoundError(f"Key not found: {key}")
        return path.read_bytes()

    async def upload(self, key: str, data: bytes, content_type: str = "image/png") -> ProviderResult:
        try:
            path_str = await asyncio.to_thread(self._upload_sync, key, data)
            return ProviderResult(
                success=True,
                data={"key": key, "size": len(data), "path": path_str},
                provider=self._provider,
            )
        except Exception as e:
            return ProviderResult(success=False, error=str(e), provider=self._provider)

    async def download(self, key: str) -> ProviderResult:
        try:
            data = await asyncio.to_thread(self._download_sync, key)
            return ProviderResult(
                success=True,
                data={"key": key, "data": data, "size": len(data)},
                provider=self._provider,
            )
        except FileNotFoundError:
            return ProviderResult(success=False, error=f"Key not found: {key}", provider=self._provider)
        except Exception as e:
            return ProviderResult(success=False, error=str(e), provider=self._provider)

    async def get_presigned_url(self, key: str, expires: int = 3600) -> str:
        path = self._path(key)
        if path.exists():
            return f"/storage/{key}"
        return ""

    async def list_objects(self, prefix: str) -> ProviderResult:
        try:
            def _list():
                prefix_path = self._path(prefix)
                keys = []
                if prefix_path.exists():
                    for root, dirs, files in os.walk(prefix_path):
                        for f in files:
                            full = Path(root) / f
                            rel = str(full.relative_to(self._base))
                            keys.append(rel)
                return keys
            keys = await asyncio.to_thread(_list)
            return ProviderResult(success=True, data={"keys": keys}, provider=self._provider)
        except Exception as e:
            return ProviderResult(success=False, error=str(e), provider=self._provider)

    async def health_check(self) -> ProviderResult:
        try:
            self._base.mkdir(parents=True, exist_ok=True)
            test_file = self._base / ".health_check"
            await asyncio.to_thread(test_file.write_bytes, b"ok")
            await asyncio.to_thread(test_file.unlink)
            return ProviderResult(
                success=True,
                data={"base_dir": str(self._base), "provider": self._provider},
                provider=self._provider,
            )
        except Exception as e:
            return ProviderResult(success=False, error=str(e), provider=self._provider)
