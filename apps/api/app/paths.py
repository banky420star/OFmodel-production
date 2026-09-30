"""The one place the storage root is defined.

Every module that writes a generated file and every route that serves one must
agree on the same directory, and it must be resolved from this file rather than
from the process CWD. Before this module, five files each built their own path
six different ways — `.parent.parent / "storage"`, `.resolve().parents[2] /
"storage"`, bare `Path("storage/shoots")` — and three of them got it wrong
(`parents[1]` -> `apps/api/app/storage/`), writing files where `main.py` never
serves them. That is how `app/storage/avatars/naomi_locked.png` came to exist
and 404.

Modules keep their own module-level names (e.g. `identity_engine.AVATAR_DIR`)
because tests patch those names directly; they simply now derive from here.
"""
from pathlib import Path

# app/paths.py -> parents[1] is apps/api, so this is apps/api/storage.
STORAGE_ROOT = Path(__file__).resolve().parents[1] / "storage"

AVATAR_DIR = STORAGE_ROOT / "avatars"
SHOOT_DIR = STORAGE_ROOT / "shoots"
GALLERY_DIR = STORAGE_ROOT / "gallery"
VIDEOS_DIR = STORAGE_ROOT / "videos"
MODELS_DIR = STORAGE_ROOT / "models"
LORA_DIR = MODELS_DIR / "loras"
DATASETS_DIR = STORAGE_ROOT / "datasets"
VOICES_DIR = STORAGE_ROOT / "voices"
ADULT_CONTENT_DIR = STORAGE_ROOT / "adult_content"
