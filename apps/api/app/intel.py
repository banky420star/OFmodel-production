"""Market Intelligence — permitted-source crawl, pattern extraction, strategy.

Architecture: ARCHITECTURE_VISION.md. Rules encoded here:

- **Permitted public sources only.** Sources that prohibit crawling are never
  crawled (OnlyFans, and any platform we have no permitted programmatic or
  public channel for). Each configured source declares its permitted channel;
  if none exists the source is recorded as `not_implemented` / `blocked` with
  the real reason — never fabricated data.
- **Strategy primitives, not copies.** We keep normalized observations
  (topics, hook styles, cadence signals) — no source media, no likeness data.
- **Hypotheses are experiments for our own models**, never clone instructions.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
import structlog

from app.models import IntelSourceRun, MarketObservation, StrategyHypothesis

logger = structlog.get_logger()

USER_AGENT = "PersonaStudio-Intelligence/0.1 (+https://localhost; public trend research; no media retained)"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_topic(text: str) -> str:
    """Lowercase, collapse whitespace/punct — the dedupe key for signals."""
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def topic_digest(text: str) -> str:
    return hashlib.md5(normalize_topic(text).encode()).hexdigest()


# ─── Source registry ─────────────────────────────────────────────────


@dataclass
class SourceSpec:
    name: str
    permitted: bool  # is there a permitted public/programmatic channel?
    note: str = ""
    crawler: str = ""  # method name on MarketIntelligence


PERMITTED_SOURCES = [
    SourceSpec("google_news_rss", True, "Public RSS, designed for syndication", "crawl_google_news"),
    SourceSpec("hf_public_api", True, "Public models API, intended for programmatic use", "crawl_hf_api"),
    SourceSpec("reddit_public", False, "Public JSON exists but returns 403 from this host; no permission to scrape HTML", "crawl_reddit"),
    SourceSpec("x_public", False, "No permitted free programmatic channel", ""),
    SourceSpec("tiktok_public", False, "No permitted free programmatic channel", ""),
    SourceSpec("onlyfans", False, "Platform prohibits crawling/scraping — hard no, by policy", ""),
]


# ─── Crawlers ────────────────────────────────────────────────────────


@dataclass
class CrawlResult:
    status: str  # ok | blocked | error | not_implemented
    items: list[dict] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


class MarketIntelligence:
    """Continuous observe → extract → hypothesize pipeline (v0)."""

    def __init__(self, http: httpx.AsyncClient | None = None):
        self._http = http or httpx.AsyncClient(
            timeout=15.0,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        self._owns_http = http is None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ── permitted crawlers ───────────────────────────────────────────

    async def crawl_google_news(self, niche: str, query: str) -> CrawlResult:
        """Google News RSS — a public feed intended for syndication."""
        url = (
            "https://news.google.com/rss/search"
            f"?q={quote(query)}"
            "&hl=en-US&gl=US&ceid=US:en"
        )
        try:
            resp = await self._http.get(url)
        except httpx.HTTPError as e:
            return CrawlResult("error", detail={"error": str(e)[:300]})
        if resp.status_code != 200:
            return CrawlResult("blocked", detail={"http_status": resp.status_code})
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            return CrawlResult("error", detail={"parse_error": str(e)[:200]})

        items = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            if not title:
                continue
            hour_utc = None
            for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
                try:
                    hour_utc = datetime.strptime(pub, fmt).hour
                    break
                except ValueError:
                    continue
            items.append(
                {
                    "topic": title,
                    "strategy": {
                        "kind": "news_signal",
                        "published_hour_utc": hour_utc,
                        "source_publisher": (item.findtext("source") or "").strip(),
                    },
                }
            )
            if len(items) >= 40:
                break
        return CrawlResult("ok", items=items, detail={"channel": "rss"})

    async def crawl_hf_api(self, niche: str, query: str) -> CrawlResult:
        """HuggingFace public models API — signals which visual styles/tools
        are gaining traction (a usable proxy for visual-trend momentum)."""
        url = "https://huggingface.co/api/models"
        params = {
            "sort": "downloads",
            "direction": -1,
            "limit": 30,
            # Search by niche — long news-style queries match nothing.
            "search": niche if niche and niche != "general" else "stable diffusion",
        }
        try:
            resp = await self._http.get(url, params=params)
        except httpx.HTTPError as e:
            return CrawlResult("error", detail={"error": str(e)[:300]})
        if resp.status_code != 200:
            return CrawlResult("blocked", detail={"http_status": resp.status_code})
        try:
            data = resp.json()
        except ValueError:
            return CrawlResult("error", detail={"parse_error": "non-json"})

        items = []
        for row in data[:30]:
            mid = row.get("id", "")
            if not mid:
                continue
            items.append(
                {
                    "topic": f"visual tool momentum: {mid}",
                    "strategy": {
                        "kind": "tool_momentum",
                        "downloads": row.get("downloads", 0),
                        "likes": row.get("likes", 0),
                        "trend_score_hint": min(100.0, (row.get("downloads", 0) or 0) / 10000.0),
                    },
                }
            )
        return CrawlResult("ok", items=items, detail={"channel": "public_api"})

    async def crawl_reddit(self, niche: str, query: str) -> CrawlResult:
        """Reddit public JSON — attempted via its permitted public channel;
        when the host blocks it we record the block honestly."""
        url = f"https://www.reddit.com/search.json?q={quote(query)}&limit=25&sort=top&t=day"
        try:
            resp = await self._http.get(url)
        except httpx.HTTPError as e:
            return CrawlResult("error", detail={"error": str(e)[:300]})
        if resp.status_code != 200:
            return CrawlResult("blocked", detail={"http_status": resp.status_code})
        # (Unreachable today from this host; kept for when it unblocks.)
        items = []
        try:
            data = resp.json()
        except ValueError:
            return CrawlResult("error", detail={"parse_error": "non-json"})
        for child in (data.get("data") or {}).get("children") or []:
            d = child.get("data") or {}
            if d.get("title"):
                items.append(
                    {
                        "topic": d["title"],
                        "strategy": {
                            "kind": "community_signal",
                            "score": d.get("score", 0),
                            "num_comments": d.get("num_comments", 0),
                        },
                    }
                )
        return CrawlResult("ok", items=items, detail={"channel": "public_json"})

    # ── orchestration ────────────────────────────────────────────────

    def _crawler_for(self, spec: SourceSpec):
        return getattr(self, spec.crawler) if spec.crawler else None

    async def run_source(self, spec: SourceSpec, niche: str, query: str, db) -> IntelSourceRun:
        """Crawl one source, persist a truthful IntelSourceRun and any
        observations. No fabrication: failures are recorded as failures."""
        run = IntelSourceRun(
            source=spec.name,
            niche=niche,
            query=query[:256],
            status="running",
            started_at=_utcnow(),
        )
        db.add(run)
        await db.flush()

        crawler = self._crawler_for(spec)
        if not spec.permitted or crawler is None:
            run.status = "not_implemented" if spec.permitted else "blocked"
            run.detail = {
                "reason": spec.note,
                "permitted": spec.permitted,
            }
            run.completed_at = _utcnow()
            await db.flush()
            return run

        result = await crawler(niche, query)

        run.status = result.status
        run.items_seen = len(result.items)
        run.detail = result.detail

        if result.status == "ok":
            seen: dict[str, MarketObservation] = {}
            for item in result.items:
                dig = topic_digest(item["topic"])
                obs = seen.get(dig)
                if obs is None:
                    obs = MarketObservation(
                        source_run_id=run.id,
                        source=spec.name,
                        niche=niche,
                        topic_digest=dig,
                        topic=item["topic"][:2000],
                        strategy=item.get("strategy") or {},
                        trend_score=float(item.get("strategy", {}).get("trend_score_hint", 0.0)),
                        signal_count=1,  # set explicitly: column defaults apply at INSERT, not construction
                        observed_at=_utcnow(),
                    )
                    seen[dig] = obs
                else:
                    obs.signal_count = (obs.signal_count or 0) + 1
            for obs in seen.values():
                db.add(obs)
            run.observations_new = len(seen)

        run.completed_at = _utcnow()
        await db.flush()
        return run

    async def observe(self, niches: list[str] | None = None, db=None) -> dict:
        """Run all permitted sources for the given niches (v0: one niche)."""
        niches = niches or ["general"]
        summary = {"runs": [], "observations_new": 0}
        for niche in niches:
            query = f"creator economy {niche} trends" if niche != "general" else "creator economy trends"
            for spec in PERMITTED_SOURCES:
                run = await self.run_source(spec, niche, query, db)
                summary["runs"].append(
                    {
                        "source": run.source,
                        "status": run.status,
                        "items_seen": run.items_seen,
                        "observations_new": run.observations_new,
                        "detail": run.detail,
                    }
                )
                if run.status == "ok":
                    summary["observations_new"] += run.observations_new
        return summary

    # ── patterns + hypotheses ────────────────────────────────────────

    async def extract_patterns(self, niche: str, db) -> list[dict]:
        """Aggregate recent observations into niche-level pattern summaries,
        grouped by strategy *kind* (not by exact primitive values — the whole
        point is that recurring kinds, not identical items, form patterns)."""
        from sqlalchemy import select, func

        since = datetime.now(timezone.utc).replace(hour=0, minute=0)
        result = await db.execute(
            select(MarketObservation.strategy)
            .where(MarketObservation.niche == niche)
            .where(MarketObservation.observed_at >= since)
        )
        by_kind: dict[str, dict] = {}
        for (strategy_json,) in result.all():
            strategy = strategy_json or {}
            kind = strategy.get("kind", "unknown")
            agg = by_kind.setdefault(kind, {"kind": kind, "count": 0, "signals": {}})
            agg["count"] += 1
            for key, value in strategy.items():
                if key == "kind":
                    continue
                if isinstance(value, (int, float)):
                    stats = agg["signals"].setdefault(key, {"sum": 0.0, "n": 0})
                    stats["sum"] += float(value)
                    stats["n"] += 1
        # Order by volume of signal — most active pattern kinds first.
        patterns = sorted(by_kind.values(), key=lambda p: -p["count"])
        for p in patterns:
            p["sample_primitives"] = {
                k: round(v["sum"] / v["n"], 3)
                for k, v in p["signals"].items()
                if v["n"] > 0
            }
            del p["signals"]
        return patterns

    async def generate_hypotheses(self, niche: str, db) -> list[StrategyHypothesis]:
        """Turn recurring patterns into measurable experiments for our models.

        v0 heuristic: for each recurring observation kind with enough signal,
        propose a comparison experiment. Never names external creators; always
        framed as original-strategy allocation for our own personas.
        """
        from sqlalchemy import select

        patterns = await self.extract_patterns(niche, db)
        hypotheses: list[StrategyHypothesis] = []
        for pattern in patterns:
            kind = pattern["kind"]
            if kind == "unknown" or pattern["count"] < 3:
                continue  # not enough signal to be a measurable hypothesis
            dig = hashlib.md5(f"{niche}:{kind}".encode()).hexdigest()

            existing = (
                await db.execute(
                    select(StrategyHypothesis).where(
                        StrategyHypothesis.pattern_digest == dig,
                        StrategyHypothesis.status == "open",
                    )
                )
            ).scalar_one_or_none()
            if existing:
                continue

            experiments = {
                "news_signal": {
                    "statement": (
                        f"Across recent permitted public signals in '{niche}', "
                        f"{pattern['count']} distinct news items cluster around "
                        "creator-economy shifts. Hypothesis: aligning this week's "
                        "posting themes to those shifts lifts engagement vs our "
                        "evergreen themes."
                    ),
                    "experiment": {
                        "metric": "engagement_rate",
                        "variant_a": "theme_aligned_to_signal",
                        "variant_b": "evergreen_theme",
                        "posts_per_variant": 3,
                        "min_sample_per_variant": 3,
                    },
                },
                "tool_momentum": {
                    "statement": (
                        f"Rising visual-tool adoption signals demand for the styles "
                        f"those tools enable. Hypothesis: applying the trending "
                        f"visual treatment (via our own models only) to 3 of Ava's "
                        f"posts lifts saves/clicks vs our current treatment."
                    ),
                    "experiment": {
                        "metric": "saves_per_post",
                        "variant_a": "trending_visual_treatment",
                        "variant_b": "current_treatment",
                        "posts_per_variant": 3,
                        "min_sample_per_variant": 3,
                    },
                },
                "community_signal": {
                    "statement": (
                        f"Community discussion volume around specific topics in "
                        f"'{niche}' indicates audience interest. Hypothesis: posts "
                        f"framed around high-volume community topics outperform "
                        f"generic topics."
                    ),
                    "experiment": {
                        "metric": "engagement_rate",
                        "variant_a": "community_topic_framing",
                        "variant_b": "generic_topic",
                        "posts_per_variant": 3,
                        "min_sample_per_variant": 3,
                    },
                },
            }.get(kind)

            if not experiments:
                continue

            h = StrategyHypothesis(
                persona_id=None,  # v0: niche-level; per-persona when measurement loop lands
                niche=niche,
                status="open",
                statement=experiments["statement"],
                pattern_digest=dig,
                evidence={"pattern": pattern, "sources": ["permitted_public_sources"]},
                experiment=experiments["experiment"],
            )
            db.add(h)
            hypotheses.append(h)
        await db.flush()
        return hypotheses
