"""Single job runner — Job rows + bounded background execution.

Replaces the three overlapping mechanisms the old repo had (Redis queue loop,
Celery tasks, bare asyncio.create_task). Every background job:

1. Persists a `Job` row (queued → running → completed/failed) for polling.
2. Runs under a global semaphore (`MAX_CONCURRENT_WORKFLOWS`).
3. Enforces a whole-job timeout (`WORKFLOW_STEP_TIMEOUT_SECONDS`).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Coroutine
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Job

logger = logging.getLogger(__name__)

_semaphore_instance: asyncio.Semaphore | None = None


def _semaphore() -> asyncio.Semaphore:
    global _semaphore_instance
    if _semaphore_instance is None:
        _semaphore_instance = asyncio.Semaphore(get_settings().MAX_CONCURRENT_WORKFLOWS)
    return _semaphore_instance


async def _run_job(
    job_id: UUID,
    coro_factory,
    session_factory,
    job_type: str,
) -> None:
    settings = get_settings()
    timeout = settings.WORKFLOW_STEP_TIMEOUT_SECONDS

    # Requeue state: queued → running (in case the route didn't already set it)
    async with session_factory() as db:
        job = await db.get(Job, job_id)
        if job and job.status == "queued":
            job.status = "running"
            await db.commit()

    try:
        async with _semaphore():
            async with asyncio.timeout(timeout):
                result = await coro_factory(job_id)
        async with session_factory() as db:
            job = await db.get(Job, job_id)
            if job and job.status in ("queued", "running"):
                job.status = "completed"
                if not job.message:
                    job.message = str(result)[:500] if result is not None else "Completed"
                await db.commit()
    except asyncio.TimeoutError:
        async with session_factory() as db:
            job = await db.get(Job, job_id)
            if job:
                job.status = "failed"
                job.message = f"Job exceeded {timeout}s timeout"
                await db.commit()
        logger.error("job_timeout", job_type=job_type, job_id=str(job_id))
    except Exception as exc:
        async with session_factory() as db:
            job = await db.get(Job, job_id)
            if job:
                job.status = "failed"
                job.message = (str(exc) or type(exc).__name__)[:500]
                await db.commit()
        logger.exception("job_failed", job_type=job_type, job_id=str(job_id))


async def spawn_job(
    db: AsyncSession,
    *,
    job_type: str,
    persona_id: UUID | None = None,
    shoot_id: UUID | None = None,
    pack_id: UUID | None = None,
    message: str = "",
    metadata: dict | None = None,
    coro_factory,
    session_factory,
) -> Job:
    """Create a Job row and schedule the coroutine under concurrency limits.

    `coro_factory` is a one-arg callable receiving the Job's UUID and returning
    the coroutine to run — built lazily so it only starts when the semaphore
    admits it.
    `session_factory` is the async session factory the coroutine uses for its
    own commits (routes pass `workflow_engine._session_factory` or
    `AsyncSessionLocal`).
    """
    from uuid import uuid4

    job = Job(
        id=uuid4(),
        type=job_type,
        status="queued",
        progress=0,
        message=message,
        persona_id=persona_id,
        shoot_id=shoot_id,
        pack_id=pack_id,
        metadata_json=metadata or {},
    )
    db.add(job)
    await db.commit()

    task = asyncio.create_task(
        _run_job(job.id, coro_factory, session_factory, job_type)
    )
    # Hold a reference so the task isn't garbage-collected mid-run.
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return job


_background_tasks: set[asyncio.Task] = set()


async def drain_background_tasks():
    """Await outstanding background jobs (used by tests / graceful shutdown)."""
    if _background_tasks:
        await asyncio.gather(*list(_background_tasks), return_exceptions=True)