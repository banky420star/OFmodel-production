"""Persona Studio — Provider Registry.

Dynamically selects mock or real providers based on configuration.
This is the central place where provider swapping happens.

Environment variables:
  PROVIDER_REGISTRY = mock | comfyui | hybrid
  PROVIDER_COOLDOWN_SECONDS = 900  (TTL blacklist after quota/rate failures)
  COMFYUI_URL = http://localhost:8188
  ELEVENLABS_API_KEY = sk_...
  WAN_VIDEO_URL = http://localhost:8080
  OLLAMA_URL = http://localhost:11434
  INSTAGRAM_ACCESS_TOKEN = EAAG...
  INSTAGRAM_ACCOUNT_ID = 17841400123456789
"""

from __future__ import annotations
import asyncio
import logging
import os
import re
import threading
import time

from app.providers.base import (
    LLMProvider, ImageProvider, VideoProvider, VoiceProvider,
    TrainerProvider, StorageProvider, ProviderResult,
)
from app.providers.mocks import (
    MockLLMProvider, MockImageProvider, MockVideoProvider,
    MockVoiceProvider, MockTrainerProvider, MockStorageProvider,
)

logger = logging.getLogger(__name__)

# TTL a provider is skipped after a quota/rate/unavailability failure
# (PROVIDER_COOLDOWN_SECONDS). One default, shared by the registry chain.
DEFAULT_PROVIDER_COOLDOWN_SECONDS = 900

# Error fragments meaning "the provider is out of quota / rate limited /
# otherwise unavailable right now" — the provider gets a TTL cooldown instead
# of being failed permanently. HTTP status codes are matched on word
# boundaries so a body mentioning "4290 bytes" is not mistaken for a 429.
_QUOTA_ERROR_MARKERS = (
    "quota", "rate limit", "ratelimit", "rate_limit", "too many requests",
    "throttl", "exhausted", "billing", "payment required",
)
_AUTH_ERROR_MARKERS = (
    "unauthorized", "forbidden", "invalid api key", "auth failed",
    "authentication", "not authenticated",
)
_CONNECTIVITY_ERROR_MARKERS = (
    "cannot connect", "connect error", "connection refused", "connection reset",
    "connection closed", "unreachable", "timed out", "did not complete within",
    "getaddrinfo failed", "network error", "no route to host",
)
_STATUS_CODE_PATTERN = re.compile(r"\b(?:401|403|429|503)\b")


def _is_unavailable_error(error: str) -> bool:
    """True if the error suggests the provider is temporarily unavailable."""
    e = (error or "").lower()
    if _STATUS_CODE_PATTERN.search(e):
        return True
    return any(
        marker in e
        for marker in _QUOTA_ERROR_MARKERS + _AUTH_ERROR_MARKERS + _CONNECTIVITY_ERROR_MARKERS
    )


class ChainImageProvider(ImageProvider):
    """Tries image providers in priority order until one succeeds.

    When a provider fails with a quota/rate/unavailability error it is
    blacklisted for ``cooldown_seconds`` (PROVIDER_COOLDOWN_SECONDS, default
    900) instead of permanently — later requests skip it while the cooldown
    is active and automatically retry it once it expires. Each attempt is
    capped at ``attempt_timeout`` seconds (PROVIDER_TIMEOUT_SECONDS) so one
    down provider cannot stall a request for minutes.

    The chain may be used concurrently (identity_engine runs it through
    ThreadPoolExecutor + asyncio.run), so cooldown state is lock-guarded.
    """

    def __init__(
        self,
        providers: list[tuple[str, ImageProvider]],
        cooldown_seconds: float = DEFAULT_PROVIDER_COOLDOWN_SECONDS,
        attempt_timeout: float = 120.0,
    ):
        self._chain = providers
        self._cooldown_seconds = max(0.0, float(cooldown_seconds))
        # 0 disables the per-attempt timeout entirely
        self._attempt_timeout = float(attempt_timeout) if attempt_timeout else None
        self._lock = threading.Lock()
        self._blocked_until: dict[str, float] = {}
        self._provider = "image_chain(" + ",".join(name for name, _ in providers) + ")"

    @property
    def provider_names(self) -> list[str]:
        return [name for name, _ in self._chain]

    def _available(self) -> list[tuple[str, ImageProvider]]:
        now = time.monotonic()
        with self._lock:
            self._blocked_until = {
                name: until for name, until in self._blocked_until.items() if until > now
            }
            blocked = set(self._blocked_until)
        return [(n, p) for n, p in self._chain if n not in blocked]

    def _block(self, name: str) -> None:
        with self._lock:
            self._blocked_until[name] = time.monotonic() + self._cooldown_seconds

    def _record_failure(self, name: str, error: str) -> None:
        if _is_unavailable_error(error):
            self._block(name)
            logger.warning(
                "provider_cooldown_started provider=%s cooldown_s=%s error=%s",
                name, self._cooldown_seconds, (error or "")[:200],
            )

    async def _attempt(self, provider_call) -> ProviderResult:
        """Run one provider call under the per-attempt timeout budget."""
        if self._attempt_timeout:
            return await asyncio.wait_for(provider_call(), timeout=self._attempt_timeout)
        return await provider_call()

    async def _try_chain(
        self,
        attempt,
        candidates: list[tuple[str, ImageProvider]] | None = None,
    ) -> ProviderResult:
        """Run `attempt(provider)` against each candidate in priority order.

        Each provider is tried at most once per call; a provider that fails
        with a quota/rate/unavailability error is put on TTL cooldown. Returns
        the last failure if every candidate fails.
        """
        if candidates is None:
            candidates = self._available()
        if not candidates:
            return ProviderResult(
                success=False,
                error="No image provider available (all on cooldown)",
                provider=self._provider,
            )

        last: ProviderResult | None = None
        for name, provider in candidates:
            try:
                result = await self._attempt(lambda p=provider: attempt(p))
            except asyncio.TimeoutError:
                result = ProviderResult(
                    success=False,
                    error=f"{name} provider timed out after {self._attempt_timeout:.0f}s",
                    provider=name,
                )
            except Exception as e:
                result = ProviderResult(
                    success=False, error=f"{name} provider error: {e}", provider=name
                )
            if result.success:
                return result
            last = result
            self._record_failure(name, result.error)

        return last  # type: ignore[return-value]  # candidates was non-empty

    async def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        steps: int = 30,
        cfg_scale: float = 7.0,
        seed: int = -1,
        lora_path: str = "",
        lora_strength: float = 0.8,
    ) -> ProviderResult:
        return await self._try_chain(lambda p: p.generate(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            steps=steps,
            cfg_scale=cfg_scale,
            seed=seed,
            lora_path=lora_path,
            lora_strength=lora_strength,
        ))

    async def img2img(
        self, image_key: str, prompt: str, strength: float = 0.75, **kwargs
    ) -> ProviderResult:
        return await self._try_chain(
            lambda p: p.img2img(image_key, prompt, strength, **kwargs)
        )

    async def edit_image(
        self,
        reference_image_bytes: bytes,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        seed: int = -1,
    ) -> ProviderResult:
        candidates = self._available()
        edit_capable = [(n, p) for n, p in candidates if hasattr(p, "edit_image")]
        if not edit_capable:
            # Never silently drop the reference image — identity lock depends
            # on it. Fail explicitly instead of regenerating from text alone.
            return ProviderResult(
                success=False,
                error=(
                    "Image editing with a reference image is not supported by any "
                    "configured image provider "
                    f"({', '.join(n for n, _ in candidates) or 'none available'}); "
                    "reference image was NOT applied"
                ),
                provider=self._provider,
            )
        return await self._try_chain(
            lambda p: p.edit_image(
                reference_image_bytes,
                prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                seed=seed,
            ),
            candidates=edit_capable,
        )

    async def upscale(self, image_key: str, scale: int = 2) -> ProviderResult:
        return await self._try_chain(lambda p: p.upscale(image_key, scale))

    async def health_check(self) -> ProviderResult:
        available = self._available()
        if not available:
            return ProviderResult(
                success=False, error="All image providers on cooldown",
                provider=self._provider,
            )
        return await available[0][1].health_check()


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

    def _build_image_chain(self) -> list[tuple[str, ImageProvider]]:
        """Build the prioritized image provider chain.

        Priority: ComfyUI → DashScope → HuggingFace → Pollinations → Mock.
        Mock is always last so generation never hard-fails offline.
        """
        chain: list[tuple[str, ImageProvider]] = []
        # Priority 1: ComfyUI (local GPU) — only if URL is explicitly set
        if self._mode in ("hybrid", "comfyui") and self._settings.COMFYUI_URL:
            try:
                from app.providers.comfyui import ComfyUIImageProvider
                chain.append(("comfyui", ComfyUIImageProvider(
                    base_url=self._settings.COMFYUI_URL
                )))
            except ImportError:
                pass
        # Priority 2: DashScope Qwen-Image (same key as Wan video, up to 2048px)
        api_key = self._settings.WAN_API_KEY or getattr(self._settings, "DASHSCOPE_API_KEY", "")
        if api_key:
            try:
                from app.providers.dashscope_image import DashScopeImageProvider
                chain.append(("dashscope", DashScopeImageProvider(api_key=api_key)))
            except ImportError:
                pass
        # Priority 3: HuggingFace Inference API — only with an API key
        # (anonymous inference always 401s, so don't waste a round-trip)
        hf_key = getattr(self._settings, "HUGGINGFACE_API_KEY", "")
        if hf_key and self._mode in ("hybrid", "huggingface", "hf"):
            try:
                from app.providers.huggingface import HuggingFaceImageProvider
                chain.append(("huggingface", HuggingFaceImageProvider(api_key=hf_key)))
            except ImportError:
                pass
        # Priority 4: Pollinations.ai (free, no key) — kept in the chain so
        # generation still produces real images when DashScope quota is out
        # and no other provider is configured.
        try:
            from app.providers.pollinations import PollinationsImageProvider
            chain.append(("pollinations", PollinationsImageProvider()))
        except ImportError:
            pass
        # Priority 5: Mock (always last — guarantees offline generation)
        chain.append(("mock", self._get("mock_image", MockImageProvider)))
        return chain

    def get_image_provider(self) -> ImageProvider:
        return self._get("image", lambda: ChainImageProvider(
            self._build_image_chain(),
            cooldown_seconds=self._settings.PROVIDER_COOLDOWN_SECONDS,
            attempt_timeout=self._settings.PROVIDER_TIMEOUT_SECONDS,
        ))

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

        Reports based on configuration, not live connectivity pings.
        This avoids blocking the API on slow provider health checks.
        """
        providers_config = {
            "llm": (self.get_llm_provider, self._settings.OLLAMA_URL),
            "image": (self.get_image_provider, self._settings.HUGGINGFACE_API_KEY or self._settings.COMFYUI_URL),
            "video": (self.get_video_provider, self._settings.WAN_API_KEY or self._settings.DASHSCOPE_API_KEY),
            "voice": (self.get_voice_provider, self._settings.ELEVENLABS_API_KEY),
            "trainer": (self.get_trainer_provider, "configured"),
            "storage": (self.get_storage_provider, "./storage/"),
        }
        report = {}
        for name, (getter, config_value) in providers_config.items():
            instance = getter()
            if isinstance(instance, ChainImageProvider):
                names = instance.provider_names
                # Mock mode is fully deterministic/offline — never report green
                is_mock = all(n.startswith("mock") for n in names) or self._mode == "mock"
                provider_name = "+".join(names)
            else:
                is_mock = "mock" in type(instance).__name__.lower()
                provider_name = getattr(instance, "_provider", type(instance).__name__).replace("mock_", "")
            report[name] = {
                "status": "green" if not is_mock or name in ("storage",) else "yellow",
                "provider": provider_name,
                "details": "Configured" if config_value else "Using defaults",
            }

        # Instagram — optional
        ig = self.get_instagram_provider()
        if ig:
            report["instagram"] = {
                "status": "green",
                "provider": "instagram_graph_api",
                "details": f"Account {self._settings.INSTAGRAM_ACCOUNT_ID[:8]}...",
            }
        else:
            report["instagram"] = {
                "status": "yellow",
                "provider": "instagram_graph_api",
                "details": "Not configured",
            }

        return report


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
