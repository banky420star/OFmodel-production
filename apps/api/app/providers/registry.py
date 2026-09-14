"""Persona Studio — Provider Registry.

Dynamically selects mock or real providers based on configuration.
This is the central place where provider swapping happens.

Environment variables:
  PROVIDER_REGISTRY = mock | comfyui | hybrid
  COMFYUI_URL = http://localhost:8188
  ELEVENLABS_API_KEY = sk_...
  WAN_VIDEO_URL = http://localhost:8080
  OLLAMA_URL = http://localhost:11434
  INSTAGRAM_ACCESS_TOKEN = EAAG...
  INSTAGRAM_ACCOUNT_ID = 17841400123456789
"""

from __future__ import annotations
import os
from typing import Type

from app.providers.base import (
    LLMProvider, ImageProvider, VideoProvider, VoiceProvider,
    TrainerProvider, StorageProvider,
)
from app.providers.mocks import (
    MockLLMProvider, MockImageProvider, MockVideoProvider,
    MockVoiceProvider, MockTrainerProvider, MockStorageProvider,
)


class ProviderRegistry:
    """Central registry that provides the correct provider implementation.

    Usage:
        registry = ProviderRegistry()
        image_provider = registry.get_image_provider()
        result = await image_provider.generate(prompt="...", seed=42)
    """

    def __init__(self, mode: str = ""):
        from app.config import get_settings
        settings = get_settings()
        self._mode = mode or settings.PROVIDER_REGISTRY
        self._settings = settings
        self._instances: dict[str, object] = {}
        # Runtime errors observed while a provider actually executed (e.g. a
        # 401 from the image API). Configuration alone can't detect these; the
        # health report turns them into an honest yellow status.
        self.last_runtime_errors: dict[str, str] = {}

    def record_runtime_error(self, kind: str, message: str) -> None:
        """Record that provider `kind` failed at runtime (latest error kept)."""
        self.last_runtime_errors[kind] = message

    def _get(self, key: str, factory):
        if key not in self._instances:
            self._instances[key] = factory()
        return self._instances[key]

    def get_llm_provider(self) -> LLMProvider:
        if self._mode in ("hybrid", "ollama", "openai"):
            try:
                from app.providers.ollama_provider import OllamaLLMProvider
                return self._get("llm", lambda: OllamaLLMProvider(
                    base_url=self._settings.OLLAMA_URL,
                    model=self._settings.OLLAMA_MODEL or "qwen3:8b",
                ))
            except ImportError:
                pass
        return self._get("llm", MockLLMProvider)

    def get_mock_image_provider(self) -> ImageProvider:
        """Deterministic offline image provider — used as the failover target
        when a configured real provider fails at runtime."""
        return self._get("image_mock_fallback", MockImageProvider)

    def get_image_provider(self) -> ImageProvider:
        # Mock mode must stay deterministic and offline — no cloud calls even
        # when API keys happen to be present in the environment.
        if self._mode == "mock":
            return self._get("image", MockImageProvider)
        # Priority 0: EachSense (each::sense) — adult-capable cloud generation;
        # activates when EACHLABS_API_KEY is set, falls through otherwise
        if self._settings.EACHLABS_API_KEY:
            try:
                from app.providers.eachsense_image import EachSenseImageProvider
                return self._get("image", lambda: EachSenseImageProvider(
                    api_key=self._settings.EACHLABS_API_KEY,
                    mode=self._settings.EACHSENSE_MODE,
                ))
            except ImportError:
                pass
        # Priority 1: ComfyUI (local GPU) — only if URL is explicitly set
        if self._mode in ("hybrid", "comfyui") and self._settings.COMFYUI_URL:
            try:
                from app.providers.comfyui import ComfyUIImageProvider
                return self._get("image", lambda: ComfyUIImageProvider(
                    base_url=self._settings.COMFYUI_URL
                ))
            except ImportError:
                pass
        # Priority 2: DashScope Qwen-Image (same key as Wan video, up to 2048px)
        api_key = self._settings.WAN_API_KEY or getattr(self._settings, "DASHSCOPE_API_KEY", "")
        if api_key:
            # Priority 2a: Wan 2.5 image (same key as Wan video) — VERIFIED
            # working on this key (t2i + i2i), unlike qwen-image* which 401s.
            # i2i is the identity-locked path (avatar as visual reference).
            try:
                from app.providers.wan_image import WanImageProvider
                return self._get("image", lambda: WanImageProvider(api_key=api_key))
            except ImportError:
                pass
            # Priority 2b: DashScope Qwen-Image (401 InvalidApiKey on this key
            # as of 2026-09-13 — kept only as a legacy fallback target)
            try:
                from app.providers.dashscope_image import DashScopeImageProvider
                return self._get("image", lambda: DashScopeImageProvider(api_key=api_key))
            except ImportError:
                pass
        # Priority 3: HuggingFace Inference API (free, no GPU needed)
        if self._mode in ("hybrid", "huggingface", "hf"):
            try:
                from app.providers.huggingface import HuggingFaceImageProvider
                return self._get("image", lambda: HuggingFaceImageProvider(
                    api_key=getattr(self._settings, "HUGGINGFACE_API_KEY", ""),
                ))
            except ImportError:
                pass
        return self._get("image", MockImageProvider)

    def get_video_provider(self) -> VideoProvider:
        # DashScope cloud Wan (Alibaba Cloud) — highest priority if API key is set
        if self._mode in ("hybrid", "wan", "wan_video", "dashscope", "wan_cloud"):
            api_key = self._settings.WAN_API_KEY or self._settings.DASHSCOPE_API_KEY
            if api_key:
                try:
                    from app.providers.wan_dashscope import DashScopeWanProvider
                    return self._get("video", lambda: DashScopeWanProvider(api_key=api_key))
                except ImportError:
                    pass
        # Self-hosted Wan server
        if self._mode in ("hybrid", "wan", "wan_video"):
            try:
                from app.providers.wan_video import WanVideoProvider
                return self._get("video", lambda: WanVideoProvider(
                    base_url=self._settings.WAN_VIDEO_URL or "http://localhost:8080"
                ))
            except ImportError:
                pass
        return self._get("video", MockVideoProvider)

    def get_voice_provider(self) -> VoiceProvider:
        if self._mode in ("hybrid", "elevenlabs"):
            try:
                from app.providers.elevenlabs import ElevenLabsVoiceProvider
                return self._get("voice", lambda: ElevenLabsVoiceProvider(
                    api_key=self._settings.ELEVENLABS_API_KEY
                ))
            except ImportError:
                pass
        return self._get("voice", MockVoiceProvider)

    def get_trainer_provider(self) -> TrainerProvider:
        # Mock mode must stay fully deterministic and fast — the real HF
        # LoRA trainer takes 5-15 minutes of GPU time per build.
        if self._mode == "mock":
            return self._get("trainer", MockTrainerProvider)
        # HuggingFace LoRA trainer (runs on MPS/CUDA)
        try:
            from app.providers.hf_trainer import HuggingFaceTrainer
            return self._get("trainer", HuggingFaceTrainer)
        except ImportError:
            pass
        return self._get("trainer", MockTrainerProvider)

    def get_storage_provider(self) -> StorageProvider:
        # MinIO storage when available
        if self._mode in ("hybrid", "minio"):
            try:
                from app.storage import MinIOStorageProvider
                return self._get("storage", MinIOStorageProvider)
            except (ImportError, Exception):
                pass
        # Local filesystem storage (hard drive)
        try:
            from app.providers.filesystem_storage import FileSystemStorageProvider
            return self._get("storage", FileSystemStorageProvider)
        except (ImportError, Exception):
            pass
        return self._get("storage", MockStorageProvider)

    def get_instagram_provider(self):
        """Get Instagram analytics provider (returns None if not configured)."""
        token = self._settings.INSTAGRAM_ACCESS_TOKEN
        account_id = self._settings.INSTAGRAM_ACCOUNT_ID
        if not token or not account_id:
            return None
        try:
            from app.providers.instagram import InstagramProvider
            return InstagramProvider(
                access_token=token,
                instagram_account_id=account_id,
            )
        except ImportError:
            return None

    def health_report(self) -> dict[str, dict]:
        """Get health status of all configured providers.

        Status is derived from actual configuration — never from whether the
        provider class instantiated.

        Status vocabulary:
          green  - a real provider is selected and its credentials/URL are set
          yellow - adapter exists but credentials/service unavailable
          red    - configured provider is failing at runtime
          mock   - the deterministic development provider is in use
        """
        def _name(instance) -> str:
            return getattr(instance, "_provider", type(instance).__name__)

        def _is_mock(instance) -> bool:
            return "mock" in type(instance).__name__.lower()

        llm = self.get_llm_provider()
        image = self.get_image_provider()
        video = self.get_video_provider()
        voice = self.get_voice_provider()
        trainer = self.get_trainer_provider()

        report: dict[str, dict] = {}

        # LLM — Ollama is local and free, so reachability is actually probed.
        if _is_mock(llm):
            report["llm"] = {
                "status": "mock",
                "provider": _name(llm),
                "details": "Deterministic mock LLM — no Ollama interaction",
            }
        else:
            reachable = self._probe_ollama()
            report["llm"] = {
                "status": "green" if reachable else "yellow",
                "provider": _name(llm),
                "details": (
                    f"Ollama reachable at {self._settings.OLLAMA_URL}"
                    if reachable
                    else f"Ollama not reachable at {self._settings.OLLAMA_URL}"
                ),
            }

        # Image — real providers are cloud APIs; probing them would spend
        # quota, so health is key-configuration truth, not a live call.
        # The key check must match the provider the cascade actually selected:
        # an adapter with an empty key is yellow, never green.
        if _is_mock(image):
            report["image"] = {
                "status": "mock",
                "provider": _name(image),
                "details": "Deterministic mock image provider",
            }
        else:
            image_key: str = ""
            if "eachsense" in _name(image).lower():
                image_key = self._settings.EACHLABS_API_KEY
            elif "comfyui" in _name(image).lower():
                image_key = self._settings.COMFYUI_URL
            elif "dashscope" in _name(image).lower():
                image_key = self._settings.WAN_API_KEY or getattr(self._settings, "DASHSCOPE_API_KEY", "")
            elif "huggingface" in _name(image).lower():
                image_key = getattr(self._settings, "HUGGINGFACE_API_KEY", "")
            runtime_err = self.last_runtime_errors.get("image", "")
            if runtime_err:
                report["image"] = {
                    "status": "yellow",
                    "provider": _name(image),
                    "details": f"Configured but failing at runtime — {runtime_err}",
                }
            elif image_key:
                report["image"] = {
                    "status": "green",
                    "provider": _name(image),
                    "details": "Real image provider configured",
                }
            else:
                report["image"] = {
                    "status": "yellow",
                    "provider": _name(image),
                    "details": "Image adapter active but its credential/URL is not set",
                }

        # Video
        if _is_mock(video):
            report["video"] = {
                "status": "mock",
                "provider": _name(video),
                "details": "Deterministic mock video provider",
            }
        else:
            wan_key = bool(self._settings.WAN_API_KEY or self._settings.DASHSCOPE_API_KEY)
            report["video"] = {
                "status": "green" if wan_key else "yellow",
                "provider": _name(video),
                "details": (
                    "DashScope Wan configured"
                    if wan_key
                    else "Self-hosted Wan endpoint configured (no API key)"
                ),
            }

        # Voice
        if _is_mock(voice):
            report["voice"] = {
                "status": "mock",
                "provider": _name(voice),
                "details": "Deterministic mock voice provider",
            }
        elif self._settings.ELEVENLABS_API_KEY:
            report["voice"] = {
                "status": "green",
                "provider": _name(voice),
                "details": "ElevenLabs configured",
            }
        else:
            report["voice"] = {
                "status": "yellow",
                "provider": _name(voice),
                "details": "No credentials configured",
            }

        # Trainer — the HF LoRA trainer runs locally when torch is importable
        if _is_mock(trainer):
            report["trainer"] = {
                "status": "mock",
                "provider": _name(trainer),
                "details": "Deterministic mock trainer",
            }
        else:
            report["trainer"] = {
                "status": "green",
                "provider": _name(trainer),
                "details": "Local LoRA training available",
            }

        # Storage — local filesystem, always available
        storage = self.get_storage_provider()
        report["storage"] = {
            "status": "mock" if _is_mock(storage) else "green",
            "provider": _name(storage),
            "details": "Local filesystem storage" if not _is_mock(storage) else "Mock storage",
        }

        # Instagram — optional
        ig = self.get_instagram_provider()
        if ig:
            report["instagram"] = {
                "status": "green",
                "provider": "instagram_graph_api",
                "details": f"Account {self._settings.INSTAGRAM_ACCOUNT_ID[:8]}…",
            }
        else:
            report["instagram"] = {
                "status": "yellow",
                "provider": "instagram_graph_api",
                "details": "Not configured",
            }

        return report

    def _probe_ollama(self) -> bool:
        """Cheap reachability probe for the local Ollama server."""
        try:
            import httpx

            resp = httpx.get(f"{self._settings.OLLAMA_URL.rstrip('/')}/api/tags", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False


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
