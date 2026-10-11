"""Shared pytest fixtures.

Every test runs against a fresh in-memory SQLite database using a single shared
connection, so tests are isolated and leave nothing behind on disk.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Make the backend package importable when pytest runs from the project root.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("SPORTMATE_DATABASE_URL", "sqlite://")

import main as main_module  # noqa: E402
from db import Base, get_db  # noqa: E402
from models import Sport  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    # Build the client without the app lifespan (which would initialise the real
    # file-backed database) and seed the sport catalogue ourselves.
    app = main_module.app
    app.dependency_overrides[get_db] = override_get_db

    with TestingSession() as seed:
        seed.add_all(
            [
                Sport(name="篮球", icon="basketball", default_capacity=10),
                Sport(name="羽毛球", icon="badminton", default_capacity=4),
            ]
        )
        seed.commit()

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def utc_now() -> datetime:
    """Timezone-aware UTC, matching the convention documented in db.utcnow()."""
    return datetime.now(timezone.utc)


def future(hours: float = 24, minutes: float = 0) -> datetime:
    return utc_now() + timedelta(hours=hours, minutes=minutes)


def future_iso(hours: float = 24, minutes: float = 0) -> str:
    """ISO-8601 timestamp carrying an explicit +00:00 offset.

    The offset matters: without it pydantic produces a naive datetime and the
    comparison against UTC inside the service layer becomes meaningless.
    """
    return future(hours, minutes).isoformat()


@pytest.fixture()
def register(client: TestClient):
    """Register a user and return (headers, user_json)."""

    def _register(
        username: str,
        *,
        skill_level: str = "any",
        campus: str = "旗山校区",
        favorite_sports: list[str] | None = None,
    ):
        response = client.post(
            "/auth/register",
            json={
                "username": username,
                "password": "secret123",
                "nickname": f"{username}-昵称",
                "campus": campus,
                "skill_level": skill_level,
                "favorite_sports": favorite_sports or [],
            },
        )
        assert response.status_code == 201, response.text
        token = response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        me = client.get("/me", headers=headers)
        assert me.status_code == 200, me.text
        return headers, me.json()

    return _register


@pytest.fixture()
def make_activity(client: TestClient):
    """Create an activity as the given user and return its JSON."""

    def _make(
        headers: dict,
        *,
        sport: str = "篮球",
        title: str = "周末打球",
        hours: float = 24,
        duration_hours: float = 2,
        capacity: int = 4,
        skill_level: str = "any",
        campus: str = "旗山校区",
        latitude: float | None = 26.0565,
        longitude: float | None = 119.1966,
    ):
        response = client.post(
            "/activities",
            headers=headers,
            json={
                "title": title,
                "sport": sport,
                "campus": campus,
                "location": "风雨操场",
                "latitude": latitude,
                "longitude": longitude,
                "start_time": future_iso(hours),
                "end_time": future_iso(hours + duration_hours),
                "skill_level": skill_level,
                "capacity": capacity,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _make
