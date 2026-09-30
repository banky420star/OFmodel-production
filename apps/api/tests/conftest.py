"""
Persona Studio — test configuration and fixtures.

The registry is strict (real providers only, no mocks in app/), so tests
inject the deterministic fakes from tests/fakes.py via force_override(),
which refuses to run outside ENVIRONMENT=test.
"""
import asyncio
import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import event

# Use a real SQLite file for testing — no external DB required.
# A file (not :memory:) matters because background tasks (persona build,
# auto-produce) open their own sessions on separate connections; an in-memory
# singleton pool makes those connections invisible to each other.
_test_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_test_db_file.close()
TEST_DB_PATH = _test_db_file.name
TEST_DATABASE_URL = f"sqlite+aiosqlite:///{TEST_DB_PATH}"

# Set env BEFORE app modules import: app.database builds its engine (and
# AsyncSessionLocal, which the identity engine and workflow engine now share)
# from settings at import time.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ENVIRONMENT"] = "test"

from app.main import app  # noqa: E402
from app.database import Base, AsyncSessionLocal, get_db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.providers.registry import get_registry  # noqa: E402

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 30},
)
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@event.listens_for(test_engine.sync_engine, "connect")
def _configure_test_sqlite(dbapi_conn, _connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    """Create all tables for testing."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


# Inject the deterministic offline fakes into the strict registry.
from tests.fakes import (  # noqa: E402
    FakeLLMProvider, FakeImageProvider, FakeVideoProvider,
    FakeVoiceProvider, FakeTrainerProvider, FakeStorageProvider,
)

get_settings.cache_clear()
_registry = get_registry()
_registry.force_override("llm", FakeLLMProvider())
_registry.force_override("image", FakeImageProvider())
_registry.force_override("video", FakeVideoProvider())
_registry.force_override("voice", FakeVoiceProvider())
_registry.force_override("trainer", FakeTrainerProvider())
_registry.force_override("storage", FakeStorageProvider())


@pytest.fixture(scope="session", autouse=True)
def isolate_storage(tmp_path_factory):
    """Keep the harness out of production storage.

    Overriding the providers above was only half the job: nothing redirected the
    directories they write into. `FakeImageProvider._tiny_png` (tests/fakes.py,
    the 8x8 grey PNG) and `LoraAwareImageProvider` (b"\\x89PNG\\r\\n\\x1a\\n",
    8 bytes) therefore wrote real files into `apps/api/storage/` on every run —
    that is where the 314 stub files under `storage/shoots/` and every
    `avatars/e2e_ava_*` came from. No production code writes placeholders
    (`MIN_AVATAR_PIXELS = 256` refuses them), so the harness was the only source.

    Patches both `app.paths` itself and every module that captured one of its
    values at import time, so a test that writes into a storage dir and the
    production code that reads it back land on the same tmp directory. If a new
    module keeps its own name for one of those dirs, add it here.
    """
    from pytest import MonkeyPatch

    root = tmp_path_factory.mktemp("storage")
    subdirs = {
        "AVATAR_DIR": "avatars",
        "SHOOT_DIR": "shoots",
        "GALLERY_DIR": "gallery",
        "VIDEOS_DIR": "videos",
        "MODELS_DIR": "models",
        "LORA_DIR": "models/loras",
        "DATASETS_DIR": "datasets",
        "VOICES_DIR": "voices",
        "ADULT_CONTENT_DIR": "adult_content",
    }
    for sub in subdirs.values():
        (root / sub).mkdir(parents=True, exist_ok=True)

    import app.paths as app_paths
    import app.identity_engine as identity_engine
    import app.main as app_main
    import app.providers.hf_trainer as hf_trainer
    import app.providers.macos_voice as macos_voice
    import app.routes.content as route_content
    import app.routes.fan as route_fan
    import app.routes.personas as route_personas
    import app.routes.schedule as route_schedule
    import app.workflows.content_flow as content_flow

    mp = MonkeyPatch()

    # app/paths.py is the source. Modules that read `paths.X` at call time
    # (persona_flow) follow this automatically.
    mp.setattr(app_paths, "STORAGE_ROOT", root)
    for name, sub in subdirs.items():
        mp.setattr(app_paths, name, root / sub)

    # Modules that bound the value to their own global at import time.
    for module, name, sub in [
        (identity_engine, "AVATAR_DIR", "avatars"),
        (identity_engine, "SHOOT_DIR", "shoots"),
        (identity_engine, "GALLERY_DIR", "gallery"),
        (app_main, "AVATARS_DIR", "avatars"),
        (app_main, "SHOOTS_DIR", "shoots"),
        (app_main, "GALLERY_DIR", "gallery"),
        (app_main, "VIDEOS_DIR", "videos"),
        (app_main, "ADULT_DIR", "adult_content"),
        (hf_trainer, "MODELS_DIR", "models"),
        (hf_trainer, "LORA_DIR", "models/loras"),
        (hf_trainer, "DATASETS_DIR", "datasets"),
        (macos_voice, "VOICE_DIR", "voices"),
        (content_flow, "SHOOT_DIR", "shoots"),
        (route_content, "SHOOT_DIR", "shoots"),
        (route_content, "ADULT_CONTENT_DIR", "adult_content"),
        (route_fan, "STORAGE_DIR", ""),
        (route_personas, "AVATAR_DIR", "avatars"),
        (route_personas, "GALLERY_DIR", "gallery"),
        (route_schedule, "STORAGE_ROOT", ""),
    ]:
        mp.setattr(module, name, root / sub if sub else root)

    yield
    mp.undo()


async def override_get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Override the database dependency for all tests
app.dependency_overrides[get_db] = override_get_db

# Override the workflow engine's session factory
from app.workflows.engine import workflow_engine  # noqa: E402
workflow_engine._session_factory = AsyncSessionLocal


@pytest.fixture
def registry_override():
    """Inject a provider for one capability, then put the fake back.

    `force_override` refuses to run outside ENVIRONMENT=test, so this fixture
    cannot be a way to sneak a stub into a real process. The teardown restores
    the previous override because the registry is a module-level singleton
    shared by every test in the session — without it, a test that swaps out the
    image provider would silently change what later tests resolve.
    """
    registry = get_registry()
    saved: dict[str, object] = {}

    def _override(capability: str, instance):
        if capability not in saved:
            saved[capability] = registry.resolve_optional(capability)
        registry.force_override(capability, instance)
        return instance

    yield _override

    for capability, previous in saved.items():
        if previous is None:
            registry.clear_override(capability)
        else:
            registry.force_override(capability, previous)


@pytest_asyncio.fixture(scope="session")
async def assigned_persona(setup_db):
    """An ACTIVE persona, and FAN_DEFAULT_PERSONA_ID pointed at it.

    Auth and fan tests need a persona to be assigned, and there are two traps:

    * `POST /personas` kicks off the build workflow and leaves the row in
      `building` — a state `_assign_persona` correctly refuses to assign a fan
      to. So the row is inserted directly.
    * Without a fixed default, assignment falls back to "oldest ACTIVE persona",
      which depends on what other test files happened to create earlier in the
      session. Pinning the setting makes the assigned persona deterministic
      instead of an accident of test ordering.
    """
    from app.config import get_settings
    from app.models import Persona, PersonaStatus

    settings = get_settings()
    saved = settings.FAN_DEFAULT_PERSONA_ID

    from sqlalchemy import select

    async with TestSessionLocal() as session:
        row = (await session.execute(
            select(Persona).where(Persona.name == "Testpersona")
        )).scalar_one_or_none()
        if row is None:
            row = Persona(
                name="Testpersona",
                age=26,
                description="an ACTIVE persona for auth tests",
                status=PersonaStatus.ACTIVE,
                brand="lifestyle",
                synthetic_identity=True,
            )
            session.add(row)
            await session.commit()
        persona_id = str(row.id)

    settings.FAN_DEFAULT_PERSONA_ID = persona_id
    yield {"id": persona_id, "name": "Testpersona"}
    settings.FAN_DEFAULT_PERSONA_ID = saved


@pytest_asyncio.fixture
async def db(setup_db):
    """Provide a clean database session per test."""
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(setup_db):
    """Provide an async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

@pytest.fixture
def write_png():
    """Write a real, decodable PNG to `path` and return the path.

    Tests used to write `b"\\x89PNG\\r\\n\\x1a\\n" + b"0" * 64` — a PNG header
    with no image in it, which is exactly the shape of the placeholder files
    this harness left in real storage. That was harmless right up until the
    publish path learned to refuse placeholder media, at which point the
    fixtures were the thing it refused. Fixtures that stand in for content
    should look like content.
    """
    from PIL import Image

    def _write(target, size=(256, 256), colour=(90, 120, 160)):
        """`target` is a path or any writable buffer; it is returned as given."""
        Image.new("RGB", size, colour).save(target, format="PNG")
        return target

    return _write
