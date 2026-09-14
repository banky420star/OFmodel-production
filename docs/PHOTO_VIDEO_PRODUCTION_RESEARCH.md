# Photo & Video Production — Research Report (2026-09-14)

_What the configured DashScope key can actually do, what it costs, and the
recommended production architecture. Model IDs marked "live probe" were tested
against this key on 2026-09-14; specs are from official Alibaba Cloud Model
Studio docs (text-to-video + image-to-video API references, retrieved 2026-09-14)._

---

## 1. Current pipeline state (verified in codebase)

| Stage | Provider / Model | Status | Gap |
|---|---|---|---|
| Text→Image | `wan2.5-t2i-preview` | ✅ real, verified (6/6 QA-approved shoots for Ava & Noor) | no reference conditioning |
| Identity→Image | `wan2.5-i2i-preview` (avatar as reference) | ✅ wired in manager `GENERATE_IMAGES` | — |
| Text→Video | `wan2.1-t2v-turbo` | ✅ real, verified (5.37s, 720×1280) | fixed ~5s, 720p max, no identity |
| Image→Video | `wan2.1-i2v-turbo` (defined, not yet identity-wired) | ⚠️ defined in `wan_dashscope.py` | **the single biggest unlock (§6)** |
| Video file handling | routes download from provider OSS → `storage/videos/` | ✅ fixed (was the orphan-row bug) | — |
| Local LoRA weights (13GB, `storage/models/loras`) | none — no local GPU inference wired | ⚠️ inert training artifacts | now symlinked to AI_DRIVE; cloud path is the production path |

## 2. Live-probe results on this key (2026-09-14)

Zero-cost intended probes (empty payloads) — the API **accepted** them and
created real async tasks instead of rejecting, so verdicts come from
acceptance vs. explicit `Model not exist`:

| Model ID | Result |
|---|---|
| `wan2.1-t2v-turbo` | task accepted (baseline, known working) |
| `wan2.5-i2v-preview` | task accepted — **identity-locked i2v is available** |
| `wan2.6-t2v` | task accepted — 2.6 t2v authorized on this key |
| `wan2.7-t2v` | task accepted — **latest-gen t2v authorized on this key** |
| `wan2.6-i2v-plus` | `Model not exist` (wrong guess; correct IDs: `wan2.6-i2v-flash`, `wan2.6-i2v`) |
| `qwen-image*` | 401 `InvalidApiKey` (from earlier cycles — dead on this key) |

**Conclusion: the key reaches the Wan 2.1 → 2.7 generation ladder for both
image and video.** The codebase only uses 2.1 for video today.

## 3. Model spec summary (official docs)

### Image-to-video (identity path — uses a real image as FIRST FRAME)

| Model | Resolutions | Durations | Notes |
|---|---|---|---|
| `wan2.1-i2v-turbo` | 480P, 720P | 3/4/5s (fixed-ish) | current default |
| `wan2.5-i2v-preview` | 480P, 720P, 1080P | 5, 10s | ≤20MB image, prompt ≤1500 chars |
| `wan2.6-i2v-flash` | 720P, 1080P | **2–15s** | **audio generation (BGM/SFX)**, `audio_url` input, `shot_type` single/multi |
| `wan2.6-i2v` / `wan2.6-i2v-us` | 720P, 1080P | 2–15s / 5,10,15 | premium tier |
| `wan2.7` i2v | — | — | **first-frame + last-frame + video continuation** (doc note); recommended by Alibaba |

Input: `img_url` = public URL **or base64 data-URL** (`data:image/png;base64,…`)
→ the Studio can send approved shot images directly from local storage.
Aspect ratio of output follows the input image's ratio (portrait shots → 9:16).

### Text-to-video

| Model | Resolutions | Durations | Notes |
|---|---|---|---|
| `wan2.1-t2v-turbo` | 480P/720P | fixed ~5s | current default |
| `wan2.7-t2v` (`wan2.7-t2v-2026-06-12`) | 720P, 1080P (default **1080P**) | **2–30s** | `ratio` param (16:9/9:16/1:1/4:3/3:4), native **audio/dubbing**, multi-shot narrative prompting, prompt ≤5000 chars |

### Common parameters (all)
`prompt_extend` (LLM prompt rewrite, default true), `negative_prompt`
(≤500 chars), `watermark` (AI label, default **false** — keep false; AI
disclosure handled at platform level per Fanvue rules), async task pattern
(`X-DashScope-Async: enable`, poll `/api/v1/tasks/{task_id}`, 1–5 min typical).

## 4. Pricing (indicative — verify in Model Studio console before scale)

Billing is **per second of video** and per image. Third-party published rates
for the Wan series (Netmind, Evolink, 2026):

- Video i2v/t2v: ≈ **$0.04/s (480P) · $0.08/s (720P) · $0.12/s (1080P)**
- Image generation (Wan image family): ≈ **$0.02–0.03 / image**
- Audio-enabled video costs more than silent
- Alibaba's own console pricing is the source of truth; tiered/region pricing
  may differ (Singapore vs Beijing workspaces)

### Production cost math (per persona-week at current targets)

| Scenario | Compute | Est. cost |
|---|---|---|
| Status quo: 6 images + 1×wan2.1 t2v clip | 6×$0.025 + 5s×$0.08 | **≈ $0.55** |
| Target: 30 images + 7×i2v 10s @720P | 30×$0.025 + 70s×$0.08 | **≈ $6.35** |
| Premium: same @1080P + audio | 30×$0.025 + 70s×$0.12 ×~1.3 | **≈ $11.90** |

Roughly **$25–50/month per actively producing persona** at the platform
manager's cadence targets. QA-rejection retries (bounded 3×) add ~20–30%.

## 5. Incidents & honesty notes from this research

1. **Probe side effect (disclosed):** the four "empty payload" probes created
   real async tasks (ids captured truncated in output). The i2v one should
   fail async validation (`img_url` required); the t2v ones may produce billed
   clips. Worst case ≈ 4×5s@720P ≈ **$1.60**; likely less. Lesson recorded:
   DashScope validates *after* task creation — never probe with empty bodies.
2. Earlier cycles' orphan-video bug (routes stored only the OSS link) is fixed;
   all video rows now have real local files.

## 6. Recommended production architecture (priority order)

1. **Identity-locked video (the unlock):** wire `wan2.5-i2v-preview` →
   `wan2.6-i2v-flash` in the manager's `GENERATE_VIDEO` stage using the
   **approved shot image as `img_url` (base64 data-URL)**. Face consistency
   then comes from the approved image, not prompt-hope. Duration 5→10s as QA
   stabilizes; `wan2.7` first+last-frame when available on this key for
   start/end pose control.
2. **Resolution ladder by tier:** PUBLIC inventory at 720P, SUBSCRIBER/PREMIUM
   at 1080P (matches the Fanvue ladder economics).
3. **Audio:** enable `wan2.6-i2v-flash` `audio=true` for SUBSCRIBER/PREMIUM
   clips only (cost gate), keep PUBLIC silent.
4. **Move t2v default from `wan2.1-t2v-turbo` to `wan2.7-t2v`** for
   non-identity b-roll: 2–30s durations, 1080P, native aspect ratios — at
   ~2× the per-second cost, still trivial vs. production value.
5. **Cost guardrails in manager memory:** per-persona monthly generation
   budget; exceed → `BLOCKED — BUDGET` with spend ledger in task results
   (provider-reported durations already land in provenance).
6. **Retire/contain the 13GB local LoRAs:** keep on AI_DRIVE via existing
   symlink; no local inference is wired and the cloud path is production.

## 7. Next exact task

Implement §6.1: identity-locked i2v in `manager_core.GENERATE_VIDEO`
(wan2.5-i2v-preview, approved shot image as first frame, base64 data-URL),
then live-verify one real video for Ava and QA-check face consistency against
the locked avatar.
