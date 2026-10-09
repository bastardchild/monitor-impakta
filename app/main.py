from contextlib import asynccontextmanager
import logging
import sys
import time
import uuid
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import close_client, fetch_all
from app.routes import auth, ingest_api, internal_api, oauth_routes, pages, partials, settings_targets
from app.security import SensitiveDataFilter
from app.services.error_logger import ErrorCategory, ErrorCode, log_system_error, prune_old_error_logs
from app.services.token_service import refresh_platform_token
from migrations.runner import run_migrations

# Configure structured logging with security sanitizer filter
logging_handler = logging.StreamHandler(sys.stdout)
logging_handler.addFilter(SensitiveDataFilter())
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "name": "%(name)s", "message": "%(message)s"}',
    handlers=[logging_handler],
)
logger = logging.getLogger("kpi-sosmed")

scheduler = AsyncIOScheduler()


async def scheduled_token_refresh_job():
    """APScheduler job: checks every 30m and refreshes tokens nearing expiration."""
    logger.info("Running periodic token expiration check...")
    try:
        now_ts = int(time.time())
        # Check all connected accounts
        accounts = await fetch_all(
            "SELECT platform_id, expires_at FROM connected_accounts WHERE status = 'connected'"
        )
        for acc in accounts:
            plat = acc["platform_id"]
            expires_at = acc["expires_at"] or 0
            time_left = expires_at - now_ts

            # Threshold: 2 hours for standard tokens, 7 days (604800s) for Instagram long-lived tokens
            threshold = 604800 if plat == "instagram" else 7200

            if time_left <= threshold:
                logger.info(f"Token for {plat} is nearing expiration ({time_left}s remaining). Triggering background refresh...")
                try:
                    await refresh_platform_token(plat)
                except Exception as e:
                    logger.error(f"Background refresh failed for {plat}: {e}")
    except Exception as e:
        logger.error(f"Error during periodic token refresh job: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Run database migrations
    logger.info("Starting up Social Media KPI Dashboard...")
    try:
        await run_migrations()
    except Exception as e:
        logger.error(f"Migration failed during startup: {e}")

    # Startup: Start APScheduler
    scheduler.add_job(scheduled_token_refresh_job, "interval", minutes=30)
    scheduler.add_job(prune_old_error_logs, "interval", hours=24)
    scheduler.start()
    logger.info("APScheduler jobs started: token refresh (every 30m), error log pruning (every 24h).")

    yield

    # Shutdown
    logger.info("Shutting down...")
    scheduler.shutdown(wait=False)
    await close_client()


settings = get_settings()

app = FastAPI(
    title="IMPAKTA RADAR",
    description="Sistem Pemantauan Terpadu Media Sosial & Portal Informasi Kampus (radar.impakta.my.id)",
    lifespan=lifespan,
)

# Correlation ID Middleware
@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    corr_id = request.headers.get("X-Correlation-ID") or f"err_{uuid.uuid4().hex[:12]}"
    request.state.correlation_id = corr_id
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = corr_id
    return response


# Session middleware
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.APP_SECRET_KEY,
    session_cookie="kpi_session",
    max_age=86400 * 30,  # 30 days
    same_site="lax",
    https_only=settings.SECURE_COOKIES,
)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include routers
app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(oauth_routes.router)
app.include_router(partials.router)
app.include_router(settings_targets.router)
app.include_router(internal_api.router)
app.include_router(ingest_api.router)


@app.exception_handler(303)
async def see_other_redirect_handler(request: Request, exc):
    location = exc.headers.get("Location", "/login") if hasattr(exc, "headers") else "/login"
    return RedirectResponse(url=location, status_code=303)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    corr_id = getattr(request.state, "correlation_id", f"err_{uuid.uuid4().hex[:12]}")
    await log_system_error(
        category=ErrorCategory.SYSTEM,
        error_code=ErrorCode.VALIDATION_ERROR,
        message=f"Request validation error on {request.url.path}",
        path=request.url.path,
        method=request.method,
        status_code=422,
        context={"validation_errors": exc.errors()},
        error_id=corr_id,
        persist=True,
    )
    return JSONResponse(
        status_code=422,
        content={
            "status": "error",
            "error_id": corr_id,
            "error_code": ErrorCode.VALIDATION_ERROR,
            "message": "Parameter permintaan tidak valid.",
            "details": exc.errors(),
        },
        headers={"X-Correlation-ID": corr_id}
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    corr_id = getattr(request.state, "correlation_id", f"err_{uuid.uuid4().hex[:12]}")
    if exc.status_code >= 400:
        err_code = ErrorCode.HTTP_NOT_FOUND if exc.status_code == 404 else ErrorCode.INTERNAL_SERVER_ERROR
        cat = ErrorCategory.AUTH if exc.status_code in (401, 403) else ErrorCategory.SYSTEM
        await log_system_error(
            category=cat,
            error_code=err_code,
            message=str(exc.detail),
            path=request.url.path,
            method=request.method,
            status_code=exc.status_code,
            error_id=corr_id,
            persist=(exc.status_code >= 500 or exc.status_code == 409),
        )

    # Return JSON for /api/* or non-HTML requests
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "status": "error",
                "error_id": corr_id,
                "detail": exc.detail,
            },
            headers={"X-Correlation-ID": corr_id}
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers={"X-Correlation-ID": corr_id})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    corr_id = getattr(request.state, "correlation_id", f"err_{uuid.uuid4().hex[:12]}")
    await log_system_error(
        category=ErrorCategory.SYSTEM,
        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        message=f"Unhandled internal server error: {str(exc)}",
        exc=exc,
        path=request.url.path,
        method=request.method,
        status_code=500,
        error_id=corr_id,
        persist=True,
    )

    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error_id": corr_id,
                "error_code": ErrorCode.INTERNAL_SERVER_ERROR,
                "message": "Terjadi kesalahan internal server.",
            },
            headers={"X-Correlation-ID": corr_id}
        )

    # Return dark error card for HTMX or HTML browser
    if "HX-Request" in request.headers:
        error_html = f"""
        <div class="p-4 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs space-y-1">
            <div class="font-bold flex items-center space-x-2">
                <span>Gagal memuat komponen</span>
                <span class="font-mono text-[10px] text-rose-400">({corr_id})</span>
            </div>
            <p class="text-slate-400">Silakan muat ulang atau periksa Log Sistem di Pengaturan.</p>
        </div>
        """
        return HTMLResponse(content=error_html, status_code=500, headers={"X-Correlation-ID": corr_id})

    return HTMLResponse(
        content=f"""
        <!DOCTYPE html>
        <html class="dark">
        <head><title>500 Internal Error &bull; Monitor Sosmed</title><script src="https://cdn.tailwindcss.com"></script></head>
        <body class="bg-[#07090e] text-slate-100 min-h-screen flex items-center justify-center p-4">
            <div class="max-w-md w-full bg-[#0d121f] border border-slate-800 p-6 rounded-2xl space-y-4 text-center">
                <div class="w-12 h-12 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-400 flex items-center justify-center mx-auto">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path stroke-linecap="round" d="M12 8v4m0 4h.01"/></svg>
                </div>
                <h2 class="text-base font-bold text-white">500 &bull; Terjadi Kesalahan Internal</h2>
                <p class="text-xs text-slate-400">Sistem mendeteksi kesalahan yang tidak tertangani. Kesalahan ini telah dicatat secara terpusat untuk diagnosis.</p>
                <div class="p-2.5 rounded-xl bg-[#07090e] border border-slate-800 font-mono text-xs text-slate-400 select-all">Error ID: {corr_id}</div>
                <a href="/" class="inline-block px-4 py-2 rounded-xl bg-sky-500 hover:bg-sky-400 text-slate-950 font-semibold text-xs transition">Kembali ke Beranda</a>
            </div>
        </body>
        </html>
        """,
        status_code=500,
        headers={"X-Correlation-ID": corr_id}
    )
