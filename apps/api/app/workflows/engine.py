"""Persona Studio — Durable Workflow State Machine.

Every workflow is resumable. If the server restarts, the workflow resumes
from its last persisted step — it does NOT start from scratch.

States:
    PENDING → RUNNING → COMPLETED
                   ↓
                 FAILED → RETRYING → RUNNING
                   ↓
                 PAUSED
                   ↓
                 CANCELLED
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import (
    Workflow, WorkflowStep,
    WorkflowStatus, WorkflowStepStatus,
)

logger = structlog.get_logger()

# Valid state transitions
TRANSITIONS: dict[WorkflowStatus, set[WorkflowStatus]] = {
    WorkflowStatus.PENDING: {WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED},
    WorkflowStatus.RUNNING: {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.PAUSED, WorkflowStatus.CANCELLED},
    WorkflowStatus.FAILED: {WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED},  # retry
    WorkflowStatus.PAUSED: {WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED},
    WorkflowStatus.COMPLETED: set(),
    WorkflowStatus.CANCELLED: set(),
}


def _utcnow():
    return datetime.now(timezone.utc)


class WorkflowEngine:
    """Durable workflow executor with step-level persistence."""

    def __init__(self, session_factory=None):
        self._step_handlers: dict[str, Callable[..., Coroutine]] = {}
        self._session_factory = session_factory or AsyncSessionLocal

    def register_step(self, step_type: str, handler: Callable[..., Coroutine]):
        """Register a handler for a workflow step type."""
        self._step_handlers[step_type] = handler
        logger.info("step_registered", step_type=step_type)

    async def create_workflow(
        self,
        name: str,
        workflow_type: str,
        persona_id: UUID | None = None,
        input_data: dict | None = None,
        steps: list[dict[str, Any]] | None = None,
    ) -> Workflow:
        """Create a new workflow with its steps."""
        async with self._session_factory() as db:
            workflow = Workflow(
                id=uuid4(),
                name=name,
                workflow_type=workflow_type,
                persona_id=persona_id,
                status=WorkflowStatus.PENDING,
                input_data=input_data or {},
            )
            db.add(workflow)

            if steps:
                for i, step_def in enumerate(steps):
                    step = WorkflowStep(
                        id=uuid4(),
                        workflow_id=workflow.id,
                        name=step_def["name"],
                        step_type=step_def["step_type"],
                        order=i,
                        input_data=step_def.get("input_data", {}),
                        provider_name=step_def.get("provider_name", ""),
                    )
                    db.add(step)

            await db.commit()
            await db.refresh(workflow)

            logger.info("workflow_created", workflow_id=str(workflow.id), name=name, type=workflow_type)
            return workflow

    async def start_workflow(self, workflow_id: UUID) -> Workflow:
        """Start or resume a workflow."""
        async with self._session_factory() as db:
            workflow = await db.get(Workflow, workflow_id)
            if not workflow:
                raise ValueError(f"Workflow {workflow_id} not found")

            if workflow.status not in {WorkflowStatus.PENDING, WorkflowStatus.FAILED}:
                raise ValueError(f"Cannot start workflow in status {workflow.status}")

            workflow.status = WorkflowStatus.RUNNING
            workflow.started_at = _utcnow()
            await db.commit()
            await db.refresh(workflow)

            logger.info("workflow_started", workflow_id=str(workflow_id))
            return workflow

    async def execute_step(self, workflow_id: UUID, step_id: UUID, context: dict | None = None) -> WorkflowStep:
        """Execute a single workflow step."""
        async with self._session_factory() as db:
            step = await db.get(WorkflowStep, step_id)
            if not step:
                raise ValueError(f"Step {step_id} not found")

            handler = self._step_handlers.get(step.step_type)
            if not handler:
                step.status = WorkflowStepStatus.FAILED
                step.error_message = f"No handler for step type: {step.step_type}"
                await db.commit()
                await db.refresh(step)
                return step

            step.status = WorkflowStepStatus.RUNNING
            step.started_at = _utcnow()
            await db.commit()

            try:
                result = await handler(
                    workflow_id=workflow_id,
                    step_id=step_id,
                    input_data={**step.input_data, **(context or {})},
                    db=db,
                )
                step.status = WorkflowStepStatus.COMPLETED
                step.output_data = result if isinstance(result, dict) else {"result": result}
                step.completed_at = _utcnow()
            except Exception as e:
                logger.error("step_failed", step_id=str(step_id), error=str(e))
                step.status = WorkflowStepStatus.FAILED
                step.error_message = str(e)
                step.completed_at = _utcnow()

            await db.commit()
            await db.refresh(step)
            return step

    async def run_workflow(self, workflow_id: UUID, job_id: UUID | None = None) -> Workflow:
        """Execute all steps of a workflow in sequence.
        
        If job_id is provided, updates the Job record with progress after each step.
        """
        workflow = await self.start_workflow(workflow_id)

        async with self._session_factory() as db:
            result = await db.execute(
                select(WorkflowStep)
                .where(WorkflowStep.workflow_id == workflow_id)
                .order_by(WorkflowStep.order)
            )
            steps = result.scalars().all()

        total_steps = len(steps)
        context: dict[str, Any] = {"workflow_id": str(workflow_id), **(workflow.input_data or {})}
        failed = False

        for i, step in enumerate(steps):
            if step.status == WorkflowStepStatus.COMPLETED:
                context[f"step_{step.order}_output"] = step.output_data
                continue

            # Update job progress before each step
            if job_id:
                await self._update_job_progress(
                    job_id, progress=int((i / total_steps) * 100),
                    message=f"Step {i+1}/{total_steps}: {step.name}"
                )

            logger.info("executing_step", step_id=str(step.id), name=step.name, order=step.order)
            updated_step = await self.execute_step(workflow_id, step.id, context=context)
            context[f"step_{step.order}_output"] = updated_step.output_data or {}

            # Update progress after each step completes
            if job_id:
                await self._update_job_progress(
                    job_id,
                    progress=int(((i + 1) / total_steps) * 100),
                    message=f"Step {i+1}/{total_steps}: {step.name} ✓",
                )

            if updated_step.status == WorkflowStepStatus.FAILED:
                failed = True
                if job_id:
                    await self._update_job_progress(
                        job_id, progress=int(((i + 1) / total_steps) * 100),
                        message=f"Failed at: {step.name}", status="failed"
                    )
                break

        # Update workflow status
        async with self._session_factory() as db:
            workflow = await db.get(Workflow, workflow_id)
            if failed:
                workflow.status = WorkflowStatus.FAILED
                workflow.error_message = "One or more steps failed"
            else:
                workflow.status = WorkflowStatus.COMPLETED
                workflow.completed_at = _utcnow()
                outputs = {}
                for key, val in context.items():
                    if key.startswith("step_"):
                        outputs[key] = val
                workflow.output_data = outputs
            await db.commit()
            await db.refresh(workflow)

        # Final job update
        if job_id:
            final_status = "completed" if not failed else "failed"
            final_msg = "Build complete" if not failed else "Build failed"
            await self._update_job_progress(
                job_id, progress=100 if not failed else int(((i+1) / total_steps) * 100),
                message=final_msg, status=final_status
            )

        logger.info(
            "workflow_finished",
            workflow_id=str(workflow_id),
            status=workflow.status.value,
        )
        return workflow

    async def _update_job_progress(self, job_id: UUID, progress: int, message: str, status: str = "running"):
        """Update a Job record with current progress."""
        from app.models import Job
        try:
            async with self._session_factory() as db:
                job = await db.get(Job, job_id)
                if job:
                    job.progress = min(progress, 100)
                    job.message = message
                    job.status = status
                    await db.commit()
        except Exception as e:
            logger.error("job_update_failed", job_id=str(job_id), error=str(e))

    async def retry_workflow(self, workflow_id: UUID) -> Workflow:
        """Retry a failed workflow from the failed step."""
        async with self._session_factory() as db:
            workflow = await db.get(Workflow, workflow_id)
            if not workflow:
                raise ValueError(f"Workflow {workflow_id} not found")
            if workflow.status != WorkflowStatus.FAILED:
                raise ValueError(f"Can only retry failed workflows, current status: {workflow.status}")

            workflow.retry_count += 1
            workflow.status = WorkflowStatus.PENDING
            workflow.error_message = ""

            # Reset failed steps
            result = await db.execute(
                select(WorkflowStep).where(
                    WorkflowStep.workflow_id == workflow_id,
                    WorkflowStep.status == WorkflowStepStatus.FAILED,
                )
            )
            for step in result.scalars().all():
                step.status = WorkflowStepStatus.PENDING
                step.error_message = ""
                step.output_data = {}

            await db.commit()
            await db.refresh(workflow)

        return await self.run_workflow(workflow_id)

    async def cancel_workflow(self, workflow_id: UUID) -> Workflow:
        """Cancel a running workflow."""
        async with self._session_factory() as db:
            workflow = await db.get(Workflow, workflow_id)
            if not workflow:
                raise ValueError(f"Workflow {workflow_id} not found")
            if workflow.status in {WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED}:
                raise ValueError(f"Cannot cancel workflow in status {workflow.status}")

            workflow.status = WorkflowStatus.CANCELLED
            await db.commit()
            await db.refresh(workflow)

        logger.info("workflow_cancelled", workflow_id=str(workflow_id))
        return workflow

    async def get_workflow(self, workflow_id: UUID) -> Workflow | None:
        async with self._session_factory() as db:
            return await db.get(Workflow, workflow_id)

    async def list_workflows(
        self, persona_id: UUID | None = None,
        status: WorkflowStatus | None = None,
        workflow_type: str | None = None,
        limit: int = 50,
    ) -> list[Workflow]:
        async with self._session_factory() as db:
            query = select(Workflow)
            if persona_id:
                query = query.where(Workflow.persona_id == persona_id)
            if status:
                query = query.where(Workflow.status == status)
            if workflow_type:
                query = query.where(Workflow.workflow_type == workflow_type)
            query = query.order_by(Workflow.created_at.desc()).limit(limit)
            result = await db.execute(query)
            return list(result.scalars().all())


# Global singleton
workflow_engine = WorkflowEngine()
