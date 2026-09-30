"""TikTok Login Kit + Display API — real analytics provider.

Two distinct pieces, mirroring how TikTok splits the responsibility:

  TikTokClient     — app-level (TIKTOK_CLIENT_KEY / _SECRET). Builds the
                     authorize URL, exchanges the one-time code for tokens,
                     and refreshes an expired token. One per installation.
  TikTokAPIClient  — account-level (an access_token). Reads the authorised
                     account's own profile and video list. One per connected
                     SocialAccount.

The Display API is strictly read-only: it exposes the account's *own* data.
There is no endpoint here — and none in the public API — for creating
accounts, following, or liking other users' videos.

API docs:
  https://developers.tiktok.com/doc/login-kit-web
  https://developers.tiktok.com/doc/tiktok-api-v2-get-user-info
  https://developers.tiktok.com/doc/tiktok-api-v2-video-list
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx


AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
API_BASE = "https://open.tiktokapis.com/v2"

# Read-only scopes only. video.publish / video.upload are deliberately absent —
# this integration never posts on the user's behalf.
DEFAULT_SCOPES = ("user.info.basic", "user.info.profile", "user.info.stats", "video.list")

USER_FIELDS = (
    "open_id,union_id,avatar_url,display_name,profile_deep_link,"
    "follower_count,following_count,likes_count,video_count"
)
VIDEO_FIELDS = (
    "id,title,video_description,duration,cover_image_url,share_url,"
    "create_time,view_count,like_count,comment_count,share_count"
)


class TikTokAuthError(Exception):
    """The app credentials or the OAuth exchange failed."""


class TikTokTokenExpired(Exception):
    """The account's access token is expired or was revoked — reconnect needed."""


@dataclass
class TikTokTokens:
    """Result of an authorization-code exchange or a refresh."""

    access_token: str
    refresh_token: str = ""
    open_id: str = ""
    scope: str = ""
    expires_in: int = 0
    refresh_expires_in: int = 0
    obtained_at: str = ""


@dataclass
class TikTokProfile:
    """Profile-level metrics for the authorised account."""

    open_id: str = ""
    display_name: str = ""
    avatar_url: str = ""
    follower_count: int = 0
    following_count: int = 0
    likes_count: int = 0  # total likes received across all videos
    video_count: int = 0


@dataclass
class TikTokVideo:
    """A single video published by the authorised account."""

    video_id: str
    title: str = ""
    description: str = ""
    created_at: str = ""
    share_url: str = ""
    duration: int = 0
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0


@dataclass
class TikTokAnalytics:
    """Aggregated analytics pulled from the Display API."""

    profile: TikTokProfile = field(default_factory=TikTokProfile)
    recent_videos: list[TikTokVideo] = field(default_factory=list)
    total_views: int = 0
    total_likes: int = 0
    total_comments: int = 0
    total_shares: int = 0
    total_engagement: int = 0
    avg_engagement_rate: float = 0.0
    synced_at: str = ""


class TikTokClient:
    """App-level TikTok OAuth client — no account state."""

    def __init__(self, client_key: str, client_secret: str, redirect_uri: str):
        self.client_key = client_key
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._client = httpx.AsyncClient(timeout=30.0)

    def authorize_url(self, state: str, scopes: tuple[str, ...] = DEFAULT_SCOPES) -> str:
        """Build the URL the operator's browser is sent to.

        `state` is the CSRF token; the callback must prove it round-tripped.
        """
        params = {
            "client_key": self.client_key,
            "response_type": "code",
            "scope": ",".join(scopes),
            "redirect_uri": self.redirect_uri,
            "state": state,
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    async def _post_token(self, payload: dict) -> TikTokTokens:
        resp = await self._client.post(
            TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            data = resp.json()
        except Exception as exc:  # non-JSON body from the token endpoint
            raise TikTokAuthError(f"TikTok token endpoint returned HTTP {resp.status_code}") from exc

        # TikTok reports OAuth failures with HTTP 200 and an error body.
        if data.get("error") or not data.get("access_token"):
            raise TikTokAuthError(
                data.get("error_description")
                or data.get("error")
                or f"TikTok token exchange failed (HTTP {resp.status_code})"
            )
        return TikTokTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
            open_id=data.get("open_id", ""),
            scope=data.get("scope", ""),
            expires_in=int(data.get("expires_in") or 0),
            refresh_expires_in=int(data.get("refresh_expires_in") or 0),
            obtained_at=datetime.now(timezone.utc).isoformat(),
        )

    async def exchange_code(self, code: str) -> TikTokTokens:
        """Trade the one-time authorization code for an access token."""
        return await self._post_token({
            "client_key": self.client_key,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        })

    async def refresh(self, refresh_token: str) -> TikTokTokens:
        """Refresh an expired access token.

        TikTok rotates the refresh token, so the caller must persist the new one.
        """
        return await self._post_token({
            "client_key": self.client_key,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })


class TikTokAPIClient:
    """Per-account Display API reader for one authorised TikTok account."""

    def __init__(self, access_token: str, open_id: str = ""):
        self.access_token = access_token
        self.open_id = open_id
        self._client = httpx.AsyncClient(timeout=30.0)

    async def _get(self, path: str, params: dict | None = None) -> dict:
        """Authenticated GET against the Display API."""
        resp = await self._client.get(
            f"{API_BASE}{path}",
            params=params or {},
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        if resp.status_code == 401:
            raise TikTokTokenExpired(
                "TikTok access token is expired or revoked — reconnect the account"
            )
        resp.raise_for_status()
        data = resp.json()
        error = data.get("error") or {}
        # Success is signalled by error.code == "ok", not by the key's absence.
        code = error.get("code", "ok")
        if code not in ("ok", "", None):
            if code in ("access_token_invalid", "access_token_expired", "scope_not_authorized"):
                raise TikTokTokenExpired(error.get("message", code))
            raise TikTokAuthError(f"TikTok API error ({code}): {error.get('message', '')}")
        return data

    async def get_profile(self) -> TikTokProfile:
        """Read the authorised account's own profile stats."""
        data = await self._get("/user/info/", {"fields": USER_FIELDS})
        user = (data.get("data") or {}).get("user") or {}
        return TikTokProfile(
            open_id=user.get("open_id", self.open_id),
            display_name=user.get("display_name", ""),
            avatar_url=user.get("avatar_url", ""),
            follower_count=user.get("follower_count", 0),
            following_count=user.get("following_count", 0),
            likes_count=user.get("likes_count", 0),
            video_count=user.get("video_count", 0),
        )

    async def get_recent_videos(self, limit: int = 20) -> list[TikTokVideo]:
        """Read the authorised account's own published videos."""
        # video/list is a POST with a JSON body, unlike the rest of the API.
        resp = await self._client.post(
            f"{API_BASE}/video/list/",
            params={"fields": VIDEO_FIELDS},
            json={"max_count": min(limit, 20)},
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code == 401:
            raise TikTokTokenExpired(
                "TikTok access token is expired or revoked — reconnect the account"
            )
        resp.raise_for_status()
        data = resp.json()
        error = data.get("error") or {}
        code = error.get("code", "ok")
        if code == "scope_not_authorized":
            raise TikTokAuthError(
                "This TikTok authorization lacks the video.list scope — reconnect "
                "the account and approve video access"
            )
        if code not in ("ok", "", None):
            raise TikTokAuthError(f"TikTok API error ({code}): {error.get('message', '')}")

        videos = []
        for item in (data.get("data") or {}).get("videos", []):
            created = item.get("create_time")
            videos.append(TikTokVideo(
                video_id=item.get("id", ""),
                title=(item.get("title") or "")[:200],
                description=(item.get("video_description") or "")[:200],
                created_at=(
                    datetime.fromtimestamp(int(created), tz=timezone.utc).isoformat()
                    if created else ""
                ),
                share_url=item.get("share_url", ""),
                duration=item.get("duration", 0),
                view_count=item.get("view_count", 0),
                like_count=item.get("like_count", 0),
                comment_count=item.get("comment_count", 0),
                share_count=item.get("share_count", 0),
            ))
        return videos

    async def sync_analytics(self, video_limit: int = 20) -> TikTokAnalytics:
        """Pull the account's own profile + videos and aggregate them."""
        profile = await self.get_profile()
        videos = await self.get_recent_videos(limit=video_limit)

        total_views = sum(v.view_count for v in videos)
        total_likes = sum(v.like_count for v in videos)
        total_comments = sum(v.comment_count for v in videos)
        total_shares = sum(v.share_count for v in videos)
        total_engagement = total_likes + total_comments + total_shares

        avg_engagement = 0.0
        if profile.follower_count > 0 and videos:
            avg_engagement = round(
                (total_engagement / len(videos) / profile.follower_count) * 100, 2
            )

        return TikTokAnalytics(
            profile=profile,
            recent_videos=videos,
            total_views=total_views,
            total_likes=total_likes,
            total_comments=total_comments,
            total_shares=total_shares,
            total_engagement=total_engagement,
            avg_engagement_rate=avg_engagement,
            synced_at=datetime.now(timezone.utc).isoformat(),
        )

    async def health_check(self) -> tuple[bool, str, int]:
        """Confirm the stored token still reads this account."""
        try:
            profile = await self.get_profile()
            return True, profile.display_name or "unknown", profile.follower_count
        except Exception as exc:
            return False, str(exc)[:200], 0
