"""Persona Studio — GPU Worker Client.

Communicates with remote GPU workers for heavy compute tasks:
- Image generation (ComfyUI)
- Video generation (Wan)
- LoRA training

Workers register their capabilities and the scheduler routes jobs accordingly.
"""

from __future__ import annotations
import asyncio
import os
import time
from enum import Enum
from typing import Any
from uuid import uuid4

import httpx
import structlog

from app.providers.base import ProviderResult

logger = structlog.get_logger()


class WorkerCapability(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    TRAINING = "training"
    UPSCALE = "upscale"


class WorkerInfo:
    """Registered GPU worker with capability information."""

    def __init__(self, worker_id: str, url: str, capabilities: list[str],
                 vram_gb: float = 0, gpu_name: str = ""):
        self.worker_id = worker_id
        self.url = url.rstrip("/")
        self.capabilities = capabilities
        self.vram_gb = vram_gb
        self.gpu_name = gpu_name
        self.active_jobs = 0
        self.last_heartbeat = time.time()

    def can_handle(self, capability: str) -> bool:
        return capability in self.capabilities


class GPUWorkerClient:
    """Client for communicating with remote GPU workers.

    Worker API endpoints:
        POST /jobs/image        — Queue image generation
        POST /jobs/video        — Queue video generation
        POST /jobs/train        — Queue LoRA training
        GET  /jobs/{id}         — Check job status
        POST /jobs/{id}/cancel  — Cancel a job
        GET  /health            — Worker health check
        GET  /capabilities      — Worker capability info
    """

    def __init__(self):
        self._workers: dict[str, WorkerInfo] = {}
        self._clients: dict[str, httpx.AsyncClient] = {}
        # Auto-register workers from env
        worker_url = os.getenv("GPU_WORKER_URL", "")
        if worker_url:
            self.register_worker("gpu-01", worker_url, ["image", "video", "training", "upscale"])

    def register_worker(self, worker_id: str, url: str, capabilities: list[str],
                        vram_gb: float = 0, gpu_name: str = ""):
        """Register a GPU worker."""
        self._workers[worker_id] = WorkerInfo(worker_id, url, capabilities, vram_gb, gpu_name)
        logger.info("worker_registered", worker_id=worker_id, url=url, capabilities=capabilities)

    def unregister_worker(self, worker_id: str):
        """Remove a GPU worker."""
        self._workers.pop(worker_id, None)
        self._clients.pop(worker_id, None)

    def get_worker_for_task(self, capability: str) -> WorkerInfo | None:
        """Find the best available worker for a task."""
        candidates = [w for w in self._workers.values() if w.can_handle(capability)]
        if not candidates:
            return None
        # Pick the worker with fewest active jobs
        return min(candidates, key=lambda w: w.active_jobs)

    async def _get_client(self, worker: WorkerInfo) -> httpx.AsyncClient:
        if worker.worker_id not in self._clients or self._clients[worker.worker_id].is_closed:
            self._clients[worker.worker_id] = httpx.AsyncClient(
                base_url=worker.url, timeout=300.0
            )
        return self._clients[worker.worker_id]

    async def submit_image_job(
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
        checkpoint: str = "",
    ) -> ProviderResult:
        """Submit an image generation job to a GPU worker."""
        worker = self.get_worker_for_task("image")
        if not worker:
            return ProviderResult(
                success=False,
                error="No GPU worker available for image generation",
                provider="gpu_worker",
            )

        start = time.monotonic()
        try:
            client = await self._get_client(worker)
            payload = {
                "type": "image",
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "width": width,
                "height": height,
                "steps": steps,
                "cfg_scale": cfg_scale,
                "seed": seed,
                "lora_path": lora_path,
                "lora_strength": lora_strength,
                "checkpoint": checkpoint,
            }
            resp = await client.post("/jobs/image", json=payload)
            resp.raise_for_status()
            job = resp.json()
            job_id = job.get("job_id", "")

            worker.active_jobs += 1
            # Poll for completion
            result = await self._poll_worker_job(worker, job_id)
            worker.active_jobs = max(0, worker.active_jobs - 1)

            elapsed_ms = (time.monotonic() - start) * 1000
            if result:
                return ProviderResult(
                    success=True,
                    data={**result, "worker_id": worker.worker_id, "is_mock": False},
                    provider="gpu_worker",
                    latency_ms=elapsed_ms,
                )
            return ProviderResult(
                success=False,
                error=f"GPU worker job {job_id} timed out",
                provider="gpu_worker",
                latency_ms=elapsed_ms,
            )

        except Exception as e:
            worker.active_jobs = max(0, worker.active_jobs - 1)
            return ProviderResult(success=False, error=str(e), provider="gpu_worker")

    async def submit_video_job(
        self, image_key: str, prompt: str = "",
        duration: float = 4.0, fps: int = 24,
    ) -> ProviderResult:
        """Submit a video generation job."""
        worker = self.get_worker_for_task("video")
        if not worker:
            return ProviderResult(success=False, error="No GPU worker for video", provider="gpu_worker")

        start = time.monotonic()
        try:
            client = await self._get_client(worker)
            payload = {
                "type": "video",
                "image_key": image_key,
                "prompt": prompt,
                "duration": duration,
                "fps": fps,
            }
            resp = await client.post("/jobs/video", json=payload)
            resp.raise_for_status()
            job = resp.json()
            job_id = job.get("job_id", "")

            worker.active_jobs += 1
            result = await self._poll_worker_job(worker, job_id)
            worker.active_jobs = max(0, worker.active_jobs - 1)

            elapsed_ms = (time.monotonic() - start) * 1000
            if result:
                return ProviderResult(
                    success=True,
                    data={**result, "worker_id": worker.worker_id, "is_mock": False},
                    provider="gpu_worker",
                    latency_ms=elapsed_ms,
                )
            return ProviderResult(success=False, error="Video job timed out", provider="gpu_worker")

        except Exception as e:
            worker.active_jobs = max(0, worker.active_jobs - 1)
            return ProviderResult(success=False, error=str(e), provider="gpu_worker")

    async def _poll_worker_job(self, worker: WorkerInfo, job_id: str) -> dict | None:
        """Poll a worker for job completion."""
        client = await self._get_client(worker)
        deadline = time.monotonic() + 300  # 5 min timeout

        while time.monotonic() < deadline:
            try:
                resp = await client.get(f"/jobs/{job_id}")
                if resp.status_code == 200:
                    status = resp.json()
                    state = status.get("status", "").lower()
                    if state in ("completed", "done", "success"):
                        return status
                    elif state in ("failed", "error"):
                        return None
            except Exception:
                pass
            await asyncio.sleep(2.0)
        return None

    async def health_check_all(self) -> dict[str, dict]:
        """Check health of all registered workers."""
        results = {}
        for worker_id, worker in self._workers.items():
            try:
                client = await self._get_client(worker)
                resp = await client.get("/health")
                results[worker_id] = {
                    "status": "green" if resp.status_code == 200 else "red",
                    "url": worker.url,
                    "capabilities": worker.capabilities,
                    "active_jobs": worker.active_jobs,
                }
            except Exception as e:
                results[worker_id] = {"status": "red", "error": str(e)}
        return results


# Singleton
_worker_client: GPUWorkerClient | None = None


def get_worker_client() -> GPUWorkerClient:
    global _worker_client
    if _worker_client is None:
        _worker_client = GPUWorkerClient()
    return _worker_client
