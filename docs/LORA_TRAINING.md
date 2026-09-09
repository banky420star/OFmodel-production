# LoRA Training Setup Guide

## Overview

Persona Studio uses LoRA (Low-Rank Adaptation) to train custom models for each persona. This creates a unique visual identity that stays consistent across all generated images and videos.

## Current Status

The training code is **fully implemented** in `apps/api/app/providers/hf_trainer.py` and works on:
- ✅ Apple M4 Metal (MPS) — tested, works but slow (~3-4 min/epoch)
- ✅ NVIDIA GPU (CUDA) — fastest option
- ⏳ Cloud GPU (RunPod/Replicate) — needs API key

## Hardware Requirements

| Setup | RAM | Storage | Speed | Cost |
|-------|-----|---------|-------|------|
| **M4 Mac** | 16GB+ | 8GB free | ~3-4 min/epoch | Free |
| **NVIDIA GPU** | 8GB+ VRAM | 8GB free | ~30 sec/epoch | Hardware cost |
| **RunPod** | Cloud | Cloud | ~30 sec/epoch | ~$0.50/hr |
| **Replicate** | Cloud | Cloud | ~1 min/training | ~$0.05/min |

## Quick Start (M4 Mac)

### 1. Install dependencies

```bash
cd apps/api
pip install torch torchvision diffusers transformers peft accelerate
```

### 2. Prepare reference images

Place 10-20 images of your persona in:
```
storage/datasets/{persona_id}/
```

Images should be:
- 512x512 or larger (will be resized)
- Consistent face/appearance
- Various poses and lighting
- JPEG or PNG format

### 3. Start training

```bash
# Via API (recommended)
curl -X POST http://localhost:8001/api/v1/personas/{persona_id}/train \
  -H "Content-Type: application/json" \
  -d '{"epochs": 3, "learning_rate": 1e-4}'

# Or directly
python -c "
from app.providers.hf_trainer import HuggingFaceTrainer
import asyncio

trainer = HuggingFaceTrainer(device='mps')
result = asyncio.run(trainer.train(
    dataset_path='storage/datasets/{persona_id}',
    output_path='storage/models/loras/{persona_id}',
    epochs=3,
    learning_rate=1e-4,
))
print(result)
"
```

### 4. Monitor training

```bash
# Check job status
curl http://localhost:8001/api/v1/jobs/{job_id}

# Training logs appear in:
tail -f /tmp/persona_api.log
```

## Cloud GPU Setup (RunPod)

### 1. Create RunPod account

Go to [runpod.io](https://runpod.io) and add credits (~$10 is enough for testing).

### 2. Get API key

Settings → API Keys → Create new key

### 3. Configure

Add to `.env`:
```
RUNPOD_API_KEY=your_key_here
```

### 4. Update trainer

The code already supports RunPod — just set the API key and it will auto-detect.

## What Happens During Training

1. **Dataset preparation** — Images are loaded, resized to 512x512, normalized
2. **Model loading** — Stable Diffusion 1.5 base model (~4GB) is loaded
3. **LoRA injection** — Low-rank adapters are added to attention layers
4. **Training loop** — Model learns to generate images in your persona's style
5. **Checkpoint saving** — Final LoRA weights saved to `storage/models/loras/`

## Output

After training, you get:
```
storage/models/loras/{persona_id}/
├── adapter_config.json    # LoRA configuration
├── adapter_model.bin      # Trained weights (~10MB)
└── training_log.json      # Training metrics
```

## Using Trained Models

Once trained, the persona's images will use the LoRA weights automatically:

```python
from app.providers.hf_trainer import HuggingFaceTrainer

trainer = HuggingFaceTrainer()
result = await trainer.validate(
    model_path='storage/models/loras/{persona_id}',
    validation_images=['test_image.png'],
)
```

## Troubleshooting

### "Out of memory"
- Reduce batch size to 1
- Use 512x512 images
- Close other applications

### "Model not found"
- First training will download SD 1.5 (~4GB)
- Check internet connection
- Ensure enough disk space

### "Training too slow"
- M4: Normal, ~3-4 min/epoch
- Consider cloud GPU for faster training
- Reduce epochs to 1 for testing

### "Poor quality output"
- Need more reference images (10-20 minimum)
- Images should be consistent
- Try more epochs (5-10)
- Adjust learning rate

## Integration with Production Pipeline

The auto-produce pipeline automatically uses trained LoRA models when available:

1. Persona is created → reference images collected
2. LoRA training triggered → model trained
3. Auto-produce runs → uses trained LoRA for consistent images
4. Images are identity-locked → same face across all content

## Next Steps

- [ ] Set up RunPod for faster training
- [ ] Add batch training for multiple personas
- [ ] Implement model versioning
- [ ] Add training metrics dashboard
