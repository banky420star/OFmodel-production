# Autonomous Creator Learning Loop — Implementation Plan

**Date:** 2026-09-17  
**Repository:** `banky420star/OFmodel-production`  
**Base reviewed:** `main@566b39391d50beae5db7eb1e3383033788a199e0`  
**Spec:** `ARCHITECTURE_VISION.md`

## Goal

Turn the existing Persona Studio production stack into a closed, evidence-driven operating loop:

`OBSERVE -> HYPOTHESIZE -> BRIEF -> PRODUCE -> QA -> INVENTORY -> PUBLISH/REGISTER -> MEASURE -> EVALUATE -> UPDATE STRATEGY -> REPEAT`

The implementation must reuse the existing Market Intelligence, `StrategyHypothesis`, Model Manager task ledger, Fanvue inventory ladder, Fan CRM, social worker, and analytics routes. Do **not** build a second job engine, second fan CRM, or parallel platform abstraction.

## Non-negotiable rules

1. Decisions that affect strategy must use real or explicitly operator-entered measurements. Synthetic/random analytics must never feed the learning loop.
2. External market research produces strategy primitives only; never copy a creator's media, likeness, scripts, or identity.
3. Only permitted public/programmatic sources and the operator's own linked accounts may be ingested.
4. OnlyFans remains restricted: own-account exports/manual imports and operator workflows only; no crawler or unauthorized automation.
5. Generated asset provenance (`provider`, `is_mock`, identity source) remains traceable end-to-end.
6. Adult workflows require synthetic-adult/consent/platform compliance gates before production; this plan covers orchestration, classification, QA, storage, measurement, and business logic, not explicit-content generation instructions.
7. Financial metrics are deterministic calculations from recorded transactions/events, not LLM estimates.
8. Strategy updates are bounded and reversible; the system does not make unlimited autonomous changes from a small sample.

---

# Milestone 0 — Synchronize and stabilize the repository first

GitHub `main` is behind the latest local work described in the handoff. Do not build the learning loop on two divergent codebases.

### Task 0.1 — Reconcile local 99-test work with GitHub

**Files to inspect/reconcile:**
- `apps/api/app/main.py`
- `apps/api/app/manager_core.py`
- `apps/api/app/models.py`
- `apps/api/app/config.py`
- `apps/api/app/audit.py` if present locally
- `apps/api/tests/test_recovery_and_audit.py` if present locally
- `README.md`
- `BUILD_STATUS.md`
- `AGENT_HANDOFF.md`

**Required state before continuing:**
- `PLAN_CONTENT` uses `ok, err, validated = validate_decision(decision)` or equivalent correct unpacking.
- Interrupted workflows have one truthful recovery policy rather than manager tasks resuming while older workflows are force-failed without review.
- Adult/private media endpoints are authenticated/authorized and audited.
- Audit records are real persisted events, not response headers claiming logging.
- Full local API test suite is green.

**Test:**
```bash
cd apps/api
pytest -q
```
Expected: current local suite passes with no known regression hidden as “pre-existing.”

**Commit:**
```bash
git add apps/api README.md BUILD_STATUS.md AGENT_HANDOFF.md
git commit -m "stabilize workflow recovery auth audit and manager correctness"
```

### Task 0.2 — Reconcile stale open PRs

Review PRs #1-#5. Merge only changes not already superseded; otherwise close with a note pointing to the replacement commit/PR. In particular preserve useful security work from auth/encryption PRs without blindly merging old branch state.

### Task 0.3 — Add CI gate

**Create:** `.github/workflows/ci.yml`

Jobs:
- backend: install API deps, `pytest -q`
- frontend: install web deps, `npx tsc --noEmit`, `npm run build`
- optional smoke: start API/web and request `/health` + one API route

Do not add browser E2E until the deterministic unit/integration suite is stable.

**Commit:** `ci: gate backend tests and frontend build`

---

# Milestone 1 — Add the minimum persistent data contract for attribution

The closed loop cannot learn until every published/registered piece of content can be traced back to the hypothesis and brief that produced it.

### Task 1.1 — Add models

**Modify:** `apps/api/app/models.py`

Add the following models (names may be adjusted to project conventions, but preserve these relationships):

### `BrandProfile`
One active strategy profile per persona, versioned through `version` and `updated_at`.

Fields:
- `id`
- `persona_id` FK
- `version` int
- `voice` JSON
- `visual_style` JSON
- `content_allocations` JSON
- `motion_preferences` JSON
- `constraints` JSON
- `source` string (`operator`, `learning_engine`)
- `created_at`, `updated_at`

### `CampaignBrief`
The Content Director's durable contract with Production.

Fields:
- `id`
- `persona_id` FK
- `strategy_hypothesis_id` nullable FK
- `platform_account_id` nullable FK
- `shoot_order_id` nullable FK
- `tier` (`public|subscriber|premium`)
- `content_type` (`image|video|mixed`)
- `objective`
- `target_metric`
- `expected_effect` JSON
- `concept` JSON
- `shot_requirements` JSON
- `motion_plan` JSON
- `status` (`draft|approved|queued|producing|completed|cancelled`)
- `shoot_id` nullable FK
- timestamps

### `PublishedContent`
The attribution bridge from inventory/platform posting back to the brief.

Fields:
- `id`
- `persona_id` FK
- `platform_account_id` FK
- `content_inventory_item_id` nullable FK
- `campaign_brief_id` nullable FK
- `strategy_hypothesis_id` nullable FK
- `platform_post_id` string
- `platform` string
- `published_at`
- `provenance` JSON

### `PerformanceSnapshot`
Real measurements only.

Fields:
- `id`
- `published_content_id` FK
- `measured_at`
- `views`, `likes`, `comments`, `shares`, `saves`, `clicks`
- `new_subscribers`
- `subscription_revenue`, `ppv_revenue`, `tips_revenue`
- `production_cost`
- `source` (`fanvue_api`, `x_api`, `manual_import`, `native_export`)
- `is_estimated` default false
- `raw` JSON

### `DirectorRecommendation`
Traceable advice from Content/Fan/Monetization directors.

Fields:
- `id`
- `persona_id` FK
- `director`
- `recommendation_type`
- `evidence` JSON
- `expected_effect` JSON
- `actual_effect` JSON
- `status` (`proposed|approved|rejected|applied|measured`)
- timestamps

**Indexes:** persona/date, hypothesis/status, published-content/date, platform-account/date.

### Task 1.2 — Write model tests first

**Create:** `apps/api/tests/test_learning_models.py`

Tests:
- a CampaignBrief can point to an existing `StrategyHypothesis` and `ShootOrder`.
- PublishedContent can point back to a brief and hypothesis.
- PerformanceSnapshot rejects/flags impossible negative counters at service validation layer.
- only one active BrandProfile version is selected by helper.
- deleting a persona does not orphan impossible attribution rows (choose cascade/set-null explicitly).

**Test command:**
```bash
cd apps/api
pytest -q tests/test_learning_models.py
```

**Commit:** `feat: add closed-loop attribution models`

---

# Milestone 2 — Content Intelligence Director: hypothesis -> original campaign brief

Existing `intel.py` already observes permitted signals and creates `StrategyHypothesis`. Extend it; do not replace it.

### Task 2.1 — Create director service

**Create:** `apps/api/app/directors/__init__.py`  
**Create:** `apps/api/app/directors/content_intelligence.py`

Public interface:
```python
async def rank_opportunities(db, persona_id: UUID) -> list[dict]: ...
async def create_campaign_brief(
    db,
    persona_id: UUID,
    *,
    hypothesis_id: UUID | None = None,
    shoot_order_id: UUID | None = None,
) -> CampaignBrief: ...
```

`rank_opportunities()` combines:
- open `StrategyHypothesis` evidence
- current BrandProfile
- real prior `PerformanceSnapshot` data
- PlatformAccount tier shortages / `ShootOrder`
- current content inventory

Scoring must be deterministic and inspectable. Suggested v1 score:
- inventory urgency: 0-40
- evidence strength/sample: 0-25
- own historical performance: 0-25
- freshness: 0-10

Return the component scores, not only the total.

`create_campaign_brief()` must transform strategy primitives into an original persona-specific concept. It may use the configured LLM for structured creative fields, but the result must validate against a Pydantic schema and must preserve:
- persona BrandProfile
- requested tier/content type
- identity constraints
- evidence/hypothesis link
- originality rule: no creator names/media/likeness references in generation instructions

### Task 2.2 — Add routes

**Create:** `apps/api/app/routes/content_intelligence.py`

Endpoints:
- `GET /content-intelligence/personas/{persona_id}/opportunities`
- `GET /content-intelligence/personas/{persona_id}/briefs`
- `POST /content-intelligence/personas/{persona_id}/briefs`
- `POST /content-intelligence/briefs/{brief_id}/approve`

**Modify:** `apps/api/app/routes/__init__.py` to register router.

### Task 2.3 — Tests first

**Create:** `apps/api/tests/test_content_intelligence.py`

Acceptance tests:
1. inventory shortage outranks a non-urgent hypothesis when all else equal.
2. a hypothesis with no evidence/sample cannot be promoted as high confidence.
3. a generated brief contains persona/tier/metric/provenance links.
4. source creator names supplied in raw evidence never appear in a generation brief.
5. repeated calls do not mint duplicate active briefs for the same open order.

**Commit:** `feat: add content intelligence director and campaign briefs`

---

# Milestone 3 — Wire CampaignBrief into the existing Model Manager / Production Director

Do not create another queue. `ManagerTask` remains the durable execution substrate.

### Task 3.1 — Extend manager task contract

**Modify:** `apps/api/app/manager_core.py`

Add one task type:
- `EXECUTE_BRIEF`

Implement:
```python
async def _execute_campaign_brief(db, mgr, persona, task) -> dict:
    # validate approved brief
    # create shoot from brief
    # persist brief.shoot_id
    # chain into existing GENERATE_IMAGES / GENERATE_VIDEO / RUN_MEDIA_QA
```

`_pipeline_create_shoot()` should accept brief-derived production inputs rather than inventing generic lifestyle fields when a `brief_id` exists.

Mapping:
- `CampaignBrief.shot_requirements` -> ShotPlan fields
- `CampaignBrief.motion_plan` -> ShotPlan.motion_prompt / video motion plan
- `CampaignBrief.tier` -> inventory tier metadata
- `CampaignBrief.objective` + target metric -> shoot/content pack metadata

Keep the old generic manual flow working when there is no `brief_id`.

### Task 3.2 — Order-to-production wiring

**Modify:** `apps/api/app/platform.py`

Add:
```python
async def queue_order_for_production(db, order_id: UUID) -> dict: ...
async def register_approved_shoot_inventory(db, shoot_id: UUID) -> dict: ...
```

Flow:
`ShootOrder(open) -> CampaignBrief -> ManagerTask(EXECUTE_BRIEF) -> Shoot -> QA -> ContentInventoryItem -> ShootOrder fulfilled`

After media QA, register approved assets into `ContentInventoryItem` with the order's tier and mark fulfilment counts truthfully.

### Task 3.3 — Tests

**Modify/Create:**
- `apps/api/tests/test_manager.py`
- `apps/api/tests/test_platform.py`
- `apps/api/tests/test_campaign_execution.py`

Acceptance:
- one open ShootOrder creates one brief and one manager execution chain.
- retry does not duplicate the shoot or inventory items.
- only QA-approved assets fulfil the order.
- failed video does not falsely fulfil a video order with an image.
- brief provenance reaches ContentInventoryItem metadata.

**Commit:** `feat: execute campaign briefs through production ledger`

---

# Milestone 4 — Measurement: replace synthetic analytics as strategy input

The current `analytics/generate` path produces random/pseudo metrics. It can remain as demo tooling only if clearly labeled, but it must be excluded from all learning and monetization decisions.

### Task 4.1 — Add measurement service

**Create:** `apps/api/app/measurement.py`

Public interface:
```python
async def record_snapshot(db, published_content_id, metrics, source) -> PerformanceSnapshot: ...
async def aggregate_campaign(db, brief_id) -> dict: ...
async def aggregate_hypothesis(db, hypothesis_id) -> dict: ...
```

Hard rule: `source` must be one of the trusted measurement sources; demo/generated analytics are rejected.

### Task 4.2 — Add measurement routes

**Create:** `apps/api/app/routes/measurement.py`

Endpoints:
- `POST /measurement/published-content`
- `POST /measurement/{published_content_id}/snapshot`
- `GET /measurement/personas/{persona_id}`
- `GET /measurement/briefs/{brief_id}`
- `POST /measurement/import`

`/measurement/import` accepts operator-owned/native export data with validation and an import ledger/hash so the same file cannot double-count revenue.

### Task 4.3 — Extend social worker for own-account metrics

**Modify:**
- `apps/api/app/social_worker.py`
- `apps/api/app/providers/social_apis.py`

For platforms whose official API exposes post metrics, sync them into `PerformanceSnapshot`. If the official client cannot supply a metric, store null/absent, not zero unless zero is a real API value.

### Task 4.4 — Quarantine synthetic analytics

**Modify:** `apps/api/app/routes/analytics.py`

- Rename/label synthetic generator response as demo/simulated.
- Add `is_estimated`/source semantics if keeping it.
- Never read those rows in learning-engine functions.
- Prefer manual real entry or linked-account sync for production dashboards.

### Task 4.5 — Tests

**Create:** `apps/api/tests/test_measurement.py`

Acceptance:
- same import cannot be counted twice.
- real API snapshot can be aggregated to a brief/hypothesis.
- `is_estimated=True` snapshots are excluded from learning calculations.
- missing revenue is not invented from likes/views.
- attribution fails loudly if a published item cannot be traced to a brief when evaluation requires it.

**Commit:** `feat: add real performance measurement and attribution`

---

# Milestone 5 — Learning Engine: measured results -> bounded BrandProfile updates

### Task 5.1 — Add learning service

**Create:** `apps/api/app/directors/learning.py`

Public interface:
```python
async def evaluate_hypothesis(db, hypothesis_id: UUID) -> dict: ...
async def update_brand_allocation(db, persona_id: UUID, evaluation: dict) -> BrandProfile: ...
```

Evaluation rules v1:
- require the hypothesis' declared `min_sample_per_variant`.
- compare the declared target metric only.
- report sample counts, means/medians as appropriate, lift, and data window.
- status is `insufficient_data`, `neutral`, `variant_a_better`, or `variant_b_better`.
- do not claim statistical certainty the calculation did not establish.

Strategy update rules:
- max allocation movement: ±10 percentage points per evaluation cycle.
- reserve at least 20% exploration unless operator config changes it.
- never change identity attributes.
- never modify platform pricing from an engagement-only experiment.
- every update creates a new BrandProfile version with before/after evidence.

### Task 5.2 — Integrate StrategyHypothesis results

**Modify:** `apps/api/app/intel.py` and/or route layer.

Add:
- `POST /intel/hypotheses/{id}/evaluate`
- persist structured result on `StrategyHypothesis.result`
- close/completed status only when sample threshold met

### Task 5.3 — Tests

**Create:** `apps/api/tests/test_learning_engine.py`

Acceptance:
- insufficient sample produces no BrandProfile change.
- 30% measured lift results in at most the configured 10pp allocation change.
- a negative result can reverse a previous allocation.
- estimated/demo snapshots do not change strategy.
- same evaluation is idempotent for the same data cutoff.

**Commit:** `feat: close the experiment learning loop`

---

# Milestone 6 — Monetization Director with deterministic economics

### Task 6.1 — Add revenue/economics service

**Create:** `apps/api/app/directors/monetization.py`

Compute from persisted real events/snapshots:
- subscription revenue
- PPV revenue
- tip revenue
- gross revenue
- platform fees where known/configured
- production cost
- contribution margin
- ARPU
- fan LTV (document formula/version)
- churn/renewal rate where data exists
- campaign revenue and ROI

No “like = R0.02” style inferred revenue in production economics.

### Task 6.2 — Recommendations

Produce `DirectorRecommendation` rows such as:
- adjust production allocation toward a measured high-ROI format
- reduce a repeated low-conversion content category
- test a price/offer variant

Guardrails:
- recommendations are proposals by default.
- pricing/account changes require approval unless a future explicit policy enables bounded automation.
- recommendation contains the exact evidence rows/metric window.

### Task 6.3 — Routes/UI contract

**Create:** `apps/api/app/routes/monetization_director.py`

Endpoints:
- `GET /monetization/personas/{id}/summary`
- `GET /monetization/personas/{id}/recommendations`
- `POST /monetization/recommendations/{id}/approve`
- `POST /monetization/personas/{id}/analyze`

**Commit:** `feat: add deterministic monetization director`

---

# Milestone 7 — Fan Relationship Manager using the existing Fan CRM

Do not introduce a second fan database.

### Task 7.1 — Add fan intelligence service

**Create:** `apps/api/app/directors/fan_relationship.py`

Derive segments from existing `Fan` rows:
- NEW
- ACTIVE
- PPV_BUYER
- HIGH_VALUE
- VIP
- RENEWAL_RISK
- LAPSED

Make thresholds explicit/configurable and return reasons for classification.

Public interface:
```python
def classify_fan(fan, now) -> FanSegmentResult: ...
async def segment_summary(db, persona_id) -> dict: ...
async def recommend_fan_actions(db, persona_id) -> list[DirectorRecommendation]: ...
```

No silent mass send. Recommended fan actions remain drafts/operator-approved until an official platform API and policy permit sending.

### Task 7.2 — Routes/tests

**Create:** `apps/api/app/routes/fan_relationship.py`  
**Create:** `apps/api/tests/test_fan_relationship.py`

Acceptance:
- deterministic segment assignment.
- one fan can have primary segment plus supporting tags, but only one primary lifecycle state.
- churn-risk logic uses real inactivity/renewal data, not random scores.
- recommendations cite fan counts and revenue exposure.

**Commit:** `feat: add fan relationship intelligence`

---

# Milestone 8 — Always-on orchestration without another daemon

Use the existing FastAPI lifespan loops and `ManagerTask` ledger.

### Task 8.1 — Add durable task types

**Modify:** `apps/api/app/manager_core.py`

Add only tasks that belong to a persona manager:
- `PLAN_CAMPAIGN`
- `EXECUTE_BRIEF`
- `SYNC_PERFORMANCE`
- `EVALUATE_EXPERIMENT`
- `ANALYZE_REVENUE`

Wire each to a real service. Remove corresponding `NotImplementedError` only when the implementation exists and tests pass.

### Task 8.2 — Cadence

**Modify:** `apps/api/app/main.py` or a small scheduler module imported by lifespan.

Suggested v1 cadence:
- every 15 min: eligible own-account measurement sync
- every 30 min: inventory shortage + open brief check
- daily: evaluate mature hypotheses + create monetization recommendations

Each job must be idempotent and use persisted last-run/data-cutoff timestamps. A restart must not run the daily evaluation 20 times.

### Task 8.3 — Tests

**Create:** `apps/api/tests/test_autonomous_loop.py`

Simulate a complete deterministic loop using mocks for external providers:
1. create persona/BrandProfile
2. seed permitted market observation/hypothesis
3. create platform shortage
4. generate brief
5. execute mock production
6. register inventory and publish attribution
7. insert real-marked PerformanceSnapshots
8. evaluate hypothesis
9. assert bounded BrandProfile update
10. rerun heartbeat and assert no duplicate production/evaluation

**Commit:** `feat: orchestrate autonomous learning cycle`

---

# Milestone 9 — Frontend control surfaces

### Task 9.1 — Intelligence page

**Create:** `apps/web/src/app/intelligence/page.tsx`

Panels:
- source health
- current patterns
- open hypotheses
- ranked opportunities
- CampaignBriefs
- experiment status and measured results

### Task 9.2 — Expand Monetization page

**Modify:** `apps/web/src/app/monetization/page.tsx`

Add:
- revenue by source
- contribution margin
- campaign ROI
- ARPU/LTV/churn when available
- recommendations with evidence + approval status
- preserve current compliance/inventory ladder

### Task 9.3 — Fan intelligence

Prefer extending `apps/web/src/app/chat/page.tsx` with segment filters and fan intelligence, or create `/fans` only if the Chat page becomes overloaded.

### Task 9.4 — Manager dashboard

**Modify:** `apps/web/src/app/manager/page.tsx`

Add director status cards:
- Content Intelligence
- Production
- Fan Relationship
- Monetization

Each card displays actual last run, next work, blocker, and evidence count from the backend; no decorative “ACTIVE” labels without backend state.

### Task 9.5 — API client/types/sidebar

**Modify:**
- `apps/web/src/lib/api.ts`
- `apps/web/src/lib/types.ts`
- `apps/web/src/components/Sidebar.tsx`

Add `Intelligence` navigation. Add typed API contracts for Briefs, PerformanceSnapshots, Recommendations and Director status.

**Verification:**
```bash
cd apps/web
npx tsc --noEmit
npm run build
```

**Commit:** `feat: add intelligence and closed-loop management UI`

---

# Milestone 10 — Production acceptance test and release gate

### Required end-to-end acceptance scenario

Run one persona through the loop with no fabricated business data:

1. Market Intelligence records a permitted signal.
2. A measurable `StrategyHypothesis` exists.
3. Platform inventory shows a real shortage.
4. Content Intelligence creates an original CampaignBrief linked to both evidence and shortage.
5. Operator approves the brief (or allowed autonomy level does so according to policy).
6. Existing manager ledger produces/QA-gates assets.
7. Approved assets enter the correct PUBLIC/SUBSCRIBER/PREMIUM inventory tier.
8. A publish/registration event creates `PublishedContent` attribution.
9. Official own-account analytics or a validated native/manual import creates `PerformanceSnapshot` rows.
10. Learning Engine evaluates the hypothesis only after its sample threshold.
11. BrandProfile changes by no more than the bounded limit.
12. Monetization Director calculates real economics and writes a traceable recommendation.
13. A restart in the middle of steps 4-10 resumes/idempotently completes without duplicate shoots, revenue, or evaluations.

### Final commands

```bash
cd apps/api
pytest -q

cd ../web
npx tsc --noEmit
npm run build
```

Then perform a live app smoke test of:
- `/manager`
- `/intelligence`
- `/monetization`
- `/chat` (or `/fans`)
- API `/health`

Only after this should the project claim the autonomous learning loop is complete.

---

# Recommended implementation order / PR boundaries

Use small PRs in this order:

1. `stabilize-main-and-ci`
2. `learning-data-contracts`
3. `content-intelligence-briefs`
4. `brief-to-production-wiring`
5. `real-measurement-attribution`
6. `learning-engine`
7. `monetization-director`
8. `fan-relationship-director`
9. `autonomous-orchestration`
10. `closed-loop-ui`

Every PR must include tests for its contract and must not claim downstream stages are complete before they are live-verified.

## Architectural result

When this plan is finished, the app's data path is:

```text
PERMITTED MARKET SIGNALS              OWN ACCOUNT / FAN DATA
          |                                   |
          v                                   v
  StrategyHypothesis                   PerformanceSnapshot
          |                                   |
          +------------+----------------------+
                       v
              Content Intelligence
                       |
                 CampaignBrief
                       |
               Model Manager Ledger
                       |
          Image / Video / QA / Pack
                       |
               Tiered Inventory
                       |
              Publish / Register
                       |
                 Measurement
                       |
             Hypothesis Evaluation
                       |
        +--------------+---------------+
        v                              v
   BrandProfile                 Monetization Director
        |                              |
        +--------------+---------------+
                       v
                 NEXT CAMPAIGN
```

That is the smallest architecture that turns the current production system into the autonomous, measurable creator-management loop described in `ARCHITECTURE_VISION.md` without discarding the systems already built.