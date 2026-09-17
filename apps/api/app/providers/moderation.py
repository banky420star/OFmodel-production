"""Persona Production Line — Content Moderation Provider.

HuggingFace NSFW image classification (Falconsai/nsfw_image_detection).
FAIL-CLOSED: if the classifier is unreachable, errors, or is still loading,
the result is treated as UNSAFE and the asset is blocked — generation must
never proceed on unmoderated media.
"""

from __future__ import annotations

from pathlib import Path
import httpx
import structlog

logger = structlog.get_logger()

# HuggingFace inference endpoint
HF_INFERENCE_URL = "https://api-inference.huggingface.co/models/falconsai/nsfw_image_detection"

UNAVAILABLE: dict = {
    "safe": False,
    "nsfw_score": None,
    "safe_score": None,
    "label": "unavailable",
    "provider": "huggingface",
    "moderation_available": False,
}


class ContentModerator:
    """NSFW content classifier — fail-closed semantics."""

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {}
            from app.config import get_settings
            key = get_settings().HUGGINGFACE_API_KEY
            if key:
                headers["Authorization"] = f"Bearer {key}"
            self._client = httpx.AsyncClient(timeout=60, headers=headers)
        return self._client

    @staticmethod
    def _unavailable(note: str, error: str = "") -> dict:
        result = dict(UNAVAILABLE)
        result["note"] = note
        if error:
            result["error"] = error
        return result

    @staticmethod
    def _parse(result) -> dict | None:
        if isinstance(result, list) and len(result) >= 2:
            scores = {item["label"]: item["score"] for item in result}
            nsfw_score = scores.get("nsfw", 0.0)
            safe_score = scores.get("safe", 0.0)
            return {
                "safe": nsfw_score < 0.5,
                "nsfw_score": round(nsfw_score, 4),
                "safe_score": round(safe_score, 4),
                "label": "nsfw" if nsfw_score >= 0.5 else "safe",
                "provider": "huggingface",
                "moderation_available": True,
            }
        return None

    async def classify_image(self, image_path: str) -> dict:
        """Classify an image file. Any failure → unsafe (fail-closed)."""
        try:
            path = Path(image_path)
            if not path.is_absolute():
                path = Path(__file__).resolve().parent.parent.parent / path
            if not path.exists():
                result = dict(UNAVAILABLE)
                result["note"] = f"Image not found: {image_path}"
                return result

            client = await self._get_client()
            resp = await client.post(
                HF_INFERENCE_URL,
                content=path.read_bytes(),
                headers={"Content-Type": "application/octet-stream"},
            )
            if resp.status_code == 200:
                parsed = self._parse(resp.json())
                if parsed:
                    return parsed

            logger.warning("moderation_unavailable", status=resp.status_code, body=resp.text[:200])
            note = "Classifier unavailable — asset blocked (fail-closed)"
            if resp.status_code == 503:
                note = "Moderation model loading — retry shortly; asset blocked (fail-closed)"
            return self._unavailable(note)
        except Exception as e:
            logger.error("moderation_error", error=str(e))
            return self._unavailable("Moderation error — asset blocked (fail-closed)", error=str(e))

    async def classify_bytes(self, image_bytes: bytes) -> dict:
        """Classify raw image bytes. Any failure → unsafe (fail-closed)."""
        try:
            client = await self._get_client()
            resp = await client.post(
                HF_INFERENCE_URL,
                content=image_bytes,
                headers={"Content-Type": "application/octet-stream"},
            )
            if resp.status_code == 200:
                parsed = self._parse(resp.json())
                if parsed:
                    return parsed
            logger.warning("moderation_unavailable", status=resp.status_code)
            return self._unavailable("Classifier unavailable — asset blocked (fail-closed)")
        except Exception as e:
            logger.error("moderation_bytes_error", error=str(e))
            return self._unavailable("Moderation error — asset blocked (fail-closed)", error=str(e))

    async def health_check(self) -> dict:
        """Check if HuggingFace inference is available."""
        try:
            client = await self._get_client()
            resp = await client.get("https://huggingface.co/api/models/falconsai/nsfw_image_detection")
            return {
                "status": "ok" if resp.status_code == 200 else "degraded",
                "model": "falconsai/nsfw_image_detection",
                "provider": "huggingface",
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}


# Singleton
_moderator: ContentModerator | None = None


def get_moderator() -> ContentModerator:
    global _moderator
    if _moderator is None:
        _moderator = ContentModerator()
    return _moderator