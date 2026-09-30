"""ComfyUI video generation — SVD image-to-video, on the local machine.

This exists because the video capability had nothing behind it that could ever
work here. `wan_video.py` POSTs to a Wan-compatible server on localhost:8080
that has never been installed, and `wan_dashscope.py` needs a paid cloud key;
`$0` budget rules that out. Meanwhile ComfyUI is already running for images and
can host SVD (Stable Video Diffusion) natively, so this adapter reuses the same
server, the same client, and the same "queue a workflow and poll /history"
pattern as `comfyui.py`.

**The limitation is real and is enforced rather than papered over.** SVD is an
image-to-video model: it animates a frame you already have. It cannot generate
video from a text prompt, so `text_to_video` fails with that reason instead of
quietly animating a blank frame and calling it a result. Anything that wants
text-to-video needs a different model (Wan, CogVideoX) and a different adapter.

Output is short by nature: SVD-XT is trained on 25 frames, which is ~1 s at
24 fps or ~3 s at 8 fps. Real creators post clips in that range, so it is
usable — but it is not a 15-second video, and `duration` is reported back as
what was actually produced rather than what was asked for.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from pathlib import Path

import httpx

from app.providers.base import ProviderResult, VideoProvider

try:  # structlog is used elsewhere in this package; keep the same logging shape.
    import structlog

    logger = structlog.get_logger()
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger(__name__)


# SVD-XT's training length. Going beyond it produces drift, not more video.
SVD_MAX_FRAMES = 25
# SVD's motion is trained around low frame rates; 8 fps reads as natural motion
# where 24 fps reads as a stutter over 25 frames.
DEFAULT_FPS = 8


def _build_svd_workflow(
    *,
    checkpoint: str,
    image_name: str,
    width: int,
    height: int,
    frames: int,
    fps: int,
    motion_bucket_id: int,
    augmentation_level: float,
    steps: int,
    cfg: float,
    min_cfg: float,
    seed: int,
    filename_prefix: str,
) -> dict:
    """The SVD graph, node ids fixed so a failure names a node that exists.

    Schemas verified against the running server's /object_info rather than
    from memory: `SVD_img2vid_Conditioning` takes (clip_vision, init_image,
    vae, width, height, video_frames, motion_bucket_id, fps,
    augmentation_level) and returns (positive, negative, latent_image).
    """
    return {
        "1": {
            "class_type": "ImageOnlyCheckpointLoader",
            "inputs": {"ckpt_name": checkpoint},
        },
        "2": {
            "class_type": "LoadImage",
            "inputs": {"image": image_name, "upload": "image"},
        },
        "3": {
            "class_type": "SVD_img2vid_Conditioning",
            "inputs": {
                "clip_vision": ["1", 1],
                "init_image": ["2", 0],
                "vae": ["1", 2],
                "width": width,
                "height": height,
                "video_frames": frames,
                "motion_bucket_id": motion_bucket_id,
                "fps": fps,
                "augmentation_level": augmentation_level,
            },
        },
        "4": {
            "class_type": "VideoLinearCFGGuidance",
            "inputs": {"model": ["1", 0], "min_cfg": min_cfg},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["4", 0],
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "karras",
                "positive": ["3", 0],
                "negative": ["3", 1],
                "latent_image": ["3", 2],
                "denoise": 1.0,
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveWEBM",
            "inputs": {
                "images": ["6", 0],
                "filename_prefix": filename_prefix,
                "codec": "vp9",
                "fps": float(fps),
                "crf": 20.0,
            },
        },
    }


class ComfyUIVideoProvider(VideoProvider):
    """Image-to-video through the local ComfyUI server, using SVD."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8188",
        timeout: float = 2700.0,
        checkpoint: str = "svd_xt.safetensors",
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._checkpoint = checkpoint
        self._provider = "comfyui_video"
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        return self._client

    # ── health ────────────────────────────────────────────────────────

    async def health_check(self) -> ProviderResult:
        """Reachable AND the SVD weights are actually installed.

        A reachable ComfyUI with no video checkpoint is the exact state this
        provider was written for, so reporting "green" on reachability alone
        would be the same class of lie as a provider that answers 200 for work
        it never did.
        """
        try:
            client = await self._get_client()
            response = await client.get("/object_info")
            response.raise_for_status()
            available = (
                response.json()
                .get("ImageOnlyCheckpointLoader", {})
                .get("input", {})
                .get("required", {})
                .get("ckpt_name", [[], {}])[0]
            )
        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error=f"Cannot connect to ComfyUI at {self._base_url}. Is ComfyUI running?",
                provider=self._provider,
            )
        except Exception as exc:
            return ProviderResult(success=False, error=str(exc), provider=self._provider)

        if self._checkpoint not in available:
            return ProviderResult(
                success=False,
                error=(
                    f"SVD checkpoint {self._checkpoint!r} is not installed. Download it "
                    "into .local/ComfyUI/models/checkpoints, or set "
                    "COMFYUI_VIDEO_CHECKPOINT to an installed filename. "
                    f"Installed: {available}"
                ),
                provider=self._provider,
            )

        return ProviderResult(
            success=True,
            data={"checkpoint": self._checkpoint, "installed": available},
            provider=self._provider,
        )

    # ── work ──────────────────────────────────────────────────────────

    async def image_to_video(
        self,
        image_key: str,
        prompt: str = "",
        duration: float = 3.0,
        fps: int = DEFAULT_FPS,
        *,
        motion_bucket_id: int = 127,
        augmentation_level: float = 0.0,
        steps: int = 25,
        cfg: float = 2.5,
        min_cfg: float = 1.0,
        seed: int = -1,
        width: int = 1024,
        height: int = 576,
        source_bytes: bytes | None = None,
    ) -> ProviderResult:
        """Animate `image_key` (or `source_bytes`) into a short clip.

        `prompt` is accepted by the `VideoProvider` interface but **SVD does not
        use one** — it is unconditioned on text, so passing a prompt changes
        nothing. It is recorded in the result rather than silently dropped, so
        a caller can see that a prompt it supplied had no effect instead of
        assuming it steered the motion. Motion is controlled by
        `motion_bucket_id` (higher = more movement) and `augmentation_level`
        (higher = looser adherence to the source frame).
        """
        if seed == -1:
            seed = random.randint(0, 2**31)
        if duration <= 0:
            return ProviderResult(
                success=False, error=f"duration must be positive, got {duration}",
                provider=self._provider,
            )

        if source_bytes is None:
            return ProviderResult(
                success=False,
                error=(
                    "SVD animates an existing frame, so source image bytes are "
                    "required. Pass source_bytes (or a local path via "
                    "source_path) — this provider will not animate a blank image "
                    "and call it a result."
                ),
                provider=self._provider,
            )

        # Frames implied by the requested duration, capped at what SVD-XT can
        # actually hold. The cap is applied here and reported, not hidden.
        frames = max(1, min(int(round(duration * fps)), SVD_MAX_FRAMES))
        actual_duration = frames / fps

        start = time.monotonic()
        try:
            client = await self._get_client()

            # Upload the source frame so the LoadImage node can read it.
            upload = await client.post(
                "/upload/image",
                files={"image": ("source.png", source_bytes, "image/png")},
                data={"overwrite": "true"},
            )
            upload.raise_for_status()
            uploaded = upload.json()
            image_name = uploaded.get("name") or "source.png"

            workflow = _build_svd_workflow(
                checkpoint=self._checkpoint,
                image_name=image_name,
                width=width,
                height=height,
                frames=frames,
                fps=fps,
                motion_bucket_id=motion_bucket_id,
                augmentation_level=augmentation_level,
                steps=steps,
                cfg=cfg,
                min_cfg=min_cfg,
                seed=seed,
                filename_prefix="persona_video",
            )

            resp = await client.post(
                "/prompt", json={"prompt": workflow, "client_id": str(uuid.uuid4())}
            )
            resp.raise_for_status()
            prompt_id = resp.json()["prompt_id"]

            result = await self._wait_for_completion(prompt_id)
            elapsed_ms = (time.monotonic() - start) * 1000

            if not result:
                return ProviderResult(
                    success=False,
                    error=(
                        f"ComfyUI prompt {prompt_id} did not complete within "
                        f"{self._timeout}s"
                    ),
                    provider=self._provider,
                    latency_ms=elapsed_ms,
                )

            video_resp = await client.get(
                "/view",
                params={
                    "filename": result["filename"],
                    "subfolder": result.get("subfolder", ""),
                    "type": "output",
                },
            )
            video_resp.raise_for_status()

            return ProviderResult(
                success=True,
                data={
                    "video_bytes": video_resp.content,
                    "video_key": result["filename"],
                    "video_url": (
                        f"{self._base_url}/view?filename={result['filename']}"
                        f"&subfolder={result.get('subfolder', '')}&type=output"
                    ),
                    "container": Path(result["filename"]).suffix.lstrip(".") or "webm",
                    "seed": seed,
                    "width": width,
                    "height": height,
                    "fps": fps,
                    "frames": frames,
                    # What was actually produced, not what was asked for.
                    "requested_duration": duration,
                    "duration": actual_duration,
                    "duration_capped": actual_duration < duration - 1e-9,
                    # Recorded so a caller can see it had no effect on the result.
                    "prompt_used": False,
                    "prompt_ignored": prompt,
                    "motion_bucket_id": motion_bucket_id,
                    "augmentation_level": augmentation_level,
                    "steps": steps,
                    "cfg": cfg,
                    "generation_time_ms": int(elapsed_ms),
                    "prompt_id": prompt_id,
                    "is_mock": False,
                    "provider": self._provider,
                },
                provider=self._provider,
                latency_ms=elapsed_ms,
            )

        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error=f"Cannot connect to ComfyUI at {self._base_url}. Is ComfyUI running?",
                provider=self._provider,
            )
        except Exception as exc:
            logger.error("comfyui_video_failed", error=str(exc))
            return ProviderResult(
                success=False,
                error=str(exc),
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )

    async def text_to_video(
        self,
        prompt: str,
        duration: float = 20.0,
        width: int = 1024,
        height: int = 576,
    ) -> ProviderResult:
        """Always fails, with the reason. SVD has no text conditioning.

        Returning a clip here would be the worst outcome available: an
        animated blank frame that looks like a feature and is not one.
        """
        return ProviderResult(
            success=False,
            error=(
                "The configured video provider (ComfyUI + SVD) is image-to-video "
                "only — SVD has no text conditioning, so it cannot generate video "
                "from a prompt. Text-to-video needs a different model (Wan, "
                "CogVideoX) and a different adapter. Animate an existing frame "
                "with image_to_video() instead."
            ),
            provider=self._provider,
        )

    async def _wait_for_completion(self, prompt_id: str) -> dict | None:
        """Poll /history until the graph finishes.

        Separate from the image provider's version because SVD's SaveWEBM
        reports its output under `gifs`, not `images` — reusing that helper
        would poll forever and report a timeout on a clip that rendered fine.
        """
        client = await self._get_client()
        deadline = time.monotonic() + self._timeout

        while time.monotonic() < deadline:
            try:
                resp = await client.get(f"/history/{prompt_id}")
                if resp.status_code == 200:
                    history = resp.json()
                    if prompt_id in history:
                        outputs = history[prompt_id].get("outputs", {})
                        for node_output in outputs.values():
                            for key in ("gifs", "videos", "images"):
                                if node_output.get(key):
                                    return node_output[key][0]
            except Exception:
                pass
            await asyncio.sleep(2.0)

        return None
