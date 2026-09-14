# Persona Studio — Architecture Vision (v2, 2026-09-13)

Derived from the expanded product architecture agreed with the owner. This
document is the target architecture; `AGENT_HANDOFF.md` tracks which slices of
it are **built and verified** versus **planned**. Nothing here may be claimed
as done until it is live-verified end to end.

## North star

Turn Persona Studio from a content generator into an **autonomous virtual-model
management system**: it watches permitted public market signals, extracts
strategy primitives (never media to copy), proposes measurable experiments for
*its own* original models, creates/QAs/distributes original content, monetizes
on AI-friendly platforms, and learns from measured results.

## Four brains

```text
                    PERSONA STUDIO
                          │
    ┌─────────────────────┼─────────────────────┐
    ▼                     ▼                     ▼
🧠 MODEL              👁 MARKET             💰 PLATFORM
MANAGER               INTELLIGENCE           MANAGER
 knows Ava            watches market         monetizes Ava
 manages Ava          discovers trends       tracks revenue
 remembers Ava        ranks strategies       inventory + funnels
    │                     │                     │
    └─────────────────────┼─────────────────────┘
                          ▼
                   🎬 CONTENT DIRECTOR
                          │
             ┌────────────┼────────────┐
             ▼            ▼            ▼
          IMAGE         VIDEO         COPY
             └────────────┼────────────┘
                          ▼
                     QA / IDENTITY
                          ▼
                       INVENTORY
                          ▼
              ┌───────────┼───────────┐
              ▼           ▼           ▼
           SOCIAL      FANVUE      ONLYFANS*
```

`*` OnlyFans workflows restricted to what the platform's rules permit
(verified-creator requirements; **no crawling/scraping of OnlyFans, ever**).

## System invariants (non-negotiable)

1. **Competitive intelligence, not a copier.** The system never reproduces
   another creator's posts, media, or likeness. It extracts *strategy
   primitives* (cadence, formats, hooks, themes, timing, engagement patterns)
   and converts them into **original strategies for our own synthetic models**.
2. **Permitted sources only.** Crawlers target sources designed for public /
   programmatic consumption (public RSS, public JSON APIs, public web search,
   official APIs) plus **our own linked accounts'** native analytics.
   - **No crawling or scraping of OnlyFans** (platform rules prohibit it; the
     Platform Manager for OnlyFans uses only our own creator account's native
     analytics/exports and permitted workflows).
   - Respect robots.txt, identify honestly in User-Agent, rate-limit, cache.
3. **Fanvue-first monetization.** Fanvue explicitly permits fully AI-generated
   creators with disclosure + KYC. OnlyFans is treated as a restricted
   verified-human-creator surface: kept as a subsystem, but the monetization
   abstraction must not depend on it.
4. **Honesty everywhere** (existing project rule). Blocked/unavailable sources
   are recorded as blocked — never fabricated, never silently dropped. Every
   generated asset keeps `is_mock`/`provider` provenance. Counts are real.
5. **18+ / consent / compliance gate before any generation** in adult
   workflows: identity verification, content-policy classification,
   platform-specific compliance check, provenance records. Explicit adult
   content generation itself stays out of scope for the assistant; the
   orchestration, gating, QA, vault, scheduling and analytics are the product.
6. **OnlyFans Manager is a separate subsystem** from the general Model Manager
   (materially different platform, identity, and automation constraints).

## Learning loop (the product core)

```text
OBSERVE MARKET → DETECT PATTERNS → GENERATE HYPOTHESIS → CREATE ORIGINAL
CONTENT → PUBLISH → MEASURE → COMPARE → UPDATE STRATEGY → REPEAT
```

A hypothesis is a measurable experiment (e.g. casual vs studio allocation),
not a copy instruction. Results update the model's strategy allocation.

## Content tiers (inventory ladder)

- **PUBLIC** — profile images, promos, teasers, acquisition material.
- **SUBSCRIBER** — feed + retention material.
- **PREMIUM** — premium sets, premium video, campaign content.
The Platform Manager computes shortages per tier and orders shoots to fill
them (e.g. "premium video below 7-day minimum → schedule premium shoot").

## Build order (measurable slices, newest first)

1. ✅ **v0 Market Intelligence + Strategy Engine** (2026-09-13): permitted-source
   crawl pipeline (`IntelSourceRun`), observation store (`MarketObservation`),
   pattern extraction, hypothesis store (`StrategyHypothesis`), `/api/v1/intel/*`
   endpoints. Blocked sources (Reddit 403 from this machine) recorded honestly
   as `blocked`, never fabricated. Own-account analytics source pending platform
   links.
2. ✅ **Fanvue Platform Manager v0** (2026-09-13): `PlatformAccount` with
   AI-disclosure/KYC/consent-owner compliance fields (gate, not decoration),
   `ContentInventoryItem` ladder (PUBLIC/SUBSCRIBER/PREMIUM × image/video) with
   honest `is_mock` provenance, gallery→PUBLIC sync (deduped by asset_key),
   shortage-driven `ShootOrder` planner (content-type-aware minimums from the
   account's own cadence targets, deduped against open orders),
   `POST /api/v1/platform/inventory/{item}/post` registration, OnlyFans
   recorded-but-restricted (no automation), `/monetization` UI page.
   Live-verified: compliant account → 1 real gallery asset synced → plan opened
   6 orders (public img ×2, public vid ×1, sub img ×4, sub vid ×1, premium img
   ×5, premium vid ×2) → re-plan deduped → non-compliant account blocked by the
   gate. Not yet built: order→shoot execution wiring, per-post measurement.
3. Content Director: hypothesis-driven shoot briefs (original prompts only),
   tier-aware inventory fill.
4. Platform Manager: inventory shortage detection → shoot scheduling.
5. Measurement: ingest per-post performance from our own accounts into
   StrategyHypothesis results; allocation updates.
6. OnlyFans Manager (restricted mode): own-account analytics ingest, inventory
   ladder, messaging drafts — no crawler, no automation beyond permitted.
7. Compliance gate service: 18+/consent/policy classification ahead of every
   generation request in adult workflows.
