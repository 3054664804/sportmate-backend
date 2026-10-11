"""Maps ORM objects onto response schemas, including per-viewer field visibility."""

from __future__ import annotations

from models import Activity, Registration, User
from schemas import (
    ActivityDetail,
    ActivityOut,
    OrganizerBrief,
    ParticipantOut,
    RegistrationOut,
)


def registration_out(registration: Registration) -> RegistrationOut:
    return RegistrationOut.model_validate(registration)


def activity_out(activity: Activity) -> ActivityOut:
    return ActivityOut(
        id=activity.id,
        title=activity.title,
        sport=activity.sport,
        description=activity.description,
        campus=activity.campus,
        location=activity.location,
        latitude=activity.latitude,
        longitude=activity.longitude,
        start_time=activity.start_time,
        end_time=activity.end_time,
        skill_level=activity.skill_level,
        capacity=activity.capacity,
        gender_limit=activity.gender_limit,
        fee=activity.fee,
        status=activity.status,
        created_at=activity.created_at,
        organizer=OrganizerBrief.model_validate(activity.organizer),
        joined_count=activity.joined_count,
        slots_left=activity.slots_left,
    )


def activity_detail(activity: Activity, viewer: User | None) -> ActivityDetail:
    base = activity_out(activity)
    participants = [
        ParticipantOut(
            user_id=registration.user_id,
            nickname=registration.user.nickname,
            skill_level=registration.user.skill_level,
            reliability_score=registration.user.reliability_score,
            status=registration.status,
            note=registration.note,
            checked_in_at=registration.checked_in_at,
            checkin_method=registration.checkin_method,
        )
        for registration in activity.registrations
    ]
    # The check-in code is a secret shared by the organiser only.
    is_organizer = viewer is not None and viewer.id == activity.organizer_id
    return ActivityDetail(
        **base.model_dump(),
        participants=participants,
        checkin_code=activity.checkin_code if is_organizer else None,
    )
