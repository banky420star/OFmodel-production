"""Persona Studio — Celery Tasks.

Async task definitions for background processing.
These tasks are dispatched by the API routes and executed by the Celery worker.

Each task corresponds to a workflow step and handles:
- Provider selection (mock vs real)
- Error handling with retry
- Progress tracking
- Result persistence
"""

from __future__ import annotations
import asyncio
import json
import time
from typing import Any
from uuid import UUID

import structlog
from celery import shared_task

logger = structlog.get_logger()


def _run_async(coro):
    """Run an async function in a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def task_generate_identity_candidates(self, persona_id: str, persona_data: dict) -> dict:
    """Generate identity candidates for a persona using the LLM provider."""
    from app.providers.registry import get_registry

    logger.info("task_started", task="generate_candidates", persona_id=persona_id)
    registry = get_registry()

    async def _generate():
        llm = registry.get_llm_provider()
        result = await llm.complete(
            system_prompt="Generate fictional synthetic identity candidates for a social media persona.",
            user_prompt=f"Generate 3 identity candidates for: {json.dumps(persona_data)}",
            schema={"type": "object", "properties": {"candidates": {"type": "array"}}},
        )
        return result

    result = _run_async(_generate())
    return {"success": result.success, "data": result.data, "provider": result.provider}


@shared_task(bind=True, max_retries=3, default_retry_delay=15)
def task_generate_image(self, prompt: str, seed: int, width: int, height: int,
                        lora_path: str = "", lora_strength: float = 0.8,
                        negative_prompt: str = "", steps: int = 30,
                        cfg_scale: float = 7.0) -> dict:
    """Generate a single image via the configured image provider."""
    from app.providers.registry import get_registry

    logger.info("task_started", task="generate_image", seed=seed, prompt=prompt[:50])
    registry = get_registry()

    async def _generate():
        image = registry.get_image_provider()
        return await image.generate(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            steps=steps,
            cfg_scale=cfg_scale,
            seed=seed,
            lora_path=lora_path,
            lora_strength=lora_strength,
        )

    result = _run_async(_generate())

    if not result.success and self.request.retries < self.max_retries:
        logger.warning("task_retry", task="generate_image", error=result.error)
        raise self.retry(exc=Exception(result.error))

    return {"success": result.success, "data": result.data, "error": result.error, "provider": result.provider}


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def task_generate_video(self, image_key: str, prompt: str, duration: float, fps: int) -> dict:
    """Generate video from image via the configured video provider."""
    from app.providers.registry import get_registry

    logger.info("task_started", task="generate_video", image_key=image_key)
    registry = get_registry()

    async def _generate():
        video = registry.get_video_provider()
        return await video.image_to_video(
            image_key=image_key,
            prompt=prompt,
            duration=duration,
            fps=fps,
        )

    result = _run_async(_generate())
    return {"success": result.success, "data": result.data, "error": result.error, "provider": result.provider}


@shared_task(bind=True, max_retries=2, default_retry_delay=15)
def task_synthesize_voice(self, text: str, voice_id: str, speed: float = 1.0,
                          output_format: str = "mp3") -> dict:
    """Synthesize voiceover audio via the configured voice provider."""
    from app.providers.registry import get_registry

    logger.info("task_started", task="synthesize_voice", voice_id=voice_id, text_len=len(text))
    registry = get_registry()

    async def _synthesize():
        voice = registry.get_voice_provider()
        return await voice.synthesize(
            text=text,
            voice_id=voice_id,
            speed=speed,
            output_format=output_format,
        )

    result = _run_async(_synthesize())
    return {"success": result.success, "data": result.data, "error": result.error, "provider": result.provider}


@shared_task(bind=True, max_retries=2)
def task_train_lora(self, dataset_id: str, model_type: str = "lora",
                    rank: int = 16, epochs: int = 10) -> dict:
    """Train a LoRA model for identity consistency."""
    from app.providers.registry import get_registry

    logger.info("task_started", task="train_lora", dataset_id=dataset_id)
    registry = get_registry()

    async def _train():
        trainer = registry.get_trainer_provider()
        return await trainer.train(
            dataset_id=dataset_id,
            model_type=model_type,
            rank=rank,
            epochs=epochs,
        )

    result = _run_async(_train())
    return {"success": result.success, "data": result.data, "error": result.error, "provider": result.provider}


@shared_task(bind=True)
def task_run_workflow_step(self, workflow_id: str, step_id: str, step_type: str, input_data: dict) -> dict:
    """Execute a single workflow step. This is the generic task dispatched by the workflow engine."""
    from app.workflows.engine import workflow_engine

    logger.info("task_started", task="workflow_step", workflow_id=workflow_id, step_type=step_type)

    async def _run():
        return await workflow_engine.execute_step(
            UUID(workflow_id), UUID(step_id), input_data
        )

    result = _run_async(_run())
    return result
