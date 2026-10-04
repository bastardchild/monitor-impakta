from datetime import datetime, timezone, timedelta
import json
import time
from typing import Optional
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.config import get_settings
from app.db import fetch_all
from app.routes.auth import require_auth
from app.services.kpi_service import (
    get_kpi_summary,
    get_kpi_targets_progress,
    get_sentiment_summary,
    get_top_posts,
    get_trend_chart_data,
)
from app.services.error_logger import get_recent_error_logs

router = APIRouter(prefix="/partials", tags=["partials"])
templates = Jinja2Templates(directory="app/templates")


def _format_jakarta_time(ts: Optional[int | str]) -> str:
    if not ts:
        return "-"
    try:
        settings = get_settings()
        tz = ZoneInfo(settings.TZ)
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz)
        else:
            dt = datetime.fromisoformat(str(ts)).astimezone(tz)
        return dt.strftime("%d %b %Y, %H:%M WIB")
    except Exception:
        return str(ts)


def _format_relative_expiry(expires_at: Optional[int]) -> tuple[str, str]:
    """Returns (human_text, badge_color_class)."""
    if not expires_at:
        return "-", "text-gray-400"
    now_ts = int(time.time())
    diff = expires_at - now_ts
    if diff <= 0:
        return "Kedaluwarsa", "text-rose-500 font-semibold"
    days = diff // 86400
    hours = (diff % 86400) // 3600
    minutes = (diff % 3600) // 60

    if days > 7:
        return f"{days} hari lagi", "text-emerald-500"
    elif days > 0:
        return f"{days} hari {hours} jam lagi", "text-amber-500 font-semibold"
    elif hours > 0:
        return f"{hours} jam {minutes} menit lagi", "text-amber-500 font-semibold"
    else:
        return f"{minutes} menit lagi", "text-rose-500 font-bold"


@router.get("/connection-status", response_class=HTMLResponse)
async def partial_connection_status(request: Request, _: None = Depends(require_auth)):
    settings = get_settings()
    base_url = settings.BASE_URL.rstrip("/")
    
    # Query platforms with connected account info
    sql = """
        SELECT 
            p.id as platform_id,
            p.name as platform_name,
            ca.status,
            ca.display_name,
            ca.avatar_url,
            ca.expires_at,
            ca.connected_at,
            ca.last_error,
            ca.external_account_id
        FROM platforms p
        LEFT JOIN connected_accounts ca ON ca.platform_id = p.id
    """
    rows = await fetch_all(sql)
    platforms_data = {}

    for r in rows:
        plat_id = r["platform_id"]
        status = r["status"] or "disconnected"
        expires_at = r["expires_at"]
        last_error = r["last_error"]
        now_ts = int(time.time())

        # Determine visual status badge
        if status == "connected":
            if expires_at and (expires_at - now_ts) < 86400 * 3:
                badge_text = "Segera kedaluwarsa"
                badge_class = "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
            else:
                badge_text = "Terhubung"
                badge_class = "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300"
        elif status == "needs_reauth":
            badge_text = "Perlu hubungkan ulang"
            badge_class = "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300"
        else:
            badge_text = "Belum terhubung"
            badge_class = "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300"

        # Last sync log
        sync_sql = """
            SELECT status, rows_upserted, created_at, error 
            FROM ingest_logs 
            WHERE source LIKE ? 
            ORDER BY created_at DESC LIMIT 1
        """
        last_sync = await fetch_all(sync_sql, [f"%{plat_id}%"])
        last_sync_info = "-"
        if last_sync:
            ls = last_sync[0]
            last_sync_info = f"{_format_jakarta_time(ls['created_at'])} ({ls['status'].capitalize()})"

        expiry_human, expiry_color = _format_relative_expiry(expires_at)

        platforms_data[plat_id] = {
            "id": plat_id,
            "name": r["platform_name"],
            "status": status,
            "badge_text": badge_text,
            "badge_class": badge_class,
            "display_name": r["display_name"] or "-",
            "avatar_url": r["avatar_url"],
            "connected_at": _format_jakarta_time(r["connected_at"]),
            "expires_at_human": expiry_human,
            "expires_at_color": expiry_color,
            "last_sync": last_sync_info,
            "last_error": last_error,
            "redirect_uri": f"{base_url}/connect/{plat_id}/callback",
        }

    return templates.TemplateResponse(
        request,
        "partials/connection_cards.html",
        {"platforms": platforms_data}
    )


@router.get("/kpi-cards", response_class=HTMLResponse)
async def partial_kpi_cards(
    request: Request,
    range: str = "30d",
    platform: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    _: None = Depends(require_auth)
):
    plat_filter = platform if (platform and platform != "all") else None
    kpi_data = await get_kpi_summary(range_key=range, platform_id=plat_filter, start_custom=start, end_custom=end)
    return templates.TemplateResponse(
        request,
        "partials/kpi_cards.html",
        {"kpi": kpi_data, "platform": platform or "all"}
    )


@router.get("/trend-chart", response_class=HTMLResponse)
async def partial_trend_chart(
    request: Request,
    range: str = "30d",
    platform: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    _: None = Depends(require_auth)
):
    plat_filter = platform if (platform and platform != "all") else None
    chart_data = await get_trend_chart_data(range_key=range, platform_id=plat_filter, start_custom=start, end_custom=end)
    return templates.TemplateResponse(
        request,
        "partials/trend_chart.html",
        {"chart_data": chart_data, "chart_json": json.dumps(chart_data)}
    )


@router.get("/top-posts", response_class=HTMLResponse)
async def partial_top_posts(
    request: Request,
    range: str = "30d",
    platform: Optional[str] = None,
    _: None = Depends(require_auth)
):
    plat_filter = platform if (platform and platform != "all") else None
    posts = await get_top_posts(range_key=range, platform_id=plat_filter, limit=6)
    return templates.TemplateResponse(
        request,
        "partials/top_posts.html",
        {"posts": posts, "platform": platform or "all"}
    )


@router.get("/kpi-targets", response_class=HTMLResponse)
async def partial_kpi_targets(
    request: Request,
    platform: Optional[str] = None,
    _: None = Depends(require_auth)
):
    plat_filter = platform if (platform and platform != "all") else None
    targets = await get_kpi_targets_progress(platform_id=plat_filter)
    return templates.TemplateResponse(
        request,
        "partials/kpi_targets.html",
        {"targets": targets}
    )


@router.get("/sentiment-insight", response_class=HTMLResponse)
async def partial_sentiment_insight(
    request: Request,
    range: str = "30d",
    platform: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    _: None = Depends(require_auth)
):
    plat_filter = platform if (platform and platform != "all") else None
    sentiment = await get_sentiment_summary(range_key=range, platform_id=plat_filter, start_custom=start, end_custom=end)
    return templates.TemplateResponse(
        request,
        "partials/sentiment_insight.html",
        {"sentiment": sentiment, "platform": platform or "all"}
    )


@router.get("/error-logs", response_class=HTMLResponse)
async def partial_error_logs(
    request: Request,
    category: Optional[str] = "all",
    platform: Optional[str] = "all",
    limit: int = 50,
    _: None = Depends(require_auth)
):
    cat_filter = category if (category and category != "all") else None
    plat_filter = platform if (platform and platform != "all") else None
    logs = await get_recent_error_logs(limit=limit, category=cat_filter, platform_id=plat_filter)
    return templates.TemplateResponse(
        request,
        "partials/error_logs_table.html",
        {
            "logs": logs,
            "category": category or "all",
            "platform": platform or "all",
            "limit": limit,
        }
    )


@router.get("/unmer/posts", response_class=HTMLResponse)
async def partial_unmer_posts(
    request: Request,
    category: Optional[str] = "all",
    search: Optional[str] = None,
    limit: int = 50,
    _: None = Depends(require_auth)
):
    from app.services.unmer_service import get_unmer_posts
    posts = await get_unmer_posts(
        category=category if category != "all" else None,
        search=search,
        limit=limit
    )
    return templates.TemplateResponse(
        request,
        "partials/unmer_posts_table.html",
        {
            "posts": posts,
            "active_category": category or "all",
            "search_query": search or "",
        }
    )


@router.post("/unmer/sync", response_class=HTMLResponse)
async def partial_unmer_sync(
    request: Request,
    _: None = Depends(require_auth)
):
    from app.services.unmer_service import sync_unmer_posts, get_unmer_summary, get_unmer_posts
    sync_result = await sync_unmer_posts(max_pages=2, per_page=50)
    summary = await get_unmer_summary()
    posts = await get_unmer_posts(limit=50)
    return templates.TemplateResponse(
        request,
        "partials/unmer_sync_result.html",
        {
            "sync_result": sync_result,
            "summary": summary,
            "posts": posts,
        }
    )


