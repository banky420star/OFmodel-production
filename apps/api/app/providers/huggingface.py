"""Persona Studio — HuggingFace Inference API Image Provider.

Uses the free HF Inference API for image generation.
No GPU required — runs on HuggingFace's infrastructure.

Models:
  - black-forest-labs/FLUX.1-schnell  (text-to-image, fast, free)
  - stabilityai/stable-diffusion-xl-base-1.0  (text-to-image, high quality)
  - stabilityai/stable-diffusion-xl-refiner-1.0  (img2img, upscaling)

Requires:
  HUGGINGFACE_API_KEY — get one free at https://huggingface.co/settings/tokens
"""

from __future__ import annotations
import base64
import io
import time
from typing import Any

import httpx

from app.providers.base import ImageProvider, ProviderResult


class HuggingFaceImageProvider(ImageProvider):
    """Image generation via HuggingFace Inference API."""

    def __init__(
        self,
        api_key: str = "",
        model_id: str = "",
        img2img_model: str = "",
    ):
        import os
        self._api_key = api_key or os.getenv("HUGGINGFACE_API_KEY", "")
        self._model = model_id or os.getenv(
            "HF_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell"
        )
        self._img2img_model = img2img_model or os.getenv(
            "HF_IMG2IMG_MODEL", "stabilityai/stable-diffusion-xl-refiner-1.0"
        )
        self._provider = "huggingface"
        self._client: httpx.AsyncClient | None = None
        self._storage: Any = None  # lazy-init storage

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.AsyncClient(
                timeout=120.0,
                headers=headers,
            )
        return self._client

    def _get_storage(self):
        if self._storage is None:
            from app.providers.mocks import MockStorageProvider
            self._storage = MockStorageProvider()
        return self._storage

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
        start = time.monotonic()

        try:
            client = await self._get_client()

            # Build the payload
            payload: dict[str, Any] = {"inputs": prompt}

            # FLUX.1-schnell doesn't use negative_prompt or guidance_scale
            if "flux" not in self._model.lower():
                if negative_prompt:
                    payload["parameters"] = payload.get("parameters", {})
                    payload["parameters"]["negative_prompt"] = negative_prompt
                payload["parameters"] = payload.get("parameters", {})
                payload["parameters"]["guidance_scale"] = cfg_scale
                payload["parameters"]["num_inference_steps"] = steps

            # Seed
            if seed >= 0:
                payload.setdefault("parameters", {})["seed"] = seed

            url = f"https://api-inference.huggingface.co/models/{self._model}"
            resp = await client.post(url, json=payload)

            # Handle model loading (503 with wait time)
            if resp.status_code == 503:
                import json
                info = resp.json()
                wait = info.get("estimated_time", 30)
                # Model is loading — wait and retry once
                await self._wait_for_model(client, url, wait)
                resp = await client.post(url, json=payload)

            if resp.status_code != 200:
                return ProviderResult(
                    success=False,
                    error=f"HF API error {resp.status_code}: {resp.text[:500]}",
                    provider=self._provider,
                    latency_ms=(time.monotonic() - start) * 1000,
                )

            # Response is raw image bytes (PNG)
            image_bytes = resp.content
            if len(image_bytes) < 100:
                return ProviderResult(
                    success=False,
                    error=f"Response too small ({len(image_bytes)} bytes), likely not an image",
                    provider=self._provider,
                    latency_ms=(time.monotonic() - start) * 1000,
                )

            # Generate a deterministic key
            effective_seed = seed if seed >= 0 else int.from_bytes(image_bytes[:4], "big")
            key = f"huggingface/images/img_s{effective_seed}_{width}x{height}.png"

            # Store via storage provider
            storage = self._get_storage()
            store_result = await storage.upload(key, image_bytes, content_type="image/png")

            elapsed_ms = (time.monotonic() - start) * 1000

            return ProviderResult(
                success=True,
                data={
                    "image_key": key,
                    "seed": effective_seed,
                    "width": width,
                    "height": height,
                    "prompt": prompt,
                    "generation_time_ms": int(elapsed_ms),
                    "model": self._model,
                    "image_size_bytes": len(image_bytes),
                },
                provider=self._provider,
                latency_ms=elapsed_ms,
            )

        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error=f"Cannot connect to HuggingFace API. Check internet connection.",
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as e:
            return ProviderResult(
                success=False,
                error=str(e),
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )

    async def img2img(
        self,
        image_key: str,
        prompt: str,
        strength: float = 0.75,
        **kwargs,
    ) -> ProviderResult:
        start = time.monotonic()

        try:
            client = await self._get_client()

            # Download the source image
            storage = self._get_storage()
            dl = await storage.download(image_key)
            if not dl.success:
                return ProviderResult(
                    success=False,
                    error=f"Could not download source image: {dl.error}",
                    provider=self._provider,
                )

            image_bytes = dl.data.get("data", b"")

            # Encode to base64 for the API
            image_b64 = base64.b64encode(image_bytes).decode()

            # Use the img2img endpoint
            url = f"https://api-inference.huggingface.co/models/{self._img2img_model}"
            payload = {
                "inputs": prompt,
                "parameters": {
                    "image": image_b64,
                    "strength": strength,
                },
            }

            resp = await client.post(url, json=payload)

            if resp.status_code == 503:
                import json
                info = resp.json()
                wait = info.get("estimated_time", 30)
                await self._wait_for_model(client, url, wait)
                resp = await client.post(url, json=payload)

            if resp.status_code != 200:
                # Fall back to text-to-image if img2img model isn't available
                return await self.generate(
                    prompt=prompt,
                    width=kwargs.get("width", 1024),
                    height=kwargs.get("height", 1024),
                    seed=kwargs.get("seed", -1),
                )

            image_bytes = resp.content
            seed = kwargs.get("seed", -1)
            effective_seed = seed if seed >= 0 else int.from_bytes(image_bytes[:4], "big")
            key = f"huggingface/images/img2img_s{effective_seed}.png"

            await storage.upload(key, image_bytes, content_type="image/png")
            elapsed_ms = (time.monotonic() - start) * 1000

            return ProviderResult(
                success=True,
                data={
                    "image_key": key,
                    "seed": effective_seed,
                    "source": image_key,
                    "strength": strength,
                    "generation_time_ms": int(elapsed_ms),
                    "model": self._img2img_model,
                },
                provider=self._provider,
                latency_ms=elapsed_ms,
            )

        except Exception as e:
            # Fall back to text-to-image
            return await self.generate(
                prompt=prompt,
                width=kwargs.get("width", 1024),
                height=kwargs.get("height", 1024),
                seed=kwargs.get("seed", -1),
            )

    async def upscale(
        self, image_key: str, scale: int = 2
    ) -> ProviderResult:
        """Upscale by generating a higher-res variant via img2img."""
        return await self.img2img(
            image_key=image_key,
            prompt="high quality, detailed, sharp, professional",
            strength=0.3,  # low strength = preserve original
            width=1024 * scale,
            height=1024 * scale,
        )

    async def health_check(self) -> ProviderResult:
        try:
            client = await self._get_client()
            # Quick check — try to get model info
            url = f"https://huggingface.co/api/models/{self._model}"
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code == 200:
                return ProviderResult(
                    success=True,
                    data={
                        "model": self._model,
                        "img2img_model": self._img2img_model,
                        "api_key_set": bool(self._api_key),
                    },
                    provider=self._provider,
                )
            return ProviderResult(
                success=False,
                error=f"HF model check returned {resp.status_code}",
                provider=self._provider,
            )
        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error="Cannot reach HuggingFace API",
                provider=self._provider,
            )
        except Exception as e:
            return ProviderResult(success=False, error=str(e), provider=self._provider)

    async def _wait_for_model(self, client, url, wait_seconds):
        """Wait for a HF model to finish loading (max 60s)."""
        import asyncio
        actual_wait = min(wait_seconds, 60)
        await asyncio.sleep(actual_wait)
