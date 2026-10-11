"""FastAPI application entry point.

Run locally:
    uvicorn main:app --reload --port 8000

Interactive docs: http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import config
import db
import services
from routers import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(
    title=config.APP_NAME,
    version=config.APP_VERSION,
    description=(
        "面向福大学生的运动组局平台后端。\n\n"
        "核心闭环：发起活动 → 报名 → 签到 → 考勤 → 评价 → 信用分。\n"
        "鉴权方式：登录返回不透明令牌，后续请求带 `Authorization: Bearer <token>`。"
    ),
    lifespan=lifespan,
)

# The campus pilot runs on a separate frontend origin during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(services.ServiceError)
async def service_error_handler(request: Request, exc: services.ServiceError) -> JSONResponse:
    """Turn business-rule violations into clean JSON instead of 500s."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "error": "service_error"},
    )


# Chinese labels for the pydantic error types a frontend is most likely to hit.
_FIELD_LABELS = {
    "username": "用户名",
    "password": "密码",
    "nickname": "昵称",
    "campus": "校区",
    "title": "活动标题",
    "sport": "运动项目",
    "capacity": "人数上限",
    "start_time": "开始时间",
    "end_time": "结束时间",
    "score": "评分",
    "code": "签到码",
}


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Normalise 422 responses so ``detail`` is always a plain string.

    FastAPI's default is a list of error objects, which forces every frontend
    to special-case it (``err.detail`` being an array on 422 but a string on
    every other error). Flattening it here keeps the error contract uniform:

        {"detail": "用户名 至少 3 个字符", "error": "validation_error"}
    """
    problems: list[str] = []
    for err in exc.errors()[:5]:  # cap the message so it stays readable
        location = [str(part) for part in err.get("loc", []) if part != "body"]
        field = location[-1] if location else ""
        label = _FIELD_LABELS.get(field, field or "请求参数")
        message = err.get("msg", "格式不正确")
        message = message.replace("String should have at least", "长度至少为")
        message = message.replace("String should have at most", "长度最多为")
        message = message.replace("Field required", "缺少必填项")
        message = message.replace("Input should be a valid", "格式不正确:")
        message = message.replace(" characters", " 个字符")
        message = message.replace("character", " 个字符")
        problems.append(f"{label} {message}" if label not in message else message)

    detail = "；".join(problems) if problems else "请求参数校验失败"
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": detail, "error": "validation_error"},
    )


app.include_router(router)


@app.get("/", tags=["基础数据"], summary="服务信息与全部接口清单")
def root() -> dict:
    return {
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "docs": "/docs",
        "endpoints": {
            "认证": [
                "POST /auth/register",
                "POST /auth/login",
                "POST /auth/logout",
                "GET  /me",
                "PATCH /me",
            ],
            "基础数据": ["GET /sports", "GET /health"],
            "用户": ["GET /users/{id}", "GET /users/{id}/reviews"],
            "活动": [
                "POST /activities",
                "GET  /activities",
                "GET  /activities/mine",
                "GET  /activities/{id}",
                "PATCH /activities/{id}",
                "POST /activities/{id}/cancel",
            ],
            "报名": [
                "POST /activities/{id}/join",
                "DELETE /activities/{id}/join",
            ],
            "签到与考勤": [
                "POST /activities/{id}/checkin",
                "POST /activities/{id}/attendance",
            ],
            "评价": ["POST /activities/{id}/reviews"],
            "匹配": ["GET /match", "GET /match/parse?q=今晚想打篮球,新手"],
        },
        "reliability_rules": {
            "初始分": config.RELIABILITY_INITIAL,
            "完成一次活动": f"+{config.RELIABILITY_COMPLETED_DELTA}",
            "临近开始才取消": f"{config.RELIABILITY_LATE_CANCEL_DELTA}",
            "爽约": f"{config.RELIABILITY_NO_SHOW_DELTA}",
            "低于该分数禁止报名": config.RELIABILITY_JOIN_THRESHOLD,
        },
    }


@app.get("/health", tags=["基础数据"], summary="健康检查")
def health() -> dict:
    return {"status": "ok", "app": config.APP_NAME, "version": config.APP_VERSION}
