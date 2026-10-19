import asyncio
import hashlib
import io
import logging
import os
from typing import Optional
import aiohttp
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse, Response
from app.db import execute
from app.services.token_service import get_valid_credentials

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/media", tags=["media"])

# Local directory where thumbnails are persistently cached
CACHE_DIR = os.environ.get("THUMBNAIL_CACHE_DIR", "/app/data/thumbnails")
os.makedirs(CACHE_DIR, exist_ok=True)

# Default Fallback SVG (Sleek dark placeholder with camera/video icon)
FALLBACK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300" width="100%" height="100%">
  <rect width="400" height="300" fill="#0f172a"/>
  <rect x="1" y="1" width="398" height="298" rx="8" fill="none" stroke="#1e293b" stroke-width="2"/>
  <g transform="translate(160, 110)" fill="none" stroke="#64748b" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
    <rect x="2" y="2" width="76" height="60" rx="12"/>
    <circle cx="40" cy="32" r="14"/>
    <circle cx="62" cy="14" r="3" fill="#64748b"/>
  </g>
</svg>"""


def get_cached_path(post_id: str) -> str:
    """Sanitize post_id to safe filename inside CACHE_DIR."""
    safe_name = "".join(c for c in post_id if c.isalnum() or c in ("-", "_"))
    return os.path.join(CACHE_DIR, f"{safe_name}.jpg")


@router.get("/thumbnail/{post_id}")
async def get_media_thumbnail(post_id: str):
    """
    Serve cached thumbnail for a given post_id.
    1. If cached on disk -> Return FileResponse (< 3ms, never expires).
    2. If not on disk -> Fetch from stored thumbnail_url, save to disk, and return.
    3. If stored thumbnail_url returns 403 (expired) -> Ask Meta Graph API / TikTok API
       for a fresh URL, update database, save to disk, and return.
    4. Fallback -> Return clean SVG placeholder.
    """
    file_path = get_cached_path(post_id)

    # 1. Check local disk cache
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return FileResponse(
            file_path,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "public, max-age=2592000, immutable",
                "Access-Control-Allow-Origin": "*",
            }
        )

    # 2. Look up post details in DB
    try:
        res = await execute(
            "SELECT platform_id, external_id, thumbnail_url, url FROM posts WHERE id = ?",
            [post_id]
        )
        if not res.rows:
            return Response(content=FALLBACK_SVG, media_type="image/svg+xml")

        platform_id = res.rows[0][0]
        external_id = res.rows[0][1]
        thumbnail_url = str(res.rows[0][2]) if res.rows[0][2] else ""
    except Exception as e:
        logger.warning(f"Error querying post {post_id} for thumbnail: {e}")
        return Response(content=FALLBACK_SVG, media_type="image/svg+xml")

    # 3. Try downloading current thumbnail_url
    image_bytes = None
    if thumbnail_url and thumbnail_url.startswith("http"):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(thumbnail_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        image_bytes = await resp.read()
                    elif resp.status == 403:
                        logger.info(f"Thumbnail URL expired for {post_id} (status 403). Attempting on-demand refresh.")
        except Exception as e:
            logger.debug(f"Direct download failed for {post_id}: {e}")

    # 4. If direct download failed and platform is Instagram, fetch fresh URL from Graph API
    if not image_bytes and platform_id == "instagram":
        try:
            creds = await get_valid_credentials("instagram")
            access_token = creds.get("access_token")
            if access_token and external_id:
                graph_url = f"https://graph.facebook.com/v21.0/{external_id}?fields=thumbnail_url,media_url&access_token={access_token}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(graph_url, timeout=aiohttp.ClientTimeout(total=6)) as g_resp:
                        if g_resp.status == 200:
                            data = await g_resp.json()
                            fresh_url = data.get("thumbnail_url") or data.get("media_url")
                            if fresh_url:
                                async with session.get(fresh_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=6)) as img_resp:
                                    if img_resp.status == 200:
                                        image_bytes = await img_resp.read()
                                        # Update database with fresh URL asynchronously
                                        try:
                                            await execute("UPDATE posts SET thumbnail_url = ? WHERE id = ?", [fresh_url, post_id])
                                        except Exception:
                                            pass
        except Exception as e:
            logger.warning(f"Failed to refresh Instagram thumbnail for {post_id}: {e}")

    # 5. Save to disk cache if image was successfully retrieved
    if image_bytes and len(image_bytes) > 100:
        try:
            with open(file_path, "wb") as f:
                f.write(image_bytes)
            return Response(
                content=image_bytes,
                media_type="image/jpeg",
                headers={
                    "Cache-Control": "public, max-age=2592000, immutable",
                    "Access-Control-Allow-Origin": "*",
                }
            )
        except Exception as e:
            logger.error(f"Failed to write thumbnail cache for {post_id}: {e}")
            return Response(content=image_bytes, media_type="image/jpeg")

    # 6. Fallback if image cannot be loaded
    return Response(
        content=FALLBACK_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=3600"}
    )
