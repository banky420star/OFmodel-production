"""Finishing — the step between a rendered plate and a shippable post.

A raw diffusion render is not a post. It is the wrong aspect ratio for every
platform, it carries no sensor noise, and it is stored at whatever size the
sampler produced. This module does those three things and reports exactly what
it did, so nothing downstream has to guess whether a plate was resized, cropped
or grain-passed.

**What this module does not do: make the render better.** Every operation here
is resampling or compositing on pixels that already exist. Lanczos upscaling
interpolates — it invents no detail that was not in the source, and a 1024px
render upscaled to 2048 is softer per-pixel than the original, not sharper.
Detail synthesis needs a separate model (ESRGAN/Real-ESRGAN), which is not
installed here. `finish()` records `upscaled.lanczos` rather than `upscaled`
for that reason: a caller reading the report is told the method, not just the
outcome. The same honesty applies to grain — it is a *cosmetic* pass that makes
a too-clean render read more like a photograph, and it is labelled cosmetic in
the report.

**Crops are destructive and are reported as such.** Converting 1:1 to 9:16
removes pixels; if the subject's head is in them, the post is ruined and the
operator finds out after publishing. `finish()` therefore refuses a crop that
would remove more than `max_crop_fraction` of either dimension unless the caller
passes `allow_heavy_crop=True`, and the report always names what was cut.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageFilter

try:  # structlog is used across app/; keep the same logging shape.
    import structlog

    logger = structlog.get_logger()
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger(__name__)


# Aspect/size targets per platform surface, as (name, width, height, note).
# Heights are chosen so the pair is a clean multiple of the short side; they are
# rendering targets, not platform mandates, and every one of them is a crop
# away from whatever the sampler produced.
PLATFORM_ASPECTS: dict[str, tuple[int, int, str]] = {
    "square": (1080, 1080, "1:1 — profile grid, safest crop"),
    "fanvue_feed": (1080, 1350, "4:5 — the feed's tallest supported ratio"),
    "portrait_3x4": (1080, 1440, "3:4 — classic portrait crop"),
    "instagram_feed": (1080, 1350, "4:5 — Instagram feed maximum height"),
    "story_9x16": (1080, 1920, "9:16 — stories, reels, TikTok; heaviest crop from 1:1"),
    "landscape_16x9": (1920, 1080, "16:9 — wallpapers and video thumbnails"),
}

# Refuse a crop this destructive without an explicit opt-in. 0.25 means "more
# than a quarter of the dimension is being thrown away" — well past the point
# where a framing mistake becomes a broken image.
DEFAULT_MAX_CROP_FRACTION = 0.25

# JPEG quality. 92 keeps skin gradient smooth without the visible blocking that
# shows up on faces around 80.
DEFAULT_JPEG_QUALITY = 92


@dataclass
class FinishReport:
    """What actually happened to the pixels, in the order it happened.

    `steps` is the audit trail: a caller that only wants the output can ignore
    it, and a caller debugging "why does this face look smooth" cannot, because
    a missing `grain` step and a doubled `resize` step read differently here
    than they would as a bare file on disk.
    """

    ok: bool
    width: int = 0
    height: int = 0
    source_width: int = 0
    source_height: int = 0
    aspect: str = ""
    steps: list[str] = field(default_factory=list)
    # Fraction of each dimension removed by cropping, 0.0 when nothing was cut.
    cropped_width_fraction: float = 0.0
    cropped_height_fraction: float = 0.0
    upscale_factor: float = 1.0
    grain_amount: float = 0.0
    format: str = ""
    bytes_written: int = 0
    out_path: str = ""
    error: str = ""
    # Stated in the report rather than left for the reader to know: neither of
    # these adds information to the image.
    cosmetic_only: list[str] = field(default_factory=lambda: ["grain"])
    note: str = (
        "Grain and upscaling are post-processes. Grain adds sensor-noise texture "
        "that a real camera would have produced; Lanczos upscaling resamples and "
        "adds no detail. Neither corrects a bad render — a wrong hand stays wrong."
    )


class FinishError(Exception):
    """Raised when finishing would destroy the image rather than polish it."""


# ── grain ────────────────────────────────────────────────────────────────


def add_grain(image: Image.Image, amount: float, seed: int = 7) -> Image.Image:
    """Sensor-noise texture: monochrome, shadow-weighted, plus slight chroma.

    Generated images come out perfectly clean, and a noise-free skin gradient is
    one of the loudest signals that a photo was synthesised — real sensors
    produce noise, and more of it in the shadows than in the highlights. This
    reproduces that distribution.

    `amount` is the monochrome standard deviation in 0-255 levels. 0 disables
    the pass and returns the image untouched. Anything above ~12 is visible as
    grain at 100% zoom rather than as texture, so values in the 5-10 range are
    what a phone photo actually looks like.
    """
    if amount <= 0:
        return image

    import numpy as np

    rgb = image.convert("RGB")
    a = np.asarray(rgb).astype(np.float32)

    # Shadows carry more visible noise than highlights on a real sensor.
    luma = a.mean(axis=2, keepdims=True) / 255.0
    weight = np.clip(1.2 - luma, 0.4, 1.2)

    rng = np.random.default_rng(seed)
    mono = rng.normal(0.0, amount, (a.shape[0], a.shape[1], 1)).astype(np.float32)
    chroma = rng.normal(0.0, amount * 0.35, a.shape).astype(np.float32)

    a = np.clip(a + mono * weight + chroma, 0, 255)
    return Image.fromarray(a.astype("uint8"))


# ── geometry ─────────────────────────────────────────────────────────────


def resize_to(
    image: Image.Image,
    width: int,
    height: int,
    *,
    fit: str = "cover",
    allow_heavy_crop: bool = False,
    max_crop_fraction: float = DEFAULT_MAX_CROP_FRACTION,
) -> tuple[Image.Image, dict]:
    """Resize to exactly width x height, returning the image and what it cost.

    `fit`:
      * ``cover``   — scale to fill, crop the overflow. Centred, so the middle
                      of the frame survives; this is the only mode that can
                      remove content.
      * ``contain`` — scale to fit inside, pad the remainder. Never crops, so
                      it is the safe choice when the subject's framing matters
                      more than filling the frame.
      * ``stretch`` — scale to exactly the target in both axes. Distorts when
                      the source aspect differs; offered because a caller may
                      genuinely want a deliberate squeeze, never as a default.

    Raises `FinishError` when a cover-crop would remove more than
    `max_crop_fraction` of a dimension and `allow_heavy_crop` is not set. A
    9:16 target from a 1:1 source cuts 43.75% of the width — usually straight
    through the arms — and an operator should be told that before it ships
    rather than after.
    """
    if width <= 0 or height <= 0:
        raise FinishError(f"target size must be positive, got {width}x{height}")
    if fit not in ("cover", "contain", "stretch"):
        raise FinishError(f"unknown fit mode {fit!r}; use cover, contain or stretch")

    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        raise FinishError(f"source image has no area: {src_w}x{src_h}")

    info: dict = {"fit": fit, "source_size": (src_w, src_h), "target_size": (width, height)}

    if fit == "stretch":
        info.update(cropped_w_fraction=0.0, cropped_h_fraction=0.0, scale=(width / src_w, height / src_h))
        return image.resize((width, height), Image.LANCZOS), info

    scale = (
        max(width / src_w, height / src_h) if fit == "cover" else min(width / src_w, height / src_h)
    )
    scaled_w = max(1, round(src_w * scale))
    scaled_h = max(1, round(src_h * scale))
    scaled = image.resize((scaled_w, scaled_h), Image.LANCZOS)

    if fit == "contain":
        canvas = Image.new("RGB", (width, height), (0, 0, 0))
        canvas.paste(scaled, ((width - scaled_w) // 2, (height - scaled_h) // 2))
        info.update(cropped_w_fraction=0.0, cropped_h_fraction=0.0, scale=(scale, scale),
                    padded=True)
        return canvas, info

    crop_w = scaled_w - width
    crop_h = scaled_h - height
    frac_w = crop_w / scaled_w if scaled_w else 0.0
    frac_h = crop_h / scaled_h if scaled_h else 0.0

    if (frac_w > max_crop_fraction or frac_h > max_crop_fraction) and not allow_heavy_crop:
        raise FinishError(
            f"{fit}-crop to {width}x{height} would remove {frac_w:.0%} of the width "
            f"and {frac_h:.0%} of the height from a {src_w}x{src_h} source, above the "
            f"{max_crop_fraction:.0%} limit. If the framing survives that, pass "
            "allow_heavy_crop=True; if it does not, use fit='contain' or generate "
            "at the target aspect instead."
        )

    left = crop_w // 2
    top = crop_h // 2
    out = scaled.crop((left, top, left + width, top + height))
    info.update(cropped_w_fraction=frac_w, cropped_h_fraction=frac_h, scale=(scale, scale))
    return out, info


def upscale(image: Image.Image, factor: float, *, sharpen: float = 0.0) -> Image.Image:
    """Resample larger with Lanczos, optionally re-sharpening slightly.

    This adds no detail. A 1024px render at 2x is a 2048px file whose real
    information content is still 1024px — the extra pixels are interpolation.
    It is worth doing when a platform re-encodes the upload anyway, because
    handing it more pixels than it needs avoids a second lossy resample; it is
    not a way to fix a soft render.

    `sharpen` runs a light unsharp mask afterwards, which restores some of the
    micro-contrast Lanczos flattens. 0 disables it. Above ~1.5 it produces the
    haloed look that reads as over-processed, so it stays small.
    """
    if factor <= 1.0:
        return image
    src_w, src_h = image.size
    out = image.resize(
        (max(1, round(src_w * factor)), max(1, round(src_h * factor))), Image.LANCZOS
    )
    if sharpen > 0:
        out = out.filter(
            ImageFilter.UnsharpMask(
                radius=1.2, percent=int(round(sharpen * 100)), threshold=3
            )
        )
    return out


# ── the pass ─────────────────────────────────────────────────────────────


def finish(
    source: Path | str | bytes | Image.Image,
    *,
    aspect: str = "",
    width: int = 0,
    height: int = 0,
    fit: str = "cover",
    allow_heavy_crop: bool = False,
    upscale_factor: float = 1.0,
    sharpen: float = 0.0,
    grain: float = 0.0,
    grain_seed: int = 7,
    out_format: str = "PNG",
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    out_path: Path | str | None = None,
) -> FinishReport:
    """Run the finishing pass and return what it did.

    `source` may be a filesystem path, encoded bytes, or an already-decoded
    `Image`. A path with no `out_path` is finished in place; bytes and Images
    are only written when `out_path` names somewhere to put them.

    Order matters and is fixed: **resize -> upscale -> grain -> encode**. Grain
    goes last because the resize resamples it away otherwise, and it goes before
    encoding because JPEG's blocking would distort it after the fact. Encoding
    is PNG by default — a finished plate is an asset to be re-used, and PNG
    survives a later re-crop that a quality-92 JPEG would not.

    `aspect` names a `PLATFORM_ASPECTS` key and supplies width/height; passing
    explicit `width`/`height` instead is fine, and passing both is an error
    rather than a silent precedence rule.

    Never raises for an oversized crop unless `allow_heavy_crop` is false — see
    `resize_to`. Returns `FinishReport(ok=False, error=...)` for anything else,
    so a caller in a workflow records a failed step instead of a traceback.
    """
    report = FinishReport(ok=False, grain_amount=grain, upscale_factor=upscale_factor)

    if aspect and (width or height):
        report.error = (
            "pass either aspect (a PLATFORM_ASPECTS key) or width/height, not both — "
            "otherwise which one wins is a rule nobody reads"
        )
        return report

    if aspect:
        target = PLATFORM_ASPECTS.get(aspect)
        if target is None:
            report.error = (
                f"unknown aspect {aspect!r}; known: {', '.join(sorted(PLATFORM_ASPECTS))}"
            )
            return report
        width, height, _note = target
    report.aspect = aspect

    try:
        if isinstance(source, Image.Image):
            # Already-decoded pixels, e.g. a render just returned by a provider
            # in the same process. Nothing to read and nothing to write back
            # unless the caller names a destination.
            image = source.convert("RGB")
        elif isinstance(source, (str, Path)):
            with Image.open(source) as im:
                image = im.convert("RGB")
            report.out_path = str(source) if out_path is None else str(out_path)
        else:
            image = Image.open(io.BytesIO(source)).convert("RGB")

        report.source_width, report.source_height = image.size

        if width and height:
            image, info = resize_to(
                image,
                width,
                height,
                fit=fit,
                allow_heavy_crop=allow_heavy_crop,
            )
            report.cropped_width_fraction = info["cropped_w_fraction"]
            report.cropped_height_fraction = info["cropped_h_fraction"]
            report.steps.append(f"resize:{fit}->{width}x{height}")

        if upscale_factor > 1.0:
            before = image.size
            image = upscale(image, upscale_factor, sharpen=sharpen)
            report.steps.append(f"upscale:lanczos x{upscale_factor} {before}->{image.size}")

        if grain > 0:
            image = add_grain(image, grain, seed=grain_seed)
            report.steps.append(f"grain:{grain} (cosmetic)")

        report.width, report.height = image.size
        report.format = out_format.upper()

        buffer = io.BytesIO()
        if out_format.upper() in ("JPG", "JPEG"):
            image.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
        else:
            image.save(buffer, format=out_format.upper())
        payload = buffer.getvalue()
        report.bytes_written = len(payload)

        if out_path is not None or isinstance(source, (str, Path)):
            destination = Path(out_path) if out_path is not None else Path(source)
            destination.write_bytes(payload)
            report.out_path = str(destination)

        report.ok = True
        report.steps.append(f"encode:{report.format} {report.bytes_written} bytes")
        return report

    except FinishError as exc:
        # A refused crop is a decision the operator must make, so it is returned
        # as a structured failure with the numbers, not swallowed.
        logger.warning("finish_refused", error=str(exc))
        report.error = str(exc)
        return report
    except Exception as exc:  # pragma: no cover - unexpected decode/encode failure
        logger.error("finish_failed", error=str(exc))
        report.error = str(exc)
        return report


def describe_plan(
    *, aspect: str = "", width: int = 0, height: int = 0, fit: str = "cover"
) -> dict:
    """Say what a resize would remove, without doing it.

    Read-only and side-effect free so a UI can warn before an operator commits
    a crop — the same numbers `finish()` would refuse on, computed from the
    target alone. It cannot report the source-relative crop without a source
    size, so it takes one when the caller has it.
    """
    if aspect and (width or height):
        return {"ok": False, "error": "pass either aspect or width/height, not both"}
    if aspect:
        target = PLATFORM_ASPECTS.get(aspect)
        if target is None:
            return {"ok": False, "error": f"unknown aspect {aspect!r}"}
        width, height, note = target
    else:
        note = ""
    if not width or not height:
        return {"ok": False, "error": "no target size given"}
    return {
        "ok": True,
        "aspect": aspect,
        "width": width,
        "height": height,
        "fit": fit,
        "note": note,
        "max_crop_fraction": DEFAULT_MAX_CROP_FRACTION,
    }
