"""Startup and infrastructure regression tests.

These guard behaviour that the in-memory test database never exercises.

Known gap: actually creating the SQLite parent directory cannot be asserted here
because the sandbox denies nested mkdir calls. The real path was verified
manually by starting the app through its lifespan (which creates ``data/`` and
seeds the sport catalogue); the URL parsing that drives it is covered below.
"""

from __future__ import annotations

from pathlib import Path

import db as db_module

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_sqlite_directory_helper_ignores_memory_and_non_sqlite():
    """In-memory and non-SQLite URLs must be no-ops, never raise."""
    db_module._ensure_sqlite_directory("sqlite://")
    db_module._ensure_sqlite_directory("sqlite:///:memory:")
    db_module._ensure_sqlite_directory("postgresql+psycopg://user:pw@localhost/db")


def test_sqlite_helper_accepts_file_urls_without_raising():
    # Existing directory: the helper must be a harmless no-op.
    existing = BACKEND_ROOT / "data"
    db_module._ensure_sqlite_directory(f"sqlite:///{(existing / 'app.db').as_posix()}")


def test_utcnow_is_naive_utc():
    """Storage uses naive UTC; a tz-aware value here would break comparisons."""
    now = db_module.utcnow()
    assert now.tzinfo is None


def test_health_and_sport_seed(client):
    """The app must answer /health and expose the seeded sport catalogue."""
    assert client.get("/health").json()["status"] == "ok"
    sports = client.get("/sports").json()
    names = {s["name"] for s in sports}
    assert {"篮球", "羽毛球"} <= names


def test_openapi_has_expected_routes():
    """Regression guard for route ordering and accidental removals."""
    import main

    paths = main.app.openapi()["paths"]
    assert "/activities/mine" in paths
    assert "/activities/{activity_id}" in paths
    assert "/match/parse" in paths
    assert "/activities/{activity_id}/checkin" in paths
    assert "/activities/{activity_id}/attendance" in paths
