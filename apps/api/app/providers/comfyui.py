"""Persona Studio — ComfyUI Image Provider.

Connects to a running ComfyUI instance via its HTTP API + websocket.
Generates images using Stable Diffusion workflows.

Requires:
  - ComfyUI running at COMFYUI_URL (default: http://localhost:8188)
  - A checkpoint model loaded (e.g. sd_xl_base_1.0.safetensors)
  - Optional LoRA files for identity consistency
"""

from __future__ import annotations
import asyncio
import json
import random
import time
import uuid
from typing import Any

import httpx
import structlog

from app.providers.base import ImageProvider, ProviderResult

logger = structlog.get_logger()


# Default ComfyUI workflow template for txt2img
DEFAULT_TXT2IMG_WORKFLOW = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "cfg": 7.0,
            "denoise": 1.0,
            "latent_image": ["5", 0],
            "model": ["4", 0],
            "negative": ["7", 0],
            "positive": ["6", 0],
            "sampler_name": "dpmpp_2m",
            "scheduler": "karras",
            "seed": 42,
            "steps": 30,
        },
    },
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {"batch_size": 1, "height": 1024, "width": 1024},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"clip": ["4", 1], "text": "a photo of a woman"},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {"clip": ["4", 1], "text": "bad quality, blurry, distorted"},
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "persona_studio", "images": ["8", 0]},
    },
}

# LoRA injection node
LORA_NODE = {
    "class_type": "LoraLoader",
    "inputs": {
        "lora_name": "model.safetensors",
        "strength_model": 0.8,
        "strength_clip": 0.8,
        "model": ["4", 0],
        "clip": ["4", 1],
    },
}


class ComfyUIImageProvider(ImageProvider):
    """Real image generation via ComfyUI API.

    Connects to ComfyUI's REST API to queue prompts and retrieve results.
    Falls back gracefully if ComfyUI is unreachable.
    """

    def __init__(self, base_url: str = "http://localhost:8188", timeout: float = 300):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._provider = "comfyui"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
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
        if seed == -1:
            seed = random.randint(0, 2**31)

        start = time.monotonic()
        try:
            client = await self._get_client()

            # Build workflow
            workflow = json.loads(json.dumps(DEFAULT_TXT2IMG_WORKFLOW))
            workflow["3"]["inputs"]["seed"] = seed
            workflow["3"]["inputs"]["steps"] = steps
            workflow["3"]["inputs"]["cfg"] = cfg_scale
            workflow["5"]["inputs"]["width"] = width
            workflow["5"]["inputs"]["height"] = height
            workflow["6"]["inputs"]["text"] = prompt
            workflow["7"]["inputs"]["text"] = negative_prompt or "bad quality, blurry"

            # Inject LoRA if provided
            if lora_path:
                import copy
                lora_node = copy.deepcopy(LORA_NODE)
                lora_node["inputs"]["lora_name"] = lora_path
                lora_node["inputs"]["strength_model"] = lora_strength
                lora_node["inputs"]["strength_clip"] = lora_strength
                # Rewire: model -> lora -> sampler, clip -> lora -> text encoders
                workflow["3"]["inputs"]["model"] = ["10", 0]
                workflow["3"]["inputs"]["positive"] = ["10", 1] if "positive" in workflow["3"]["inputs"] else ["6", 0]
                workflow["6"]["inputs"]["clip"] = ["10", 1]
                workflow["7"]["inputs"]["clip"] = ["10", 1]
                workflow["10"] = lora_node

            # Queue prompt
            payload = {"prompt": workflow, "client_id": str(uuid.uuid4())}
            resp = await client.post("/prompt", json=payload)
            resp.raise_for_status()
            prompt_id = resp.json()["prompt_id"]

            # Poll for completion
            result = await self._wait_for_completion(prompt_id)
            elapsed_ms = (time.monotonic() - start) * 1000

            if result:
                return ProviderResult(
                    success=True,
                    data={
                        "image_key": result["filename"],
                        "image_url": f"{self._base_url}/view?filename={result['filename']}&subfolder={result.get('subfolder', '')}&type=output",
                        "seed": seed,
                        "width": width,
                        "height": height,
                        "prompt": prompt,
                        "negative_prompt": negative_prompt,
                        "steps": steps,
                        "cfg_scale": cfg_scale,
                        "lora_path": lora_path,
                        "lora_strength": lora_strength,
                        "generation_time_ms": int(elapsed_ms),
                        "prompt_id": prompt_id,
                        "is_mock": False,
                        "provider": self._provider,
                    },
                    provider=self._provider,
                    latency_ms=elapsed_ms,
                )
            else:
                return ProviderResult(
                    success=False,
                    error=f"ComfyUI prompt {prompt_id} did not complete within {self._timeout}s",
                    provider=self._provider,
                    latency_ms=elapsed_ms,
                )

        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error=f"Cannot connect to ComfyUI at {self._base_url}. Is ComfyUI running?",
                provider=self._provider,
            )
        except Exception as e:
            logger.error("comfyui_generate_failed", error=str(e))
            return ProviderResult(
                success=False,
                error=str(e),
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )

    async def _wait_for_completion(self, prompt_id: str) -> dict | None:
        """Poll ComfyUI /history endpoint until prompt completes."""
        client = await self._get_client()
        deadline = time.monotonic() + self._timeout

        while time.monotonic() < deadline:
            try:
                resp = await client.get(f"/history/{prompt_id}")
                if resp.status_code == 200:
                    history = resp.json()
                    if prompt_id in history:
                        outputs = history[prompt_id].get("outputs", {})
                        # Find SaveImage output
                        for node_id, node_output in outputs.items():
                            if "images" in node_output:
                                return node_output["images"][0]
            except Exception:
                pass
            await asyncio.sleep(1.0)

        return None

    async def img2img(
        self, image_key: str, prompt: str, strength: float = 0.75, **kwargs
    ) -> ProviderResult:
        """Image-to-image generation. Uses the same txt2img workflow with denoise < 1.0.

        For full img2img, ComfyUI needs LoadImage + VAEEncode nodes.
        This is a simplified version that adjusts denoise on txt2img.
        """
        width = kwargs.get("width", 1024)
        height = kwargs.get("height", 1024)
        seed = kwargs.get("seed", random.randint(0, 2**31))
        negative_prompt = kwargs.get("negative_prompt", "")
        steps = kwargs.get("steps", 30)
        cfg_scale = kwargs.get("cfg_scale", 7.0)

        try:
            client = await self._get_client()

            # Build workflow with img2img nodes
            workflow = {
                "1": {
                    "class_type": "LoadImage",
                    "inputs": {"image": image_key},
                },
                "2": {
                    "class_type": "VAEEncode",
                    "inputs": {"pixels": ["1", 0], "vae": ["4", 2]},
                },
                "3": {
                    "class_type": "KSampler",
                    "inputs": {
                        "cfg": cfg_scale,
                        "denoise": strength,
                        "latent_image": ["2", 0],
                        "model": ["4", 0],
                        "negative": ["7", 0],
                        "positive": ["6", 0],
                        "sampler_name": "dpmpp_2m",
                        "scheduler": "karras",
                        "seed": seed,
                        "steps": steps,
                    },
                },
                "4": {
                    "class_type": "CheckpointLoaderSimple",
                    "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
                },
                "6": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"clip": ["4", 1], "text": prompt},
                },
                "7": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"clip": ["4", 1], "text": negative_prompt or "bad quality, blurry"},
                },
                "8": {
                    "class_type": "VAEDecode",
                    "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
                },
                "9": {
                    "class_type": "SaveImage",
                    "inputs": {"filename_prefix": "persona_studio_i2i", "images": ["8", 0]},
                },
            }

            start = time.monotonic()
            payload = {"prompt": workflow, "client_id": str(uuid.uuid4())}
            resp = await client.post("/prompt", json=payload)
            resp.raise_for_status()
            prompt_id = resp.json()["prompt_id"]

            result = await self._wait_for_completion(prompt_id)
            elapsed_ms = (time.monotonic() - start) * 1000

            if result:
                return ProviderResult(
                    success=True,
                    data={
                        "image_key": result["filename"],
                        "source": image_key,
                        "seed": seed,
                        "strength": strength,
                        "is_mock": False,
                    },
                    provider=self._provider,
                    latency_ms=elapsed_ms,
                )
            else:
                return ProviderResult(success=False, error="img2img timed out", provider=self._provider)

        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error=f"Cannot connect to ComfyUI at {self._base_url}",
                provider=self._provider,
            )
        except Exception as e:
            return ProviderResult(success=False, error=str(e), provider=self._provider)

    async def upscale(self, image_key: str, scale: int = 2) -> ProviderResult:
        """Upscale via ComfyUI's UpscaleModelLoader + ImageUpscaleWithModel nodes."""
        try:
            client = await self._get_client()

            workflow = {
                "1": {
                    "class_type": "LoadImage",
                    "inputs": {"image": image_key},
                },
                "2": {
                    "class_type": "UpscaleModelLoader",
                    "inputs": {"model_name": "RealESRGAN_x4plus.pth"},
                },
                "3": {
                    "class_type": "ImageUpscaleWithModel",
                    "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]},
                },
                "4": {
                    "class_type": "SaveImage",
                    "inputs": {"filename_prefix": "persona_studio_up", "images": ["3", 0]},
                },
            }

            start = time.monotonic()
            payload = {"prompt": workflow, "client_id": str(uuid.uuid4())}
            resp = await client.post("/prompt", json=payload)
            resp.raise_for_status()
            prompt_id = resp.json()["prompt_id"]

            result = await self._wait_for_completion(prompt_id)
            elapsed_ms = (time.monotonic() - start) * 1000

            if result:
                return ProviderResult(
                    success=True,
                    data={"image_key": result["filename"], "source": image_key, "scale": scale, "is_mock": False},
                    provider=self._provider,
                    latency_ms=elapsed_ms,
                )
            else:
                return ProviderResult(success=False, error="upscale timed out", provider=self._provider)

        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error=f"Cannot connect to ComfyUI at {self._base_url}",
                provider=self._provider,
            )
        except Exception as e:
            return ProviderResult(success=False, error=str(e), provider=self._provider)

    async def health_check(self) -> ProviderResult:
        try:
            client = await self._get_client()
            resp = await client.get("/system_stats")
            if resp.status_code == 200:
                stats = resp.json()
                return ProviderResult(
                    success=True,
                    data={
                        "status": "connected",
                        "gpu": stats.get("devices", [{}])[0].get("name", "unknown"),
                        "vram_total": stats.get("devices", [{}])[0].get("vram_total", 0),
                    },
                    provider=self._provider,
                )
            return ProviderResult(success=False, error=f"ComfyUI returned {resp.status_code}", provider=self._provider)
        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error=f"ComfyUI not reachable at {self._base_url}",
                provider=self._provider,
            )
        except Exception as e:
            return ProviderResult(success=False, error=str(e), provider=self._provider)
