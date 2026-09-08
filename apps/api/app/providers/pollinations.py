"""Persona Studio — Pollinations.ai Image Provider.

Free image generation via Pollinations.ai.
No API key required. Supports text-to-image with style prompts.
"""

from __future__ import annotations
import asyncio
import base64
import time
import urllib.parse
from typing import Any

import httpx

from app.providers.base import ImageProvider, ProviderResult

# Pollinations.ai API endpoint (free, no key)
POLLINATIONS_URL = "https://image.pollinations.ai/prompt"


class PollinationsImageProvider(ImageProvider):
    """Image generation via Pollinations.ai (free, no API key)."""

    def __init__(self):
        self._provider = "pollinations"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=120.0)
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
    ) -> ProviderResult:
        """Generate an image from text prompt via Pollinations.ai."""
        start = time.monotonic()

        # Build the prompt with negative prompt as exclusion
        full_prompt = prompt
        if negative_prompt:
            full_prompt = f"{prompt}, not {negative_prompt}"

        # URL encode the prompt
        encoded = urllib.parse.quote(full_prompt)
        url = f"{POLLINATIONS_URL}/{encoded}?width={width}&height={height}&nologo=true"
        if seed >= 0:
            url += f"&seed={seed}"

        try:
            client = await self._get_client()
            resp = await client.get(url)

            if resp.status_code == 200 and len(resp.content) > 1000:
                image_bytes = resp.content
                effective_seed = seed if seed >= 0 else int.from_bytes(image_bytes[:4], "big") % 2147483647
                elapsed_ms = (time.monotonic() - start) * 1000

                return ProviderResult(
                    success=True,
                    data={
                        "image_bytes": image_bytes,
                        "image_key": f"pollinations/img_{effective_seed}.png",
                        "seed": effective_seed,
                        "width": width,
                        "height": height,
                        "prompt": prompt,
                        "generation_time_ms": int(elapsed_ms),
                        "model": "pollinations-sana",
                        "image_size_bytes": len(image_bytes),
                    },
                    provider=self._provider,
                    latency_ms=elapsed_ms,
                )
            else:
                return ProviderResult(
                    success=False,
                    error=f"Pollinations returned HTTP {resp.status_code}, {len(resp.content)} bytes",
                    provider=self._provider,
                    latency_ms=(time.monotonic() - start) * 1000,
                )

        except Exception as e:
            return ProviderResult(
                success=False,
                error=f"Pollinations error: {e}",
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )

    async def img2img(
        self, image_key: str, prompt: str, strength: float = 0.75, **kwargs
    ) -> ProviderResult:
        """Image-to-image falls back to text-to-image (Pollinations doesn't support i2i)."""
        return await self.generate(
            prompt=prompt,
            width=kwargs.get("width", 1024),
            height=kwargs.get("height", 1024),
            seed=kwargs.get("seed", -1),
        )

    async def edit_image(
        self,
        reference_image_bytes: bytes,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        seed: int = -1,
    ) -> ProviderResult:
        """Edit falls back to text-to-image with the prompt as-is."""
        return await self.generate(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            seed=seed,
        )

    async def upscale(self, image_key: str, scale: int = 2) -> ProviderResult:
        return await self.generate(
            prompt="High quality, detailed, sharp, professional, photorealistic",
            width=1024 * scale,
            height=1024 * scale,
        )

    async def health_check(self) -> ProviderResult:
        return ProviderResult(
            success=True,
            data={"model": "pollinations-sana", "api_key_set": False, "endpoint": "pollinations.ai"},
            provider=self._provider,
        )
