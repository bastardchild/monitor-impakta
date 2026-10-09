import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import aiohttp
from app.db import execute, fetch_all, fetch_one
from app.services.error_logger import ErrorCategory, ErrorCode, log_system_error
from app.services.token_service import get_valid_credentials

logger = logging.getLogger(__name__)


async def sync_youtube_videos(since_year: int = 2024) -> Dict[str, Any]:
    """
    Fetch all YouTube videos from the connected channel since a given year (default: 2024)
    and upsert them along with their statistics into Turso database.
    """
    start_time = time.time()
    since_iso = f"{since_year}-01-01T00:00:00Z"
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        creds = await get_valid_credentials("youtube")
    except Exception as e:
        logger.error(f"Cannot get valid YouTube credentials: {e}")
        await log_system_error(
            category=ErrorCategory.OAUTH,
            error_code=ErrorCode.CREDENTIALS_REAUTH_REQUIRED,
            message=f"YouTube credentials invalid or not connected: {e}",
            platform_id="youtube",
            exc=e
        )
        raise

    access_token = creds["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    async with aiohttp.ClientSession() as session:
        # 1. Fetch channel info & uploads playlist ID
        async with session.get(
            "https://www.googleapis.com/youtube/v3/channels?part=snippet,contentDetails,statistics&mine=true",
            headers=headers
        ) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                raise ValueError(f"Failed to fetch YouTube channel: status {resp.status}, response: {err_text}")
            ch_data = await resp.json()
            if "items" not in ch_data or not ch_data["items"]:
                raise ValueError(f"No YouTube channel found for connected account: {ch_data}")

            ch_item = ch_data["items"][0]
            uploads_playlist = ch_item["contentDetails"]["relatedPlaylists"]["uploads"]
            ch_stats = ch_item.get("statistics", {})
            sub_count = int(ch_stats.get("subscriberCount", 0))
            view_count = int(ch_stats.get("viewCount", 0))
            comment_count = int(ch_stats.get("commentCount", 0))

        # 2. Update channel daily metrics for today
        await execute(
            """
            INSERT INTO account_daily_metrics (
                platform_id, date, followers, views, reach, impressions, watch_time_sec, engagement_count
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(platform_id, date) DO UPDATE SET
                followers = excluded.followers,
                views = excluded.views,
                reach = excluded.reach,
                impressions = excluded.impressions,
                engagement_count = excluded.engagement_count
            """,
            ["youtube", today_iso, sub_count, view_count, view_count, view_count, comment_count]
        )

        # 3. Iterate through uploads playlist items
        all_video_ids: List[str] = []
        page_token = None

        while True:
            url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=snippet,contentDetails&playlistId={uploads_playlist}&maxResults=50"
            if page_token:
                url += f"&pageToken={page_token}"

            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    logger.warning(f"Error fetching playlistItems page: {err_text}")
                    break

                pl_data = await resp.json()
                items = pl_data.get("items", [])
                if not items:
                    break

                for it in items:
                    pub = it["snippet"]["publishedAt"]
                    vid = it["contentDetails"]["videoId"]
                    if pub >= since_iso:
                        all_video_ids.append(vid)

                next_token = pl_data.get("nextPageToken")
                oldest_pub = items[-1]["snippet"]["publishedAt"]

                if oldest_pub < since_iso or not next_token:
                    break

                page_token = next_token

        # 4. Fetch full video statistics in batches of 50
        batch_size = 50
        total_upserted = 0
        posts_by_year: Dict[str, int] = {}

        for i in range(0, len(all_video_ids), batch_size):
            batch_ids = all_video_ids[i:i + batch_size]
            ids_str = ",".join(batch_ids)
            v_url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics,status,liveStreamingDetails&id={ids_str}"

            async with session.get(v_url, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(f"Failed to fetch video details batch: {await resp.text()}")
                    continue

                v_data = await resp.json()
                v_items = v_data.get("items", [])

                for v in v_items:
                    vid = v["id"]
                    snip = v.get("snippet", {})
                    stat = v.get("statistics", {})
                    st = v.get("status", {})
                    ls = v.get("liveStreamingDetails")

                    title = snip.get("title", "")
                    pub_at = snip.get("publishedAt")
                    yr = pub_at[:4] if pub_at else "unknown"
                    posts_by_year[yr] = posts_by_year.get(yr, 0) + 1

                    thumbs = snip.get("thumbnails", {})
                    thumb_url = (
                        (thumbs.get("maxres") or {}).get("url") or
                        (thumbs.get("standard") or {}).get("url") or
                        (thumbs.get("high") or {}).get("url") or
                        (thumbs.get("medium") or {}).get("url") or
                        (thumbs.get("default") or {}).get("url") or
                        ""
                    )

                    streamed_at = (ls.get("actualStartTime") or ls.get("scheduledStartTime")) if ls else None
                    privacy = (st.get("privacyStatus") or "public").lower()

                    v_views = int(stat.get("viewCount", 0))
                    v_likes = int(stat.get("likeCount", 0))
                    v_comments = int(stat.get("commentCount", 0))

                    post_id = f"youtube_{vid}"
                    url = f"https://www.youtube.com/watch?v={vid}"

                    # Upsert post
                    await execute(
                        """
                        INSERT INTO posts (
                            id, platform_id, external_id, title, url, thumbnail_url, post_type, published_at, streamed_at, privacy_status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(external_id) DO UPDATE SET
                            title = excluded.title,
                            url = excluded.url,
                            thumbnail_url = excluded.thumbnail_url,
                            post_type = excluded.post_type,
                            published_at = excluded.published_at,
                            streamed_at = COALESCE(excluded.streamed_at, posts.streamed_at),
                            privacy_status = COALESCE(excluded.privacy_status, posts.privacy_status)
                        """,
                        [post_id, "youtube", vid, title, url, thumb_url, "video", pub_at, streamed_at, privacy]
                    )

                    # Upsert post daily metrics for publish date
                    if pub_at and len(pub_at) >= 10:
                        pub_date = pub_at[:10]
                        await execute(
                            """
                            INSERT INTO post_daily_metrics (
                                post_id, date, views, likes, comments, shares, saves, avg_watch_sec
                            ) VALUES (?, ?, ?, ?, ?, 0, 0, 0.0)
                            ON CONFLICT(post_id, date) DO UPDATE SET
                                views = excluded.views,
                                likes = excluded.likes,
                                comments = excluded.comments
                            """,
                            [post_id, pub_date, v_views, v_likes, v_comments]
                        )

                    # Upsert post daily metrics for today snapshot
                    await execute(
                        """
                        INSERT INTO post_daily_metrics (
                            post_id, date, views, likes, comments, shares, saves, avg_watch_sec
                        ) VALUES (?, ?, ?, ?, ?, 0, 0, 0.0)
                        ON CONFLICT(post_id, date) DO UPDATE SET
                            views = excluded.views,
                            likes = excluded.likes,
                            comments = excluded.comments
                        """,
                        [post_id, today_iso, v_views, v_likes, v_comments]
                    )

                    total_upserted += 1

        elapsed = round(time.time() - start_time, 2)
        logger.info(f"YouTube sync completed in {elapsed}s: {total_upserted} videos upserted.")

        # Log ingest result
        await execute(
            """
            INSERT INTO ingest_logs (id, source, status, rows_upserted, error, created_at)
            VALUES (?, ?, ?, ?, NULL, ?)
            """,
            [f"yt_{int(time.time())}", f"youtube_sync_from_{since_year}", "success", total_upserted, datetime.now(timezone.utc).isoformat()]
        )

        return {
            "status": "success",
            "total_upserted": total_upserted,
            "posts_by_year": posts_by_year,
            "subscribers": sub_count,
            "channel_views": view_count,
            "elapsed_seconds": elapsed,
        }
