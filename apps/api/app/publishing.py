"""Publishing one scheduled post — the core the HTTP route and the clock share.

Extracted from `app/routes/publish.py` the moment a second caller appeared (the
scheduler). The extraction is the point: the rule below is the only thing
standing between this codebase and a paid post that never existed, and two
copies of it is how one copy drifts while the other keeps passing its tests.

    **`status = "posted"` is written only when the platform returned a post id.**

Not when the request was sent, not when it returned 200, not when the local
write succeeded. `app/delivery.py` documents the endpoints this codebase removed
for answering `{"status": "sent"}` after a local-only write.

Nothing here knows about HTTP. A refusal is raised as `PublishRefused` carrying a
status code, which the route renders as an `HTTPException` and the scheduler
renders as a `blocked` row. One decision, two renderings, no second rule.

The publisher is passed in rather than resolved here, because the two callers
resolve it differently and both need to keep that difference: the route uses
`require_publisher()` (which raises HTTP 503/409 by design), the scheduler uses
`get_publisher()` and records the absence instead of failing a request.
"""

from __future__ import annotations

import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app import paths
from app.models import ScheduledPost


class PublishRefused(Exception):
    """This post cannot be published as it stands.

    Carries the status code the HTTP layer should use. 409 for a state of *this
    post* (already posted, priced with no media, media that does not resolve),
    404 for a post that is not there, 502 when the platform was reached and did
    not confirm.
    """

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def dollars_to_minor(price: float | None) -> int | None:
    """$20.50 -> 2050 minor units, exactly.

    Through `Decimal(str(price))`, not `price * 100`: 20.5 * 100 is exactly
    representable, but plenty of prices are not (4.35 * 100 = 434.99999…), and
    truncating that would undercharge a paying fan by a cent. The publisher takes
    minor units unconverted, so this is the only place the conversion happens.
    """
    if price is None:
        return None
    try:
        return int((Decimal(str(price)) * 100).to_integral_value())
    except (InvalidOperation, ValueError) as exc:
        raise PublishRefused(400, f"ppv_price {price!r} is not a usable amount") from exc


def publisher_classes() -> dict[str, type]:
    """Every publish adapter, keyed by the platform name it declares.

    Discovered, never listed. A hand-kept tuple of platform names is exactly
    what drifts from the adapters, and this codebase already carries the scar:
    `routes/schedule.py` fills the calendar for instagram/tiktok/youtube while
    the only adapter that can post anywhere is a fourth name that appears
    nowhere in it.
    """
    import importlib
    import pkgutil

    import app.providers.publish as package
    from app.providers.publish import PublishProvider

    found: dict[str, type] = {}
    for info in pkgutil.iter_modules(package.__path__, prefix=f"{package.__name__}."):
        module = importlib.import_module(info.name)
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, PublishProvider)
                and value is not PublishProvider
            ):
                name = getattr(value, "name", "")
                if name and name != PublishProvider.name:
                    found[name] = value
    return found


def platform_supports_price(platform: str) -> bool:
    """Whether a post on `platform` can carry a price at all.

    A platform with no adapter here is not sellable *through this app*, which
    is a different thing from a platform that cannot sell — the calendar may
    still schedule for one; it just cannot promise money for it.
    """
    return bool(getattr(publisher_classes().get(platform), "supports_price", False))


def resolved_price_minor(platform: str, stated_price: float | None) -> int | None:
    """What a new post on `platform` should be priced at, or None if it cannot be.

    `None` is returned only for a platform that cannot charge, where a price
    would be meaningless rather than free.

    On a platform that *can* charge, this raises when the operator has not
    stated a price. It has to: the alternative is a free post, and an unstated
    price is not a decision to give content away. The same goes for a stated
    price below the platform's floor — `dollars_to_minor` would carry it to
    `create_post` perfectly happily, and the platform would be the one to
    refuse it, after the calendar had already promised the slot.
    """
    platform_class = publisher_classes().get(platform)
    if not getattr(platform_class, "supports_price", False):
        return None

    if stated_price is None:
        raise PublishRefused(
            409,
            f"{platform} sells, so a post there needs a price — and none has "
            "been stated. Set the operator's default price before generating "
            "sellable slots; leaving it unset would schedule a calendar of "
            "free giveaways.",
        )

    price_minor = dollars_to_minor(stated_price)
    floor = int(getattr(platform_class, "min_price_minor", 0) or 0)
    if price_minor is not None and price_minor < floor:
        raise PublishRefused(
            409,
            f"{stated_price} is below {platform}'s minimum of "
            f"${floor / 100:.2f} per post, so it would be refused at publish "
            "time — after the calendar had already promised the slot.",
        )
    return price_minor


# The smallest edge a file must have to be content rather than a placeholder.
#
# Matches `app/workflows/persona_flow.MIN_AVATAR_PIXELS`, which refuses a
# placeholder at the avatar door. This is the same judgement at a different
# door — an image too small to be a picture must not become a persona's face,
# and it must not become a post a fan pays for either — so it is the same
# number, and `tests/test_publish_route.py` pins the two equal so they cannot
# drift into disagreeing about what a real image is.
#
# It is not hypothetical. `tests/conftest.py` records that the harness's own
# fakes (`_tiny_png`, an 8x8 PNG; `LoraAwareImageProvider`, a bare 8-byte
# signature) wrote 300-odd placeholder files into real storage before
# `isolate_storage` existed. `resolve_media` counted every one of them as a
# successful resolve, so the publisher was handed a header with no image in it
# and the post was marked published.
MIN_MEDIA_EDGE = 256

# Checked by extension. A file with no extension, or one this list does not
# know, is passed through on the not-empty check alone: the placeholder class
# was measured for images, and inventing a policy for media nobody has produced
# yet would be guessing at what to refuse.
_IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
)

# The leading segment every producer writes into a storage key, and therefore
# part of the key format rather than of any directory's name. See
# `media_path_for` for why it is a literal.
_KEY_PREFIX = "storage"


def _judge_image(size: tuple[int, int] | None, error: str = "") -> tuple[bool, str]:
    """The one place the floor is applied, so the two entry points agree."""
    if size is None:
        return False, f"it does not decode as an image ({error})"
    width, height = size
    if width < MIN_MEDIA_EDGE or height < MIN_MEDIA_EDGE:
        return False, (
            f"it is {width}x{height}, below the {MIN_MEDIA_EDGE}x{MIN_MEDIA_EDGE} "
            "floor — a placeholder rather than a picture"
        )
    return True, ""


def inspect_media(data: bytes, suffix: str = "") -> tuple[bool, str]:
    """Is this bytes-object an image a fan could actually be shown?

    Returns `(usable, reason)`, with `reason` empty when usable. Structural on
    purpose: it answers "is this an image of real size", never "is this a good
    image" — that second question is the operator's, and a codebase that
    answers it silently is a codebase that has started deciding what is for
    sale.
    """
    if not data:
        return False, "the file is empty"

    if suffix.lower() not in _IMAGE_SUFFIXES:
        return True, ""

    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            size = image.size
    except Exception as exc:  # unreadable header, truncated, not an image at all
        return _judge_image(None, type(exc).__name__)
    return _judge_image(size)


def inspect_media_file(path: Path) -> tuple[bool, str]:
    """`inspect_media`, for a file on disk, reading only the header.

    Separate entry point because the scheduler scans directories: reading every
    byte of every image on disk to check its size is the kind of thing that
    works on 338 placeholder files and falls over on a real library. Pillow's
    `open` parses the header and stops.
    """
    if not path.exists():
        return False, "the file is not on disk"
    if path.suffix.lower() not in _IMAGE_SUFFIXES:
        return True, ""

    try:
        from PIL import Image

        with Image.open(path) as image:
            size = image.size
    except Exception as exc:
        return _judge_image(None, type(exc).__name__)
    return _judge_image(size)


def media_path_for(key: str) -> Path | None:
    """Where a storage key actually lives on disk, or None for an empty key.

    One rule, because two producers write keys in two shapes and both are
    legitimate: `routes/schedule.py` and `routes/content.py` write
    repo-root-relative keys (`storage/shoots/<dir>/shot_01.png`), while older
    callers write bare ones (`shoots/<dir>/shot_01.png`). `STORAGE_ROOT` is
    `apps/api/storage`, so a prefixed key joined to it directly lands on
    `apps/api/storage/storage/...` — a directory that has never existed.

    That was the live state: every media key this app writes resolved to
    nothing, so `resolve_media` reported "no media" for posts that plainly had
    some, and the publisher refused them. It went unnoticed because there is no
    real media yet — the 338 files under `storage/shoots` are placeholders that
    would have been refused on their own merits — so the bug had nothing to
    bite on and no test to catch it.

    `_KEY_PREFIX` is a literal rather than `paths.STORAGE_ROOT.name` on purpose.
    The prefix is part of the key *format* — a wire convention shared by every
    producer and by rows already in the database — while `STORAGE_ROOT` is a
    deployment detail that the test harness relocates. Deriving one from the
    other made the rule silently stop stripping the moment storage lived
    anywhere but a directory literally called `storage`.
    """
    if not key:
        return None
    candidate = Path(key)
    if candidate.is_absolute():
        return candidate
    parts = candidate.parts
    if parts and parts[0] == _KEY_PREFIX:
        return paths.STORAGE_ROOT.joinpath(*parts[1:])
    return paths.STORAGE_ROOT / candidate


def media_on_disk(key: str) -> tuple[bool, str]:
    """`inspect_media_file` for a storage key. Read-only: no workdir, no write.

    The business view asks "could this post actually ship?" of every slot on the
    calendar, and that question must not create files to answer it — which is
    exactly why this exists beside `resolve_media` instead of calling it.
    """
    path = media_path_for(key)
    if path is None:
        return False, "the post carries no media key at all"
    return inspect_media_file(path)


async def resolve_media(
    keys: list[str], workdir: Path
) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """Storage keys -> local file paths, plus the keys that did not resolve.

    Returns three lists: resolved paths, keys that produced no file at all, and
    `(key, reason)` for files that were found but are not usable media. The
    third list is the one that did not exist before, and its absence is how 338
    placeholder files sat one call away from a paid post: a key that resolves to
    eight bytes was counted as a successful resolve, so the publisher was handed
    a corrupt file and the post was marked published.

    The caller refuses rather than filtering, for the same reason it refuses a
    partial media set: publishing two of three photos, or three photos where one
    is a broken placeholder, is a different product from the one that was
    scheduled.
    """
    from app.providers.registry import get_registry

    storage = get_registry().resolve_optional("storage")
    resolved: list[str] = []
    missing: list[str] = []
    unusable: list[tuple[str, str]] = []

    for index, key in enumerate(keys):
        if not key:
            continue
        data: bytes | None = None

        # The storage provider is the source of truth for its own keying.
        if storage is not None:
            try:
                result = await storage.download(key)
                # `FileSystemStorageProvider.download` returns its bytes under
                # `data`, not `content`. Reading the wrong key made this branch
                # unreachable for every provider — it always fell through to the
                # disk path below, so a remote-backed key could never resolve.
                if result.success and result.data.get("data"):
                    data = result.data["data"]
            except Exception:
                data = None

        # Fall back to a direct path under STORAGE_ROOT. Keys written by older
        # code are bare relative paths, and a key that is *only* on disk would
        # otherwise read as "no media" for a post that plainly has some.
        if data is None:
            candidate = media_path_for(key)
            if candidate is not None and candidate.is_file():
                data = candidate.read_bytes()

        if data is None:
            missing.append(key)
            continue

        suffix = Path(key).suffix or ".png"
        usable, reason = inspect_media(data, suffix)
        if not usable:
            unusable.append((key, reason))
            continue

        target = workdir / f"{index:03d}{suffix}"
        target.write_bytes(data)
        resolved.append(str(target))

    return resolved, missing, unusable


async def publish_post(
    db: AsyncSession,
    post_id: str,
    *,
    audience: str,
    publisher,
) -> dict:
    """Publish one scheduled post and record what actually happened.

    Raises `PublishRefused` for every state in which the post must not go out.
    On success the row carries the platform's own post id, which is the only
    evidence that anything was published.
    """
    post = await db.get(ScheduledPost, post_id)
    if not post:
        raise PublishRefused(404, "Scheduled post not found")

    if post.status == "posted":
        raise PublishRefused(
            409,
            f"This post was already published ({post.post_url or 'no url recorded'}). "
            "Publishing again would post it twice on the platform.",
        )

    caption = post.caption or post.title or ""
    price_minor = dollars_to_minor(post.ppv_price)
    keys = [k for k in (post.media_keys or []) if k]

    if not caption and not keys:
        raise PublishRefused(
            409,
            "This post has neither a caption nor any media. Publishing it would "
            "put an empty post on the platform.",
        )

    if price_minor is not None and not keys:
        raise PublishRefused(
            409,
            f"This post is priced at {post.ppv_price} but carries no media. A "
            "paid post with nothing attached would charge a fan for an empty post.",
        )

    # The other half of the pair, and the one that was missing. On a platform
    # where a post can carry a price, *no* price means the post goes out free —
    # so an unpriced post there is a giveaway that has to be intended, not a
    # default. Without this the calendar's rows (all `ppv_price = NULL`, and the
    # column's own comment says "null = free post") would publish happily at
    # zero for as long as anyone let them, and the studio would earn nothing
    # while every check reported healthy.
    #
    # `metadata_json["free_post"]` must be the boolean True — a truthy string is
    # refused too. This codebase has already been bitten once by a gate that
    # accepted the *presence* of a key rather than its value (`{"approved":
    # "no"}` passing an approval check), and "free" is a worse thing to get
    # wrong than "approved".
    if price_minor is None and getattr(publisher, "supports_price", False):
        marked_free = (post.metadata_json or {}).get("free_post")
        if marked_free is not True:
            floor = int(getattr(publisher, "min_price_minor", 0) or 0)
            # The floor is named from the adapter, not restated here: a second
            # copy of "Fanvue's minimum is $3.00" is a copy that can go stale
            # while the platform's own check keeps enforcing the real one.
            floor_note = (
                f" ({publisher.name}'s minimum is ${floor / 100:.2f})" if floor else ""
            )
            raise PublishRefused(
                409,
                f"This post has no price, and {publisher.name} sells — so it would "
                f"publish as a free giveaway. Set `ppv_price`{floor_note}, or mark "
                "it deliberately with metadata_json.free_post = true if a free "
                "post is what you want. An unset price is not an instruction to "
                "give it away.",
            )

    workdir = Path(tempfile.mkdtemp(prefix="publish_"))
    try:
        media_paths, missing, unusable = await resolve_media(keys, workdir)

        if missing:
            raise PublishRefused(
                409,
                f"{len(missing)} of {len(keys)} media key(s) do not resolve to a "
                f"file: {', '.join(missing[:5])}"
                + (" …" if len(missing) > 5 else "")
                + ". Publishing a subset would ship a different post than the one "
                "that was scheduled.",
            )

        if unusable:
            # Distinct from `missing`, and it has to be: "we cannot find it" and
            # "we found it and it is eight bytes" send the operator to different
            # places, and only this one names a file that is sitting on disk
            # looking like content.
            detail = "; ".join(f"{key} — {reason}" for key, reason in unusable[:3])
            if len(unusable) > 3:
                detail += f" (and {len(unusable) - 3} more)"
            raise PublishRefused(
                409,
                f"{len(unusable)} media file(s) resolved but are not usable "
                f"media: {detail}. Publishing them would ship a placeholder (or "
                "a corrupt file) to a paying fan.",
            )

        from app.providers.publish import PublishFailed

        try:
            result = await publisher.create_post(
                text=caption,
                media_paths=media_paths,
                price_minor=price_minor,
                audience=audience,
            )
        except PublishFailed as exc:
            # The request reached the platform and did not succeed. Recorded as
            # failed with the reason, never as posted.
            post.status = "failed"
            post.metadata_json = {
                **(post.metadata_json or {}),
                "publish_error": str(exc),
                "publish_attempted_at": now_iso(),
            }
            await db.commit()
            raise PublishRefused(502, f"Publishing failed: {exc}") from exc

        if not result.ok or not result.post_uuid:
            # A response we cannot read as a published post. Treated as failure
            # for the same reason: no id means no proof it exists.
            post.status = "failed"
            post.metadata_json = {
                **(post.metadata_json or {}),
                "publish_error": result.error or "no post id returned",
                "publish_detail": result.detail,
                "publish_attempted_at": now_iso(),
            }
            await db.commit()
            raise PublishRefused(
                502,
                "The platform did not return a post id, so nothing is recorded as "
                f"published. Response said: {result.error or 'no error given'}",
            )

        post.status = "posted"
        post.posted_at = now()
        post.post_url = result.detail.get("url", "") or post.post_url or ""
        post.metadata_json = {
            **(post.metadata_json or {}),
            "post_uuid": result.post_uuid,
            "published_at": now_iso(),
        }
        await db.commit()

        return {
            "post_id": post.id,
            "status": "posted",
            "platform": post.platform,
            "post_uuid": result.post_uuid,
            "post_url": post.post_url,
            "price_minor": price_minor,
            "media_count": len(media_paths),
            "audience": audience,
            "note": (
                "Published. `post_uuid` is the platform's own id — the row is "
                "marked posted because that came back, not because the request "
                "was sent."
            ),
        }
    finally:
        for leftover in workdir.glob("*"):
            leftover.unlink(missing_ok=True)
        workdir.rmdir()


def now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat()
