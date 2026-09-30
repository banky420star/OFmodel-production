"""Was the identity adapter trained on the checkpoint that will render it?

A LoRA is bound to the weights it was trained against. Two SDXL checkpoints are
the *same family* and different weights: the trainer's guard compares families
(`hf_trainer.assert_matches_image_checkpoint`), so a LoRA trained on stock
`sd_xl_base_1.0` and later rendered on a photoreal finetune such as
`RealVisXL_V4.0` passes that guard and applies nothing that holds. Measured
2026-09-30 on Naomi: with the adapter at strength 1.0 the rendered face was the
same as with no adapter at all, against a prompt that named blue eyes and got
green ones.

The guard is right about what it can see at *train* time. This module is what the
render path needs afterwards, where the only record of the training base is what
was written down when the adapter was made. It answers in three states, and the
third is the one that matters: `unknown` is not a pass. A build that predates the
record cannot be shown to match, and saying so is the honest answer rather than
inferring safety from a family that was always going to agree.

Torch-free on purpose: this is imported by `models.py`, which the whole app loads,
while `hf_trainer` imports torch lazily inside its functions. `model_family` is
borrowed from there because importing it is cheap (0.08 s, no torch) and a second
copy of the rule would be free to drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Any

MATCH = "match"
MISMATCH = "mismatch"
UNKNOWN = "unknown"

# Values that name no file. `lora_base_state` holds one of the three verdicts
# above, and a serializer or a caller can hand that verdict back where a
# checkpoint name is expected — so a bare verdict must not normalize into a
# filename that could then be compared against a real one. Passing `"match"`
# as a base is a caller bug, and the honest answer to it is `unknown`.
_NO_FILE = frozenset({"", "unknown", "none", "null", "n/a", "match", "mismatch"})


def _basename(value: str) -> str:
    """The filename part, whichever slash a path uses."""
    return PurePosixPath((value or "").replace("\\", "/")).name


def _as_checkpoint_name(value: Any) -> str:
    """A checkpoint filename, or "" when the value names no file.

    `bool` is rejected rather than stringified. `COMFYUI_ADULT_CHECKPOINT` is a
    capability flag, not a path, and `PurePosixPath(str(True)).name` is the
    filename `True` — a live checkpoint that matches nothing. That reported a
    mismatch against a real adapter, i.e. it refused every render on exactly the
    path where the flag gets turned on.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, str):
        return ""
    name = _basename(value)
    if name.casefold() in _NO_FILE:
        return ""
    return name


def _step_sort_key(key: str) -> tuple[int, str]:
    """Order `step_2` before `step_10` — the numeric part, not the text."""
    digits = "".join(ch for ch in key if ch.isdigit())
    return (int(digits) if digits else 10**9, key)


def training_base_from_steps(input_data: dict) -> dict:
    """The base a build trained its adapter against, as its training step reported it.

    Mirrors `_lora_name_from_steps` in the persona flow: a step's output dict
    lands under a `step_N` key, and only the training step knows the base. The
    first step that carries any of it wins, in numeric step order.
    """
    for key in sorted(
        (k for k in input_data if k.startswith("step_") and isinstance(input_data[k], dict)),
        key=_step_sort_key,
    ):
        out = input_data[key]
        checkpoint = out.get("training_checkpoint") or ""
        source = out.get("training_source") or ""
        base_model = out.get("base_model") or ""
        if not (checkpoint or source or base_model):
            continue
        # `training_checkpoint` is already a bare filename, the same shape as
        # `COMFYUI_CHECKPOINT`. `training_source` is the absolute path, or an
        # `hf:<repo>` marker when no local file was used — and a repo marker names
        # no file, so it must not be mistaken for one.
        if not checkpoint and source and not source.startswith("hf:"):
            checkpoint = _basename(source)
        from app.providers.hf_trainer import model_family

        return {
            "checkpoint": _basename(checkpoint),
            "base_model": base_model,
            "source": source,
            "family": out.get("lora_family") or out.get("family")
            or model_family(base_model or source),
        }
    return {}


def base_mismatch(recorded: Any, render_checkpoint: Any) -> dict:
    """Whether an adapter's recorded training base matches the render checkpoint.

    Returns `{"state": match|mismatch|unknown, "reason": <plain language>}`.
    `mismatch` is a real finding; `unknown` means it could not be checked — never
    that it is fine.

    `recorded` is the free-form `lora_training_base` JSON off the identity, so it
    is whatever was written there: a dict from `training_base_from_steps`, or
    nothing at all. Anything that is not a dict is *not recorded* rather than an
    error — a caller does not get to crash the persona summary because one row
    holds a bare string where a record belongs.
    """
    render = _as_checkpoint_name(render_checkpoint)

    if not isinstance(recorded, dict) or not (
        recorded.get("checkpoint") or recorded.get("base_model") or recorded.get("family")
    ):
        return {
            "state": UNKNOWN,
            "reason": (
                "the base this adapter was trained on was never recorded on the "
                "identity (the field is written from 2026-09-30 onward), so it "
                f"cannot be checked against the render checkpoint "
                f"{render or '(none configured)'}"
            ),
        }

    if not render:
        return {
            "state": UNKNOWN,
            "reason": "no render checkpoint is configured, so there is nothing to compare against",
        }

    # Both sides are reduced to a filename: a caller may hand over a full path on
    # either side, and a path difference is not a different checkpoint.
    trained = _as_checkpoint_name(recorded.get("checkpoint"))
    if trained:
        if trained.casefold() == render.casefold():
            return {"state": MATCH, "reason": f"trained and rendered on {render}"}
        return {
            "state": MISMATCH,
            "reason": (
                f"trained on {trained} but the image path renders on {render}. An "
                "adapter is bound to the weights it was trained against, so the "
                "face will not hold across this swap — retrain against the render "
                "checkpoint, or render on the checkpoint the adapter was trained on"
            ),
        }

    # Only a family was recorded — from a build that used an HF repo rather than
    # a local file. The family agreeing is not evidence of a match: every SDXL
    # finetune shares the family, which is exactly how this went unnoticed.
    from app.providers.hf_trainer import model_family

    trained_family = recorded.get("family") or model_family(recorded.get("base_model", ""))
    render_family = model_family(render)
    if trained_family and render_family and trained_family != render_family:
        return {
            "state": MISMATCH,
            "reason": (
                f"trained for family {trained_family} but rendering on a "
                f"{render_family} checkpoint ({render})"
            ),
        }
    return {
        "state": UNKNOWN,
        "reason": (
            f"the record names only the family ({trained_family or 'unknown'}), not "
            f"the file — a different {render_family or 'same-family'} finetune than "
            f"{render} cannot be ruled out"
        ),
    }


# ── enforcement: the render path ────────────────────────────────────────
#
# `base_mismatch` above answers the question. Nothing made anyone ask it:
# `lora_base_state` was computed for the persona summary and surfaced by the
# API while every generation path rendered straight past it, so a swapped base
# was reported after the sale instead of refused before it. What follows is the
# part that refuses.


class PathPolicy(str, Enum):
    """What an unverifiable identity costs on this path.

    Adult sells the face itself — a stranger rendered under her name is the
    product failing — so the honest-but-unverifiable case fails closed. Plate
    content (lifestyle, preview) is not sold as her in the same way, so an
    unrecorded base warns and continues rather than blocking a working persona.
    """

    ADULT = "adult"
    PLATE = "plate"


@dataclass(frozen=True)
class GateDecision:
    state: str
    allowed: bool
    reason: str
    recorded_base: str | None = None
    render_checkpoint: str | None = None
    warn: bool = False


class GateError(Exception):
    """The render must not proceed. Carries the decision so callers can report it."""

    def __init__(self, decision: GateDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


def enforce_before_render(
    recorded: dict | None,
    render_checkpoint: Any,
    *,
    path: PathPolicy = PathPolicy.ADULT,
) -> GateDecision:
    """The identity check a render has to pass. Call it *before* the provider runs.

    match     — render
    mismatch  — refuse, on both paths: the adapter does not bind to these weights
    unknown   — adult: refuse (fail closed); plate: warn and continue

    There is deliberately no fallback to the bare checkpoint. Rendering without
    the adapter produces a stranger, which is the failure `chat_engine` refuses
    to commit when it raises `LLMUnavailable` instead of returning a canned
    line — substituting something that looks like her is worse than failing.
    """
    verdict = base_mismatch(recorded, render_checkpoint)
    state = verdict["state"]
    recorded_name = _as_checkpoint_name((recorded or {}).get("checkpoint")) or None
    render_name = _as_checkpoint_name(render_checkpoint) or None
    decision = GateDecision(state, True, verdict["reason"], recorded_name, render_name)

    if state == MATCH:
        return decision

    if state == MISMATCH:
        raise GateError(GateDecision(state, False, verdict["reason"], recorded_name, render_name))

    if path is PathPolicy.ADULT:
        raise GateError(GateDecision(state, False, verdict["reason"], recorded_name, render_name))

    return GateDecision(
        state,
        True,
        verdict["reason"] + " (plate path: warn and continue)",
        recorded_name,
        render_name,
        warn=True,
    )
