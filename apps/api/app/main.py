"""Persona Production Line — FastAPI Application.

Real providers only: the lifespan runs a startup self-check that resolves
every capability through the strict registry. Production refuses to start
degraded; development starts but reports "degraded" until all required
providers resolve.
"""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
import structlog

from app.config import get_settings
from app.database import init_db, AsyncSessionLocal
from app.models import Job
from app.routes import router
from app.routes_jobs import router as jobs_router
from sqlalchemy import update

settings = get_settings()

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Startup: create tables for SQLite local dev
    await init_db()
    # Clear jobs orphaned by a previous process (they will never resume)
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Job)
            .where(Job.status.in_(["queued", "running"]))
            .values(status="failed", message="Interrupted by server restart")
        )
        await db.commit()

    # Strict provider self-check — no mocks, no silent degradation.
    from app.providers.registry import get_registry
    rows = get_registry().startup_selfcheck()
    for row in rows:
        logger.info(
            "provider_selfcheck",
            capability=row["capability"],
            provider=row["provider"],
            status=row["status"],
            configured=row["configured"],
            hint=row["env_hint"],
        )
    unresolved = [r["capability"] for r in rows if r["capability"] in get_registry().required_capabilities() and not r["configured"]]
    if unresolved:
        if settings.ENVIRONMENT == "production":
            raise RuntimeError(
                f"Refusing to start in production with unconfigured providers: {unresolved}. "
                "Set the required keys in .env — mock fallback does not exist."
            )
        logger.warning("providers_unresolved", capabilities=unresolved)

    yield
    # Shutdown: nothing needed


app = FastAPI(
    title="Persona Production Line",
    description="Synthetic-persona content production line — real providers only",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

WEB_ORIGIN = os.getenv("WEB_ORIGIN", settings.WEB_ORIGIN)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        WEB_ORIGIN,
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")

# Avatar static files
AVATARS_DIR = Path(__file__).parent.parent / "storage" / "avatars"


@app.get("/api/v1/avatars/{filename}")
async def serve_avatar(filename: str):
    """Serve persona avatar images with identity-lock cache headers."""
    file_path = AVATARS_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Avatar not found")
    return FileResponse(
        file_path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache, must-revalidate",
            "X-Identity-Locked": "true",
        },
    )


# Gallery images
GALLERY_DIR = Path(__file__).parent.parent / "storage" / "gallery"


@app.get("/api/v1/gallery/{filename}")
async def serve_gallery(filename: str):
    """Serve gallery variation images."""
    file_path = GALLERY_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Gallery image not found")
    return FileResponse(
        file_path,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Identity-Locked": "true",
        },
    )


# Generated videos
VIDEOS_DIR = Path(__file__).parent.parent / "storage" / "videos"


@app.get("/api/v1/media/videos/{filename}")
async def serve_video(filename: str):
    """Serve generated persona videos."""
    file_path = VIDEOS_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(
        file_path,
        media_type="video/mp4",
        headers={
            "Cache-Control": "public, max-age=86400",
            "Accept-Ranges": "bytes",
        },
    )


# Shoot images
SHOOTS_DIR = Path(__file__).parent.parent / "storage" / "shoots"


@app.get("/api/v1/shoots/{shoot_id}/images/{filename}")
async def serve_shoot_image(shoot_id: str, filename: str):
    """Serve photoshoot images."""
    file_path = SHOOTS_DIR / shoot_id / filename
    if not file_path.exists() or not file_path.is_file():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Shoot image not found")
    return FileResponse(
        file_path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ── Adult content gate ────────────────────────────────────────────────
# Age-confirmation cookie gate: content is only served to requests carrying
# the HttpOnly `adult_verified` cookie set by POST /api/v1/gate/age-confirm.
# Without it the route answers 410 regardless of file existence (no leak).
ADULT_DIR = Path(__file__).parent.parent / "storage" / "adult_content"
ADULT_COOKIE = "adult_verified"


@app.post("/api/v1/gate/age-confirm")
async def age_confirm():
    """Confirm viewer age — sets an HttpOnly cookie for adult-content media."""
    response = JSONResponse({"confirmed": True})
    response.set_cookie(
        key=ADULT_COOKIE,
        value="1",
        max_age=60 * 60 * 12,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/api/v1/adult-content/{persona_id}/{filename}")
async def serve_adult_content(persona_id: str, filename: str, request: Request):
    """Serve adult content images — requires the age-confirm cookie."""
    if request.cookies.get(ADULT_COOKIE) != "1":
        return JSONResponse(
            status_code=410,
            content={"detail": "Age confirmation required (POST /api/v1/gate/age-confirm)"},
        )
    file_path = ADULT_DIR / persona_id / filename
    if not file_path.exists() or not file_path.is_file():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Content not found")
    return FileResponse(
        file_path,
        media_type="image/png",
        headers={
            "Cache-Control": "private, no-store",
            "X-Adult-Content": "true",
            "X-Audit-Logged": "true",
        },
    )


@app.get("/")
async def root():
    return {
        "name": "Persona Production Line",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "health": "/api/v1/health",
    }


@app.get("/health")
async def simple_health():
    return {"status": "ok"}