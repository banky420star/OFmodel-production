"""Persona Studio — HuggingFace LoRA Trainer.

Trains LoRA adapters on reference images using diffusers + peft.
Runs on Apple M4 Metal (MPS) or NVIDIA GPU.

Requires:
  - torch, diffusers, transformers, peft (pip install)
  - ~4GB disk for SD 1.5 base model (downloaded on first use)
  - ~8GB RAM for training on M4
"""

from __future__ import annotations
import asyncio
import os
import time
from pathlib import Path
from typing import Any

import structlog

from app import paths
from app.providers.base import TrainerProvider, ProviderResult

logger = structlog.get_logger()

# Storage paths — owned by app/paths.py; kept module-level by name because
# tests patch `hf_trainer.DATASETS_DIR` directly.
MODELS_DIR = paths.MODELS_DIR
LORA_DIR = paths.LORA_DIR
DATASETS_DIR = paths.DATASETS_DIR

# Use SD 1.5 (~4GB) — smaller than SDXL for disk-constrained setups
BASE_MODEL = "runwayml/stable-diffusion-v1-5"
LORA_TARGET_MODULES = ["to_q", "to_k", "to_v", "to_out.0"]  # Attention layers for SD1.5

# Checkpoint file -> HF repo of the same family. `COMFYUI_CHECKPOINT` is a
# filename on disk, so a LoRA trained for a *different* family than the one that
# generates the images cannot be loaded by the identity-locked path. Matching
# substrings rather than exact names, because checkpoint filenames vary
# (`sd_xl_base_1.0.safetensors`, `sdXL_v10.safetensors`, ...).
_CHECKPOINT_FAMILIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("xl", "sdxl"), "stabilityai/stable-diffusion-xl-base-1.0"),
    (("v1-5", "v1.5", "sd15", "1-5"), "runwayml/stable-diffusion-v1-5"),
)


def base_model_for_checkpoint(checkpoint: str) -> str:
    """Return the HF base model matching a checkpoint filename, or "" if unknown."""
    lowered = (checkpoint or "").casefold()
    for needles, repo in _CHECKPOINT_FAMILIES:
        if any(needle in lowered for needle in needles):
            return repo
    return ""


def model_family(model: str) -> str:
    """Coarse family key used to compare a LoRA base against a checkpoint."""
    lowered = (model or "").casefold()
    if "xl" in lowered:
        return "sdxl"
    if "stable-diffusion-v1-5" in lowered or "sd15" in lowered:
        return "sd15"
    return lowered


# UNet parameter counts for the two families this trainer supports. These are
# fixed by the architecture rather than by the checkpoint, so they can be stated
# rather than measured — and measuring would mean loading the weights, which is
# the very cost the dtype choice is deciding.
UNET_PARAMS: dict[str, int] = {
    "sd15": 859_520_964,
    "sdxl": 2_567_462_628,
}


def comfyui_lora_dir() -> Path:
    """Directory ComfyUI loads LoRAs from, so a trained adapter is reachable.

    The in-project ComfyUI install lives at the *repository* root, not under
    `apps/api` — this file is `apps/api/app/providers/hf_trainer.py`, so reaching
    the root takes five hops up. The earlier three-hop version resolved to
    `apps/api/.local/ComfyUI/models/loras`, which does not exist: the trainer
    would have created that directory and published the adapter into it, where
    ComfyUI never looks.
    """
    from app.config import get_settings

    configured = get_settings().COMFYUI_LORA_DIR
    if configured:
        return Path(configured)
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / ".local" / "ComfyUI" / "models" / "loras"


def comfyui_lora_key(key: str) -> str:
    """One diffusers/peft tensor name -> the name ComfyUI's key table holds.

    `save_lora_adapter` writes diffusers module paths with a peft suffix:

        down_blocks.1.attentions.0.transformer_blocks.0.attn1.to_q.lora_A.weight

    ComfyUI builds its diffusers entries in `comfy/lora.py:model_lora_keys_unet`
    as `k.replace(".to_", ".processor.to_")` over the UNet's diffusers names, so
    the base key it will look up is

        down_blocks.1.attentions.0.transformer_blocks.0.attn1.processor.to_q

    and `comfy/weight_adapter/lora.py:LoRAAdapter.load` then reads
    `{base}.lora_A.weight` from the file. The peft suffix was never the problem —
    the base name was missing `.processor.`, so nothing matched at all. For
    `to_out` the map also drops the trailing `.0`.

    Measured on a real trained adapter (1120 tensors): as written by the trainer,
    ComfyUI reported **2240 keys not loaded** (every tensor, twice over — once per
    patch pass) and applied nothing. Renamed this way, **0 keys not loaded**.
    """
    suffix = ""
    for candidate in (".lora_A.weight", ".lora_B.weight", ".alpha"):
        if key.endswith(candidate):
            suffix = candidate
            break
    base = key[: -len(suffix)] if suffix else key

    base = base.replace(".to_", ".processor.to_")
    if base.endswith(".processor.to_out.0"):
        base = base[: -len(".0")]
    return base + suffix


def publish_comfyui_lora(source: Path, destination: Path) -> int:
    """Write a ComfyUI-loadable copy of a trained adapter; return the tensor count.

    Renames keys, keeps values and the metadata header (which carries the peft
    LoraConfig, including lora_alpha). A rename, not a re-train: the diffusers
    original stays where it is, so any diffusers-based path still reads the keys
    it expects.
    """
    from safetensors import safe_open
    from safetensors.torch import save_file

    tensors: dict[str, Any] = {}
    metadata: dict[str, str] = {}
    with safe_open(str(source), framework="pt") as handle:
        metadata = dict(handle.metadata() or {})
        for key in handle.keys():
            tensors[comfyui_lora_key(key)] = handle.get_tensor(key)

    save_file(tensors, str(destination), metadata=metadata)
    return len(tensors)


def comfyui_checkpoint_dir() -> Path:
    """Directory ComfyUI loads checkpoints from — the sibling of the LoRA dir."""
    from app.config import get_settings

    configured = get_settings().COMFYUI_LORA_DIR
    if configured:
        return Path(configured).parent / "checkpoints"
    return Path(__file__).resolve().parents[4] / ".local" / "ComfyUI" / "models" / "checkpoints"


def local_checkpoint_path() -> Path | None:
    """The single-file checkpoint ComfyUI will generate from, if it is on disk.

    This is the preferred way to get SDXL into the trainer. It is the *same
    file* the image path loads, so the adapter is trained against exactly the
    weights that will apply it — the family-mismatch failure mode disappears
    rather than being checked for. It also avoids a ~12 GB diffusers download:
    the repository's `unet/diffusion_pytorch_model.safetensors` is fp32-only
    (there is no fp16 variant), and a checkpoint that is already present is
    free. `from_single_file` still fetches the component configs and
    tokenizers, which are a few hundred kilobytes.
    """
    from app.config import get_settings

    name = get_settings().COMFYUI_CHECKPOINT
    if not name:
        return None
    candidate = comfyui_checkpoint_dir() / name
    return candidate if candidate.is_file() else None


def _accelerator_limit_gb() -> float | None:
    """The accelerator's working-set budget in GB, or None on plain CPU."""
    import torch

    if torch.cuda.is_available():
        return torch.cuda.get_device_properties(0).total_memory / 1e9
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.mps.recommended_max_memory() / 1e9
    return None


def _fits_in_accelerator(param_count: int, bytes_per_param: int,
                         headroom_gb: float = 2.5,
                         limit_gb: float | None = None) -> bool:
    """Whether `param_count` weights of this width fit, leaving room to work.

    Weights alone are not enough: activations, gradients, the optimizer state
    and the latents all need space too. `headroom_gb` is the slack left after
    the weights; below it, the run is likely to thrash or be killed rather than
    to fail cleanly.

    `limit_gb` overrides the detected budget, which is what lets the rule be
    tested against a stated machine rather than only the one running the tests.
    """
    if limit_gb is None:
        limit_gb = _accelerator_limit_gb()
    if limit_gb is None:
        return False  # CPU: no fixed budget, but float32 is the safe default there
    return param_count * bytes_per_param / 1e9 + headroom_gb <= limit_gb


def _bfloat16_supported(device: str) -> bool:
    """Whether this accelerator can actually run a bfloat16 pass.

    bfloat16 is the same 2 bytes as float16 but needs hardware support float16
    does not: Metal from macOS 14, compute capability 8.0+ on CUDA. Where it is
    missing, asking for it does not fail loudly — it emulates, or silently
    promotes — so the answer has to be asked for rather than assumed.
    """
    import torch

    if device == "cuda":
        return torch.cuda.is_bf16_supported()
    if device == "mps":
        return (
            hasattr(torch.backends.mps, "is_macos_or_newer")
            and torch.backends.mps.is_macos_or_newer(14, 0)
        )
    return False


def training_dtype(family: str, device: str, override: str = "",
                   param_count: int | None = None):
    """The dtype for the training forward/backward pass.

    float32 is preferred whenever it fits, because the fp16 forward is prone to
    overflowing to a non-finite loss at some noise/timestep combinations — the
    observed failure was a finite loss on step 1 and `nan` on step 2, and it is
    intermittent rather than a weight blow-up. float32 does not have that
    failure mode, so the training loop never has to skip a step.

    bfloat16 is the fallback when float32's weights would not fit: it is the same
    2 bytes, but carries float32's exponent range, so it does not have the
    overflow that makes float16 skip steps. SDXL's UNet is 2,567,462,628
    parameters — 10.3 GB at float32 — and how that lands depends on the machine:
    a 24 GB card takes float32, a tighter budget halves the weights.
    `param_count` defaults to the family's known UNet size, so the answer follows
    the architecture; a family with no entry in `UNET_PARAMS` is *unknown*, and
    unknown is answered with the fallback, because assuming the weights fit and
    being wrong means an allocation failure partway through a long run.

    float16 is the last resort, for accelerators without bfloat16. It is the one
    that skips steps, and the loop refuses to call those skips a success.

    Measured on this machine (16 GB, MPS budget 12.71 GB), SDXL at 1024px,
    against the 14400 s whole-job timeout `.env` sets: float32 lands every step
    but took 6122 s for 9 steps (~11 min/step — thrashing, since 10.3 GB of
    weights against a 12.71 GB budget, but it finishes); float16 takes 244 s and
    skips 8 of 9 steps, which the guard below rejects; bfloat16 lands every step
    (skipped_steps=0, finite loss) and took 161 s for 2 steps — 80 s/step, so
    ~12 min for a 9-image epoch, roughly 8x faster than float32.

    `override` (settings.TRAINER_DTYPE) wins, so an operator on a bigger machine
    — or one chasing a divergence — can pin it without a code change.
    """
    import torch

    if override:
        pin = override.strip().casefold()
        if pin in ("float32", "fp32"):
            return torch.float32
        if pin in ("float16", "fp16", "half"):
            return torch.float16
        if pin in ("bfloat16", "bf16"):
            return torch.bfloat16
        raise ValueError(
            f"TRAINER_DTYPE is '{override}' — expected '', 'float32', 'float16' "
            "or 'bfloat16'"
        )
    if device == "cpu":
        return torch.float32
    if param_count is None:
        param_count = UNET_PARAMS.get(family)
    if param_count and _fits_in_accelerator(param_count, 4):
        return torch.float32
    if _bfloat16_supported(device):
        return torch.bfloat16
    return torch.float16


def _initial_loss_scale(dtype) -> float:
    """The starting loss scale — 1.0 unless the dtype needs scaling at all.

    Only float16 needs it: the fp16 forward loses the small MSE gradients in its
    narrow exponent range. float32 and bfloat16 both carry float32's exponent
    range, so scaling them is not merely unnecessary — the backward pass
    multiplies by this scale unconditionally, while the unscale that divides it
    back out is guarded by `use_scaling`. Returning 1024.0 for a dtype that is
    never unscaled meant every float32 and bfloat16 run stepped its optimizer
    1024x too far: the adapter was trained at 1024x the configured learning
    rate, and reported it as a small, healthy loss.
    """
    import torch

    return 1024.0 if dtype == torch.float16 else 1.0


def load_pipeline(family: str, dtype, single_file: Path | None, **kwargs):
    """Build the SD or SDXL pipeline, preferring a local single-file checkpoint.

    A missing checkpoint is a real failure and is allowed to raise: silently
    falling back to the HF repo would train the adapter against different
    weights than the ones that will apply it, which is the whole failure this
    avoids.

    `kwargs` are forwarded to whichever loader runs, so callers keep control of
    `safety_checker=None` (SD 1.5 needs it; SDXL has none to disable).
    """
    from diffusers import StableDiffusionPipeline, StableDiffusionXLPipeline

    cls = StableDiffusionXLPipeline if family == "sdxl" else StableDiffusionPipeline
    if single_file is not None:
        return cls.from_single_file(str(single_file), torch_dtype=dtype, **kwargs)
    repo = "stabilityai/stable-diffusion-xl-base-1.0" if family == "sdxl" else BASE_MODEL
    return cls.from_pretrained(repo, torch_dtype=dtype, **kwargs)


def _get_device():
    """Get the best available device."""
    import torch
    if torch.cuda.is_available():
        return "cuda"
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class HuggingFaceTrainer(TrainerProvider):
    """Real LoRA training via HuggingFace diffusers + peft on MPS/CUDA."""

    def __init__(self, base_model: str | None = None, device: str = ""):
        # `None` means "work it out"; an explicit string is honoured as given.
        # The sentinel is None rather than the BASE_MODEL constant on purpose:
        # with BASE_MODEL as the sentinel, passing SD 1.5 explicitly was
        # indistinguishable from not passing anything, and the explicit choice
        # was silently replaced by the derived one.
        from app.config import get_settings

        settings = get_settings()
        self._base_model = (
            base_model
            or settings.TRAINER_BASE_MODEL
            or base_model_for_checkpoint(settings.COMFYUI_CHECKPOINT)
            or BASE_MODEL
        )
        self._device = device or _get_device()
        self._provider = "huggingface_trainer"
        self._pipeline = None
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        LORA_DIR.mkdir(parents=True, exist_ok=True)
        DATASETS_DIR.mkdir(parents=True, exist_ok=True)

    def assert_matches_image_checkpoint(self) -> None:
        """Refuse to train a LoRA the image path could never load.

        A LoRA is bound to the base model it was trained on: SD 1.5 targets do
        not match SDXL's UNet, so ComfyUI would fail to apply the adapter (or
        silently ignore it) while the build reported a trained model. Catching it
        here costs nothing; catching it after a multi-gigabyte download and hours
        of MPS training wastes both, and the artifact is still unusable.
        """
        from app.config import get_settings

        settings = get_settings()
        if settings.IMAGE_PROVIDER != "comfyui":
            # Some other backend generates the images; the ComfyUI checkpoint is
            # not the thing this LoRA has to match.
            return
        checkpoint_family = model_family(
            base_model_for_checkpoint(settings.COMFYUI_CHECKPOINT)
            or settings.COMFYUI_CHECKPOINT
        )
        base_family = model_family(self._base_model)
        if checkpoint_family and base_family and checkpoint_family != base_family:
            raise ValueError(
                f"LoRA base model '{self._base_model}' ({base_family}) does not "
                f"match the image checkpoint '{settings.COMFYUI_CHECKPOINT}' "
                f"({checkpoint_family}). The adapter could not be applied to "
                "images generated by that checkpoint. Set TRAINER_BASE_MODEL to "
                "the matching family, or switch COMFYUI_CHECKPOINT."
            )

    async def train(
        self,
        dataset_id: str,
        model_type: str = "lora",
        rank: int = 16,
        epochs: int = 10,
        learning_rate: float = 1e-4,
        batch_size: int = 1,
        image_paths: list[str] | None = None,
    ) -> ProviderResult:
        """Train a LoRA adapter on reference images.

        `image_paths` is the dataset's recorded reference images (the
        `ReferenceDataset.image_keys` the build step wrote). It is the
        authoritative list: this trainer used to resolve images only from
        `storage/datasets/<dataset_id>/`, while the build step wrote them to
        `storage/datasets/<persona_hex8>/` — the two halves of the pipeline
        never looked in the same place, so every run found an empty directory
        and failed with "No reference images found", after ~40 minutes of
        reference generation that had in fact succeeded.

        This runs synchronously in a thread to avoid blocking the event loop.

        Cost depends on the family: the adapter must match the checkpoint that
        generates the images, and ComfyUI's checkpoint here is SDXL, so a real
        run loads ~7 GB of weights and trains at 1024px. Expect this to be slow
        on M4 MPS — the first run also downloads the base model, which is why
        the mismatch guard runs before any of it.
        """
        start = time.monotonic()

        try:
            # Before downloading weights or spending training time: is this
            # adapter even loadable by the checkpoint that makes the images?
            self.assert_matches_image_checkpoint()

            # Run training in a thread (blocking ML operations)
            result = await asyncio.to_thread(
                self._train_sync,
                dataset_id=dataset_id,
                rank=rank,
                epochs=epochs,
                learning_rate=learning_rate,
                batch_size=batch_size,
                image_paths=image_paths,
            )
            elapsed_ms = (time.monotonic() - start) * 1000
            result["training_time_s"] = round(elapsed_ms / 1000, 1)
            result["is_mock"] = False

            logger.info(
                "lora_training_complete",
                dataset_id=dataset_id,
                epochs=epochs,
                loss=result.get("final_loss", 0),
                time_s=result["training_time_s"],
                device=self._device,
            )

            return ProviderResult(
                success=True,
                data=result,
                provider=self._provider,
                latency_ms=elapsed_ms,
            )

        except Exception as e:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.error("lora_training_failed", error=str(e))
            return ProviderResult(
                success=False,
                error=str(e),
                provider=self._provider,
                latency_ms=elapsed_ms,
            )

    @staticmethod
    def resolve_dataset_images(
        dataset_id: str,
        image_paths: list[str] | None = None,
    ) -> list[Path]:
        """Return the reference images to train on, or raise naming what is missing.

        Split out from `_train_sync` so the resolution can be tested without
        training anything: the bug this replaced was purely a matter of *where*
        the files were looked for (`storage/datasets/<dataset_id>/` instead of
        the paths the build step recorded), and it made every run fail after the
        ~40 minutes of reference generation had already succeeded.

        The dataset record's own list wins. The directory scan survives only for
        datasets recorded by an older build that stored images under the dataset
        id.
        """
        image_files: list[Path] = []

        if image_paths:
            resolved = [Path(p) for p in image_paths]
            image_files = sorted(p for p in resolved if p.is_file())
            missing = len(resolved) - len(image_files)
            if not image_files:
                raise ValueError(
                    f"Dataset '{dataset_id}' records {len(resolved)} reference "
                    f"image(s) but none exist on disk (first: {resolved[0]}). "
                    "The reference images were deleted or the storage volume is "
                    "not mounted — re-run build_reference_dataset."
                )
            if missing:
                logger.warning(
                    "lora_train_missing_reference_images",
                    dataset_id=dataset_id,
                    recorded=len(resolved),
                    found=len(image_files),
                )
        else:
            dataset_path = DATASETS_DIR / dataset_id
            image_files = sorted(
                list(dataset_path.glob("*.png"))
                + list(dataset_path.glob("*.jpg"))
                + list(dataset_path.glob("*.jpeg"))
            )
            if not image_files:
                raise ValueError(
                    f"No reference images found for dataset '{dataset_id}'. "
                    f"Looked in {dataset_path} and the dataset record listed no "
                    "images. Add at least one licensed PNG or JPEG before "
                    "starting LoRA training."
                )

        return image_files

    def _train_sync(
        self,
        dataset_id: str,
        rank: int = 16,
        epochs: int = 10,
        learning_rate: float = 1e-4,
        batch_size: int = 1,
        image_paths: list[str] | None = None,
    ) -> dict:
        """Synchronous LoRA training — called from a thread."""
        import gc

        import numpy as np
        import torch
        from PIL import Image
        from diffusers import DDPMScheduler, StableDiffusionPipeline, StableDiffusionXLPipeline
        from peft import LoraConfig

        logger.info(
            "lora_training_start",
            device=self._device,
            base_model=self._base_model,
            family=model_family(self._base_model),
            rank=rank,
            epochs=epochs,
        )

        # Load reference images. The dataset record's own list wins: those are
        # the files the build actually produced, wherever it chose to put them.
        image_files = self.resolve_dataset_images(dataset_id, image_paths)

        logger.info(
            "loaded_dataset",
            images=len(image_files),
            path=str(image_files[0].parent),
        )

        is_xl = model_family(self._base_model) == "sdxl"
        # SDXL is trained at its native 1024; SD 1.5 at 512. Training SDXL at
        # 512 produces an adapter that fights the model at inference.
        resolution = 1024 if is_xl else 512

        # float16 halves the UNet, at the cost of an fp16 forward that can
        # overflow — see training_dtype() and the dynamic loss scaling in the
        # training loop. float32 is chosen whenever the weights fit, so the
        # common path never skips a step.
        from app.config import get_settings as _get_settings
        family = "sdxl" if is_xl else "sd15"
        dtype = training_dtype(
            family, self._device,
            _get_settings().TRAINER_DTYPE,
            param_count=UNET_PARAMS.get(family),
        )

        def _to_tensor(path) -> "torch.Tensor":
            """PIL -> normalized CHW tensor, without torchvision.

            torchvision is not installed in this virtualenv, and the previous
            `torchvision.transforms.ToTensor` import meant training died at the
            first batch with ModuleNotFoundError — after the ~40 minutes of
            reference generation had already succeeded.
            """
            img = Image.open(path).convert("RGB").resize(
                (resolution, resolution), Image.LANCZOS
            )
            arr = np.asarray(img, dtype=np.float32) / 127.5 - 1.0  # -> [-1, 1]
            return torch.from_numpy(arr).permute(2, 0, 1).contiguous()

        # ── Load the pipeline matching the checkpoint family ─────────────
        # A single-file checkpoint already on disk is preferred over the HF
        # repo: it is the exact file the image path generates from, so the
        # adapter cannot end up trained against different weights than the ones
        # that will apply it.
        single_file = local_checkpoint_path()
        logger.info(
            "loading_base_model",
            model=self._base_model,
            dtype=str(dtype),
            source=str(single_file) if single_file else f"hf:{self._base_model}",
        )
        # `dtype` is load_pipeline's own argument, so it must not also appear in
        # the extra kwargs — passing it twice raises TypeError before any
        # weights load.
        load_kwargs = {}
        if not is_xl:
            load_kwargs.update(safety_checker=None, requires_safety_checker=False)
        pipe = load_pipeline(family, dtype, single_file, **load_kwargs)
        pipe = pipe.to(self._device)
        pipe.set_progress_bar_config(disable=True)

        # ── Precompute conditioning and latents, then free what training
        # does not need. Only the UNet is trained, so after this point the text
        # encoders and VAE can go: that is what makes SDXL fit in 16 GB.
        prompt = "a portrait photo"
        with torch.no_grad():
            encoded = pipe.encode_prompt(
                prompt=prompt,
                device=self._device,
                num_images_per_prompt=1,
                do_classifier_free_guidance=False,
            )
        # encode_prompt returns 2 values for SD 1.5 and 4 for SDXL.
        prompt_embeds = encoded[0]
        pooled_prompt_embeds = encoded[2] if is_xl and len(encoded) > 2 else None

        added_cond_kwargs = None
        if is_xl:
            add_time_ids = pipe._get_add_time_ids(
                (resolution, resolution),
                (0, 0),
                (resolution, resolution),
                dtype=prompt_embeds.dtype,
                text_encoder_projection_dim=pipe.text_encoder_2.config.projection_dim,
            ).to(self._device)
            added_cond_kwargs = {
                "text_embeds": pooled_prompt_embeds,
                "time_ids": add_time_ids,
            }

        latents_by_index = []
        scaling = pipe.vae.config.scaling_factor
        # The VAE runs in float32 whatever the UNet's dtype. SDXL's VAE is
        # numerically unstable in float16 — it is the classic source of NaN
        # latents — and a NaN here would poison every step while surfacing only
        # as a NaN loss at the end. Only the UNet (the trained part) needs the
        # lower precision.
        pipe.vae.to(torch.float32)
        for path in image_files:
            pixels = _to_tensor(path).unsqueeze(0).to(self._device, dtype=torch.float32)
            with torch.no_grad():
                latent = pipe.vae.encode(pixels).latent_dist.sample() * scaling
            # Keep latents on the CPU: they are small, and holding them on the
            # device competes with the UNet for memory.
            latents_by_index.append(latent.detach().to("cpu"))

        del pipe.vae
        for attribute in ("text_encoder", "text_encoder_2"):
            if hasattr(pipe, attribute):
                delattr(pipe, attribute)
        gc.collect()
        if self._device == "mps":
            torch.mps.empty_cache()

        # ── LoRA adapter (diffusers-native injection) ────────────────────
        # `add_adapter` alone. The legacy `unet.enable_lora()` call that used to
        # sit here only flips `enabled` on modules that already carry a lora_A
        # key, so before injection it did nothing — and the LoRAAttnProcessor
        # path it belongs to predates peft support entirely.
        pipe.unet.add_adapter(
            LoraConfig(
                r=rank,
                lora_alpha=rank,
                target_modules=LORA_TARGET_MODULES,
                lora_dropout=0.05,
                bias="none",
            )
        )
        # Trades compute for activation memory, which is the binding constraint.
        try:
            pipe.unet.enable_gradient_checkpointing()
        except Exception:  # not supported for every attention processor
            logger.warning("gradient_checkpointing_unavailable")

        trainable = sum(p.numel() for p in pipe.unet.parameters() if p.requires_grad)
        total = sum(p.numel() for p in pipe.unet.parameters())
        logger.info(
            "lora_adapter_added",
            trainable=trainable,
            total=total,
            percent=round(100 * trainable / max(total, 1), 3),
        )

        trainable_params = [p for p in pipe.unet.parameters() if p.requires_grad]
        optimizer = torch.optim.AdamW(trainable_params, lr=learning_rate, weight_decay=0.01)
        scheduler = DDPMScheduler.from_config(pipe.scheduler.config)

        # `batch_size` is honoured as gradient accumulation, not as a DataLoader
        # batch. Several 1024px SDXL latents resident at once do not fit on a
        # 16 GB machine, but accumulating over N single-image steps produces the
        # same update at a fraction of the memory. The previous code built a real
        # DataLoader(batch_size) — affordable only because it ran 512px SD 1.5.
        accum = max(1, int(batch_size))

        # ── Loss scaling ─────────────────────────────────────────────────
        # SDXL on MPS runs the UNet in float16 (~5 GB) because float32 is ~10 GB
        # and does not fit. But an fp16 forward/backward loses precision in the
        # small MSE gradients, and the observed result was `loss=nan` on step 2
        # of the very first run — the adapter was never saved. The fix is the
        # standard one: scale the loss up before backward so the gradients land
        # in fp16's representable range, then unscale before the optimizer step.
        #
        # Dynamic, because a fixed factor is either too small to help or large
        # enough to overflow on its own: halve it when a step produces
        # non-finite gradients (and skip that step), double it every
        # `_SCALE_GROWTH` clean steps. This is what torch's GradScaler does, but
        # GradScaler is CUDA-only and this trains on MPS.
        use_scaling = dtype == torch.float16
        # 1.0 when scaling is off — the backward below multiplies by `scale`
        # unconditionally, but the divide that undoes it is guarded by
        # use_scaling. See _initial_loss_scale().
        scale = _initial_loss_scale(dtype)
        clean_streak = 0
        skipped_steps = 0

        # ── Training loop ────────────────────────────────────────────────
        pipe.unet.train()
        total_loss = 0.0
        steps = 0

        for epoch in range(epochs):
            epoch_loss = 0.0
            for latent in latents_by_index:
                latents = latent.to(self._device, dtype=dtype)

                noise = torch.randn_like(latents)
                bsz = latents.shape[0]
                timesteps = torch.randint(
                    0, scheduler.config.num_train_timesteps, (bsz,), device=self._device
                ).long()
                noisy_latents = scheduler.add_noise(latents, noise, timesteps)

                if is_xl:
                    model_pred = pipe.unet(
                        noisy_latents,
                        timesteps,
                        encoder_hidden_states=prompt_embeds,
                        added_cond_kwargs=added_cond_kwargs,
                    ).sample
                else:
                    model_pred = pipe.unet(
                        noisy_latents, timesteps, prompt_embeds
                    ).sample

                # Computed in float32 regardless of the model dtype: the MSE
                # over ~16k latent values in fp16 is where the precision was
                # being lost in the first place.
                loss = torch.nn.functional.mse_loss(model_pred.float(), noise.float())

                if not torch.isfinite(loss):
                    # Even with scaling, a non-finite loss means this step cannot
                    # be used. With scaling enabled that is a reason to lower the
                    # scale and move on; without it, the run is already worthless.
                    if not use_scaling:
                        raise RuntimeError(
                            f"LoRA training diverged at step {steps + 1} "
                            f"(loss={loss.item()}) — the adapter was not saved. "
                            "The accelerator could not represent the loss; lower "
                            "the learning rate or train on CPU."
                        )
                    scale = max(scale / 2, 1.0)
                    clean_streak = 0
                    skipped_steps += 1
                    optimizer.zero_grad()
                    steps += 1
                    logger.warning("lora_step_skipped_nonfinite_loss",
                                   step=steps, new_scale=scale)
                    continue

                (loss * scale / accum).backward()
                steps += 1

                if steps % accum == 0:
                    if use_scaling:
                        # Unscale in place, then look for the inf/nan that
                        # overflowed during backward — the loss being finite
                        # does not mean every gradient was.
                        for p in trainable_params:
                            if p.grad is not None:
                                p.grad.div_(scale)
                        bad = any(
                            p.grad is not None and not torch.isfinite(p.grad).all()
                            for p in trainable_params
                        )
                        if bad:
                            scale = max(scale / 2, 1.0)
                            clean_streak = 0
                            skipped_steps += 1
                            optimizer.zero_grad()
                            logger.warning("lora_step_skipped_nonfinite_grad",
                                           step=steps, new_scale=scale)
                        else:
                            optimizer.step()
                            optimizer.zero_grad()
                            clean_streak += 1
                            if clean_streak >= 20:
                                scale = min(scale * 2, 65536.0)
                                clean_streak = 0
                    else:
                        optimizer.step()
                        optimizer.zero_grad()

                epoch_loss += loss.item()

            avg_epoch_loss = epoch_loss / max(len(latents_by_index), 1)
            total_loss += avg_epoch_loss
            logger.info(
                "lora_epoch",
                epoch=epoch + 1,
                total=epochs,
                loss=round(avg_epoch_loss, 6),
                loss_scale=scale,
                skipped_steps=skipped_steps,
            )

        # A run that skipped most of its steps produced an adapter barely
        # distinguishable from its random initialization, but it *would* save a
        # file and report a small final loss — success with no training behind
        # it. The first real run skipped 8 of 9 steps and reported exactly that.
        # Requiring every step to land would be too strict (one overflow in a
        # long run is normal); requiring half is not.
        trained_steps = steps - skipped_steps
        if use_scaling and steps and trained_steps * 2 < steps:
            raise RuntimeError(
                f"LoRA training skipped {skipped_steps} of {steps} steps — only "
                f"{trained_steps} update(s) landed, so the saved adapter would be "
                "close to untrained. The float16 forward is overflowing. Set "
                "TRAINER_DTYPE=float32 (needs ~11 GB of accelerator memory) or "
                "lower the learning rate."
            )

        # Flush a partial accumulation window, so the last images of the final
        # epoch are not silently thrown away.
        if steps % accum != 0:
            if use_scaling:
                for p in trainable_params:
                    if p.grad is not None:
                        p.grad.div_(scale)
            optimizer.step()
            optimizer.zero_grad()

        # ── Save where ComfyUI can load it ───────────────────────────────
        # ComfyUI's LoraLoader resolves `lora_name` against its own models/loras
        # directory, so a file written only into the project's storage is
        # invisible to generation. Writing it there is not enough on its own:
        # the *key names* have to line up too. See `comfyui_lora_key` — the
        # trainer's own keys carry no `.processor.`, so a straight copy loads
        # zero tensors while reporting success. `publish_comfyui_lora` renames;
        # it does not re-train, and the diffusers-keyed original is kept.
        #
        # NOT save_attn_procs: in diffusers 0.40 that raises ValueError for a
        # peft adapter ("only supports saving Custom Diffusion attention
        # processors"), so using it would have failed after the whole training
        # run had completed.
        storage_dir = LORA_DIR / dataset_id
        storage_dir.mkdir(parents=True, exist_ok=True)
        pipe.unet.save_lora_adapter(str(storage_dir))
        weights_file = storage_dir / "pytorch_lora_weights.safetensors"
        if not weights_file.exists():
            # Name varies by diffusers version; take whatever it wrote.
            candidates = sorted(storage_dir.glob("*.safetensors"))
            if not candidates:
                raise RuntimeError(
                    f"LoRA training finished but no .safetensors was written to {storage_dir}"
                )
            weights_file = candidates[0]

        comfy_dir = comfyui_lora_dir()
        comfy_name = ""
        comfy_tensors = 0
        try:
            comfy_dir.mkdir(parents=True, exist_ok=True)
            comfy_name = f"persona_{dataset_id}.safetensors"
            comfy_tensors = publish_comfyui_lora(weights_file, comfy_dir / comfy_name)
            logger.info(
                "lora_published_to_comfyui",
                path=str(comfy_dir / comfy_name),
                tensors=comfy_tensors,
            )
        except Exception as exc:
            # Not fatal: the adapter exists and is recorded. Generation through
            # ComfyUI needs it in that directory though, so say so loudly.
            logger.error("lora_publish_failed", dir=str(comfy_dir), error=str(exc))
            comfy_name = ""

        logger.info("lora_saved", path=str(weights_file), comfyui_name=comfy_name)

        del pipe
        gc.collect()
        if self._device == "cuda":
            torch.cuda.empty_cache()
        elif self._device == "mps":
            torch.mps.empty_cache()

        final_loss = total_loss / max(epochs, 1)
        return {
            "model_path": str(weights_file),
            "model_type": "lora",
            "base_model": self._base_model,
            "lora_family": model_family(self._base_model),
            # The exact weights the adapter was trained against. `base_model` is
            # only the coarse family's HF repo — for SDXL that is always the
            # *stock base* — and `assert_matches_image_checkpoint` compares
            # families, so a LoRA trained on stock SDXL and later rendered on a
            # photoreal SDXL finetune passes that guard silently while the face
            # stops holding. The file is what makes that detectable after the
            # fact, so it is recorded rather than derived later.
            "training_checkpoint": single_file.name if single_file else "",
            "training_source": (
                str(single_file) if single_file else f"hf:{self._base_model}"
            ),
            "comfyui_lora_dir": str(comfy_dir),
            "comfyui_lora_name": comfy_name,
            "comfyui_lora_tensors": comfy_tensors,
            "rank": rank,
            "epochs_completed": epochs,
            "final_loss": round(final_loss, 6),
            "total_steps": steps,
            "device": self._device,
            "resolution": resolution,
        }


    async def validate(
        self,
        model_path: str,
        validation_images: list[str],
    ) -> ProviderResult:
        """Validate a trained LoRA model by generating test images."""
        start = time.monotonic()
        try:
            result = await asyncio.to_thread(
                self._validate_sync, model_path, validation_images
            )
            elapsed_ms = (time.monotonic() - start) * 1000
            return ProviderResult(
                success=True,
                data=result,
                provider=self._provider,
                latency_ms=elapsed_ms,
            )
        except Exception as e:
            elapsed_ms = (time.monotonic() - start) * 1000
            return ProviderResult(
                success=False,
                error=str(e),
                provider=self._provider,
                latency_ms=elapsed_ms,
            )

    def _validate_sync(self, model_path: str, validation_images: list[str]) -> dict:
        """Synchronous validation — generate a test image with the LoRA.

        Note this is not on any code path: identity validation goes through
        `validate_identity_handler`, which asks the local LLM for a verdict. It is
        kept correct rather than left as a trap — it was SD 1.5-only and called
        the legacy `enable_lora()` before any adapter was loaded, so if anything
        ever did call it, it would have validated the wrong model family and
        silently generated without the adapter.
        """
        import torch

        lora_path = Path(model_path)
        if not lora_path.exists():
            return {
                "validation_score": 0.0,
                "passed": False,
                "error": f"LoRA not found at {model_path}",
                "is_mock": False,
            }

        # Load pipeline with LoRA — same source the trainer trained against, so
        # validation exercises the adapter on the weights it will meet in
        # production rather than on a differently-sourced copy.
        is_xl = model_family(self._base_model) == "sdxl"
        resolution = 1024 if is_xl else 512
        # As in training: dtype belongs to load_pipeline, not to the kwargs.
        load_kwargs = {}
        if not is_xl:
            load_kwargs.update(safety_checker=None, requires_safety_checker=False)
        pipe = load_pipeline(
            "sdxl" if is_xl else "sd15", torch.float32,
            local_checkpoint_path(), **load_kwargs,
        )
        pipe = pipe.to(self._device)
        pipe.load_lora_weights(str(lora_path))

        # Generate a test image
        with torch.no_grad():
            result = pipe(
                "a portrait photo of a woman, natural lighting",
                height=resolution,
                width=resolution,
                num_inference_steps=20,
                guidance_scale=7.5,
            )

        # Save test image
        test_img = result.images[0]
        test_path = lora_path / "validation_test.png"
        test_img.save(test_path)

        del pipe
        if self._device == "cuda":
            torch.cuda.empty_cache()

        return {
            "validation_score": None,
            "identity_similarity": None,
            "quality_score": None,
            "images_validated": len(validation_images),
            "passed": False,
            "evaluation_status": "not_scored",
            "error": (
                "Generated validation image saved, but identity and quality were not "
                "measured. Review the image manually or configure a real evaluator."
            ),
            "test_image": str(test_path),
            "is_mock": False,
        }

    async def health_check(self) -> ProviderResult:
        """Check if the trainer is available.

        Reports which weights training will actually load. That distinction is
        worth surfacing: a local single-file checkpoint means the adapter is
        trained on exactly what the image path generates with, while the HF
        fallback downloads the whole repo first — and for SDXL that is ~12 GB,
        because the repo ships only an fp32 UNet.
        """
        try:
            import torch
            from diffusers import StableDiffusionPipeline  # noqa: F401 — dependency probe

            device = self._device
            mps_ok = hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
            cuda_ok = torch.cuda.is_available()
            local = local_checkpoint_path()

            return ProviderResult(
                success=True,
                data={
                    "status": "ready",
                    "device": device,
                    "mps_available": mps_ok,
                    "cuda_available": cuda_ok,
                    "base_model": self._base_model,
                    "family": model_family(self._base_model),
                    "weights_source": str(local) if local else f"hf:{self._base_model}",
                    "uses_local_checkpoint": local is not None,
                    "lora_dir": str(LORA_DIR),
                    "comfyui_lora_dir": str(comfyui_lora_dir()),
                },
                provider=self._provider,
            )
        except ImportError as e:
            return ProviderResult(
                success=False,
                error=f"Missing dependency: {e}",
                provider=self._provider,
            )
