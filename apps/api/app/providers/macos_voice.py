"""Local macOS text-to-speech provider.

Uses the built-in `say` command and ffmpeg, so no voice data or request leaves
the machine. This is intentionally synthesis-only; it does not clone a real
person's voice.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from app.providers.base import ProviderResult, VoiceProvider


VOICE_DIR = Path(__file__).parent.parent.parent / "storage" / "voices"


class MacOSVoiceProvider(VoiceProvider):
    def __init__(self) -> None:
        self._provider = "macos_say"
        self._voice_ids: dict[str, str] = {}
        VOICE_DIR.mkdir(parents=True, exist_ok=True)

    async def create_voice(
        self,
        name: str,
        description: str = "",
        accent: str = "",
        tone: str = "",
    ) -> ProviderResult:
        if not shutil.which("say"):
            return ProviderResult(False, error="macOS 'say' command is unavailable", provider=self._provider)
        voice_id = f"local-{uuid.uuid4().hex[:12]}"
        self._voice_ids[voice_id] = "Samantha"
        return ProviderResult(
            True,
            data={"voice_id": voice_id, "name": name, "local": True, "voice": "Samantha"},
            provider=self._provider,
        )

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        speed: float = 1.0,
        output_format: str = "wav",
    ) -> ProviderResult:
        if not text.strip():
            return ProviderResult(False, error="Text must not be empty", provider=self._provider)
        if voice_id not in self._voice_ids:
            return ProviderResult(False, error=f"Unknown local voice '{voice_id}'", provider=self._provider)
        if not shutil.which("say") or not shutil.which("ffmpeg"):
            return ProviderResult(False, error="Local voice requires macOS 'say' and ffmpeg", provider=self._provider)

        start = time.monotonic()
        output_format = output_format.lower().lstrip(".")
        if output_format not in {"wav", "m4a", "mp3"}:
            return ProviderResult(False, error=f"Unsupported local audio format '{output_format}'", provider=self._provider)

        with tempfile.TemporaryDirectory(prefix="persona-voice-") as tmp:
            source = Path(tmp) / "speech.aiff"
            output = VOICE_DIR / f"{uuid.uuid4().hex}.{output_format}"
            rate = max(80, min(500, round(200 * speed)))
            try:
                await asyncio.to_thread(
                    subprocess.run,
                    ["say", "-v", self._voice_ids[voice_id], "-r", str(rate), "-o", str(source), text],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                await asyncio.to_thread(
                    subprocess.run,
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", str(source), str(output)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                return ProviderResult(False, error=f"Local voice synthesis failed: {exc}", provider=self._provider)

        return ProviderResult(
            True,
            data={
                "voice_key": str(output.relative_to(VOICE_DIR.parent.parent)),
                "audio_path": str(output),
                "voice_id": voice_id,
                "local": True,
            },
            provider=self._provider,
            latency_ms=(time.monotonic() - start) * 1000,
        )

    async def health_check(self) -> ProviderResult:
        ready = bool(shutil.which("say") and shutil.which("ffmpeg"))
        return ProviderResult(
            ready,
            data={"status": "ready" if ready else "degraded", "provider": self._provider},
            error="" if ready else "macOS 'say' and ffmpeg are required",
            provider=self._provider,
        )
