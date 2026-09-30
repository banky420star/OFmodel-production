"""Persona Studio — a build's step numbers must reach the page that shows them.

`GET /jobs/{id}` publishes `current_step` and `total_steps`, and the Create Model
page renders them as "Step 3 of 7" beside a checklist. Nothing ever wrote them:
they are read from `Job.metadata_json`, which the workflow engine left empty, so
they came back null on every build and the page derived the step from `progress`
instead — a number that disagrees with the `message` printed directly above it
("Step 3/7: build_reference_dataset") and reads as step 1 of 7 for the whole
first seventh of a build.

These tests pin the writer, including the part that is easy to get wrong: the
engine rebinds the metadata dict rather than mutating it (a JSON column is only
seen as changed on assignment), and that rebind must carry the keys already in
there. Dropping them would silently discard a job's `result`.
"""

from __future__ import annotations

import uuid

import pytest

from app.database import AsyncSessionLocal
from app.models import Job
from app.workflows.engine import WorkflowEngine


async def _job(db, metadata=None) -> Job:
    job = Job(
        id=uuid.uuid4(), type="persona_build", status="queued", progress=0,
        message="queued", metadata_json=metadata or {},
    )
    db.add(job)
    await db.commit()
    return job


@pytest.mark.asyncio
async def test_step_numbers_are_published_in_job_metadata():
    async with AsyncSessionLocal() as db:
        job = await _job(db)
        engine = WorkflowEngine()

        await engine._update_job_progress(
            job.id, progress=42, message="Step 3/7: build_reference_dataset",
            current_step=3, total_steps=7,
        )

        await db.refresh(job)
        assert job.progress == 42
        assert job.metadata_json["current_step"] == 3
        assert job.metadata_json["total_steps"] == 7

        await db.delete(job)
        await db.commit()


@pytest.mark.asyncio
async def test_existing_metadata_survives_a_progress_update():
    """A job's `result` and anything else already in metadata must not be dropped."""
    async with AsyncSessionLocal() as db:
        job = await _job(db, metadata={"result": {"persona_id": "abc"}, "note": "keep me"})
        engine = WorkflowEngine()

        await engine._update_job_progress(
            job.id, progress=100, message="Build complete", status="completed",
            current_step=7, total_steps=7,
        )

        await db.refresh(job)
        assert job.metadata_json["result"] == {"persona_id": "abc"}
        assert job.metadata_json["note"] == "keep me"
        assert job.metadata_json["current_step"] == 7
        assert job.status == "completed"

        await db.delete(job)
        await db.commit()


@pytest.mark.asyncio
async def test_a_progress_update_without_step_numbers_leaves_them_alone():
    """Callers that pass none (e.g. a job type with no step list) must not erase them."""
    async with AsyncSessionLocal() as db:
        job = await _job(db, metadata={"current_step": 4, "total_steps": 7})
        engine = WorkflowEngine()

        await engine._update_job_progress(job.id, progress=55, message="still working")

        await db.refresh(job)
        assert job.metadata_json["current_step"] == 4
        assert job.metadata_json["total_steps"] == 7

        await db.delete(job)
        await db.commit()


@pytest.mark.asyncio
async def test_steps_are_written_where_the_jobs_endpoint_reads_them():
    """The contract the UI uses: GET /jobs/{id} reads these two exact keys."""
    async with AsyncSessionLocal() as db:
        job = await _job(db)
        engine = WorkflowEngine()
        await engine._update_job_progress(
            job.id, progress=71, message="Step 5/7: validate_identity",
            current_step=5, total_steps=7,
        )

        await db.refresh(job)
        # Mirrors app/routes/system.py:get_job
        published = {
            "current_step": job.metadata_json.get("current_step") if job.metadata_json else None,
            "total_steps": job.metadata_json.get("total_steps") if job.metadata_json else None,
        }
        assert published == {"current_step": 5, "total_steps": 7}

        await db.delete(job)
        await db.commit()
