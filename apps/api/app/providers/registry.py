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

    def clear_override(self, capability: str) -> None:
        """Remove a test override, falling back to the configured provider.

        The counterpart to force_override: the registry is a module-level
        singleton shared by a whole test session, so a test that swaps out a
        provider must be able to put the real one back instead of leaking its
        stub into every later test.
        """
        from app.config import get_settings
        if get_settings().ENVIRONMENT != "test":
            raise RuntimeError("clear_override is only allowed in ENVIRONMENT=test")
        self._overrides.pop(capability, None)

    # ── capability builders (explicit — one env var per capability) ──

    def _require_env(self, capability: str, value: str, env_name: str, what: str):
        if not value:
            # Only name a <CAPABILITY>_PROVIDER selector when one actually exists —
            # instagram/tiktok have none, and the old hardcoded IMAGE_PROVIDER text
            # rendered as "IMAGE_PROVIDER is ''" for them.
            selector = getattr(self._settings, capability.upper() + "_PROVIDER", "")
            detail = f"Set {what} in .env"
            if selector:
                detail += f" ({capability.upper()}_PROVIDER is '{selector}')"
            raise ProviderNotConfigured(capability, detail + ".")
        return value

    def _build_ollama(self):
        self._require_env("llm", self._settings.OLLAMA_URL, "", "OLLAMA_URL")
        from app.providers.ollama_provider import OllamaLLMProvider
        return OllamaLLMProvider(
            base_url=self._settings.OLLAMA_URL,
            model=self._settings.OLLAMA_MODEL or "qwen3:4b",
        )

    def _build_openrouter(self):
        self._require_env("llm", self._settings.OPENROUTER_API_KEY, "", "OPENROUTER_API_KEY")
        from app.providers.openrouter_provider import OpenRouterLLMProvider
        return OpenRouterLLMProvider(
            api_key=self._settings.OPENROUTER_API_KEY,
            model=self._settings.LLM_OPENROUTER_MODEL,
            timeout=self._settings.LLM_FALLBACK_HOP_TIMEOUT_SECONDS,
        )

    def _build_llm(self) -> LLMProvider:
        """The LLM chain.

        `LLM_PROVIDER` names the head, so a single-provider install behaves
        exactly as before. When `LLM_FALLBACK_ENABLED` is on and a *second*
        provider has credentials, it is appended as a real fallback — see
        app/providers/fallback_llm.py for why that is not the mock cascade this
        project removed.

        The result is always wrapped, even with one member, so `served_by` is
        stamped on every reply and "who answered" never has to be inferred from
        which class happened to be configured.
        """
        s = self._settings
        head = s.LLM_PROVIDER

        if head == "ollama":
            members: list[tuple[str, LLMProvider]] = [("ollama", self._build_ollama())]
        elif head == "openrouter":
            members = [("openrouter", self._build_openrouter())]
        else:
            raise ProviderNotConfigured(
                "llm", f"Unknown LLM_PROVIDER '{head}'"
            )

        if s.LLM_FALLBACK_ENABLED:
            names = {name for name, _ in members}
            if "openrouter" not in names and s.OPENROUTER_API_KEY:
                members.append(("openrouter", self._build_openrouter()))
            elif "ollama" not in names and s.OLLAMA_URL:
                members.append(("ollama", self._build_ollama()))

        from app.providers.fallback_llm import FallbackLLMProvider
        return FallbackLLMProvider(members, enabled=s.LLM_FALLBACK_ENABLED)

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
        if s.VIDEO_PROVIDER == "comfyui":
            # Reuses the ComfyUI server already running for images. Local and
            # free, and image-to-video only — SVD cannot generate from a prompt,
            # which its text_to_video says out loud rather than faking.
            self._require_env("video", s.COMFYUI_URL, "", "COMFYUI_URL")
            from app.providers.comfyui_video import ComfyUIVideoProvider
            return ComfyUIVideoProvider(
                base_url=s.COMFYUI_URL,
                timeout=s.COMFYUI_TIMEOUT,
                checkpoint=s.COMFYUI_VIDEO_CHECKPOINT,
            )
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

    def _build_tiktok(self):
        """App-level TikTok OAuth client.

        Only the developer-app credentials are checked here. Whether a *persona*
        has connected an account is per-row state on SocialAccount.api_token,
        not installation config — so this capability being green means "the app
        can start an OAuth flow", not "an account is connected".
        """
        s = self._settings
        missing = [
            name for name, value in (
                ("TIKTOK_CLIENT_KEY", s.TIKTOK_CLIENT_KEY),
                ("TIKTOK_CLIENT_SECRET", s.TIKTOK_CLIENT_SECRET),
                ("TIKTOK_REDIRECT_URI", s.TIKTOK_REDIRECT_URI),
            ) if not value
        ]
        if missing:
            raise ProviderNotConfigured(
                "tiktok",
                f"Set {' + '.join(missing)} in .env (TikTok developer app "
                "credentials; the redirect URI must be HTTPS).",
            )
        from app.providers.tiktok import TikTokClient
        return TikTokClient(
            client_key=s.TIKTOK_CLIENT_KEY,
            client_secret=s.TIKTOK_CLIENT_SECRET,
            redirect_uri=s.TIKTOK_REDIRECT_URI,
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

    def get_tiktok_provider(self):
        return self.resolve_optional("tiktok")

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

        # LLM: probe every member of the chain, and report the chain itself.
        #
        # This replaced a probe that only ever asked Ollama. With a second
        # provider configured, that check reported yellow forever no matter how
        # healthy OpenRouter was, and said nothing about which members were up.
        if report["llm"]["status"] == "green":
            instance = self._instances.get("llm")
            members = getattr(instance, "member_names", None)
            if members:
                report["llm"]["chain"] = self._chain_report(instance, members)
                up = [m["name"] for m in report["llm"]["chain"] if m["reachable"]]
                if len(members) == 1:
                    report["llm"]["provider"] = members[0]
                if up:
                    report["llm"]["detail"] = (
                        f"{len(up)}/{len(members)} provider(s) reachable: "
                        + ", ".join(up)
                    )
                else:
                    report["llm"].update(
                        status="yellow",
                        detail=(
                            "no provider in the LLM chain is reachable — "
                            + "; ".join(
                                f"{m['name']}: {m['detail']}"
                                for m in report["llm"]["chain"]
                            )
                        ),
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
                "env_hint": "",
            }
        except ProviderNotConfigured as exc:
            report["instagram"] = {
                "status": "yellow", "provider": None, "configured": False,
                "detail": str(exc), "env_hint": _ENV_HINTS["instagram"],
            }

        # TikTok is optional too — and always visible, so an unconfigured
        # install shows a yellow row naming the exact env vars.
        try:
            self.resolve("tiktok")
            report["tiktok"] = {
                "status": "green", "provider": "TikTokClient",
                "configured": True,
                "detail": "Login Kit app credentials configured — accounts can connect",
                "env_hint": "",
            }
        except ProviderNotConfigured as exc:
            report["tiktok"] = {
                "status": "yellow", "provider": None, "configured": False,
                "detail": str(exc), "env_hint": _ENV_HINTS["tiktok"],
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

    def _chain_report(self, instance, members: list[str]) -> list[dict]:
        """Per-member reachability for the health table.

        Deliberately synchronous and short-bounded: this backs an HTTP status
        endpoint that a UI polls, so it must not wait on a slow model. Each
        provider's own async `health_check()` does the expensive, honest check
        (a real completion) for callers that can afford it; this is a
        reachability report and is labelled as one.
        """
        return [
            {"name": name, **self._probe_llm_member(name)} for name in members
        ]

    def _probe_llm_member(self, name: str) -> dict:
        import httpx

        if name == "ollama":
            url = f"{self._settings.OLLAMA_URL.rstrip('/')}/api/tags"
            try:
                resp = httpx.get(url, timeout=2.0)
                if resp.status_code == 200:
                    return {"reachable": True, "detail": "server up"}
                return {"reachable": False, "detail": f"HTTP {resp.status_code}"}
            except Exception:
                return {"reachable": False, "detail": f"not reachable at {url}"}

        if name == "openrouter":
            if not self._settings.OPENROUTER_API_KEY:
                return {"reachable": False, "detail": "OPENROUTER_API_KEY is not set"}
            # A real one-token completion, not a /models listing: a listing can
            # return 200 on an account whose routes all fail, and it would not
            # catch the account-level provider allowlist or a ZDR refusal.
            try:
                resp = httpx.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._settings.OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._settings.LLM_OPENROUTER_MODEL,
                        "messages": [{"role": "user", "content": "ok"}],
                        "max_tokens": 1,
                    },
                    timeout=8.0,
                )
                if resp.status_code == 200:
                    return {
                        "reachable": True,
                        "detail": f"{self._settings.LLM_OPENROUTER_MODEL} answered",
                    }
                body = resp.text[:160]
                return {"reachable": False, "detail": f"HTTP {resp.status_code}: {body}"}
            except Exception as exc:
                return {"reachable": False, "detail": f"probe failed: {exc}"}

        return {"reachable": False, "detail": f"no probe implemented for '{name}'"}


_ENV_HINTS = {
    "llm": "OLLAMA_URL / OLLAMA_MODEL (and optionally OPENROUTER_API_KEY)",
    "image": "WAN_API_KEY or DASHSCOPE_API_KEY (IMAGE_PROVIDER=dashscope)",
    "video": "WAN_API_KEY / DASHSCOPE_API_KEY or WAN_VIDEO_URL",
    "voice": "ELEVENLABS_API_KEY or macOS say + ffmpeg",
    "trainer": "install torch/diffusers/peft (TRAINER_PROVIDER=hf)",
    "storage": "STORAGE_PROVIDER=filesystem|minio",
    "moderation": "HUGGINGFACE_API_KEY",
    "instagram": "INSTAGRAM_ACCESS_TOKEN + INSTAGRAM_ACCOUNT_ID",
    "tiktok": "TIKTOK_CLIENT_KEY + TIKTOK_CLIENT_SECRET + TIKTOK_REDIRECT_URI (HTTPS)",
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