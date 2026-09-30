"""Price a produced content pack, and publish it to the fan surface.

Prices lived in exactly two places before this: `seed_fan_slice.py`, which
writes them once by hand, and the Monetization page, which displays hardcoded
literals. So a pack the production line produced could be generated, assembled
and shown in the operator UI, and still not be buyable — nothing turned a pack
into a `Product`. This is that step.

Two rules govern everything here:

* **It sets list prices only.** No money moves, no ledger entry is written, and
  nothing in this module can create revenue. The ledger records payments and
  stays the only place that does.
* **A pack with no sellable media is not published.** An empty product is worse
  than no product: it takes a fan's money for nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import audit
from app.models import ContentPack, ContentPackStatus, Product, ProductMedia


@dataclass(frozen=True)
class PriceRule:
    """What a kind of product costs, and who gets it without paying again."""

    kind: str
    base_minor: int
    per_extra_minor: int
    included_in_pack: int
    min_tier_rank: int


# Ordered so the first rule whose `kind` matches wins; there is one rule per
# kind today, and the list shape is what makes a second one additive.
RULES: tuple[PriceRule, ...] = (
    PriceRule("image",   base_minor=300,  per_extra_minor=100, included_in_pack=1, min_tier_rank=0),
    PriceRule("photoset", base_minor=300, per_extra_minor=100, included_in_pack=3, min_tier_rank=0),
    PriceRule("video",   base_minor=900,  per_extra_minor=300, included_in_pack=1, min_tier_rank=0),
    PriceRule("bundle",  base_minor=1200, per_extra_minor=50,  included_in_pack=3, min_tier_rank=2),
)

# A ceiling, not a target: past this a single item stops looking like a
# purchase and starts looking like a mistake, and a mis-scaled rule should
# fail loudly in a test rather than produce a four-figure PPV.
MAX_PRICE_MINOR = 25_000


def rule_for(kind: str) -> PriceRule:
    for rule in RULES:
        if rule.kind == kind:
            return rule
    raise KeyError(f"no price rule for product kind {kind!r}")


def price_for(kind: str, media_count: int) -> int:
    """List price in minor units for `media_count` files of `kind`.

    Scales with the number of files past the number the base price covers, so
    a 12-image set is not sold for the price of a 3-image one.
    """
    if media_count < 1:
        raise ValueError("a product with no media cannot be priced")
    rule = rule_for(kind)
    extra = max(0, media_count - rule.included_in_pack)
    return min(rule.base_minor + extra * rule.per_extra_minor, MAX_PRICE_MINOR)


def _sellable_media(pack: ContentPack) -> list[str]:
    """Images first, then videos — the keys the pack actually holds.

    Order is fixed rather than incidental so the same pack always yields the
    same product media order, and a re-run updates rows instead of reshuffling
    them under a fan who is looking at the page.
    """
    return [k for k in (pack.images or []) if k] + [k for k in (pack.videos or []) if k]


def classify(pack: ContentPack) -> str:
    """Which price rule a pack falls under."""
    videos = [k for k in (pack.videos or []) if k]
    images = [k for k in (pack.images or []) if k]
    if videos and images:
        return "bundle"
    if videos:
        return "video"
    if len(images) == 1:
        return "image"
    return "photoset"


async def publish_pack(
    db: AsyncSession,
    *,
    persona,
    pack: ContentPack,
    title: str | None = None,
    description: str = "",
) -> dict:
    """Price `pack` and publish it as a `Product` the fan surface can sell.

    Idempotent on `(persona_id, title)`: running twice updates the same product
    and its media rather than creating a second listing for the same content.
    Returns a report — never raises for a pack that simply has nothing to sell,
    because "there was nothing to publish" is an outcome the caller records,
    not an exception.
    """
    media = _sellable_media(pack)
    if not media:
        return {
            "outcome": "produced_nothing",
            "reason": "pack holds no image or video keys",
            "product_id": None,
        }

    kind = classify(pack)
    rule = rule_for(kind)
    price_minor = price_for(kind, len(media))
    title = title or pack.name or f"{persona.name} — untitled set"

    existing = (
        await db.execute(
            select(Product).where(
                Product.persona_id == str(persona.id),
                Product.title == title,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        product = Product(
            persona_id=str(persona.id),
            title=title,
            description=description,
            price_minor=price_minor,
            kind=kind,
            min_tier_rank=rule.min_tier_rank,
            # Stays False: the DOB check is self-attested, which is not age
            # assurance, so nothing may be gated as adult-only. Same reasoning
            # as the model's own docstring.
            is_adult=False,
            status="published",
            cover_path=media[0],
        )
        db.add(product)
        await db.flush()
        created = True
    else:
        product = existing
        product.price_minor = price_minor
        product.kind = kind
        product.min_tier_rank = rule.min_tier_rank
        product.description = description or product.description
        product.cover_path = media[0]
        created = False
        await db.flush()
        # Replace the media set wholesale: a pack that lost a file must not
        # keep selling it.
        await db.execute(
            ProductMedia.__table__.delete().where(ProductMedia.product_id == product.id)
        )

    for position, key in enumerate(media):
        db.add(ProductMedia(
            product_id=product.id,
            rel_path=key,
            caption=f"{persona.name} — {title} {position + 1}",
            position=position,
        ))

    pack.status = ContentPackStatus.PUBLISHED
    pack.published_at = datetime.now(timezone.utc)
    await db.flush()

    await audit(
        db,
        "content.pack_published",
        actor="line",
        object_type="product",
        object_id=product.id,
        detail={
            "pack_id": str(pack.id),
            "kind": kind,
            "price_minor": price_minor,
            "media_count": len(media),
            "created": created,
        },
    )

    return {
        "outcome": "did_work",
        "product_id": str(product.id),
        "kind": kind,
        "price_minor": price_minor,
        "media_count": len(media),
        "created": created,
    }


def pack_id_or_none(value) -> UUID | None:
    """ContentPack.persona_id is a UUID column; route params arrive as strings."""
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None
