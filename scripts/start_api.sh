#!/bin/bash
cd /Users/bank/Downloads/Gemma_Local_Agent_v3_MODEL_FIX/apps/api
export PYTHONPATH=/Users/bank/Downloads/Gemma_Local_Agent_v3_MODEL_FIX/apps/api
export UVICORN_HTTP=h11
export UVICORN_LOOP=asyncio
# Remove compiled extension caches so uvicorn uses pure-Python fallback
find /Users/bank/Library/Python/3.9/lib/python/site-packages/uvicorn -name '__pycache__' -exec rm -rf {} + 2>/dev/null
exec /usr/bin/python3 -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8001 \
    --no-access-log \
    --loop asyncio \
    --http h11
