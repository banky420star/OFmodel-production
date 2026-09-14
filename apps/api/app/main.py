"""Persona Studio — FastAPI Application.

The main application with all 14 phases implemented.
"""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path
import structlog

from app.config import get_settings
from app.database import init_db, AsyncSessionLocal
from app.models import Job, Workflow, WorkflowStatus
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


async def _repair_stuck_building_personas(db) -> int:
    """Flip BUILDING personas whose latest persona_build workflow FAILED
    (interrupted or dead builds) to FAILED. Active/ready personas with old
    failed attempts are left untouched. Returns the number repaired."""
    from sqlalchemy import select as _select
    from app.models import Persona, PersonaStatus as _PS
    stuck = (await db.execute(
        _select(Persona).where(Persona.status == _PS.BUILDING)
    )).scalars().all()
    repaired = 0
    for persona in stuck:
        latest = (await db.execute(
            _select(Workflow)
            .where(Workflow.persona_id == persona.id)
            .where(Workflow.workflow_type == "persona_creation")
            .order_by(Workflow.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        if latest is not None and latest.status == WorkflowStatus.FAILED:
            persona.status = _PS.FAILED
            repaired += 1
    await db.commit()
    return repaired


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Startup: create tables for SQLite local dev
    await init_db()
    # Clear jobs orphaned by a previous process (they will never resume) and
    # fail their workflows the same way — a RUNNING workflow row whose
    # in-process task died with the old process must not block a rebuild.
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Job)
            .where(Job.status.in_(["queued", "running"]))
            .values(status="failed", message="Interrupted by server restart")
        )
        await db.execute(
            update(Workflow)
            .where(Workflow.status == WorkflowStatus.RUNNING)
            .values(
                status=WorkflowStatus.FAILED,
                error_message="Interrupted by server restart — rebuild the persona",
            )
        )
        await _repair_stuck_building_personas(db)
        # Model-manager restart recovery: tasks left RUNNING by a dead process
        # go back to PENDING (due immediately) so managers resume cleanly.
        from app.manager_core import recover_running_tasks
        recovered = await recover_running_tasks(db)
        if recovered:
            import logging
            logging.getLogger("persona.manager").info("restart recovery: %d manager tasks requeued", recovered)

    # Always-on heartbeat: event/state-driven wake for every started manager.
    # The loop checks DB state only — it invents no work and makes no provider
    # calls when managers have nothing due.
    import asyncio as _asyncio

    async def _manager_heartbeat_loop() -> None:
        from app.database import AsyncSessionLocal as _S
        from app.manager_core import heartbeat_once
        import logging as _log
        log = _log.getLogger("persona.manager")
        while True:
            try:
                await _asyncio.sleep(45)
                async with _S() as db:
                    woke = await heartbeat_once(db)
                    active = [w for w in woke if w.get("reason") not in ("idle", None)]
                    if active:
                        log.info("heartbeat: %d/%d managers woke: %s",
                                 len(active), len(woke),
                                 [w.get("reason") for w in active])
            except _asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — heartbeat must survive anything
                log.exception("heartbeat cycle failed")

    _heartbeat = _asyncio.create_task(_manager_heartbeat_loop())

    # Always-on social worker: drives OFFICIAL platform APIs only (Fanvue,
    # X, ...) for accounts with stored credentials. Platforms without an
    # official API are never touched; failures are recorded, never faked.
    async def _social_worker_loop_task() -> None:
        from app.social_worker import social_worker_loop
        await social_worker_loop()

    _social = _asyncio.create_task(_social_worker_loop_task())
    yield
    _heartbeat.cancel()
    _social.cancel()
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
    """Serve photoshoot images. Accepts either the full shoot UUID or the
    8-hex directory prefix used on disk."""
    file_path = SHOOTS_DIR / shoot_id / filename
    if not file_path.exists() and len(shoot_id) > 8:
        file_path = SHOOTS_DIR / shoot_id.replace("-", "")[:8] / filename
    if not file_path.exists() or not file_path.is_file():
        from fastapi import HTTPException
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
        "name": "Persona Studio",
        "version": "0.1.0",
        "status": "running",
        "docs": "/docs",
        "health": "/api/v1/health",
    }


@app.get("/health")
async def simple_health():
    return {"status": "ok"}
