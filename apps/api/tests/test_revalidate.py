"""POST /personas/{id}/revalidate — re-judging a model that already exists.

Identity QA used to run only as the last step of a full build. That made the
verdict expensive to obtain a second time: the only way back was `rebuild`,
which re-generates the reference set, re-trains the LoRA and spends the whole
~2 hours again. So an identity whose evaluator happened to be unreachable at
that one moment — the local model not running, most often — sat on REVIEW with
no cheap route off it.

The endpoint re-judges from stored facts through the *same* `evaluate_identity`
the build step uses. Two properties are therefore the whole point, and both are
tested here:

  - the facts it rebuilds must be the stored ones, or a re-check would be
    judging different evidence than the verdict it is re-checking and the two
    scores on the record would stop being comparable; and
  - it must pick the same identity the UI shows, or a persona could display one
    identity's score while a re-check promoted a different one.

The failure policy is the build's, unchanged: a verdict promotes or demotes,
and an evaluator that cannot answer records REVIEW and changes nothing.
"""
import json
import uuid

import pytest
from sqlalchemy import select

from app.models import (
    Identity, IdentityLock, IdentityStatus, Persona, QAResult, QAStatus,
    ReferenceDataset, TrainingJob, WorkflowStatus, persona_storage_hex,
)

from tests.test_identity_qa import _Evaluator


async def _seed(db, *, status=IdentityStatus.TRAINING, score=0.0,
                total_images=9, job_status=WorkflowStatus.COMPLETED,
                metrics=None, lora="naomi_lora.safetensors"):
    """A persona with the rows a finished build leaves behind.

    Committed, not flushed: the endpoint runs on its own session (the request's
    `get_db` dependency), so rows that are only flushed are invisible to it.
    The unique name keeps one test's persona from colliding with another's.
    """
    persona = Persona(
        id=uuid.uuid4(), name=f"Revalidate_{uuid.uuid4().hex[:8]}", age=27,
        appearance={"hair": "auburn"}, personality=["wry"],
    )
    identity = Identity(
        id=uuid.uuid4(), persona_id=persona.id, name=persona.name,
        status=status, consistency_score=score, lora_model_path=lora,
    )
    db.add_all([persona, identity])
    await db.flush()

    if total_images is not None:
        db.add(ReferenceDataset(
            id=uuid.uuid4(), identity_id=identity.id, total_images=total_images,
        ))
    if job_status is not None:
        db.add(TrainingJob(
            id=uuid.uuid4(), identity_id=identity.id, status=job_status,
            metrics=metrics if metrics is not None else {"loss": 0.081, "provider": "hf"},
        ))
    await db.flush()
    await db.commit()
    return persona, identity


async def _verdict_row(db, identity_id) -> QAResult:
    return (
        await db.execute(
            select(QAResult)
            .where(QAResult.identity_id == identity_id)
            .order_by(QAResult.created_at)
        )
    ).scalars().all()[-1]


# ─── The ordinary case ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_revalidate_judges_the_existing_model(client, db, registry_override):
    persona, identity = await _seed(db, score=0.4)
    registry_override("llm", _Evaluator())

    resp = await client.post(f"/api/v1/personas/{persona.id}/revalidate")

    assert resp.status_code == 200
    body = resp.json()
    assert body["approved"] is True
    assert body["identity_score"] == 0.75  # "strong" on the 5-level scale
    assert body["identity_id"] == str(identity.id)
    assert body["identity_status"] == "ready"

    qa = await _verdict_row(db, identity.id)
    assert qa.status == QAStatus.PASSED
    # No workflow behind this verdict, and the row says so rather than having a
    # workflow invented to hold it.
    assert qa.workflow_id is None
    assert qa.images_checked == 9
    assert qa.threshold == 0.7

    refreshed = await db.refresh(identity) or identity
    assert refreshed.status == IdentityStatus.READY
    assert refreshed.consistency_score == 0.75
    # A re-check must not lose the adapter the build already published.
    assert refreshed.lora_model_path == "naomi_lora.safetensors"


@pytest.mark.asyncio
async def test_the_evaluator_is_shown_the_stored_facts(client, db, registry_override):
    """The rebuild has to reproduce the build's evidence, fact for fact.

    If this state were assembled differently from the one the build step
    produced, a re-check would be re-judging the model on different evidence
    than the verdict it is re-checking — and the two scores on the record would
    stop being comparable.
    """
    persona, identity = await _seed(db)
    evaluator = registry_override("llm", _Evaluator())

    await client.post(f"/api/v1/personas/{persona.id}/revalidate")

    prompt = evaluator.calls[0]["user_prompt"]
    assert '"reference_views_generated": 9' in prompt
    assert '"reference_coverage": 1.0' in prompt
    assert '"training_succeeded": true' in prompt
    # loss / provider / error live in the TrainingJob's `metrics` JSON — the
    # model has no columns for them, so reading the row means reading that.
    assert '"training_loss": 0.081' in prompt
    assert '"training_provider": "hf"' in prompt
    assert '"hair": "auburn"' in prompt
    # …and nothing that is not evidence.
    assert "password" not in prompt.lower()
    assert "api_key" not in prompt.lower()


@pytest.mark.asyncio
async def test_coverage_is_recomputed_not_copied(client, db, registry_override):
    """The dataset records how many images exist, never how many were asked for.

    There is no coverage column, so the figure the evaluator judges has to be
    recomputed from `len(REFERENCE_VIEWS)` — the same arithmetic the build step
    does. A partial set is the case that matters: 5 of 9 views is a different
    claim from 9 of 9, and an evaluator shown "coverage: 1.0" for a half-built
    set would be approving on a fact that is not true.
    """
    persona, identity = await _seed(db, total_images=5)
    registry_override("llm", _Evaluator())

    resp = await client.post(f"/api/v1/personas/{persona.id}/revalidate")

    assert resp.json()["state"]["reference_coverage"] == round(5 / 9, 3) == 0.556
    assert resp.json()["state"]["reference_views_generated"] == 5


@pytest.mark.asyncio
async def test_a_run_that_failed_training_is_reported_as_failed(client, db, registry_override):
    persona, identity = await _seed(db, job_status=WorkflowStatus.FAILED, metrics={
        "loss": None, "provider": "hf", "error": "CUDA out of memory",
    })
    evaluator = registry_override("llm", _Evaluator(answer={
        "approved": False, "identity_score": "unusable", "quality_score": "unusable",
    }))

    resp = await client.post(f"/api/v1/personas/{persona.id}/revalidate")

    assert resp.json()["state"]["training_succeeded"] is False
    assert '"training_error": "CUDA out of memory"' in evaluator.calls[0]["user_prompt"]
    assert resp.json()["approved"] is False
    assert resp.json()["identity_status"] == "failed"


# ─── Why the endpoint exists ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_broken_run_can_be_re_scored_without_rebuilding(client, db, registry_override):
    """The case the endpoint was added for.

    An identity left FAILED by a verdict its model could not be trusted for —
    or simply by a bad reference set that has since been replaced — gets a
    second verdict from the rows on record, with no regeneration and no
    re-training.
    """
    persona, identity = await _seed(db, status=IdentityStatus.FAILED, score=0.0)
    registry_override("llm", _Evaluator())

    resp = await client.post(f"/api/v1/personas/{persona.id}/revalidate")

    assert resp.json()["approved"] is True
    assert resp.json()["identity_status"] == "ready"
    assert (await db.refresh(identity) or identity).status == IdentityStatus.READY


@pytest.mark.asyncio
async def test_an_unreachable_evaluator_leaves_the_identity_exactly_as_it_was(
    client, db, registry_override
):
    """The failure policy is the build's, and it is the important half.

    A re-check is cheap, which makes it tempting to reach for when something
    looks wrong. It must still be impossible for a *failed* re-check to damage a
    healthy identity: the local model not running is an outage, not a verdict.
    """
    persona, identity = await _seed(db, status=IdentityStatus.READY, score=0.62)
    registry_override("llm", _Evaluator(
        success=False, error="Cannot connect to Ollama at http://localhost:11434"
    ))

    resp = await client.post(f"/api/v1/personas/{persona.id}/revalidate")

    assert resp.status_code == 200
    body = resp.json()
    assert body["approved"] is False
    assert body["evaluation_status"] == "blocked"
    assert body["identity_status"] == "ready"  # unchanged, and the body says so

    qa = await _verdict_row(db, identity.id)
    assert qa.status == QAStatus.REVIEW
    assert qa.workflow_id is None
    assert "Cannot connect to Ollama" in qa.details["error"]

    refreshed = await db.refresh(identity) or identity
    assert refreshed.status == IdentityStatus.READY
    assert refreshed.consistency_score == 0.62  # not clobbered


# ─── Which identity gets judged ──────────────────────────────────────

@pytest.mark.asyncio
async def test_it_judges_the_identity_the_ui_shows(client, db, registry_override):
    """The lock anchor wins over a newer row.

    The persona's lock is the anchor every image-generation path uses, so the
    identity it points at is the one whose score governs generation — and the
    one the Models list displays. If a re-check picked the newest identity
    instead, the UI would show one identity's score while the re-check promoted
    another, and the number on screen would belong to neither.
    """
    persona, anchored = await _seed(db, status=IdentityStatus.READY, score=0.5)
    # A newer identity that is *not* the one in force — a later candidate.
    decoy = Identity(
        id=uuid.uuid4(), persona_id=persona.id, name=persona.name,
        status=IdentityStatus.CANDIDATE, consistency_score=0.0,
    )
    db.add(decoy)
    db.add(IdentityLock(
        persona_id=persona_storage_hex(persona.id), seed=7,
        identity_prompt="anchored prompt", identity_id=anchored.id.hex,
    ))
    await db.commit()

    evaluator = registry_override("llm", _Evaluator())
    resp = await client.post(f"/api/v1/personas/{persona.id}/revalidate")

    assert resp.json()["identity_id"] == str(anchored.id)

    refreshed = await db.refresh(anchored) or anchored
    assert refreshed.status == IdentityStatus.READY
    # The decoy is untouched — nothing judged it.
    untouched = await db.refresh(decoy) or decoy
    assert untouched.status == IdentityStatus.CANDIDATE
    assert untouched.consistency_score == 0.0
    assert len(evaluator.calls) == 1


@pytest.mark.asyncio
async def test_without_a_lock_it_prefers_a_settled_identity_over_a_newer_candidate(
    client, db, registry_override
):
    """Mid-build, the newest row is a candidate and judging it would be wrong."""
    persona, settled = await _seed(db, status=IdentityStatus.TRAINING, score=0.0)
    newer_candidate = Identity(
        id=uuid.uuid4(), persona_id=persona.id, name=persona.name,
        status=IdentityStatus.CANDIDATE, consistency_score=0.0,
    )
    db.add(newer_candidate)
    await db.commit()

    registry_override("llm", _Evaluator())
    resp = await client.post(f"/api/v1/personas/{persona.id}/revalidate")

    assert resp.json()["identity_id"] == str(settled.id)


# ─── When there is nothing to judge ──────────────────────────────────

@pytest.mark.asyncio
async def test_a_persona_with_no_identity_cannot_be_revalidated(client, db):
    persona = Persona(id=uuid.uuid4(), name=f"Empty_{uuid.uuid4().hex[:8]}", age=25)
    db.add(persona)
    await db.commit()

    resp = await client.post(f"/api/v1/personas/{persona.id}/revalidate")

    assert resp.status_code == 404
    assert "no identity to re-evaluate" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_an_identity_with_nothing_recorded_cannot_be_judged(client, db, registry_override):
    """A candidate from `generate_candidates` has no dataset and no training run.

    Refused rather than judged: an evaluator handed two nulls would answer
    whatever it answers to nothing, and that answer would be written down as a
    verdict on the identity.
    """
    persona, identity = await _seed(db, status=IdentityStatus.CANDIDATE,
                                    total_images=None, job_status=None)
    evaluator = registry_override("llm", _Evaluator())

    resp = await client.post(f"/api/v1/personas/{persona.id}/revalidate")

    assert resp.status_code == 409
    assert "nothing to judge" in resp.json()["detail"]
    # Nothing was asked, and nothing was recorded.
    assert evaluator.calls == []
    assert (await _pending_qa(db, identity.id)) == []


@pytest.mark.asyncio
async def test_an_unknown_persona_is_a_404(client):
    resp = await client.post(f"/api/v1/personas/{uuid.uuid4()}/revalidate")
    assert resp.status_code == 404


async def _pending_qa(db, identity_id) -> list:
    return (
        await db.execute(select(QAResult).where(QAResult.identity_id == identity_id))
    ).scalars().all()
