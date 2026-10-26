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
import socket
from typing import Generator

from fastapi import HTTPException, status
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
    if INIT_ERROR:
        # Storage-dependent endpoints fail with a clear, honest status rather than a 500
        # carrying a driver traceback.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The database is unavailable, so reports cannot be stored or read. "
                "Diagnosis, model information and weather still work."
            ),
        )
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


# Set when the database could not be prepared at startup. The API keeps serving the
# endpoints that do not need storage (/health, /api/model, /api/weather) and reports the
# failure honestly instead of the whole process dying. A deploy should not be taken down
# by a database that is briefly unreachable — that turns a recoverable problem into an
# outage, and it hides the reason behind "exited with status 3".
INIT_ERROR: Optional[str] = None
INIT_REASON: Optional[str] = None


def host_diagnostics() -> dict:
    """Does the configured host resolve, and to which address family?

    This is the difference between three very different problems that all surface as
    "OperationalError": a typo (nothing resolves), an IPv6-only host on an IPv4-only
    platform (AAAA only — Supabase's direct host is exactly this), and a reachable host
    that rejects us for another reason. The hostname itself is never published.
    """
    if engine.dialect.name == "sqlite":
        return {"applies": False}
    host = engine.url.host
    if not host:
        return {"applies": True, "resolves": False, "address_family": "none"}
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return {"applies": True, "resolves": False, "address_family": "none"}
    families = {info[0] for info in infos}
    if socket.AF_INET in families and socket.AF_INET6 in families:
        family = "both"
    elif socket.AF_INET in families:
        family = "ipv4"
    elif socket.AF_INET6 in families:
        family = "ipv6_only"
    else:
        family = "unknown"
    return {"applies": True, "resolves": True, "address_family": family}


def classify_db_error(message: str) -> tuple:
    """Turn a driver message into (reason_code, hint) safe to publish.

    The raw message stays in the logs; /health gets a code and a fix, never a hostname,
    username or password. This exists because "OperationalError" alone is useless to
    whoever is staring at a failed deploy.
    """
    text = message.lower()
    if "could not translate host name" in text or "name or service not known" in text:
        return (
            "host_not_found",
            "The database hostname does not resolve from this server. On Render, Supabase's "
            "direct host is IPv6-only and cannot be reached — use the Session pooler "
            "hostname (aws-<n>-<region>.pooler.supabase.com) instead.",
        )
    if "network is unreachable" in text:
        return (
            "no_route",
            "This server has no route to the database address. Supabase's direct host is "
            "IPv6-only; switch DATABASE_URL to the Session pooler connection string.",
        )
    if "password authentication failed" in text or "authentication failed" in text:
        return (
            "auth_failed",
            "The database rejected the credentials. Reset the database password in "
            "Supabase and update DATABASE_URL (URL-encode characters such as @ and #).",
        )
    if "connection refused" in text:
        return (
            "connection_refused",
            "Nothing is listening on that host and port. Check the port: 5432 for the "
            "session pooler, 6543 for the transaction pooler.",
        )
    if "timeout" in text or "timed out" in text:
        return (
            "timeout",
            "The connection timed out. A free database may be waking up; retry shortly. If "
            "it persists, confirm the hostname and that the project is not paused.",
        )
    if "ssl" in text or "certificate" in text:
        return ("tls_error", "TLS negotiation failed. Append ?sslmode=require to DATABASE_URL.")
    if "too many connections" in text:
        return ("too_many_connections", "The connection pool is exhausted. Reduce pool_size or restart the service.")
    return ("unknown", "See the service logs for the driver message.")


def try_init_db() -> Optional[str]:
    """Initialise the database, returning an error description instead of raising."""
    global INIT_ERROR, INIT_REASON
    try:
        init_db()
        INIT_ERROR = None
        INIT_REASON = None
    except Exception as error:  # noqa: BLE001 - startup must not crash on this
        INIT_ERROR = f"{type(error).__name__}: {error}"
        INIT_REASON = classify_db_error(str(error))[0]
        logger.error("database unavailable at startup: %s", INIT_ERROR)
        if "supabase.co" in DATABASE_URL and "pooler" not in DATABASE_URL:
            logger.error(
                "DATABASE_URL points at Supabase's direct host. That host is IPv6-only on "
                "new projects and Render's free tier has no IPv6 egress. Use the Session "
                "pooler connection string instead "
                "(aws-<n>-<region>.pooler.supabase.com:5432)."
            )
        elif "could not translate host name" in str(error).lower():
            logger.error(
                "The database host could not be resolved. Check the hostname in "
                "DATABASE_URL — for Supabase it must be the pooler host."
            )
        elif "password authentication failed" in str(error).lower():
            logger.error("The database rejected the credentials. Reset the password and update DATABASE_URL.")
    return INIT_ERROR


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
