"""FastAPI application entry point.

Run locally:
    uvicorn main:app --reload --port 8000

Interactive docs: http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
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
