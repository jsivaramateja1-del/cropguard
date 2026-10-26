"""Database engine, session factory and schema creation.

Production uses PostgreSQL on Supabase through ``DATABASE_URL``. When that variable
is unset the app falls back to ``sqlite:///./cropguard_local.db`` so a developer can
clone the repo and run the API with no setup at all.

Two details matter for Supabase specifically:

* Supabase hands out ``postgres://`` URLs (the Heroku-era alias). SQLAlchemy 2.x
  rejects that scheme, so it is rewritten to ``postgresql://`` here.
* ``pool_pre_ping`` is enabled for PostgreSQL so that a pooled connection killed by
  the server (idle timeout, failover) is transparently replaced instead of raising
  on the next request. SQLite does not need it and does not support it well.
"""

from __future__ import annotations

import logging
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import BASE_DIR, settings

logger = logging.getLogger("cropguard.database")

SQLITE_FALLBACK = f"sqlite:///{BASE_DIR / 'cropguard_local.db'}"


def normalise_database_url(raw: str) -> str:
    """Return a URL SQLAlchemy accepts, or the SQLite fallback when empty."""
    url = (raw or "").strip()
    if not url:
        return SQLITE_FALLBACK
    if url.startswith("postgres://"):
        # Supabase/Heroku style alias -> SQLAlchemy 2.x scheme
        return "postgresql://" + url[len("postgres://") :]
    return url


DATABASE_URL = normalise_database_url(settings.database_url)
IS_SQLITE = DATABASE_URL.startswith("sqlite")

_engine_kwargs: dict = {"future": True, "echo": False}
if IS_SQLITE:
    # FastAPI serves requests from a thread pool, so SQLite must allow cross-thread use.
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update({"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10})

engine: Engine = create_engine(DATABASE_URL, **_engine_kwargs)


if IS_SQLITE:

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        """SQLite ignores foreign keys unless asked; without this, cascade deletes lie."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


class Base(DeclarativeBase):
    """Declarative base shared by every model."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session that is always closed.

    Committing is the caller's responsibility so that a request can fail between
    two writes without leaving a half-applied change behind.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# Columns added after the first deployment. `create_all` creates missing *tables* but
# never alters an existing one, so a live SQLite database would reject every insert after
# a schema change. These are applied idempotently at startup.
_ADDED_COLUMNS = {
    "reports": {
        "owner_token_hash": "VARCHAR(64)",
        "reference": "VARCHAR(16)",
    },
}


def _apply_additive_migrations() -> None:
    """Add any missing columns to existing tables, and the indexes that go with them."""
    from sqlalchemy import inspect, text  # noqa: PLC0415

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in existing_tables:
                continue
            present = {column["name"] for column in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name in present:
                    continue
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
                logger.info("migration: added %s.%s", table, name)
        # Indexes and the unique constraint are created only if absent.
        for statement in (
            "CREATE INDEX IF NOT EXISTS ix_reports_owner_token_hash ON reports (owner_token_hash)",
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_reports_reference ON reports (reference)",
        ):
            try:
                connection.execute(text(statement))
            except Exception as error:  # noqa: BLE001 - non-fatal, log and continue
                logger.warning("migration step skipped (%s): %s", statement, error)


def init_db() -> None:
    """Create tables that do not exist yet.

    Fine for the prototype and for SQLite. Production schema changes go through
    Alembic migrations (wired up in Phase 7).
    """
    import models  # noqa: F401  (import registers the mappers on Base.metadata)

    Base.metadata.create_all(bind=engine)
    _apply_additive_migrations()


def database_info() -> dict:
    """Non-secret summary used by /health. Never exposes credentials."""
    dialect = engine.dialect.name
    if IS_SQLITE:
        # Report the file actually in use, not the fallback default: a test or a
        # deployment may point DATABASE_URL at a different SQLite file.
        target = engine.url.database or ":memory:"
    else:
        target = f"{engine.url.host}:{engine.url.port or 5432}/{engine.url.database}"
    return {"dialect": dialect, "target": target}
