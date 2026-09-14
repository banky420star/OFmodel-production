"""Tests for the Fanvue Platform Manager v0 (compliance-gated inventory)."""
import pytest
from uuid import uuid4

from app import platform as pm
from app.models import PlatformAccount, Persona, ContentInventoryItem


async def _make_persona(client) -> str:
    r = await client.post("/api/v1/personas", json={
        "name": f"FanvueTest_{uuid4().hex[:6]}",
        "age": 27,
        "brand": "AI creator",
        "appearance": {},
        "personality": ["warm"],
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _fanvue_payload(persona_id: str, **over) -> dict:
    base = {
        "persona_id": persona_id,
        "platform": "fanvue",
        "handle": "ava.ai",
        "subscription_price": 9.99,
        "is_ai_disclosed": True,
        "ai_disclosure_text": "Ava is a virtual AI-generated creator.",
        "kyc_status": "verified",
        "consent_owner": "bank",
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_create_fanvue_account_requires_persona(client):
    r = await client.post("/api/v1/platform/accounts", json=_fanvue_payload(str(uuid4())))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_fanvue_account_and_compliance(client):
    pid = await _make_persona(client)
    r = await client.post("/api/v1/platform/accounts", json=_fanvue_payload(pid))
    assert r.status_code in (200, 201), r.text
    data = r.json()
    assert data["platform"] == "fanvue"
    assert data["is_ai_disclosed"] is True
    assert data["compliance"]["compliant"] is True
    assert data["status"] == "onboarding"


@pytest.mark.asyncio
async def test_compliance_gate_blocks_planning(client):
    pid = await _make_persona(client)
    # NOT disclosed, KYC not verified
    r = await client.post("/api/v1/platform/accounts", json=_fanvue_payload(
        pid, is_ai_disclosed=False, ai_disclosure_text="", kyc_status="not_started",
    ))
    aid = r.json()["id"]
    r = await client.post("/api/v1/platform/inventory/plan", params={"account_id": aid})
    assert r.status_code == 200
    data = r.json()
    assert data["planning_allowed"] is False
    assert data["restricted"] is False
    assert any("disclosure" in p for p in data["compliance"]["problems"])
    assert any("KYC" in p for p in data["compliance"]["problems"])


@pytest.mark.asyncio
async def test_onlyfans_planning_is_restricted(client):
    pid = await _make_persona(client)
    r = await client.post("/api/v1/platform/accounts", json=_fanvue_payload(
        pid, platform="onlyfans", kyc_status="verified",
    ))
    aid = r.json()["id"]
    r = await client.post("/api/v1/platform/inventory/plan", params={"account_id": aid})
    data = r.json()
    assert data["planning_allowed"] is False
    assert data["restricted"] is True
    assert "restricted" in data["reason"].lower()


@pytest.mark.asyncio
async def test_plan_creates_shortage_orders_and_dedupes(client):
    pid = await _make_persona(client)
    r = await client.post("/api/v1/platform/accounts", json=_fanvue_payload(pid))
    aid = r.json()["id"]

    r1 = await client.post("/api/v1/platform/inventory/plan", params={"account_id": aid})
    assert r1.status_code == 200
    plan1 = r1.json()
    assert plan1["planning_allowed"] is True
    assert len(plan1["orders_created"]) > 0  # empty inventory → shortages everywhere
    cells = {(c["tier"], c["content_type"]): c for c in plan1["cells"]}
    assert cells[("premium", "video")]["shortage"] > 0
    assert cells[("public", "image")]["shortage"] > 0

    # Re-plan immediately: every cell with an open order is covered → no dupes
    r2 = await client.post("/api/v1/platform/inventory/plan", params={"account_id": aid})
    plan2 = r2.json()
    assert plan2["orders_created"] == []
    assert all(c["open_order_exists"] for c in plan2["cells"] if c["shortage"] > 0)


@pytest.mark.asyncio
async def test_gallery_sync_registers_real_assets_without_dupes(client, db):
    pid = await _make_persona(client)
    r = await client.post("/api/v1/platform/accounts", json=_fanvue_payload(pid))
    aid = r.json()["id"]

    r1 = await client.post(f"/api/v1/platform/accounts/{aid}/inventory/sync-gallery")
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["added"] == d1["found"]  # first sync adds everything it finds
    r2 = await client.post(f"/api/v1/platform/accounts/{aid}/inventory/sync-gallery")
    d2 = r2.json()
    assert d2["added"] == 0  # dedupe by asset_key
    assert d2["already_registered"] == d2["found"]

    # Every synced item must be public-tier with honest provenance
    items = await client.get(f"/api/v1/platform/accounts/{aid}/inventory", params={"tier": "public"})
    for it in items.json():
        assert it["tier"] == "public"
        assert it["content_type"] == "image"


@pytest.mark.asyncio
async def test_post_item_and_minimums_math(db):
    pid = uuid4()
    acct = PlatformAccount(
        persona_id=pid, platform="fanvue", handle="x",
        is_ai_disclosed=True, ai_disclosure_text="AI", kyc_status="verified",
        consent_owner="bank",
        target_public_posts_per_day=2.0,   # → public image min = 4
        target_subscriber_posts_per_day=1.5,  # → subscriber image min = 3
        target_premium_items_per_week=3.0,  # → premium video min = 2 (3/2 rounded)
    )
    db.add(acct)
    await db.flush()

    assert pm._min_for("public", "image", acct) == 4
    assert pm._min_for("subscriber", "image", acct) == 3
    assert pm._min_for("premium", "video", acct) == 2
    assert pm._min_for("public", "video", acct) == 1

    item = ContentInventoryItem(
        account_id=acct.id, persona_id=pid, tier="subscriber",
        content_type="image", asset_key="/api/v1/gallery/x.png", status="ready",
    )
    db.add(item)
    await db.flush()

    counts = await pm.inventory_counts(db, acct.id)
    assert counts["ready"]["subscriber"]["image"] == 1

    result = await pm.register_post(db, item.id)
    await db.commit()
    assert result["status"] == "posted"
    counts2 = await pm.inventory_counts(db, acct.id)
    assert counts2["ready"]["subscriber"]["image"] == 0
    assert counts2["posted"]["subscriber"]["image"] == 1


@pytest.mark.asyncio
async def test_is_mock_provenance_surfaces_in_inventory(client):
    pid = await _make_persona(client)
    r = await client.post("/api/v1/platform/accounts", json=_fanvue_payload(pid))
    aid = r.json()["id"]
    r = await client.post(f"/api/v1/platform/accounts/{aid}/inventory", json={
        "tier": "premium", "content_type": "image", "is_mock": True,
        "source_type": "generated", "caption": "mock fallback set",
    })
    assert r.json()["is_mock"] is True
    items = await client.get(f"/api/v1/platform/accounts/{aid}/inventory", params={"tier": "premium"})
    assert any(i["is_mock"] for i in items.json())
