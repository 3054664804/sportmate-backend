"""Application configuration.

All settings can be overridden through environment variables so the same image
can run locally, in Docker and on a PaaS such as Render.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def now_utc() -> datetime:
    """Single source of truth for 'now'.

    Always timezone-aware UTC. Comparing this against naive datetimes (which is
    what a client sends when it omits an offset) raises TypeError instead of
    silently producing a wrong answer, which is the failure mode we want.
    """
    return datetime.now(timezone.utc)

# SQLite by default: zero-config, good enough for a campus pilot.
DATABASE_URL: str = os.getenv(
    "SPORTMATE_DATABASE_URL",
    f"sqlite:///{(BASE_DIR / 'data' / 'sportmate.db').as_posix()}",
)

# Tests use an in-memory database and never touch the token TTL below.
APP_NAME: str = "SportMate 校园运动组局平台"
APP_VERSION: str = "0.1.0"

# Opaque session tokens are stored hashed; a plain token is only ever returned
# once, at login time.
TOKEN_TTL_DAYS: int = int(os.getenv("SPORTMATE_TOKEN_TTL_DAYS", "30"))

# Reliability score rules. The score starts at 100 and is clamped to [0, 100].
RELIABILITY_INITIAL: int = 100
RELIABILITY_MIN: int = 0
RELIABILITY_MAX: int = 100
RELIABILITY_COMPLETED_DELTA: int = 2
RELIABILITY_LATE_CANCEL_DELTA: int = -5
RELIABILITY_NO_SHOW_DELTA: int = -15

# A user below this score is temporarily blocked from joining new activities.
RELIABILITY_JOIN_THRESHOLD: int = 60

# Sign-ups must be cancelled at least this many minutes before start time,
# otherwise the cancellation counts as late.
LATE_CANCEL_WINDOW_MINUTES: int = 120

# Geofence radius (metres) used by check-in.
CHECKIN_RADIUS_METERS: int = 300

# Organiser-provided check-in code length.
CHECKIN_CODE_LENGTH: int = 4
