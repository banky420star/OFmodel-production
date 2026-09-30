"""Persona Studio — the content gate must be able to fail.

The handler this replaces could not: it asked the LLM a content-free question,
took `content.get("identity_score", 0.92)` — a default that PASSES — hardcoded
`failed_count=0`, and copied `images_checked` from the step's own input. Every
test below asserts a verdict the old code was structurally incapable of
producing.
"""

from __future__ import annotations

import uuid

import pytest
from PIL import Image, ImageDraw, ImageFilter

from app.curation import (
    MIN_LAPLACIAN_VARIANCE,
    inspect_image,
    curate_images,
)


def _save(img: Image.Image, path) -> str:
    img.save(path)
    return str(path)


def _detailed(size=(768, 768)) -> Image.Image:
    """An image with structure: enough edges that a focus measure is meaningful."""
    img = Image.new("RGB", size, (90, 110, 130))
    draw = ImageDraw.Draw(img)
    for i in range(0, size[0], 24):
        shade = 40 + (i * 7) % 180
        draw.line([(i, 0), (i, size[1])], fill=(shade, shade, shade), width=9)
        draw.line([(0, i), (size[0], i)], fill=(shade // 2, shade, 255 - shade), width=5)
    draw.ellipse([size[0] // 4, size[1] // 4, size[0] // 2, size[1] // 2], fill=(200, 60, 60))
    return img


# ── the checks actually measure something ────────────────────────────

def test_a_structured_image_passes(tmp_path):
    report = inspect_image(_save(_detailed(), tmp_path / "good.png"))
    assert report.readable
    assert report.passed, report.defects
    assert report.width == 768


def test_a_solid_colour_frame_is_a_defect(tmp_path):
    """A generation that collapsed to one colour is not a photo. The old gate
    would have scored it 0.92 and passed it."""
    flat = Image.new("RGB", (768, 768), (128, 128, 128))
    report = inspect_image(_save(flat, tmp_path / "flat.png"))
    assert not report.passed
    assert any("flat" in d or "detail" in d for d in report.defects), report.defects


def test_a_black_frame_is_a_defect(tmp_path):
    """A truncated decode is a black frame, not a missing file."""
    black = Image.new("RGB", (768, 768), (0, 0, 0))
    report = inspect_image(_save(black, tmp_path / "black.png"))
    assert not report.passed
    assert any("black" in d or "underexposed" in d or "flat" in d for d in report.defects)


def test_a_white_frame_is_a_defect(tmp_path):
    white = Image.new("RGB", (768, 768), (255, 255, 255))
    report = inspect_image(_save(white, tmp_path / "white.png"))
    assert not report.passed


def test_a_blurred_frame_is_a_defect(tmp_path):
    blurred = _detailed().filter(ImageFilter.GaussianBlur(radius=24))
    report = inspect_image(_save(blurred, tmp_path / "blurred.png"))
    assert report.blur_variance < MIN_LAPLACIAN_VARIANCE
    assert not report.passed
    assert any("detail" in d for d in report.defects), report.defects


def test_a_too_small_image_is_a_defect(tmp_path):
    small = _detailed(size=(128, 128))
    report = inspect_image(_save(small, tmp_path / "small.png"))
    assert not report.passed
    assert any("resolution" in d for d in report.defects), report.defects


def test_a_missing_file_is_a_defect_not_an_exception(tmp_path):
    report = inspect_image(tmp_path / "absent.png")
    assert not report.readable
    assert not report.passed
    assert "not found" in report.error


def test_a_non_image_is_a_defect_not_an_exception(tmp_path):
    bad = tmp_path / "not-an-image.png"
    bad.write_bytes(b"this is not a PNG")
    report = inspect_image(bad)
    assert not report.readable
    assert not report.passed
    assert report.error


# ── aggregate verdict ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_clean_with_no_judge_is_review_not_passed(tmp_path):
    """The central honesty rule: a judge that did not run cannot certify
    anything. "Nothing found by the checks that ran" is REVIEW."""
    good = _save(_detailed(), tmp_path / "a.png")
    result = await curate_images([good], vision_model="")

    assert result["status"] == "review"
    assert result["images_checked"] == 1
    assert result["passed_count"] == 1
    assert result["vision_judge"]["ran"] is False
    assert any("did not run" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_a_bad_image_fails_the_gate(tmp_path):
    good = _save(_detailed(), tmp_path / "a.png")
    flat = _save(Image.new("RGB", (768, 768), (128, 128, 128)), tmp_path / "b.png")
    result = await curate_images([good, flat], vision_model="")

    assert result["status"] == "failed"
    assert result["failed_count"] == 1
    assert result["passed_count"] == 1
    assert result["score"] == 0.5


@pytest.mark.asyncio
async def test_no_images_at_all_is_review(tmp_path):
    result = await curate_images([], vision_model="")
    assert result["status"] == "review"
    assert result["images_checked"] == 0


@pytest.mark.asyncio
async def test_unreachable_vision_model_degrades_to_not_run(tmp_path):
    """An unreachable judge must not be reported as a clean pass."""
    good = _save(_detailed(), tmp_path / "a.png")
    result = await curate_images(
        [good], vision_model="gemma3:4b", ollama_url="http://127.0.0.1:1"
    )
    assert result["status"] == "review"
    assert result["vision_judge"]["ran"] is False
    assert result["vision_judge"]["reason"]


# ── the handler ──────────────────────────────────────────────────────

async def _seed_generated_images(db, workflow_id, paths: list[str]):
    from app.models import GeneratedImage

    for i, path in enumerate(paths):
        db.add(GeneratedImage(
            id=uuid.uuid4(),
            workflow_id=workflow_id,
            prompt="p",
            image_key=f"key_{i}.png",
            seed=i,
            metadata_json={"shot_index": i, "local_path": path},
        ))
    await db.flush()


@pytest.mark.asyncio
async def test_handler_measures_the_images_it_generated(db, tmp_path, monkeypatch):
    from app.workflows.content_flow import quality_check_handler
    from app.models import QAResult, QAStatus

    workflow_id = uuid.uuid4()
    paths = [
        _save(_detailed(), tmp_path / "a.png"),
        _save(Image.new("RGB", (768, 768), (128, 128, 128)), tmp_path / "b.png"),
    ]
    await _seed_generated_images(db, workflow_id, paths)

    import app.curation as curation

    async def _no_judge(images, model, base_url=""):
        return {"ran": False, "reason": "disabled for test"}

    monkeypatch.setattr(curation, "judge_with_vision", _no_judge)

    result = await quality_check_handler(
        workflow_id=workflow_id, step_id=uuid.uuid4(),
        input_data={"image_count": 2}, db=db,
    )

    assert result["approved"] is False
    assert result["images_measured"] == 2, "it must open the images, not trust a count"
    assert result["score"] == 0.5

    from sqlalchemy import select

    qa = (await db.execute(
        select(QAResult).where(QAResult.workflow_id == workflow_id)
    )).scalars().one()
    assert qa.status == QAStatus.FAILED
    assert qa.failed_count == 1
    assert qa.images_checked == 2
    assert qa.details["checks"], "the measurements must be on the row"


@pytest.mark.asyncio
async def test_handler_reports_images_it_could_not_measure(db, tmp_path, monkeypatch):
    """An image whose bytes were never persisted is unverified, not a pass."""
    from app.workflows.content_flow import quality_check_handler
    from app.models import QAResult, QAStatus

    workflow_id = uuid.uuid4()
    good = _save(_detailed(), tmp_path / "a.png")
    await _seed_generated_images(db, workflow_id, [good, ""])

    import app.curation as curation

    async def _no_judge(images, model, base_url=""):
        return {"ran": False, "reason": "disabled for test"}

    monkeypatch.setattr(curation, "judge_with_vision", _no_judge)

    result = await quality_check_handler(
        workflow_id=workflow_id, step_id=uuid.uuid4(),
        input_data={"image_count": 2}, db=db,
    )

    assert result["images_unverified"] == 1
    assert result["warnings"], "the unmeasured image must be surfaced"
    assert result["status"] == "review", "an unmeasured image cannot be a clean pass"

    from sqlalchemy import select

    qa = (await db.execute(
        select(QAResult).where(QAResult.workflow_id == workflow_id)
    )).scalars().one()
    assert qa.status == QAStatus.REVIEW
    assert qa.details["unverified_images"][0]["image_key"] == "key_1.png"


@pytest.mark.asyncio
async def test_handler_with_no_images_does_not_claim_success(db, monkeypatch):
    from app.workflows.content_flow import quality_check_handler
    from sqlalchemy import select
    from app.models import QAResult, QAStatus

    workflow_id = uuid.uuid4()
    result = await quality_check_handler(
        workflow_id=workflow_id, step_id=uuid.uuid4(),
        input_data={"image_count": 0}, db=db,
    )

    assert result["approved"] is False
    assert result["status"] == "review"

    qa = (await db.execute(
        select(QAResult).where(QAResult.workflow_id == workflow_id)
    )).scalars().one()
    assert qa.status == QAStatus.REVIEW
