"""Persona Studio — FastAPI Application.

The main application with all 14 phases implemented.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import structlog

from app.config import get_settings
from app.database import init_db
from app.routes import router
from app.routes_jobs import router as jobs_router

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")


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
