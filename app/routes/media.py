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


@router.get("/avatar/{platform_id}")
async def get_platform_avatar(platform_id: str):
    """
    Serve cached profile picture (avatar) for a platform (tiktok, instagram, youtube).
    1. If cached on disk -> Return FileResponse (< 3ms).
    2. If not on disk -> Fetch fresh avatar from platform API or DB, save to disk, and return.
    3. Fallback -> Return SVG icon.
    """
    plat = platform_id.lower().strip()
    file_path = os.path.join(CACHE_DIR, f"avatar_{plat}.jpg")

    # 1. Check local disk cache
    if os.path.exists(file_path) and os.path.getsize(file_path) > 100:
        return FileResponse(
            file_path,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "public, max-age=2592000, immutable",
                "Access-Control-Allow-Origin": "*",
            }
        )

    image_bytes = None

    # 2. Try fetching fresh avatar from platform API
    if plat == "tiktok":
        try:
            creds = await get_valid_credentials("tiktok")
            token = creds.get("access_token")
            if token:
                u_info = "https://open.tiktokapis.com/v2/user/info/?fields=avatar_url,avatar_url_100,avatar_large_url"
                async with aiohttp.ClientSession() as s:
                    async with s.get(u_info, headers={"Authorization": f"Bearer {token}"}, timeout=aiohttp.ClientTimeout(total=8)) as r:
                        if r.status == 200:
                            d = await r.json()
                            user = d.get("data", {}).get("user", {})
                            fresh_url = user.get("avatar_large_url") or user.get("avatar_url") or user.get("avatar_url_100")
                            if fresh_url:
                                async with s.get(fresh_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=8)) as img_r:
                                    if img_r.status == 200:
                                        image_bytes = await img_r.read()
                                        await execute("UPDATE connected_accounts SET avatar_url = ? WHERE platform_id = 'tiktok'", [fresh_url])
        except Exception as e:
            logger.warning(f"On-demand TikTok avatar refresh failed: {e}")

    elif plat == "instagram":
        try:
            creds = await get_valid_credentials("instagram")
            token = creds.get("access_token")
            account_id = creds.get("account_id")
            if token and account_id:
                graph_url = f"https://graph.facebook.com/v21.0/{account_id}?fields=profile_picture_url&access_token={token}"
                async with aiohttp.ClientSession() as s:
                    async with s.get(graph_url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                        if r.status == 200:
                            d = await r.json()
                            fresh_url = d.get("profile_picture_url")
                            if fresh_url:
                                async with s.get(fresh_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=8)) as img_r:
                                    if img_r.status == 200:
                                        image_bytes = await img_r.read()
                                        await execute("UPDATE connected_accounts SET avatar_url = ? WHERE platform_id = 'instagram'", [fresh_url])
        except Exception as e:
            logger.warning(f"On-demand Instagram avatar refresh failed: {e}")

    elif plat == "youtube":
        try:
            res = await execute("SELECT avatar_url FROM connected_accounts WHERE platform_id = 'youtube'")
            if res.rows and res.rows[0][0]:
                y_url = str(res.rows[0][0])
                async with aiohttp.ClientSession() as s:
                    async with s.get(y_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=8)) as img_r:
                        if img_r.status == 200:
                            image_bytes = await img_r.read()
        except Exception as e:
            logger.warning(f"On-demand YouTube avatar fetch failed: {e}")

    # 3. Save to disk if successfully retrieved
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
            logger.error(f"Failed to write avatar file {file_path}: {e}")
            return Response(content=image_bytes, media_type="image/jpeg")

    # 4. Fallback SVG icon
    return Response(
        content=FALLBACK_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=3600"}
    )

