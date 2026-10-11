"""Shared FastAPI dependencies: database session and current user."""

from __future__ import annotations

from fastapi import Depends, Header, status
from sqlalchemy.orm import Session

import services
from db import get_db
from models import User


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Resolve a bearer token into a user, or fail with 401."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized("请先登录")

    token = authorization.split(" ", 1)[1].strip()
    user = services.resolve_token(db, token)
    if not user:
        raise _unauthorized("登录已过期，请重新登录")
    return user


def _unauthorized(message: str) -> services.ServiceError:
    return services.ServiceError(message, status.HTTP_401_UNAUTHORIZED)


def extract_token(authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(" ", 1)[1].strip()
