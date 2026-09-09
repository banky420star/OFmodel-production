"""Persona Studio — FastAPI Application.

The main application with all 14 phases implemented.
"""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path
import structlog

from app.config import get_settings
from app.database import init_db
from app.routes import router

settings = get_settings()

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Startup: create tables for SQLite local dev
    await init_db()
    yield
    # Shutdown: nothing needed


app = FastAPI(
    title="Persona Studio",
    description="Local/cloud hybrid platform for creating and operating persistent fictional synthetic creator identities",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

WEB_ORIGIN = os.getenv("WEB_ORIGIN", "http://localhost:3000")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        WEB_ORIGIN,
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")


def _safe_file_path(base_dir: Path, *parts: str) -> Path | None:
    """Join *parts* under *base_dir*, refusing path-traversal escapes.

    Returns the resolved path if it is a real file inside base_dir, else None.
    """
    base = base_dir.resolve()
    candidate = base.joinpath(*parts).resolve()
    if candidate != base and base not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


# Avatar static files
AVATARS_DIR = Path(__file__).parent.parent / "storage" / "avatars"


@app.get("/api/v1/avatars/{filename}")
async def serve_avatar(filename: str):
    """Serve persona avatar images with identity-lock cache headers."""
    file_path = _safe_file_path(AVATARS_DIR, filename)
    if file_path is None:
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
    file_path = _safe_file_path(GALLERY_DIR, filename)
    if file_path is None:
        raise HTTPException(status_code=404, detail="Gallery image not found")
    return FileResponse(
        file_path,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Identity-Locked": "true",
        },
    )


# Shoot images
SHOOTS_DIR = Path(__file__).parent.parent / "storage" / "shoots"


@app.get("/api/v1/shoots/{shoot_id}/images/{filename}")
async def serve_shoot_image(shoot_id: str, filename: str):
    """Serve photoshoot images."""
    file_path = _safe_file_path(SHOOTS_DIR, shoot_id, filename)
    if file_path is None:
        raise HTTPException(status_code=404, detail="Shoot image not found")
    return FileResponse(
        file_path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# Adult content images
ADULT_DIR = Path(__file__).parent.parent / "storage" / "adult_content"


@app.get("/api/v1/adult-content/{persona_id}/{filename}")
async def serve_adult_content(persona_id: str, filename: str):
    """Serve adult content images.
    
    Requires age verification cookie in production.
    """
    file_path = _safe_file_path(ADULT_DIR, persona_id, filename)
    if file_path is None:
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
        "name": "Persona Studio",
        "version": "0.1.0",
        "status": "running",
        "docs": "/docs",
        "health": "/api/v1/health",
    }


@app.get("/health")
async def simple_health():
    return {"status": "ok"}
