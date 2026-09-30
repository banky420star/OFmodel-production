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
from app.routes.tiktok import router as tiktok_router
from app.routes.fanvue import router as fanvue_router
from app.routes.team import router as team_router
from app.routes.system import router as system_router
from app.routes.manager import router as manager_router
from app.routes.publish import router as publish_router
from app.routes.auth import router as auth_router
from app.routes.fan import router as fan_router

router = APIRouter(tags=["persona-studio"])

router.include_router(analytics_router)
router.include_router(personas_router)
router.include_router(content_router)
router.include_router(schedule_router)
router.include_router(fans_router)
router.include_router(socials_router)
router.include_router(tiktok_router)
# Fanvue's OAuth. Separate from publish_router because that one posts, and this
# one only ever obtains the authority to: the connect/callback pair is what
# makes `FANVUE_ACCESS_TOKEN` obtainable without hand-driving the flow in a
# terminal, which is how it had to be done before.
router.include_router(fanvue_router)
router.include_router(team_router)
router.include_router(system_router)
router.include_router(manager_router)
router.include_router(publish_router)

# The fan-facing surface. These are the only routes in the app that require a
# session cookie; everything above is still the unauthenticated operator API,
# which is deliberate (see the plan's risk 1 — do not expose beyond localhost).
router.include_router(auth_router)
router.include_router(fan_router)
