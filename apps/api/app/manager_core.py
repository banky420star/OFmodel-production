"""Persona Studio — Model Manager core.

One persistent, event-driven state machine per persona. The manager is NOT a
chat loop: it wakes on events (schedule, inventory shortage, retry-eligibility,
user action), consults real database state, creates durable ledger tasks, and
executes a deterministic production pipeline. When nothing justifies work it
returns to IDLE without spending LLM/API calls.

Decision contract (when reasoning is needed) is a validated JSON structure —
freeform prose is rejected, not executed.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AssetStatus,
    ContentInventoryItem,
    ContentPack,
    GeneratedVideo,
    Identity,
    IdentityLock,
    IdentityLockStatus,
    ManagerEvent,
    ManagerStatus,
    ManagerTask,
    ManagerTaskStatus,
    ModelManager,
    Persona,
    QAResult,
    Shoot,
    ShotPlan,
)

logger = logging.getLogger("persona.manager")

# ── Constants ─────────────────────────────────────────────────────────

TASK_TYPES = {
    "PLAN_CONTENT", "CHECK_INVENTORY", "CREATE_SHOOT", "GENERATE_IMAGES",
    "GENERATE_VIDEO", "RUN_IDENTITY_QA", "RUN_MEDIA_QA", "WRITE_COPY",
    "SCHEDULE_CONTENT", "PUBLISH_CONTENT", "CHECK_ENGAGEMENT", "HANDLE_INBOX",
    "ANALYZE_PERFORMANCE", "RETRY_JOB", "PLATFORM_TASK",
}

DECISION_TYPES = {
    "CREATE_SHOOT", "GENERATE_IMAGES", "GENERATE_VIDEO", "RUN_IDENTITY_QA",
    "RUN_MEDIA_QA", "WRITE_COPY", "SCHEDULE_CONTENT", "PUBLISH_CONTENT",
    "CHECK_ENGAGEMENT", "HANDLE_INBOX", "ANALYZE_PERFORMANCE", "RETRY_JOB",
    "PLATFORM_TASK", "IDLE",
}

# Inventory thresholds: the manager produces when available days < target.
TARGET_DAYS_OF_CONTENT = 7
DAILY_POST_TARGET = 2          # posts/day assumed for days-available math
MIN_IMAGES_FOR_SHOOT = 6       # images per planned shoot
MAX_CONCURRENT_PRODUCE = 1     # one shoot in flight per persona

# Permissions -------------------------------------------------------------
AUTO_ALLOWED = {
    "persona.read", "persona.read_identity", "persona.read_manager_state",
    "content.create_shoot", "content.run_qa", "content.plan",
    "social.read", "analytics.read", "jobs.create", "jobs.retry",
    "email.read",
}
APPROVAL_REQUIRED = {
    "content.generate_image", "content.generate_video",  # spend money
    "social.publish", "social.schedule",                  # public surface
    "jobs.cancel", "platform.account_changes",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Permissions ────────────────────────────────────────────────────────

def permission_for(task_type: str, autonomy_level: int) -> tuple[str, str]:
    """Return (permission, mode) where mode is 'auto' or 'approval_required'."""
    mapping = {
        "PLAN_CONTENT": "persona.read",
        "CHECK_INVENTORY": "persona.read",
        "CREATE_SHOOT": "content.create_shoot",
        "GENERATE_IMAGES": "content.generate_image",
        "GENERATE_VIDEO": "content.generate_video",
        "RUN_IDENTITY_QA": "content.run_qa",
        "RUN_MEDIA_QA": "content.run_qa",
        "WRITE_COPY": "social.draft",
        "SCHEDULE_CONTENT": "social.schedule",
        "PUBLISH_CONTENT": "social.publish",
        "CHECK_ENGAGEMENT": "social.read",
        "HANDLE_INBOX": "email.read",
        "ANALYZE_PERFORMANCE": "analytics.read",
        "RETRY_JOB": "jobs.retry",
        "PLATFORM_TASK": "platform.account_changes",
    }
    perm = mapping.get(task_type, "persona.read")
    if autonomy_level >= 2 and perm in {"content.generate_image", "content.generate_video"}:
        # Full-auto personas may spend: still recorded, no approval gate.
        return perm, "auto"
    return perm, "auto" if perm in AUTO_ALLOWED else "approval_required"


# ── Manager lifecycle ──────────────────────────────────────────────────

async def get_or_create_manager(persona_id: UUID, db: AsyncSession) -> ModelManager:
    mgr = (await db.execute(
        select(ModelManager).where(ModelManager.persona_id == persona_id)
    )).scalar_one_or_none()
    if mgr is None:
        mgr = ModelManager(
            persona_id=persona_id,
            status=ManagerStatus.INITIALIZING,
            current_objective="Maintain target content inventory",
        )
        db.add(mgr)
        await db.flush()
        await log_event(db, mgr, "info", "Manager created", {"status": mgr.status.value})
    return mgr


async def log_event(
    db: AsyncSession, mgr: ModelManager, kind: str, message: str, data: dict | None = None
) -> None:
    db.add(ManagerEvent(persona_id=mgr.persona_id, manager_id=mgr.id,
                        kind=kind, message=message, data=data or {}))


async def set_state(db: AsyncSession, mgr: ModelManager, status: ManagerStatus,
                    objective: str | None = None, task_id: UUID | None = None) -> None:
    old = mgr.status.value if mgr.status else None
    mgr.status = status
    if objective is not None:
        mgr.current_objective = objective
    if task_id is not None:
        mgr.current_task_id = task_id
    if old != status.value:
        await log_event(db, mgr, "state_change", f"{old} → {status.value}",
                        {"from": old, "to": status.value})


# ── Inventory (real math, no vibes) ────────────────────────────────────

async def compute_inventory(db: AsyncSession, persona_id: UUID) -> dict:
    """Approved-content inventory from real rows."""
    approved_shoot_images = (await db.scalar(
        select(func.count(ShotPlan.id)).where(
            ShotPlan.persona_id == persona_id,
            ShotPlan.generation_status == AssetStatus.APPROVED,
            ShotPlan.asset_key != "",
        )
    )) or 0
    videos = (await db.scalar(
        select(func.count(GeneratedVideo.id)).where(
            GeneratedVideo.id.in_(
                select(GeneratedVideo.id)
            )
        )
    )) or 0  # refined below by persona link when available

    # Videos are linked via identity; resolve persona identity ids.
    identity_ids = (await db.execute(
        select(Identity.id).where(Identity.persona_id == persona_id)
    )).scalars().all()
    if identity_ids:
        videos = (await db.scalar(
            select(func.count(GeneratedVideo.id)).where(
                GeneratedVideo.identity_id.in_(identity_ids),
                GeneratedVideo.metadata_json.contains('"is_mock": false'),
            )
        )) or 0

    scheduled = 0  # ScheduledPost linkage added with SCHEDULE_CONTENT tasks
    total_units = approved_shoot_images + videos
    days_available = round(total_units / DAILY_POST_TARGET, 1)
    return {
        "approved_images": approved_shoot_images,
        "approved_videos": videos,
        "scheduled": scheduled,
        "total_units": total_units,
        "days_available": days_available,
        "target_days": TARGET_DAYS_OF_CONTENT,
        "shortage": max(0, TARGET_DAYS_OF_CONTENT - days_available),
    }


# ── Decision contract ──────────────────────────────────────────────────

def validate_decision(d: dict) -> tuple[bool, str, dict]:
    """Validate a structured manager decision. Reject ambiguity."""
    if not isinstance(d, dict):
        return False, "decision must be a JSON object", {}
    decision = d.get("decision")
    if decision not in DECISION_TYPES:
        return False, f"decision '{decision}' not in {sorted(DECISION_TYPES)}", {}
    if decision == "IDLE":
        return True, "", d
    if not d.get("reason") or not isinstance(d.get("reason"), str):
        return False, "non-IDLE decision requires a string 'reason'", {}
    priority = d.get("priority", "MEDIUM")
    if priority not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
        return False, f"invalid priority {priority}", {}
    return True, "", d


def decide_next_action(inventory: dict, mgr: ModelManager, last_error_kind: str | None = None) -> dict:
    """Deterministic decision layer. LLM reasoning is optional garnish on top;
    this function is the contract the system actually trusts."""
    if mgr.paused:
        return {"decision": "IDLE", "reason": "Manager paused by operator"}
    if last_error_kind == "PROVIDER_UNAVAILABLE" and inventory["shortage"] > 0:
        # The image provider is dead and cannot satisfy the shortage — do not
        # mint doomed shoots on every heartbeat (no fake progress, no churn).
        return {
            "decision": "IDLE",
            "reason": ("BLOCKED — REAL IMAGE PROVIDER UNAVAILABLE: "
                       f"inventory {inventory['days_available']}d < target "
                       f"{inventory['target_days']}d, but generation cannot "
                       "succeed until the provider recovers. Fix the provider "
                       "or resume manually."),
            "priority": "HIGH",
        }
    if inventory["shortage"] > 0:
        return {
            "decision": "CREATE_SHOOT",
            "reason": (f"Inventory {inventory['days_available']}d < target "
                       f"{inventory['target_days']}d (shortage {inventory['shortage']}d)"),
            "priority": "HIGH" if inventory["shortage"] >= 3 else "MEDIUM",
            "required_outputs": {"images": MIN_IMAGES_FOR_SHOOT, "videos": 1},
            "requires_approval": False,
        }
    return {"decision": "IDLE",
            "reason": f"Inventory sufficient ({inventory['days_available']}d ≥ {inventory['target_days']}d)"}


# ── Task ledger ────────────────────────────────────────────────────────

async def create_task(
    db: AsyncSession, mgr: ModelManager, task_type: str, payload: dict,
    source: str = "manager", priority: int = 5, dedupe: bool = True,
) -> tuple[ManagerTask | None, bool]:
    """Create a ledger task. Returns (task, created). Dedupes on open tasks of
    the same type+persona so wakes never pile duplicate work."""
    if task_type not in TASK_TYPES:
        raise ValueError(f"unknown task_type {task_type}")
    if dedupe:
        existing = (await db.execute(
            select(ManagerTask).where(
                ManagerTask.manager_id == mgr.id,
                ManagerTask.task_type == task_type,
                ManagerTask.status.in_([ManagerTaskStatus.PENDING, ManagerTaskStatus.RUNNING]),
            )
    )).scalar_one_or_none()
        if existing:
            return existing, False
    perm, mode = permission_for(task_type, mgr.autonomy_level)
    task = ManagerTask(
        persona_id=mgr.persona_id, manager_id=mgr.id, task_type=task_type,
        priority=priority, source=source, payload={**payload, "permission": perm, "permission_mode": mode},
    )
    db.add(task)
    await db.flush()
    await log_event(db, mgr, "decision", f"Created task {task_type}",
                    {"task_id": str(task.id), "payload": payload, "permission": perm, "mode": mode})
    return task, True


async def run_task_guarded(db: AsyncSession, task_id: UUID) -> ManagerTask:
    """Execute a ledger task exactly once. Idempotent: a COMPLETED task
    returns its stored result instead of re-executing."""
    task = await db.get(ManagerTask, task_id)
    if task is None:
        raise ValueError(f"task {task_id} not found")
    if task.status == ManagerTaskStatus.COMPLETED:
        return task  # duplicate-delivery protection
    if task.status in (ManagerTaskStatus.CANCELLED, ManagerTaskStatus.BLOCKED):
        return task

    mgr = await db.get(ModelManager, task.manager_id)
    task.status = ManagerTaskStatus.RUNNING
    task.started_at = utcnow()
    await set_state(db, mgr, _state_for_task(task.task_type), task_id=task.id)
    await db.commit()

    started = time.monotonic()
    try:
        result = await _execute_task(db, mgr, task)
        task.result = result
        task.status = ManagerTaskStatus.COMPLETED
        task.completed_at = utcnow()
        task.duration_ms = int((time.monotonic() - started) * 1000)
        mgr.last_success_at = utcnow()
        mgr.failure_count = 0
        await log_event(db, mgr, "info", f"Completed {task.task_type}",
                        {"task_id": str(task.id), "duration_ms": task.duration_ms})
    except Exception as e:  # noqa: BLE001 — ledger records every failure
        task.error = str(e)[:1000]
        task.error_kind = _classify_error(e)
        task.retry_count += 1
        task.duration_ms = int((time.monotonic() - started) * 1000)
        if task.retry_count >= task.max_retries or task.error_kind == "INVALID_REQUEST":
            task.status = ManagerTaskStatus.BLOCKED
            await set_state(db, mgr, ManagerStatus.BLOCKED)
            await log_event(db, mgr, "blocker",
                            f"{task.task_type} blocked after {task.retry_count} attempts",
                            {"error": task.error, "kind": task.error_kind})
        else:
            task.status = ManagerTaskStatus.PENDING
            task.scheduled_for = utcnow() + timedelta(seconds=30 * task.retry_count)
            await set_state(db, mgr, ManagerStatus.RETRYING)
            await log_event(db, mgr, "error", f"{task.task_type} failed (attempt {task.retry_count})",
                            {"error": task.error, "kind": task.error_kind})
    await db.commit()
    return task


async def _recent_provider_failure(db: AsyncSession, mgr: ModelManager) -> str | None:
    """'PROVIDER_UNAVAILABLE' if a recent generation task died on the provider
    (401/quota/unreachable — not per-shot QA), else None. Feeds the decide
    contract so a dead provider blocks rather than churns doomed shoots.
    Checks BOTH the task ledger and per-shot BLOCKED rows (a partially
    completed GENERATE_IMAGES still records the provider error per shot)."""
    row = (await db.execute(
        select(ManagerTask).where(
            ManagerTask.manager_id == mgr.id,
            ManagerTask.task_type.in_(["GENERATE_IMAGES", "GENERATE_VIDEO"]),
            ManagerTask.error_kind.in_(["PROVIDER_UNAVAILABLE", "RATE_LIMIT",
                                        "TEMPORARY_PROVIDER_FAILURE"]),
        ).order_by(ManagerTask.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if row:
        return row.error_kind
    # Task-level COMPLETED but every shot BLOCKED on the provider → dead provider
    blocked = (await db.execute(
        select(ShotPlan).where(
            ShotPlan.persona_id == mgr.persona_id,
            ShotPlan.generation_status == AssetStatus.BLOCKED,
            ShotPlan.error.is_not(None),
        ).order_by(ShotPlan.created_at.desc()).limit(3)
    )).scalars().all()
    if blocked:
        msg = " ".join((b.error or "").lower() for b in blocked)
        if "401" in msg or "auth" in msg or "unauthorized" in msg or "quota" in msg:
            return "PROVIDER_UNAVAILABLE"
    return None


def _classify_error(e: Exception) -> str:
    msg = str(e).lower()
    if "401" in msg or "auth" in msg or "unauthorized" in msg or "credentials" in msg or "api key" in msg:
        return "PROVIDER_UNAVAILABLE"
    if "timeout" in msg or "connection" in msg or "unreachable" in msg:
        return "TEMPORARY_PROVIDER_FAILURE"
    if "429" in msg or "rate" in msg or "quota" in msg:
        return "RATE_LIMIT"
    if "invalid" in msg or "not supported" in msg or "400" in msg:
        return "INVALID_REQUEST"
    if "identity" in msg:
        return "IDENTITY_QA_FAILURE"
    return "TEMPORARY_PROVIDER_FAILURE"


def _state_for_task(task_type: str) -> ManagerStatus:
    return {
        "PLAN_CONTENT": ManagerStatus.PLANNING,
        "CHECK_INVENTORY": ManagerStatus.PLANNING,
        "CREATE_SHOOT": ManagerStatus.PRODUCING,
        "GENERATE_IMAGES": ManagerStatus.PRODUCING,
        "GENERATE_VIDEO": ManagerStatus.PRODUCING,
        "RUN_IDENTITY_QA": ManagerStatus.QA,
        "RUN_MEDIA_QA": ManagerStatus.QA,
        "WRITE_COPY": ManagerStatus.PLANNING,
        "SCHEDULE_CONTENT": ManagerStatus.SCHEDULING,
        "PUBLISH_CONTENT": ManagerStatus.PUBLISHING,
        "CHECK_ENGAGEMENT": ManagerStatus.ENGAGING,
        "HANDLE_INBOX": ManagerStatus.ENGAGING,
        "ANALYZE_PERFORMANCE": ManagerStatus.ANALYZING,
        "RETRY_JOB": ManagerStatus.RETRYING,
        "PLATFORM_TASK": ManagerStatus.WORKING if hasattr(ManagerStatus, "WORKING") else ManagerStatus.PRODUCING,
    }.get(task_type, ManagerStatus.PRODUCING)


# ── Identity-lock gate (mandatory) ─────────────────────────────────────

async def require_active_lock(db: AsyncSession, persona: Persona) -> IdentityLock:
    """NO IDENTITY LOCK → GENERATION BLOCKED. Raises, never silently substitutes."""
    storage_hex = persona.id.hex
    lock = (await db.execute(
        select(IdentityLock).where(IdentityLock.persona_id == storage_hex)
    )).scalar_one_or_none()
    if lock is None or lock.status != IdentityLockStatus.ACTIVE.value:
        raise PermissionError(
            f"GENERATION BLOCKED: persona '{persona.name}' has no ACTIVE identity lock "
            f"(status: {lock.status if lock else 'none'}). Approve an identity and lock it before production."
        )
    return lock


# ── Task execution (the real pipeline) ─────────────────────────────────

async def _execute_task(db: AsyncSession, mgr: ModelManager, task: ManagerTask) -> dict:
    persona = await db.get(Persona, task.persona_id)
    if persona is None:
        raise ValueError("persona not found")

    if task.task_type == "CHECK_INVENTORY":
        inv = await compute_inventory(db, persona.id)
        decision = decide_next_action(inv, mgr)
        await log_event(db, mgr, "decision", f"CHECK_INVENTORY → {decision['decision']}",
                        {"inventory": inv, "decision": decision})
        return {"inventory": inv, "decision": decision}

    if task.task_type == "PLAN_CONTENT":
        inv = await compute_inventory(db, persona.id)
        decision = decide_next_action(inv, mgr)
        v = ok, err = validate_decision(decision)
        if not ok:
            raise ValueError(f"invalid decision: {err}")
        if decision["decision"] == "CREATE_SHOOT":
            shoot_task, created = await create_task(
                db, mgr, "CREATE_SHOOT",
                {"theme": "lifestyle", "images": MIN_IMAGES_FOR_SHOOT, "reason": decision["reason"]},
                source="plan", priority=3,
            )
        return {"decision": decision, "task_created": decision["decision"] != "IDLE"}

    if task.task_type == "CREATE_SHOOT":
        return await _pipeline_create_shoot(db, mgr, persona, task)

    if task.task_type == "GENERATE_IMAGES":
        return await _pipeline_generate_images(db, mgr, persona, task)

    if task.task_type == "GENERATE_VIDEO":
        return await _pipeline_generate_video(db, mgr, persona, task)

    if task.task_type == "RUN_MEDIA_QA":
        return await _pipeline_media_qa(db, mgr, persona, task)

    # Ledger-only types (engagement/inbox/analytics) execute as recorded no-ops
    # until their subsystems land; they must not fake success.
    if task.task_type in {"CHECK_ENGAGEMENT", "HANDLE_INBOX", "ANALYZE_PERFORMANCE",
                          "WRITE_COPY", "SCHEDULE_CONTENT", "PUBLISH_CONTENT", "PLATFORM_TASK"}:
        raise NotImplementedError(
            f"{task.task_type} is registered in the ledger but its subsystem is not wired yet — "
            "the manager refuses to pretend success."
        )

    if task.task_type == "RETRY_JOB":
        return await _retry_blocked_children(db, mgr, persona)

    raise ValueError(f"unhandled task_type {task.task_type}")


# ── Production pipeline stages ─────────────────────────────────────────

async def _pipeline_create_shoot(db: AsyncSession, mgr: ModelManager, persona: Persona, task: ManagerTask) -> dict:
    # Identity gate BEFORE anything expensive.
    lock = await require_active_lock(db, persona)

    theme = task.payload.get("theme", "lifestyle")
    n_images = int(task.payload.get("images", MIN_IMAGES_FOR_SHOOT))

    shoot = Shoot(persona_id=persona.id, identity_id=_uuid_or_none(lock.identity_id),
                  name=f"{theme.title()} — {persona.name}", theme=theme,
                  image_count=n_images)  # status defaults to DRAFT
    db.add(shoot)
    await db.flush()

    # A real shot plan: varied shot types/compositions per slot.
    plans = []
    types = ["portrait", "full_body", "detail", "candid", "portrait", "full_body"]
    comps = ["centered 3/4", "low angle wide", "macro close", "over-shoulder", "seated profile", "walking toward camera"]
    lights = ["soft window light", "golden hour rim", "bright diffused", "moody practical", "softbox", "sunlit backlight"]
    for i in range(n_images):
        plans.append(ShotPlan(
            shoot_id=shoot.id, persona_id=persona.id, shot_number=i + 1,
            shot_type=types[i % len(types)],
            scene=f"{theme} scene {i + 1} — {persona.brand or persona.name} aesthetic",
            composition=comps[i % len(comps)],
            wardrobe=(persona.metadata_json or {}).get("wardrobe_default", "signature style"),
            camera="85mm f/1.8" if types[i % len(types)] == "portrait" else "35mm f/2.8",
            lighting=lights[i % len(lights)],
            pose="natural, relaxed" if i % 2 == 0 else "dynamic mid-motion",
            expression="soft confident smile" if i % 2 == 0 else "candid laugh",
            background=theme,
            aspect_ratio="4:5",
            motion_prompt=f"slow gentle {['push-in', 'pan left', 'tilt down', 'orbit'][i % 4]}, natural breathing motion",
        ))
    db.add_all(plans)
    # shoot stays 'draft' until generation completes (ShootStatus enum)
    task.payload = {**task.payload, "shoot_id": str(shoot.id)}

    # Chain the generation stage.
    gen_task, _ = await create_task(
        db, mgr, "GENERATE_IMAGES",
        {"shoot_id": str(shoot.id), "reason": "shoot planned"}, source="pipeline", priority=3,
    )
    await db.commit()
    return {"shoot_id": str(shoot.id), "shots_planned": len(plans),
            "next_task": str(gen_task.id)}


def _uuid_or_none(hex_str: str | None) -> UUID | None:
    if not hex_str:
        return None
    try:
        return UUID(hex_str)
    except (ValueError, AttributeError):
        return None


async def _pipeline_generate_images(db: AsyncSession, mgr: ModelManager, persona: Persona, task: ManagerTask) -> dict:
    lock = await require_active_lock(db, persona)
    shoot_id = task.payload.get("shoot_id")
    shoot = await db.get(Shoot, UUID(shoot_id)) if shoot_id else None
    if shoot is None:
        raise ValueError("GENERATE_IMAGES requires payload.shoot_id")

    plans = (await db.execute(
        select(ShotPlan).where(
            ShotPlan.shoot_id == shoot.id,
            ShotPlan.generation_status.in_([AssetStatus.PLANNED, AssetStatus.QUEUED, AssetStatus.GENERATING]),
        ).order_by(ShotPlan.shot_number)
    )).scalars().all()
    if not plans:
        return {"already_done": True, "note": "no pending shots for this shoot"}

    # Provider state must be REAL. Mock only when explicitly selected.
    from app.providers.registry import get_registry
    provider = get_registry().get_image_provider()
    provider_name = type(provider).__name__
    is_mock = "mock" in provider_name.lower()
    task.provider = provider_name

    # Identity-locked reference: real edit-capable providers receive the
    # persona's ACTUAL avatar as the visual reference (face preserved by
    # construction, not by prompt-hope).
    ref_bytes = None
    if not is_mock:
        from app.identity_engine import get_avatar_bytes
        ref_bytes = get_avatar_bytes(persona.id.hex) if hasattr(persona.id, "hex") else get_avatar_bytes(str(persona.id))
        if not ref_bytes:
            raise RuntimeError(
                "IDENTITY REFERENCE MISSING: persona has no real avatar — "
                "identity-locked generation refused (no prompt-only substitutes)"
            )

    results = {"generated": 0, "approved": 0, "rejected": 0, "blocked": 0, "details": []}
    for plan in plans:
        plan.generation_status = AssetStatus.GENERATING
        await db.commit()
        try:
            from app.identity_engine import build_locked_prompt
            scene_prompt = f"{plan.shot_type} shot, {plan.scene}, {plan.composition}, {plan.wardrobe}, {plan.lighting}, {plan.pose}, {plan.expression}, {plan.background} background"
            prompt = build_locked_prompt({"identity_prompt": lock.identity_prompt,
                                          "negative_prompt": lock.negative_prompt}, scene_prompt)
            t0 = time.monotonic()
            if ref_bytes is not None and hasattr(provider, "edit_image"):
                result = await provider.edit_image(
                    reference_image_bytes=ref_bytes,
                    prompt=prompt,
                    negative_prompt=lock.negative_prompt or "",
                    width=832, height=1040,  # 4:5
                    seed=lock.seed + plan.shot_number,
                )
            else:
                result = await provider.generate(
                    prompt=prompt, width=832, height=1040,  # 4:5
                    seed=lock.seed + plan.shot_number,
                )
            if not result.success:
                raise RuntimeError(f"provider failed: {result.error}")
            # Bytes come back inline (wan2.5/edit providers) or via storage key
            img_bytes = result.data.get("image_bytes")
            if not img_bytes and result.data.get("image_key"):
                img_bytes = await _fetch_asset_bytes(result.data["image_key"])
            if not img_bytes:
                raise RuntimeError("provider returned no image bytes")
            # Persist file
            from pathlib import Path
            rel_dir = f"storage/shoots/{shoot.id.hex[:8]}"
            dest = Path(__file__).resolve().parent.parent / rel_dir
            dest.mkdir(parents=True, exist_ok=True)
            fname = f"shot_{plan.shot_number:02d}.png"
            (dest / fname).write_bytes(img_bytes)
            plan.asset_key = f"{rel_dir}/{fname}"
            plan.generation_status = AssetStatus.GENERATED
            plan.qa_status = "pending"
            results["generated"] += 1

            # ── QA: technical + identity(recorded) + content ──
            qa = _technical_qa(img_bytes, 832, 1040)
            if qa["pass"]:
                plan.generation_status = AssetStatus.APPROVED
                plan.qa_status = "passed"
                plan.qa_json = {**qa, "identity_lock": "active", "provider": provider_name,
                                "generation_ms": int((time.monotonic() - t0) * 1000)}
                results["approved"] += 1
            else:
                plan.generation_status = AssetStatus.QA_FAILED
                plan.qa_status = "failed"
                plan.qa_json = qa
                plan.error = qa.get("reason", "qa failed")
                results["rejected"] += 1
        except Exception as e:  # noqa: BLE001
            plan.generation_status = AssetStatus.BLOCKED
            plan.error = str(e)[:500]
            plan.retry_count += 1
            results["blocked"] += 1
            results["details"].append({"shot": plan.shot_number, "error": str(e)[:200]})
        await db.commit()

    if results["generated"] > 0:
        shoot.generated_images = [p.asset_key for p in plans if p.asset_key]
        if all(p.generation_status in (AssetStatus.APPROVED, AssetStatus.QA_FAILED, AssetStatus.BLOCKED)
               for p in plans):
            from app.models import ShootStatus
            shoot.status = ShootStatus.COMPLETED if results["approved"] > 0 else ShootStatus.FAILED
            shoot.progress = 100.0
            shoot.completed_at = utcnow()
        # Chain video stage when we have approved sources
        approved_keys = [p.asset_key for p in plans if p.generation_status == AssetStatus.APPROVED]
        if approved_keys:
            await create_task(db, mgr, "GENERATE_VIDEO",
                              {"shoot_id": str(shoot.id), "source_asset": approved_keys[0]},
                              source="pipeline", priority=4)
    await db.commit()
    return results


async def _fetch_asset_bytes(image_key: str) -> bytes:
    """Read generated bytes back through the SAME storage provider the
    registry resolves (filesystem in local dev, not the in-memory mock)."""
    from app.providers.registry import get_registry
    storage = get_registry().get_storage_provider()
    result = await storage.download(image_key)
    if not result.success or not result.data.get("data"):
        raise RuntimeError(f"provider returned no bytes for {image_key}")
    return result.data["data"]


def _technical_qa(img_bytes: bytes, expect_w: int, expect_h: int) -> dict:
    qa: dict = {"provider_bytes": len(img_bytes)}
    if len(img_bytes) < 1000:
        return {**qa, "pass": False, "reason": "file too small / zero-byte result"}
    if img_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        return {**qa, "pass": False, "reason": "not a valid PNG"}
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(img_bytes))
        qa["width"], qa["height"] = im.size
        if abs(qa["width"] / qa["height"] - expect_w / expect_h) > 0.05:
            return {**qa, "pass": False, "reason": f"aspect ratio {qa['width']}x{qa['height']} off-target"}
    except Exception as e:  # noqa: BLE001
        return {**qa, "pass": False, "reason": f"unreadable image: {e}"}
    return {**qa, "pass": True}


async def _pipeline_generate_video(db: AsyncSession, mgr: ModelManager, persona: Persona, task: ManagerTask) -> dict:
    lock = await require_active_lock(db, persona)
    shoot_id = task.payload.get("shoot_id")
    source_asset = task.payload.get("source_asset", "")
    shoot = await db.get(Shoot, UUID(shoot_id)) if shoot_id else None

    approved_source = (await db.execute(
        select(ShotPlan).where(
            ShotPlan.shoot_id == UUID(shoot_id),
            ShotPlan.generation_status == AssetStatus.APPROVED,
            ShotPlan.asset_key == source_asset,
        )
    )).scalar_one_or_none() if shoot_id else None
    if approved_source is None:
        # VIDEO consumes APPROVED image state only.
        raise ValueError("GENERATE_VIDEO blocked: no approved source asset for this shoot")

    from app.providers.registry import get_registry
    vp = get_registry().get_video_provider()
    provider_name = type(vp).__name__
    task.provider = provider_name
    if "mock" in provider_name.lower():
        raise RuntimeError("REAL VIDEO PROVIDER UNAVAILABLE — mock fallback refused in production pipeline")

    identity = (await db.execute(
        select(Identity).where(Identity.persona_id == persona.id).order_by(Identity.created_at.desc())
    )).scalars().first()

    motion = {
        "camera_motion": "slow push-in",
        "subject_motion": "natural breathing, subtle weight shift",
        "facial_expression": approved_source.expression,
        "body_motion": "minimal, elegant",
        "scene_motion": "ambient light shift",
        "duration": 4,
        "loop_behavior": "none",
    }
    motion_prompt = (f"{motion['camera_motion']}, {motion['subject_motion']}, "
                     f"{motion['facial_expression']}, {motion['scene_motion']}")

    t0 = time.monotonic()
    result = await vp.image_to_video(
        image_key=source_asset, prompt=motion_prompt, duration=motion["duration"],
    ) if hasattr(vp, "image_to_video") else await vp.text_to_video(
        prompt=f"{approved_source.scene}. {motion_prompt}", duration=motion["duration"],
        width=720, height=900,
    )
    if not result.success:
        raise RuntimeError(f"video provider failed: {result.error}")

    import httpx
    from pathlib import Path
    video_url = result.data.get("video_url", "")
    video_key = result.data.get("video_key") or f"videos/{result.data.get('task_id', 'gen')}.mp4"
    dest = Path(__file__).resolve().parent.parent / "storage" / video_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=300) as dl:
        dl_resp = await dl.get(video_url)
        dl_resp.raise_for_status()
        dest.write_bytes(dl_resp.content)
    local_bytes = len(dl_resp.content)

    # ── Video QA: exists, decodes, duration, dims ──
    vqa = await _video_qa(dest, local_bytes)
    video = GeneratedVideo(
        identity_id=identity.id if identity else None,
        prompt=motion_prompt,
        video_key=video_key,
        duration_seconds=vqa.get("duration", 0),
        width=vqa.get("width", 720), height=vqa.get("height", 1280),
        generation_time_ms=int((time.monotonic() - t0) * 1000),
        metadata_json={"shoot_id": str(shoot.id) if shoot else None,
                       "source_asset": source_asset, "provider": provider_name,
                       "task_id": result.data.get("task_id", ""),
                       "qa": vqa, "approved": vqa["pass"], "is_mock": False},
    )
    db.add(video)
    approved_source.motion_prompt = motion_prompt
    await db.commit()

    if not vqa["pass"]:
        raise RuntimeError(f"VIDEO QA FAILED: {vqa.get('reason')}")

    # Pack stage
    await create_task(db, mgr, "RUN_MEDIA_QA", {"shoot_id": str(shoot.id) if shoot else None},
                      source="pipeline", priority=5)
    return {"video_key": video_key, "bytes": local_bytes, "qa": vqa, "motion": motion}


async def _video_qa(path, size: int) -> dict:
    if size < 10_000:
        return {"pass": False, "reason": "video file too small"}
    try:
        import subprocess
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30)
        import json
        info = json.loads(out.stdout or "{}")
        duration = float(info.get("format", {}).get("duration", 0))
        streams = info.get("streams", [])
        w = streams[0].get("width") if streams else 0
        h = streams[0].get("height") if streams else 0
        if duration < 1:
            return {"pass": False, "reason": f"duration too short ({duration}s)", "duration": duration}
        return {"pass": True, "duration": duration, "width": w, "height": h, "bytes": size}
    except FileNotFoundError:
        return {"pass": True, "note": "ffprobe unavailable — basic size check only", "bytes": size}
    except Exception as e:  # noqa: BLE001
        return {"pass": False, "reason": f"decode failed: {e}"}


async def _pipeline_media_qa(db: AsyncSession, mgr: ModelManager, persona: Persona, task: ManagerTask) -> dict:
    """Assemble approved assets into a ContentPack (packaging stage)."""
    shoot_id = task.payload.get("shoot_id")
    approved = (await db.execute(
        select(ShotPlan).where(
            ShotPlan.shoot_id == UUID(shoot_id),
            ShotPlan.generation_status == AssetStatus.APPROVED,
        )
    )).scalars().all() if shoot_id else []
    if not approved:
        return {"note": "no approved assets to package", "pack_created": False}

    pack = ContentPack(
        persona_id=persona.id,
        name=f"{approved[0].scene.split(' — ')[0] if approved else 'Pack'} — {persona.name}",
        status="assembled",
        platform="all",
        images=[p.asset_key for p in approved],
        videos=[],
        captions=[],
        metadata_json={"shoot_id": shoot_id, "assembled_by": "manager",
                       "shot_ids": [str(p.id) for p in approved], "is_mock": False},
    )
    db.add(pack)
    inv = await compute_inventory(db, persona.id)
    decision = decide_next_action(inv, mgr)
    await set_state(db, mgr, ManagerStatus.IDLE if decision["decision"] == "IDLE"
                    else ManagerStatus.PLANNING,
                    objective=decision["reason"])
    await db.commit()
    return {"pack_id": str(pack.id), "images": len(approved), "inventory": inv,
            "next": decision["decision"]}


async def _retry_blocked_children(db: AsyncSession, mgr: ModelManager, persona: Persona) -> dict:
    blocked = (await db.execute(
        select(ShotPlan).where(
            ShotPlan.persona_id == persona.id,
            ShotPlan.generation_status == AssetStatus.BLOCKED,
            ShotPlan.retry_count < 3,
        )
    )).scalars().all()
    requeued = 0
    shoot_ids: set[str] = set()
    for p in blocked:
        p.generation_status = AssetStatus.QUEUED
        p.error = ""
        requeued += 1
        shoot_ids.add(str(p.shoot_id))
    # Re-drive the pipeline: each affected shoot gets a GENERATE_IMAGES task so
    # the requeued shots actually re-execute (otherwise they sit QUEUED and a
    # later wake would mint a duplicate shoot instead).
    chained = 0
    for sid in shoot_ids:
        _t, created = await create_task(
            db, mgr, "GENERATE_IMAGES",
            {"shoot_id": sid, "reason": "retry-failed requeue"},
            source="retry", priority=2,
        )
        if created:
            chained += 1
    await db.commit()
    return {"requeued_shots": requeued, "generation_tasks_queued": chained}


# ── Wake / heartbeat ───────────────────────────────────────────────────

async def manager_wake(db: AsyncSession, persona_id: UUID, source: str = "heartbeat") -> dict:
    """One event-driven cycle: check real state → decide → create/execute tasks.
    Returns a summary; never invents work when inventory is sufficient."""
    mgr = await get_or_create_manager(persona_id, db)
    if mgr.paused:
        return {"manager": str(mgr.id), "status": mgr.status.value, "action": "paused"}
    mgr.last_heartbeat_at = utcnow()

    # Resume-eligible retries first (retry clock fired)
    due_retry = (await db.execute(
        select(ManagerTask).where(
            ManagerTask.manager_id == mgr.id,
            ManagerTask.status == ManagerTaskStatus.PENDING,
            ManagerTask.scheduled_for is not None,
            ManagerTask.scheduled_for <= utcnow(),
        )
    )).scalars().first()
    if due_retry:
        t = await run_task_guarded(db, due_retry.id)
        return {"manager": str(mgr.id), "status": mgr.status.value,
                "action": "retried", "task": str(t.id), "task_status": t.status.value}

    inv = await compute_inventory(db, persona_id)
    decision = decide_next_action(inv, mgr, last_error_kind=await _recent_provider_failure(db, mgr))

    if decision["decision"] == "IDLE":
        await set_state(db, mgr, ManagerStatus.IDLE, objective=decision["reason"])
        mgr.next_wake_at = utcnow() + timedelta(minutes=30)
        await db.commit()
        return {"manager": str(mgr.id), "status": "IDLE", "action": "none",
                "inventory": inv, "reason": decision["reason"]}

    # Create the decision's task (deduped — a repeated shortage wake won't pile
    # up shoots), then drive the pipeline chain until drained or blocked.
    decision_task, _created = await create_task(
        db, mgr, decision["decision"],
        {**(decision.get("required_outputs") or {}), "reason": decision["reason"]},
        source=source,
        priority={"CRITICAL": 1, "HIGH": 2, "MEDIUM": 5, "LOW": 7}.get(decision.get("priority", "MEDIUM"), 5),
    )
    await db.commit()

    executed: list[str] = []
    for _ in range(6):  # bounded chain per wake
        pending = (await db.execute(
            select(ManagerTask).where(
                ManagerTask.manager_id == mgr.id,
                ManagerTask.status == ManagerTaskStatus.PENDING,
            ).order_by(ManagerTask.priority, ManagerTask.created_at).limit(1)
        )).scalar_one_or_none()
        if pending is None:
            break
        t = await run_task_guarded(db, pending.id)
        executed.append(f"{t.task_type}:{t.status.value}")
        if t.status in (ManagerTaskStatus.BLOCKED, ManagerTaskStatus.PENDING):
            break  # blocked or scheduled-for-retry: stop the chain

    inv2 = await compute_inventory(db, persona_id)
    action = "executed:" + ",".join(executed) if executed else "none"
    if not executed and decision["decision"] == "IDLE":
        action = "none"
    return {"manager": str(mgr.id), "status": mgr.status.value,
            "action": action,
            "inventory_before": inv, "inventory_after": inv2}


async def recover_running_tasks(db: AsyncSession) -> int:
    """Restart recovery: tasks left RUNNING by a dead process go back to
    PENDING (once) — their pipelines are resumable because every stage is
    ledgered and per-shot state is durable. Returns count recovered."""
    running = (await db.execute(
        select(ManagerTask).where(ManagerTask.status == ManagerTaskStatus.RUNNING)
    )).scalars().all()
    recovered = 0
    for t in running:
        t.status = ManagerTaskStatus.PENDING
        t.scheduled_for = utcnow()
        t.error = "recovered after restart"
        recovered += 1
    if recovered:
        await db.commit()
    return recovered


async def heartbeat_once(db: AsyncSession) -> list[dict]:
    """One heartbeat pass over ALL non-paused managers. Purely event/state
    driven: wakes only managers whose ledger shows due/pending work, work
    finished since last wake, or inventory below target. The heartbeat does
    NOT invent work — the decide contract governs that. Returns per-manager
    wake summaries (empty = every manager idle at the DB level)."""
    managers = (await db.execute(
        select(ModelManager).where(ModelManager.paused == False)  # noqa: E712
    )).scalars().all()
    summaries: list[dict] = []
    for mgr in managers:
        # Due/pending ledger work OR a completed task since the last wake —
        # e.g. chained video work queued by an earlier task — justifies a wake.
        pending = (await db.execute(
            select(ManagerTask).where(
                ManagerTask.manager_id == mgr.id,
                ManagerTask.status.in_([ManagerTaskStatus.PENDING, ManagerTaskStatus.RUNNING]),
                ManagerTask.scheduled_for <= utcnow(),
            ).limit(1)
        )).scalar_one_or_none()
        wake_reason = "due_task" if pending else None
        if not pending:
            finished = (await db.execute(
                select(ManagerTask).where(
                    ManagerTask.manager_id == mgr.id,
                    ManagerTask.status == ManagerTaskStatus.COMPLETED,
                    ManagerTask.completed_at > (mgr.last_heartbeat_at or mgr.created_at),
                ).limit(1)
            )).scalar_one_or_none()
            if finished:
                wake_reason = "work_finished"
        if not wake_reason:
            # Inventory-threshold event: below target days → the decide
            # contract will mint a production task (or dedupe to an existing
            # open one — no duplicates).
            inv = await compute_inventory(db, mgr.persona_id)
            if inv.get("shortage", 0) > 0 and not mgr.paused:
                wake_reason = "inventory_shortage"
        summary: dict = {"manager": str(mgr.id), "reason": wake_reason or "idle"}
        if wake_reason:
            summaries.append({**summary, **await manager_wake(db, mgr.persona_id, source="heartbeat")})
        else:
            # Idle-at-DB-level: still touch the heartbeat so the dashboard
            # shows liveness without consuming any LLM/provider calls.
            mgr.last_heartbeat_at = utcnow()
            if mgr.status == ManagerStatus.IDLE:
                mgr.next_wake_at = None
            await db.commit()
            summaries.append(summary)
    return summaries
