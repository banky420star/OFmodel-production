"""
Persona Studio — regression test for the identity-lock creation bug.

This is the persistent automated test for the exact defect where a newly created
persona completes the apparent build workflow but has no identity lock, causing
Auto-Produce to fail with "No identity lock for persona".

It exercises:
    create persona
    -> build workflow (auto-selects, approves, validates, activates identity)
    -> identity lock exists and is ACTIVE
    -> re-opened sessions (restart-like semantics) still see the same lock
    -> Auto-Produce is BLOCKED while the identity lock is not active
    -> create shoot + run production on the mock provider
    -> job ends honest: completed (labelled is_mock) or failed with a reason
"""
import asyncio
import uuid

import pytest


async def _wait_for_build(client, persona_id_hex: str, max_seconds: float = 30) -> str:
    """Wait until the persona leaves BUILDING. Returns its final status."""
    status = "building"
    for _ in range(int(max_seconds / 0.5)):
        await asyncio.sleep(0.5)
        pr = await client.get(f"/api/v1/personas/{persona_id_hex}")
        if pr.status_code == 200:
            status = pr.json().get("status", "building")
            if status != "building":
                break
    return status


@pytest.mark.asyncio
async def test_new_persona_gets_durable_identity_lock(client):
    """A newly created persona must end the build with a usable identity lock."""
    unique_name = f"Regen_{uuid.uuid4().hex[:8]}"

    # ── 1. Create persona ────────────────────────────────────────────────
    create_resp = await client.post("/api/v1/personas", json={
        "name": unique_name,
        "age": 24,
        "description": "Regression-test persona",
        "adult_verified": True,
        "synthetic_identity": True,
        "appearance": {
            "hair": "long blonde",
            "hair_length": "long",
            "hair_colour": "blonde",
            "eye_colour": "blue",
            "facial_characteristics": "high cheekbones",
            "skin_characteristics": "fair",
            "body_characteristics": "athletic",
            "height_profile": "5'7\"",
            "identifying_synthetic_features": "distinctive smile",
            "style_preferences": ["luxury", "lifestyle"],
        },
        "personality": ["confident", "playful"],
        "brand": "luxury lifestyle",
        "voice_style": "South African English",
        "publishing_frequency": "5 packs/week",
    })
    assert create_resp.status_code in (200, 201), f"create persona failed: {create_resp.text}"
    persona = create_resp.json()
    persona_id = uuid.UUID(persona["id"])
    assert persona["status"] in ("building", "active")

    # ── 2. Auto-Produce must be BLOCKED before the build activates the lock ──
    # Fired immediately after creation, before the background build can reach
    # its activation step: no usable lock exists yet, so production is refused
    # with an actionable message instead of silently starting.
    guard = await client.post(
        f"/api/v1/personas/{persona_id.hex}/auto-produce",
        params={"shoot_count": 1, "images_per_shoot": 1, "generate_videos": False},
    )
    assert guard.status_code == 409, (
        f"auto-produce must be blocked before identity activation: {guard.status_code} {guard.text}"
    )
    assert "approve" in guard.json()["detail"].lower()

    # ── 3. Wait for the build workflow to complete ───────────────────────
    final_status = await _wait_for_build(client, persona_id.hex)
    assert final_status == "active", f"build should complete as active, got {final_status}"

    # ── 4. Identities were generated and the canonical one is usable ─────
    ir = await client.get(f"/api/v1/personas/{persona_id.hex}/identities")
    assert ir.status_code == 200
    identities = ir.json()
    assert len(identities) >= 1, f"No identities generated for new persona (status={final_status})"
    assert any(i["status"] in ("approved", "ready") for i in identities), (
        f"no approved/ready identity: {[i['status'] for i in identities]}"
    )

    # ── 4b. The build's reference-dataset step actually generated images ──
    # This is the assertion that pins the original defect: the lock was
    # created uncommitted inside the step, the sync reader never saw it, and
    # every reference image failed with "No identity lock for persona".
    wr = await client.get(f"/api/v1/workflows?persona_id={persona_id.hex}")
    assert wr.status_code == 200
    workflows = wr.json()
    build_workflows = [w for w in workflows if w.get("workflow_type") == "persona_creation"]
    assert build_workflows, f"no persona_creation workflow found: {workflows}"
    steps_resp = await client.get(f"/api/v1/workflows/{build_workflows[0]['id']}/steps")
    assert steps_resp.status_code == 200
    steps = steps_resp.json()
    ds_steps = [s for s in steps if s["step_type"] == "build_reference_dataset"]
    assert ds_steps, f"build_reference_dataset step missing: {[s['step_type'] for s in steps]}"
    ds_out = ds_steps[0].get("output_data") or {}
    assert "No identity lock for persona" not in str(ds_out.get("errors", "")), (
        f"reference generation hit the identity-lock visibility bug: {ds_out}"
    )
    assert ds_out.get("total_images", 0) >= 1, (
        f"build must generate reference images from the lock: {ds_out}"
    )

    # ── 5. Identity lock exists, ACTIVE, and references the persona ──────
    lr = await client.get(f"/api/v1/personas/{persona_id.hex}/identity-lock")
    assert lr.status_code == 200, f"identity lock missing: {lr.text}"
    lock = lr.json()
    assert lock["status"] == "active", f"lock should be active after build: {lock['status']}"
    assert lock["seed"] != 0
    assert lock["identity_prompt"]
    assert lock["negative_prompt"]
    assert lock["style_tags"]
    assert lock["persona_id"] == str(persona_id)   # route returns dashed UUID
    assert lock["storage_hex"] == persona_id.hex   # storage key is dashless hex
    assert lock["identity_id"], "lock must reference the canonical identity"

    # ── 6. Re-opened session (restart semantics): same durable lock ──────
    lr2 = await client.get(f"/api/v1/personas/{persona_id.hex}/identity-lock")
    lock2 = lr2.json()
    assert lock2["seed"] == lock["seed"], "seed must not change between sessions"
    assert lock2["identity_prompt"] == lock["identity_prompt"]
    assert lock2["status"] == "active"

    # ── 7. Create shoot ───────────────────────────────────────────────────
    sr = await client.post(f"/api/v1/personas/{persona_id.hex}/shoots", json={
        "name": "Regression shoot",
        "theme": "lifestyle",
        "image_count": 3,
    })
    assert sr.status_code in (200, 201), f"create shoot failed: {sr.text}"

    # ── 8. Start production (mock provider path) ──────────────────────────
    ar_prod = await client.post(
        f"/api/v1/personas/{persona_id.hex}/auto-produce",
        params={"shoot_count": 1, "images_per_shoot": 2, "generate_videos": False},
    )
    assert ar_prod.status_code in (200, 201), f"auto-produce failed: {ar_prod.text}"
    job_id = ar_prod.json()["job_id"]

    for _ in range(80):
        await asyncio.sleep(0.3)
        jr = await client.get(f"/api/v1/jobs/{job_id}")
        if jr.status_code == 200 and jr.json()["status"] in ("completed", "failed"):
            break

    # ── 9. Final state is honest ──────────────────────────────────────────
    jr_final = await client.get(f"/api/v1/jobs/{job_id}")
    assert jr_final.status_code == 200
    final_job = jr_final.json()
    assert final_job["status"] in ("completed", "failed")
    assert final_job["message"], "job must end with a message"
    if final_job["status"] == "failed":
        assert final_job["message"], "failed job must say why"

    if final_job["status"] == "completed":
        meta = final_job["metadata_json"]
        assert meta.get("is_mock") is True, "mock run must be labelled is_mock=true"
        assert meta.get("image_provider"), "job must record which provider executed"
