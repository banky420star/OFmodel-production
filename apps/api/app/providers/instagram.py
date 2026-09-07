"""Instagram Graph API — real analytics provider.

Pulls real followers, engagement, reach, impressions, and post performance
from the Instagram Graph API (requires Instagram Business/Creator account
linked to a Facebook Page).

API docs: https://developers.facebook.com/docs/instagram-api/getting-started
"""

from __future__ import annotations

import httpx
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional


INSTAGRAM_API_BASE = "https://graph.facebook.com/v19.0"


@dataclass
class IGMedia:
    """Single Instagram media item with insights."""
    media_id: str
    media_type: str  # IMAGE, VIDEO, CAROUSEL_ALBUM
    caption: str
    timestamp: str
    permalink: str
    like_count: int = 0
    comments_count: int = 0
    # Insights
    reach: int = 0
    impressions: int = 0
    saved: int = 0
    shares: int = 0
    plays: int = 0  # for video/reel


@dataclass
class IGProfile:
    """Instagram profile-level metrics."""
    username: str = ""
    followers_count: int = 0
    follows_count: int = 0
    media_count: int = 0
    bio: str = ""
    profile_pic_url: str = ""


@dataclass
class IGAnalytics:
    """Aggregated analytics from Instagram."""
    profile: IGProfile = field(default_factory=IGProfile)
    recent_media: list[IGMedia] = field(default_factory=list)
    # Aggregated (last 30 days)
    total_reach: int = 0
    total_impressions: int = 0
    total_engagement: int = 0  # likes + comments + saves + shares
    total_likes: int = 0
    total_comments: int = 0
    total_saves: int = 0
    total_shares: int = 0
    avg_engagement_rate: float = 0.0
    synced_at: str = ""


class InstagramProvider:
    """Real Instagram Graph API client."""

    def __init__(self, access_token: str, instagram_account_id: str = ""):
        self.access_token = access_token
        self.ig_account_id = instagram_account_id
        self._client = httpx.AsyncClient(timeout=30.0)

    async def _get(self, path: str, params: dict | None = None) -> dict:
        """Make authenticated GET request to IG Graph API."""
        url = f"{INSTAGRAM_API_BASE}{path}"
        p = params or {}
        p["access_token"] = self.access_token
        resp = await self._client.get(url, params=p)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise Exception(f"Instagram API error: {data['error'].get('message', data['error'])}")
        return data

    async def get_profile(self) -> IGProfile:
        """Get profile-level data (followers, follows, media count)."""
        data = await self._get(f"/{self.ig_account_id}", {
            "fields": "username,followers_count,follows_count,media_count,biography,profile_picture_url"
        })
        return IGProfile(
            username=data.get("username", ""),
            followers_count=data.get("followers_count", 0),
            follows_count=data.get("follows_count", 0),
            media_count=data.get("media_count", 0),
            bio=data.get("biography", ""),
            profile_pic_url=data.get("profile_picture_url", ""),
        )

    async def get_recent_media(self, limit: int = 25) -> list[IGMedia]:
        """Get recent media with insights."""
        # Step 1: Get media IDs
        data = await self._get(f"/{self.ig_account_id}/media", {
            "fields": "id,media_type,caption,timestamp,permalink,like_count,comments_count",
            "limit": str(limit),
        })
        media_items = data.get("data", [])

        # Step 2: Get insights for each media item
        media_list = []
        for item in media_items:
            media = IGMedia(
                media_id=item["id"],
                media_type=item.get("media_type", "IMAGE"),
                caption=(item.get("caption") or "")[:200],
                timestamp=item.get("timestamp", ""),
                permalink=item.get("permalink", ""),
                like_count=item.get("like_count", 0),
                comments_count=item.get("comments_count", 0),
            )
            # Fetch insights for this media
            try:
                insights = await self._get(f"/{item['id']}/insights", {
                    "metric": "impressions,reach,saved,shares" + (",plays" if item.get("media_type") == "VIDEO" else ""),
                })
                for metric in insights.get("data", []):
                    name = metric.get("name", "")
                    val = metric.get("values", [{}])[0].get("value", 0)
                    if name == "impressions":
                        media.impressions = val
                    elif name == "reach":
                        media.reach = val
                    elif name == "saved":
                        media.saved = val
                    elif name == "shares":
                        media.shares = val
                    elif name == "plays":
                        media.plays = val
            except Exception:
                pass  # Insights may not be available for all media

            media_list.append(media)

        return media_list

    async def get_account_insights(self, period: str = "day", days: int = 30) -> list[dict]:
        """Get account-level insights over a time period."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        data = await self._get(f"/{self.ig_account_id}/insights", {
            "metric": "impressions,reach,profile_views,follower_count",
            "period": period,
            "since": str(int(start.timestamp())),
            "until": str(int(end.timestamp())),
        })
        return data.get("data", [])

    async def sync_analytics(self) -> IGAnalytics:
        """Pull all real Instagram data and return aggregated analytics."""
        profile = await self.get_profile()
        recent_media = await self.get_recent_media(limit=25)

        # Aggregate
        total_likes = sum(m.like_count for m in recent_media)
        total_comments = sum(m.comments_count for m in recent_media)
        total_saves = sum(m.saved for m in recent_media)
        total_shares = sum(m.shares for m in recent_media)
        total_reach = sum(m.reach for m in recent_media)
        total_impressions = sum(m.impressions for m in recent_media)
        total_engagement = total_likes + total_comments + total_saves + total_shares

        # Engagement rate = (engagement / followers) * 100
        avg_engagement = 0.0
        if profile.followers_count > 0 and recent_media:
            avg_engagement = round(
                (total_engagement / len(recent_media) / profile.followers_count) * 100, 2
            )

        return IGAnalytics(
            profile=profile,
            recent_media=recent_media,
            total_reach=total_reach,
            total_impressions=total_impressions,
            total_engagement=total_engagement,
            total_likes=total_likes,
            total_comments=total_comments,
            total_saves=total_saves,
            total_shares=total_shares,
            avg_engagement_rate=avg_engagement,
            synced_at=datetime.now(timezone.utc).isoformat(),
        )

    async def health_check(self):
        """Quick check that the API token is valid."""
        try:
            data = await self._get(f"/{self.ig_account_id}", {
                "fields": "username,followers_count"
            })
            return True, data.get("username", "unknown"), data.get("followers_count", 0)
        except Exception as e:
            return False, str(e), 0
