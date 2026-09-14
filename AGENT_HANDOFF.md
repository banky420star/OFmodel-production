# AGENT_HANDOFF.md — Persona Studio development handoff

_Last updated: 2026-09-14, cycle "disk-space relocation: LoRA weights moved to AI_DRIVE"._

## STORAGE LOCATION (2026-09-14)

- The project **code, DB and media stay at `/Users/bank/Downloads/Gemma_Local_Agent_v3_MODEL_FIX`**
  (internal disk) — do not move them; macOS privacy (TCC) blocks launchd-spawned
  processes from reading `/Volumes/AI_DRIVE`, which crash-looped the API agent.
- The **13GB of LoRA training weights** (`apps/api/storage/models/loras`) now live at
  `/Volumes/AI_DRIVE/Gemma_Local_Agent_v3_MODEL_FIX/apps/api/storage/models/loras`
  (byte-verified with cmp on all 8 files). `apps/api/storage/models/loras` is a
  **symlink** to that location. Nothing in `apps/api/app/` references `storage/models` —
  the weights are training artifacts only; generation runs through cloud providers.
- A full pre-move snapshot of the entire project (2026-09-14, excl. `.next` caches)
  is preserved at `/Volumes/AI_DRIVE/Gemma_Local_Agent_v3_MODEL_FIX`.
- Internal disk freed: 15Gi → 28Gi available.
- `com.persona.api` LaunchAgent supervises the API (KeepAlive); the two duplicate
  agents `com.persona-studio.api` / `com.personastudio.api` are DISABLED (they
  collided on port 8001). New `com.persona.web` LaunchAgent supervises the web
  dev server on 3100 (PATH is patched inside the plist: `/Users/bank/.local/bin`)
  because this tool environment reaps non-launchd background processes.
- Verified after relocation: API health ok, shoot image 200 (1.3MB), avatar 200
  (4.5MB), weights reachable through the symlink, web 200 on / and /production,
  65/65 tests pass, web typecheck clean.

## Photo & video production research (2026-09-14)

- Full report: `docs/PHOTO_VIDEO_PRODUCTION_RESEARCH.md`. Headline: the key
  reaches the whole Wan 2.1→2.7 ladder (`wan2.6-t2v`, `wan2.7-t2v`,
  `wan2.5-i2v-preview` all live-probed authorized); i2v with the approved shot
  image as first frame is the next unlock; pricing ≈$0.02–0.03/image,
  $0.04–0.12/s video; disclosure note inside about probe tasks created.

## Always-on social worker + official platform APIs (2026-09-14)

- `apps/api/app/providers/social_apis.py` — official-API clients + capability
  matrix. **Fanvue** (OAuth2, `X-Fanvue-API-Version: 2025-06-26`, validated
  live: garbage token → real 401 AUTH_FAILED), **X** (API v2 bearer),
  **TikTok/Instagram/Facebook** (official but gate-level: NOT_CONFIGURED until
  developer-app approval). **Fansly/OnlyFans: NO official API → no client, by
  policy** (scrapers violate ToS; never built).
- `apps/api/app/social_worker.py` — always-on loop in the API lifespan (next
  to the manager heartbeat, 300s cadence, ≤25 accounts/pass): real profile
  syncs through official APIs only; failures recorded verbatim in
  `metadata_json.last_sync_result`; api_connected reflects reality.
- `apps/api/app/routes/social_worker.py` — /status, /run-once, /connect-api
  (validates against the real platform immediately), /disconnect.
- UI: Socials page worker panel (API OK / NO API badges, credential-source
  tooltips, Run Now), per-account Connect/Disconnect API for fanvue/twitter.
- Live-verified: fansly connect refused; run-once honest no-op + per-account
  ledger; real Fanvue 401 on bad token; 74/74 tests; TS clean.
- NEXT: operator obtains Fanvue OAuth token (creator + KYC) and X bearer →
  first REAL connected-platform sync; then wire approved-content → Fanvue
  post creation through the official API.

## Socials card profile links (2026-09-14)

- `apps/web/src/app/socials/page.tsx` — when a social account has a real
  `profile_url` (only set by a real signup/sync run), its platform icon becomes
  a link to the live profile (new tab, ↗ badge, colored border). No URL →
  plain icon, as before. Live-verified both states in the UI (temp value on
  one account, then reverted — DB again has 0 profile_urls, truthful).
  Backend already persists `profile_url` on full-auto signup success and
  profile sync; no backend change needed.

---

## Previous cycle: mock videos purged; REAL Wan video generation verified end-to-end; Production page compressed (2026-09-13)

## FILES CHANGED (real-video + UI-compression cycle, 2026-09-13)

- `apps/api/app/providers/wan_dashscope.py` — **do not send `duration`** to
  DashScope: wan2.1-t2v-turbo rejects it (`duration customization is not
  supported`, InvalidParameter). Model returns fixed ~5.4s clip.
- `apps/api/app/routes/content.py` — both video routes (persona + shoot) now
  **download the generated file** from the provider's temporary OSS URL into
  `storage/videos/<task_id>.mp4` (previously only the remote link was stored →
  the orphan-video-row bug; 14 rows had no file). Metadata records bytes +
  `is_mock: False`.
- `apps/web/src/app/production/page.tsx` — compressed from ~10+ screens to
  ~1.6: Quick-Produce is one pill row (avatar+name, disabled with reason for
  failed/busy personas), Shoots are a dense 3:4 image-card grid with
  status filter (all/completed/failed/draft), image-count badge, click-to-
  lightbox, videos as compact 9:16 tiles.

## LAST VERIFIED RESULT (real-video cycle)

- Deleted the 4 "teaser" videos — metadata said `"mock": true` (mock-provider
  artifacts; user: "videos look shit" — they were never real).
- **Discovery: the DashScope key 401s on Qwen-Image but WORKS for Wan video.**
  Probed `wan2.1-t2v-turbo` directly: submit→RUNNING→SUCCEEDED in ~80–90s.
- **LIVE end-to-end through the app**: `POST /personas/{Ava}/generate-video`
  → HTTP 200 in 111s → real 720×1280 mp4 (5.37s, 1.77MB) downloaded to
  `storage/videos/e0cf65a7-….mp4` → served `200 video/mp4` via
  `/api/v1/media/videos/…` → renders in the Production Videos grid
  (`duration: 5.366667` confirmed via video element metadata).
- Fixed during verification: missing download block (NameError), and
  `_Path(__file__).parent.parent` resolving to `app/` instead of the API root
  (now `.resolve().parent.parent.parent`). Earlier failure's row was recovered
  from its still-live OSS URL.
- Production page verified: pill Quick-Produce renders all 10 personas in one
  row, status filter works (failed → 1 card), COMPLETED badge bug fixed
  (case mismatch), lightbox opens full image, videos render real Wan output.
  53/53 tests, TypeScript clean.
- **Remaining video-quality gap**: image-to-video (identity-locked, from a
  reference/avatar frame) is the route to consistent-looking persona videos;
  t2v drifts identity. `image_to_video` exists in the provider — next cycle
  can wire "animate this avatar" into the UI.

## FILES CHANGED (demo-data purge cycle, most recent first)

No code changes — data-integrity purge. Backup from previous cycle
(`apps/api/persona_studio.db.bak-cleanup-0913`) predates both cleanups.

## LAST VERIFIED RESULT (demo-data purge cycle, 2026-09-13)

Every remaining row is now real and evidenced:
- **270 analytics_snapshots DELETED** — all dated 2026-06-06→09-08 with
  platform='all', i.e. they predate every persona (created Sep 4+) and no
  account was ever connected: fabricated revenue/engagement history.
- **2 forecasts DELETED** — deterministic_v1 projections derived from those
  fake snapshots ("break_even_month: 1").
- **14 generated_videos rows DELETED** — files missing on disk (Sep 6 demo
  run). The 4 real teaser videos (ava/noor/zara/sasha, Sep 12, files verified)
  remain and render on Production.
- **9 social_accounts DELETED** — belonged to personas deleted last cycle
  (testmodel.*, verify_*, suspended trainerpath.*).
- **4 rejected social_accounts DELETED** — recorded decision variants, nothing
  real behind them.
- **4 demo-era 'active' socials demoted to 'approved'** — created Sep 7 with
  zero signup evidence (no metadata, no session, no run status); the only
  Instagram signups ever really attempted were the 3 suspended trainerpath
  accounts. Real state: identity + inbox approved, platform signup NOT yet
  performed. Socials now shows 0 active / 7 approved / 11 pending_approval.
- **3 orphan identity_locks DELETED** (personas already removed).
- Kept as real: 109 market_observations + intel_source_runs (today's live
  permitted-source crawls), 2 strategy_hypotheses (derived from that real
  intel), 4 content packs (operator-created), failoververify.ai platform
  account + its inventory/orders, 4 real teaser videos, 50 completed shoots
  with verified image files.
- Live UI verification: Overview "Revenue this month R 0 — from analytics"
  (honest), Analytics empty-state with real guidance, Chat 0 fans / R0,
  Socials 0 active, Production 0 generating + 4 real videos. 53/53 tests.

## FILES CHANGED (stale-data cleanup cycle, most recent first)

No code changes this cycle — data-only cleanup + one prior-cycle finding:
legacy shoots stored ABSOLUTE paths in `generated_images` (the images endpoint
only parses relative `storage/shoots/<hex8>/<file>` keys, so those entries
were dead weight). Backup: `apps/api/persona_studio.db.bak-cleanup-0913`.

## LAST VERIFIED RESULT (stale-data cleanup cycle, 2026-09-13)

- **17 stuck GENERATING shoots resolved truthfully**: the 5 with partial
  images on disk → COMPLETED with those images (partial work preserved, e.g.
  `Fashion — Sasha` af44814e recovered 4 images); the 12 truly empty → FAILED.
- **214 orphaned scheduled_posts deleted** (their persona rows no longer
  existed; all were demo-era rows Sep 4–Oct 6, none postable). Calendar now
  honestly shows 0 scheduled posts.
- **7 FAILED test/probe personas deleted** (TestModel, U6 Test Persona,
  Probe_832ea940, Probe_0d47cb8b, E2E_Verify_915, CycleCheck_0913,
  TrainerPath_0913) with their 27 identity candidates. Models list: 9 → real
  personas only.
- **13 shoots normalized**: absolute paths → relative storage keys, deduped.
  Verified badge == endpoint count on every completed shoot.
- Live UI verification: Models (9 real personas, no test stubs), Production
  (0 "Generating/Building", 8 ready + 2 honest build-failed), Calendar
  ("0 scheduled posts"), Sasha Shoots (17 expandable shoots, badge == rendered
  images, all load).
- 53/53 tests pass. DB state: 8 ACTIVE + 2 FAILED personas, 50 COMPLETED /
  22 FAILED / 3 DRAFT shoots, 0 orphan rows in any table.

## FILES CHANGED (shoot-images cycle, most recent first)

- `apps/web/src/lib/api.ts` — added `getShootImages(shootId)` client fn (the
  `GET /shoots/{id}/images` endpoint existed but was never called by the UI).
- `apps/web/src/app/personas/[id]/page.tsx` — ShootsTab rewrite: each completed
  shoot row is now expandable and shows its real image grid via the existing
  images endpoint (with per-shoot lazy fetch, count badge, filename labels,
  full-size lightbox, honest "no images stored" empty state).

## LAST VERIFIED RESULT (shoot-images cycle)

Live in the Preview tab on the real persona page: 8 shoots listed, each with an
honest image count (e.g. "Fashion — Noor — 3 images"), the `generating` shoot
correctly has no expander; clicking a row loads its images via
`GET /shoots/{id}/images` (verified `naturalWidth>0` for all 3 thumbnails),
and the lightbox opens/closes on the full-size image. Screenshot shows the
grid rendering real generated images. 53/53 tests, TypeScript clean. No backend
changes needed — the defect was purely that the Shoots tab never displayed
the images the backend already served.

## FILES CHANGED (Fanvue cycle)

- `apps/api/app/models.py` — `PlatformAccount` (AI-disclosure/KYC/consent_owner
  compliance fields, cadence targets, economics), `ContentInventoryItem`
  (tier × content_type, honest `is_mock` provenance, asset_key), `ShootOrder`
  (shortage order with fulfilment tracking).
- `apps/api/app/platform.py` — **new**: tier minimums (content-type-aware,
  derived from the account's cadence targets), compliance gate, shortage
  planner with open-order dedupe, gallery→PUBLIC sync (deduped), post
  registration, OnlyFans `restricted` policy.
- `apps/api/app/routes/platform.py` — **new**: `POST/GET/PATCH /platform/accounts`,
  `GET/POST /platform/accounts/{id}/inventory`,
  `POST .../inventory/sync-gallery`, `POST /platform/inventory/plan`,
  `GET .../orders`, `POST /platform/inventory/{item}/post`.
- `apps/api/tests/test_platform.py` — **new**: 8 tests (compliance gate lists
  every missing item, OnlyFans restricted, shortage orders + dedupe, gallery
  sync dedupe, minimums math, post flow, mock provenance in inventory).
- `apps/web/src/lib/api.ts` — platform client functions.
- `apps/web/src/app/monetization/page.tsx` — **new** Monetization page:
  account list with compliance badges, detail with compliance card + remediation
  buttons, ladder (ready/posted per tier), gallery-sync + plan buttons, item
  cards with Post action, shoot-order list with reasons.
- `apps/web/src/components/Sidebar.tsx` — Monetization nav entry.

## LAST VERIFIED RESULT (Fanvue cycle)

- Tests: **50/50 passed** (8 new); `npx tsc --noEmit` clean.
- Live on the real API + UI: created compliant account `@failoververify.ai`
  (auto-generated AI-disclosure text) → gallery sync registered the persona's
  1 real avatar as ready PUBLIC inventory → plan opened exactly 6 orders
  (public img ×2, public vid ×1, subscriber img ×4, subscriber vid ×1,
  premium img ×5, premium vid ×2) → re-plan deduped (0 new) → a second,
  non-disclosed account was blocked by the compliance gate listing all 4
  problems → post action moved the public item ready→posted.
  UI verified on :3100/monetization: compliance card with "Planning unlocked",
  ladder counts, and all 6 open orders with reasons.

(Previous cycles: full-auto Instagram signup wired into Socials with live
progress — see git history and INSTAGRAM_STATUS.md; three accounts suspended
on creation by Instagram anti-abuse, recorded honestly.)

## CURRENT OBJECTIVE

**Model Manager + media pipeline (Objective A+B pass) — largely complete and
live-verified. REAL identity-locked image generation now works:** the Wan key
that 401s on qwen-image* was discovered to WORK for `wan2.5-t2i-preview` and
`wan2.5-i2i-preview`. New `WanImageProvider` (t2i + avatar-reference i2i) is
priority-2a in the registry; the manager's GENERATE_IMAGES pipeline sends the
persona's real avatar as the visual reference, so identity is preserved by
construction. Live-verified: Ava and Noor each produced a 6/6-APPROVED shoot
through the manager (832x1040 PNGs, ~1.2MB each, ~55s/shot), QA passed, assets
served through the app and rendered in the Shoots tab.
Remaining owner actions: none for images (key works); Meta-app decision and
Fanvue account creation still open.

## LAST VERIFIED RESULT (real-provider image cycle, 2026-09-13)

- Wan image provider verified live end-to-end through the manager wake loop:
  CREATE_SHOOT → GENERATE_IMAGES (edit_image w/ avatar ref) → 6 shots
  APPROVED (technical+identity QA passed) → GENERATE_VIDEO chained. Served
  via `/api/v1/shoots/{id}/images/{file}` (route now accepts 8-hex prefix
  dirs) and rendered in the UI (Preview tab, all images loaded).
- approve-identity route now promotes the identity to READY when the persona
  has a real avatar (>20KB) — unblocks lock activation via the UI for the six
  real-avatar personas (Ava, Noor, zara, Luna, Mia, Sasha); locks verified
  active.
- Heartbeat loop (45s) runs in lifespan; wakes on due tasks, finished work,
  or inventory shortage; idle managers cost zero provider calls. Restart
  recovery requeues RUNNING tasks (verified live). Provider-unavailable guard
  blocks shoot churn when the image provider is dead (verified) — auto-
  recovers once the provider responds.
- 65/65 tests (12 manager tests added), TypeScript clean, Manager dashboard
  page live at /manager with real controls (pause/resume/run-now/retry/
  autonomy) verified to mutate backend state.

(Prior shoot-images cycle summary, 2026-09-13)

UI-only defect found by live walkthrough, fixed and verified in the Preview
tab: completed shoots listed but never displayed their images — the Shoots tab
rendered only name + status even though `GET /shoots/{id}/images` worked.
ShootsTab now expands each completed shoot into its real image grid (count
badge, lazy per-shoot fetch, filename labels, lightbox, honest empty state).
Verified live: 8 shoots with correct counts, `generating` shoot not expandable,
all thumbnails load (`naturalWidth>0`), lightbox opens/closes. 53/53 tests,
TypeScript clean, no backend changes required.

(Previous cycle, full-auto signup UI wiring — superseded details trimmed):
the button works end-to-end through the real UI:

1. **Wired**: `POST /api/v1/social-accounts/{id}/fullauto-signup` (launches
   `scripts/ig_fullauto_signup.py` detached via the Xcode-Python interpreter —
   the only env with Playwright + chromium-1223) and
   `GET .../fullauto-status` (per-account status file in
   `/tmp/ig_fullauto_status/`, stale-live guard after 15 min).
   `apps/web/src/lib/api.ts`: `startFullAutoSignup` / `getFullAutoSignupStatus`.
   Socials page: `🤖 Full-Auto Sign Up` button on approved/pending Instagram
   accounts with email; green progress panel with live stage, percent, code,
   2 s polling, terminal toast; generic `🚀 Sign Up` retained for other platforms.
2. **Script now reports staged progress** (`IG_FULLAUTO_STATUS_FILE`): launching
   0% → page 5% → filling 10% → submit 30% → polling inbox 40% → code typed 70%
   → waiting session 80% → done/failed 100% with result + screenshot path.
3. **Live verification through the UI**: launched `@trainerpath.studio`
   (`instagram4953@uberip.com`) — full chain ran: form filled → submitted →
   inbox polled → **code typed by the robot** → `sessionid` issued →
   **Instagram suspended the account on creation again** (3rd account, same
   signature `/accounts/suspended/`). Status panel hit done, row honestly
   `suspended`, proofs in `apps/api/storage/signup_proofs/trainerpath.studio_fullauto/`.
4. **Bugs found and fixed during wiring** (all live-found): missing `log_file`
   before `Popen` (500 on launch, left a stuck `fullauto_live` flag → added
   failure-safe rollback that clears the flag); script `str | None` on Py3.9;
   unmatched paren from an edit. Two accounts (`testmodel.style`, `noor.design`)
   briefly stuck `signup_in_progress` from launches during the bug window —
   restored truthfully (no run ever executed; note in `metadata_json`).
5. Tests: **42/42 passed**; `npx tsc --noEmit` clean.

**Standing conclusion (3rd confirmation)**: signup automation is complete and
UI-accessible; Instagram's anti-abuse suspends fresh automated signups from
this environment within seconds. Environment reputation (warmed/residential,
manual-first) or Fanvue-first are the real levers. Do not mass-attempt.

## FILES CHANGED (this cycle)

- `apps/api/app/routes/socials.py` — `fullauto-signup` + `fullauto-status`
  routes; `_FULLAUTO_PY`/`_FULLAUTO_SCRIPT` constants; failure-safe launch.
- `scripts/ig_fullauto_signup.py` — staged `write_status()` progress via
  status-file env var; py3.9 fix.
- `apps/web/src/lib/api.ts` — `startFullAutoSignup`, `getFullAutoSignupStatus`.
- `apps/web/src/app/socials/page.tsx` — Full-Auto button (instagram only),
  live progress panel, polling effect, generic Sign Up fallback for other
  platforms.
- Proof artifacts: `apps/api/storage/signup_proofs/trainerpath.studio_fullauto/`.

## TEST RESULTS

- `apps/api`: **42/42 passed**; web typecheck clean.
- Live: UI button → detached runner → real Instagram run → honest suspended
  record; status endpoint tracked every stage (quoted values are from the
  real run, not mocks).

## FAILED ITEMS / BLOCKERS

- **Instagram suspends fresh automated signups on creation** — three accounts
  in a row (`trainerpath.official`, `trainerpath.assist`, `trainerpath.studio`).
  Not a code problem. Levers: warmed/residential environment, manual signups,
  Fanvue-first.
- `BLOCKED — EXTERNAL PROVIDER`: Ollama Cloud 429 (monthly max). **Unchanged.**
- **DashScope key invalid (401)** — real image/video pending key. **Unchanged.**
- Reddit 403 from host — recorded blocked. **Unchanged.**
- Still open: `?search=` ignored; interrupted builds leave persona `BUILDING`;
  expired DashScope OSS video URLs 403; historical LoRAs on synthetic fallback.

## CURRENT OBJECTIVE (Fanvue Platform Manager v0 — built and verified)

Per architecture build order item 2, Fanvue-first monetization inventory is
live: see ARCHITECTURE_VISION.md build-order entry 2 for the verified scope.

## LAST VERIFIED RESULT (production-walkthrough fix cycle, 2026-09-13)

User asked to see the product at production level; the walkthrough found three
real defects, all fixed and live-verified:

1. **Stuck-BUILDING personas** (8 rows): two fixes — the workflow engine now
   flips a persona out of BUILDING when its build workflow fails (engine.py
   failure branch), and startup recovery repairs historical damage
   (`_repair_stuck_building_personas` in main.py; only personas whose LATEST
   `persona_creation` workflow is FAILED are flipped — active ones with old
   failed attempts stay untouched). Live: all 8 repaired on restart; Production
   page now shows "Build failed — rebuild from Models" instead of a false
   "Building..." (UI ternary made honest per-status).
2. **Ignored `?search=`**: `list_personas` now filters by name (ilike,
   case-insensitive). Live-verified: `?search=Failover` → 1 row.
3. **Demo fan roster**: 18 seeded fans + 30 chat messages (no real inbox
   linkage) purged from the DB; Chat now shows real state (0 fans, R0 revenue)
   until a real connected audience exists.

Tests: **53/53** (3 new regression tests). Typecheck clean.

## DECISIONS RECORDED

- **Instagram warmed-browser/residential-proxy signup automation: rejected**
  (owner request, declined). It functions only as anti-abuse evasion; three
  suspensions confirm platform opposition, not a fixable code gap. Adopted
  instead: `ACCOUNT_LIFECYCLE_DESIGN.md` — human-first signup (one guided
  operator session per account) + official Instagram Graph API operation
  (OAuth, publisher, insights). Awaiting owner decision on maintaining a
  Meta developer app; Studio OAuth/publisher/insights work unblocks on that.

## NEXT EXACT TASK

1. **Order→shoot execution wiring**: fulfil ShootOrders via the existing
   persona shoot pipeline — when an order is queued, create a Shoot for the
   account's persona (tier-appropriate count/type), and on completion register
   produced assets into the matching inventory tier (`units_fulfilled`).
   After that: `POST /platform/inventory/plan` → auto-queued shoots → inventory
   refills → planner stops re-ordering. Close that loop end-to-end.
2. Measurement loop: per-post performance (manual import first) →
   `StrategyHypothesis.result` → allocation updates; also feed platform
   earnings into PlatformAccount.
3. Owner decision on Instagram: manual-only signup per
   `ACCOUNT_LIFECYCLE_DESIGN.md` is the recorded design; stop burning accounts
   on fresh-IP automation.
4. Carry-over: DashScope key replacement (user action) → retrain active
   personas. (Persona-status flip, `?search=`, and shoot-images display are
   all DONE.)

## ENVIRONMENT NOTES (carried forward)

- **Playwright runs only from the Xcode Python 3.9 interpreter**
  (`/Applications/Xcode.app/.../3.9/.../MacOS/Python`; user-site playwright +
  chromium-1223). `apps/api/.venv` has no playwright; system 3.13 mismatches.
- Full-auto runs are detached `subprocess.Popen` children of the API; status
  files in `/tmp/ig_fullauto_status/<account_id>.json`; stdout in
  `/tmp/ig_fullauto_stdout.log`.
- API restart via `osascript` Terminal.app tab (`uvicorn app.main:app --port
  8001`); launchd hits TCC in Downloads. PID via `lsof -nP -iTCP:8001`.
- Verification web instance on :3100 (`launchctl label persona-web-verify`,
  `NEXT_DIST_DIR=.next-verify`); :3000 belongs to another thread — don't touch.
- Real MPS LoRA training: ~60–95 min observed (AGENTS.md's 5–15 min is stale).
- Fast Refresh can invalidate preview uids between snapshot and click.
- mail.tm: fresh codes expire in minutes; robots must re-poll at code-entry
  time (already implemented).
