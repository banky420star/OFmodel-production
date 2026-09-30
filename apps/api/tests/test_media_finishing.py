"""Finishing pass — geometry, grain, and the refusals that keep it honest.

The point of most of these is not that a resize returns the right size (it
must) but that it *reports what it removed*. A crop that silently eats the
subject's arms produces a published post nobody checked, so the numbers in the
report and the refusal threshold are the behaviour worth pinning down.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from app.media.finishing import (
    DEFAULT_MAX_CROP_FRACTION,
    PLATFORM_ASPECTS,
    FinishError,
    FinishReport,
    add_grain,
    describe_plan,
    finish,
    resize_to,
    upscale,
)


def _flat(width: int, height: int, value: int = 128) -> Image.Image:
    return Image.new("RGB", (width, height), (value, value, value))


def _gradient(width: int, height: int) -> Image.Image:
    """Left half black, right half white — lets a crop be seen, not inferred."""
    a = np.zeros((height, width, 3), dtype=np.uint8)
    a[:, width // 2 :] = 255
    return Image.fromarray(a)


def _png_bytes(image: Image.Image) -> bytes:
    """Encoded bytes, which is what `finish()` accepts for an in-memory source.

    Not `Image.tobytes()` — that is the raw pixel buffer with no header, and
    Pillow cannot open it.
    """
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ── grain ────────────────────────────────────────────────────────────────


def test_zero_grain_leaves_the_image_untouched():
    """0 must be a true no-op, not a noiseless round-trip through numpy."""
    source = _flat(64, 64)
    out = add_grain(source, 0.0)
    assert out is source


def test_grain_changes_pixels_and_is_seeded():
    source = _gradient(128, 128)
    once = np.asarray(add_grain(source, 6.0, seed=7))
    twice = np.asarray(add_grain(source, 6.0, seed=7))
    other = np.asarray(add_grain(source, 6.0, seed=8))

    assert not np.array_equal(np.asarray(source), once)
    assert np.array_equal(once, twice), "same seed must reproduce the same grain"
    assert not np.array_equal(once, other), "a different seed must differ"


def test_grain_is_heavier_in_the_shadows_than_the_highlights():
    """The distribution is the whole reason this is not `+ uniform noise`."""
    source = _gradient(256, 256)
    out = np.asarray(add_grain(source, 8.0, seed=1)).astype(np.float32)
    original = np.asarray(source).astype(np.float32)
    delta = np.abs(out - original).mean(axis=2)

    dark = delta[:, :110].std()
    bright = delta[:, 146:].std()
    assert dark > bright, f"shadow noise {dark:.2f} should exceed highlight noise {bright:.2f}"


def test_grain_stays_in_range():
    out = np.asarray(add_grain(_flat(64, 64, 0), 40.0, seed=3))
    assert out.min() >= 0 and out.max() <= 255
    assert out.dtype == np.uint8


# ── geometry ─────────────────────────────────────────────────────────────


def test_cover_hits_the_target_exactly_and_reports_the_crop():
    image, info = resize_to(_gradient(1080, 1350), 1080, 1080, fit="cover")
    assert image.size == (1080, 1080)
    assert info["cropped_h_fraction"] == pytest.approx(0.2, abs=1e-6)
    assert info["cropped_w_fraction"] == 0.0


def test_cover_refuses_a_crop_that_would_remove_too_much():
    """1:1 -> 9:16 cuts 43.75% of the width, which is past the limit."""
    with pytest.raises(FinishError) as exc:
        resize_to(_gradient(1024, 1024), 1080, 1920, fit="cover")
    message = str(exc.value)
    assert "44%" in message or "43%" in message
    assert "allow_heavy_crop=True" in message


def test_a_heavy_crop_is_allowed_when_the_operator_says_so():
    image, info = resize_to(
        _gradient(1024, 1024), 1080, 1920, fit="cover", allow_heavy_crop=True
    )
    assert image.size == (1080, 1920)
    assert info["cropped_w_fraction"] > DEFAULT_MAX_CROP_FRACTION


def test_contain_pads_instead_of_cropping():
    image, info = resize_to(_gradient(1024, 1024), 1080, 1920, fit="contain")
    assert image.size == (1080, 1920)
    assert info["cropped_w_fraction"] == 0.0
    assert info["cropped_h_fraction"] == 0.0
    assert info["padded"] is True


def test_a_letterboxed_image_keeps_the_source_clear_of_the_bars():
    """Padding is black; the source must land in the middle, unwarped."""
    image, _ = resize_to(_flat(1024, 1024, 200), 1080, 1920, fit="contain")
    a = np.asarray(image)
    assert tuple(a[10, 540]) == (0, 0, 0), "top bar should be padding"
    assert tuple(a[960, 540]) == (200, 200, 200), "centre should be the source"


def test_stretch_is_exact_and_says_it_distorted():
    image, info = resize_to(_flat(1000, 500), 500, 500, fit="stretch")
    assert image.size == (500, 500)
    assert info["scale"] == (0.5, 1.0)


def test_an_unknown_fit_mode_is_refused_rather_than_defaulted():
    with pytest.raises(FinishError):
        resize_to(_flat(64, 64), 32, 32, fit="squish")


def test_a_non_positive_target_is_refused():
    with pytest.raises(FinishError):
        resize_to(_flat(64, 64), 0, 64)


# ── upscale ──────────────────────────────────────────────────────────────


def test_upscale_below_one_is_a_no_op():
    source = _flat(64, 64)
    assert upscale(source, 1.0) is source
    assert upscale(source, 0.5) is source


def test_upscale_scales_both_axes():
    assert upscale(_flat(100, 50), 2.0).size == (200, 100)


def test_upscale_adds_pixels_but_never_claims_detail():
    """The report must name the method — lanczos, not "enhanced"."""
    report = finish(_png_bytes(_flat(64, 64)), upscale_factor=2.0)
    assert report.ok
    assert report.width == 128
    assert any(step.startswith("upscale:lanczos") for step in report.steps)


# ── the pass ─────────────────────────────────────────────────────────────


def test_finish_runs_the_steps_in_order(tmp_path):
    """resize -> upscale -> grain -> encode. Grain before resize is lost."""
    out = tmp_path / "plate.png"
    report = finish(
        _gradient(1080, 1350),
        aspect="fanvue_feed",
        upscale_factor=2.0,
        grain=6.0,
        out_path=out,
    )
    assert report.ok, report.error
    assert report.width == 2160 and report.height == 2700
    assert out.exists() and out.stat().st_size == report.bytes_written

    kinds = [step.split(":")[0] for step in report.steps]
    assert kinds == ["resize", "upscale", "grain", "encode"]


def test_finish_reports_the_source_and_the_target_size():
    report = finish(_png_bytes(_flat(1080, 1350)), aspect="fanvue_feed")
    assert (report.source_width, report.source_height) == (1080, 1350)
    assert (report.width, report.height) == (1080, 1350)
    assert report.cropped_height_fraction == pytest.approx(0.0)


def test_finish_surfaces_a_refused_crop_as_a_failure_not_a_traceback():
    report = finish(_png_bytes(_flat(1024, 1024)), aspect="story_9x16")
    assert report.ok is False
    assert "allow_heavy_crop=True" in report.error
    assert report.steps == [], "a refused pass must not report steps it did not run"


def test_finish_takes_the_heavy_crop_when_told():
    report = finish(_png_bytes(_flat(1024, 1024)), aspect="story_9x16", allow_heavy_crop=True)
    assert report.ok, report.error
    assert report.cropped_width_fraction > DEFAULT_MAX_CROP_FRACTION


def test_passing_both_an_aspect_and_a_size_is_refused():
    """Two ways to say the same thing means one silently wins. Refuse."""
    report = finish(_png_bytes(_flat(64, 64)), aspect="square", width=100, height=100)
    assert report.ok is False
    assert "not both" in report.error


def test_an_unknown_aspect_names_the_ones_that_exist():
    report = finish(_png_bytes(_flat(64, 64)), aspect="tiktok_vertical")
    assert report.ok is False
    assert "unknown aspect" in report.error
    assert "story_9x16" in report.error


def test_undecodable_source_bytes_fail_cleanly():
    report = finish(b"not an image at all")
    assert report.ok is False
    assert report.error


def test_jpeg_output_is_actually_jpeg():
    report = finish(_png_bytes(_flat(64, 64)), width=64, height=64, out_format="JPEG")
    assert report.ok
    assert report.format == "JPEG"
    payload = io.BytesIO()
    Image.new("RGB", (8, 8)).save(payload, format="JPEG")
    assert report.bytes_written > 0


def test_a_finished_report_never_implies_the_render_improved():
    report = finish(_png_bytes(_flat(64, 64)), grain=6.0)
    assert "grain" in report.cosmetic_only
    assert "post-process" in report.note


def test_finishing_a_file_path_overwrites_in_place_by_default(tmp_path):
    source = tmp_path / "plate.png"
    _gradient(1080, 1350).save(source)
    report = finish(source, aspect="square", fit="contain")
    assert report.ok, report.error
    assert report.out_path == str(source)
    with Image.open(source) as im:
        assert im.size == (1080, 1080)


# ── plan preview ─────────────────────────────────────────────────────────


def test_describe_plan_returns_the_numbers_without_touching_pixels():
    plan = describe_plan(aspect="fanvue_feed")
    assert plan["ok"] is True
    assert (plan["width"], plan["height"]) == PLATFORM_ASPECTS["fanvue_feed"][:2]
    assert plan["max_crop_fraction"] == DEFAULT_MAX_CROP_FRACTION


def test_describe_plan_refuses_the_same_ambiguous_input_finish_does():
    assert describe_plan(aspect="square", width=10)["ok"] is False
    assert describe_plan(aspect="nope")["ok"] is False
    assert describe_plan()["ok"] is False


def test_every_platform_aspect_is_a_positive_real_size():
    for name, (w, h, note) in PLATFORM_ASPECTS.items():
        assert w > 0 and h > 0, name
        assert note, f"{name} must explain what the surface is"
