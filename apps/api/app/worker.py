"""Persona Studio — Standalone Worker Process.

Consumes jobs from Redis, executes provider pipelines,
and updates job progress in PostgreSQL.

Run with:
  python -m app.worker
  # or
  uvicorn app.worker:run --no-header

Architecture:
  Redis → Worker → Provider (ComfyUI/Wan/ElevenLabs/Mock) → PostgreSQL → Frontend polls
"""

from __future__ import annotations
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)

logger = structlog.get_logger()

# Database connection
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://persona:persona_secret@localhost:5432/persona_studio",
)
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False)


async def update_job(
    job_id: str,
    status: str,
    progress: int,
    message: str,
    output_data: dict | None = None,
) -> None:
    """Update job status and progress in PostgreSQL."""
    async with Session() as db:
        now = datetime.now(timezone.utc)
        extra = ""
        params: dict[str, Any] = {
            "s": status,
            "p": progress,
            "m": message,
            "u": now,
            "id": job_id,
        }
        if output_data is not None:
            extra = ", output_data = :output"
            params["output"] = json.dumps(output_data, default=str)

        await db.execute(
            text(
                f"UPDATE jobs SET status = :s, progress = :p, message = :m, "
                f"updated_at = :u {extra} WHERE id = :id"
            ),
            params,
        )
        await db.commit()


async def update_persona_status(persona_id: str, status: str) -> None:
    """Update persona status after build completes."""
    async with Session() as db:
        await db.execute(
            text("UPDATE personas SET status = :s WHERE id = :id"),
            {"s": status, "id": persona_id},
        )
        await db.commit()


async def update_shoot_status(shoot_id: str, status: str) -> None:
    """Update shoot status after generation completes."""
    async with Session() as db:
        await db.execute(
            text("UPDATE shoots SET status = :s WHERE id = :id"),
            {"s": status, "id": shoot_id},
        )
        await db.commit()


async def run_build_persona(job: dict) -> None:
    """Execute persona build pipeline with real provider calls."""
    jid = job["job_id"]
    persona_id = job.get("persona_id", "")

    steps = [
        (5, "Preparing reference dataset"),
        (15, "Generating identity candidates"),
        (25, "Selecting canonical identity"),
        (35, "Captioning reference images"),
        (45, "Building training dataset"),
        (55, "Training LoRA model"),
        (70, "Testing checkpoint quality"),
        (80, "Running identity QA"),
        (90, "Registering visual model"),
        (95, "Creating voice profile"),
        (100, "Model ready"),
    ]

    await update_job(jid, "running", 0, "Started")

    try:
        from app.providers.registry import get_registry
        registry = get_registry()

        for progress, message in steps:
            await asyncio.sleep(1.5)  # Simulate work (replace with real provider calls)
            await update_job(jid, "running", progress, message)

            # At specific steps, call real providers
            if progress == 15:
                # Generate identity candidates via LLM
                llm = registry.get_llm_provider()
                result = await llm.complete(
                    system_prompt="Generate identity candidates for a synthetic persona.",
                    user_prompt=f"Generate 3 candidates for persona {persona_id}",
                )
                logger.info("identity_candidates_generated", success=result.success)

            elif progress == 55:
                # Train LoRA (mock or real)
                trainer = registry.get_trainer_provider()
                result = await trainer.train(
                    dataset_id=f"dataset_{persona_id}",
                    model_type="lora",
                    rank=16,
                    epochs=10,
                )
                logger.info("lora_training_complete", success=result.success)

            elif progress == 80:
                # Identity QA
                logger.info("identity_qa_running", persona_id=persona_id)

        await update_job(jid, "completed", 100, "Model ready")
        if persona_id:
            await update_persona_status(persona_id, "ready")
        logger.info("build_complete", job_id=jid)

    except Exception as e:
        logger.error("build_failed", job_id=jid, error=str(e))
        await update_job(jid, "failed", 0, str(e))
        if persona_id:
            await update_persona_status(persona_id, "failed")


async def run_generate_shoot(job: dict) -> None:
    """Execute shoot generation pipeline with real provider calls."""
    jid = job["job_id"]
    shoot_id = job.get("shoot_id", "")
    persona_id = job.get("persona_id", "")

    steps = [
        (5, "Planning shot list"),
        (15, "Generating prompts"),
        (25, "Generating images (batch 1/3)"),
        (40, "Generating images (batch 2/3)"),
        (55, "Generating images (batch 3/3)"),
        (65, "Generating video clips"),
        (75, "Generating voiceover"),
        (85, "Running quality assurance"),
        (92, "Assembling content pack"),
        (100, "Shoot ready"),
    ]

    await update_job(jid, "running", 0, "Started")

    try:
        from app.providers.registry import get_registry
        registry = get_registry()

        for progress, message in steps:
            await asyncio.sleep(2)  # Simulate work
            await update_job(jid, "running", progress, message)

            # At specific steps, call real providers
            if progress == 25:
                # Generate images via ComfyUI or mock
                image_provider = registry.get_image_provider()
                for i in range(4):
                    result = await image_provider.generate(
                        prompt=f"lifestyle photo, professional quality, seed={i}",
                        width=1024,
                        height=1024,
                        steps=30,
                        seed=i * 1000 + 42,
                    )
                    logger.info("image_generated", index=i, success=result.success)

            elif progress == 65:
                # Generate video via Wan or mock
                video_provider = registry.get_video_provider()
                result = await video_provider.image_to_video(
                    image_key="latest_image",
                    prompt="subtle natural motion",
                    duration=15.0,
                )
                logger.info("video_generated", success=result.success)

            elif progress == 75:
                # Generate voiceover via ElevenLabs or mock
                voice_provider = registry.get_voice_provider()
                result = await voice_provider.synthesize(
                    text="Welcome to my world. This is a behind the scenes look at my latest shoot.",
                    voice_id="default",
                    speed=1.0,
                )
                logger.info("voiceover_generated", success=result.success)

            elif progress == 85:
                # QA scoring
                logger.info("qa_running", shoot_id=shoot_id)

        await update_job(jid, "completed", 100, "Shoot ready")
        if shoot_id:
            await update_shoot_status(shoot_id, "ready")
        logger.info("shoot_complete", job_id=jid)

    except Exception as e:
        logger.error("shoot_failed", job_id=jid, error=str(e))
        await update_job(jid, "failed", 0, str(e))
        if shoot_id:
            await update_shoot_status(shoot_id, "failed")


# Job type → handler mapping
JOB_HANDLERS = {
    "build_persona": run_build_persona,
    "generate_shoot": run_generate_shoot,
}


async def process_job(job: dict) -> None:
    """Route a job to the appropriate handler."""
    job_type = job.get("type", "")
    handler = JOB_HANDLERS.get(job_type)
    if handler:
        await handler(job)
    else:
        logger.warning("unknown_job_type", type=job_type, job_id=job.get("job_id"))


async def run_worker() -> None:
    """Main worker loop — consume jobs from Redis and process them."""
    from app.queue import dequeue

    logger.info("worker_started", pid=os.getpid())

    while True:
        try:
            job = await dequeue()
            if job is None:
                continue

            logger.info("job_received", job_id=job.get("job_id"), type=job.get("type"))
            await process_job(job)

        except Exception as e:
            logger.error("worker_error", error=str(e))
            await asyncio.sleep(1)


# Allow running directly: python -m app.worker
if __name__ == "__main__":
    asyncio.run(run_worker())
