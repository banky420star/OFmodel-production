#!/usr/bin/env python3
"""Run LoRA training in background."""
import asyncio
import time
import sys

async def train():
    sys.stdout.write("Starting LoRA training...\n")
    sys.stdout.flush()
    from app.providers.hf_trainer import HuggingFaceTrainer
    t = HuggingFaceTrainer()
    sys.stdout.write(f"Device: {t._device}\n")
    sys.stdout.flush()
    start = time.time()
    result = await t.train(dataset_id='test_dataset', model_type='lora', rank=4, epochs=1)
    elapsed = time.time() - start
    if result.success:
        sys.stdout.write(f"SUCCESS|{elapsed:.1f}|{result.data.get('model_path')}|{result.data.get('final_loss')}\n")
    else:
        sys.stdout.write(f"FAILED|{elapsed:.1f}|{result.error}\n")
    sys.stdout.flush()

asyncio.run(train())
