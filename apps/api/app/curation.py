"""Persona Studio — deterministic curation of generated images.

This replaces a gate that could not fail. It asked the LLM a content-free
question ("QA check for generated content pack"), then took
`score = content.get("identity_score", 0.92)` — a default that PASSES — with
`failed_count=0` hardcoded and `images_checked` copied from the step's own
input rather than counted from anything. It never opened an image, and its
verdict was decided before it ran.

The gate here is deterministic and offline: resolution, focus (variance of the
Laplacian), exposure/clipping, and degenerate-contrast checks computed from the
pixels by numpy and PIL, both already installed. No new dependency, no network,
and the same file always produces the same verdict.

Two signals are deliberately *not* gates:

* The NSFW classifier. In a pipeline whose purpose is adult content, a
  "nsfw" label marks exactly the intended output, so gating on it would reject
  the product. Its score is recorded as a label and nothing more.
* The vision judge. It only runs when `CURATION_VISION_MODEL` names a model
  that answers, and it is advisory: it can add defects and force REVIEW, never
  override a technical failure. When it does not run, that is recorded as
  "not run" — never as a pass, which is the mistake the old gate made.
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# ── Thresholds ───────────────────────────────────────────────────────
# Deliberately permissive. The job is to catch broken output — a blank frame, a
# solid-colour image, a black or blown-out exposure, a mush of noise — not to
# apply a taste judgement, which no threshold can make.
MIN_EDGE = 512            # shorter side, px
MIN_LAPLACIAN_VARIANCE = 25.0   # below this the frame is out of focus or flat
MIN_STDDEV = 8.0          # below this the frame is essentially one colour
MAX_CLIP_HIGH_PCT = 12.0  # blown highlights
MAX_CLIP_LOW_PCT = 25.0   # crushed shadows
MIN_MEAN_LUMA = 12.0
MAX_MEAN_LUMA = 243.0


@dataclass
class ImageReport:
    """What was measured on one image, and what that means."""

    path: str
    readable: bool
    width: int = 0
    height: int = 0
    blur_variance: float = 0.0
    mean_luma: float = 0.0
    stddev: float = 0.0
    clipped_high_pct: float = 0.0
    clipped_low_pct: float = 0.0
    defects: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.readable and not self.defects

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "readable": self.readable,
            "width": self.width,
            "height": self.height,
            "blur_variance": round(self.blur_variance, 3),
            "mean_luma": round(self.mean_luma, 2),
            "stddev": round(self.stddev, 2),
            "clipped_high_pct": round(self.clipped_high_pct, 2),
            "clipped_low_pct": round(self.clipped_low_pct, 2),
            "defects": list(self.defects),
            "error": self.error,
        }


def _laplacian_variance(gray) -> float:
    """Focus measure: variance of the 4-neighbour Laplacian of the luma plane.

    A sharp image has a wide spread of second derivatives; a blurred or flat one
    has almost none. Implemented directly with numpy slicing rather than through
    a filter library, so the only dependencies are numpy and PIL — both already
    required by the trainer, and no new package enters the pipeline for this.
    """
    import numpy as np

    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    lap = (
        -4.0 * gray[1:-1, 1:-1]
        + gray[:-2, 1:-1]
        + gray[2:, 1:-1]
        + gray[1:-1, :-2]
        + gray[1:-1, 2:]
    )
    return float(lap.var())


def inspect_image(source: str | Path | bytes) -> ImageReport:
    """Measure one image and list its technical defects. Never raises."""
    import numpy as np
    from PIL import Image, UnidentifiedImageError

    label = source if isinstance(source, (str, Path)) else "<bytes>"
    try:
        if isinstance(source, (str, Path)):
            handle = Path(source)
            if not handle.exists():
                return ImageReport(path=str(label), readable=False,
                                   error="file not found")
            img = Image.open(handle)
        else:
            img = Image.open(io.BytesIO(source))
        img = img.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        return ImageReport(path=str(label), readable=False,
                           error=f"{type(exc).__name__}: {exc}")

    width, height = img.size
    gray = np.asarray(img.convert("L"), dtype=np.float32)

    report = ImageReport(
        path=str(label),
        readable=True,
        width=width,
        height=height,
        blur_variance=_laplacian_variance(gray),
        mean_luma=float(gray.mean()),
        stddev=float(gray.std()),
        clipped_high_pct=float((gray >= 254).mean() * 100),
        clipped_low_pct=float((gray <= 1).mean() * 100),
    )

    # A file that opens but decodes to nothing usable is a failure, not a pass:
    # PIL will happily open a truncated PNG and hand back a black frame.
    if min(width, height) < MIN_EDGE:
        report.defects.append(
            f"resolution {width}x{height} is below the {MIN_EDGE}px minimum side"
        )
    if report.stddev < MIN_STDDEV:
        report.defects.append(
            f"image is essentially flat (luma stddev {report.stddev:.1f})"
        )
    if report.blur_variance < MIN_LAPLACIAN_VARIANCE:
        report.defects.append(
            f"no detectable detail (laplacian variance "
            f"{report.blur_variance:.1f} < {MIN_LAPLACIAN_VARIANCE})"
        )
    if report.clipped_high_pct > MAX_CLIP_HIGH_PCT:
        report.defects.append(
            f"{report.clipped_high_pct:.1f}% of pixels are blown out"
        )
    if report.clipped_low_pct > MAX_CLIP_LOW_PCT:
        report.defects.append(
            f"{report.clipped_low_pct:.1f}% of pixels are crushed to black"
        )
    if report.mean_luma < MIN_MEAN_LUMA:
        report.defects.append(f"badly underexposed (mean luma {report.mean_luma:.1f})")
    if report.mean_luma > MAX_MEAN_LUMA:
        report.defects.append(f"badly overexposed (mean luma {report.mean_luma:.1f})")

    return report


async def judge_with_vision(
    images: list[str | Path],
    model: str,
    base_url: str = "",
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Ask a local vision model to report defects. Advisory, never a gate.

    Returns a dict with `ran` set honestly. Every failure path — no model
    configured, Ollama unreachable, model not pulled, an unparseable answer —
    returns `ran: False` with the reason. It never returns "clean", because a
    judge that did not run cannot certify anything; that conflation is what made
    the old quality check vacuous.
    """
    import httpx

    if not model:
        return {"ran": False, "reason": "no CURATION_VISION_MODEL configured"}

    payload_images: list[str] = []
    for path in images:
        handle = Path(path)
        if not handle.exists():
            continue
        payload_images.append(base64.b64encode(handle.read_bytes()).decode())
    if not payload_images:
        return {"ran": False, "reason": "no readable images to judge"}

    base = base_url or "http://localhost:11434"
    prompt = (
        "You are a technical defect checker for generated images, not a "
        "taste judge. For each image report only concrete generation artifacts: "
        "deformed or extra limbs, malformed hands or faces, duplicated body "
        "parts, melted or smeared texture, nonsensical background geometry, "
        "text-like gibberish. Ignore styling, attractiveness, clothing and "
        "subject matter entirely. Reply with JSON: "
        '{"images": [{"index": 0, "defects": ["..."]}], "verdict": "clean"|"defects"}'
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base.rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt,
                                  "images": payload_images}],
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.0},
                },
            )
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "")
    except Exception as exc:
        return {"ran": False, "reason": f"{type(exc).__name__}: {exc}", "model": model}

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {"ran": False, "reason": "vision model returned non-JSON",
                "model": model, "raw": content[:400]}

    defects = []
    for entry in parsed.get("images") or []:
        index = entry.get("index")
        for defect in entry.get("defects") or []:
            defects.append({"index": index, "defect": str(defect)})

    return {
        "ran": True,
        "model": model,
        "images_judged": len(payload_images),
        "defects": defects,
        "verdict": parsed.get("verdict", ""),
    }


async def curate_images(
    image_paths: list[str | Path],
    vision_model: str = "",
    ollama_url: str = "",
) -> dict[str, Any]:
    """Run the full gate over a set of images.

    `status` is REVIEW — not PASSED — when the technical checks are clean but the
    vision judge did not run. There is a difference between "verified clean" and
    "nothing found by the checks that ran", and the row has to say which.
    """
    reports = [inspect_image(path) for path in image_paths]
    technical_failures = [r for r in reports if not r.passed]

    judge = await judge_with_vision(image_paths, vision_model, ollama_url)
    judge_defects = judge.get("defects") or [] if judge.get("ran") else []

    if technical_failures:
        status = "failed"
    elif judge_defects:
        status = "review"
    elif judge.get("ran"):
        status = "passed"
    else:
        status = "review"

    reasons = []
    if technical_failures:
        reasons.append(
            f"{len(technical_failures)} of {len(reports)} image(s) failed the "
            "technical checks"
        )
    if judge_defects:
        reasons.append(f"the vision judge reported {len(judge_defects)} defect(s)")
    if not judge.get("ran"):
        reasons.append(f"vision judge did not run: {judge.get('reason')}")

    return {
        "status": status,
        "score": round(
            (len(reports) - len(technical_failures)) / len(reports), 4
        ) if reports else 0.0,
        "images_checked": len(reports),
        "passed_count": len(reports) - len(technical_failures),
        "failed_count": len(technical_failures),
        "reports": [r.to_dict() for r in reports],
        "vision_judge": judge,
        "reasons": reasons,
    }
