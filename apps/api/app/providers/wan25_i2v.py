"""Wan 2.5 image-to-video adapter for identity-locked production.

The manager already gates GENERATE_VIDEO on an APPROVED ShotPlan asset. This
provider consumes that approved image as the first frame and submits it to
DashScope's wan2.5-i2v-preview endpoint at 1080P by default.

It intentionally subclasses the existing DashScopeWanProvider so polling,
authentication and text-to-video behaviour stay centralized in one provider.
"""

from __future__ import annotations

import base64
import io
import time
from pathlib import Path

import httpx
import structlog

from app.providers.base import ProviderResult
from app.providers.wan_dashscope import DashScopeWanProvider, TASK_SUBMIT_URL

logger = structlog.get_logger()

WAN25_I2V_MODEL = "wan2.5-i2v-preview"
I2V_RESOLUTIONS = {"720P", "1080P"}
I2V_DURATION_RANGE = (3, 10)


class Wan25I2VProvider(DashScopeWanProvider):
    """DashScope Wan 2.5 image-to-video provider.

    The source image may be an http(s) URL, a data URI, an absolute local path,
    or a path relative to the ``apps/api`` project root such as
    ``storage/shoots/<id>/shot_01.png``.
    """

    def __init__(self, api_key: str = "", timeout: float = 600):
        super().__init__(api_key=api_key, timeout=timeout)
        self._provider = "dashscope_wan25_i2v"

    @staticmethod
    def _resolve_local_image(image_key: str) -> Path:
        path = Path(image_key)
        if path.is_absolute():
            return path
        api_root = Path(__file__).resolve().parents[2]
        return api_root / path

    @staticmethod
    def _encode_local_image(local_path: Path) -> tuple[str, int]:
        if not local_path.exists() or not local_path.is_file():
            raise FileNotFoundError(f"source image not found: {local_path}")

        img_bytes = local_path.read_bytes()
        mime = (local_path.suffix.lstrip(".") or "png").lower()
        if mime == "jpg":
            mime = "jpeg"

        # Keep request payloads bounded. Generated shoot images are often
        # around 1 MB; downscaling the transport copy does not alter the
        # approved source asset on disk.
        if len(img_bytes) > 900_000:
            try:
                from PIL import Image

                img = Image.open(io.BytesIO(img_bytes))
                img.thumbnail((1024, 1280), Image.LANCZOS)
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=88)
                img_bytes = buf.getvalue()
                mime = "jpeg"
            except ImportError:
                pass

        b64 = base64.b64encode(img_bytes).decode("ascii")
        return f"data:image/{mime};base64,{b64}", len(img_bytes)

    async def image_to_video(
        self,
        image_key: str,
        prompt: str = "",
        duration: float = 5.0,
        fps: int = 24,
        resolution: str = "1080P",
        negative_prompt: str = "",
    ) -> ProviderResult:
        """Animate an approved first-frame image with Wan 2.5 I2V.

        Duration is clamped to the model's supported 3-10 second range.
        ``resolution`` is restricted to 720P/1080P; manager calls inherit the
        1080P default even when they do not pass the argument explicitly.
        """
        start = time.monotonic()

        if not self._api_key:
            return ProviderResult(
                success=False,
                error="WAN_API_KEY not configured — set it in .env",
                provider=self._provider,
            )

        resolution = str(resolution).upper()
        if resolution not in I2V_RESOLUTIONS:
            return ProviderResult(
                success=False,
                error=f"invalid resolution '{resolution}' — expected 720P or 1080P",
                provider=self._provider,
            )

        dur_sec = max(I2V_DURATION_RANGE[0], min(I2V_DURATION_RANGE[1], int(duration)))
        media_url = image_key

        try:
            if not image_key.startswith("http") and not image_key.startswith("data:"):
                local_path = self._resolve_local_image(image_key)
                media_url, encoded_size = self._encode_local_image(local_path)
                logger.info(
                    "wan25_i2v_image_encoded",
                    path=str(local_path),
                    size=encoded_size,
                )

            input_data = {
                "img_url": media_url,
                "prompt": prompt or "subtle natural motion, gentle movement",
            }
            if negative_prompt:
                input_data["negative_prompt"] = negative_prompt

            payload = {
                "model": WAN25_I2V_MODEL,
                "input": input_data,
                "parameters": {
                    "resolution": resolution,
                    "duration": dur_sec,
                    "prompt_extend": False,
                },
            }

            logger.info(
                "dashscope_wan25_i2v_submit",
                image_key=image_key,
                prompt=(prompt or "")[:80],
                resolution=resolution,
                duration=dur_sec,
            )

            client = await self._get_client()
            resp = await client.post(
                TASK_SUBMIT_URL,
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            task_data = resp.json()
            task_id = (task_data.get("output") or {}).get("task_id", "")
            if not task_id:
                return ProviderResult(
                    success=False,
                    error=f"No task_id returned: {task_data}",
                    provider=self._provider,
                    latency_ms=(time.monotonic() - start) * 1000,
                )

            result = await self._poll_task(task_id)
            elapsed_ms = (time.monotonic() - start) * 1000
            if result and result.get("_failed"):
                return ProviderResult(
                    success=False,
                    error=f"DashScope task failed: {result.get('message', 'unknown')}",
                    provider=self._provider,
                    latency_ms=elapsed_ms,
                )
            if not result:
                return ProviderResult(
                    success=False,
                    error=f"DashScope task {task_id} timed out",
                    provider=self._provider,
                    latency_ms=elapsed_ms,
                )

            video_url = self._extract_video_url(result)
            if not video_url:
                return ProviderResult(
                    success=False,
                    error=f"DashScope task {task_id} succeeded without a video URL",
                    provider=self._provider,
                    latency_ms=elapsed_ms,
                )

            return ProviderResult(
                success=True,
                data={
                    "video_url": video_url,
                    "video_key": f"videos/{task_id}.mp4",
                    "source_image": image_key,
                    "prompt": prompt,
                    "duration": dur_sec,
                    "fps": fps,
                    "resolution": resolution,
                    "generation_time_ms": int(elapsed_ms),
                    "task_id": task_id,
                    "model": WAN25_I2V_MODEL,
                    "is_mock": False,
                },
                provider=self._provider,
                latency_ms=elapsed_ms,
            )

        except FileNotFoundError as e:
            return ProviderResult(
                success=False,
                error=str(e),
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )
        except httpx.HTTPStatusError as e:
            try:
                body = e.response.json()
                error_body = body.get("message") or body.get("code") or str(e)
            except Exception:
                error_body = str(e)
            status = e.response.status_code if e.response is not None else "unknown"
            logger.error("dashscope_wan25_i2v_http_error", status=status, error=error_body)
            return ProviderResult(
                success=False,
                error=f"DashScope HTTP {status}: {error_body}",
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )
        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error="Cannot connect to DashScope API",
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as e:
            logger.error("dashscope_wan25_i2v_failed", error=str(e))
            return ProviderResult(
                success=False,
                error=str(e),
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )
