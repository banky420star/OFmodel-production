#!/usr/bin/env python3
"""Start the Persona Studio API server — bypasses compiled extensions for launchd."""
import sys, os

os.chdir("/Users/bank/Downloads/Gemma_Local_Agent_v3_MODEL_FIX/apps/api")
sys.path.insert(0, "/Users/bank/Downloads/Gemma_Local_Agent_v3_MODEL_FIX/apps/api")
os.environ["UVICORN_HTTP"] = "h11"
os.environ["UVICORN_LOOP"] = "asyncio"

# Import hook that makes `import httptools` / `import uvloop` raise ImportError
class _BlockedImporter:
    BLOCKED = {"httptools", "uvloop"}
    def find_module(self, name, path=None):
        top = name.split(".")[0]
        if top in self.BLOCKED:
            return self
    def load_module(self, name):
        raise ImportError(f"Blocked compiled extension: {name}")

sys.meta_path.insert(0, _BlockedImporter())

import uvicorn
uvicorn.run("app.main:app", host="0.0.0.0", port=8001, loop="asyncio", http="h11", access_log=False)
