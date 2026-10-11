"""Request and response schemas (Pydantic v2)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from models import ActivityStatus, RegistrationStatus, SkillLevel


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
class UserRegister(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(min_length=6, max_length=64)
    nickname: str = Field(min_length=1, max_length=32)
    campus: str = Field(default="旗山校区", max_length=64)
    phone: str | None = Field(default=None, max_length=20)
    skill_level: SkillLevel = SkillLevel.any
    favorite_sports: list[str] = Field(default_factory=list)


class UserLogin(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=64)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    nickname: str
    campus: str
    skill_level: SkillLevel
    favorite_sports: list[str] = Field(default_factory=list)
    reliability_score: int
    completed_count: int
    no_show_count: int
    created_at: datetime

    @field_validator("favorite_sports", mode="before")
    @classmethod
    def _split_sports(cls, v: object) -> object:
        if isinstance(v, str):
            return [s for s in (x.strip() for x in v.split(",")) if s]
        return v


class UserUpdate(BaseModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=32)
    campus: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=20)
    skill_level: SkillLevel | None = None
    favorite_sports: list[str] | None = None


class UserProfile(UserOut):
    """Public profile with review aggregate."""

    model_config = ConfigDict(from_attributes=True)

    review_average: float | None = None
    review_count: int = 0


# --------------------------------------------------------------------------- #
# Sport
# --------------------------------------------------------------------------- #
class SportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    icon: str
    default_capacity: int


# --------------------------------------------------------------------------- #
# Activity
# --------------------------------------------------------------------------- #
class ActivityCreate(BaseModel):
    title: str = Field(min_length=2, max_length=80)
    sport: str = Field(min_length=1, max_length=32)
    description: str = Field(default="", max_length=1000)
    campus: str = Field(default="旗山校区", max_length=64)
    location: str = Field(default="", max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    start_time: datetime
    end_time: datetime
    skill_level: SkillLevel = SkillLevel.any
    capacity: int = Field(default=6, ge=2, le=100)
    gender_limit: str = Field(default="any", pattern=r"^(any|male|female)$")
    fee: int = Field(default=0, ge=0, le=1000000)

    @model_validator(mode="after")
    def _check_window(self) -> "ActivityCreate":
        if self.end_time <= self.start_time:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class ActivityUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=1000)
    location: str | None = Field(default=None, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    start_time: datetime | None = None
    end_time: datetime | None = None
    skill_level: SkillLevel | None = None
    capacity: int | None = Field(default=None, ge=2, le=100)
    gender_limit: str | None = Field(default=None, pattern=r"^(any|male|female)$")
    fee: int | None = Field(default=None, ge=0, le=1000000)


class OrganizerBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nickname: str
    campus: str
    reliability_score: int


class ParticipantOut(BaseModel):
    user_id: int
    nickname: str
    skill_level: SkillLevel
    reliability_score: int
    status: RegistrationStatus
    note: str = ""
    checked_in_at: datetime | None = None
    checkin_method: str | None = None


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    sport: str
    description: str
    campus: str
    location: str
    latitude: float | None
    longitude: float | None
    start_time: datetime
    end_time: datetime
    skill_level: SkillLevel
    capacity: int
    gender_limit: str
    fee: int
    status: ActivityStatus
    created_at: datetime

    organizer: OrganizerBrief
    joined_count: int
    slots_left: int


class ActivityDetail(ActivityOut):
    participants: list[ParticipantOut] = Field(default_factory=list)
    # Only exposed to the organiser.
    checkin_code: str | None = None


# --------------------------------------------------------------------------- #
# Registration / check-in / attendance
# --------------------------------------------------------------------------- #
class RegistrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    activity_id: int
    user_id: int
    status: RegistrationStatus
    note: str
    is_late_cancel: bool
    checked_in_at: datetime | None
    checkin_method: str | None
    checkin_distance_m: float | None
    created_at: datetime


class JoinRequest(BaseModel):
    note: str = Field(default="", max_length=200)


class CheckinRequest(BaseModel):
    """Check in either with the organiser code or by proximity."""

    code: str | None = Field(default=None, max_length=8)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def _need_one_method(self) -> "CheckinRequest":
        if not self.code and (self.latitude is None or self.longitude is None):
            raise ValueError("必须提供签到码，或同时提供经纬度")
        return self


class CheckinResult(BaseModel):
    success: bool
    method: str
    distance_m: float | None = None
    message: str
    registration: RegistrationOut


class AttendanceResult(BaseModel):
    activity_id: int
    attended: list[int] = Field(default_factory=list)
    no_show: list[int] = Field(default_factory=list)
    score_changes: dict[str, int] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Review
# --------------------------------------------------------------------------- #
class ReviewCreate(BaseModel):
    reviewee_id: int
    score: int = Field(ge=1, le=5)
    tags: list[str] = Field(default_factory=list, max_length=8)
    comment: str = Field(default="", max_length=500)


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    activity_id: int
    reviewer_id: int
    reviewee_id: int
    score: int
    tags: list[str] = Field(default_factory=list)
    comment: str
    created_at: datetime

    @field_validator("tags", mode="before")
    @classmethod
    def _split_tags(cls, v: object) -> object:
        if isinstance(v, str):
            return [t for t in (x.strip() for x in v.split(",")) if t]
        return v


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
class MatchQuery(BaseModel):
    """One-sentence request from the student, normalised by the API layer."""

    sport: str | None = None
    campus: str | None = None
    skill_level: SkillLevel | None = None
    available_from: datetime | None = None
    available_to: datetime | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    max_distance_km: float | None = Field(default=None, gt=0, le=100)
    limit: int = Field(default=10, ge=1, le=50)


class MatchReason(BaseModel):
    sport: bool = False
    time: bool = False
    skill: bool = False
    campus: bool = False
    distance_km: float | None = None
    slots_left: int = 0


class MatchItem(BaseModel):
    activity: ActivityOut
    score: float
    reason: MatchReason


class MatchResponse(BaseModel):
    query: MatchQuery
    count: int
    items: list[MatchItem]
