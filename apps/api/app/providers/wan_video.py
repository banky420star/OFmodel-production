"""Persona Studio — Wan Video Provider.

Connects to a Wan-compatible video generation API (Wan2.1 / CogVideoX / etc.)
for image-to-video and text-to-video generation.

Requires:
  - A Wan-compatible video server running at WAN_VIDEO_URL
  - GPU with sufficient VRAM (8GB+ for 512x512, 16GB+ for 720p)
"""

from __future__ import annotations
import random
import time
import uuid
from typing import Any

import httpx
import structlog

from app.providers.base import VideoProvider, ProviderResult

logger = structlog.get_logger()


class WanVideoProvider(VideoProvider):
    """Real video generation via Wan-compatible API.

    Supports image-to-video and text-to-video workflows.
    The API endpoint should accept:
    - POST /generate/image_to_video  (with image + prompt)
    - POST /generate/text_to_video   (with prompt + params)
    - GET  /status/{job_id}          (poll for completion)
    - GET  /download/{job_id}        (download result)
    """

    def __init__(self, base_url: str = "http://localhost:8080", timeout: float = 600):
        import os
        self._base_url = (base_url or os.getenv("WAN_VIDEO_URL", "http://localhost:8080")).rstrip("/")
        self._timeout = timeout
        self._provider = "wan_video"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        return self._client

    async def image_to_video(
        self,
        image_key: str,
        prompt: str = "",
        duration: float = 4.0,
        fps: int = 24,
    ) -> ProviderResult:
        """Generate video from a source image + motion prompt."""
        start = time.monotonic()
        try:
            client = await self._get_client()

            payload = {
                "image": image_key,
                "prompt": prompt or "subtle natural motion, gentle movement",
                "duration": duration,
                "fps": fps,
                "width": 512,   # Wan default, will scale
                "height": 512,
                "num_frames": int(duration * fps),
                "motion_strength": 0.6,
                "guidance_scale": 7.5,
                "num_inference_steps": 50,
                "seed": random.randint(0, 2**31),
            }

            resp = await client.post("/generate/image_to_video", json=payload)
            resp.raise_for_status()
            job = resp.json()
            job_id = job.get("job_id", job.get("id", ""))

            # Poll for completion
            result = await self._poll_job(job_id)
            elapsed_ms = (time.monotonic() - start) * 1000

            if result:
                return ProviderResult(
                    success=True,
                    data={
                        "video_key": result.get("output_path", f"videos/{job_id}.mp4"),
                        "video_url": result.get("output_url", ""),
                        "source_image": image_key,
                        "duration": duration,
                        "fps": fps,
                        "width": payload["width"],
                        "height": payload["height"],
                        "generation_time_ms": int(elapsed_ms),
                        "job_id": job_id,
                        "is_mock": False,
                    },
                    provider=self._provider,
                    latency_ms=elapsed_ms,
                )
            else:
                return ProviderResult(
                    success=False,
                    error=f"Wan video job {job_id} did not complete within {self._timeout}s",
                    provider=self._provider,
                    latency_ms=elapsed_ms,
                )

        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error=f"Cannot connect to Wan video server at {self._base_url}",
                provider=self._provider,
            )
        except Exception as e:
            logger.error("wan_image_to_video_failed", error=str(e))
            return ProviderResult(
                success=False,
                error=str(e),
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )

    async def text_to_video(
        self,
        prompt: str,
        duration: float = 4.0,
        width: int = 512,
        height: int = 512,
    ) -> ProviderResult:
        """Generate video from text prompt alone."""
        start = time.monotonic()
        try:
            client = await self._get_client()

            payload = {
                "prompt": prompt,
                "duration": duration,
                "fps": 24,
                "width": width,
                "height": height,
                "num_frames": int(duration * 24),
                "guidance_scale": 7.5,
                "num_inference_steps": 50,
                "seed": random.randint(0, 2**31),
            }

            resp = await client.post("/generate/text_to_video", json=payload)
            resp.raise_for_status()
            job = resp.json()
            job_id = job.get("job_id", job.get("id", ""))

            result = await self._poll_job(job_id)
            elapsed_ms = (time.monotonic() - start) * 1000

            if result:
                return ProviderResult(
                    success=True,
                    data={
                        "video_key": result.get("output_path", f"videos/{job_id}.mp4"),
                        "video_url": result.get("output_url", ""),
                        "duration": duration,
                        "width": width,
                        "height": height,
                        "prompt": prompt,
                        "generation_time_ms": int(elapsed_ms),
                        "job_id": job_id,
                        "is_mock": False,
                    },
                    provider=self._provider,
                    latency_ms=elapsed_ms,
                )
            else:
                return ProviderResult(
                    success=False,
                    error=f"Wan video job {job_id} timed out",
                    provider=self._provider,
                    latency_ms=elapsed_ms,
                )

        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error=f"Cannot connect to Wan video server at {self._base_url}",
                provider=self._provider,
            )
        except Exception as e:
            logger.error("wan_text_to_video_failed", error=str(e))
            return ProviderResult(
                success=False,
                error=str(e),
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )

    async def _poll_job(self, job_id: str) -> dict | None:
        """Poll Wan API for job completion."""
        import asyncio
        client = await self._get_client()
        deadline = time.monotonic() + self._timeout

        while time.monotonic() < deadline:
            try:
                resp = await client.get(f"/status/{job_id}")
                if resp.status_code == 200:
                    status = resp.json()
                    state = status.get("status", "").lower()
                    if state in ("completed", "done", "success"):
                        return status
                    elif state in ("failed", "error"):
                        logger.error("wan_job_failed", job_id=job_id, status=status)
                        return None
            except Exception:
                pass
            await asyncio.sleep(2.0)

        return None

    async def health_check(self) -> ProviderResult:
        try:
            client = await self._get_client()
            resp = await client.get("/health")
            if resp.status_code == 200:
                return ProviderResult(
                    success=True,
                    data={"status": "connected", "server": self._base_url},
                    provider=self._provider,
                )
            return ProviderResult(
                success=False,
                error=f"Wan server returned {resp.status_code}",
                provider=self._provider,
            )
        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error=f"Wan video server not reachable at {self._base_url}",
                provider=self._provider,
            )
        except Exception as e:
            return ProviderResult(success=False, error=str(e), provider=self._provider)
