"""Model Manager + media pipeline tests.

Covers the required acceptance cases: manager lifecycle, task ledger,
inventory-driven production, identity-lock gate, generation→QA→approval,
restart recovery, duplicate protection, idle behavior.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.manager_core import (
    compute_inventory,
    create_task,
    decide_next_action,
    get_or_create_manager,
    manager_wake,
    recover_running_tasks,
    require_active_lock,
    run_task_guarded,
    validate_decision,
)
from app.models import (
    AssetStatus,
    Identity,
    IdentityLock,
    IdentityLockStatus,
    ManagerStatus,
    ManagerTask,
    ManagerTaskStatus,
    ModelManager,
    Persona,
    PersonaStatus,
    ShotPlan,
)


async def _make_persona(db, name="MgrTest", status=PersonaStatus.ACTIVE) -> Persona:
    existing = (await db.execute(
        select(Persona).where(Persona.name == name))).scalar_one_or_none()
    if existing:
        return existing
    p = Persona(name=name, age=24, status=status, adult_verified=True,
                synthetic_identity=True)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _activate_lock(persona: Persona, db) -> IdentityLock:
    lock = IdentityLock(
        persona_id=persona.id.hex, seed=12345,
        identity_prompt="test persona, photorealistic",
        negative_prompt="deformed", status=IdentityLockStatus.ACTIVE.value,
    )
    db.add(lock)
    await db.commit()
    return lock


# ── Manager lifecycle ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_manager_created_for_persona(db):
    persona = await _make_persona(db, name="MgrCreate")
    if True:
        mgr = await get_or_create_manager(persona.id, db)
        await db.commit()
        assert mgr.persona_id == persona.id
        assert mgr.status == ManagerStatus.INITIALIZING
        # exactly one manager per persona
        again = await get_or_create_manager(persona.id, db)
        assert again.id == mgr.id


@pytest.mark.asyncio
async def test_manager_state_persists(db):
    persona = await _make_persona(db, name="MgrPersist")
    mgr = await get_or_create_manager(persona.id, db)
    mgr.status = ManagerStatus.PRODUCING
    mgr.current_objective = "test objective"
    await db.commit()
    mid = mgr.id
    # expire + re-fetch = simulate fresh read after restart
    db.expire_all()
    mgr2 = await db.get(ModelManager, mid)
    assert mgr2.status == ManagerStatus.PRODUCING
    assert mgr2.current_objective == "test objective"


@pytest.mark.asyncio
async def test_idle_when_inventory_sufficient(db):
    persona = await _make_persona(db, name="MgrIdle")
    if True:
        await _activate_lock(persona, db)
        # 20 approved shots = 10 days > target 7
        for i in range(20):
            db.add(ShotPlan(shoot_id=uuid4(), persona_id=persona.id, shot_number=i + 1,
                            generation_status=AssetStatus.APPROVED, asset_key=f"storage/x/{i}.png"))
        await db.commit()
        result = await manager_wake(db, persona.id)
        assert result["status"] == "IDLE"
        assert result["action"] == "none"
        # and a second wake does not create any tasks (no duplicates)
        n_tasks = len((await db.execute(
            __import__("sqlalchemy").select(ManagerTask))).scalars().all())
        await manager_wake(db, persona.id)
        n_tasks_after = len((await db.execute(
            __import__("sqlalchemy").select(ManagerTask))).scalars().all())
        assert n_tasks == n_tasks_after == 0


@pytest.mark.asyncio
async def test_inventory_shortage_creates_production_task(db):
    persona = await _make_persona(db, name="MgrShortage")
    if True:
        await _activate_lock(persona, db)
        result = await manager_wake(db, persona.id)
        # one wake chains the whole production graph synchronously:
        # CREATE_SHOOT → GENERATE_IMAGES → GENERATE_VIDEO (video may stay
        # pending if no real provider is configured — pipeline correctness,
        # not completion, is what this test pins)
        assert result["action"].startswith("executed:CREATE_SHOOT:COMPLETED,GENERATE_IMAGES:COMPLETED")
        assert "GENERATE_VIDEO" in result["action"]
        # ledger has the task(s)
        tasks = (await db.execute(
            __import__("sqlalchemy").select(ManagerTask))).scalars().all()
        types = {t.task_type for t in tasks}
        assert {"CREATE_SHOOT", "GENERATE_IMAGES"} <= types


# ── Decision contract ─────────────────────────────────────────────────

def test_decision_validation_rejects_prose():
    ok, err, _ = validate_decision({"decision": "do some content maybe"})
    assert not ok and "not in" in err
    ok, err, _ = validate_decision({"decision": "CREATE_SHOOT"})  # missing reason
    assert not ok and "reason" in err
    ok, err, _ = validate_decision({"decision": "IDLE", "reason": "all good"})
    assert ok


# ── Identity gate ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_identity_lock_blocks_generation(db):
    persona = await _make_persona(db, name="MgrNoLock")
    if True:
        p = await db.get(Persona, persona.id)
        with pytest.raises(PermissionError, match="GENERATION BLOCKED"):
            await require_active_lock(db, p)


@pytest.mark.asyncio
async def test_waiting_lock_blocks_generation(db):
    persona = await _make_persona(db, name="MgrWaitLock")
    if True:
        p = await db.get(Persona, persona.id)
        db.add(IdentityLock(persona_id=p.id.hex, seed=1, identity_prompt="x",
                            status=IdentityLockStatus.AWAITING_IDENTITY_APPROVAL.value))
        await db.commit()
        with pytest.raises(PermissionError, match="no ACTIVE identity lock"):
            await require_active_lock(db, p)


# ── Task ledger + pipeline (mock provider) ────────────────────────────

@pytest.mark.asyncio
async def test_full_pipeline_mock_provider(db):
    persona = await _make_persona(db, name="MgrPipeline")
    if True:
        await _activate_lock(persona, db)

        # Wake 1: shortage → CREATE_SHOOT → chains GENERATE_IMAGES (+VIDEO) in one wake
        r1 = await manager_wake(db, persona.id)
        assert r1["action"].startswith("executed:CREATE_SHOOT:COMPLETED,GENERATE_IMAGES:COMPLETED")
        assert "GENERATE_VIDEO" in r1["action"]
        # The pipeline may have run GENERATE_IMAGES + VIDEO + QA synchronously;
        # verify ledger and shot states are consistent either way.
        tasks = (await db.execute(
            __import__("sqlalchemy").select(ManagerTask).order_by(ManagerTask.created_at))).scalars().all()
        types = [t.task_type for t in tasks]
        assert "GENERATE_IMAGES" in types

        shots = (await db.execute(
            __import__("sqlalchemy").select(ShotPlan).where(ShotPlan.persona_id == persona.id))).scalars().all()
        assert len(shots) >= 6, "a full shoot plan was created"
        # every shot has a real production spec (not 6 identical prompts)
        specs = {(s.shot_type, s.composition, s.lighting) for s in shots}
        assert len(specs) >= 3, "shot plans vary"
        # every shot reached a terminal state
        terminal = {AssetStatus.APPROVED, AssetStatus.QA_FAILED, AssetStatus.BLOCKED}
        assert all(s.generation_status in terminal for s in shots)
        # approved shots carry QA + provenance
        approved = [s for s in shots if s.generation_status == AssetStatus.APPROVED]
        for s in approved:
            assert s.asset_key.startswith("storage/shoots/")
            assert s.qa_status == "passed"
            assert s.qa_json.get("pass") is True


@pytest.mark.asyncio
async def test_duplicate_task_execution_safe(db):
    persona = await _make_persona(db, name="MgrDup")
    if True:
        await _activate_lock(persona, db)
        mgr = await get_or_create_manager(persona.id, db)
        t1, created1 = await create_task(db, mgr, "CHECK_INVENTORY", {}, dedupe=True)
        t2, created2 = await create_task(db, mgr, "CHECK_INVENTORY", {}, dedupe=True)
        assert created1 and not created2 and t1.id == t2.id
        await db.commit()
        r1 = await run_task_guarded(db, t1.id)
        assert r1.status == ManagerTaskStatus.COMPLETED
        # re-running a COMPLETED task returns the stored result (no dup exec)
        before = r1.result
        r2 = await run_task_guarded(db, t1.id)
        assert r2.status == ManagerTaskStatus.COMPLETED and r2.result == before


@pytest.mark.asyncio
async def test_failed_task_retries_bounded_then_blocks(db):
    persona = await _make_persona(db, name="MgrRetry")
    if True:
        await _activate_lock(persona, db)
        mgr = await get_or_create_manager(persona.id, db)
        # GENERATE_IMAGES without shoot_id raises ValueError → INVALID_REQUEST path
        t, _ = await create_task(db, mgr, "GENERATE_IMAGES", {}, dedupe=False)
        await db.commit()
        for _ in range(5):
            t = await run_task_guarded(db, t.id)
            if t.status == ManagerTaskStatus.BLOCKED:
                break
        assert t.status == ManagerTaskStatus.BLOCKED
        assert "shoot_id" in t.error


# ── Restart recovery ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_restart_recovery_requeues_running_tasks(db):
    persona = await _make_persona(db, name="MgrRestart")
    await _activate_lock(persona, db)
    mgr = await get_or_create_manager(persona.id, db)
    t = ManagerTask(persona_id=persona.id, manager_id=mgr.id,
                    task_type="CHECK_INVENTORY", status=ManagerTaskStatus.RUNNING)
    db.add(t)
    await db.commit()
    tid = t.id
    db.expire_all()
    n = await recover_running_tasks(db)
    assert n >= 1
    t2 = await db.get(ManagerTask, tid)
    assert t2.status == ManagerTaskStatus.PENDING
    assert t2.scheduled_for is not None  # due immediately


# ── Inventory math ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_inventory_math(db):
    persona = await _make_persona(db, name="MgrInv")
    if True:
        for i in range(10):
            db.add(ShotPlan(shoot_id=uuid4(), persona_id=persona.id, shot_number=1,
                            generation_status=AssetStatus.APPROVED, asset_key=f"k{i}"))
        db.add(ShotPlan(shoot_id=uuid4(), persona_id=persona.id, shot_number=2,
                        generation_status=AssetStatus.QA_FAILED, asset_key="bad"))
        await db.commit()
        inv = await compute_inventory(db, persona.id)
        assert inv["approved_images"] == 10
        assert inv["days_available"] == 5.0
        assert inv["shortage"] == 2  # 7 - 5
