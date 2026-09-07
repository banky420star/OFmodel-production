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

from app.database import get_db
from app.models import (
    Persona, ContentPack, Shoot, SocialAccount, ScheduledPost,
    PersonaStatus,
)
from app.schemas import ScheduledPostResponse

router = APIRouter()

@router.get("/personas/{persona_id}/schedule", response_model=list[ScheduledPostResponse])
async def get_schedule(persona_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ScheduledPost).where(ScheduledPost.persona_id == persona_id).order_by(ScheduledPost.scheduled_at)
    )
    return result.scalars().all()


@router.post("/personas/{persona_id}/schedule/generate")
async def generate_schedule(persona_id: str, db: AsyncSession = Depends(get_db)):
    persona = await db.get(Persona, persona_id)
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
                    id=uuid4(),
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
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    
    # Get all content packs for this persona that have content
    packs_result = await db.execute(
        select(ContentPack).where(ContentPack.persona_id == persona_id)
    )
    packs = packs_result.scalars().all()
    
    # Collect all available content from packs
    available_content = []
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
    shoot_dirs = list(Path("storage/shoots").iterdir()) if Path("storage/shoots").exists() else []
    # Build a map: 8-char prefix -> full shoot object
    shoots_result = await db.execute(select(Shoot).where(Shoot.persona_id == persona_id))
    shoots = shoots_result.scalars().all()
    shoot_map = {str(s.id)[:8]: s for s in shoots}
    
    for shoot_dir in shoot_dirs:
        if not shoot_dir.is_dir():
            continue
        dir_name = shoot_dir.name
        shoot = shoot_map.get(dir_name)
        if not shoot:
            continue
        for img_file in sorted(shoot_dir.glob("*.png")):
            img_key = f"storage/shoots/{dir_name}/{img_file.name}"
            available_content.append({
                "pack_id": None,
                "type": "image",
                "key": img_key,
                "caption": f"{shoot.name} 📸",
                "platforms": ["instagram", "onlyfans"],
            })
        # Check for generated videos
        video_dir = Path(f"storage/videos/{dir_name}")
        if video_dir.exists():
            for vid_file in sorted(video_dir.glob("*.mp4")):
                vid_key = f"storage/videos/{dir_name}/{vid_file.name}"
                available_content.append({
                    "pack_id": None,
                    "type": "video",
                    "key": vid_key,
                    "caption": f"{shoot.name} 🎬",
                    "platforms": ["tiktok", "instagram"],
                })
    
    if not available_content:
        return {"status": "no_content", "message": "No content packs or shoots with generated content found", "posts": 0}
    
    # Platform posting schedules (hour -> platform weight)
    PLATFORM_SCHEDULES = {
        "instagram": [(9, 3), (12, 4), (18, 5)],
        "tiktok": [(7, 4), (12, 3), (21, 5)],
        "onlyfans": [(10, 3), (14, 4), (20, 5)],
        "twitter": [(8, 3), (12, 3), (17, 4)],
    }
    
    # Determine platforms for this persona (check social accounts)
    from app.models import SocialAccount
    socials_result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.persona_id == str(persona_id),
            SocialAccount.status.in_(['active', 'approved'])
        )
    )
    socials = socials_result.scalars().all()
    active_platforms = list(set([s.platform for s in socials])) if socials else ["instagram", "tiktok"]
    
    # Remove old scheduled posts for this persona
    await db.execute(
        text("DELETE FROM scheduled_posts WHERE persona_id = :pid AND status = 'scheduled'"),
        {"pid": str(persona_id)}
    )
    
    # Schedule content across 30 days
    now = datetime.now(timezone.utc)
    posts_created = 0
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
            platform = active_platforms[post_num % len(active_platforms)]
            
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
                tags=[platform, content["type"]],
                scheduled_at=scheduled_at,
                status="scheduled",
            )
            db.add(post)
            posts_created += 1
            content_idx += 1
    
    await db.commit()
    return {"status": "scheduled", "posts": posts_created, "content_used": len(available_content), "days": 30}


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
                            persona_id=p.id,
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

