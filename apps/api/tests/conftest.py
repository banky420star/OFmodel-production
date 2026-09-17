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