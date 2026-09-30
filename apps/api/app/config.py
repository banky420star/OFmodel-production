"""Persona Production Line — Settings & Configuration.

Real providers only: one explicit provider choice per capability. There is
no PROVIDER_REGISTRY mode string and no mock mode — setting the legacy
PROVIDER_REGISTRY variable raises at import.
"""

from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database — defaults to SQLite for local dev; set DATABASE_URL to postgres for production
    DATABASE_URL: str = "sqlite+aiosqlite:///./persona_studio.db"

    # Application
    ENVIRONMENT: str = "development"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    WEB_ORIGIN: str = "http://localhost:3000"

    # ── Provider selection — real providers only, one per capability ──
    LLM_PROVIDER: str = "ollama"                # ollama
    IMAGE_PROVIDER: str = "dashscope"           # dashscope | eachsense | comfyui | huggingface
    VIDEO_PROVIDER: str = "dashscope_wan"       # dashscope_wan | wan_server
    VOICE_PROVIDER: str = "macos_say"           # macos_say | elevenlabs
    TRAINER_PROVIDER: str = "hf"                # hf (local MPS/CUDA LoRA)
    # This line used to be appended to the TRAINER_PROVIDER comment above, so the
    # field did not exist at all: `getattr(settings, "STORAGE_PROVIDER")` raised,
    # the storage capability resolved to None, and the health endpoint reported
    # storage red. A `.env` STORAGE_PROVIDER was silently ignored too.
    STORAGE_PROVIDER: str = "filesystem"        # filesystem | minio
    MODERATION_PROVIDER: str = "huggingface"    # huggingface (fail-closed)

    # External API keys / endpoints
    ELEVENLABS_API_KEY: str = ""
    WAN_API_KEY: str = ""
    DASHSCOPE_API_KEY: str = ""
    WAN_VIDEO_URL: str = ""
    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen3:4b"
    COMFYUI_URL: str = ""
    COMFYUI_CHECKPOINT: str = "sd_xl_base_1.0.safetensors"
    # Where ComfyUI reads LoRAs from. The trainer must write the adapter here or
    # LoraLoader cannot find it by name — a LoRA saved anywhere else is invisible
    # to generation even though the build reports a trained model. Empty means
    # "<repo>/.local/ComfyUI/models/loras", the in-project install this stack
    # actually runs; override it if ComfyUI lives elsewhere.
    COMFYUI_LORA_DIR: str = ""
    # Does the loaded COMFYUI_CHECKPOINT actually generate explicit content?
    # False by default: the local backend is only adult-capable when the
    # operator says the weights on disk are, so the adult gate can't be
    # satisfied by a stock checkpoint. Read via
    # ComfyUIImageProvider.SUPPORTS_ADULT.
    COMFYUI_ADULT_CHECKPOINT: bool = False
    # Cold SDXL load from disk is 3-4 min on top of ~2 min sampling; 300s
    # spuriously failed every first generation after a ComfyUI restart.
    COMFYUI_TIMEOUT: int = 900
    # The SVD checkpoint the video provider animates with, read by
    # ComfyUIVideoProvider via ImageOnlyCheckpointLoader. Named explicitly and
    # not defaulted from COMFYUI_CHECKPOINT: SVD and SDXL are loaded by
    # different node classes and are not interchangeable, so silently using an
    # image checkpoint here would fail at the graph rather than at config.
    COMFYUI_VIDEO_CHECKPOINT: str = "svd_xt.safetensors"
    HUGGINGFACE_API_KEY: str = ""
    HF_IMAGE_MODEL: str = "black-forest-labs/FLUX.1-schnell"
    HF_IMG2IMG_MODEL: str = "stabilityai/stable-diffusion-xl-refiner-1.0"

    # Base model the LoRA is trained against. It MUST be the same family as the
    # checkpoint that generates the images (COMFYUI_CHECKPOINT), or the adapter
    # cannot be loaded: a LoRA trained on SD 1.5's UNet does not match SDXL's,
    # so the identity-locked generation path would reject or ignore it while the
    # build still reported a trained model.
    #
    # Empty (the default) means "derive it from COMFYUI_CHECKPOINT", so the two
    # cannot drift apart unless someone overrides this deliberately.
    TRAINER_BASE_MODEL: str = ""

    # Precision for the training forward/backward pass: "" (auto), "float32"
    # or "float16".
    #
    # Auto picks float16 for SDXL on MPS, because the SDXL UNet is ~10.3 GB in
    # float32 and the accelerator's working set is ~12.7 GB — there is not room
    # for the weights plus activations. float16 halves that to ~5 GB, but the
    # fp16 forward is prone to overflowing to a non-finite loss, which is why
    # the training loop carries dynamic loss scaling and reports how many steps
    # it had to skip. Set "float32" to trade memory for a clean run: slower and
    # it may fail to allocate, but no step is skipped.
    TRAINER_DTYPE: str = ""

    # EachLabs each::sense (adult-capable image generation)
    EACHLABS_API_KEY: str = ""
    EACHSENSE_MODE: str = "max"  # max | eco

    # MinIO (only when STORAGE_PROVIDER=minio)
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET: str = "persona"
    MINIO_SECURE: bool = False

    # Instagram Graph API (optional analytics)
    INSTAGRAM_ACCESS_TOKEN: str = ""
    INSTAGRAM_ACCOUNT_ID: str = ""

    # TikTok Login Kit + Display API (optional analytics).
    # These are *app-level* credentials from a TikTok developer app; the
    # per-account access token obtained via OAuth lives on SocialAccount.api_token.
    # TikTok requires an HTTPS redirect URI, so register one that reaches this
    # API (a tunnel or a real domain) — http://localhost is rejected by TikTok.
    TIKTOK_CLIENT_KEY: str = ""
    TIKTOK_CLIENT_SECRET: str = ""
    TIKTOK_REDIRECT_URI: str = ""

    # Fanvue creator API — the only platform in this category with a public
    # publishing API, and the only one whose rules permit a disclosed AI persona
    # to be a first-class account. OAuth 2.0 (authorization code + PKCE, rotating
    # refresh tokens); there are no static API keys, so every field below comes
    # from one authorization the operator performs against their own account.
    #
    # Empty credentials are not a degraded mode: the publisher raises
    # PublishNotConfigured and the line records blocked_gate. Nothing here ever
    # falls back to a local-only write that reports success — that is exactly the
    # failure app/delivery.py exists to document.
    FANVUE_CLIENT_ID: str = ""
    FANVUE_CLIENT_SECRET: str = ""
    FANVUE_ACCESS_TOKEN: str = ""
    FANVUE_REFRESH_TOKEN: str = ""
    # The creator's own user UUID. Every media and post path is keyed by it, so
    # there is no "discover it" fallback: an unset UUID means unconfigured.
    FANVUE_CREATOR_UUID: str = ""
    FANVUE_API_BASE: str = "https://api.fanvue.com"
    # Misnamed, and kept for the .env files already in the wild that set it: this
    # is the **token** endpoint, not an authorization URL. The authorize endpoint
    # is FANVUE_AUTHORIZE_URL below. Both are from the published spec
    # (https://api.fanvue.com/docs/openapi-v1.json, securityScheme `BearerAuth`,
    # flow `authorizationCode`).
    FANVUE_AUTH_URL: str = "https://auth.fanvue.com/oauth2/token"
    FANVUE_AUTHORIZE_URL: str = "https://auth.fanvue.com/oauth2/auth"
    # Where Fanvue sends the operator's browser back to. Must match the redirect
    # registered on the client, byte for byte, or the token exchange is refused.
    # Empty means the connect endpoint refuses rather than sending an operator to
    # an authorize URL that will bounce.
    FANVUE_REDIRECT_URI: str = ""
    # Sent on every request; Fanvue versions its API by header, not by path.
    FANVUE_API_VERSION: str = "2025-06-26"

    # Adult content — OFF by default; four-layer gate (see routes/content.py)
    ADULT_CONTENT_ENABLED: bool = False

    # ── Fan-facing monetization slice ──────────────────────────────────
    # OpenRouter as a second live LLM. The fan chat runs a chain (see
    # app/providers/fallback_llm.py): Ollama first when it is up, this second.
    # Both are real network providers — this is not a mock cascade. When the
    # chain is exhausted the reply fails with a 503 rather than falling back to
    # a canned line pretending to be the persona.
    OPENROUTER_API_KEY: str = ""
    LLM_OPENROUTER_MODEL: str = "inclusionai/ling-3.0-flash-sante:free"
    LLM_FALLBACK_ENABLED: bool = True
    # Free-tier models are slow and flaky (measured 40–67% failure). Each hop
    # gets a short bound so a dead route cannot hold a fan's message for the
    # full OLLAMA_TIMEOUT_SECONDS before the chain moves on.
    LLM_FALLBACK_HOP_TIMEOUT_SECONDS: int = 45

    # Simulated money only. "fake" is the only processor that exists; the ABC in
    # billing/processor.py is what keeps a real one swappable without touching
    # any call site. No card data is ever collected or stored.
    PAYMENT_PROCESSOR: str = "fake"
    # Minor units (cents). 500 = $5.00 credited at signup so the slice is
    # walkable without a top-up; it is booked as promo expense, not revenue.
    SIGNUP_CREDIT_MINOR: int = 500
    SESSION_TTL_DAYS: int = 30
    # Which persona a new fan is assigned. Empty means "the first ACTIVE or
    # READY persona", chosen deterministically.
    FAN_DEFAULT_PERSONA_ID: str = ""

    # Posting to a real platform on the operator's behalf. Off until an account
    # is connected AND the operator arms it. This is a separate switch from the
    # credentials: having a token in .env must not be the same act as deciding
    # the line may post with it, or a value copied in for a read-only test would
    # silently become permission to publish.
    FANVUE_PUBLISH_ENABLED: bool = False

    # What a sellable post costs, in dollars, stated once by the operator.
    #
    # Unset by default, and unset means *no price is stated* — never "free".
    # Those are different sentences and only one of them is true. With it
    # unset, the scheduler refuses to create slots on a platform that sells
    # (`app/publishing.resolved_price_minor`) and the publish path refuses an
    # unpriced post (`app/publishing.publish_post`), so a calendar built
    # without a price fails loudly instead of quietly giving the content away.
    #
    # This is deliberately not a default number. What a post is worth is a
    # commercial decision about a specific account, and a value invented here
    # would be indistinguishable from one the operator chose.
    DEFAULT_PPV_PRICE: float | None = None

    @field_validator("DEFAULT_PPV_PRICE", mode="before")
    @classmethod
    def _blank_price_means_unstated(cls, value):
        """`DEFAULT_PPV_PRICE=` in a .env arrives as an empty string.

        Read literally that is a float parse error, which would turn "the
        operator has not decided yet" into a crash on boot — the one outcome
        that tells them nothing about what to do. Blank is unset.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # Automated platform signup — the Playwright path behind
    # POST /social-accounts/{id}/auto-signup and /auto-signup-all
    # (app/providers/browser_signup.py). It drives a real browser against a real
    # platform using anti-automation flags (--disable-blink-features=
    # AutomationControlled, navigator.webdriver deletion) and a mail.tm
    # disposable-inbox code bypass, and it creates real accounts when it works.
    #
    # Refused by default, and the refusal is the point: those routes are
    # registered, so without this they are one curl away, while several
    # user-facing strings in the app say no process here creates an account.
    # Turning this on is an explicit, deliberate act on the operator's part.
    # The supported path is the manual signup packet — see routes/socials.py.
    AUTO_SIGNUP_ENABLED: bool = False

    # The clock (app/scheduler.py). Every division in the studio declares a
    # cadence; before this existed, not one of them was real — a ScheduledPost
    # carried a `scheduled_at` and nothing ever read it back, which is why the
    # week_events division reported "not scheduled".
    #
    # Off by default, and the same split the publisher makes: running the loop
    # is a separate act from being allowed to publish with what it finds. An
    # enabled scheduler with no publisher ticks, finds due posts, publishes none,
    # and says so — it does not storm the gate with retries.
    SCHEDULER_ENABLED: bool = False
    SCHEDULER_INTERVAL_SECONDS: int = 60
    # One post per tick by default. A first tick after a long outage could
    # otherwise put a day's worth of the calendar on the platform in a minute,
    # which reads as spam to the platform and to fans.
    SCHEDULER_MAX_PER_TICK: int = 1
    # A post due longer ago than this is missed, not late. Publishing a
    # six-hour-old calendar slot now would put the wrong post in front of fans
    # at the wrong time.
    SCHEDULER_MAX_LATENESS_SECONDS: int = 6 * 3600

    # Workflow engine (enforced by app/jobs/runner.py)
    MAX_CONCURRENT_WORKFLOWS: int = 10
    WORKFLOW_STEP_TIMEOUT_SECONDS: int = 600

    # Content curation — the local vision model asked about generation artifacts
    # (deformed hands, extra limbs, melted texture). Advisory only: it can add
    # defects and force REVIEW, never override the deterministic technical
    # checks. Empty disables the pass, and the QA row then records "did not run"
    # rather than implying a clean bill of health.
    CURATION_VISION_MODEL: str = "gemma3:4b"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if getattr(self, "PROVIDER_REGISTRY", None):
            raise ValueError(
                "PROVIDER_REGISTRY is removed — set explicit per-capability vars "
                "(LLM_PROVIDER, IMAGE_PROVIDER, VIDEO_PROVIDER, VOICE_PROVIDER, "
                "TRAINER_PROVIDER, STORAGE_PROVIDER, MODERATION_PROVIDER). "
                "Mock mode does not exist in this project."
            )

    class Config:
        env_file = str(Path(__file__).resolve().parent.parent.parent.parent / ".env")
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()