"""Persona Studio — EachSense (each::sense) Image Provider.

Cloud image generation via EachLabs each::sense agent. Supports mature/adult
content (safety checker disabled) and session-based character consistency:
pass the persona's identity-lock session id as `session_id` so the same
character persists across shots in a shoot.

Env:
  EACHLABS_API_KEY = eachlabs.ai API key (required to activate)
  EACHSENSE_MODE   = max | eco (default max)
"""

from __future__ import annotations
import json
import os
import time
from typing import Any
import logging

import httpx

from app.providers.base import ImageProvider, ProviderResult

logger = logging.getLogger(__name__)

BASE_URL = "https://eachsense-agent.core.eachlabs.run"
ENDPOINT = f"{BASE_URL}/v1/chat/completions"

IMAGE_EXT_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _extract_image_urls(obj: Any) -> list[str]:
    """Recursively collect http(s) URLs that look like generated images."""
    found: list[str] = []
    if isinstance(obj, str):
        if obj.startswith(("http://", "https://")) and (
            obj.lower().endswith(IMAGE_EXT_SUFFIXES)
            or "image" in obj.lower()
            or "generation" in obj.lower()
        ):
            found.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            found.extend(_extract_image_urls(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(_extract_image_urls(v))
    return found


def _parse_sse_for_image(text: str) -> tuple[str, str]:
    """Parse an SSE stream body, returning (image_url, error).

    Tolerant of schema drift: scans every `data:` payload for image URLs,
    preferring ones from `generation_response`/`complete` events; captures
    the first `error` payload otherwise.
    """
    image_url = ""
    error = ""
    last_url_seen = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        payload = ""
        if line.startswith("data:"):
            payload = line[5:].strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        # Event type: top-level `type`/`event`, or nested under the OpenAI
        # chunk wrapper in the `eachlabs` extension field
        eachlabs = data.get("eachlabs") or {}
        event = str(
            data.get("event")
            or data.get("type")
            or (eachlabs.get("type") if isinstance(eachlabs, dict) else "")
            or ""
        ).lower()
        urls = _extract_image_urls(data)
        if urls:
            last_url_seen = urls[-1]
            if event in ("generation_response", "complete", "completed"):
                image_url = image_url or urls[-1]
        if event == "error":
            src = eachlabs if isinstance(eachlabs, dict) and eachlabs.get("message") else data
            error = error or str(src.get("message") or src.get("error") or data)[:300]
        if event == "complete" and not urls:
            status = str(data.get("status") or "")
            if status and status != "ok":
                error = error or f"eachsense completed with status={status}"
    # Fall back to the last URL seen if no completion event carried one
    return (image_url or last_url_seen), error


class EachSenseImageProvider(ImageProvider):
    """Image generation via EachLabs each::sense (adult-capable)."""

    def __init__(self, api_key: str = "", mode: str = ""):
        self._api_key = api_key or os.getenv("EACHLABS_API_KEY", "")
        self._mode = mode or os.getenv("EACHSENSE_MODE", "max")
        self._provider = "eachsense_image"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=600.0)
        return self._client

    async def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        steps: int = 30,
        cfg_scale: float = 7.0,
        seed: int = -1,
        lora_path: str = "",
        lora_strength: float = 0.8,
        session_id: str = "",
    ) -> ProviderResult:
        """Generate an image. `session_id` keeps the character consistent
        across calls (pass the persona's identity session)."""
        start = time.monotonic()

        if not self._api_key:
            return ProviderResult(
                success=False,
                error="No EACHLABS_API_KEY configured",
                provider=self._provider,
                latency_ms=0,
            )

        # each::sense takes a natural-language brief; fold quality params in
        content = prompt
        if negative_prompt:
            content += f". Avoid: {negative_prompt}"
        content += (
            f". Aspect ratio {width}x{height}, photorealistic quality,"
            f" coherent composition."
        )

        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": content}],
            "model": "eachsense/beta",
            "stream": True,
            "mode": self._mode,
            "enable_safety_checker": False,
        }
        if session_id:
            payload["session_id"] = session_id

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self._api_key,
            "Accept": "text/event-stream",
        }

        try:
            client = await self._get_client()
            resp = await client.post(ENDPOINT, json=payload, headers=headers)
        except httpx.ConnectError as e:
            return ProviderResult(
                success=False,
                error=f"Cannot reach eachsense endpoint: {e}",
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as e:
            return ProviderResult(
                success=False,
                error=f"eachsense request failed: {e}",
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )

        if resp.status_code != 200:
            return ProviderResult(
                success=False,
                error=f"eachsense HTTP {resp.status_code}: {resp.text[:300]}",
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )

        image_url, sse_error = _parse_sse_for_image(resp.text)
        if not image_url:
            elapsed_ms = (time.monotonic() - start) * 1000
            return ProviderResult(
                success=False,
                error=sse_error or "No image URL in eachsense SSE stream",
                provider=self._provider,
                latency_ms=elapsed_ms,
            )

        # Download the generated image
        try:
            img_resp = await client.get(image_url)
            img_resp.raise_for_status()
            image_bytes = img_resp.content
        except Exception as e:
            return ProviderResult(
                success=False,
                error=f"eachsense image download failed: {e}",
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )

        if len(image_bytes) <= 100:
            return ProviderResult(
                success=False,
                error="eachsense returned an empty/invalid image",
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )

        effective_seed = seed if seed >= 0 else int.from_bytes(image_bytes[:4], "big")
        elapsed_ms = (time.monotonic() - start) * 1000
        return ProviderResult(
            success=True,
            data={
                "image_key": f"eachsense/images/img_s{effective_seed}_{width}x{height}.png",
                "image_bytes": image_bytes,
                "seed": effective_seed,
                "width": width,
                "height": height,
                "prompt": prompt,
                "generation_time_ms": int(elapsed_ms),
                "model": "eachsense/beta",
                "image_size_bytes": len(image_bytes),
                "source_url": image_url,
                "session_id": session_id or None,
            },
            provider=self._provider,
            latency_ms=elapsed_ms,
        )

    async def img2img(
        self,
        image_key: str,
        prompt: str,
        strength: float = 0.75,
        **kwargs,
    ) -> ProviderResult:
        # Reference-based generation when a public URL is available,
        # otherwise fall back to text-to-image.
        session_id = kwargs.get("session_id", "")
        if image_key.startswith(("http://", "https://")):
            start = time.monotonic()
            payload = {
                "messages": [{"role": "user", "content": prompt}],
                "model": "eachsense/beta",
                "image_urls": [image_key],
                "stream": True,
                "mode": self._mode,
                "enable_safety_checker": False,
            }
            if session_id:
                payload["session_id"] = session_id
            headers = {
                "Content-Type": "application/json",
                "X-API-Key": self._api_key,
                "Accept": "text/event-stream",
            }
            client = await self._get_client()
            resp = await client.post(ENDPOINT, json=payload, headers=headers)
            if resp.status_code == 200:
                image_url, sse_error = _parse_sse_for_image(resp.text)
                if image_url:
                    img_resp = await client.get(image_url)
                    image_bytes = img_resp.content
                    return ProviderResult(
                        success=True,
                        data={
                            "image_bytes": image_bytes,
                            "seed": kwargs.get("seed", -1),
                            "prompt": prompt,
                            "mode": "img2img",
                            "source_url": image_url,
                        },
                        provider=self._provider,
                        latency_ms=(time.monotonic() - start) * 1000,
                    )
                return ProviderResult(
                    success=False,
                    error=sse_error or "No image in img2img stream",
                    provider=self._provider,
                )
            return ProviderResult(
                success=False,
                error=f"eachsense img2img HTTP {resp.status_code}",
                provider=self._provider,
            )
        return await self.generate(prompt=prompt, session_id=session_id, **kwargs)

    async def upscale(self, image_key: str, scale: int = 2) -> ProviderResult:
        return await self.generate(
            prompt="High quality, detailed, sharp, professional, photorealistic",
            width=1024 * scale,
            height=1024 * scale,
        )

    async def health_check(self) -> ProviderResult:
        if not self._api_key:
            return ProviderResult(
                success=False,
                error="No EACHLABS_API_KEY",
                provider=self._provider,
            )
        return ProviderResult(
            success=True,
            data={
                "model": "eachsense/beta",
                "api_key_set": True,
                "mode": self._mode,
                "endpoint": "eachsense-agent.core.eachlabs.run",
                "safety_checker": False,
            },
            provider=self._provider,
        )
