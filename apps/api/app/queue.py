"""Persona Studio — Redis Job Queue.

Simple, direct Redis-based job queue. No Celery overhead.
The API enqueues jobs; the worker dequeues and processes them.

Pattern:
  API → enqueue({job_id, type, persona_id, ...}) → Redis LPUSH
  Worker → Redis BRPOP → process → update PostgreSQL
"""

from __future__ import annotations
import json
import os
from typing import Any

import redis.asyncio as aioredis

_redis: aioredis.Redis | None = None

QUEUE_KEY = "persona:jobs"


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
    return _redis


async def enqueue(payload: dict[str, Any]) -> None:
    """Push a job onto the Redis queue."""
    r = await get_redis()
    await r.rpush(QUEUE_KEY, json.dumps(payload, default=str))


async def dequeue() -> dict[str, Any] | None:
    """Pop the next job from the Redis queue. Blocks until available."""
    r = await get_redis()
    result = await r.blpop(QUEUE_KEY, timeout=5)
    if result is None:
        return None
    _, payload = result
    return json.loads(payload)


async def queue_length() -> int:
    """Get the number of pending jobs."""
    r = await get_redis()
    return await r.llen(QUEUE_KEY)


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None
