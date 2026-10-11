"""ORM models for the campus sports matchmaking platform.

Domain loop covered here:
    create activity -> join -> check in -> mark attendance -> review -> score
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

import config
from db import Base, utcnow


class SkillLevel(str, enum.Enum):
    """Self-declared level, used by the matching engine."""

    beginner = "beginner"      # 新手 / 娱乐
    intermediate = "intermediate"  # 一般 / 会打
    advanced = "advanced"      # 熟练 / 竞技
    any = "any"                # 不限水平


class ActivityStatus(str, enum.Enum):
    open = "open"              # 可报名
    full = "full"              # 已满员
    ongoing = "ongoing"        # 进行中
    finished = "finished"      # 已结束
    cancelled = "cancelled"    # 已取消


class RegistrationStatus(str, enum.Enum):
    registered = "registered"  # 已报名，未签到
    checked_in = "checked_in"  # 已签到
    attended = "attended"      # 组织者确认到场
    no_show = "no_show"        # 放鸽子
    cancelled = "cancelled"    # 主动取消


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    nickname: Mapped[str] = mapped_column(String(32))
    campus: Mapped[str] = mapped_column(String(64), default="旗山校区")
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Self-declared level per user, matched against activity requirements.
    skill_level: Mapped[SkillLevel] = mapped_column(
        Enum(SkillLevel, native_enum=False, length=16), default=SkillLevel.any
    )
    favorite_sports: Mapped[str] = mapped_column(String(255), default="")

    # Anti-flake reputation.
    reliability_score: Mapped[int] = mapped_column(
        Integer, default=config.RELIABILITY_INITIAL
    )
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    no_show_count: Mapped[int] = mapped_column(Integer, default=0)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    organized: Mapped[list["Activity"]] = relationship(
        back_populates="organizer", cascade="all, delete-orphan"
    )
    registrations: Mapped[list["Registration"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def blocked_from_joining(self) -> bool:
        return self.reliability_score < config.RELIABILITY_JOIN_THRESHOLD


class UserSession(Base):
    """Opaque bearer token. Only the SHA-256 digest is stored."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship()


class Sport(Base):
    """Sport catalogue, seeded on first start."""

    __tablename__ = "sports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    icon: Mapped[str] = mapped_column(String(32), default="")
    default_capacity: Mapped[int] = mapped_column(Integer, default=6)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organizer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    title: Mapped[str] = mapped_column(String(80))
    sport: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str] = mapped_column(Text, default="")

    campus: Mapped[str] = mapped_column(String(64), default="旗山校区", index=True)
    location: Mapped[str] = mapped_column(String(120), default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    start_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime)

    skill_level: Mapped[SkillLevel] = mapped_column(
        Enum(SkillLevel, native_enum=False, length=16), default=SkillLevel.any
    )
    capacity: Mapped[int] = mapped_column(Integer, default=6)
    gender_limit: Mapped[str] = mapped_column(String(8), default="any")  # any/male/female
    fee: Mapped[int] = mapped_column(Integer, default=0)  # 人均费用，单位分

    status: Mapped[ActivityStatus] = mapped_column(
        Enum(ActivityStatus, native_enum=False, length=16), default=ActivityStatus.open, index=True
    )
    checkin_code: Mapped[str] = mapped_column(String(8), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    organizer: Mapped[User] = relationship(back_populates="organized")
    registrations: Mapped[list["Registration"]] = relationship(
        back_populates="activity", cascade="all, delete-orphan"
    )

    @property
    def active_registrations(self) -> list["Registration"]:
        """Registrations that still occupy a slot."""
        return [
            r
            for r in self.registrations
            if r.status
            in (
                RegistrationStatus.registered,
                RegistrationStatus.checked_in,
                RegistrationStatus.attended,
            )
        ]

    @property
    def joined_count(self) -> int:
        return len(self.active_registrations)

    @property
    def slots_left(self) -> int:
        return max(self.capacity - self.joined_count, 0)


class Registration(Base):
    __tablename__ = "registrations"
    __table_args__ = (
        UniqueConstraint("activity_id", "user_id", name="uq_registration_activity_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    status: Mapped[RegistrationStatus] = mapped_column(
        Enum(RegistrationStatus, native_enum=False, length=16),
        default=RegistrationStatus.registered,
        index=True,
    )
    note: Mapped[str] = mapped_column(String(200), default="")

    # Filled by check-in. method is "code" or "geo".
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checkin_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    checkin_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    is_late_cancel: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    activity: Mapped[Activity] = relationship(back_populates="registrations")
    user: Mapped[User] = relationship(back_populates="registrations")


class Review(Base):
    """Peer review written after an activity is finished."""

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint(
            "activity_id", "reviewer_id", "reviewee_id", name="uq_review_triple"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), index=True
    )
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    reviewee_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    score: Mapped[int] = mapped_column(Integer)  # 1..5
    tags: Mapped[str] = mapped_column(String(200), default="")  # comma separated
    comment: Mapped[str] = mapped_column(String(500), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    reviewer: Mapped[User] = relationship(foreign_keys=[reviewer_id])
    reviewee: Mapped[User] = relationship(foreign_keys=[reviewee_id])
