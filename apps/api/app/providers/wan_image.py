"""Persona Studio — Wan (DashScope) image provider.

Real text-to-image AND image-to-image generation through the SAME DashScope
key that powers Wan video. The i2i path is the identity-locked path: the
persona's locked avatar is sent as the visual reference, so the face is
preserved by construction instead of by prompt-hope.

Verified live 2026-09-13:
  - wan2.5-t2i-preview  → 200, task SUCCEEDED
  - wan2.5-i2i-preview  → 200 (avatar reference)
  - qwen-image* models  → 401 InvalidApiKey on this key (unusable)
"""
from __future__ import annotations

import asyncio
import base64
import time
from typing import Optional

import httpx

from app.providers.base import ImageProvider, ProviderResult

BASE_URLS = [
    "https://dashscope-intl.aliyuncs.com",
    "https://dashscope.aliyuncs.com",
]

T2I_MODEL = "wan2.5-t2i-preview"
I2I_MODEL = "wan2.5-i2i-preview"


class WanImageProvider(ImageProvider):
    """Wan 2.5 image generation (text→image and image→image) via DashScope."""

    name = "wan_image"

    def __init__(self, api_key: str, model: str = T2I_MODEL, i2i_model: str = I2I_MODEL):
        self._api_key = api_key
        self._t2i_model = model
        self._i2i_model = i2i_model
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0))
        return self._client

    async def _submit(self, path: str, payload: dict) -> dict:
        """Submit an async task on the first base URL that answers. Raises on
        auth/param errors; returns the task descriptor dict."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        last = None
        for base in BASE_URLS:
            try:
                resp = await self._get_client().post(f"{base}{path}", json=payload, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
                last = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if resp.status_code == 401:
                    raise RuntimeError(f"DashScope auth failed (401): invalid key")
            except httpx.HTTPError as e:
                last = f"connection error: {e}"
        raise RuntimeError(f"DashScope submit failed: {last}")

    async def _wait_for_task(self, task_id: str, timeout_s: float = 240) -> dict:
        """Poll the DashScope task endpoint until SUCCEEDED/FAILED."""
        headers = {"Authorization": f"Bearer {self._api_key}"}
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            resp = await self._get_client().get(
                f"{BASE_URLS[0]}/api/v1/tasks/{task_id}", headers=headers
            )
            if resp.status_code != 200:
                raise RuntimeError(f"task poll failed: HTTP {resp.status_code}")
            d = resp.json().get("output", {})
            status = d.get("task_status")
            if status == "SUCCEEDED":
                return d
            if status in ("FAILED", "CANCELED", "UNKNOWN"):
                msg = d.get("message") or d.get("code") or "unknown task failure"
                raise RuntimeError(f"DashScope task {status}: {msg}")
            await asyncio.sleep(3)
        raise RuntimeError("DashScope task timed out")

    async def generate(
        self, prompt: str, width: int = 832, height: int = 1248,
        seed: int = -1, negative_prompt: str = "", **kwargs,
    ) -> ProviderResult:
        """Text-to-image (Wan 2.5)."""
        start = time.monotonic()
        if not self._api_key:
            return ProviderResult(success=False, error="No DashScope API key configured",
                                  provider=self.name, latency_ms=0)
        w = max(512, min(1440, int(width)))
        h = max(512, min(1440, int(height)))
        payload = {
            "model": self._t2i_model,
            "input": {"prompt": prompt, **({"negative_prompt": negative_prompt} if negative_prompt else {})},
            "parameters": {"size": f"{w}*{h}", "n": 1, "prompt_extend": True},
        }
        try:
            desc = await self._submit("/api/v1/services/aigc/text2image/image-synthesis", payload)
            task_id = desc.get("output", {}).get("task_id")
            if not task_id:
                raise RuntimeError("no task_id in submit response")
            out = await self._wait_for_task(task_id)
            results = out.get("results") or []
            url = results[0].get("url") if results else None
            if not url:
                raise RuntimeError("task succeeded but no image URL")
            img_resp = await self._get_client().get(url)
            img_resp.raise_for_status()
            image_bytes = img_resp.content
            return ProviderResult(
                success=True,
                data={
                    "image_bytes": image_bytes,
                    "image_key": None,  # caller persists via storage
                    "seed": seed,
                    "width": w, "height": h,
                    "prompt": prompt,
                    "generation_time_ms": int((time.monotonic() - start) * 1000),
                    "model": self._t2i_model,
                    "image_size_bytes": len(image_bytes),
                    "mode": "t2i",
                },
                provider=self.name,
                latency_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:  # noqa: BLE001
            return ProviderResult(success=False, error=str(e)[:400], provider=self.name,
                                  latency_ms=int((time.monotonic() - start) * 1000))

    async def img2img(
        self, image_key: str, prompt: str, strength: float = 0.55, **kwargs,
    ) -> ProviderResult:
        """Interface adapter: img2img by storage key. The identity-locked path
        uses edit_image() with raw avatar bytes; this variant resolves the
        reference through the shared storage provider first."""
        from app.providers.registry import get_registry
        storage = get_registry().get_storage_provider()
        result = await storage.download(image_key)
        if not result.success or not result.data.get("data"):
            return ProviderResult(success=False, error=f"reference not readable: {image_key}",
                                  provider=self.name, latency_ms=0)
        return await self.edit_image(
            reference_image_bytes=result.data["data"], prompt=prompt,
            strength=strength, **kwargs,
        )

    async def upscale(self, image_key: str, scale: int = 2) -> ProviderResult:
        """Not offered by the Wan 2.5 image API on this key — honest refusal."""
        return ProviderResult(success=False, error="upscale not supported by wan2.5 image API",
                              provider=self.name, latency_ms=0)

    async def health_check(self) -> ProviderResult:
        """Real health probe: submit a tiny t2i task and confirm it's accepted.
        Does not wait for completion (fast, cheap, no image wasted)."""
        start = time.monotonic()
        if not self._api_key:
            return ProviderResult(success=False, error="No DashScope API key configured",
                                  provider=self.name, latency_ms=0)
        try:
            payload = {
                "model": self._t2i_model,
                "input": {"prompt": "solid light gray square, minimal"},
                "parameters": {"size": "512*512", "n": 1},
            }
            desc = await self._submit("/api/v1/services/aigc/text2image/image-synthesis", payload)
            task_id = desc.get("output", {}).get("task_id")
            return ProviderResult(success=bool(task_id), provider=self.name,
                                  latency_ms=int((time.monotonic() - start) * 1000),
                                  data={"task_id": task_id} if task_id else {})
        except Exception as e:  # noqa: BLE001
            return ProviderResult(success=False, error=str(e)[:200], provider=self.name,
                                  latency_ms=int((time.monotonic() - start) * 1000))

    async def edit_image(
        self, reference_image_bytes: bytes, prompt: str,
        negative_prompt: str = "", width: int = 832, height: int = 1248,
        seed: int = -1, strength: float = 0.55, **kwargs,
    ) -> ProviderResult:
        """Image-to-image: the persona's avatar is the visual reference —
        the identity-locked generation path (face preserved by construction)."""
        start = time.monotonic()
        if not self._api_key:
            return ProviderResult(success=False, error="No DashScope API key configured",
                                  provider=self.name, latency_ms=0)
        w = max(512, min(1440, int(width)))
        h = max(512, min(1440, int(height)))
        dataurl = "data:image/jpeg;base64," + base64.b64encode(reference_image_bytes).decode()
        payload = {
            "model": self._i2i_model,
            "input": {
                "prompt": prompt,
                # API contract (verified live): the reference must be the
                # LIST field `images` — singular `image` fails with
                # "images field is required".
                "images": [dataurl],
                **({"negative_prompt": negative_prompt} if negative_prompt else {}),
            },
            "parameters": {"size": f"{w}*{h}", "n": 1},
        }
        try:
            desc = await self._submit("/api/v1/services/aigc/image2image/image-synthesis", payload)
            task_id = desc.get("output", {}).get("task_id")
            if not task_id:
                raise RuntimeError("no task_id in submit response")
            out = await self._wait_for_task(task_id)
            results = out.get("results") or []
            url = results[0].get("url") if results else None
            if not url:
                raise RuntimeError("task succeeded but no image URL")
            img_resp = await self._get_client().get(url)
            img_resp.raise_for_status()
            image_bytes = img_resp.content
            return ProviderResult(
                success=True,
                data={
                    "image_bytes": image_bytes,
                    "image_key": None,
                    "seed": seed,
                    "width": w, "height": h,
                    "prompt": prompt,
                    "generation_time_ms": int((time.monotonic() - start) * 1000),
                    "model": self._i2i_model,
                    "image_size_bytes": len(image_bytes),
                    "mode": "i2i_identity_locked",
                    "reference_used": True,
                },
                provider=self.name,
                latency_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:  # noqa: BLE001
            return ProviderResult(success=False, error=str(e)[:400], provider=self.name,
                                  latency_ms=int((time.monotonic() - start) * 1000))
