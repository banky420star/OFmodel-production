"""Persona Studio — an existing avatar file is not the same as a usable face.

`build_reference_dataset` accepted any file at `storage/avatars/<name>.jpg` as
the identity reference, testing only `path.exists()`. That directory holds 128
placeholders from e2e runs: valid 8x8 PNGs with a .jpg name, so they exist *and*
decode. The identity engine upscales the avatar to the generation size, so an
8x8 placeholder becomes a 1024px blur and every reference image for the build is
edited toward that blur — a whole build, and the adapter trained on it, anchored
to a grey square.
"""

from __future__ import annotations

import uuid

from PIL import Image

from app.workflows.persona_flow import MIN_AVATAR_PIXELS, avatar_reference_state


def _write(path, size, fmt="JPEG"):
    Image.new("RGB", size, (120, 90, 60)).save(path, format=fmt)
    return path


def test_a_real_portrait_is_a_usable_reference(tmp_path):
    path = _write(tmp_path / "naomi.jpg", (768, 768))

    usable, why = avatar_reference_state(path)

    assert usable is True
    assert "768x768" in why


def test_the_e2e_placeholder_is_refused_even_though_it_opens(tmp_path):
    """The exact file left in storage/avatars: an 8x8 PNG. It decodes cleanly,
    which is why the guard cannot be a decode check."""
    path = _write(tmp_path / "e2e_ava_1789248798310.jpg", (8, 8), fmt="PNG")

    with Image.open(path) as im:  # it really does open
        assert im.size == (8, 8)

    usable, why = avatar_reference_state(path)

    assert usable is False
    assert "only 8x8" in why


def test_a_wide_but_short_image_is_refused_on_its_shorter_side(tmp_path):
    """The engine resizes to a square, so a 1024x8 strip is a smear, not a face."""
    path = _write(tmp_path / "strip.jpg", (1024, 8))

    usable, why = avatar_reference_state(path)

    assert usable is False
    assert "only 1024x8" in why


def test_a_missing_avatar_is_reported_as_missing_not_as_unusable(tmp_path):
    """The two take different messages: one is the normal first build, the other
    means something unexpected is sitting in the directory."""
    usable, why = avatar_reference_state(tmp_path / "nobody.jpg")

    assert usable is False
    assert why == "no avatar yet"


def test_a_truncated_file_is_refused_rather_than_crashing_the_build(tmp_path):
    """A half-written file is what a killed generation leaves behind."""
    path = tmp_path / "partial.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0 this is not a jpeg")

    usable, why = avatar_reference_state(path)

    assert usable is False
    assert "unreadable" in why


def test_the_threshold_is_a_face_size_not_a_pixel_count(tmp_path):
    """Pinned so the guard cannot be lowered to whatever happens to pass: below
    this the upscale to 1024 is inventing a face rather than enlarging one."""
    assert MIN_AVATAR_PIXELS >= 256
    assert avatar_reference_state(_write(tmp_path / "a.jpg", (255, 255)))[0] is False
    assert avatar_reference_state(_write(tmp_path / "b.jpg", (256, 256)))[0] is True
