"""End-to-end tests for the sports matchmaking loop.

Covers: register -> create activity -> join -> check-in -> attendance -> review,
plus the anti-flake reliability rules and the matching engine.
"""

from __future__ import annotations

from conftest import future_iso


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
def test_register_login_and_me(client):
    response = client.post(
        "/auth/register",
        json={
            "username": "alice",
            "password": "secret123",
            "nickname": "小爱",
            "campus": "旗山校区",
            "skill_level": "intermediate",
            "favorite_sports": ["篮球", "跑步"],
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    assert response.json()["token_type"] == "bearer"

    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/me", headers=headers)
    assert me.status_code == 200
    body = me.json()
    assert body["nickname"] == "小爱"
    assert body["favorite_sports"] == ["篮球", "跑步"]
    assert body["reliability_score"] == 100
    assert body["review_count"] == 0

    login = client.post("/auth/login", json={"username": "alice", "password": "secret123"})
    assert login.status_code == 200

    bad = client.post("/auth/login", json={"username": "alice", "password": "wrong-pass"})
    assert bad.status_code == 401

    duplicate = client.post(
        "/auth/register",
        json={"username": "alice", "password": "secret123", "nickname": "重复"},
    )
    assert duplicate.status_code == 409


def test_auth_is_required_without_token(client):
    assert client.get("/me").status_code == 401
    assert client.get("/activities/mine").status_code == 401
    assert client.get("/match").status_code == 401
    assert client.get("/health").status_code == 200
    assert client.get("/sports").status_code == 200


def test_token_is_revoked_after_logout(client, register):
    headers, _ = register("bob")
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/me", headers=headers).status_code == 401


# --------------------------------------------------------------------------- #
# activity lifecycle
# --------------------------------------------------------------------------- #
def test_create_activity_generates_checkin_code_only_for_organizer(client, register):
    organizer_headers, organizer = register("organizer")
    other_headers, _ = register("other")

    created = client.post(
        "/activities",
        headers=organizer_headers,
        json={
            "title": "周三晚篮球",
            "sport": "篮球",
            "campus": "旗山校区",
            "location": "风雨操场 3 号场",
            "latitude": 26.0565,
            "longitude": 119.1966,
            "start_time": future_iso(24),
            "end_time": future_iso(26),
            "skill_level": "any",
            "capacity": 6,
        },
    )
    assert created.status_code == 201, created.text
    activity = created.json()
    assert activity["status"] == "open"
    assert activity["joined_count"] == 0
    assert activity["slots_left"] == 6

    # Organiser sees the code ...
    as_organizer = client.get(f"/activities/{activity['id']}", headers=organizer_headers)
    assert as_organizer.status_code == 200
    code = as_organizer.json()["checkin_code"]
    assert code and len(code) == 4

    # ... other users do not.
    as_other = client.get(f"/activities/{activity['id']}", headers=other_headers)
    assert as_other.json()["checkin_code"] is None

    # Anonymous viewers see no code either.
    anonymous = client.get(f"/activities/{activity['id']}")
    assert anonymous.status_code == 200
    assert anonymous.json()["checkin_code"] is None

    assert activity["organizer"]["id"] == organizer["id"]


def test_activity_time_window_is_validated(client, register):
    headers, _ = register("validator")
    bad = client.post(
        "/activities",
        headers=headers,
        json={
            "title": "时间反了",
            "sport": "篮球",
            "start_time": future_iso(5),
            "end_time": future_iso(3),
        },
    )
    assert bad.status_code == 422  # pydantic model validator

    past = client.post(
        "/activities",
        headers=headers,
        json={
            "title": "已经开始了",
            "sport": "篮球",
            "start_time": future_iso(-3),
            "end_time": future_iso(-1),
        },
    )
    assert past.status_code == 400
    assert "开始时间" in past.json()["detail"]


# --------------------------------------------------------------------------- #
# join / leave
# --------------------------------------------------------------------------- #
def test_join_full_flow_and_duplicate_join(client, register, make_activity):
    organizer_headers, _ = register("org2")
    joiner_headers, joiner = register("joiner")
    activity = make_activity(organizer_headers, capacity=2)

    joined = client.post(f"/activities/{activity['id']}/join", headers=joiner_headers,
                         json={"note": "第一次来，多担待"})
    assert joined.status_code == 201, joined.text
    assert joined.json()["status"] == "registered"

    detail = client.get(f"/activities/{activity['id']}")
    assert detail.json()["joined_count"] == 1
    assert detail.json()["slots_left"] == 1
    assert [p["nickname"] for p in detail.json()["participants"]] == ["joiner-昵称"]

    again = client.post(f"/activities/{activity['id']}/join", headers=joiner_headers)
    assert again.status_code == 409


def test_organizer_cannot_join_own_activity(client, register, make_activity):
    headers, _ = register("solo")
    activity = make_activity(headers)
    response = client.post(f"/activities/{activity['id']}/join", headers=headers)
    assert response.status_code == 400
    assert "发起人" in response.json()["detail"]


def test_activity_becomes_full_at_capacity(client, register, make_activity):
    organizer_headers, _ = register("org3")
    activity = make_activity(organizer_headers, capacity=2)

    for name in ("player1", "player2"):
        headers, _ = register(name)
        assert client.post(f"/activities/{activity['id']}/join", headers=headers).status_code == 201

    late_headers, _ = register("player3")
    rejected = client.post(f"/activities/{activity['id']}/join", headers=late_headers)
    assert rejected.status_code == 409
    assert "满员" in rejected.json()["detail"]

    assert client.get(f"/activities/{activity['id']}").json()["status"] == "full"


def test_cancelling_before_deadline_does_not_hurt_score(client, register, make_activity):
    organizer_headers, _ = register("org4")
    joiner_headers, joiner = register("early_leaver")
    activity = make_activity(organizer_headers, hours=48)

    client.post(f"/activities/{activity['id']}/join", headers=joiner_headers)
    left = client.delete(f"/activities/{activity['id']}/join", headers=joiner_headers)
    assert left.status_code == 200
    assert left.json()["status"] == "cancelled"
    assert left.json()["is_late_cancel"] is False

    me = client.get("/me", headers=joiner_headers).json()
    assert me["reliability_score"] == 100


def test_late_cancel_costs_reliability(client, register, make_activity):
    organizer_headers, _ = register("org5")
    joiner_headers, _ = register("late_leaver")
    # Starts in 30 minutes: inside the late-cancellation window.
    activity = make_activity(organizer_headers, hours=0.5, duration_hours=1)

    client.post(f"/activities/{activity['id']}/join", headers=joiner_headers)
    left = client.delete(f"/activities/{activity['id']}/join", headers=joiner_headers)
    assert left.json()["is_late_cancel"] is True

    me = client.get("/me", headers=joiner_headers).json()
    assert me["reliability_score"] == 95  # 100 - 5


# --------------------------------------------------------------------------- #
# check-in
# --------------------------------------------------------------------------- #
def test_checkin_with_organizer_code(client, register, make_activity):
    organizer_headers, _ = register("org6")
    joiner_headers, _ = register("checker")
    activity = make_activity(organizer_headers)

    client.post(f"/activities/{activity['id']}/join", headers=joiner_headers)
    code = client.get(f"/activities/{activity['id']}", headers=organizer_headers).json()["checkin_code"]

    wrong = client.post(
        f"/activities/{activity['id']}/checkin", headers=joiner_headers, json={"code": "0000"}
    )
    assert wrong.status_code in (400, 403)

    ok = client.post(
        f"/activities/{activity['id']}/checkin", headers=joiner_headers, json={"code": code}
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["success"] is True
    assert body["method"] == "code"
    assert body["registration"]["status"] == "checked_in"

    duplicate = client.post(
        f"/activities/{activity['id']}/checkin", headers=joiner_headers, json={"code": code}
    )
    assert duplicate.status_code == 409


def test_checkin_requires_proximity_or_code(client, register, make_activity):
    organizer_headers, _ = register("org7")
    joiner_headers, _ = register("far_away")
    activity = make_activity(organizer_headers, latitude=26.0565, longitude=119.1966)
    client.post(f"/activities/{activity['id']}/join", headers=joiner_headers)

    # ~1.5 km away: outside the 300 m fence.
    too_far = client.post(
        f"/activities/{activity['id']}/checkin",
        headers=joiner_headers,
        json={"latitude": 26.0700, "longitude": 119.1966},
    )
    assert too_far.status_code == 403
    assert "超出" in too_far.json()["detail"]

    # Standing on the court.
    ok = client.post(
        f"/activities/{activity['id']}/checkin",
        headers=joiner_headers,
        json={"latitude": 26.0566, "longitude": 119.1967},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["method"] == "geo"
    assert ok.json()["distance_m"] is not None

    # Neither code nor coordinates is a validation error.
    neither = client.post(
        f"/activities/{activity['id']}/checkin", headers=joiner_headers, json={}
    )
    assert neither.status_code == 422


# --------------------------------------------------------------------------- #
# attendance, reviews, reliability
# --------------------------------------------------------------------------- #
def _finished_activity_with_two_players(client, register, make_activity, prefix: str):
    """Helper: organiser + 2 joiners, organiser reports attendance."""
    organizer_headers, organizer = register(f"{prefix}_org")
    a_headers, a = register(f"{prefix}_a")
    b_headers, b = register(f"{prefix}_b")
    activity = make_activity(organizer_headers, capacity=4)

    for headers in (a_headers, b_headers):
        assert client.post(f"/activities/{activity['id']}/join", headers=headers).status_code == 201

    # a shows up, b flakes.
    attendance = client.post(
        f"/activities/{activity['id']}/attendance",
        headers=organizer_headers,
        json=[a["id"]],
    )
    assert attendance.status_code == 200, attendance.text
    return organizer_headers, organizer, a_headers, a, b_headers, b, activity, attendance.json()


def test_attendance_marks_no_show_and_recalculates_scores(client, register, make_activity):
    (
        organizer_headers,
        organizer,
        a_headers,
        a,
        b_headers,
        b,
        activity,
        result,
    ) = _finished_activity_with_two_players(client, register, make_activity, "att")

    assert result["attended"] == [a["id"]]
    assert result["no_show"] == [b["id"]]

    assert client.get("/me", headers=a_headers).json()["reliability_score"] == 100
    b_me = client.get("/me", headers=b_headers).json()
    assert b_me["reliability_score"] == 85  # 100 - 15
    assert b_me["no_show_count"] == 1
    assert b_me["completed_count"] == 0

    organizer_me = client.get("/me", headers=organizer_headers).json()
    assert organizer_me["completed_count"] == 1


def test_only_organizer_can_record_attendance(client, register, make_activity):
    organizer_headers, _ = register("att_org2")
    joiner_headers, joiner = register("att_joiner2")
    activity = make_activity(organizer_headers)
    client.post(f"/activities/{activity['id']}/join", headers=joiner_headers)

    forbidden = client.post(
        f"/activities/{activity['id']}/attendance", headers=joiner_headers, json=[joiner["id"]]
    )
    assert forbidden.status_code == 403
    assert "发起人" in forbidden.json()["detail"]


def test_review_closes_the_loop(client, register, make_activity):
    (
        organizer_headers,
        organizer,
        a_headers,
        a,
        b_headers,
        b,
        activity,
        result,
    ) = _finished_activity_with_two_players(client, register, make_activity, "rev")

    # Attending player A reviews the organiser.
    ok = client.post(
        f"/activities/{activity['id']}/reviews",
        headers=a_headers,
        json={
            "reviewee_id": organizer["id"],
            "score": 5,
            "tags": ["准时", "好相处"],
            "comment": "组织得很到位",
        },
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["tags"] == ["准时", "好相处"]

    duplicate = client.post(
        f"/activities/{activity['id']}/reviews",
        headers=a_headers,
        json={"reviewee_id": organizer["id"], "score": 1},
    )
    assert duplicate.status_code == 409

    # The no-show player never attended, so cannot review.
    cannot = client.post(
        f"/activities/{activity['id']}/reviews",
        headers=b_headers,
        json={"reviewee_id": organizer["id"], "score": 5},
    )
    assert cannot.status_code == 403

    profile = client.get(f"/users/{organizer['id']}").json()
    assert profile["review_count"] == 1
    assert profile["review_average"] == 5.0


def test_review_requires_finished_activity(client, register, make_activity):
    organizer_headers, organizer = register("rev_org2")
    joiner_headers, joiner = register("rev_joiner2")
    activity = make_activity(organizer_headers)
    client.post(f"/activities/{activity['id']}/join", headers=joiner_headers)

    response = client.post(
        f"/activities/{activity['id']}/reviews",
        headers=joiner_headers,
        json={"reviewee_id": organizer["id"], "score": 5},
    )
    assert response.status_code == 400
    assert "结束" in response.json()["detail"]


def test_low_reliability_blocks_joining_new_activities(client, register, make_activity):
    organizer_headers, _ = register("block_org")
    flaky_headers, flaky = register("flaky_user")

    # Two no-shows: 100 -> 85 -> 70, still above the threshold.
    for index in range(2):
        activity = make_activity(organizer_headers, title=f"局 {index}")
        client.post(f"/activities/{activity['id']}/join", headers=flaky_headers)
        client.post(
            f"/activities/{activity['id']}/attendance", headers=organizer_headers, json=[]
        )
    assert client.get("/me", headers=flaky_headers).json()["reliability_score"] == 70

    # A third no-show drops below 60 and locks the account out of new sign-ups.
    activity = make_activity(organizer_headers, title="第三局")
    client.post(f"/activities/{activity['id']}/join", headers=flaky_headers)
    client.post(f"/activities/{activity['id']}/attendance", headers=organizer_headers, json=[])
    assert client.get("/me", headers=flaky_headers).json()["reliability_score"] == 55

    fresh = make_activity(organizer_headers, title="新局")
    blocked = client.post(f"/activities/{fresh['id']}/join", headers=flaky_headers)
    assert blocked.status_code == 403
    assert "信用分" in blocked.json()["detail"]


# --------------------------------------------------------------------------- #
# cancel activity
# --------------------------------------------------------------------------- #
def test_organizer_cancelling_activity_frees_participants(client, register, make_activity):
    organizer_headers, _ = register("cancel_org")
    joiner_headers, _ = register("cancel_joiner")
    activity = make_activity(organizer_headers)
    client.post(f"/activities/{activity['id']}/join", headers=joiner_headers)

    cancelled = client.post(f"/activities/{activity['id']}/cancel", headers=organizer_headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    detail = client.get(f"/activities/{activity['id']}").json()
    assert detail["participants"][0]["status"] == "cancelled"
    assert detail["slots_left"] == detail["capacity"]


def test_non_organizer_cannot_cancel(client, register, make_activity):
    organizer_headers, _ = register("cancel_org2")
    other_headers, _ = register("cancel_other")
    activity = make_activity(organizer_headers)

    response = client.post(f"/activities/{activity['id']}/cancel", headers=other_headers)
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# listing / filtering
# --------------------------------------------------------------------------- #
def test_activity_listing_filters(client, register, make_activity):
    headers, _ = register("lister")
    make_activity(headers, sport="篮球", title="篮球局", campus="旗山校区")
    make_activity(headers, sport="羽毛球", title="羽毛球局", campus="铜盘校区")

    all_items = client.get("/activities").json()
    assert len(all_items) == 2

    only_badminton = client.get("/activities", params={"sport": "羽毛球"}).json()
    assert [a["title"] for a in only_badminton] == ["羽毛球局"]

    only_campus = client.get("/activities", params={"campus": "铜盘校区"}).json()
    assert [a["title"] for a in only_campus] == ["羽毛球局"]

    mine = client.get("/activities/mine", headers=headers).json()
    assert len(mine["organized"]) == 2
    assert mine["joined"] == []


# --------------------------------------------------------------------------- #
# matching engine
# --------------------------------------------------------------------------- #
def test_match_ranks_by_skill_time_and_distance(client, register, make_activity):
    organizer_headers, _ = register("match_org")
    # Perfect candidate: same sport, beginner friendly, close by.
    make_activity(
        organizer_headers,
        sport="篮球",
        title="新手友好局",
        skill_level="beginner",
        latitude=26.0565,
        longitude=119.1966,
    )
    # Wrong sport: must be excluded entirely.
    make_activity(organizer_headers, sport="羽毛球", title="羽毛球局")

    seeker_headers, _ = register("seeker", skill_level="beginner")
    response = client.get(
        "/match",
        headers=seeker_headers,
        params={
            "sport": "篮球",
            "skill_level": "beginner",
            "campus": "旗山校区",
            "latitude": 26.0565,
            "longitude": 119.1966,
            "max_distance_km": 5,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["activity"]["title"] == "新手友好局"
    assert item["reason"]["sport"] is True
    assert item["reason"]["skill"] is True
    assert item["reason"]["distance_km"] is not None


def test_match_respects_available_time_window(client, register, make_activity):
    organizer_headers, _ = register("match_org2")
    make_activity(organizer_headers, sport="篮球", title="明晚的局", hours=24)

    seeker_headers, _ = register("busy_seeker")
    # Only free tonight: the 24h-from-now activity must be filtered out.
    response = client.get(
        "/match",
        headers=seeker_headers,
        params={
            "sport": "篮球",
            "available_from": future_iso(1),
            "available_to": future_iso(3),
        },
    )
    assert response.json()["count"] == 0


def test_match_excludes_own_and_already_joined(client, register, make_activity):
    organizer_headers, _ = register("match_org3")
    joiner_headers, _ = register("match_joiner")
    make_activity(organizer_headers, sport="篮球", title="别人的局")
    joined = make_activity(organizer_headers, sport="篮球", title="我已报名的局")
    client.post(f"/activities/{joined['id']}/join", headers=joiner_headers)

    response = client.get("/match", headers=joiner_headers, params={"sport": "篮球"})
    titles = [item["activity"]["title"] for item in response.json()["items"]]
    assert titles == ["别人的局"]

    # The organiser never sees their own activity as a match.
    own = client.get("/match", headers=organizer_headers, params={"sport": "篮球"})
    assert own.json()["count"] == 0


def test_match_by_sentence_parses_chinese(client, register, make_activity):
    organizer_headers, _ = register("nlp_org")
    make_activity(organizer_headers, sport="篮球", title="篮球新手局", skill_level="beginner")
    make_activity(organizer_headers, sport="羽毛球", title="羽毛球局", skill_level="beginner")

    seeker_headers, _ = register("nlp_seeker")
    response = client.get(
        "/match/parse", headers=seeker_headers, params={"q": "今晚想在旗山校区打篮球，新手可以吗"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["query"]["sport"] == "篮球"
    assert body["query"]["campus"] == "旗山校区"
    assert body["query"]["skill_level"] == "beginner"
    assert [i["activity"]["title"] for i in body["items"]] == ["篮球新手局"]
