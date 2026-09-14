"""Persona Studio — Model Manager routes.

Every control here mutates real backend manager state (no frontend-only
toggles): pause/resume flip `model_managers.paused`, run-now enqueues a real
ledger task, retry requeues blocked shots through the pipeline.
"""
from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.manager_core import (
    compute_inventory,
    decide_next_action,
    get_or_create_manager,
    log_event,
    manager_wake,
    recover_running_tasks,
    require_active_lock,
    run_task_guarded,
    set_state,
    utcnow,
)
from app.models import (
    ManagerEvent,
    ManagerStatus,
    ManagerTask,
    ManagerTaskStatus,
    ModelManager,
    Persona,
    ShotPlan,
)

router = APIRouter(prefix="/manager", tags=["model-manager"])


async def _persona_or_404(persona_id: UUID, db: AsyncSession) -> Persona:
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    return persona


@router.get("")
async def list_managers(db: AsyncSession = Depends(get_db)):
    """All managers with persona names — the roster for the dashboard."""
    rows = (await db.execute(
        select(ModelManager, Persona.name)
        .join(Persona, Persona.id == ModelManager.persona_id)
    )).all()
    return [
        {
            "manager_id": str(m.id), "persona_id": str(m.persona_id),
            "persona_name": name, "status": m.status.value,
            "paused": bool(m.paused), "autonomy_level": m.autonomy_level,
            "current_objective": m.current_objective,
            "current_task_id": str(m.current_task_id) if m.current_task_id else None,
            "last_heartbeat_at": m.last_heartbeat_at.isoformat() if m.last_heartbeat_at else None,
            "last_success_at": m.last_success_at.isoformat() if m.last_success_at else None,
            "last_error": m.last_error, "failure_count": m.failure_count,
        }
        for m, name in rows
    ]


@router.post("/personas/{persona_id}/start")
async def start_manager(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """Create (or revive) the persistent manager for a persona."""
    persona = await _persona_or_404(persona_id, db)
    mgr = await get_or_create_manager(persona.id, db)
    mgr.paused = 0
    await set_state(db, mgr, ManagerStatus.IDLE, objective="Maintain target content inventory")
    await db.commit()
    return {"manager_id": str(mgr.id), "status": mgr.status.value, "persona": persona.name}


@router.get("/personas/{persona_id}/dashboard")
async def manager_dashboard(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """The full Manager view: status, objective, task, pipeline, inventory,
    jobs, failures, decisions, events — all from real rows."""
    persona = await _persona_or_404(persona_id, db)
    mgr = await get_or_create_manager(persona.id, db)

    inventory = await compute_inventory(db, persona.id)
    from app.manager_core import _recent_provider_failure
    next_decision = decide_next_action(inventory, mgr,
                                       last_error_kind=await _recent_provider_failure(db, mgr))

    current_task = None
    if mgr.current_task_id:
        t = await db.get(ManagerTask, mgr.current_task_id)
        if t:
            current_task = _task_dto(t)

    tasks = (await db.execute(
        select(ManagerTask).where(ManagerTask.manager_id == mgr.id)
        .order_by(ManagerTask.created_at.desc()).limit(50)
    )).scalars().all()

    events = (await db.execute(
        select(ManagerEvent).where(ManagerEvent.manager_id == mgr.id)
        .order_by(ManagerEvent.created_at.desc()).limit(30)
    )).scalars().all()

    # Per-shoot pipeline stage summary
    shoots = (await db.execute(
        select(ShotPlan).where(ShotPlan.persona_id == persona.id)
        .order_by(ShotPlan.created_at.desc()).limit(60)
    )).scalars().all()
    by_shoot: dict[str, dict] = {}
    for s in shoots:
        entry = by_shoot.setdefault(str(s.shoot_id), {
            "shoot_id": str(s.shoot_id), "stages": {
                "plan": 0, "images_total": 0, "images_approved": 0,
                "images_failed": 0, "images_pending": 0,
            }, "shots": []})
        entry["stages"]["images_total"] += 1
        if s.generation_status.value == "APPROVED":
            entry["stages"]["images_approved"] += 1
        elif s.generation_status.value in ("QA_FAILED", "BLOCKED"):
            entry["stages"]["images_failed"] += 1
        else:
            entry["stages"]["images_pending"] += 1
        entry["shots"].append({
            "shot_id": str(s.id), "number": s.shot_number, "type": s.shot_type,
            "status": s.generation_status.value, "qa": s.qa_status,
            "asset_key": s.asset_key, "error": s.error, "retries": s.retry_count,
        })

    lock = (await db.execute(
        select(ManagerEvent).where(ManagerEvent.manager_id == mgr.id, ManagerEvent.kind == "info")
        .limit(1)
    )).scalar_one_or_none()
    from app.models import IdentityLock
    identity_lock = (await db.execute(
        select(IdentityLock).where(IdentityLock.persona_id == persona.id.hex)
    )).scalar_one_or_none()

    failed_tasks = [t for t in tasks if t.status in (ManagerTaskStatus.FAILED, ManagerTaskStatus.BLOCKED)]
    return {
        "manager_id": str(mgr.id),
        "persona_id": str(persona.id),
        "persona_name": persona.name,
        "status": mgr.status.value,
        "paused": bool(mgr.paused),
        "autonomy_level": mgr.autonomy_level,
        "current_objective": mgr.current_objective,
        "current_task": current_task,
        "next_action": next_decision,
        "next_wake_at": mgr.next_wake_at.isoformat() if mgr.next_wake_at else None,
        "last_heartbeat_at": mgr.last_heartbeat_at.isoformat() if mgr.last_heartbeat_at else None,
        "identity_lock": {
            "status": identity_lock.status if identity_lock else "none",
            "generation_allowed": bool(identity_lock and identity_lock.status == "active"),
        },
        "inventory": inventory,
        "pipeline": list(by_shoot.values())[:8],
        "active_jobs": [_task_dto(t) for t in tasks if t.status == ManagerTaskStatus.RUNNING],
        "pending_jobs": [_task_dto(t) for t in tasks if t.status == ManagerTaskStatus.PENDING],
        "failed_jobs": [_task_dto(t) for t in failed_tasks[:10]],
        "recent_tasks": [_task_dto(t) for t in tasks[:15]],
        "recent_decisions": [
            {"at": e.created_at.isoformat(), "message": e.message, "data": e.data}
            for e in events if e.kind == "decision"
        ][:10],
        "event_log": [
            {"at": e.created_at.isoformat(), "kind": e.kind, "message": e.message}
            for e in events
        ],
        "blockers": [
            {"error": t.error, "kind": t.error_kind, "task": t.task_type}
            for t in failed_tasks if t.status == ManagerTaskStatus.BLOCKED
        ][:5],
    }


def _task_dto(t: ManagerTask) -> dict:
    return {
        "id": str(t.id), "task_type": t.task_type, "status": t.status.value,
        "priority": t.priority, "source": t.source, "provider": t.provider,
        "payload": t.payload, "result": t.result, "error": t.error,
        "error_kind": t.error_kind, "retry_count": t.retry_count,
        "max_retries": t.max_retries,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "duration_ms": t.duration_ms,
    }


@router.post("/personas/{persona_id}/wake")
async def wake_manager(persona_id: UUID, source: str = "manual", db: AsyncSession = Depends(get_db)):
    """Run one event-driven manager cycle right now (the heartbeat body)."""
    persona = await _persona_or_404(persona_id, db)
    result = await manager_wake(db, persona.id, source=source)
    return result


@router.post("/personas/{persona_id}/pause")
async def pause_manager(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    persona = await _persona_or_404(persona_id, db)
    mgr = await get_or_create_manager(persona.id, db)
    mgr.paused = 1
    await set_state(db, mgr, ManagerStatus.PAUSED)
    await log_event(db, mgr, "info", "Paused by operator")
    await db.commit()
    return {"manager_id": str(mgr.id), "status": mgr.status.value, "paused": True}


@router.post("/personas/{persona_id}/resume")
async def resume_manager(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    persona = await _persona_or_404(persona_id, db)
    mgr = await get_or_create_manager(persona.id, db)
    mgr.paused = 0
    await set_state(db, mgr, ManagerStatus.IDLE)
    await log_event(db, mgr, "info", "Resumed by operator")
    await db.commit()
    return {"manager_id": str(mgr.id), "status": mgr.status.value, "paused": False}


@router.post("/personas/{persona_id}/run-now")
async def run_now(persona_id: UUID, task_type: str = "CHECK_INVENTORY",
                  db: AsyncSession = Depends(get_db)):
    """Operator-forced run: enqueue + execute a real ledger task."""
    persona = await _persona_or_404(persona_id, db)
    mgr = await get_or_create_manager(persona.id, db)
    from app.manager_core import create_task
    task, created = await create_task(db, mgr, task_type.upper(), {"reason": "operator run-now"},
                                      source="user", dedupe=False)
    await db.commit()
    executed = await run_task_guarded(db, task.id)
    return {"task_id": str(executed.id), "status": executed.status.value,
            "result": executed.result, "error": executed.error}


@router.post("/personas/{persona_id}/retry-failed")
async def retry_failed(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    persona = await _persona_or_404(persona_id, db)
    mgr = await get_or_create_manager(persona.id, db)
    from app.manager_core import create_task
    task, _ = await create_task(db, mgr, "RETRY_JOB", {"reason": "operator retry"},
                                source="user", dedupe=False)
    await db.commit()
    executed = await run_task_guarded(db, task.id)
    return {"task_id": str(executed.id), "status": executed.status.value, "result": executed.result}


@router.post("/personas/{persona_id}/autonomy")
async def set_autonomy(persona_id: UUID, level: int, db: AsyncSession = Depends(get_db)):
    """0 = approve-all, 1 = auto-low-risk, 2 = full-auto (may spend)."""
    persona = await _persona_or_404(persona_id, db)
    mgr = await get_or_create_manager(persona.id, db)
    if level not in (0, 1, 2):
        raise HTTPException(400, "autonomy level must be 0, 1 or 2")
    mgr.autonomy_level = level
    await log_event(db, mgr, "info", f"Autonomy set to level {level}")
    await db.commit()
    return {"manager_id": str(mgr.id), "autonomy_level": level}


@router.get("/tasks/{task_id}")
async def get_task(task_id: UUID, db: AsyncSession = Depends(get_db)):
    t = await db.get(ManagerTask, task_id)
    if not t:
        raise HTTPException(404, "Task not found")
    return _task_dto(t)


@router.get("/restart-recovery")
async def restart_recovery(db: AsyncSession = Depends(get_db)):
    """Requeue tasks orphaned by a restart; report what happened."""
    n = await recover_running_tasks(db)
    return {"recovered": n}
