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
        "openrouter": "OPENROUTER_API_KEY (openrouter.ai)",
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
        "comfyui": (
            "COMFYUI_URL (local ComfyUI server) plus an SVD checkpoint under "
            ".local/ComfyUI/models/checkpoints named by COMFYUI_VIDEO_CHECKPOINT"
        ),
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
    "tiktok": {
        "tiktok": "TIKTOK_CLIENT_KEY + TIKTOK_CLIENT_SECRET + TIKTOK_REDIRECT_URI",
    },
    # One implementation and no *_PROVIDER selector, like instagram and tiktok,
    # so the hint is unconditional. The two-part wording is deliberate: an
    # operator who has authorized but not armed needs to hear "disabled", not
    # "unconfigured", or they will go looking for a missing credential that is
    # already there.
    "publish": {
        "fanvue": (
            "FANVUE_CLIENT_ID + FANVUE_ACCESS_TOKEN + FANVUE_CREATOR_UUID, "
            "and FANVUE_PUBLISH_ENABLED=true to arm posting "
            "(app/providers/publish/fanvue.py)"
        ),
    },
}


def _fix_hint(capability: str) -> str:
    from app.config import get_settings

    settings = get_settings()
    options = _PROVIDER_ENV_VARS.get(capability, {})
    chosen = getattr(settings, f"{capability.upper()}_PROVIDER", "")
    env_hint = options.get(chosen)
    if env_hint is None:
        # instagram and tiktok have no *_PROVIDER selector — there is exactly one
        # implementation, so the hint is unconditional rather than a bad guess at
        # a "<CAPABILITY>_PROVIDER" setting that will never exist.
        if len(options) == 1 and not chosen:
            env_hint = next(iter(options.values()))
        else:
            env_hint = f"set {capability.upper()}_PROVIDER to a valid option"
    # This used to read "No fallback exists". That is no longer true for llm:
    # the fan chat runs a chain of real providers (app/providers/fallback_llm.py).
    # The thing that still does not exist is a *mock* fallback, and that is what
    # the sentence now says, so it stays accurate as the chain changes.
    return (
        f"Provider '{capability}' is not configured: {env_hint}. "
        "Nothing here is simulated — configure it in .env before calling this "
        "endpoint. (The LLM chain may fall back to another real provider; it "
        "never falls back to a canned or mock response.)"
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


def require_publisher():
    """Resolve the external publish provider, or raise 503.

    Deliberately not routed through `require()`: the publisher is not a
    registry capability, because it distinguishes two failures the registry
    cannot — `PublishNotConfigured` (no credentials) and `PublishDisabled`
    (credentials present, posting not armed). Collapsing those into one message
    would send an operator hunting for a missing token that is already in .env.
    """
    from app.providers.publish import (
        PublishDisabled,
        PublishError,
        PublishNotConfigured,
        get_publisher,
    )

    try:
        return get_publisher()
    except PublishNotConfigured as exc:
        raise HTTPException(
            503,
            f"Publishing is not configured: {exc} Nothing here is simulated — "
            "the line records blocked_gate rather than reporting a post it "
            "never made.",
        ) from exc
    except PublishDisabled as exc:
        raise HTTPException(409, f"Publishing is configured but not armed: {exc}") from exc
    except PublishError as exc:
        raise HTTPException(503, str(exc)) from exc


def require_adult_image() -> object:
    """Adult-content endpoints additionally require an adult-capable image provider.

    Asks the provider that is *actually configured* rather than naming one
    vendor. The old check was `settings.IMAGE_PROVIDER != "eachsense"`, which
    refused a perfectly capable local backend while telling the operator to buy
    a specific cloud API — and the persona record was simultaneously claiming
    adult verification by default, so the two layers contradicted each other.

    Each provider declares its own capability (`ImageProvider.SUPPORTS_ADULT`),
    and nothing declares yes by default. For a self-hosted backend that means
    an adult checkpoint must be explicitly configured, not merely that the
    software could load one.
    """
    resolved = require("image", "moderation")

    image = resolved.get("image")
    if not getattr(image, "SUPPORTS_ADULT", False):
        from app.config import get_settings

        raise HTTPException(
            409,
            "Configured image provider "
            f"({get_settings().IMAGE_PROVIDER}) does not declare adult support — "
            "use an adult-capable provider (IMAGE_PROVIDER=eachsense), or for a "
            "local backend set COMFYUI_ADULT_CHECKPOINT=true once an adult "
            "checkpoint is loaded.",
        )
    return resolved


def adult_gate_status() -> dict:
    """What the adult-content gate currently allows, and what is missing.

    The gate has two layers that can disagree: the global kill switch
    (`ADULT_CONTENT_ENABLED`) and the configured image provider's own
    declaration (`ImageProvider.SUPPORTS_ADULT`). The second is a
    *declaration*, not a detection — ComfyUI will load any weights, so
    `COMFYUI_ADULT_CHECKPOINT=true` means "an adult checkpoint is what is
    loaded", and nothing in this app verifies that claim. Reporting both, next
    to the checkpoint actually loaded, is what lets an operator notice a false
    declaration rather than infer it from mangled output later.

    Read-only and side-effect free: it resolves the image provider but never
    raises, so it is safe to call from a status endpoint.
    """
    from app.config import get_settings

    settings = get_settings()
    image = get_registry().resolve_optional("image")
    supports_adult = bool(getattr(image, "SUPPORTS_ADULT", False))

    blockers: list[str] = []
    if not settings.ADULT_CONTENT_ENABLED:
        blockers.append("ADULT_CONTENT_ENABLED is false — the global kill switch is off")
    if not supports_adult:
        blockers.append(
            f"the configured image provider ({settings.IMAGE_PROVIDER}) does not "
            "declare adult support"
        )
        if settings.IMAGE_PROVIDER == "comfyui":
            blockers.append(
                "for comfyui, SUPPORTS_ADULT follows COMFYUI_ADULT_CHECKPOINT "
                f"(currently false) while COMFYUI_CHECKPOINT is "
                f"{settings.COMFYUI_CHECKPOINT!r} — setting the flag asserts "
                "that an adult-tuned checkpoint is what ComfyUI has loaded"
            )

    return {
        "adult_content_enabled": settings.ADULT_CONTENT_ENABLED,
        "image_provider": settings.IMAGE_PROVIDER,
        "image_provider_supports_adult": supports_adult,
        "checkpoint": settings.COMFYUI_CHECKPOINT if settings.IMAGE_PROVIDER == "comfyui" else "",
        "allowed": settings.ADULT_CONTENT_ENABLED and supports_adult,
        "blockers": blockers,
    }


CAPABILITY_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "persona_build": ("llm", "image", "trainer"),
    "auto_produce": ("image",),
    "auto_produce_video": ("image", "video"),
    "adult_content": ("image", "moderation"),
    "fan_chat": ("llm",),
    "voice": ("voice",),
    "ig_sync": ("instagram",),
    "tiktok_connect": ("tiktok",),
}