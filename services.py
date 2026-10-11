"""Domain services: auth, activities, sign-up loop, check-in, reviews, matching."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

import config
import security
from models import (
    Activity,
    ActivityStatus,
    Registration,
    RegistrationStatus,
    Review,
    SkillLevel,
    Sport,
    User,
    UserSession,
)
from db import utcnow
from schemas import MatchItem, MatchQuery, MatchReason


class ServiceError(Exception):
    """Business-rule violation. ``status_code`` maps to the HTTP response."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


def clamp_score(value: int) -> int:
    return max(config.RELIABILITY_MIN, min(config.RELIABILITY_MAX, value))


def ensure_aware_utc(value: datetime) -> datetime:
    """Normalise an incoming timestamp to timezone-aware UTC.

    A naive value is assumed to already be UTC; an aware value is converted.
    This keeps client input (which may or may not carry an offset) from ever
    being silently compared against local time.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def refresh_activity_status(activity: Activity) -> Activity:
    """Keep ``status`` consistent with time and capacity.

    Terminal states (finished / cancelled) are never overwritten.
    """
    if activity.status in (ActivityStatus.finished, ActivityStatus.cancelled):
        return activity

    now = utcnow()
    if now >= activity.end_time:
        activity.status = ActivityStatus.finished
    elif now >= activity.start_time:
        activity.status = ActivityStatus.ongoing
    elif activity.joined_count >= activity.capacity:
        activity.status = ActivityStatus.full
    else:
        activity.status = ActivityStatus.open
    return activity


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
def register_user(db: Session, payload) -> User:
    exists = db.scalar(select(User).where(User.username == payload.username))
    if exists:
        raise ServiceError("用户名已被占用", 409)

    user = User(
        username=payload.username,
        password_hash=security.hash_password(payload.password),
        nickname=payload.nickname,
        campus=payload.campus,
        phone=payload.phone,
        skill_level=payload.skill_level,
        favorite_sports=",".join(payload.favorite_sports),
        reliability_score=config.RELIABILITY_INITIAL,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, username: str, password: str) -> User:
    user = db.scalar(select(User).where(User.username == username))
    if not user or not security.verify_password(password, user.password_hash):
        raise ServiceError("用户名或密码错误", 401)
    if not user.is_active:
        raise ServiceError("账号已被禁用", 403)
    return user


def issue_token(db: Session, user: User) -> tuple[str, datetime]:
    raw = security.new_token()
    expires_at = utcnow() + timedelta(days=config.TOKEN_TTL_DAYS)
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=security.token_digest(raw),
            expires_at=expires_at,
        )
    )
    db.commit()
    return raw, expires_at


def resolve_token(db: Session, raw_token: str) -> User | None:
    session = db.scalar(
        select(UserSession).where(UserSession.token_hash == security.token_digest(raw_token))
    )
    if not session or session.expires_at <= utcnow():
        return None
    user = db.get(User, session.user_id)
    if not user or not user.is_active:
        return None
    return user


def revoke_token(db: Session, raw_token: str) -> None:
    session = db.scalar(
        select(UserSession).where(UserSession.token_hash == security.token_digest(raw_token))
    )
    if session:
        db.delete(session)
        db.commit()


# --------------------------------------------------------------------------- #
# reliability
# --------------------------------------------------------------------------- #
def apply_score(db: Session, user: User, delta: int) -> int:
    user.reliability_score = clamp_score(user.reliability_score + delta)
    return user.reliability_score


def recalculate_reliability(db: Session, user: User) -> int:
    """Recompute the score from the user's whole participation history.

    Deterministic, so it can be re-run at any time to repair drift:
        score = initial + 2*attended - 5*late_cancels - 15*no_shows
    """
    attended = (
        db.scalar(
            select(func.count(Registration.id)).where(
                Registration.user_id == user.id,
                Registration.status == RegistrationStatus.attended,
            )
        )
        or 0
    )
    no_shows = (
        db.scalar(
            select(func.count(Registration.id)).where(
                Registration.user_id == user.id,
                Registration.status == RegistrationStatus.no_show,
            )
        )
        or 0
    )
    late_cancels = (
        db.scalar(
            select(func.count(Registration.id)).where(
                Registration.user_id == user.id,
                Registration.status == RegistrationStatus.cancelled,
                Registration.is_late_cancel.is_(True),
            )
        )
        or 0
    )

    user.completed_count = attended
    user.no_show_count = no_shows
    user.reliability_score = clamp_score(
        config.RELIABILITY_INITIAL
        + config.RELIABILITY_COMPLETED_DELTA * attended
        + config.RELIABILITY_LATE_CANCEL_DELTA * late_cancels
        + config.RELIABILITY_NO_SHOW_DELTA * no_shows
    )
    db.commit()
    return user.reliability_score


# --------------------------------------------------------------------------- #
# activities
# --------------------------------------------------------------------------- #
def create_activity(db: Session, organizer: User, payload) -> Activity:
    # Store naive UTC so every comparison in this module is like-for-like.
    start_utc = ensure_aware_utc(payload.start_time).replace(tzinfo=None)
    end_utc = ensure_aware_utc(payload.end_time).replace(tzinfo=None)

    if start_utc <= utcnow():
        raise ServiceError("开始时间必须晚于当前时间")
    if end_utc <= start_utc:
        raise ServiceError("结束时间必须晚于开始时间")

    activity = Activity(
        organizer_id=organizer.id,
        title=payload.title,
        sport=payload.sport,
        description=payload.description,
        campus=payload.campus,
        location=payload.location,
        latitude=payload.latitude,
        longitude=payload.longitude,
        start_time=start_utc,
        end_time=end_utc,
        skill_level=payload.skill_level,
        capacity=payload.capacity,
        gender_limit=payload.gender_limit,
        fee=payload.fee,
        status=ActivityStatus.open,
        checkin_code=security.new_checkin_code(config.CHECKIN_CODE_LENGTH),
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def list_activities(
    db: Session,
    *,
    sport: str | None = None,
    campus: str | None = None,
    skill_level: SkillLevel | None = None,
    status: ActivityStatus | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    only_available: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[Activity]:
    stmt = select(Activity)

    if sport:
        stmt = stmt.where(Activity.sport == sport)
    if campus:
        stmt = stmt.where(Activity.campus == campus)
    if skill_level and skill_level is not SkillLevel.any:
        # "any" activities accept every level, so include them as well.
        stmt = stmt.where(
            or_(Activity.skill_level == skill_level, Activity.skill_level == SkillLevel.any)
        )
    if status:
        stmt = stmt.where(Activity.status == status)
    if date_from:
        stmt = stmt.where(Activity.start_time >= date_from)
    if date_to:
        stmt = stmt.where(Activity.start_time <= date_to)

    stmt = stmt.order_by(Activity.start_time.asc()).limit(limit).offset(offset)
    activities = list(db.scalars(stmt))
    for activity in activities:
        refresh_activity_status(activity)

    if only_available:
        activities = [
            a
            for a in activities
            if a.status in (ActivityStatus.open, ActivityStatus.ongoing) and a.slots_left > 0
        ]
    db.commit()
    return activities


def get_activity(db: Session, activity_id: int) -> Activity:
    activity = db.get(Activity, activity_id)
    if not activity:
        raise ServiceError("活动不存在", 404)
    refresh_activity_status(activity)
    db.commit()
    return activity


def update_activity(db: Session, activity: Activity, actor: User, payload) -> Activity:
    if activity.organizer_id != actor.id:
        raise ServiceError("只有发起人可以修改活动", 403)
    if activity.status in (ActivityStatus.finished, ActivityStatus.cancelled):
        raise ServiceError("已结束或已取消的活动不能修改")

    data = payload.model_dump(exclude_unset=True)
    if "capacity" in data and data["capacity"] < activity.joined_count:
        raise ServiceError(f"已有人报名 {activity.joined_count} 人，容量不能小于该数量")

    for key in ("start_time", "end_time"):
        if key in data and data[key] is not None:
            data[key] = ensure_aware_utc(data[key]).replace(tzinfo=None)

    new_start = data.get("start_time", activity.start_time)
    new_end = data.get("end_time", activity.end_time)
    if new_end <= new_start:
        raise ServiceError("结束时间必须晚于开始时间")

    for key, value in data.items():
        setattr(activity, key, value)

    refresh_activity_status(activity)
    db.commit()
    db.refresh(activity)
    return activity


def cancel_activity(db: Session, activity: Activity, actor: User) -> Activity:
    if activity.organizer_id != actor.id:
        raise ServiceError("只有发起人可以取消活动", 403)
    if activity.status is ActivityStatus.cancelled:
        raise ServiceError("活动已经取消")
    if activity.status is ActivityStatus.finished:
        raise ServiceError("已结束的活动不能取消")

    activity.status = ActivityStatus.cancelled
    for registration in activity.active_registrations:
        registration.status = RegistrationStatus.cancelled
    db.commit()
    db.refresh(activity)
    return activity


def my_activities(db: Session, user: User) -> dict[str, list[Activity]]:
    """Organised and joined activities for the '我的' page."""
    organized = list(
        db.scalars(
            select(Activity)
            .where(Activity.organizer_id == user.id)
            .order_by(Activity.start_time.desc())
        )
    )
    joined = list(
        db.scalars(
            select(Activity)
            .join(Registration, Registration.activity_id == Activity.id)
            .where(
                Registration.user_id == user.id,
                Registration.status != RegistrationStatus.cancelled,
            )
            .order_by(Activity.start_time.desc())
        )
    )
    for activity in organized + joined:
        refresh_activity_status(activity)
    db.commit()
    return {"organized": organized, "joined": joined}


# --------------------------------------------------------------------------- #
# registration loop
# --------------------------------------------------------------------------- #
def _skill_compatible(activity_level: SkillLevel, user_level: SkillLevel) -> bool:
    if activity_level is SkillLevel.any or user_level is SkillLevel.any:
        return True
    return activity_level is user_level


def join_activity(db: Session, activity: Activity, user: User, note: str = "") -> Registration:
    if activity.status is ActivityStatus.cancelled:
        raise ServiceError("活动已取消")
    if activity.status is ActivityStatus.finished:
        raise ServiceError("活动已结束")
    if utcnow() >= activity.start_time:
        raise ServiceError("活动已开始，无法报名")
    if activity.organizer_id == user.id:
        raise ServiceError("你是发起人，无需报名")

    if user.reliability_score < config.RELIABILITY_JOIN_THRESHOLD:
        raise ServiceError(
            f"信用分 {user.reliability_score} 低于 {config.RELIABILITY_JOIN_THRESHOLD}，"
            "暂时无法报名新活动，请先完成已报名的活动",
            403,
        )

    if not _skill_compatible(activity.skill_level, user.skill_level):
        raise ServiceError(
            f"该活动要求 {activity.skill_level.value} 水平，与你的水平不匹配"
        )

    existing = db.scalar(
        select(Registration).where(
            Registration.activity_id == activity.id, Registration.user_id == user.id
        )
    )
    if existing and existing.status is not RegistrationStatus.cancelled:
        raise ServiceError("你已经报名过该活动", 409)

    if activity.slots_left <= 0:
        raise ServiceError("活动已满员", 409)

    if existing:  # re-joining after cancelling
        existing.status = RegistrationStatus.registered
        existing.note = note
        existing.is_late_cancel = False
        registration = existing
    else:
        registration = Registration(
            activity_id=activity.id,
            user_id=user.id,
            status=RegistrationStatus.registered,
            note=note,
        )
        db.add(registration)

    db.flush()
    refresh_activity_status(activity)
    db.commit()
    db.refresh(registration)
    return registration


def leave_activity(db: Session, activity: Activity, user: User) -> Registration:
    registration = db.scalar(
        select(Registration).where(
            Registration.activity_id == activity.id, Registration.user_id == user.id
        )
    )
    if not registration or registration.status is RegistrationStatus.cancelled:
        raise ServiceError("你还没有报名该活动", 404)
    if registration.status in (RegistrationStatus.attended, RegistrationStatus.no_show):
        raise ServiceError("考勤已记录，无法取消")

    activity_start = activity.start_time
    within_window = utcnow() > activity_start - timedelta(
        minutes=config.LATE_CANCEL_WINDOW_MINUTES
    )
    registration.status = RegistrationStatus.cancelled
    registration.is_late_cancel = within_window
    db.flush()

    if within_window:
        apply_score(db, user, config.RELIABILITY_LATE_CANCEL_DELTA)

    refresh_activity_status(activity)
    db.commit()
    db.refresh(registration)
    return registration


def check_in(
    db: Session,
    activity: Activity,
    user: User,
    *,
    code: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> tuple[Registration, str, float | None, str]:
    registration = db.scalar(
        select(Registration).where(
            Registration.activity_id == activity.id, Registration.user_id == user.id
        )
    )
    if not registration or registration.status is RegistrationStatus.cancelled:
        raise ServiceError("你还没有报名该活动", 404)
    if registration.status in (RegistrationStatus.attended, RegistrationStatus.no_show):
        raise ServiceError("考勤已记录，无法重复签到")
    if registration.status is RegistrationStatus.checked_in:
        raise ServiceError("你已经签到过了", 409)

    distance: float | None = None
    method = ""

    if code:
        if not activity.checkin_code:
            raise ServiceError("该活动未生成签到码")
        if code.strip() != activity.checkin_code:
            raise ServiceError("签到码错误", 403)
        method = "code"
    else:
        if activity.latitude is None or activity.longitude is None:
            raise ServiceError("该活动未设置坐标，请使用签到码签到")
        assert latitude is not None and longitude is not None
        distance = haversine_m(latitude, longitude, activity.latitude, activity.longitude)
        if distance > config.CHECKIN_RADIUS_METERS:
            raise ServiceError(
                f"你距离活动地点约 {distance:.0f} 米，超出 "
                f"{config.CHECKIN_RADIUS_METERS} 米签到范围",
                403,
            )
        method = "geo"

    registration.status = RegistrationStatus.checked_in
    registration.checked_in_at = utcnow()
    registration.checkin_method = method
    registration.checkin_distance_m = distance
    db.commit()
    db.refresh(registration)

    message = "签到成功" + (f"（距离 {distance:.0f} 米）" if distance is not None else "")
    return registration, method, distance, message  # type: ignore[return-value]


def record_attendance(
    db: Session, activity: Activity, actor: User, attended_ids: list[int]
) -> dict:
    """Organiser closes the loop: who showed up, who flaked."""
    if activity.organizer_id != actor.id:
        raise ServiceError("只有发起人可以记录考勤", 403)
    if activity.status not in (
        ActivityStatus.ongoing,
        ActivityStatus.finished,
        ActivityStatus.full,
        ActivityStatus.open,
    ):
        raise ServiceError("当前状态不能记录考勤")

    registrations = {
        r.user_id: r
        for r in activity.registrations
        if r.status
        in (RegistrationStatus.registered, RegistrationStatus.checked_in)
    }

    unknown = [uid for uid in attended_ids if uid not in registrations]
    if unknown:
        raise ServiceError(f"以下用户未报名该活动: {unknown}")

    attended_set = set(attended_ids)
    result_attended: list[int] = []
    result_no_show: list[int] = []
    changes: dict[str, int] = {}

    for user_id, registration in registrations.items():
        user = db.get(User, user_id)
        assert user is not None
        before = user.reliability_score

        if user_id in attended_set:
            registration.status = RegistrationStatus.attended
            result_attended.append(user_id)
        else:
            registration.status = RegistrationStatus.no_show
            result_no_show.append(user_id)

    # Flush the status changes first: recalculate_reliability counts rows from
    # the database, so it must see the values written above.
    db.flush()

    for user_id in list(registrations):
        user = db.get(User, user_id)
        assert user is not None
        before = user.completed_count, user.no_show_count, user.reliability_score
        recalculate_reliability(db, user)  # commits
        after = user.completed_count, user.no_show_count, user.reliability_score
        if after[2] != before[2]:
            changes[str(user_id)] = after[2] - before[2]

    # Organisers who show up also earn credit, and get a registration row so
    # they can be reviewed like everybody else.
    if attended_ids:
        organizer = db.get(User, activity.organizer_id)
        if organizer:
            before = organizer.reliability_score
            organizer_registration = db.scalar(
                select(Registration).where(
                    Registration.activity_id == activity.id,
                    Registration.user_id == organizer.id,
                )
            )
            if organizer_registration is None:
                organizer_registration = Registration(
                    activity_id=activity.id,
                    user_id=organizer.id,
                    status=RegistrationStatus.attended,
                )
                db.add(organizer_registration)
            else:
                organizer_registration.status = RegistrationStatus.attended

            organizer.completed_count += 1
            apply_score(db, organizer, config.RELIABILITY_COMPLETED_DELTA)
            if organizer.reliability_score != before:
                changes[str(organizer.id)] = organizer.reliability_score - before

    activity.status = ActivityStatus.finished
    db.commit()

    return {
        "activity_id": activity.id,
        "attended": result_attended,
        "no_show": result_no_show,
        "score_changes": changes,
    }


# --------------------------------------------------------------------------- #
# reviews
# --------------------------------------------------------------------------- #
def create_review(db: Session, activity: Activity, reviewer: User, payload) -> Review:
    if activity.status is not ActivityStatus.finished:
        raise ServiceError("活动结束后才能评价")
    if payload.reviewee_id == reviewer.id:
        raise ServiceError("不能评价自己")

    def was_there(user_id: int) -> bool:
        # The organiser is present by definition and is treated as attended
        # (record_attendance also writes a registration row for them).
        if user_id == activity.organizer_id:
            return True
        registration = db.scalar(
            select(Registration).where(
                Registration.activity_id == activity.id, Registration.user_id == user_id
            )
        )
        return bool(registration and registration.status is RegistrationStatus.attended)

    if not was_there(reviewer.id):
        raise ServiceError("只有实际到场的参与者可以评价", 403)
    if not was_there(payload.reviewee_id):
        raise ServiceError("对方没有到场，无法评价", 403)

    existing = db.scalar(
        select(Review).where(
            Review.activity_id == activity.id,
            Review.reviewer_id == reviewer.id,
            Review.reviewee_id == payload.reviewee_id,
        )
    )
    if existing:
        raise ServiceError("你已经评价过对方了", 409)

    review = Review(
        activity_id=activity.id,
        reviewer_id=reviewer.id,
        reviewee_id=payload.reviewee_id,
        score=payload.score,
        tags=",".join(payload.tags),
        comment=payload.comment,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


def list_reviews_for_user(db: Session, user_id: int, limit: int = 50) -> list[Review]:
    return list(
        db.scalars(
            select(Review)
            .where(Review.reviewee_id == user_id)
            .order_by(Review.created_at.desc())
            .limit(limit)
        )
    )


def review_summary(db: Session, user_id: int) -> tuple[float | None, int]:
    row = db.execute(
        select(func.avg(Review.score), func.count(Review.id)).where(
            Review.reviewee_id == user_id
        )
    ).one()
    average, count = row
    return (round(float(average), 2) if average is not None else None), int(count or 0)


# --------------------------------------------------------------------------- #
# matching engine
# --------------------------------------------------------------------------- #
WEIGHT_SPORT = 40.0
WEIGHT_TIME = 25.0
WEIGHT_SKILL = 15.0
WEIGHT_CAMPUS = 10.0
WEIGHT_DISTANCE = 20.0


def match_activities(
    db: Session,
    query: MatchQuery,
    *,
    exclude_organizer_id: int | None = None,
    exclude_joined_user_id: int | None = None,
) -> list[MatchItem]:
    """Rank joinable activities against a structured request.

    Weights favour hard constraints (sport) and then convenience (time,
    distance), which mirrors how students actually choose a game to join.
    """
    now = utcnow()
    stmt = select(Activity).where(
        Activity.status.in_([ActivityStatus.open, ActivityStatus.full]),
        Activity.start_time > now,
    )

    # Client-supplied timestamps may carry an offset; normalise them so every
    # comparison below is naive-UTC against naive-UTC.
    if query.available_from is not None:
        query.available_from = ensure_aware_utc(query.available_from).replace(tzinfo=None)
    if query.available_to is not None:
        query.available_to = ensure_aware_utc(query.available_to).replace(tzinfo=None)

    candidates = list(db.scalars(stmt.order_by(Activity.start_time.asc()).limit(200)))

    joined_ids: set[int] = set()
    if exclude_joined_user_id is not None:
        joined_ids = set(
            db.scalars(
                select(Registration.activity_id).where(
                    Registration.user_id == exclude_joined_user_id,
                    Registration.status != RegistrationStatus.cancelled,
                )
            )
        )

    items: list[MatchItem] = []
    for activity in candidates:
        refresh_activity_status(activity)
        if activity.status not in (ActivityStatus.open, ActivityStatus.full):
            continue
        if exclude_organizer_id is not None and activity.organizer_id == exclude_organizer_id:
            continue
        if activity.id in joined_ids:
            continue

        score = 0.0
        reason = MatchReason(slots_left=activity.slots_left)

        # 1. sport (hard preference)
        if query.sport:
            if activity.sport == query.sport:
                score += WEIGHT_SPORT
                reason.sport = True
            else:
                continue  # a different sport is not a match at all
        else:
            score += WEIGHT_SPORT * 0.5

        # 2. time window overlap
        if query.available_from or query.available_to:
            window_start = query.available_from or activity.start_time
            window_end = query.available_to or activity.end_time
            if activity.start_time < window_end and activity.end_time > window_start:
                score += WEIGHT_TIME
                reason.time = True
            else:
                continue  # outside the only free slot the student has
        else:
            score += WEIGHT_TIME * 0.5

        # 3. skill level
        if query.skill_level and query.skill_level is not SkillLevel.any:
            if activity.skill_level in (query.skill_level, SkillLevel.any):
                score += WEIGHT_SKILL
                reason.skill = True
        else:
            score += WEIGHT_SKILL * 0.5

        # 4. campus
        if query.campus:
            if activity.campus == query.campus:
                score += WEIGHT_CAMPUS
                reason.campus = True

        # 5. distance
        if (
            query.latitude is not None
            and query.longitude is not None
            and activity.latitude is not None
            and activity.longitude is not None
        ):
            distance_km = (
                haversine_m(
                    query.latitude, query.longitude, activity.latitude, activity.longitude
                )
                / 1000.0
            )
            reason.distance_km = round(distance_km, 2)
            if query.max_distance_km is not None and distance_km > query.max_distance_km:
                continue
            score += WEIGHT_DISTANCE * (1.0 / (1.0 + distance_km))

        # Prefer activities that still need people, and punish near-full ones.
        if activity.slots_left > 0:
            score += 2.0
        else:
            score -= 5.0

        items.append(MatchItem(activity=activity, score=round(score, 2), reason=reason))

    items.sort(key=lambda item: (-item.score, item.activity.start_time))
    return items[: query.limit]


# --------------------------------------------------------------------------- #
# natural-language request parsing (one-sentence entry point)
# --------------------------------------------------------------------------- #
SPORT_KEYWORDS = {
    "篮球": "篮球", "打球": "篮球", "ball": "篮球",
    "羽毛球": "羽毛球",
    "足球": "足球", "踢球": "足球",
    "乒乓球": "乒乓球",
    "网球": "网球",
    "跑步": "跑步", "夜跑": "跑步", "晨跑": "跑步",
    "徒步": "徒步", "爬山": "徒步",
    "骑行": "骑行", "骑车": "骑行",
    "健身": "健身", "撸铁": "健身",
    "排球": "排球",
}
CAMPUS_KEYWORDS = ["旗山校区", "铜盘校区", "怡山校区", "泉港校区", "厦门工艺美院"]
SKILL_KEYWORDS = {
    "新手": SkillLevel.beginner,
    "入门": SkillLevel.beginner,
    "零基础": SkillLevel.beginner,
    "娱乐": SkillLevel.beginner,
    "会打": SkillLevel.intermediate,
    "一般": SkillLevel.intermediate,
    "熟练": SkillLevel.advanced,
    "竞技": SkillLevel.advanced,
    "高手": SkillLevel.advanced,
}


def parse_natural_language(text: str) -> dict:
    """Best-effort extraction of structured filters from one Chinese sentence.

    Kept deliberately simple and explainable: an LLM can replace this later
    without touching the matching engine, because the output contract is the
    ``MatchQuery`` shape.
    """
    result: dict = {}

    for keyword, sport in SPORT_KEYWORDS.items():
        if keyword in text:
            result["sport"] = sport
            break

    for campus in CAMPUS_KEYWORDS:
        if campus in text:
            result["campus"] = campus
            break

    for keyword, level in SKILL_KEYWORDS.items():
        if keyword in text:
            result["skill_level"] = level
            break

    return result
