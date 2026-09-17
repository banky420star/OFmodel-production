"""Provider gates — endpoint-level enforcement of the real-providers-only rule.

Every production endpoint declares the capabilities it needs; `require()`
resolves them through the strict registry and raises HTTP 503 with the exact
environment variable to set when something is unconfigured. Nothing can
silently degrade to a placeholder — if a gate passes, a real provider ran.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.providers.registry import get_registry


# Which env vars configure each selectable provider, per capability.
_PROVIDER_ENV_VARS: dict[str, dict[str, str]] = {
    "llm": {
        "ollama": "OLLAMA_URL (local Ollama server must be running)",
    },
    "image": {
        "dashscope": "WAN_API_KEY or DASHSCOPE_API_KEY (Alibaba DashScope qwen-image)",
        "eachsense": "EACHLABS_API_KEY (EachLabs each::sense)",
        "comfyui": "COMFYUI_URL (local ComfyUI server)",
        "huggingface": "HUGGINGFACE_API_KEY",
    },
    "video": {
        "dashscope_wan": "WAN_API_KEY or DASHSCOPE_API_KEY (DashScope Wan)",
        "wan_server": "WAN_VIDEO_URL (self-hosted Wan-compatible server)",
    },
    "voice": {
        "macos_say": "macOS say + ffmpeg (local synthesis)",
        "elevenlabs": "ELEVENLABS_API_KEY",
    },
    "trainer": {
        "hf": "HUGGINGFACE_API_KEY (for model downloads; training runs locally)",
    },
    "storage": {
        "filesystem": "no credentials needed",
        "minio": "MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY",
    },
    "moderation": {
        "huggingface": "HUGGINGFACE_API_KEY",
    },
    "instagram": {
        "instagram": "INSTAGRAM_ACCESS_TOKEN + INSTAGRAM_ACCOUNT_ID",
    },
}


def _fix_hint(capability: str) -> str:
    from app.config import get_settings

    settings = get_settings()
    chosen = getattr(settings, f"{capability.upper()}_PROVIDER", "")
    env_hint = _PROVIDER_ENV_VARS.get(capability, {}).get(
        chosen, f"set {capability.upper()}_PROVIDER to a valid option"
    )
    return (
        f"Provider '{capability}' is not configured: {env_hint}. "
        f"No fallback exists — configure it in .env before calling this endpoint."
    )


def require(*capabilities: str) -> dict[str, object]:
    """Resolve the given capabilities or raise 503 naming the missing config."""
    registry = get_registry()
    resolved: dict[str, object] = {}
    missing: list[str] = []
    for capability in capabilities:
        try:
            resolved[capability] = registry.resolve(capability)
        except Exception as exc:  # ProviderNotConfigured and friends
            missing.append(f"{capability}: {exc}")
    if missing:
        raise HTTPException(503, "; ".join(missing))
    return resolved


def require_adult_image() -> object:
    """Adult-content endpoints additionally require an adult-capable image provider."""
    from app.config import get_settings

    settings = get_settings()
    if settings.IMAGE_PROVIDER != "eachsense":
        raise HTTPException(
            409,
            "Configured image provider does not support adult content — "
            "set IMAGE_PROVIDER=eachsense",
        )
    return require("image", "moderation")


CAPABILITY_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "persona_build": ("llm", "image", "trainer"),
    "auto_produce": ("image",),
    "auto_produce_video": ("image", "video"),
    "adult_content": ("image", "moderation"),
    "fan_chat": ("llm",),
    "voice": ("voice",),
    "ig_sync": ("instagram",),
}