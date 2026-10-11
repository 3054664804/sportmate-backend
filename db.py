"""Database engine, session factory and declarative base."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

import config


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        # check_same_thread=False is required because FastAPI runs sync
        # endpoints in a thread pool.
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


def _ensure_sqlite_directory(url: str) -> None:
    """Create the parent directory of a file-backed SQLite database.

    Without this, a fresh checkout fails at startup with
    ``sqlite3.OperationalError: unable to open database file`` because SQLite
    will create the file but never the directory.
    """
    if not url.startswith("sqlite"):
        return

    raw_path = url.split("///", 1)[-1]
    if not raw_path or raw_path.startswith(":memory:"):
        return

    candidate = Path(raw_path)
    if candidate.is_absolute():
        Path(candidate).parent.mkdir(parents=True, exist_ok=True)
    else:
        # Relative SQLite URLs are resolved against the process CWD, but the
        # default configuration already produces an absolute path.
        (config.BASE_DIR / candidate).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_directory(config.DATABASE_URL)

engine = create_engine(config.DATABASE_URL, **_engine_kwargs(config.DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def utcnow() -> datetime:
    """Timezone-aware UTC now.

    SQLite cannot store an offset, so the value is normalised to naive UTC for
    persistence. Comparisons therefore always happen between two naive-UTC
    values, and clients are expected to send ISO-8601 timestamps with an
    explicit offset (which pydantic parses into aware datetimes).
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables and seed the sport catalogue (idempotent)."""
    import models  # noqa: F401  (import registers the mappers)

    Base.metadata.create_all(bind=engine)

    from models import Sport

    with SessionLocal() as db:
        if db.query(Sport).count() == 0:
            db.add_all(
                [
                    Sport(name="篮球", icon="basketball", default_capacity=10),
                    Sport(name="羽毛球", icon="badminton", default_capacity=4),
                    Sport(name="足球", icon="football", default_capacity=12),
                    Sport(name="乒乓球", icon="table-tennis", default_capacity=4),
                    Sport(name="网球", icon="tennis", default_capacity=4),
                    Sport(name="跑步", icon="running", default_capacity=6),
                    Sport(name="徒步", icon="hiking", default_capacity=8),
                    Sport(name="骑行", icon="cycling", default_capacity=6),
                    Sport(name="健身", icon="gym", default_capacity=4),
                    Sport(name="排球", icon="volleyball", default_capacity=12),
                ]
            )
            db.commit()
