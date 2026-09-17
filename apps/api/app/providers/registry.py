"""Persona Production Line — strict provider registry.

Real providers only. Each capability is explicitly selected in .env
(LLM_PROVIDER, IMAGE_PROVIDER, ...) and `resolve()` either returns the named
adapter or raises ProviderNotConfigured. There are no mock providers and no
fallback cascades: a missing credential is an honest, immediate failure.

Test seam: `force_override()` (test environment only) lets tests inject the
fakes from tests/fakes.py without polluting app/.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.providers.base import (
    LLMProvider, ImageProvider, TrainerProvider, StorageProvider,
    VideoProvider, VoiceProvider,
)


class ProviderNotConfigured(Exception):
    """Raised when a capability's provider is missing or lacks credentials."""

    def __init__(self, capability: str, detail: str = ""):
        self.capability = capability
        self.detail = detail
        super().__init__(f"Provider '{capability}' not configured. {detail}")


class ProviderRegistry:
    """Explicit per-capability provider resolution — no fallbacks, no mocks."""

    def __init__(self):
        from app.config import get_settings
        self._settings = get_settings()
        self._instances: dict[str, object] = {}
        self._overrides: dict[str, object] = {}

    # ── resolution ────────────────────────────────────────────────────

    def resolve(self, capability: str):
        """Return the provider instance named by settings, or raise."""
        if capability in self._overrides:
            return self._overrides[capability]
        builder = getattr(self, f"_build_{capability}", None)
        if builder is None:
            raise ProviderNotConfigured(capability, f"Unknown capability '{capability}'")
        if capability not in self._instances:
            self._instances[capability] = builder()
        return self._instances[capability]

    def resolve_optional(self, capability: str):
        """Like resolve(), but returns None instead of raising (optional caps)."""
        try:
            return self.resolve(capability)
        except ProviderNotConfigured:
            return None

    def force_override(self, capability: str, instance) -> None:
        """Test-only override. Refuses outside ENVIRONMENT=test."""
        from app.config import get_settings
        if get_settings().ENVIRONMENT != "test":
            raise RuntimeError("force_override is only allowed in ENVIRONMENT=test")
        self._overrides[capability] = instance

    # ── capability builders (explicit — one env var per capability) ──

    def _require_env(self, capability: str, value: str, env_name: str, what: str):
        if not value:
            raise ProviderNotConfigured(
                capability,
                f"Set {what} in .env (IMAGE_PROVIDER is "
                f"'{getattr(self._settings, capability.upper() + '_PROVIDER', '')}').",
            )
        return value

    def _build_llm(self) -> LLMProvider:
        if self._settings.LLM_PROVIDER != "ollama":
            raise ProviderNotConfigured("llm", f"Unknown LLM_PROVIDER '{self._settings.LLM_PROVIDER}'")
        self._require_env("llm", self._settings.OLLAMA_URL, "", "OLLAMA_URL")
        from app.providers.ollama_provider import OllamaLLMProvider
        return OllamaLLMProvider(
            base_url=self._settings.OLLAMA_URL,
            model=self._settings.OLLAMA_MODEL or "qwen3:4b",
        )

    def _build_image(self) -> ImageProvider:
        s = self._settings
        if s.IMAGE_PROVIDER == "dashscope":
            api_key = s.WAN_API_KEY or s.DASHSCOPE_API_KEY
            self._require_env("image", api_key, "", "WAN_API_KEY or DASHSCOPE_API_KEY")
            from app.providers.dashscope_image import DashScopeImageProvider
            return DashScopeImageProvider(api_key=api_key)
        if s.IMAGE_PROVIDER == "eachsense":
            self._require_env("image", s.EACHLABS_API_KEY, "", "EACHLABS_API_KEY")
            from app.providers.eachsense_image import EachSenseImageProvider
            return EachSenseImageProvider(api_key=s.EACHLABS_API_KEY, mode=s.EACHSENSE_MODE)
        if s.IMAGE_PROVIDER == "comfyui":
            self._require_env("image", s.COMFYUI_URL, "", "COMFYUI_URL")
            from app.providers.comfyui import ComfyUIImageProvider
            return ComfyUIImageProvider(base_url=s.COMFYUI_URL, timeout=s.COMFYUI_TIMEOUT)
        if s.IMAGE_PROVIDER == "huggingface":
            self._require_env("image", s.HUGGINGFACE_API_KEY, "", "HUGGINGFACE_API_KEY")
            from app.providers.huggingface import HuggingFaceImageProvider
            return HuggingFaceImageProvider(api_key=s.HUGGINGFACE_API_KEY)
        raise ProviderNotConfigured("image", f"Unknown IMAGE_PROVIDER '{s.IMAGE_PROVIDER}'")

    def _build_video(self) -> VideoProvider:
        s = self._settings
        if s.VIDEO_PROVIDER == "dashscope_wan":
            api_key = s.WAN_API_KEY or s.DASHSCOPE_API_KEY
            self._require_env("video", api_key, "", "WAN_API_KEY or DASHSCOPE_API_KEY")
            from app.providers.wan_dashscope import DashScopeWanProvider
            return DashScopeWanProvider(api_key=api_key)
        if s.VIDEO_PROVIDER == "wan_server":
            self._require_env("video", s.WAN_VIDEO_URL, "", "WAN_VIDEO_URL")
            from app.providers.wan_video import WanVideoProvider
            return WanVideoProvider(base_url=s.WAN_VIDEO_URL or "http://localhost:8080")
        raise ProviderNotConfigured("video", f"Unknown VIDEO_PROVIDER '{s.VIDEO_PROVIDER}'")

    def _build_voice(self) -> VoiceProvider:
        s = self._settings
        if s.VOICE_PROVIDER == "macos_say":
            from app.providers.macos_voice import MacOSVoiceProvider
            return MacOSVoiceProvider()
        if s.VOICE_PROVIDER != "elevenlabs":
            raise ProviderNotConfigured("voice", f"Unknown VOICE_PROVIDER '{s.VOICE_PROVIDER}'")
        self._require_env("voice", s.ELEVENLABS_API_KEY, "", "ELEVENLABS_API_KEY")
        from app.providers.elevenlabs import ElevenLabsVoiceProvider
        return ElevenLabsVoiceProvider(api_key=s.ELEVENLABS_API_KEY)

    def _build_trainer(self) -> TrainerProvider:
        s = self._settings
        if s.TRAINER_PROVIDER != "hf":
            raise ProviderNotConfigured("trainer", f"Unknown TRAINER_PROVIDER '{s.TRAINER_PROVIDER}'")
        try:
            from app.providers.hf_trainer import HuggingFaceTrainer
        except ImportError as exc:
            raise ProviderNotConfigured(
                "trainer", f"Local LoRA trainer needs torch/diffusers/peft installed ({exc})"
            )
        return HuggingFaceTrainer()

    def _build_storage(self) -> StorageProvider:
        s = self._settings
        if s.STORAGE_PROVIDER == "filesystem":
            from app.providers.filesystem_storage import FileSystemStorageProvider
            return FileSystemStorageProvider()
        raise ProviderNotConfigured("storage", f"Unknown STORAGE_PROVIDER '{s.STORAGE_PROVIDER}'")

    def _build_moderation(self):
        if self._settings.MODERATION_PROVIDER != "huggingface":
            raise ProviderNotConfigured(
                "moderation", f"Unknown MODERATION_PROVIDER '{self._settings.MODERATION_PROVIDER}'"
            )
        self._require_env(
            "moderation", self._settings.HUGGINGFACE_API_KEY, "", "HUGGINGFACE_API_KEY"
        )
        from app.providers.moderation import get_moderator
        return get_moderator()

    def _build_instagram(self):
        s = self._settings
        self._require_env(
            "instagram",
            s.INSTAGRAM_ACCESS_TOKEN if s.INSTAGRAM_ACCOUNT_ID else "",
            "",
            "INSTAGRAM_ACCESS_TOKEN + INSTAGRAM_ACCOUNT_ID",
        )
        from app.providers.instagram import InstagramProvider
        return InstagramProvider(
            access_token=s.INSTAGRAM_ACCESS_TOKEN,
            instagram_account_id=s.INSTAGRAM_ACCOUNT_ID,
        )

    # ── legacy accessor names (routes still call these) ───────────────

    def get_llm_provider(self) -> LLMProvider:
        return self.resolve("llm")

    def get_image_provider(self) -> ImageProvider:
        return self.resolve("image")

    def get_video_provider(self) -> VideoProvider:
        return self.resolve("video")

    def get_voice_provider(self) -> VoiceProvider:
        return self.resolve("voice")

    def get_trainer_provider(self) -> TrainerProvider:
        return self.resolve("trainer")

    def get_storage_provider(self) -> StorageProvider:
        return self.resolve("storage")

    def get_instagram_provider(self):
        return self.resolve_optional("instagram")

    # ── health & self-check ───────────────────────────────────────────

    def health_report(self) -> dict[str, dict]:
        """Per-capability configuration truth.

        Status vocabulary is strictly green | yellow | red — the word "mock"
        can no longer be produced by any code path:
          green  — provider selected AND its credential/endpoint is set
          yellow — resolution failed (missing config / local service down)
          red    — provider resolved but its runtime check failed
        """
        report: dict[str, dict] = {}
        for capability in ("llm", "image", "video", "voice", "trainer", "storage", "moderation"):
            try:
                instance = self.resolve(capability)
            except ProviderNotConfigured as exc:
                report[capability] = {
                    "status": "yellow",
                    "provider": None,
                    "configured": False,
                    "detail": str(exc),
                    "env_hint": _ENV_HINTS.get(capability, ""),
                }
                continue
            except Exception as exc:
                report[capability] = {
                    "status": "red",
                    "provider": None,
                    "configured": False,
                    "detail": str(exc),
                    "env_hint": _ENV_HINTS.get(capability, ""),
                }
                continue
            report[capability] = {
                "status": "green",
                "provider": type(instance).__name__,
                "configured": True,
                "detail": f"{type(instance).__name__} ready",
                "env_hint": "",
            }

        # LLM gets a real reachability probe (local + free).
        if report["llm"]["status"] == "green":
            if self._probe_ollama():
                report["llm"]["detail"] = f"Ollama reachable at {self._settings.OLLAMA_URL}"
            else:
                report["llm"].update(
                    status="yellow",
                    detail=f"Ollama not reachable at {self._settings.OLLAMA_URL} — start it or check OLLAMA_URL",
                )

        # A local Wan adapter is only operational when its server responds.
        # Cloud adapters have credential checks in their own builder.
        if report["video"]["status"] == "green" and self._settings.VIDEO_PROVIDER == "wan_server":
            try:
                import httpx
                response = httpx.get(
                    f"{self._settings.WAN_VIDEO_URL.rstrip('/')}/health",
                    timeout=2.0,
                )
                if response.status_code != 200:
                    report["video"].update(
                        status="yellow",
                        detail=f"Wan video server returned HTTP {response.status_code}",
                    )
                else:
                    report["video"]["detail"] = (
                        f"Wan video server reachable at {self._settings.WAN_VIDEO_URL}"
                    )
            except Exception:
                report["video"].update(
                    status="yellow",
                    detail=f"Wan video server not reachable at {self._settings.WAN_VIDEO_URL}",
                )

        if report["image"]["status"] == "green" and self._settings.IMAGE_PROVIDER == "comfyui":
            try:
                import httpx
                response = httpx.get(
                    f"{self._settings.COMFYUI_URL.rstrip('/')}/system_stats",
                    timeout=2.0,
                )
                if response.status_code != 200:
                    report["image"].update(
                        status="yellow",
                        detail=f"ComfyUI returned HTTP {response.status_code}",
                    )
                else:
                    models = httpx.get(
                        f"{self._settings.COMFYUI_URL.rstrip('/')}/object_info",
                        timeout=2.0,
                    )
                    checkpoints = (
                        models.json()
                        .get("CheckpointLoaderSimple", {})
                        .get("input", {})
                        .get("required", {})
                        .get("ckpt_name", [[], {}])[0]
                    ) if models.status_code == 200 else []
                    expected_checkpoint = self._settings.COMFYUI_CHECKPOINT
                    if expected_checkpoint not in checkpoints:
                        report["image"].update(
                            status="yellow",
                            detail=(
                                f"ComfyUI is reachable, but checkpoint "
                                f"{expected_checkpoint!r} is not installed. Add it under "
                                ".local/ComfyUI/models/checkpoints or set "
                                "COMFYUI_CHECKPOINT to an installed compatible file."
                            ),
                        )
                    else:
                        report["image"]["detail"] = (
                            f"ComfyUI reachable at {self._settings.COMFYUI_URL} "
                            f"(checkpoint: {expected_checkpoint})"
                        )
            except Exception:
                report["image"].update(
                    status="yellow",
                    detail=f"ComfyUI not reachable at {self._settings.COMFYUI_URL}",
                )

        if report["trainer"]["status"] == "green":
            cache_root = Path(
                os.getenv("HF_HOME", Path.home() / ".cache" / "huggingface")
            )
            model_cache = cache_root / "hub" / "models--runwayml--stable-diffusion-v1-5"
            if not model_cache.exists():
                report["trainer"].update(
                    status="yellow",
                    detail=(
                        "Trainer dependencies are installed, but the base model is not cached. "
                        "The first training run will download runwayml/stable-diffusion-v1-5."
                    ),
                )

        # Instagram is optional.
        try:
            self.resolve("instagram")
            report["instagram"] = {
                "status": "green", "provider": "InstagramProvider",
                "configured": True, "detail": "Graph API analytics configured",
            }
        except ProviderNotConfigured as exc:
            report["instagram"] = {
                "status": "yellow", "provider": None, "configured": False,
                "detail": str(exc), "env_hint": "INSTAGRAM_ACCESS_TOKEN + INSTAGRAM_ACCOUNT_ID (optional)",
            }

        return report

    def startup_selfcheck(self) -> list[dict]:
        """One line per capability, for the startup log and /system/providers."""
        report = self.health_report()
        rows = []
        for capability, info in report.items():
            rows.append({
                "capability": capability,
                "provider": info.get("provider"),
                "status": info["status"],
                "configured": info.get("configured", False),
                "detail": info.get("detail", ""),
                "env_hint": info.get("env_hint", ""),
            })
        return rows

    def required_capabilities(self) -> tuple[str, ...]:
        return ("llm", "image", "video", "voice", "trainer", "storage", "moderation")

    def _probe_ollama(self) -> bool:
        """Cheap reachability probe for the local Ollama server."""
        try:
            import httpx

            resp = httpx.get(f"{self._settings.OLLAMA_URL.rstrip('/')}/api/tags", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False


_ENV_HINTS = {
    "llm": "OLLAMA_URL / OLLAMA_MODEL",
    "image": "WAN_API_KEY or DASHSCOPE_API_KEY (IMAGE_PROVIDER=dashscope)",
    "video": "WAN_API_KEY / DASHSCOPE_API_KEY or WAN_VIDEO_URL",
    "voice": "ELEVENLABS_API_KEY or macOS say + ffmpeg",
    "trainer": "install torch/diffusers/peft (TRAINER_PROVIDER=hf)",
    "storage": "STORAGE_PROVIDER=filesystem|minio",
    "moderation": "HUGGINGFACE_API_KEY",
    "instagram": "INSTAGRAM_ACCESS_TOKEN + INSTAGRAM_ACCOUNT_ID",
}


# Singleton registry
_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry


def reset_registry():
    global _registry
    _registry = None