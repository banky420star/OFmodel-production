"""Pricing and publishing a produced pack.

Prices were hand-written in `seed_fan_slice.py` and nowhere else, so a pack the
line produced could be generated and assembled and still not be buyable. These
tests pin the rule table, the idempotency of the publish step, and the two
things that must never happen: publishing an empty product, and any of this
touching the ledger.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.billing import pricing
from app.models import (
    ContentPack, ContentPackStatus, LedgerEntry, LedgerTransaction, Persona,
    Product, ProductMedia, Wallet,
)


async def _persona(db, name: str = "") -> Persona:
    persona = Persona(id=uuid.uuid4(), name=name or f"Price_{uuid.uuid4().hex[:6]}", age=26)
    db.add(persona)
    await db.flush()
    return persona


async def _pack(db, persona, *, images=(), videos=(), status=ContentPackStatus.ASSEMBLED):
    pack = ContentPack(
        id=uuid.uuid4(),
        persona_id=persona.id,
        name="Test Set",
        status=status,
        images=list(images),
        videos=list(videos),
        captions=[],
    )
    db.add(pack)
    await db.flush()
    return pack


# ── the rule table ───────────────────────────────────────────────────

def test_price_scales_with_the_number_of_files():
    assert pricing.price_for("photoset", 3) == 300
    assert pricing.price_for("photoset", 4) == 400
    assert pricing.price_for("photoset", 12) == 1200
    # Fewer files than the base covers is the base price, never negative.
    assert pricing.price_for("photoset", 1) == 300


def test_an_unpriced_kind_is_a_loud_error():
    """Adding a product kind without a price must fail, not default to free."""
    with pytest.raises(KeyError):
        pricing.price_for("livestream", 1)


def test_a_product_with_no_media_cannot_be_priced():
    with pytest.raises(ValueError):
        pricing.price_for("photoset", 0)


def test_the_ceiling_holds():
    assert pricing.price_for("video", 500) == pricing.MAX_PRICE_MINOR


def test_classify_reads_the_pack_contents():
    p = Persona(id=uuid.uuid4(), name="x", age=26)
    assert pricing.classify(ContentPack(images=["a"], videos=[])) == "image"
    assert pricing.classify(ContentPack(images=["a", "b"], videos=[])) == "photoset"
    assert pricing.classify(ContentPack(images=[], videos=["v"])) == "video"
    assert pricing.classify(ContentPack(images=["a"], videos=["v"])) == "bundle"


def test_a_bundle_is_the_one_gated_behind_a_tier():
    assert pricing.rule_for("bundle").min_tier_rank == 2
    assert pricing.rule_for("photoset").min_tier_rank == 0


# ── publishing ───────────────────────────────────────────────────────

async def test_a_pack_with_no_media_sells_nothing(db):
    persona = await _persona(db)
    pack = await _pack(db, persona, images=[], videos=[])

    report = await pricing.publish_pack(db, persona=persona, pack=pack)

    assert report["outcome"] == "produced_nothing"
    assert report["product_id"] is None
    count = (await db.execute(
        select(func.count()).select_from(Product).where(Product.persona_id == str(persona.id))
    )).scalar_one()
    assert count == 0, "an empty product must never be created"
    assert pack.status is ContentPackStatus.ASSEMBLED, "an unpublished pack stays unpublished"


async def test_publishing_writes_a_buyable_product(db):
    persona = await _persona(db)
    keys = [f"storage/shoots/abc/{i}.png" for i in range(1, 5)]
    pack = await _pack(db, persona, images=keys)

    report = await pricing.publish_pack(db, persona=persona, pack=pack)

    assert report["outcome"] == "did_work"
    product = await db.get(Product, report["product_id"])
    assert product.persona_id == str(persona.id)
    assert product.price_minor == 400          # 300 base + 1 extra file
    assert product.kind == "photoset"
    assert product.status == "published"
    assert product.is_adult is False

    media = (await db.execute(
        select(ProductMedia).where(ProductMedia.product_id == product.id)
        .order_by(ProductMedia.position)
    )).scalars().all()
    assert [m.rel_path for m in media] == keys

    assert pack.status is ContentPackStatus.PUBLISHED
    assert pack.published_at is not None


async def test_publishing_twice_updates_one_listing(db):
    persona = await _persona(db)
    pack = await _pack(db, persona, images=["a.png", "b.png", "c.png"])

    first = await pricing.publish_pack(db, persona=persona, pack=pack)
    second = await pricing.publish_pack(db, persona=persona, pack=pack)

    assert first["product_id"] == second["product_id"]
    assert second["created"] is False
    count = (await db.execute(
        select(func.count()).select_from(Product).where(Product.persona_id == str(persona.id))
    )).scalar_one()
    assert count == 1, "a re-run must not list the same content twice"
    media = (await db.execute(
        select(func.count()).select_from(ProductMedia)
        .where(ProductMedia.product_id == first["product_id"])
    )).scalar_one()
    assert media == 3, "and must not duplicate its media rows"


async def test_a_pack_that_lost_a_file_stops_selling_it(db):
    persona = await _persona(db)
    pack = await _pack(db, persona, images=["a.png", "b.png", "c.png"])
    first = await pricing.publish_pack(db, persona=persona, pack=pack)

    pack.images = ["a.png"]
    await pricing.publish_pack(db, persona=persona, pack=pack)

    media = (await db.execute(
        select(ProductMedia).where(ProductMedia.product_id == first["product_id"])
    )).scalars().all()
    assert [m.rel_path for m in media] == ["a.png"]


async def test_pricing_moves_no_money(db):
    """The ledger is the only thing that records a payment, and this is not one."""
    persona = await _persona(db)
    pack = await _pack(db, persona, images=["a.png", "b.png"])

    before_tx = (await db.execute(select(func.count()).select_from(LedgerTransaction))).scalar_one()
    before_entries = (await db.execute(select(func.count()).select_from(LedgerEntry))).scalar_one()
    before_wallets = (await db.execute(select(func.count()).select_from(Wallet))).scalar_one()

    await pricing.publish_pack(db, persona=persona, pack=pack)

    assert (await db.execute(select(func.count()).select_from(LedgerTransaction))).scalar_one() == before_tx
    assert (await db.execute(select(func.count()).select_from(LedgerEntry))).scalar_one() == before_entries
    assert (await db.execute(select(func.count()).select_from(Wallet))).scalar_one() == before_wallets


async def test_a_video_pack_is_priced_as_video(db):
    persona = await _persona(db)
    pack = await _pack(db, persona, videos=["v.mp4"])

    report = await pricing.publish_pack(db, persona=persona, pack=pack)

    assert report["kind"] == "video"
    assert report["price_minor"] == 900
