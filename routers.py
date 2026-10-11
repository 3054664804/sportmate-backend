"""All HTTP routes for the campus sports matchmaking platform."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

import converters
import services
from deps import extract_token, get_current_user
from models import Activity, ActivityStatus, RegistrationStatus, SkillLevel, Sport, User
from schemas import (
    ActivityCreate,
    ActivityDetail,
    ActivityOut,
    ActivityUpdate,
    AttendanceResult,
    CheckinRequest,
    CheckinResult,
    JoinRequest,
    MatchItem,
    MatchQuery,
    MatchReason,
    MatchResponse,
    RegistrationOut,
    ReviewCreate,
    ReviewOut,
    SportOut,
    TokenOut,
    UserLogin,
    UserOut,
    UserProfile,
    UserRegister,
    UserUpdate,
)
from db import get_db

router = APIRouter()


# =========================================================================== #
# auth & profile
# =========================================================================== #
@router.post("/auth/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED,
             summary="注册并直接获得登录令牌", tags=["认证"])
def register(payload: UserRegister, db: Session = Depends(get_db)) -> TokenOut:
    user = services.register_user(db, payload)
    token, expires_at = services.issue_token(db, user)
    return TokenOut(access_token=token, expires_at=expires_at)


@router.post("/auth/login", response_model=TokenOut, summary="账号密码登录", tags=["认证"])
def login(payload: UserLogin, db: Session = Depends(get_db)) -> TokenOut:
    user = services.authenticate(db, payload.username, payload.password)
    token, expires_at = services.issue_token(db, user)
    return TokenOut(access_token=token, expires_at=expires_at)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT,
             response_class=Response,
             summary="退出登录（吊销当前令牌）", tags=["认证"])
def logout(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Response:
    token = extract_token(authorization)
    if token:
        services.revoke_token(db, token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserProfile, summary="我的资料（含评价汇总）", tags=["认证"])
def read_me(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> UserProfile:
    average, count = services.review_summary(db, user.id)
    profile = UserProfile.model_validate(user)
    profile.review_average = average
    profile.review_count = count
    return profile


@router.patch("/me", response_model=UserOut, summary="修改我的资料", tags=["认证"])
def update_me(
    payload: UserUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> User:
    data = payload.model_dump(exclude_unset=True)
    if "favorite_sports" in data and data["favorite_sports"] is not None:
        data["favorite_sports"] = ",".join(data["favorite_sports"])
    for key, value in data.items():
        if value is not None:
            setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


@router.get("/users/{user_id}", response_model=UserProfile, summary="查看他人公开资料", tags=["用户"])
def read_user(user_id: int, db: Session = Depends(get_db)) -> UserProfile:
    user = db.get(User, user_id)
    if not user:
        raise services.ServiceError("用户不存在", 404)
    average, count = services.review_summary(db, user.id)
    profile = UserProfile.model_validate(user)
    profile.review_average = average
    profile.review_count = count
    return profile


@router.get("/users/{user_id}/reviews", response_model=list[ReviewOut],
            summary="查看某人的评价", tags=["用户"])
def read_user_reviews(
    user_id: int, limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)
) -> list[ReviewOut]:
    reviews = services.list_reviews_for_user(db, user_id, limit=limit)
    return [ReviewOut.model_validate(r) for r in reviews]


# =========================================================================== #
# sports
# =========================================================================== #
@router.get("/sports", response_model=list[SportOut], summary="运动项目列表", tags=["基础数据"])
def list_sports(db: Session = Depends(get_db)) -> list[SportOut]:
    sports = db.scalars(select(Sport).where(Sport.is_active.is_(True)).order_by(Sport.id)).all()
    return [SportOut.model_validate(s) for s in sports]


# =========================================================================== #
# activities
# =========================================================================== #
@router.post("/activities", response_model=ActivityOut, status_code=status.HTTP_201_CREATED,
             summary="发起活动（自动生成签到码）", tags=["活动"])
def create_activity(
    payload: ActivityCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ActivityOut:
    activity = services.create_activity(db, user, payload)
    return converters.activity_out(activity)


@router.get("/activities", response_model=list[ActivityOut], summary="活动列表（支持筛选）",
            tags=["活动"])
def list_activities(
    sport: str | None = None,
    campus: str | None = None,
    skill_level: SkillLevel | None = None,
    activity_status: ActivityStatus | None = Query(default=None, alias="status"),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    only_available: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[ActivityOut]:
    activities = services.list_activities(
        db,
        sport=sport,
        campus=campus,
        skill_level=skill_level,
        status=activity_status,
        date_from=date_from,
        date_to=date_to,
        only_available=only_available,
        limit=limit,
        offset=offset,
    )
    return [converters.activity_out(a) for a in activities]


# NOTE: this literal route must be declared before /activities/{activity_id},
# otherwise "mine" would be parsed as an activity id.
@router.get("/activities/mine", summary="我发起的 / 我报名的活动", tags=["活动"])
def my_activities(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    grouped = services.my_activities(db, user)
    return {
        "organized": [converters.activity_out(a) for a in grouped["organized"]],
        "joined": [converters.activity_out(a) for a in grouped["joined"]],
    }


@router.get("/activities/{activity_id}", response_model=ActivityDetail,
            summary="活动详情（报名名单；发起人可见签到码）", tags=["活动"])
def read_activity(
    activity_id: int,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> ActivityDetail:
    activity = services.get_activity(db, activity_id)
    token = extract_token(authorization)
    viewer = services.resolve_token(db, token) if token else None
    return converters.activity_detail(activity, viewer)


@router.patch("/activities/{activity_id}", response_model=ActivityOut,
              summary="修改活动（仅发起人）", tags=["活动"])
def patch_activity(
    activity_id: int,
    payload: ActivityUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ActivityOut:
    activity = services.get_activity(db, activity_id)
    updated = services.update_activity(db, activity, user, payload)
    return converters.activity_out(updated)


@router.post("/activities/{activity_id}/cancel", response_model=ActivityOut,
             summary="取消活动（仅发起人，通知全部报名者）", tags=["活动"])
def cancel_activity(
    activity_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ActivityOut:
    activity = services.get_activity(db, activity_id)
    cancelled = services.cancel_activity(db, activity, user)
    return converters.activity_out(cancelled)


# =========================================================================== #
# join / leave
# =========================================================================== #
@router.post("/activities/{activity_id}/join", response_model=RegistrationOut,
             status_code=status.HTTP_201_CREATED, summary="报名参加活动", tags=["报名"])
def join_activity(
    activity_id: int,
    payload: JoinRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RegistrationOut:
    activity = services.get_activity(db, activity_id)
    note = payload.note if payload else ""
    registration = services.join_activity(db, activity, user, note=note)
    return converters.registration_out(registration)


@router.delete("/activities/{activity_id}/join", response_model=RegistrationOut,
               summary="取消报名（临近开始会扣信用分）", tags=["报名"])
def leave_activity(
    activity_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RegistrationOut:
    activity = services.get_activity(db, activity_id)
    registration = services.leave_activity(db, activity, user)
    return converters.registration_out(registration)


# =========================================================================== #
# check-in
# =========================================================================== #
@router.post("/activities/{activity_id}/checkin", response_model=CheckinResult,
             summary="签到（签到码 或 定位围栏）", tags=["签到"])
def checkin(
    activity_id: int,
    payload: CheckinRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CheckinResult:
    activity = services.get_activity(db, activity_id)
    registration, method, distance, message = services.check_in(
        db,
        activity,
        user,
        code=payload.code,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    return CheckinResult(
        success=True,
        method=method,
        distance_m=round(distance, 1) if distance is not None else None,
        message=message,
        registration=converters.registration_out(registration),
    )


# =========================================================================== #
# attendance (organiser closes the loop)
# =========================================================================== #
@router.post("/activities/{activity_id}/attendance", response_model=AttendanceResult,
             summary="记录考勤：确认到场者，其余自动记为爽约并扣分", tags=["考勤"])
def record_attendance(
    activity_id: int,
    attended_ids: list[int],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AttendanceResult:
    activity = services.get_activity(db, activity_id)
    result = services.record_attendance(db, activity, user, attended_ids)
    return AttendanceResult(**result)


# =========================================================================== #
# reviews
# =========================================================================== #
@router.post("/activities/{activity_id}/reviews", response_model=ReviewOut,
             status_code=status.HTTP_201_CREATED,
             summary="活动结束后评价同场伙伴（仅到场者可评）", tags=["评价"])
def create_review(
    activity_id: int,
    payload: ReviewCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReviewOut:
    activity = services.get_activity(db, activity_id)
    review = services.create_review(db, activity, user, payload)
    return ReviewOut.model_validate(review)


# =========================================================================== #
# matching
# =========================================================================== #
@router.get("/match", response_model=MatchResponse,
            summary="结构化匹配：按项目/时间/水平/地点/距离排序", tags=["匹配"])
def match(
    sport: str | None = None,
    campus: str | None = None,
    skill_level: SkillLevel | None = None,
    available_from: datetime | None = None,
    available_to: datetime | None = None,
    latitude: float | None = Query(default=None, ge=-90, le=90),
    longitude: float | None = Query(default=None, ge=-180, le=180),
    max_distance_km: float | None = Query(default=None, gt=0, le=100),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MatchResponse:
    query = MatchQuery(
        sport=sport,
        campus=campus,
        skill_level=skill_level,
        available_from=available_from,
        available_to=available_to,
        latitude=latitude,
        longitude=longitude,
        max_distance_km=max_distance_km,
        limit=limit,
    )
    items = services.match_activities(
        db,
        query,
        exclude_organizer_id=user.id,
        exclude_joined_user_id=user.id,
    )
    return MatchResponse(query=query, count=len(items), items=items)


@router.get("/match/parse", response_model=MatchResponse,
            summary="一句话找局：自然语言解析后直接返回匹配结果", tags=["匹配"])
def match_by_sentence(
    q: str = Query(min_length=2, max_length=120, description="例如：今晚想打篮球，新手可以吗"),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MatchResponse:
    parsed = services.parse_natural_language(q)
    query = MatchQuery(limit=limit, **parsed)

    items = services.match_activities(
        db,
        query,
        exclude_organizer_id=user.id,
        exclude_joined_user_id=user.id,
    )

    # If the sentence matched nothing, fall back to the student's own profile
    # so the endpoint never returns a dead end.
    if not items and (not query.sport or not query.campus):
        fallback = MatchQuery(
            sport=query.sport or (user.favorite_sports.split(",")[0] if user.favorite_sports else None),
            campus=query.campus or user.campus,
            skill_level=query.skill_level or user.skill_level,
            limit=limit,
        )
        items = services.match_activities(
            db,
            fallback,
            exclude_organizer_id=user.id,
            exclude_joined_user_id=user.id,
        )
        query = fallback

    return MatchResponse(query=query, count=len(items), items=items)
