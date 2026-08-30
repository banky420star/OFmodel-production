"""Persona Studio — ElevenLabs Voice Provider.

Connects to ElevenLabs API for voice synthesis and voice cloning.
Requires ELEVENLABS_API_KEY environment variable.

API Reference: https://elevenlabs.io/docs/api-reference
"""

from __future__ import annotations
import random
import time
from typing import Any

import httpx
import structlog

from app.providers.base import VoiceProvider, ProviderResult

logger = structlog.get_logger()


class ElevenLabsVoiceProvider(VoiceProvider):
    """Real voice synthesis via ElevenLabs API.

    Supports:
    - Voice creation from samples
    - Text-to-speech synthesis
    - Multiple languages and accents
    - Streaming audio output
    """

    BASE_URL = "https://api.elevenlabs.io/v1"

    # Voice presets for different accents/tones
    VOICE_PRESETS = {
        "south_african_english": "21m00Tcm4TlvDq8ikWAM",  # Rachel (close match)
        "american_english": "21m00Tcm4TlvDq8ikWAM",  # Rachel
        "british_english": "pNInz6obpgDQGcFmaJgB",  # Adam
        "australian_english": "AZnzlk1XvdvUeBnXmlld",  # Domi
    }

    def __init__(self, api_key: str = ""):
        import os
        self._api_key = api_key or os.getenv("ELEVENLABS_API_KEY", "")
        self._provider = "elevenlabs"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers={"xi-api-key": self._api_key},
                timeout=60.0,
            )
        return self._client

    async def create_voice(
        self,
        name: str,
        description: str = "",
        accent: str = "",
        tone: str = "",
    ) -> ProviderResult:
        """Create a new synthetic voice via ElevenLabs.

        In production, this would upload voice samples to create a cloned voice.
        For initial setup, we use predefined voices and map them.
        """
        if not self._api_key:
            return ProviderResult(
                success=False,
                error="ELEVENLABS_API_KEY not configured",
                provider=self._provider,
            )

        start = time.monotonic()
        try:
            client = await self._get_client()

            # First, list available voices to find a suitable one
            resp = await client.get("/voices")
            resp.raise_for_status()
            voices = resp.json().get("voices", [])

            if not voices:
                return ProviderResult(
                    success=False,
                    error="No voices available in ElevenLabs account",
                    provider=self._provider,
                )

            # Pick a voice based on accent/tone preferences
            selected = voices[0]  # Default to first voice
            accent_lower = accent.lower()
            for voice in voices:
                labels = voice.get("labels", {})
                if accent_lower and accent_lower.replace(" ", "") in (labels.get("accent", "") + labels.get("language", "")).replace(" ", "").lower():
                    selected = voice
                    break

            voice_id = selected["voice_id"]
            elapsed_ms = (time.monotonic() - start) * 1000

            return ProviderResult(
                success=True,
                data={
                    "voice_id": voice_id,
                    "name": selected.get("name", name),
                    "preview_url": selected.get("preview_url", ""),
                    "labels": selected.get("labels", {}),
                    "is_mock": False,
                },
                provider=self._provider,
                latency_ms=elapsed_ms,
            )

        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error="Cannot connect to ElevenLabs API",
                provider=self._provider,
            )
        except Exception as e:
            logger.error("elevenlabs_create_voice_failed", error=str(e))
            return ProviderResult(
                success=False,
                error=str(e),
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        speed: float = 1.0,
        output_format: str = "wav",
    ) -> ProviderResult:
        """Synthesize speech from text using ElevenLabs TTS API."""
        if not self._api_key:
            return ProviderResult(
                success=False,
                error="ELEVENLABS_API_KEY not configured",
                provider=self._provider,
            )

        start = time.monotonic()
        try:
            client = await self._get_client()

            # ElevenLabs output format mapping
            fmt_map = {
                "wav": "pcm_22050",
                "mp3": "mp3_44100_128",
                "opus": "opus_44100",
                "flac": "flac_44100",
            }
            output_fmt = fmt_map.get(output_format, "pcm_22050")

            payload = {
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                    "style": 0.5,
                    "use_speaker_boost": True,
                },
            }

            resp = await client.post(
                f"/text-to-speech/{voice_id}",
                json=payload,
                params={"output_format": output_fmt},
            )
            resp.raise_for_status()

            audio_data = resp.content
            elapsed_ms = (time.monotonic() - start) * 1000

            # Store audio (in real app, upload to MinIO/S3)
            key = f"voices/{voice_id}/{int(time.time())}.{output_format}"

            # Estimate duration: ~22050 samples/sec for pcm, ~44100 for others
            sample_rate = 22050 if "pcm" in output_fmt else 44100
            duration = len(audio_data) / (sample_rate * 2)  # 16-bit = 2 bytes/sample

            return ProviderResult(
                success=True,
                data={
                    "voice_key": key,
                    "voice_id": voice_id,
                    "audio_data": audio_data,
                    "duration_seconds": round(duration, 2),
                    "format": output_format,
                    "sample_rate": sample_rate,
                    "text_length": len(text),
                    "generation_time_ms": int(elapsed_ms),
                    "is_mock": False,
                },
                provider=self._provider,
                latency_ms=elapsed_ms,
            )

        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error="Cannot connect to ElevenLabs API",
                provider=self._provider,
            )
        except httpx.HTTPStatusError as e:
            error_body = ""
            try:
                error_body = e.response.json().get("detail", {}).get("message", str(e))
            except Exception:
                error_body = str(e)
            return ProviderResult(
                success=False,
                error=f"ElevenLabs API error: {error_body}",
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as e:
            logger.error("elevenlabs_synthesize_failed", error=str(e))
            return ProviderResult(
                success=False,
                error=str(e),
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )

    async def health_check(self) -> ProviderResult:
        if not self._api_key:
            return ProviderResult(
                success=False,
                error="ELEVENLABS_API_KEY not configured",
                provider=self._provider,
            )

        try:
            client = await self._get_client()
            resp = await client.get("/user")
            if resp.status_code == 200:
                user = resp.json()
                return ProviderResult(
                    success=True,
                    data={
                        "status": "connected",
                        "tier": user.get("subscription", {}).get("tier", "unknown"),
                        "character_count": user.get("subscription", {}).get("character_count", 0),
                        "character_limit": user.get("subscription", {}).get("character_limit", 0),
                    },
                    provider=self._provider,
                )
            return ProviderResult(
                success=False,
                error=f"ElevenLabs returned {resp.status_code}",
                provider=self._provider,
            )
        except Exception as e:
            return ProviderResult(success=False, error=str(e), provider=self._provider)
