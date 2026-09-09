"""Persona Studio — Test configuration and fixtures."""

import asyncio
import pytest
import pytest_asyncio
from uuid import uuid4
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from fastapi import Depends
from app.main import app
from app.database import Base, get_db

from sqlalchemy.pool import StaticPool

# Use SQLite for testing — no external DB required
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,  # Single connection for SQLite concurrency
)
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


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


async def override_get_db():
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

# Override the database dependency for all tests
app.dependency_overrides[get_db] = override_get_db

# Override the workflow engine's session factory
from app.workflows.engine import workflow_engine
workflow_engine._session_factory = TestSessionLocal

# Force mock providers for tests (avoid real LLM/image/voice calls)
import os
os.environ["PROVIDER_REGISTRY"] = "mock"
os.environ["API_AUTH_TOKEN"] = ""
from app.providers.registry import reset_registry, get_registry
from app.config import get_settings
get_settings.cache_clear()
reset_registry()
_registry = get_registry()
assert _registry._mode == "mock", f"Expected mock, got {_registry._mode}"


@pytest.fixture(autouse=True)
def auth_disabled(monkeypatch):
    """Force API_AUTH_TOKEN empty for every test.

    The auth dependency calls get_settings() at request time, so the settings
    cache is cleared before/after each test: an operator's exported
    API_AUTH_TOKEN can never accidentally gate the suite.
    """
    monkeypatch.setenv("API_AUTH_TOKEN", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
