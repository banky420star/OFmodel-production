"""Persona Studio — Content Moderation Provider.

Uses HuggingFace free inference API for NSFW image classification.
No API key required for basic models.

Models used:
- Falconsai/nsfw_image_detection (free, no key)
- For text: can use local Ollama for content policy checking
"""

from __future__ import annotations
import io
import base64
from pathlib import Path
from typing import Any
import httpx
import structlog

logger = structlog.get_logger()

# HuggingFace free inference endpoint
HF_INFERENCE_URL = "https://api-inference.huggingface.co/models/falconsai/nsfw_image_detection"


class ContentModerator:
    """NSFW content classifier using HuggingFace free inference."""

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    async def classify_image(self, image_path: str) -> dict:
        """Classify an image as NSFW or safe.

        Args:
            image_path: Path to image file (relative to API root or absolute)

        Returns:
            {
                "safe": bool,
                "nsfw_score": float (0-1),
                "safe_score": float (0-1),
                "label": "nsfw" | "safe",
                "provider": "huggingface"
            }
        """
        try:
            client = await self._get_client()

            # Read image
            path = Path(image_path)
            if not path.is_absolute():
                path = Path("/Users/bank/Downloads/Gemma_Local_Agent_v3_MODEL_FIX/apps/api") / path

            if not path.exists():
                return {
                    "safe": True,
                    "nsfw_score": 0.0,
                    "safe_score": 1.0,
                    "label": "safe",
                    "provider": "huggingface",
                    "error": f"Image not found: {image_path}",
                }

            image_bytes = path.read_bytes()

            # Classify via HuggingFace
            resp = await client.post(
                HF_INFERENCE_URL,
                content=image_bytes,
                headers={"Content-Type": "application/octet-stream"},
            )

            if resp.status_code == 200:
                result = resp.json()
                # Format: [{"label": "nsfw", "score": 0.95}, {"label": "safe", "score": 0.05}]
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
                    }

            # Fallback: if model is loading, assume safe
            logger.warning("moderation_fallback", status=resp.status_code, body=resp.text[:200])
            return {
                "safe": True,
                "nsfw_score": 0.0,
                "safe_score": 1.0,
                "label": "safe",
                "provider": "huggingface",
                "note": "Model loading or unavailable — defaulted to safe",
            }

        except Exception as e:
            logger.error("moderation_error", error=str(e))
            return {
                "safe": True,
                "nsfw_score": 0.0,
                "safe_score": 1.0,
                "label": "safe",
                "provider": "huggingface",
                "error": str(e),
            }

    async def classify_bytes(self, image_bytes: bytes) -> dict:
        """Classify raw image bytes."""
        try:
            client = await self._get_client()
            resp = await client.post(
                HF_INFERENCE_URL,
                content=image_bytes,
                headers={"Content-Type": "application/octet-stream"},
            )

            if resp.status_code == 200:
                result = resp.json()
                if isinstance(result, list) and len(result) >= 2:
                    scores = {item["label"]: item["score"] for item in result}
                    nsfw_score = scores.get("nsfw", 0.0)
                    safe_score = scores.get("safe", 0.0)
                    return {
                        "safe": nsfw_score < 0.5,
                        "nsfw_score": round(nsfw_score, 4),
                        "safe_score": round(safe_score, 4),
                        "label": "nsfw" if nsfw_score >= 0.5 else "safe",
                    }

            return {"safe": True, "nsfw_score": 0.0, "safe_score": 1.0, "label": "safe"}

        except Exception as e:
            logger.error("moderation_bytes_error", error=str(e))
            return {"safe": True, "nsfw_score": 0.0, "safe_score": 1.0, "label": "safe", "error": str(e)}

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
