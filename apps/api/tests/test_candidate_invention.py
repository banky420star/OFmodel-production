"""Persona Studio — a step that invents its own input has to say so.

`generate_candidates` writes three candidate identities even when the LLM never
answered: empty descriptions and `random.uniform(0.80, 0.95)` consistency
scores. `approve_identity` then picks the highest score, so a failed call still
produces a confident-looking identity choice. The per-identity `source` field
already said "fallback"; the step result looked identical either way, and the
step result is what a person reads.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models import Identity, Persona
from app.providers.base import LLMProvider, ProviderResult
from app.workflows.persona_flow import generate_candidates_handler


class CannedLLM(LLMProvider):
    """An LLM whose answer is decided by the test, including not answering."""

    def __init__(self, content=None, success: bool = True, error: str = ""):
        self._content = content
        self._success = success
        self._error = error

    async def complete(self, system_prompt, user_prompt, schema=None,
                       temperature=0.7, max_tokens=2048) -> ProviderResult:
        if not self._success:
            return ProviderResult(False, {}, provider="canned", error=self._error)
        return ProviderResult(
            True, {"content": self._content, "model": "canned"}, provider="canned",
            latency_ms=1,
        )

    async def health_check(self) -> ProviderResult:
        return ProviderResult(True, {"ok": True}, provider="canned")


async def _seed_persona(db) -> Persona:
    persona = Persona(id=uuid.uuid4(), name=f"Cand{uuid.uuid4().hex[:6]}", age=25)
    db.add(persona)
    await db.flush()
    return persona


async def _run(db, persona, workflow_id):
    return await generate_candidates_handler(
        workflow_id=workflow_id, step_id=uuid.uuid4(),
        input_data={"persona_id": str(persona.id), "persona_name": persona.name},
        db=db,
    )


@pytest.mark.asyncio
async def test_a_real_answer_carries_no_warning(db, registry_override):
    persona = await _seed_persona(db)
    registry_override("llm", CannedLLM(content={"candidates": [
        {"name": "A", "appearance": "tall", "personality": "calm",
         "consistency_score": 0.93},
        {"name": "B", "appearance": "short", "personality": "wry",
         "consistency_score": 0.88},
    ]}))

    out = await _run(db, persona, uuid.uuid4())

    assert out["count"] == 3, "the loop tops up to three whatever it is given"
    assert out["warnings"] == [], "a real answer must not be flagged"


@pytest.mark.asyncio
async def test_an_llm_that_never_answered_says_the_candidates_are_invented(
    db, registry_override,
):
    """This is the dangerous one: the step still returns three candidates with
    plausible scores, so nothing downstream can tell they were never the model's
    opinion."""
    persona = await _seed_persona(db)
    registry_override("llm", CannedLLM(success=False, error="ReadTimeout"))

    out = await _run(db, persona, uuid.uuid4())

    assert out["count"] == 3
    assert out["warnings"], "an invented identity must not read as a normal result"
    assert "invented" in out["warnings"][0]
    assert "ReadTimeout" in out["warnings"][0], "the reason belongs in the warning"

    rows = (await db.execute(
        select(Identity).where(Identity.persona_id == persona.id)
    )).scalars().all()
    assert len(rows) == 3
    for row in rows:
        assert row.metadata_json["source"] == "fallback"
        assert row.metadata_json["appearance"] == "", "nothing was described"


@pytest.mark.asyncio
async def test_an_answer_with_no_candidates_is_flagged_separately(db, registry_override):
    """A model that replied but returned an empty list is a different fix from a
    model that never replied, so the two must not share a message."""
    persona = await _seed_persona(db)
    registry_override("llm", CannedLLM(content={"candidates": []}))

    out = await _run(db, persona, uuid.uuid4())

    assert out["count"] == 3
    assert out["warnings"]
    assert "no candidates" in out["warnings"][0]
    assert "did not answer" not in out["warnings"][0]


@pytest.mark.asyncio
async def test_the_fabricated_scores_are_the_documented_range(db, registry_override):
    """`approve_identity` reads these as a ranking. They are random, and they are
    recorded as random — but only inside the documented range, so a fabricated
    candidate can never outrank a real one by accident of scale."""
    persona = await _seed_persona(db)
    registry_override("llm", CannedLLM(success=False, error="boom"))

    await _run(db, persona, uuid.uuid4())

    rows = (await db.execute(
        select(Identity).where(Identity.persona_id == persona.id)
    )).scalars().all()
    for row in rows:
        assert 0.80 <= row.consistency_score <= 0.95, (
            "a fabricated score outside the documented range would rank "
            "unpredictably against real candidates"
        )
