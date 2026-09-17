"""Persona Studio — API routes package.

Combines all domain-specific sub-routers into a single router
that main.py includes at /api/v1.
"""

from fastapi import APIRouter

from app.routes.analytics import router as analytics_router
from app.routes.personas import router as personas_router
from app.routes.content import router as content_router
from app.routes.schedule import router as schedule_router
from app.routes.fans import router as fans_router
from app.routes.socials import router as socials_router
from app.routes.system import router as system_router

router = APIRouter(tags=["persona-studio"])

router.include_router(analytics_router)
router.include_router(personas_router)
router.include_router(content_router)
router.include_router(schedule_router)
router.include_router(fans_router)
router.include_router(socials_router)
router.include_router(system_router)
