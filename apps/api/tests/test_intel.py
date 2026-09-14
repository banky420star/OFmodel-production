"""Market Intelligence v0 — permitted sources, honest failures, experiments.

Invariants under test (ARCHITECTURE_VISION.md):
- permitted public sources only; prohibited platforms never crawled
- blocked/failed crawls are recorded truthfully, never fabricated
- observations are strategy primitives (no source media, no likenesses)
- hypotheses are measurable experiments for our own models, not copy orders
"""

import pytest
import uuid

from app.intel import (
    CrawlResult,
    MarketIntelligence,
    PERMITTED_SOURCES,
    normalize_topic,
    topic_digest,
)
from app.models import IntelSourceRun, MarketObservation, StrategyHypothesis


def _uniq(name: str) -> str:
    return f"{name}_{uuid.uuid4().hex[:8]}"


# ─── policy invariants ───────────────────────────────────────────────


def test_prohibited_platforms_are_not_permitted():
    by_name = {s.name: s for s in PERMITTED_SOURCES}
    assert by_name["onlyfans"].permitted is False
    assert "crawl" not in (by_name["onlyfans"].crawler or "")


def test_source_registry_declares_permission():
    by_name = {s.name: s for s in PERMITTED_SOURCES}
    assert by_name["google_news_rss"].permitted is True
    assert by_name["hf_public_api"].permitted is True
    assert by_name["x_public"].permitted is False
    assert by_name["tiktok_public"].permitted is False


# ─── normalization ───────────────────────────────────────────────────


def test_normalize_topic_collapses_noise():
    assert normalize_topic("Hello,  World!!  — Test") == "hello world test"


def test_topic_digest_is_stable():
    assert topic_digest("Same Topic") == topic_digest("same  topic!")


# ─── crawler behaviour (network-free, deterministic) ─────────────────


@pytest.mark.asyncio
async def test_google_news_crawler_parses_rss(monkeypatch):
    mi = MarketIntelligence()

    rss = b"""<?xml version="1.0"?>
    <rss><channel>
      <item><title>Casual creator content outperforms studio shots</title>
        <pubDate>Fri, 12 Sep 2026 20:15:00 GMT</pubDate>
        <source>Ad Age</source></item>
      <item><title>Casual creator content outperforms studio shots</title>
        <pubDate>Fri, 12 Sep 2026 21:00:00 GMT</pubDate></item>
    </channel></rss>"""

    class FakeResp:
        status_code = 200
        content = rss

    async def fake_get(url, **kwargs):
        return FakeResp()

    monkeypatch.setattr(mi._http, "get", fake_get)
    result = await mi.crawl_google_news("general", "creator trends")
    mi._owns_http = False
    await mi.aclose()

    assert result.status == "ok"
    assert len(result.items) == 2
    assert result.items[0]["strategy"]["published_hour_utc"] == 20
    assert "studio shots" in result.items[0]["topic"].lower()


@pytest.mark.asyncio
async def test_crawler_records_block_honestly():
    """A 403 from a source is recorded as blocked — never fabricated."""
    mi = MarketIntelligence()

    async def fake_get(url, **kwargs):
        class R:
            status_code = 403

        return R()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mi._http, "get", fake_get)
    result = await mi.crawl_reddit("general", "fitness")
    monkeypatch.undo()
    mi._owns_http = False
    await mi.aclose()

    assert result.status == "blocked"
    assert result.detail == {"http_status": 403}
    assert result.items == []


@pytest.mark.asyncio
async def test_not_permitted_source_is_never_crawled(db):
    """x_public has no permitted channel: run_source must not fetch at all."""
    mi = MarketIntelligence()
    called = {"n": 0}

    async def fake_get(url, **kwargs):
        called["n"] += 1
        raise AssertionError("crawler must not fetch a non-permitted source")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mi._http, "get", fake_get)
    spec = next(s for s in PERMITTED_SOURCES if s.name == "x_public")
    run = await mi.run_source(spec, "general", "query", db)
    monkeypatch.undo()
    mi._owns_http = False
    await mi.aclose()

    assert called["n"] == 0
    assert run.status == "blocked"  # policy-blocked: no permitted channel
    assert run.detail["permitted"] is False
    assert run.detail["reason"]  # the real reason is recorded


@pytest.mark.asyncio
async def test_run_source_dedupes_and_counts(db):
    """Duplicate topics collapse into one observation with signal_count > 1."""
    mi = MarketIntelligence()
    spec = next(s for s in PERMITTED_SOURCES if s.name == "google_news_rss")

    async def fake_crawl(niche, query):
        return CrawlResult(
            "ok",
            items=[
                {"topic": "Same headline", "strategy": {"kind": "news_signal"}},
                {"topic": "Same headline!", "strategy": {"kind": "news_signal"}},
                {"topic": "Other headline", "strategy": {"kind": "news_signal"}},
            ],
            detail={"channel": "rss"},
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mi, "crawl_google_news", fake_crawl)
    run = await mi.run_source(spec, "general", "q", db)
    monkeypatch.undo()

    obs = (
        await db.execute(
            MarketObservation.__table__.select().where(
                MarketObservation.source_run_id == run.id
            )
        )
    ).fetchall()
    mi._owns_http = False
    await mi.aclose()

    assert run.status == "ok"
    assert run.observations_new == 2
    assert len(obs) == 2
    counts = sorted(o.signal_count for o in obs)
    assert counts == [1, 2]


# ─── hypotheses ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hypotheses_require_minimum_signal(db):
    """Fewer than 3 signals per pattern → no hypothesis (no noise)."""
    mi = MarketIntelligence()
    from datetime import datetime, timezone

    db.add(
        MarketObservation(
            niche="general",
            topic_digest=topic_digest("t1"),
            topic="t1",
            strategy={"kind": "news_signal"},
            observed_at=datetime.now(timezone.utc),
        )
    )
    await db.flush()

    created = await mi.generate_hypotheses("general", db)
    mi._owns_http = False
    await mi.aclose()
    assert created == []


@pytest.mark.asyncio
async def test_hypotheses_are_experiments_not_copies(db):
    """Hypotheses must reference experiment design, never external creators."""
    mi = MarketIntelligence()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    for i in range(4):
        db.add(
            MarketObservation(
                niche="general",
                topic_digest=topic_digest(f"signal {i}"),
                topic=f"signal {i}",
                strategy={"kind": "news_signal"},
                observed_at=now,
            )
        )
    await db.flush()

    created = await mi.generate_hypotheses("general", db)
    mi._owns_http = False
    await mi.aclose()

    assert len(created) >= 1
    for h in created:
        assert h.status == "open"
        assert h.experiment.get("variant_a") and h.experiment.get("variant_b")
        assert h.experiment.get("min_sample_per_variant", 0) >= 1
        blob = (h.statement + " " + str(h.experiment)).lower()
        for banned in ("copy", "clone", "@"):
            assert banned not in blob.replace("clone instructions", "")


@pytest.mark.asyncio
async def test_hypothesis_dedupe_by_pattern(db):
    """A second generation run must not duplicate an open hypothesis."""
    mi = MarketIntelligence()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    for i in range(4):
        db.add(
            MarketObservation(
                niche="general",
                topic_digest=topic_digest(f"sig {i}"),
                topic=f"sig {i}",
                strategy={"kind": "news_signal"},
                observed_at=now,
            )
        )
    await db.flush()

    first = await mi.generate_hypotheses("general", db)
    # Same pattern again — should be skipped as duplicate
    second = await mi.generate_hypotheses("general", db)
    mi._owns_http = False
    await mi.aclose()

    assert len(first) == 1
    assert second == [] or all(h.pattern_digest != first[0].pattern_digest for h in second)


# ─── API surface ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_intel_sources_endpoint(client):
    resp = await client.get("/api/v1/intel/sources")
    assert resp.status_code == 200
    data = resp.json()
    names = {s["name"] for s in data["sources"]}
    assert "onlyfans" in names
    onlyfans = next(s for s in data["sources"] if s["name"] == "onlyfans")
    assert onlyfans["permitted"] is False


@pytest.mark.asyncio
async def test_intel_runs_and_observations_endpoints(client):
    resp = await client.get("/api/v1/intel/runs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = await client.get("/api/v1/intel/observations")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
