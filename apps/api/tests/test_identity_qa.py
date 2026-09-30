"""The identity QA gate — local LLM (Ollama) evaluator.

This replaced a two-evaluator arrangement: a preferred remote typed-decision
model, with the local LLM as its fallback. The remote one is gone (unreachable,
and unable to do the research half of its job even when reachable), so the local
model is now the *only* evaluator rather than a second choice — which changes
what has to be true of it:

  - it must actually receive the build facts, because an evaluator handed a
    placeholder prompt returns a verdict that means nothing; and
  - its answer must be type-checked, because there is no longer a typed contract
    in front of it and the previous version only checked that three keys existed.

The behaviour that must survive any evaluator change is here too: a verdict
promotes or demotes the identity, while an evaluator that *cannot* answer leaves
the identity alone and records REVIEW. That is the regression test for the old
bug, where an evaluator that merely declined to emit JSON marked a healthy
identity FAILED after a two-hour build.
"""
import json
import uuid

import pytest

from app.models import (
    Identity, IdentityStatus, Persona, QAResult, QAStatus,
)
from app.providers.base import ProviderResult
from app.workflows.persona_flow import (
    CONSISTENCY_LEVELS, IDENTITY_QA_SCHEMA, _coerce_qa_verdict,
    score_label_to_number, validate_identity_handler,
)

from tests.fakes import FakeLLMProvider


class _Evaluator(FakeLLMProvider):
    """The evaluator, answering in the shape the QA step reads.

    FakeLLMProvider returns `{"text": ...}`, which the step never looks at (it
    reads `result.data["content"]`), so on its own it always falls through to
    the blocked branch. This one answers the way Ollama's provider does, and
    records the prompt so a test can assert what the evaluator was actually
    shown.
    """

    def __init__(self, answer=None, success=True, error="", raises=None):
        self.answer = answer if answer is not None else {
            "approved": True, "identity_score": "strong", "quality_score": "excellent",
        }
        self.success = success
        self.error = error
        self.raises = raises
        self.calls: list[dict] = []

    async def complete(self, system_prompt, user_prompt, schema=None,
                       temperature=0.7, max_tokens=2048):
        self.calls.append({
            "system_prompt": system_prompt, "user_prompt": user_prompt,
            "schema": schema,
        })
        if self.raises is not None:
            raise self.raises
        return ProviderResult(
            success=self.success,
            data={"content": self.answer} if self.success else {},
            error=self.error,
            provider="local_llm",
            latency_ms=1,
        )


# ─── Layer 1: reading the evaluator's answer ─────────────────────────

def test_score_label_mapping_is_stable():
    # 5 levels → 0.0, 0.25, 0.5, 0.75, 1.0
    assert [score_label_to_number(l, CONSISTENCY_LEVELS) for l in CONSISTENCY_LEVELS] == \
        [0.0, 0.25, 0.5, 0.75, 1.0]
    # Casing and whitespace must not silently fail an identity.
    assert score_label_to_number("Strong", CONSISTENCY_LEVELS) == 0.75
    assert score_label_to_number("  EXCELLENT ", CONSISTENCY_LEVELS) == 1.0
    # A numeric answer already on the 0..1 scale is a score.
    assert score_label_to_number(0.6, CONSISTENCY_LEVELS) == 0.6
    assert score_label_to_number("0.6", CONSISTENCY_LEVELS) == 0.6
    # Anything unrecognised scores 0.0 rather than raising or guessing.
    assert score_label_to_number("transcendent", CONSISTENCY_LEVELS) == 0.0
    assert score_label_to_number(None, CONSISTENCY_LEVELS) == 0.0
    assert score_label_to_number("", CONSISTENCY_LEVELS) == 0.0
    # Off-scale numbers are not silently clamped into a passing score.
    assert score_label_to_number(3, CONSISTENCY_LEVELS) == 0.0
    assert score_label_to_number(True, CONSISTENCY_LEVELS) == 0.0


def test_the_word_no_is_not_a_pass():
    """A model saying "no" must not read as approval.

    The old check was `{"approved", "identity_score", "quality_score"}.issubset(
    candidate)` — key presence only. `"no"` is a non-empty string, so it is
    truthy, so `QAStatus.PASSED if content["approved"] else FAILED` recorded a
    refusal as a pass. This is the assertion that pins the fix.
    """
    assert _coerce_qa_verdict(
        {"approved": "no", "identity_score": "weak", "quality_score": "weak"}
    )["approved"] is False
    assert _coerce_qa_verdict({"approved": "no", "identity_score": "strong",
                               "quality_score": "strong"})["approved"] is False


def test_a_non_numeric_score_does_not_raise():
    """`float(content["identity_score"])` used to raise on a label answer.

    The key check passed for `{"identity_score": "high"}`, and the handler then
    called `float()` on it — a ValueError inside the step, i.e. a 500 that
    aborts a build after the two-hour reference and training pipeline has run.
    """
    verdict = _coerce_qa_verdict(
        {"approved": True, "identity_score": "high", "quality_score": "excellent"}
    )
    assert verdict is not None
    assert verdict["identity_score"] == 0.0  # unrecognised → 0.0, not a crash
    assert verdict["quality_score"] == 1.0


def test_a_verdict_without_approved_is_not_a_verdict():
    """Fail closed: no approval means no verdict, which means REVIEW."""
    assert _coerce_qa_verdict({}) is None
    assert _coerce_qa_verdict({"identity_score": "strong"}) is None
    assert _coerce_qa_verdict({"approved": None, "identity_score": "strong"}) is None
    assert _coerce_qa_verdict({"approved": 1}) is None
    assert _coerce_qa_verdict(None) is None
    assert _coerce_qa_verdict("approved") is None


def test_unambiguous_words_are_accepted_but_only_unambiguous_ones():
    assert _coerce_qa_verdict({"approved": " YES "})["approved"] is True
    assert _coerce_qa_verdict({"approved": "true"})["approved"] is True
    assert _coerce_qa_verdict({"approved": "FALSE"})["approved"] is False
    # Anything else is refused rather than guessed at.
    assert _coerce_qa_verdict({"approved": "probably"}) is None
    assert _coerce_qa_verdict({"approved": "maybe"}) is None


# ─── Layer 2: the handler ────────────────────────────────────────────

async def _seed(db, consistency: float = 0.0, status=IdentityStatus.TRAINING):
    persona = Persona(
        id=uuid.uuid4(), name=f"IdentityQA_{uuid.uuid4().hex[:8]}", age=27,
        appearance={"hair": "auburn"}, personality=["wry"],
    )
    db.add(persona)
    identity = Identity(
        id=uuid.uuid4(), persona_id=persona.id, name=persona.name,
        status=status, consistency_score=consistency,
    )
    db.add(identity)
    # flush, not commit: the `db` fixture rolls back, so nothing leaks between
    # tests and the unique persona name cannot collide across runs.
    await db.flush()
    return persona, identity


def _step_input(persona, identity) -> dict:
    return {
        "persona_id": str(persona.id),
        # Which step carries identity_id is not fixed — approve_identity emits
        # it, and the handler scans the step outputs in order to find it.
        "step_1_output": {"identity_id": str(identity.id), "status": "approved"},
        "step_2_output": {"total_images": 9, "coverage_score": 1.0},
        "step_3_output": {"training_failed": False, "loss": 0.081,
                          "provider": "hf", "model_path": "/tmp/lora.safetensors"},
    }


@pytest.mark.asyncio
async def test_pass_verdict_promotes_identity_and_shows_the_evaluator_the_facts(
    db, monkeypatch, registry_override
):
    persona, identity = await _seed(db)
    evaluator = registry_override("llm", _Evaluator())

    out = await validate_identity_handler(
        uuid.uuid4(), uuid.uuid4(), _step_input(persona, identity), db
    )

    assert out["approved"] is True
    # "strong" → 0.75 on the 5-level scale.
    assert out["identity_score"] == 0.75

    qa = await db.get(QAResult, uuid.UUID(out["qa_id"]))
    assert qa.status == QAStatus.PASSED
    assert qa.threshold == 0.7
    assert qa.images_checked == 9
    assert qa.details["evaluator"] == "llm"
    # The raw answer rides along, so a verdict that looks wrong in the UI can be
    # traced back to what the model actually said.
    assert qa.details["model_answer"]["identity_score"] == "strong"

    refreshed = await db.get(Identity, identity.id)
    assert refreshed.status == IdentityStatus.READY
    assert refreshed.consistency_score == 0.75
    # The handler still backfills the trained model path from the step output.
    assert refreshed.lora_model_path == "/tmp/lora.safetensors"

    # ── The evaluator was handed the real build facts ────────────────
    assert len(evaluator.calls) == 1
    prompt = evaluator.calls[0]["user_prompt"]
    assert '"reference_views_generated": 9' in prompt
    assert '"training_succeeded": true' in prompt
    assert '"training_loss": 0.081' in prompt
    assert '"hair": "auburn"' in prompt
    # …and no credentials, wherever the evaluator runs.
    assert "password" not in prompt.lower()
    assert "api_key" not in prompt.lower()


@pytest.mark.asyncio
async def test_the_schema_is_offered_so_a_local_model_answers_in_json(
    db, registry_override
):
    """Ollama needs the schema to enter JSON mode.

    `OllamaLLMProvider.complete` sets `format: "json"` and `think: false` only
    when a schema is passed, and without them a thinking model spends the token
    budget on its reasoning trace and the JSON is reached late or not at all.
    The step used to pass no schema, so this is a wiring guard: it fails if the
    schema is dropped from the call.
    """
    persona, identity = await _seed(db)
    evaluator = registry_override("llm", _Evaluator())

    await validate_identity_handler(
        uuid.uuid4(), uuid.uuid4(), _step_input(persona, identity), db
    )

    schema = evaluator.calls[0]["schema"]
    assert schema is IDENTITY_QA_SCHEMA
    assert schema["properties"]["approved"]["type"] == "boolean"
    assert schema["properties"]["identity_score"]["enum"] == CONSISTENCY_LEVELS
    assert schema["required"] == ["approved", "identity_score", "quality_score"]


@pytest.mark.asyncio
async def test_rejecting_verdict_marks_identity_failed(db, registry_override):
    persona, identity = await _seed(db)
    registry_override("llm", _Evaluator(answer={
        "approved": False, "identity_score": "weak", "quality_score": "unusable",
    }))

    out = await validate_identity_handler(
        uuid.uuid4(), uuid.uuid4(), _step_input(persona, identity), db
    )

    assert out["approved"] is False
    qa = await db.get(QAResult, uuid.UUID(out["qa_id"]))
    assert qa.status == QAStatus.FAILED
    assert (await db.get(Identity, identity.id)).status == IdentityStatus.FAILED


@pytest.mark.asyncio
async def test_unreachable_evaluator_records_review_and_leaves_identity_alone(
    db, registry_override
):
    """The regression that matters: 'could not evaluate' is not 'is bad'.

    The previous implementation marked the identity FAILED whenever the
    evaluator failed to emit JSON, destroying a healthy identity that had
    already survived a two-hour build.
    """
    persona, identity = await _seed(db, consistency=0.62, status=IdentityStatus.TRAINING)
    registry_override("llm", _Evaluator(
        success=False, error="Cannot connect to Ollama at http://localhost:11434"
    ))

    out = await validate_identity_handler(
        uuid.uuid4(), uuid.uuid4(), _step_input(persona, identity), db
    )

    assert out["approved"] is False
    assert out["evaluation_status"] == "blocked"

    qa = await db.get(QAResult, uuid.UUID(out["qa_id"]))
    assert qa.status == QAStatus.REVIEW
    # The row says *why*, so an outage is not a blank.
    assert "Cannot connect to Ollama" in qa.details["error"]

    # Untouched: neither promoted nor demoted, and the score is not clobbered.
    refreshed = await db.get(Identity, identity.id)
    assert refreshed.status == IdentityStatus.TRAINING
    assert refreshed.consistency_score == 0.62


@pytest.mark.asyncio
async def test_an_answer_that_cannot_be_read_is_review_not_failed(db, registry_override):
    """A model that answers in prose has not judged the identity bad."""
    persona, identity = await _seed(db, consistency=0.4)
    registry_override("llm", _Evaluator(answer={"raw_response": "It looks fine to me!"}))

    out = await validate_identity_handler(
        uuid.uuid4(), uuid.uuid4(), _step_input(persona, identity), db
    )

    assert out["evaluation_status"] == "blocked"
    qa = await db.get(QAResult, uuid.UUID(out["qa_id"]))
    assert qa.status == QAStatus.REVIEW
    assert "no usable verdict" in qa.details["error"]
    assert (await db.get(Identity, identity.id)).status == IdentityStatus.TRAINING


@pytest.mark.asyncio
async def test_a_raising_evaluator_does_not_abort_the_build(db, registry_override):
    """The step sits after ~2 hours of generation and training.

    An unexpected failure at this boundary — a transport error the provider did
    not convert, an unexpected shape — must degrade to REVIEW rather than
    propagate. This is the protection the remote evaluator's branch used to
    carry with a broad `except`; it has to survive its removal, or the last
    step of the pipeline becomes the one that can kill it.
    """
    persona, identity = await _seed(db, consistency=0.3)
    registry_override("llm", _Evaluator(raises=RuntimeError("model crashed")))

    out = await validate_identity_handler(
        uuid.uuid4(), uuid.uuid4(), _step_input(persona, identity), db
    )

    assert out["approved"] is False
    assert out["evaluation_status"] == "blocked"
    qa = await db.get(QAResult, uuid.UUID(out["qa_id"]))
    assert qa.status == QAStatus.REVIEW
    assert qa.details["error"].startswith("RuntimeError")
    assert (await db.get(Identity, identity.id)).status == IdentityStatus.TRAINING
