# Persona Studio — Build Status

## Phase Progress

| Phase | Status | Tests | Description |
|-------|--------|-------|-------------|
| [✓] Phase 1 | Infrastructure | 2/2 | Docker Compose, Dockerfiles, env config |
| [✓] Phase 2 | Database | 2/2 | 15 SQLAlchemy models, async engine, Alembic |
| [✓] Phase 3 | Identity | 4/4 | Persona creation, identity candidates, approval |
| [✓] Phase 4 | Workflow Engine | 1/1 | Durable state machine, step persistence, retry |
| [✓] Phase 5 | Image Provider | 4/4 | Provider abstraction, ComfyUI adapter, mock, GPU worker |
| [✓] Phase 6 | LoRA Trainer | 2/2 | Trainer interface, mock trainer, validation |
| [✓] Phase 7 | Identity QA | 1/1 | Scoring, threshold validation, regeneration |
| [✓] Phase 8 | Video + Voice | 3/3 | Wan video adapter, ElevenLabs voice, mock providers |
| [✓] Phase 9 | Content Packs | 2/2 | Shoot Director, pack assembly, caption generation |
| [✓] Phase 10 | Autopilot | 1/1 | OFF/ASSISTED/ON modes, scheduling |
| [✓] Phase 11 | Analytics | 1/1 | 90-day metric generation and retrieval |
| [✓] Phase 12 | Forecasting | 1/1 | 24-month deterministic projections |
| [✓] Phase 13 | E2E Integration | 3/3 | Full mock workflow, API tests, schemas |
| [•] Phase 14 | Real Providers | — | ComfyUI ✓, ElevenLabs ✓, Wan ✓, Ollama ✓, GPU Worker ✓ |

**Total: 28/28 tests passing**

## What's Built

### Backend (FastAPI)
- 25+ API endpoints covering all 14 phases
- Durable workflow engine with step-level persistence
- Provider registry with dynamic mock/real selection
- Celery async task definitions for background processing
- SQLite (dev) / PostgreSQL (production) database support

### Provider Abstraction Layer
| Provider | Interface | Mock | Real Adapter |
|----------|-----------|------|-------------|
| LLM | `LLMProvider` | ✓ Deterministic | ✓ Ollama (local) |
| Image | `ImageProvider` | ✓ Deterministic PNG | ✓ ComfyUI API |
| Video | `VideoProvider` | ✓ Mock data | ✓ Wan2.1 API |
| Voice | `VoiceProvider` | ✓ Mock audio | ✓ ElevenLabs API |
| Trainer | `TrainerProvider` | ✓ Mock LoRA | ⏳ GPU Worker |
| Storage | `StorageProvider` | ✓ In-memory | ✓ MinIO/S3 |

### Real Provider Adapters
- **ComfyUI** (`providers/comfyui.py`) — txt2img, img2img, upscale via ComfyUI HTTP API + websocket polling
- **ElevenLabs** (`providers/elevenlabs.py`) — voice creation, TTS synthesis via ElevenLabs REST API
- **Wan Video** (`providers/wan_video.py`) — image-to-video, text-to-video via Wan-compatible API
- **Ollama** (`providers/ollama_provider.py`) — local LLM inference for structured decisions
- **GPU Worker** (`providers/gpu_worker.py`) — remote GPU job dispatch with capability-based routing

### Frontend (Next.js)
- Dashboard with health status, model cards
- Model creation with safety gates
- Persona detail with 8 tabs

### Infrastructure
- `docker-compose.yml` — PostgreSQL, Redis, MinIO, API, Celery worker, frontend
- Provider selection via `PROVIDER_REGISTRY` env var
- GPU worker support via `GPU_WORKER_URL`

## How to Run

### Mock mode (no GPU needed)
```bash
docker compose up --build
# API: http://localhost:8000/docs
# Frontend: http://localhost:3000
```

### Real providers (add credentials)
```bash
# Edit .env:
PROVIDER_REGISTRY=hybrid
COMFYUI_URL=http://your-gpu-machine:8188
ELEVENLABS_API_KEY=sk_your_key
WAN_VIDEO_URL=http://your-gpu-machine:8080
OLLAMA_URL=http://localhost:11434

docker compose up --build
```

### Remote GPU worker
```bash
# On your Mac (control plane):
PROVIDER_REGISTRY=hybrid
GPU_WORKER_URL=http://gpu-server:9000

# On GPU server:
# Run the worker API (separate service)
```

## Remaining for Full Production

| Item | Status | Notes |
|------|--------|-------|
| `docker compose up --build` | Needs verification | Dockerfiles updated, needs build test |
| Playwright E2E tests | Scaffold exists | `apps/web/e2e/workflow.spec.ts` |
| Real LoRA training | Needs GPU worker API | Interface defined, mock works |
| MinIO storage integration | Interface defined | Mock works, real needs MinIO running |
| Authentication/RBAC | Not started | Spec calls for it |
| Audit logging | Not started | Spec calls for it |
| OpenTelemetry | Not started | Spec calls for it |
