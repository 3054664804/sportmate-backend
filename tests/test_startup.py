"""Startup and infrastructure regression tests.

These guard behaviour that the in-memory test database never exercises.

Known gap: actually creating the SQLite parent directory cannot be asserted here
because the sandbox denies nested mkdir calls. The real path was verified
manually by starting the app through its lifespan (which creates ``data/`` and
seeds the sport catalogue); the URL parsing that drives it is covered below.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import db as db_module

BACKEND_ROOT = Path(__file__).resolve().parents[1]
# The workspace root is the parent of the backend package; scratch directories
# are created there rather than in the system temp folder, which is sandboxed.
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def test_sqlite_directory_helper_ignores_memory_and_non_sqlite():
    """In-memory and non-SQLite URLs must be no-ops, never raise."""
    db_module._ensure_sqlite_directory("sqlite://")
    db_module._ensure_sqlite_directory("sqlite:///:memory:")
    db_module._ensure_sqlite_directory("postgresql+psycopg://user:pw@localhost/db")


def test_sqlite_helper_accepts_file_urls_without_raising():
    # Existing directory: the helper must be a harmless no-op.
    existing = BACKEND_ROOT / "data"
    db_module._ensure_sqlite_directory(f"sqlite:///{(existing / 'app.db').as_posix()}")


def test_relative_sqlite_path_resolves_against_cwd():
    """Relative URLs (as used in .env.example) must follow SQLite's own rule.

    SQLite resolves a relative path against the current working directory, so
    the directory creation must do the same -- otherwise the app creates
    ``<project>/data`` while SQLite tries to open ``<cwd>/data`` and fails.

    Tested through the pure path helper because the sandbox blocks os.chdir.
    """
    fake_cwd = Path("/somewhere/else")
    resolved = db_module.sqlite_parent_dir("sqlite:///./data/sportmate.db", cwd=fake_cwd)
    assert resolved == fake_cwd / "data"

    # Absolute URLs ignore the current working directory entirely.
    absolute = db_module.sqlite_parent_dir(
        "sqlite:////var/data/sportmate.db", cwd=fake_cwd
    )
    assert absolute == Path("/var/data")


def test_sqlite_parent_dir_returns_none_for_non_file_urls():
    assert db_module.sqlite_parent_dir("sqlite://") is None
    assert db_module.sqlite_parent_dir("sqlite:///:memory:") is None
    assert db_module.sqlite_parent_dir("postgresql+psycopg://user:pw@host/db") is None


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


def test_error_contract_always_uses_string_detail(client):
    """``detail`` must be a string on every error, including 422.

    FastAPI's default 422 body carries a list of error objects, which forces
    frontends to branch on the type. The app normalises it, and this test keeps
    that contract from silently regressing.
    """
    # 422 - pydantic validation
    invalid = client.post(
        "/auth/register", json={"username": "ab", "password": "1", "nickname": ""}
    )
    assert invalid.status_code == 422
    body = invalid.json()
    assert isinstance(body["detail"], str), "422 的 detail 必须是字符串"
    assert body["error"] == "validation_error"

    # 401 - missing token
    unauthorized = client.get("/me")
    assert unauthorized.status_code == 401
    assert isinstance(unauthorized.json()["detail"], str)

    # 404 - business rule
    missing = client.get("/activities/999999")
    assert missing.status_code == 404
    assert isinstance(missing.json()["detail"], str)
