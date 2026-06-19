"""FastAPI エントリポイント（詳細設計 §3 / §7）。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .core.config import settings
from .seed import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # テーブル作成＋サンプル投入（冪等）
    if settings.enable_scheduler:
        from .scheduler import start
        start()  # 定期プローブ＋予報リフレッシュ
    yield
    if settings.enable_scheduler:
        from .scheduler import stop
        await stop()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

_origins = ["*"] if settings.cors_origins.strip() == "*" else [
    o.strip() for o in settings.cors_origins.split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


app.include_router(router, prefix="/api")
