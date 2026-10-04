import logging
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
import httpx
from app.config import get_settings
from app.oauth import get_provider
from app.routes.auth import require_auth
from app.security import generate_oauth_state
from app.services.error_logger import ErrorCategory, ErrorCode, log_system_error
from app.services.token_service import disconnect_account, save_connected_account

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _get_redirect_uri(platform: str) -> str:
    settings = get_settings()
    base = settings.BASE_URL.rstrip("/")
    return f"{base}/connect/{platform}/callback"


@router.get("/connect/{platform}")
async def connect_platform(platform: str, request: Request, _: None = Depends(require_auth)):
    platform = platform.lower()
    try:
        provider = get_provider(platform)
    except KeyError:
        raise HTTPException(status_code=404, detail="Platform tidak didukung.")

    state = generate_oauth_state()
    request.session[f"oauth_state_{platform}"] = state
    redirect_uri = _get_redirect_uri(platform)
    auth_url = provider.build_auth_url(redirect_uri=redirect_uri, state=state)
    return RedirectResponse(url=auth_url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/connect/{platform}/callback")
async def connect_platform_callback(
    platform: str,
    request: Request,
    state: Optional[str] = None,
    code: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    _: None = Depends(require_auth)
):
    platform = platform.lower()
    expected_state = request.session.pop(f"oauth_state_{platform}", None)

    if error:
        err_msg = error_description or error
        await log_system_error(
            category=ErrorCategory.OAUTH,
            error_code=ErrorCode.OAUTH_TOKEN_EXCHANGE_FAILED,
            message=f"Platform returned error during OAuth for {platform}: {err_msg}",
            path=request.url.path,
            method=request.method,
            platform_id=platform,
            status_code=400,
        )
        request.session["flash_error"] = f"Gagal otorisasi {platform}: {err_msg}"
        return RedirectResponse(url="/settings/connections", status_code=status.HTTP_303_SEE_OTHER)

    if not expected_state or expected_state != state:
        await log_system_error(
            category=ErrorCategory.OAUTH,
            error_code=ErrorCode.OAUTH_STATE_INVALID,
            message=f"CSRF state mismatch for {platform}. Expected state not matching callback state.",
            path=request.url.path,
            method=request.method,
            platform_id=platform,
            status_code=400,
        )
        request.session["flash_error"] = "Validasi keamanan CSRF (state mismatch) gagal. Silakan ulangi koneksi."
        return RedirectResponse(url="/settings/connections", status_code=status.HTTP_303_SEE_OTHER)

    if not code:
        request.session["flash_error"] = "Kode otorisasi tidak ditemukan dari respon platform."
        return RedirectResponse(url="/settings/connections", status_code=status.HTTP_303_SEE_OTHER)

    redirect_uri = _get_redirect_uri(platform)
    provider = get_provider(platform)

    try:
        tokens = await provider.exchange_code(code=code, redirect_uri=redirect_uri)
        
        # Meta / Instagram specific: Check if user has multiple connected Instagram pages
        if platform == "instagram":
            meta_provider = provider
            pages = await meta_provider.fetch_connected_pages(tokens["access_token"])
            if not pages:
                raise ValueError(
                    "Tidak ditemukan akun Instagram Business/Creator yang terhubung ke Facebook Page. "
                    "Pastikan akun Instagram bertipe Profesional dan sudah ditautkan ke Facebook Page."
                )
            if len(pages) > 1:
                # Store in session and present selection page
                request.session["meta_temp_tokens"] = tokens
                request.session["meta_pages"] = pages
                return RedirectResponse(url="/connect/instagram/select", status_code=status.HTTP_303_SEE_OTHER)
            
            account_info = await provider.fetch_account_info(tokens["access_token"], ig_account_id=pages[0]["ig_id"])
        else:
            account_info = await provider.fetch_account_info(tokens["access_token"])

        await save_connected_account(platform, tokens, account_info)
        request.session["flash_success"] = f"Akun {platform.capitalize()} ({account_info.get('display_name')}) berhasil dihubungkan!"
    except Exception as e:
        err_id = await log_system_error(
            category=ErrorCategory.OAUTH,
            error_code=ErrorCode.OAUTH_TOKEN_EXCHANGE_FAILED,
            message=f"OAuth callback error for {platform}: {str(e)}",
            exc=e,
            path=request.url.path,
            method=request.method,
            platform_id=platform,
            status_code=500,
        )
        request.session["flash_error"] = f"Terjadi kesalahan saat menghubungkan {platform}: {str(e)} (ID: {err_id})"

    return RedirectResponse(url="/settings/connections", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/connect/instagram/select", response_class=HTMLResponse)
async def select_instagram_page(request: Request, _: None = Depends(require_auth)):
    pages = request.session.get("meta_pages", [])
    if not pages:
        return RedirectResponse(url="/settings/connections", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "select_instagram_account.html", {"pages": pages})


@router.post("/connect/instagram/select")
async def select_instagram_page_submit(
    request: Request,
    ig_id: str = Form(...),
    _: None = Depends(require_auth)
):
    tokens = request.session.pop("meta_temp_tokens", None)
    pages = request.session.pop("meta_pages", None)

    if not tokens:
        request.session["flash_error"] = "Sesi pemilihan akun Instagram telah kedaluwarsa. Silakan ulangi koneksi."
        return RedirectResponse(url="/settings/connections", status_code=status.HTTP_303_SEE_OTHER)

    provider = get_provider("instagram")
    try:
        account_info = await provider.fetch_account_info(tokens["access_token"], ig_account_id=ig_id)
        await save_connected_account("instagram", tokens, account_info)
        request.session["flash_success"] = f"Akun Instagram ({account_info.get('display_name')}) berhasil dihubungkan!"
    except Exception as e:
        request.session["flash_error"] = f"Gagal menyimpan akun Instagram yang dipilih: {e}"

    return RedirectResponse(url="/settings/connections", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/disconnect/{platform}")
async def disconnect(platform: str, request: Request, _: None = Depends(require_auth)):
    platform = platform.lower()
    await disconnect_account(platform)
    request.session["flash_success"] = f"Koneksi {platform.capitalize()} berhasil diputuskan."
    
    if request.headers.get("HX-Request"):
        # Respond with HX-Redirect to refresh page cleanly
        return Response(status_code=200, headers={"HX-Redirect": "/settings/connections"})
    return RedirectResponse(url="/settings/connections", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/connections/{platform}/sync")
async def trigger_platform_sync(platform: str, request: Request, _: None = Depends(require_auth)):
    settings = get_settings()
    platform = platform.lower()
    
    url_attr = f"N8N_WEBHOOK_URL_{platform.upper()}"
    webhook_url = getattr(settings, url_attr, None)
    
    if not webhook_url:
        return templates.TemplateResponse(
            request,
            "partials/sync_status.html",
            {"status": "error", "message": f"Webhook URL untuk {platform} belum dikonfigurasi di .env"}
        )

    headers = {"X-Webhook-Secret": settings.N8N_WEBHOOK_SECRET}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(webhook_url, headers=headers, json={"platform": platform}, timeout=10.0)
            if resp.status_code in (200, 201, 202):
                return templates.TemplateResponse(
                    request,
                    "partials/sync_status.html",
                    {"status": "success", "message": f"Sinkronisasi {platform.capitalize()} berhasil dipicu di n8n."}
                )
            else:
                return templates.TemplateResponse(
                    request,
                    "partials/sync_status.html",
                    {"status": "error", "message": f"n8n merespon status {resp.status_code}"}
                )
    except Exception as e:
        err_id = await log_system_error(
            category=ErrorCategory.OAUTH,
            error_code=ErrorCode.OAUTH_WEBHOOK_FAILED,
            message=f"Error calling n8n sync webhook for {platform}: {str(e)}",
            exc=e,
            path=request.url.path,
            method=request.method,
            platform_id=platform,
            status_code=500,
        )
        return templates.TemplateResponse(
            request,
            "partials/sync_status.html",
            {"status": "error", "message": f"Gagal menghubungi n8n: {str(e)} ({err_id})"}
        )
