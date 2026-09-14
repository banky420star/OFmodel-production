# AGENTS.md

## Run & environment

- Project root is `~/Downloads/Gemma_Local_Agent_v3_MODEL_FIX`. API = FastAPI on :8001 (`apps/api`), web = Next.js on :3000 (`apps/web`), SQLite at `apps/api/persona_studio.db` (auto-created).
- Start the API via `scripts/start_api.sh` launched through Terminal.app (osascript). Launching under launchd hits macOS TCC `PermissionError` for the Downloads folder; the repo's start script also works around a uvicorn/compiled-extension issue with the Xcode `/usr/bin/python3`.
- The command runner reaps detached process groups — `nohup` may silently die (pid gone ≠ crash; check the log). Web survives under `launchctl submit` with absolute node/npm paths; the API needs the Terminal.app route. Logs: `/tmp/persona_api.log`, `/tmp/persona_web.log`.
- If :3000 is stale, Next.js silently binds a random port (e.g. 52599). `pkill -f "next dev"` first, then relaunch.

## Database & data quirks

- UUIDs are stored WITHOUT dashes in `scheduled_posts.persona_id` (and some seeds); ORM ids are also dashless. Queries mixing dashed/hashed forms silently match zero rows — calendar showed 0 posts with 214 in the DB. Normalize format before comparing.
- Job `status` values are UPPERCASE in the DB (`RUNNING`, `COMPLETED`); lowercase filters match nothing.
- Seed scripts wrote bare disk paths into `personas.avatar_url` (`storage/avatars/x.jpg`) — browsers resolve these against the frontend origin and 404. The API serves them at `/api/v1/avatars/{filename}`; list endpoints must return that URL form, not the disk path.
- `kill -9` mid-production leaves orphaned `RUNNING` job rows that permanently 409 a persona's auto-produce; `main.py` lifespan startup resets interrupted jobs — keep that recovery.

## API correctness pitfalls

- `apps/api/app/routes/content.py` auto-produce: the sync image-generation functions MUST run via `asyncio.to_thread` inside async tasks. A direct call blocks the whole event loop — health, dashboard, and every frontend-proxied request hang for the duration.
- A function-local `from app.models import Job` shadows the module-level import and NameErrors the duplicate-run guard. Don't re-add local imports that shadow models.
- `GET /jobs?status=running` previously ignored the filter and returned all jobs; the frontend "Producing…" state and duplicate-run guard both depend on that filter being honored (`routes/system.py`).
- Fan chat reply endpoint is `POST /fans/{id}/reply` (not `/auto-reply`). Ollama (qwen3:1.7b) takes ~5-16s per reply. The chat UI must reload messages from the server after sending — a silent catch made sent messages "vanish".
- Chat timestamps: DB stores UTC, UI displays local (UTC+2 here) — offsets looked wrong but weren't; verify the offset before "fixing".

## Providers

- DashScope video: model `wan2.1-t2v-turbo` works; `wan2.7-*` names 404. Body param is `size`, not `resolution`/`ratio`. Generation takes ~2-3 min.
- DashScope image editing must use the **edit task** on `qwen-image-edit` (reference image + instruction) — `qwen-image-2.0-pro`/`-max`/`-plus` free quota is exhausted (403 `AllocationQuota.FreeTierOnly`). The Wan task family (`wan2.1-t2i-turbo` stills, `wan2.1-t2v-turbo` video) has a separate quota pool that still works. Only the intl endpoint accepts this key; China endpoint 401s. Edits take ~25-30s.
- HuggingFace moderation (`falconsai/nsfw_image_detection`) is keyless and fails safe (returns safe on network error); DNS failures on this machine log spam but don't block — fail-safe is intended, don't "fix" it.
- mail.tm temp emails (`@uberip.com`): creation rate-limits after ~4 rapid accounts — sleep 20-25s between batches.
- Playwright signup quirks (`providers/browser_signup.py`): Instagram submit is `div[role="button"]`, birthday dropdowns are virtualized `role="combobox"` elements (set via JS, not clicks); field order email → password → birthday → name → username.

## Testing

- `cd apps/api && python3 -m pytest tests/ -v --tb=short` — 27/28 pass; one e2e failure is pre-existing, don't chase it as a regression.
- `.env` may hold vars beyond `Settings`; either add fields or keep `extra="ignore"` or the suite won't boot.

## Driving native macOS app UI (LocalAgent)

- LocalAgent (menu-bar SwiftUI app) source lives at `~/Downloads/LocalAgent_Agentic_Studio_V5_1_1_LIVE_HOTFIX` with its own AGENTS.md of per-app quirks — don't confuse it with this web project (a past playtest pass drove the wrong product).
- Synthetic mouse clicks (CGEvent) only register when the target window is ACTIVE — the first click on an inactive window is consumed by activation. Activate via System Events `set frontmost` first; a "focus click" on content makes a working button read as dead.
- `CGEvent.postToPid` keyboard events land even when the app is backgrounded; mouse events don't. This asymmetry makes typing tests pass while click tests fail — check activation before blaming the control.
- A graceful `quit` can flush state AFTER a following `defaults write`, silently reverting it (seen with `localagent.models.routingMode`). To force state: `kill -9`, wait for exit, write, verify with `defaults read`, then launch.
- The app spawns a blank 500×500 phantom window next to the real dashboard; pick windows by OCR line count of a capture, not size or list order. The SwiftUI window exposes no AX content to System Events — assert on-screen state via `screencapture -l<id>` + Vision OCR, locating controls by OCR bounding box (mind retina 2x scaling).

## User preferences

- No mock data in the frontend, ever — audits penalize anything seeded/fake; wire real providers or show honest empty states.
- Show visible progress (loading/progress states, toasts) for any long-running action; the user watches the preview and wants to see work happening.
- Verify through the real surface (preview browser clicks), not just curl.
- Product = OnlyFans-style AI model production studio (personas, shoots, videos, fan chat, socials). Git remote: `github.com/banky420star/OFmodel-production.git` — only commit/push when asked.
