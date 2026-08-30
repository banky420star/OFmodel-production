# Persona Studio

Local/cloud hybrid platform for creating and operating persistent fictional synthetic creator identities.

## Quick Start

```bash
# Clone and start
docker compose up --build

# API docs
open http://localhost:8000/docs

# Frontend
open http://localhost:3000
```

## Architecture

```
Persona Studio
    │
  Next.js (3000)
    │
  FastAPI (8000)
    │
  Workflow Orchestrator
    │
    ├── Mock Providers (development)
    │   ├── MockImageProvider
    │   ├── MockVideoProvider
    │   ├── MockVoiceProvider
    │   └── MockTrainerProvider
    │
    └── Real Providers (production)
        ├── ComfyUIImageProvider
        ├── WanVideoProvider
        ├── ElevenLabsVoiceProvider
        └── LoRATrainer
    │
  PostgreSQL → Redis → MinIO
```

## Features

- **Persona Creation**: Define appearance, personality, brand
- **Identity Pipeline**: Generate candidates → approve → reference dataset → train → validate
- **Shoot Director**: Plan and generate photo/video shoots
- **Content Packs**: Assemble images, video, voiceover, captions
- **QA Engine**: Automated identity consistency and quality checks
- **Analytics**: 90-day performance tracking
- **24-Month Forecast**: Conservative/Base/Aggressive scenarios
- **Content Calendar**: Auto-scheduling across platforms
- **Autopilot**: OFF / ASSISTED / ON modes
- **Durable Workflows**: Resumable state machine, survives restarts

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/health` | GET | System health check |
| `/api/v1/personas` | GET/POST | List/create personas |
| `/api/v1/personas/{id}` | GET | Get persona detail |
| `/api/v1/personas/{id}/identities` | GET | List identity candidates |
| `/api/v1/personas/{id}/identities/{id}/approve` | POST | Approve identity |
| `/api/v1/personas/{id}/shoots` | GET/POST | List/create shoots |
| `/api/v1/shoots/{id}/generate` | POST | Generate shoot content |
| `/api/v1/personas/{id}/packs` | GET/POST | List/create content packs |
| `/api/v1/packs/{id}/assemble` | POST | Assemble full pack |
| `/api/v1/workflows` | GET | List workflows |
| `/api/v1/workflows/{id}` | GET | Get workflow detail |
| `/api/v1/workflows/{id}/steps` | GET | Get workflow steps |
| `/api/v1/workflows/{id}/retry` | POST | Retry failed workflow |
| `/api/v1/personas/{id}/qa` | GET | QA results |
| `/api/v1/personas/{id}/analytics` | GET | Analytics data |
| `/api/v1/personas/{id}/analytics/generate` | POST | Generate mock analytics |
| `/api/v1/personas/{id}/forecasts` | GET | Forecast data |
| `/api/v1/personas/{id}/forecasts/generate` | POST | Generate 24-month forecast |
| `/api/v1/personas/{id}/schedule` | GET | Content calendar |
| `/api/v1/personas/{id}/schedule/generate` | POST | Auto-schedule |
| `/api/v1/personas/{id}/autopilot` | POST | Toggle autopilot mode |

## Running Tests

```bash
# API unit + integration tests
cd apps/api
pip install -e ".[dev]"
pytest -v

# Playwright E2E tests
cd apps/web
npm install
npx playwright install
npx playwright test
```

## Development

```bash
# Start infrastructure only
docker compose up postgres redis minio

# Run API locally
cd apps/api
uvicorn app.main:app --reload

# Run frontend locally
cd apps/web
npm run dev
```

## Provider Configuration

All providers start as **mocks**. To connect real providers:

| Provider | Environment Variable | Required |
|----------|---------------------|----------|
| ComfyUI | `COMFYUI_URL` | Real image gen |
| Wan Video | `WAN_VIDEO_URL` | Real video gen |
| ElevenLabs | `ELEVENLABS_API_KEY` | Real voice |
| Ollama | `OLLAMA_BASE_URL` | Local LLM |
| GPU Worker | `GPU_WORKER_URL` | Remote GPU |

## Safety Requirements

- Adult verification required for all personas
- Synthetic identities only — no real person data
- Training data requires explicit rights documentation
- Human approval for canonical identity
- No API keys in frontend code
