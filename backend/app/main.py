"""FastAPI エントリポイント（詳細設計 §3 / §7）。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.auth import router as auth_router
from .api.routes import router
from .core.config import settings
from .core import readiness
from .seed import init_db

logger = logging.getLogger("cwwd")

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Strict-Transport-Security": "max-age=31536000",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    "X-Permitted-Cross-Domain-Policies": "none",
    "X-Download-Options": "noopen",
    "Cache-Control": "no-store",
}
_API_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'none'"
)
_DOC_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}


def _api_docs_enabled() -> bool:
    return settings.app_env == "local"


def _apply_security_headers(response, path: str = ""):
    for key, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    if not (_api_docs_enabled() and path in _DOC_PATHS):
        response.headers.setdefault("Content-Security-Policy", _API_CONTENT_SECURITY_POLICY)
    return response


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


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _api_docs_enabled() else None,
    redoc_url="/redoc" if _api_docs_enabled() else None,
    openapi_url="/openapi.json" if _api_docs_enabled() else None,
)

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


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    return _apply_security_headers(response, request.url.path)


# 例外を握って 500 でも CORS ヘッダが付く応答にする（CORSMiddleware を通すため）。設計§15
# 内部情報を漏らさないため、詳細はサーバ側ログのみ・クライアントへは汎用メッセージ。
@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    logger.exception("Unhandled error: %s %s", request.method, request.url.path, exc_info=exc)
    return _apply_security_headers(JSONResponse(
        status_code=500,
        content={"code": "INTERNAL_ERROR", "message": "Internal server error"},
    ), request.url.path)


# 検証エラー(422)の既定応答は投入値(input)と ctx を反射する。誤ったキー名で送られた
# APIキー等の秘密値がエコーされ漏洩する経路になるため、loc/msg/type のみへ絞る（#80 high-1）。
# frontend は detail[].msg を参照するため、この3項目は維持する。
@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    safe = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()]
    return _apply_security_headers(JSONResponse(status_code=422, content={"detail": safe}),
                                   request.url.path)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


@app.get("/readyz")
def readyz():
    payload = readiness.check_readiness()
    status_code = 200 if payload["status"] == "ok" else 503
    return JSONResponse(status_code=status_code, content=payload)


app.include_router(auth_router, prefix="/api")  # /api/auth/* は公開（ログイン）
app.include_router(router, prefix="/api")        # その他は認証必須
