from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from app.db import fetch_all, fetch_one
from app.routes.auth import require_auth

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

VALID_PLATFORMS = {"youtube", "instagram", "tiktok"}


def _get_flash_messages(request: Request) -> dict:
    success = request.session.pop("flash_success", None)
    error = request.session.pop("flash_error", None)
    return {"flash_success": success, "flash_error": error}


@router.get("/health", tags=["health"])
async def health_check():
    return JSONResponse({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


@router.get("/", response_class=HTMLResponse)
async def overview_page(
    request: Request,
    range: str = "30d",
    start: Optional[str] = None,
    end: Optional[str] = None,
    _: None = Depends(require_auth)
):
    flash = _get_flash_messages(request)
    
    # Check if there are any connected accounts or if it's empty state
    connected = await fetch_all("SELECT platform_id, status FROM connected_accounts WHERE status = 'connected'")
    needs_reauth = await fetch_all("SELECT platform_id FROM connected_accounts WHERE status = 'needs_reauth'")
    
    return templates.TemplateResponse(
        request,
        "overview.html",
        {
            "current_page": "overview",
            "range": range,
            "start": start,
            "end": end,
            "has_connected_accounts": len(connected) > 0,
            "reauth_platforms": [r["platform_id"] for r in needs_reauth],
            **flash
        }
    )


@router.get("/platform/{name}", response_class=HTMLResponse)
async def platform_detail_page(
    name: str,
    request: Request,
    range: str = "30d",
    start: Optional[str] = None,
    end: Optional[str] = None,
    _: None = Depends(require_auth)
):
    plat_name = name.lower()
    if plat_name not in VALID_PLATFORMS:
        raise HTTPException(status_code=404, detail="Platform tidak ditemukan.")

    flash = _get_flash_messages(request)
    account = await fetch_one(
        """
        SELECT ca.*, p.name as platform_name, p.handle, p.account_id
        FROM platforms p
        LEFT JOIN connected_accounts ca ON ca.platform_id = p.id
        WHERE p.id = ?
        """,
        [plat_name]
    )

    return templates.TemplateResponse(
        request,
        "platform_detail.html",
        {
            "current_page": plat_name,
            "platform_id": plat_name,
            "platform_name": account["platform_name"] if account else plat_name.capitalize(),
            "account": account,
            "range": range,
            "start": start,
            "end": end,
            **flash
        }
    )


@router.get("/settings/connections", response_class=HTMLResponse)
async def connections_page(request: Request, _: None = Depends(require_auth)):
    flash = _get_flash_messages(request)
    return templates.TemplateResponse(
        request,
        "connections.html",
        {
            "current_page": "connections",
            **flash
        }
    )


@router.get("/unmer", response_class=HTMLResponse)
async def unmer_portal_page(
    request: Request,
    category: Optional[str] = "all",
    search: Optional[str] = None,
    _: None = Depends(require_auth)
):
    from app.services.unmer_service import get_unmer_summary, get_unmer_posts
    flash = _get_flash_messages(request)
    summary = await get_unmer_summary()
    posts = await get_unmer_posts(
        category=category if category != "all" else None,
        search=search,
        limit=50
    )
    return templates.TemplateResponse(
        request,
        "unmer_detail.html",
        {
            "current_page": "unmer",
            "summary": summary,
            "posts": posts,
            "active_category": category or "all",
            "search_query": search or "",
            **flash
        }
    )


@router.get("/settings/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    category: Optional[str] = "all",
    platform: Optional[str] = "all",
    limit: int = 50,
    _: None = Depends(require_auth)
):
    flash = _get_flash_messages(request)
    return templates.TemplateResponse(
        request,
        "logs.html",
        {
            "current_page": "logs",
            "category": category,
            "platform": platform,
            "limit": limit,
            **flash
        }
    )


