"""Persona Studio — schedule routes."""

from __future__ import annotations
import json
import time
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Depends, Query, Form
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import paths
from app.database import get_db
from app.publishing import inspect_media_file
from app.models import (
    Persona, ContentPack, Shoot, SocialAccount, ScheduledPost,
    PersonaStatus,
)
from app.schemas import ScheduledPostResponse

router = APIRouter()

# Resolved from app/paths.py, never from the process CWD. These used to be bare
# `Path("storage/...")` lookups, which only found content when uvicorn happened
# to have been launched from `apps/api`. Started anywhere else — a launchd
# service, a test runner, the repo root — the calendar silently matched nothing
# and wrote an empty schedule with no error.
STORAGE_ROOT = paths.STORAGE_ROOT


def _persona_uuid(persona_id: str) -> UUID:
    """Coerce a path param to UUID — Persona.id is a PG-UUID column and a
    dashed string crashes the type's bind processor."""
    return UUID(persona_id)


@router.get("/personas/{persona_id}/schedule", response_model=list[ScheduledPostResponse])
async def get_schedule(persona_id: str, db: AsyncSession = Depends(get_db)):
    pid = _persona_uuid(persona_id)
    # ScheduledPost.persona_id is a String(36) column, not a UUID column like
    # Persona.id / ContentPack.persona_id / Shoot.persona_id — rows are written
    # with str(persona_id). Binding the UUID object here matched nothing, so
    # this endpoint returned an empty calendar for every persona.
    result = await db.execute(
        select(ScheduledPost).where(ScheduledPost.persona_id == str(pid)).order_by(ScheduledPost.scheduled_at)
    )
    return result.scalars().all()


@router.post("/personas/{persona_id}/schedule/generate")
async def generate_schedule(persona_id: str, db: AsyncSession = Depends(get_db)):
    persona = await db.get(Persona, _persona_uuid(persona_id))
    if not persona:
        raise HTTPException(404, "Persona not found")

    now = datetime.now(timezone.utc)
    platforms = ["instagram", "tiktok", "youtube"]
    posts = []

    for day_offset in range(30):
        date = now + timedelta(days=day_offset)
        if date.weekday() < 5:  # Weekdays only
            for platform in random.sample(platforms, k=min(2, len(platforms))):
                post = ScheduledPost(
                    id=str(uuid4()),
                    persona_id=persona_id,
                    platform=platform,
                    scheduled_at=date.replace(hour=random.choice([9, 12, 15, 18]), minute=0),
                    status="scheduled",
                )
                db.add(post)
                posts.append(post)

    await db.commit()
    return {"status": "generated", "posts": len(posts)}


@router.post("/personas/{persona_id}/schedule/smart")
async def smart_schedule(persona_id: str, db: AsyncSession = Depends(get_db)):
    """Smart scheduler: pulls from content packs and assigns real content to calendar slots.
    
    Optimal posting times by platform:
    - Instagram: 9am, 12pm, 6pm (image-heavy)
    - TikTok: 7am, 12pm, 9pm (video-heavy)
    - OnlyFans: 10am, 2pm, 8pm (mixed, premium at 8pm)
    - Twitter/X: 8am, 12pm, 5pm (text + image)
    """
    pid = _persona_uuid(persona_id)
    persona = await db.get(Persona, pid)
    if not persona:
        raise HTTPException(404, "Persona not found")
    
    # Get all content packs for this persona that have content
    packs_result = await db.execute(
        select(ContentPack).where(ContentPack.persona_id == pid)
    )
    packs = packs_result.scalars().all()
    
    # Collect all available content from packs
    available_content = []
    skipped_media: list[str] = []
    for pack in packs:
        images = pack.images or []
        videos = pack.videos or []
        captions = pack.captions or []
        for i, img in enumerate(images):
            caption = captions[i] if i < len(captions) else "New post ✨"
            available_content.append({
                "pack_id": str(pack.id),
                "type": "image",
                "key": img,
                "caption": caption,
                "platforms": ["instagram", "tiktok"],
            })
        for i, vid in enumerate(videos):
            caption = captions[len(images) + i] if len(images) + i < len(captions) else "New video 🎬"
            available_content.append({
                "pack_id": str(pack.id),
                "type": "video",
                "key": vid,
                "caption": caption,
                "platforms": ["tiktok", "instagram"],
            })
    
    # Also pull from shoots that have generated images on disk
    # Shoot dirs use 8-char truncated UUIDs: storage/shoots/{8char}/
    shoots_root = STORAGE_ROOT / "shoots"
    shoot_dirs = list(shoots_root.iterdir()) if shoots_root.exists() else []
    # Build a map: 8-char prefix -> full shoot object
    shoots_result = await db.execute(select(Shoot).where(Shoot.persona_id == pid))
    shoots = shoots_result.scalars().all()
    shoot_map = {str(s.id)[:8]: s for s in shoots}

    # Directories on disk that no Shoot row claims. Counted and reported rather
    # than passed over in silence, because it is the difference between "there
    # is no content" and "there is content here that nothing can attribute to a
    # persona" — and those send the operator to completely different places.
    # Measured 2026-09-30: 449 directories under storage/shoots, one Shoot row.
    unattributed_dirs = 0

    for shoot_dir in shoot_dirs:
        if not shoot_dir.is_dir():
            continue
        dir_name = shoot_dir.name
        shoot = shoot_map.get(dir_name)
        if not shoot:
            unattributed_dirs += 1
            continue
        for img_file in sorted(shoot_dir.glob("*.png")):
            img_key = f"storage/shoots/{dir_name}/{img_file.name}"
            # A directory full of files is not a directory full of pictures.
            # Live storage held 338 files under `storage/shoots` and not one
            # usable image — 254 were a bare 8-byte PNG signature, 84 were 8x8.
            # Scheduled as-is, every one became a slot that could only ever be
            # refused at publish time, which is a calendar that looks full and
            # ships nothing.
            usable, reason = inspect_media_file(img_file)
            if not usable:
                skipped_media.append(f"{img_key} — {reason}")
                continue
            available_content.append({
                "pack_id": None,
                "type": "image",
                "key": img_key,
                "caption": f"{shoot.name} 📸",
                "platforms": ["instagram", "onlyfans"],
            })
        # Check for generated videos
        video_dir = STORAGE_ROOT / "videos" / dir_name
        if video_dir.exists():
            for vid_file in sorted(video_dir.glob("*.mp4")):
                vid_key = f"storage/videos/{dir_name}/{vid_file.name}"
                usable, reason = inspect_media_file(vid_file)
                if not usable:
                    skipped_media.append(f"{vid_key} — {reason}")
                    continue
                available_content.append({
                    "pack_id": None,
                    "type": "video",
                    "key": vid_key,
                    "caption": f"{shoot.name} 🎬",
                    "platforms": ["tiktok", "instagram"],
                })

    if not available_content:
        return {
            "status": "no_content",
            "message": "No content packs or shoots with generated content found",
            "posts": 0,
            # Named, not just counted: "found nothing" and "found 338 files and
            # none of them were pictures" are different problems, and only the
            # second one points at the generator.
            "skipped_media": skipped_media[:5],
            "skipped_media_count": len(skipped_media),
            "unattributed_shoot_dirs": unattributed_dirs,
        }
    
    # Platform posting schedules (hour -> platform weight)
    PLATFORM_SCHEDULES = {
        "instagram": [(9, 3), (12, 4), (18, 5)],
        "tiktok": [(7, 4), (12, 3), (21, 5)],
        "onlyfans": [(10, 3), (14, 4), (20, 5)],
        "twitter": [(8, 3), (12, 3), (17, 4)],
        # Fanvue, the one platform here that can actually be posted to from this
        # app and the only one that can charge for a post. Absent from this table
        # it fell through to the `[(12, 1)]` default and every slot for the only
        # sellable platform in the list landed at noon.
        "fanvue": [(11, 3), (15, 4), (20, 5)],
    }

    # Determine platforms for this persona (check social accounts)
    from app.models import SocialAccount
    socials_result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.persona_id == str(pid),
            SocialAccount.status.in_(['active', 'approved'])
        )
    )
    socials = socials_result.scalars().all()
    active_platforms = list(set([s.platform for s in socials]))

    # There is deliberately no fallback here. There used to be one — `if socials
    # else ["instagram", "tiktok"]` — and it fired in exactly the case where
    # *nothing* was connected, manufacturing 30 days of slots for two platforms
    # this app has no publishing adapter for. Every one of those rows was
    # unshippable, the operator had never mentioned either platform, and the run
    # reported `status: "scheduled"` with a post count, which reads as inventory.
    # A refusal is the honest answer to "schedule for what?".
    if not active_platforms:
        # Named from the adapter registry rather than hardcoded, so this message
        # cannot come to name a platform nothing can publish to.
        from app.publishing import publisher_classes

        sellable = ", ".join(sorted(publisher_classes())) or "(none built)"
        return {
            "status": "no_platform",
            "message": (
                "No platform is connected for this persona, so there is nothing to "
                "schedule for and nothing was written. Approve an account for it "
                f"first — {sellable} is the only platform this app can publish to."
            ),
            "posts": 0,
            "unpriceable": {},
        }

    # What each platform's posts must be priced at. Resolved before a single
    # slot is written, because a slot that cannot be priced is a slot that can
    # never ship — and a calendar full of those looks exactly like a calendar
    # full of sellable content right up until publish time.
    from app.config import get_settings
    from app.publishing import PublishRefused, resolved_price_minor

    stated_price = get_settings().DEFAULT_PPV_PRICE
    prices: dict[str, int | None] = {}
    unpriceable: dict[str, str] = {}
    for _platform in active_platforms:
        try:
            prices[_platform] = resolved_price_minor(_platform, stated_price)
        except PublishRefused as exc:
            unpriceable[_platform] = exc.detail

    schedulable = [p for p in active_platforms if p not in unpriceable]
    if not schedulable:
        return {
            "status": "unpriceable",
            "message": (
                "Every connected platform sells, and no price is stated, so no "
                "slot here could be published. Nothing was scheduled."
            ),
            "posts": 0,
            "unpriceable": unpriceable,
        }

    # Remove old scheduled posts for this persona
    await db.execute(
        text("DELETE FROM scheduled_posts WHERE persona_id = :pid AND status = 'scheduled'"),
        {"pid": str(persona_id)}
    )

    # Schedule content across 30 days
    now = datetime.now(timezone.utc)
    posts_created = 0
    priced_posts = 0
    content_idx = 0

    for day_offset in range(30):
        date = now + timedelta(days=day_offset)
        weekday = date.weekday()

        # 2 posts per weekday, 1 on weekends
        posts_today = 2 if weekday < 5 else 1

        for post_num in range(posts_today):
            if content_idx >= len(available_content):
                content_idx = 0  # Wrap around

            content = available_content[content_idx]
            platform = schedulable[post_num % len(schedulable)]

            # Get optimal hour for this platform
            schedule = PLATFORM_SCHEDULES.get(platform, [(12, 1)])
            hour = schedule[post_num % len(schedule)][0]

            scheduled_at = date.replace(hour=hour, minute=0, second=0, microsecond=0)

            post = ScheduledPost(
                persona_id=persona_id,
                content_pack_id=content["pack_id"],
                platform=platform,
                content_type=content["type"],
                title=content["caption"].split("\n")[0][:100] if content["caption"] else "",
                caption=content["caption"],
                media_keys=[content["key"]],
                # `ppv_price` is the difference between content and inventory.
                # Nothing used to set it, so every row carried the column's
                # "null = free post" and the whole calendar was a giveaway.
                ppv_price=None if prices[platform] is None else prices[platform] / 100,
                tags=[platform, content["type"]],
                scheduled_at=scheduled_at,
                status="scheduled",
            )
            db.add(post)
            posts_created += 1
            if prices[platform] is not None:
                priced_posts += 1
            content_idx += 1

    await db.commit()
    return {
        "status": "scheduled",
        "posts": posts_created,
        # The number that matters: posts carrying a price a fan would have to
        # pay. `posts` counts slots; this counts sellable ones.
        "priced_posts": priced_posts,
        "price_minor": {
            p: prices[p] for p in schedulable if prices[p] is not None
        },
        "unpriceable": unpriceable,
        "content_used": len(available_content),
        # Reported on the success path too, not only when nothing could be
        # scheduled. A run that booked 20 slots from 2,000 files and passed over
        # 1,980 placeholders in silence reads as a healthy calendar.
        "skipped_media_count": len(skipped_media),
        "unattributed_shoot_dirs": unattributed_dirs,
        "days": 30,
    }


@router.post("/schedule/smart-all")
async def smart_schedule_all(db: AsyncSession = Depends(get_db)):
    """Smart-schedule all active personas."""
    result = await db.execute(select(Persona).where(Persona.status == PersonaStatus.ACTIVE))
    personas = result.scalars().all()
    total = 0
    for p in personas:
        # Reuse the logic from smart_schedule but inline to avoid duplication
        packs_result = await db.execute(select(ContentPack).where(ContentPack.persona_id == p.id))
        packs = packs_result.scalars().all()
        content_count = sum(len(pack.images or []) + len(pack.videos or []) for pack in packs)
        if content_count > 0:
            # Create simple schedule
            now = datetime.now(timezone.utc)
            for day in range(30):
                date = now + timedelta(days=day)
                if date.weekday() < 5:
                    for hour in [12, 18]:
                        post = ScheduledPost(
                            # String(36) column — bind the dashed string, not p.id.
                            persona_id=str(p.id),
                            platform="instagram",
                            content_type="image",
                            caption=f"{p.name} post day {day + 1} 📸",
                            scheduled_at=date.replace(hour=hour, minute=0),
                            status="scheduled",
                        )
                        db.add(post)
                        total += 1
    await db.commit()
    return {"status": "scheduled", "posts": total, "personas": len(personas)}


# ─── Autopilot ──────────────────────────────────────────────────────

