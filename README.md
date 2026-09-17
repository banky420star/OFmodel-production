# Persona Production Line

Synthetic-persona content production line — the Persona Studio architecture, rebuilt to run **real providers only**. There is no mock/demo mode: every capability is explicitly configured, and any endpoint whose provider is unconfigured fails with a 503 naming the exact env var to set.

## Stack

- **API** — FastAPI + SQLAlchemy async (SQLite dev / Postgres prod), durable workflow engine, single job runner with concurrency + timeout limits.
- **Web** — Next.js 15 App Router, dark design system in plain CSS.
- **Providers** (all real, one per capability, chosen in `.env`):
  - LLM: Ollama (local)
  - Image: DashScope qwen-image / qwen-image-edit (default), EachSense, ComfyUI, HuggingFace
  - Video: DashScope Wan (cloud) or self-hosted Wan server
  - Voice: ElevenLabs
  - Trainer: local LoRA (diffusers + peft, MPS/CUDA)
  - Moderation: HuggingFace NSFW classifier (fail-closed — unavailable moderation blocks generation)
  - Storage: local filesystem or MinIO
  - Analytics: real Instagram Graph sync + manual entry (no synthetic data)
  - Creator business: monetization workspace with offer-ladder planning, recorded-vs-projected revenue separation, and launch gates for disclosure, rights, human review, and provider health.

## Layout

```
apps/api     FastAPI backend (app/, tests/, alembic/)
apps/web     Next.js frontend (src/)
scripts      launchd plists, init.sql, provider e2e tests
storage      runtime media (git-ignored): avatars/, gallery/, shoots/, videos/, datasets/
```

## Run (local)

```bash
cp .env.example .env        # fill in real keys
cd apps/api && python3 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/python -m uvicorn app.main:app --port 8000
cd apps/web && npm install && npm run dev
```

API docs: http://localhost:8000/docs — Web: http://localhost:3000

## Creator monetization workspace

Open **Monetization** in the web app for a platform-neutral subscription
planning view. It uses recorded analytics when available and labels scenario
math as planning only; it does not invent revenue or subscriber data. For
platforms without an official API, publishing, verification, and account
actions remain manual and human-approved.

## ComfyUI

ComfyUI is already supported as the image provider. Start ComfyUI separately,
then set:

```bash
IMAGE_PROVIDER=comfyui
COMFYUI_URL=http://127.0.0.1:8188
COMFYUI_CHECKPOINT=sd_xl_base_1.0.safetensors
```

For a local Apple Silicon setup, the project includes launchers for the
complete development stack:

```bash
./scripts/start_comfyui.sh   # ComfyUI on MPS at :8188
./scripts/start_local.sh     # API on :8000 and web UI on :3000
```

`start_comfyui.sh` uses the M4/MPS device by default; set `COMFYUI_CPU=1`
when a CPU-only run is required. The ComfyUI checkpoint still must be
installed locally under `.local/ComfyUI/models/checkpoints` before queuing a
generation. The app will show ComfyUI as reachable even before a checkpoint
is installed, but generation remains blocked by ComfyUI until a checkpoint is
available.

Run the preflight before generating:

```bash
python3 scripts/check_comfyui.py --url http://127.0.0.1:8188
```

The check verifies the API, GPU/device response, and the nodes required for
identity-locked txt2img/img2img (`CheckpointLoaderSimple`, `KSampler`,
`LoadImage`, `VAEEncode`, and `SaveImage`). Install a checkpoint such as
`sd_xl_base_1.0.safetensors` in ComfyUI's `models/checkpoints` directory before
starting a production run.

The identity engine still requires an active identity lock, a consented
reference, and provider-backed QA before media can be used in a pack.

## Run (docker)

```bash
cp .env.example .env
docker compose up --build   # postgres + api(8000) + web(3000)
```

## The identity lock

Every image goes through the identity engine, which requires an ACTIVE `identity_locks` row (created at persona build; flips ACTIVE only when the identity passes QA) and the persona's real avatar as the edit reference. No lock → no generation. This is what keeps the face consistent and what keeps placeholders out of the pipeline.