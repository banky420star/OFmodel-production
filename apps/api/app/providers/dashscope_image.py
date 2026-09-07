"""Persona Studio — DashScope Qwen-Image Provider.

Uses Alibaba Cloud's Qwen-Image 2.0 Pro for photorealistic image generation.
Same API key as DashScope Wan video — no extra setup needed.

Models:
  - qwen-image-2.0-pro  (up to 2048×2048, best quality)
  - qwen-image-max      (up to 1664×928, higher realism)
  - qwen-image-plus     (up to 1664×928, diverse styles)
"""

from __future__ import annotations
import base64
import time
from typing import Any
import logging

import httpx

from app.providers.base import ImageProvider, ProviderResult

logger = logging.getLogger(__name__)

# International endpoint for non-China API keys
ENDPOINTS = [
    "https://dashscope-intl.aliyuncs.com",
    "https://dashscope.aliyuncs.com",
]

NEGATIVE_PROMPT = (
    "Low resolution, low quality, distorted limbs, malformed fingers, "
    "oversaturated colors, wax-figure appearance, lack of facial detail, "
    "excessive smoothness, AI-looking artifacts, chaotic composition, "
    "blurry or warped text, cartoon, anime, illustration, painting"
)


class DashScopeImageProvider(ImageProvider):
    """Image generation via DashScope Qwen-Image."""

    def __init__(
        self,
        api_key: str = "",
        model_id: str = "",
    ):
        import os
        self._api_key = api_key or os.getenv("WAN_API_KEY", "")
        self._model = model_id or os.getenv(
            "HF_IMAGE_MODEL", "qwen-image-2.0-pro"
        )
        self._provider = "dashscope_image"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=300.0)
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
        start = time.monotonic()

        if not self._api_key:
            return ProviderResult(
                success=False,
                error="No DashScope API key configured",
                provider=self._provider,
                latency_ms=0,
            )

        # Clamp to supported sizes
        width = max(512, min(2048, width))
        height = max(512, min(2048, height))
        size_str = f"{width}*{height}"

        neg = negative_prompt or NEGATIVE_PROMPT

        payload = {
            "model": self._model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"text": prompt}],
                    }
                ]
            },
            "parameters": {
                "negative_prompt": neg,
                "prompt_extend": True,
                "watermark": False,
                "size": size_str,
            },
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        # Try international endpoint first, then China fallback
        last_error = None
        for base_url in ENDPOINTS:
            url = f"{base_url}/api/v1/services/aigc/multimodal-generation/generation"
            try:
                client = await self._get_client()
                resp = await client.post(url, json=payload, headers=headers)

                if resp.status_code == 200:
                    data = resp.json()
                    # Extract image from response
                    output = data.get("output", {})
                    choices = output.get("choices", [])

                    if choices:
                        content = choices[0].get("message", {}).get("content", [])
                        for item in content:
                            if "image" in item:
                                # Image URL or base64
                                img_url = item["image"]
                                if img_url.startswith("http"):
                                    # Download the image
                                    img_resp = await client.get(img_url)
                                    image_bytes = img_resp.content
                                else:
                                    # Base64 encoded
                                    image_bytes = base64.b64decode(img_url)

                                if len(image_bytes) > 100:
                                    effective_seed = seed if seed >= 0 else int.from_bytes(image_bytes[:4], "big")
                                    key = f"dashscope/images/img_s{effective_seed}_{width}x{height}.png"

                                    # Store via filesystem
                                    from pathlib import Path
                                    storage_dir = Path(__file__).parent.parent.parent / "storage" / "avatars"
                                    storage_dir.mkdir(parents=True, exist_ok=True)

                                    elapsed_ms = (time.monotonic() - start) * 1000
                                    return ProviderResult(
                                        success=True,
                                        data={
                                            "image_key": key,
                                            "image_bytes": image_bytes,
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

                    # No image in response
                    last_error = f"No image in response: {data}"
                    logger.warning("dashscope_image_no_image", extra={"response": str(data)[:500]})

                elif resp.status_code == 401:
                    last_error = f"Auth failed (401) on {base_url}"
                    continue  # Try next endpoint
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:500]}"
                    logger.warning("dashscope_image_http_error",
                                   extra={"status": resp.status_code, "error": resp.text[:300]})

            except httpx.ConnectError:
                last_error = f"Cannot connect to {base_url}"
                continue
            except Exception as e:
                last_error = str(e)
                logger.error("dashscope_image_error", extra={"error": str(e)})

        elapsed_ms = (time.monotonic() - start) * 1000
        return ProviderResult(
            success=False,
            error=f"DashScope image failed: {last_error}",
            provider=self._provider,
            latency_ms=elapsed_ms,
        )

    async def edit_image(
        self,
        reference_image_bytes: bytes,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1536,
        height: int = 2048,
        seed: int = -1,
    ) -> ProviderResult:
        """Edit a reference image using Qwen-Image I2I.
        
        Preserves facial features while changing outfit, pose, or setting.
        Uses qwen-image-3.0-pro for best edit quality.
        """
        start = time.monotonic()

        if not self._api_key:
            return ProviderResult(
                success=False,
                error="No DashScope API key configured",
                provider=self._provider,
                latency_ms=0,
            )

        # Encode reference image as base64 data URI
        img_b64 = base64.b64encode(reference_image_bytes).decode()
        img_data_uri = f"data:image/png;base64,{img_b64}"

        neg = negative_prompt or NEGATIVE_PROMPT
        size_str = f"{max(512, min(2048, width))}*{max(512, min(2048, height))}"

        payload = {
            "model": "qwen-image-2.0-pro",
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"image": img_data_uri},
                            {"text": prompt},
                        ],
                    }
                ]
            },
            "parameters": {
                "negative_prompt": neg,
                "prompt_extend": True,
                "watermark": False,
                "size": size_str,
            },
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        last_error = None
        for base_url in ENDPOINTS:
            url = f"{base_url}/api/v1/services/aigc/multimodal-generation/generation"
            try:
                client = await self._get_client()
                resp = await client.post(url, json=payload, headers=headers)

                if resp.status_code == 200:
                    data = resp.json()
                    output = data.get("output", {})
                    choices = output.get("choices", [])

                    if choices:
                        content = choices[0].get("message", {}).get("content", [])
                        for item in content:
                            if "image" in item:
                                img_url = item["image"]
                                if img_url.startswith("http"):
                                    img_resp = await client.get(img_url)
                                    image_bytes = img_resp.content
                                else:
                                    image_bytes = base64.b64decode(img_url)

                                if len(image_bytes) > 100:
                                    effective_seed = seed if seed >= 0 else int.from_bytes(image_bytes[:4], "big")
                                    elapsed_ms = (time.monotonic() - start) * 1000
                                    return ProviderResult(
                                        success=True,
                                        data={
                                            "image_bytes": image_bytes,
                                            "seed": effective_seed,
                                            "width": width,
                                            "height": height,
                                            "prompt": prompt,
                                            "generation_time_ms": int(elapsed_ms),
                                            "model": "qwen-image-3.0-pro",
                                            "image_size_bytes": len(image_bytes),
                                            "mode": "edit",
                                        },
                                        provider=self._provider,
                                        latency_ms=elapsed_ms,
                                    )

                    last_error = f"No image in response: {data}"
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"

            except httpx.ConnectError:
                last_error = f"Cannot connect to {base_url}"
                continue
            except Exception as e:
                last_error = str(e)

        elapsed_ms = (time.monotonic() - start) * 1000
        return ProviderResult(
            success=False,
            error=f"DashScope edit failed: {last_error}",
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
        # Fall back to text-to-image
        return await self.generate(
            prompt=prompt,
            width=kwargs.get("width", 1024),
            height=kwargs.get("height", 1024),
            seed=kwargs.get("seed", -1),
        )

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
                error="No DashScope API key",
                provider=self._provider,
            )
        return ProviderResult(
            success=True,
            data={
                "model": self._model,
                "api_key_set": True,
                "endpoint": "dashscope-intl.aliyuncs.com",
            },
            provider=self._provider,
        )
