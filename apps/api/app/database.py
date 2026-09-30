"""Persona Studio API — Database setup."""

import os
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings

settings = get_settings()

_is_sqlite = "sqlite" in settings.DATABASE_URL

if _is_sqlite:
    # SQLite over a file: WAL plus a real pool, so two sessions are two
    # connections.
    #
    # This was StaticPool — one DBAPI connection shared by every session. With
    # two sessions on one connection, the second session's `commit()` commits
    # *inside* the first session's still-open transaction rather than ending
    # it. The symptom was exact and confusing: `POST /personas` spawned the
    # build job and then awaited one more query, that query opened a
    # transaction, the background build's `create_workflow` ran inside it, its
    # commit was swallowed, and the workflow row was never written — a count
    # taken immediately after that commit returned 0, and the build died with
    # "Could not refresh instance '<Workflow>'". Nothing about that message
    # points at connection pooling.
    #
    # StaticPool *is* the right choice for `:memory:`, where each connection
    # would otherwise be a separate empty database. This URL is a file, and
    # `busy_timeout` is what makes concurrent writers wait rather than fail.
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.ENVIRONMENT == "development",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
else:
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.ENVIRONMENT == "development",
        pool_pre_ping=True,
    )

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


async def init_db():
    """Create all tables — used for SQLite local dev."""
    if _is_sqlite:
        # Enable WAL mode for better concurrency
        @event.listens_for(engine.sync_engine, "connect")
        def _set_pragma(dbapi_conn, connection_record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()
    from app.models import Base as ModelBase, reconcile_identity_locks  # noqa
    async with engine.begin() as conn:
        await conn.run_sync(ModelBase.metadata.create_all)
    # SQLite create_all never ALTERs an existing table; reconcile legacy
    # identity_locks so new lock rows can actually be inserted.
    await reconcile_identity_locks(engine)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
