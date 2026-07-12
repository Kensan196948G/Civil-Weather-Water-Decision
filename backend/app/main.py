"""FastAPI エントリポイント（詳細設計 §3 / §7）。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.auth import router as auth_router
from .api.routes import router
from .core.config import settings
from .seed import init_db

logger = logging.getLogger("cwwd")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # テーブル作成＋サンプル投入（冪等）
    # 判定閾値のDB上書きを起動時に反映（#34/#35）
    from .core.db import SessionLocal
    from .services import rules as rules_service
    _db = SessionLocal()
    try:
        rules_service.apply_overrides(_db)
    finally:
        _db.close()
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


# 例外を握って 500 でも CORS ヘッダが付く応答にする（CORSMiddleware を通すため）。設計§15
# 内部情報を漏らさないため、詳細はサーバ側ログのみ・クライアントへは汎用メッセージ。
@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    logger.exception("Unhandled error: %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=500,
                        content={"code": "INTERNAL_ERROR", "message": "Internal server error"})


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


app.include_router(auth_router, prefix="/api")  # /api/auth/* は公開（ログイン）
app.include_router(router, prefix="/api")        # その他は認証必須
