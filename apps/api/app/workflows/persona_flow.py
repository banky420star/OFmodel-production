"""Persona Studio — Persona creation workflow.

Flow:
    create_persona
    → generate_identity_candidates
    → human_approval
    → build_reference_dataset
    → train_lora
    → validate_identity
    → create_voice
    → activate_persona
"""

from __future__ import annotations
import json
import random
import structlog
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app import paths
from app.lora_base import training_base_from_steps
from app.models import (
    Persona, Identity, IdentityLock, ReferenceDataset, TrainingJob, QAResult,
    PersonaStatus, IdentityStatus, IdentityLockStatus, QAStatus, WorkflowStatus,
    persona_storage_hex, ensure_identity_lock,
)
from app.providers.registry import get_registry

logger = structlog.get_logger()


# ─── Identity QA vocabulary ──────────────────────────────────────────
# The two scores on a QA row are one of five ordered levels, and the level is
# what the evaluator is asked for. A local 4B model returns one of five known
# tokens far more reliably than it returns a float on a 0..1 scale, and the
# label maps back to a stable number so `threshold` keeps meaning.

CONSISTENCY_LEVELS = ["unusable", "weak", "acceptable", "strong", "excellent"]

# The nine reference views one identity build generates. A module constant
# rather than a local list, because the coverage figure the evaluator judges is
# `generated / len(REFERENCE_VIEWS)` — and the on-demand re-evaluation has to
# recompute that from stored rows, since the reference dataset records how many
# images exist but not how many were asked for.
REFERENCE_VIEWS = [
    "frontal portrait, neutral expression, studio lighting",
    "left profile, natural light",
    "right profile, soft window light",
    "three quarter view, warm smile",
    "three quarter view, confident expression",
    "full body standing, fashion pose",
    "medium shot seated, relaxed",
    "indoor lighting, cozy setting",
    "outdoor natural light, golden hour",
]

IDENTITY_QA_SCHEMA = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "identity_score": {"type": "string", "enum": CONSISTENCY_LEVELS},
        "quality_score": {"type": "string", "enum": CONSISTENCY_LEVELS},
    },
    "required": ["approved", "identity_score", "quality_score"],
}


def score_label_to_number(label, levels: list[str]) -> float:
    """Map a returned score label to 0..1.

    Tolerant of casing and surrounding whitespace on purpose: "Strong" and
    " strong " must not silently become 0.0 and fail an identity. A numeric
    answer already on the 0..1 scale is taken as-is. Anything else returns
    0.0 — the caller keeps the raw answer on the QA row, so a label this
    doesn't recognise is visible rather than invisible.
    """
    if label is None:
        return 0.0
    if isinstance(label, bool):  # bools are ints; not a score
        return 0.0
    if isinstance(label, (int, float)):
        value = float(label)
        return round(value, 4) if 0.0 <= value <= 1.0 else 0.0
    text = str(label).strip()
    if not text:
        return 0.0
    try:
        value = float(text)
    except ValueError:
        value = None
    if value is not None:
        return round(value, 4) if 0.0 <= value <= 1.0 else 0.0
    folded = text.casefold()
    for index, level in enumerate(levels):
        if level.casefold() == folded:
            return round(index / (len(levels) - 1), 4)
    return 0.0


def _coerce_qa_verdict(candidate) -> dict | None:
    """Turn an evaluator's answer into the three fields the gate reads, or None.

    A local model answers in JSON but not always in *types*, and the previous
    implementation only checked that the three keys were present. Two real
    consequences of that, both fixed here:

      - `{"approved": "no"}` passed the key check and then read as a **truthy
        string**, so a model refusing the identity was recorded as PASSED.
      - `{"identity_score": "high"}` passed the key check and then `float()` on
        it raised inside the handler — a 500 that aborts the build after the
        two-hour reference and training pipeline has already run.

    `approved` is therefore accepted only as a real bool or an unambiguous word,
    and anything unreadable returns None so the caller records REVIEW. The
    fail-closed direction matters: an unparsed answer must not become a pass.
    """
    if not isinstance(candidate, dict):
        return None

    approved = candidate.get("approved")
    if isinstance(approved, bool):
        pass
    elif isinstance(approved, str) and approved.strip().casefold() in {"true", "yes"}:
        approved = True
    elif isinstance(approved, str) and approved.strip().casefold() in {"false", "no"}:
        approved = False
    else:
        return None

    # A missing score is not a reason to refuse the verdict — the identity gate
    # reads `approved`, and `score_label_to_number` already maps anything it does
    # not recognise to 0.0 rather than raising.
    return {
        "approved": approved,
        "identity_score": score_label_to_number(
            candidate.get("identity_score"), CONSISTENCY_LEVELS
        ),
        "quality_score": score_label_to_number(
            candidate.get("quality_score"), CONSISTENCY_LEVELS
        ),
    }


def _get_llm():
    return get_registry().get_llm_provider()


def _get_image():
    return get_registry().get_image_provider()


def _get_trainer():
    return get_registry().get_trainer_provider()


def _get_voice():
    return get_registry().get_voice_provider()


async def create_persona_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Create the persona record."""
    persona = Persona(
        id=uuid4(),
        name=input_data["name"],
        age=input_data.get("age", 24),
        description=input_data.get("description", ""),
        # These belong on the columns, not in metadata_json. The gate that
        # decides whether a persona may produce content —
        # `persona_ready_for_production` — reads `persona.adult_verified`, and
        # so does the adult-content route. Writing the value only into
        # metadata_json left the column at its default, so a persona built with
        # adult_verified=True was still refused by the gate that the value was
        # supposed to satisfy. Nothing reads appearance/personality/brand out of
        # persona.metadata_json either; the columns are the source of truth
        # (POST /personas writes them the same way).
        adult_verified=input_data.get("adult_verified", False),
        synthetic_identity=input_data.get("synthetic_identity", True),
        status=PersonaStatus.ACTIVE,
        appearance=input_data.get("appearance", {}) or {},
        personality=input_data.get("personality", []) or [],
        brand=input_data.get("brand", "luxury lifestyle"),
        voice_style=input_data.get("voice_style", ""),
        publishing_frequency=input_data.get("publishing_frequency", ""),
        metadata_json={},
    )
    db.add(persona)
    await db.flush()
    return {"persona_id": str(persona.id), "name": persona.name}


async def generate_candidates_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Generate identity candidates using LLM."""
    persona_id = input_data["persona_id"]
    persona_name = input_data.get('persona_name', 'unknown')
    brand = input_data.get('brand', 'lifestyle')
    age = input_data.get('age', 24)
    appearance = input_data.get('appearance', {})
    personality = input_data.get('personality', '')

    llm = _get_llm()
    schema = {
        "candidates": [
            {
                "name": "string",
                "appearance": "detailed physical description",
                "personality": "comma-separated traits",
                "consistency_score": 0.95
            }
        ]
    }
    system_prompt = (
        "You are a creative AI director for a synthetic persona studio. "
        "Generate realistic, detailed identity candidates for virtual influencer personas. "
        "Each candidate must have a unique look that fits the brand."
    )
    user_prompt = (
        f"Generate 3 identity candidates for persona '{persona_name}', age {age}, "
        f"brand: {brand}. Personality: {personality}. "
        f"Appearance details: {appearance}. "
        f"Each candidate needs: name, detailed physical appearance (hair, eyes, skin, build, style), "
        f"personality traits (comma-separated), and consistency_score (0.80-0.99)."
    )
    result = await llm.complete(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=schema,
    )

    # Parse LLM output into identity candidates
    candidates = []
    llm_data = result.data.get("content", {}) if result.success else {}
    raw_candidates = llm_data.get("candidates", []) if isinstance(llm_data, dict) else []

    for i in range(max(len(raw_candidates), 3)):
        if i < len(raw_candidates):
            c = raw_candidates[i]
            candidate_name = c.get("name", f"Candidate {i+1}")
            appearance_desc = c.get("appearance", "")
            personality_desc = c.get("personality", "")
            score = float(c.get("consistency_score", 0.90))
        else:
            candidate_name = f"Candidate {i+1}"
            appearance_desc = ""
            personality_desc = ""
            score = round(random.uniform(0.80, 0.95), 3)

        candidate = Identity(
            id=uuid4(),
            persona_id=UUID(persona_id),
            name=candidate_name,
            status=IdentityStatus.CANDIDATE,
            consistency_score=score,
            metadata_json={
                "source": "ollama" if result.success else "fallback",
                "candidate_index": i,
                "appearance": appearance_desc,
                "personality": personality_desc,
                "model": result.data.get("model", "unknown") if result.success else "none",
            },
        )
        db.add(candidate)
        candidates.append(str(candidate.id))
    await db.flush()

    # When the LLM did not answer, the loop above still writes three candidates —
    # with empty descriptions and `random.uniform` consistency scores — and
    # `approve_identity` then picks the highest score. That is a choice between
    # invented options presented as a normal step result. The per-identity
    # `source` says "fallback", but the step looked identical either way, so the
    # run says it too.
    warnings = []
    if not result.success:
        warnings.append(
            "The LLM did not answer, so these candidates were invented: their "
            f"descriptions are empty and their scores are random ({result.error}). "
            "The approved identity is therefore a placeholder."
        )
    elif not raw_candidates:
        warnings.append(
            "The LLM answered but returned no candidates, so these were invented "
            "with random scores."
        )

    return {"candidates": candidates, "count": len(candidates), "warnings": warnings}


async def approve_identity_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Approve the selected identity candidate."""
    identity_id = input_data.get("identity_id") or input_data.get("selected_identity")
    if not identity_id:
        # Auto-select the highest-scoring candidate
        result = await db.execute(
            select(Identity)
            .where(Identity.persona_id == UUID(input_data["persona_id"]))
            .where(Identity.status == IdentityStatus.CANDIDATE)
            .order_by(Identity.consistency_score.desc())
            .limit(1)
        )
        identity = result.scalar_one_or_none()
        if identity:
            identity_id = str(identity.id)

    if identity_id:
        identity = await db.get(Identity, UUID(identity_id))
        if identity:
            identity.status = IdentityStatus.APPROVED
            await db.flush()
            return {"identity_id": str(identity.id), "status": "approved"}

    return {"identity_id": None, "status": "no_candidates"}


MIN_AVATAR_PIXELS = 256


def avatar_reference_state(path) -> tuple[bool, str]:
    """Whether an avatar file can serve as an identity reference, and why not.

    `path.exists()` was the only test, and this directory currently holds 128
    placeholder images left behind by e2e runs — valid **8x8 PNGs named .jpg**.
    So existence is wrong in both directions: they exist, and they decode
    cleanly, which means a decode check passes them too. But the identity engine
    upscales the avatar to the generation size, so an 8x8 placeholder becomes a
    1024px blur, and *that* blur is the face every reference image is edited
    toward. Size is what separates a reference from a placeholder.
    """
    from pathlib import Path as _Path

    if not _Path(path).exists():
        return False, "no avatar yet"
    try:
        from PIL import Image

        with Image.open(path) as im:
            width, height = im.size
            im.verify()
    except Exception as exc:
        return False, f"unreadable ({type(exc).__name__})"
    if min(width, height) < MIN_AVATAR_PIXELS:
        return False, f"only {width}x{height}"
    return True, f"{width}x{height}"


async def build_reference_dataset_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Generate reference images for the identity."""
    # Find persona_id and identity_id from input or any previous step output
    persona_id = input_data.get("persona_id")
    identity_id = input_data.get("identity_id")
    if not identity_id:
        for key in sorted(input_data.keys()):
            if key.startswith("step_") and isinstance(input_data[key], dict):
                if "identity_id" in input_data[key]:
                    identity_id = input_data[key]["identity_id"]
                    break
    if not identity_id:
        return {"error": "no identity_id"}
    if not persona_id:
        return {"error": "no persona_id"}

    # Create the durable identity lock for this persona through the async ORM
    # so Auto-Produce, gallery, and the sync image engine all agree on one row.
    persona = await db.get(Persona, UUID(persona_id))
    lock = await ensure_identity_lock(persona, db, identity_id=UUID(identity_id))
    storage_hex = persona_storage_hex(persona.id)

    # Commit before generating, because of what this handler does next: the
    # avatar bootstrap and nine reference views, measured at 16.5 s per SDXL
    # step on this machine — roughly 75 minutes of work. `ensure_identity_lock`
    # just wrote, and `execute_step` does not commit until this handler returns,
    # so without this line the step holds SQLite's single writer slot for the
    # whole build. WAL lets readers through, but not writers: measured, the next
    # writer blocks for the entire 30 s busy_timeout and then fails outright with
    # "database is locked". Every other write in the app — the Job progress the
    # Create Model page polls, a second persona build, the operator's own
    # actions — would fail for the length of the build.
    await db.commit()

    # Generate reference images through the identity engine using the same
    # full-hex key the rest of the system uses.
    from app.identity_engine import generate_identity_locked, stable_seed
    from pathlib import Path as _Path

    persona_name = input_data.get("persona_name", "model")

    # The edit-based provider needs an avatar as its identity reference.
    # Bootstrap a real one with a text-to-image call through the configured
    # image provider — no placeholder can enter storage/avatars/. On failure
    # the step fails and the identity lock never becomes ACTIVE.
    avatar_dir = paths.AVATAR_DIR
    avatar_dir.mkdir(parents=True, exist_ok=True)
    avatar_path = avatar_dir / f"{persona.name.lower()}.jpg"
    # Read the state before generating, so the message can distinguish "there was
    # nothing" from "there was something unusable" — and so writing the new file
    # cannot change the answer underneath the message.
    avatar_usable, avatar_why = avatar_reference_state(avatar_path)
    if not avatar_usable:
        appearance = (getattr(persona, "appearance", None) or {}) or {}
        appearance_bits = ", ".join(
            f"{k.replace('_', ' ')}: {v}" for k, v in appearance.items()
        )
        avatar_prompt = (
            f"Professional portrait photograph of {persona.name}. "
            f"{persona.description or ''}. "
            f"{appearance_bits}. Frontal head-and-shoulders portrait, "
            "neutral studio background, soft even lighting, photorealistic"
        )
        avatar_result = await _get_image().generate(
            prompt=avatar_prompt,
            width=768,
            height=768,
            seed=stable_seed("avatar", str(persona.id)),
        )
        if not avatar_result.success or not avatar_result.data.get("image_bytes"):
            raise RuntimeError(
                f"Avatar bootstrap failed for {persona.name}: "
                f"{avatar_result.error or 'image provider returned no image'}"
            )
        avatar_path.write_bytes(avatar_result.data["image_bytes"])
        if avatar_why == "no avatar yet":
            logger_info = (
                f"generated real avatar for {persona.name} via {avatar_result.provider}"
            )
        else:
            logger_info = (
                f"replaced an avatar that could not be a face ({avatar_why}) for "
                f"{persona.name} via {avatar_result.provider}"
            )
    else:
        logger_info = f"existing avatar used as identity reference ({avatar_why})"

    views = REFERENCE_VIEWS

    dataset_dir = paths.DATASETS_DIR / storage_hex[:8]
    dataset_dir.mkdir(parents=True, exist_ok=True)

    image_keys = []
    gen_errors = []
    for i, view in enumerate(views):
        out_path = str(dataset_dir / f"ref_{i+1:02d}.png")
        result = await generate_identity_locked(
            persona_id_hex=storage_hex,
            scene_prompt=view,
            output_path=out_path,
            seed_override=stable_seed(identity_id, i),
            # The same session that just wrote the lock. Without it this call
            # opens its own, and the lock `ensure_identity_lock` flushed a few
            # lines above is not committed yet — `execute_step` commits only
            # after this handler returns — so the second connection cannot see
            # the row it just created and every view fails with "No identity
            # lock for persona". The shoot path has always passed it
            # (content_flow); this one was the only caller that did not, and
            # the shared StaticPool connection used to hide the difference.
            db=db,
        )
        if result["success"]:
            image_keys.append(out_path)
        else:
            gen_errors.append(result.get("error", "unknown"))

    if not image_keys:
        # Every reference view failed. Returning normally from here marked the
        # step COMPLETED with total_images=0 and a truncated error, so the
        # workflow walked on, tried to train a LoRA against an empty dataset,
        # and would have gone on to activate a persona with no trained model —
        # a build reporting success while producing nothing. Fail loudly and
        # never write the empty dataset row.
        raise RuntimeError(
            f"Generated 0/{len(views)} reference images for {persona.name} "
            f"(identity {identity_id}): {gen_errors[0] if gen_errors else 'no error reported'}"
        )

    dataset = ReferenceDataset(
        id=uuid4(),
        identity_id=UUID(identity_id),
        name="primary_reference",
        image_keys=image_keys,
        total_images=len(image_keys),
        quality_score=None,
    )
    db.add(dataset)
    await db.flush()

    return {
        "dataset_id": str(dataset.id),
        "total_images": len(image_keys),
        "quality_score": None,
        "coverage_score": round(len(image_keys) / len(views), 3),
        "identity_lock_status": lock.status,
        "avatar": logger_info,
        "errors": gen_errors[:1],
    }


async def train_lora_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Train LoRA model for the identity."""
    # Find identity_id and dataset_id from any previous step output
    identity_id = input_data.get("identity_id")
    dataset_id = input_data.get("dataset_id")
    if not identity_id or not dataset_id:
        for key in sorted(input_data.keys()):
            if key.startswith("step_") and isinstance(input_data[key], dict):
                val = input_data[key]
                if not identity_id and "identity_id" in val:
                    identity_id = val["identity_id"]
                if not dataset_id and "dataset_id" in val:
                    dataset_id = val["dataset_id"]
    if not identity_id or not dataset_id:
        return {"error": "missing identity_id or dataset_id"}

    tr = _get_trainer()

    # Hand the trainer the dataset's own reference images. Without this it
    # resolved images from `storage/datasets/<dataset_id>/` while the build step
    # wrote them to `storage/datasets/<persona_hex8>/`, so training ran against
    # an empty directory and failed on every build — after the ~40 minutes of
    # reference generation that produced the images had already succeeded.
    dataset = await db.get(ReferenceDataset, UUID(dataset_id))
    if dataset is None:
        raise RuntimeError(
            f"Reference dataset {dataset_id} does not exist, so there is "
            "nothing to train on. Re-run build_reference_dataset."
        )
    image_paths = list(dataset.image_keys or [])
    if not image_paths:
        raise RuntimeError(
            f"Reference dataset {dataset_id} records no images. Re-run "
            "build_reference_dataset before training."
        )

    result = await tr.train(
        dataset_id=dataset_id,
        model_type="lora",
        rank=16,
        epochs=10,
        image_paths=image_paths,
    )

    # Record the training attempt truthfully even when the trainer fails —
    # a missing GPU must not corrupt the identity pipeline, but it must not
    # be hidden either.
    job = TrainingJob(
        id=uuid4(),
        identity_id=UUID(identity_id),
        dataset_id=UUID(dataset_id),
        status=WorkflowStatus.COMPLETED if result.success else WorkflowStatus.FAILED,
        output_path=result.data.get("model_path", "") if result.success else "",
        metrics={
            "loss": result.data.get("final_loss", 0),
            "training_time": result.data.get("training_time_s", 0),
            "provider": result.provider,
            **({"error": result.error} if not result.success else {}),
        },
    )
    db.add(job)
    await db.flush()

    # A failed training does not stop the build. Two wrong answers were tried
    # before this one: returning normally was invisible — the step was marked
    # COMPLETED, the persona went ACTIVE with no model and a defaulted
    # consistency score, and the failure lived only in a table nobody opened.
    # Raising was loud but destroyed the build: one missing GPU or one OOM left
    # the persona stuck in BUILDING with no way to see or use anything.
    #
    # So: the persona stays usable and the failure is put on the record in three
    # places at once — a REVIEW QA row (visible as a QA verdict), a `warnings`
    # entry on the persona itself, and `training_failed` in this step's output
    # (which validate_identity reads to build the evaluator's state).
    if not result.success:
        warning = (
            "LoRA training failed: "
            f"{result.error or 'trainer reported failure with no error'}"
        )
        qa = QAResult(
            id=uuid4(),
            identity_id=UUID(identity_id),
            workflow_id=workflow_id,
            qa_type="training",
            status=QAStatus.REVIEW,
            score=0.0,
            threshold=0.0,
            details={
                "evaluation_status": "training_failed",
                "provider": result.provider,
                "model_type": "lora",
                "error": warning,
                "reference_images": len(image_paths),
                "base_model": result.data.get("base_model", ""),
                "note": (
                    "No adapter was produced. The persona is usable but its "
                    "identity is enforced by the locked avatar alone."
                ),
            },
            images_checked=0,
            passed_count=0,
            failed_count=0,
        )
        db.add(qa)

        persona = None
        if input_data.get("persona_id"):
            persona = await db.get(Persona, UUID(input_data["persona_id"]))
        if persona is not None:
            metadata = dict(persona.metadata_json or {})
            warnings = list(metadata.get("warnings") or [])
            warnings.append(
                {"step": "train_lora", "message": warning, "qa_id": str(qa.id)}
            )
            metadata["warnings"] = warnings
            persona.metadata_json = metadata

        await db.flush()
        logger.warning(
            "lora_training_failed_continuing",
            identity_id=identity_id,
            error=warning,
            qa_id=str(qa.id),
        )
        return {
            "model_path": "",
            "comfyui_lora_name": "",
            "loss": result.data.get("final_loss", 0),
            "training_time_s": result.data.get("training_time_s", 0),
            "provider": result.provider,
            "training_failed": True,
            "training_error": result.error or warning,
            "warnings": [warning],
            "qa_id": str(qa.id),
        }

    return {
        "model_path": result.data.get("model_path", ""),
        "comfyui_lora_name": result.data.get("comfyui_lora_name", ""),
        "loss": result.data.get("final_loss", 0),
        "training_time_s": result.data.get("training_time_s", 0),
        "provider": result.provider,
        "training_failed": False,
        "training_error": "",
        "warnings": [],
    }


def _step_order(key: str) -> int:
    """`step_2_output` → 2.

    Sorted numerically so a workflow with 10+ steps reads in execution order,
    not lexicographic order (where step_10 sorts before step_2 and the
    last-write-wins pick would take the wrong step's numbers).
    """
    try:
        return int(key.split("_")[1])
    except (IndexError, ValueError):
        return -1


def _lora_name_from_steps(input_data: dict) -> str:
    """The adapter name the training step reported, if any.

    Recorded on the identity in the form generation can use. ComfyUI's
    LoraLoader resolves `lora_name` against its own models/loras directory, so
    the relative filename the trainer published under is the value that works;
    the absolute path lives on the TrainingJob and the QA row. An absolute path
    here would load nothing while looking populated.
    """
    for key in sorted(
        (k for k in input_data
         if k.startswith("step_") and isinstance(input_data[k], dict)),
        key=_step_order,
    ):
        out = input_data[key]
        name = out.get("comfyui_lora_name") or out.get("model_path")
        if name:
            return name
    return ""


async def identity_qa_state_from_db(db: AsyncSession, persona, identity) -> dict:
    """Rebuild the QA state from stored rows, for an on-demand re-evaluation.

    It has to produce the same facts the build's step produced, or a re-check
    would be judging different evidence than the verdict it is re-checking — and
    the two scores on the record would stop being comparable.

    Where each fact comes from:
      - `reference_views_generated` → the newest ReferenceDataset's `total_images`
      - `reference_coverage`        → recomputed as generated / len(REFERENCE_VIEWS),
        because the dataset records how many images exist but not how many were
        asked for. This is the same arithmetic the build step does.
      - training outcome            → the newest TrainingJob's status, and its
        `metrics` JSON, which is where the loss, provider and error actually live
        (`TrainingJob` has no such columns).
    """
    dataset = (
        await db.execute(
            select(ReferenceDataset)
            .where(ReferenceDataset.identity_id == identity.id)
            .order_by(ReferenceDataset.created_at, ReferenceDataset.id)
        )
    ).scalars().all()
    dataset = dataset[-1] if dataset else None

    job = (
        await db.execute(
            select(TrainingJob)
            .where(TrainingJob.identity_id == identity.id)
            .order_by(TrainingJob.created_at, TrainingJob.id)
        )
    ).scalars().all()
    job = job[-1] if job else None

    total = dataset.total_images if dataset is not None else None
    coverage = (
        round(total / len(REFERENCE_VIEWS), 3)
        if total
        else None
    )
    metrics = dict(job.metrics or {}) if job is not None else {}
    failed = (job.status == WorkflowStatus.FAILED) if job is not None else None

    return {
        "persona_name": getattr(persona, "name", None),
        "appearance": getattr(persona, "appearance", None) or {},
        "personality": getattr(persona, "personality", None) or [],
        "reference_views_generated": total,
        "reference_coverage": coverage,
        "training_succeeded": None if failed is None else not failed,
        "training_loss": metrics.get("loss"),
        "training_provider": metrics.get("provider", ""),
        "training_error": metrics.get("error", ""),
    }


async def evaluate_identity(
    state: dict,
    identity_id: UUID,
    workflow_id: UUID | None,
    db: AsyncSession,
    lora_model_path: str = "",
    training_base: dict | None = None,
) -> dict:
    """Run the identity QA evaluator on `state`, record the verdict, apply it.

    Shared on purpose between the build's `validate_identity` step and the
    on-demand re-evaluation endpoint. It has to be one gate: if a hand-triggered
    re-check could judge by different rules than the build that first scored the
    identity, the two scores on the record would not mean the same thing, and
    neither would the threshold.

    `workflow_id` is None for a re-evaluation — the QA row's FK is nullable
    precisely so a verdict can exist without a workflow behind it, and inventing
    a workflow row to hold it would be a lie about what happened.

    The failure policy is the important part and lives here: a verdict promotes
    or demotes the identity, while an evaluator that cannot answer records
    REVIEW and leaves the identity exactly as it was.
    """
    content: dict | None = None
    evaluator = ""
    evaluation_error = ""

    # The evaluator is the local LLM (Ollama). It is handed the `state` built
    # above — the real reference and training facts — because an evaluator given
    # a placeholder prompt returns a verdict that means nothing. The schema puts
    # Ollama in JSON mode and turns off a thinking model's reasoning trace, which
    # otherwise eats the token budget before the JSON is reached.
    llm2 = _get_llm()
    try:
        result = await llm2.complete(
            system_prompt=(
                "You are the identity QA gate for a synthetic-character pipeline. "
                "Judge only from the state you are given. Approve only if the "
                "reference set is complete and training succeeded; a failed or "
                "erroring training run is never approved."
            ),
            user_prompt=(
                "Identity QA state:\n"
                f"{json.dumps(state, indent=2, default=str)}\n\n"
                "Answer with approved (boolean), and identity_score and quality_score, "
                f"each one of: {', '.join(CONSISTENCY_LEVELS)}."
            ),
            schema=IDENTITY_QA_SCHEMA,
        )
    except Exception as exc:
        # Broad on purpose, and this boundary is why: the step sits after the
        # ~2-hour reference-generation and training pipeline, so any unforeseen
        # failure here — a transport error the provider did not convert, an
        # unexpected shape — must degrade to REVIEW rather than abort a build
        # that has already succeeded at everything expensive.
        result = None
        evaluation_error = f"{type(exc).__name__}: {exc}"
    else:
        candidate = result.data.get("content", {}) if isinstance(result.data, dict) else {}
        verdict = _coerce_qa_verdict(candidate)
        if verdict is not None:
            content = {**verdict, "evaluator": "llm", "model_answer": candidate}
            evaluator = "llm"
        else:
            # Say *why* there is no verdict. A transport failure and an answer
            # that could not be read are both REVIEW, but they are different
            # problems and the QA row should distinguish them, not report blank.
            evaluation_error = result.error or (
                f"evaluator returned no usable verdict: {candidate!r}"
            )

    if content is None:
        logger.warning("identity_qa_unavailable", error=evaluation_error)

    # `approved` is the evaluator's own boolean verdict; the two scores are
    # recorded measurements, not extra gates. `threshold` is what the row is
    # compared against when a human reads a score back, and it no longer derives
    # from `approved` — the evaluator decides, not a cut on its score.
    threshold = 0.7

    if content is None:
        # No trustworthy verdict. Record REVIEW and leave the identity's own
        # status untouched — this branch used to mark the identity FAILED, so
        # an evaluator that merely declined to emit JSON destroyed a good
        # identity. "Could not evaluate" is not "is bad".
        qa = QAResult(
            id=uuid4(),
            identity_id=identity_id,
            workflow_id=workflow_id,
            qa_type="consistency",
            status=QAStatus.REVIEW,
            score=0.0,
            threshold=threshold,
            details={
                "evaluation_status": "blocked",
                "evaluator": evaluator or "none",
                "error": evaluation_error or "no evaluator returned a verdict",
                "state": state,
            },
            images_checked=int(state.get("reference_views_generated") or 0),
            passed_count=0,
            failed_count=0,
        )
        db.add(qa)
        await db.flush()
        return {
            "approved": False,
            "identity_score": 0.0,
            "qa_id": str(qa.id),
            "evaluation_status": "blocked",
            "evaluator": evaluator or "none",
        }

    qa = QAResult(
        id=uuid4(),
        identity_id=identity_id,
        workflow_id=workflow_id,
        qa_type="consistency",
        status=QAStatus.PASSED if content["approved"] else QAStatus.FAILED,
        score=float(content["identity_score"]),
        threshold=threshold,
        details=content,
        images_checked=int(content.get("images_checked", state.get("reference_views_generated") or 0)),
        passed_count=int(content.get("passed_count", 0)),
        failed_count=int(content.get("failed_count", 0)),
    )
    db.add(qa)
    await db.flush()

    # Update identity. REVIEW leaves the status alone by construction (it
    # returns above), so this only ever promotes or demotes on a real verdict.
    identity = await db.get(Identity, identity_id)
    if identity:
        identity.consistency_score = qa.score
        identity.status = (
            IdentityStatus.READY if qa.status == QAStatus.PASSED else IdentityStatus.FAILED
        )
        # Record the adapter in the form generation can actually use. ComfyUI's
        # LoraLoader resolves `lora_name` against its own models/loras directory,
        # so the relative filename the trainer published under is the value that
        # works; the absolute path lives on the TrainingJob and the QA row. An
        # absolute path here would load nothing while looking populated.
        if identity.lora_model_path == "" and lora_model_path:
            identity.lora_model_path = lora_model_path
        # Record the base the adapter was trained against. The trainer's own
        # guard compares *families*, and every SDXL finetune shares one, so a
        # LoRA trained on stock SDXL and later rendered on a photoreal finetune
        # passes that guard while the face stops holding. Keeping the file name
        # is what lets the render path tell, afterwards, whether the checkpoint
        # it generates on is the one the adapter is bound to. A JSON column only
        # registers on assignment, so the dict is rebound, not mutated; written
        # once, because the first successful train is what produced the adapter
        # and a later re-check must not rewrite its provenance.
        if training_base and not (identity.metadata_json or {}).get("lora_training_base"):
            identity.metadata_json = {
                **(identity.metadata_json or {}),
                "lora_training_base": training_base,
            }

    return {
        "approved": qa.status == QAStatus.PASSED,
        "identity_score": qa.score,
        "qa_id": str(qa.id),
    }



async def validate_identity_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Validate identity consistency after training."""
    # Find identity_id from any previous step output
    identity_id = input_data.get("identity_id")
    if not identity_id:
        for key in sorted(input_data.keys()):
            if key.startswith("step_") and isinstance(input_data[key], dict):
                if "identity_id" in input_data[key]:
                    identity_id = input_data[key]["identity_id"]
                    break
    if not identity_id:
        return {"error": "no identity_id"}

    # ── Gather the state the evaluator judges ────────────────────────
    # Drawn from what the previous steps actually produced. No credentials:
    # this text goes to the local model, and the rule that nothing secret
    # belongs in an evaluator's state holds regardless of where it runs.
    persona = None
    if input_data.get("persona_id"):
        persona = await db.get(Persona, UUID(input_data["persona_id"]))

    ds_total = ds_coverage = train_failed = train_loss = None
    train_error = train_provider = ""

    step_keys = sorted(
        (k for k in input_data if k.startswith("step_") and isinstance(input_data[k], dict)),
        key=_step_order,
    )
    for key in step_keys:
        out = input_data[key]
        if out.get("total_images") is not None:
            ds_total = out.get("total_images")
            ds_coverage = out.get("coverage_score")
        if "training_failed" in out:
            train_failed = out.get("training_failed")
            train_error = out.get("training_error") or ""
            train_loss = out.get("loss")
            train_provider = out.get("provider") or ""

    state = {
        "persona_name": getattr(persona, "name", None),
        "appearance": getattr(persona, "appearance", None) or {},
        "personality": getattr(persona, "personality", None) or [],
        "reference_views_generated": ds_total,
        "reference_coverage": ds_coverage,
        "training_succeeded": None if train_failed is None else not train_failed,
        "training_loss": train_loss,
        "training_provider": train_provider,
        "training_error": train_error,
    }

    return await evaluate_identity(
        state, UUID(identity_id), workflow_id, db,
        lora_model_path=_lora_name_from_steps(input_data),
        training_base=training_base_from_steps(input_data),
    )


async def create_voice_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Create voice profile for the identity."""
    identity_id = input_data.get("identity_id")
    if not identity_id:
        for key in sorted(input_data.keys()):
            if key.startswith("step_") and isinstance(input_data[key], dict):
                if "identity_id" in input_data[key]:
                    identity_id = input_data[key]["identity_id"]
                    break
    persona_name = input_data.get("persona_name", "model")
    voice_style = input_data.get("voice_style", "South African English")

    voc = _get_voice()
    result = await voc.create_voice(
        name=f"{persona_name}_voice",
        description=f"Voice profile for {persona_name}",
        accent=voice_style,
        tone="warm, confident",
    )

    if not result.success:
        return {
            "voice_id": "",
            "voice_key": "",
            "duration_seconds": 0,
            "status": "voice_unavailable",
            "voice_error": result.error,
        }

    # Test synthesis
    synth_result = await voc.synthesize(
        text=f"Hello, I'm {persona_name}. Welcome to my world.",
        voice_id=result.data.get("voice_id", ""),
    )

    if not synth_result.success:
        return {
            "voice_id": result.data.get("voice_id", ""),
            "voice_key": "",
            "duration_seconds": 0,
            "status": "voice_unavailable",
            "voice_error": synth_result.error,
        }

    return {
        "voice_id": result.data.get("voice_id", ""),
        "voice_key": synth_result.data.get("voice_key", ""),
        "duration_seconds": synth_result.data.get("duration_seconds", 0),
        "status": "ready",
    }


async def activate_persona_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Activate the persona and finalize its identity lock."""
    persona_id = input_data.get("persona_id")
    identity_id = input_data.get("identity_id")
    if not identity_id:
        for key in sorted(input_data.keys()):
            if key.startswith("step_") and isinstance(input_data[key], dict):
                if "identity_id" in input_data[key]:
                    identity_id = input_data[key]["identity_id"]
                    break

    persona = await db.get(Persona, UUID(persona_id))
    if not persona:
        return {"status": "persona_not_found", "persona_id": persona_id}

    if identity_id:
        identity = await db.get(Identity, UUID(identity_id))
        if identity and identity.status != IdentityStatus.READY:
            identity.status = IdentityStatus.READY
            await db.flush()

    # Create or refresh the durable identity lock for this persona.
    # This is the missing step that made new personas fail with
    # "No identity lock for persona" during Auto-Produce.
    lock = await ensure_identity_lock(persona, db, identity_id=identity_id)

    return {
        "status": "persona_activated",
        "persona_id": persona_id,
        "identity_lock_id": lock.persona_id,
        "identity_lock_status": lock.status,
    }
