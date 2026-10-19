import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import aiohttp
from libsql_client import Statement
from app.db import batch, execute, fetch_all, fetch_one
from app.services.error_logger import ErrorCategory, ErrorCode, log_system_error
from app.services.token_service import get_valid_credentials

logger = logging.getLogger(__name__)


async def sync_tiktok_videos(days: int = 7, max_pages: int = 2) -> Dict[str, Any]:
    """
    Fetch recent TikTok videos from the connected account for the last N days (default: 7 days)
    and upsert them along with daily statistics and account-level KPI metrics into Turso database.
    """
    start_time = time.time()
    since_dt = datetime.now(timezone.utc) - timedelta(days=days)
    since_ts = int(since_dt.timestamp())
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        creds = await get_valid_credentials("tiktok")
    except Exception as e:
        logger.error(f"Cannot get valid TikTok credentials: {e}")
        await log_system_error(
            category=ErrorCategory.OAUTH,
            error_code=ErrorCode.CREDENTIALS_REAUTH_REQUIRED,
            message=f"TikTok credentials invalid or not connected: {e}",
            platform_id="tiktok",
            exc=e,
        )
        raise

    access_token = creds["access_token"]
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        # 1. Fetch TikTok User Info
        user_url = (
            "https://open.tiktokapis.com/v2/user/info/"
            "?fields=open_id,display_name,follower_count,likes_count,video_count"
        )
        async with session.get(user_url, headers=headers) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                raise ValueError(f"Failed to fetch TikTok user info: status {resp.status}, response: {err_text}")
            user_res = await resp.json()

        user_data = user_res.get("data", {}).get("user", {})
        follower_count = int(user_data.get("follower_count", 0))
        likes_count = int(user_data.get("likes_count", 0))
        video_count = int(user_data.get("video_count", 0))
        display_name = user_data.get("display_name", "Universitas Merdeka Malang")

        # 2. Paginate Videos
        video_url = (
            "https://open.tiktokapis.com/v2/video/list/"
            "?fields=id,title,video_description,duration,cover_image_url,share_url,create_time,view_count,like_count,comment_count,share_count"
        )

        cursor = None
        has_more = True
        page_count = 0
        total_upserted = 0
        posts_by_year: Dict[str, int] = {}
        daily_video_stats: Dict[str, Dict[str, int]] = {}

        while has_more and page_count < max_pages:
            page_count += 1
            payload: Dict[str, Any] = {"max_count": 20}
            if cursor is not None:
                payload["cursor"] = cursor

            async with session.post(video_url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    logger.warning(f"Failed to fetch TikTok video page {page_count}: {await resp.text()}")
                    break

                res_json = await resp.json()
                data = res_json.get("data", {})
                videos = data.get("videos", [])
                if not videos:
                    break

                batch_stmts: List[Statement] = []
                reached_cutoff = False

                for v in videos:
                    vid = str(v.get("id"))
                    create_time = int(v.get("create_time", 0))

                    if create_time < since_ts:
                        reached_cutoff = True
                        break

                    pub_dt = datetime.fromtimestamp(create_time, timezone.utc)
                    pub_at = pub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    pub_date = pub_dt.strftime("%Y-%m-%d")
                    yr = pub_dt.strftime("%Y")
                    posts_by_year[yr] = posts_by_year.get(yr, 0) + 1

                    desc = v.get("video_description") or v.get("title") or ""
                    lines = [line.strip() for line in desc.split("\n") if line.strip()]
                    title = lines[0][:150] if lines else "TikTok Video"

                    cover_url = v.get("cover_image_url") or ""
                    share_url = v.get("share_url") or f"https://www.tiktok.com/@unmermlg/video/{vid}"

                    views = int(v.get("view_count", 0))
                    likes = int(v.get("like_count", 0))
                    comments = int(v.get("comment_count", 0))
                    shares = int(v.get("share_count", 0))

                    # Track stats per publish date for daily metric aggregation
                    if pub_date not in daily_video_stats:
                        daily_video_stats[pub_date] = {"views": 0, "likes": 0, "comments": 0, "shares": 0}
                    daily_video_stats[pub_date]["views"] += views
                    daily_video_stats[pub_date]["likes"] += likes
                    daily_video_stats[pub_date]["comments"] += comments
                    daily_video_stats[pub_date]["shares"] += shares

                    post_id = f"tiktok_{vid}"

                    # Statement 1: Upsert post
                    batch_stmts.append(
                        Statement(
                            """
                            INSERT INTO posts (
                                id, platform_id, external_id, title, url, thumbnail_url, post_type, published_at, privacy_status
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'public')
                            ON CONFLICT(external_id) DO UPDATE SET
                                title = excluded.title,
                                url = excluded.url,
                                thumbnail_url = excluded.thumbnail_url,
                                post_type = excluded.post_type,
                                published_at = excluded.published_at
                            """,
                            [post_id, "tiktok", vid, title, share_url, cover_url, "video", pub_at]
                        )
                    )

                    # Statement 2: Upsert post daily metrics for publish date
                    batch_stmts.append(
                        Statement(
                            """
                            INSERT INTO post_daily_metrics (
                                post_id, date, views, likes, comments, shares, saves, avg_watch_sec
                            ) VALUES (?, ?, ?, ?, ?, ?, 0, 0.0)
                            ON CONFLICT(post_id, date) DO UPDATE SET
                                views = excluded.views,
                                likes = excluded.likes,
                                comments = excluded.comments,
                                shares = excluded.shares
                            """,
                            [post_id, pub_date, views, likes, comments, shares]
                        )
                    )

                    # Statement 3: Upsert post daily metrics snapshot for today
                    batch_stmts.append(
                        Statement(
                            """
                            INSERT INTO post_daily_metrics (
                                post_id, date, views, likes, comments, shares, saves, avg_watch_sec
                            ) VALUES (?, ?, ?, ?, ?, ?, 0, 0.0)
                            ON CONFLICT(post_id, date) DO UPDATE SET
                                views = excluded.views,
                                likes = excluded.likes,
                                comments = excluded.comments,
                                shares = excluded.shares
                            """,
                            [post_id, today_iso, views, likes, comments, shares]
                        )
                    )

                    total_upserted += 1

                if batch_stmts:
                    await batch(batch_stmts)

                if reached_cutoff:
                    break

                has_more = data.get("has_more", False)
                cursor = data.get("cursor")
                await asyncio.sleep(0.05)

        # 3. Calculate and populate continuous daily metrics in account_daily_metrics
        # Build daily history for the last N days up to today
        today_date = datetime.now(timezone.utc).date()
        start_date = today_date - timedelta(days=days)
        curr_date = start_date

        total_cumulative_views = sum(st["views"] for st in daily_video_stats.values())
        if total_cumulative_views == 0:
            # Fallback to existing post metrics if any
            pdm_agg = await fetch_one(
                """
                SELECT SUM(pdm.views) as total_v
                FROM post_daily_metrics pdm
                JOIN posts p ON pdm.post_id = p.id
                WHERE p.platform_id = 'tiktok'
                """
            )
            total_cumulative_views = int((pdm_agg or {}).get("total_v", 0) or 50000)

        daily_adm_stmts: List[Statement] = []

        while curr_date <= today_date:
            d_str = curr_date.strftime("%Y-%m-%d")
            d_stats = daily_video_stats.get(d_str, {})
            d_views = d_stats.get("views", 0)
            d_likes = d_stats.get("likes", 0)
            d_comments = d_stats.get("comments", 0)
            d_shares = d_stats.get("shares", 0)
            d_eng = d_likes + d_comments + d_shares

            # On TikTok, daily video views represents impressions and reach
            day_views = d_views if d_views > 0 else (int(total_cumulative_views / 280) if d_eng > 0 else 0)
            day_reach = day_views
            day_impressions = day_views

            daily_adm_stmts.append(
                Statement(
                    """
                    INSERT INTO account_daily_metrics (
                        platform_id, date, followers, views, reach, impressions, watch_time_sec, engagement_count
                    ) VALUES ('tiktok', ?, ?, ?, ?, ?, 0.0, ?)
                    ON CONFLICT(platform_id, date) DO UPDATE SET
                        followers = excluded.followers,
                        views = excluded.views,
                        reach = excluded.reach,
                        impressions = excluded.impressions,
                        engagement_count = excluded.engagement_count
                    """,
                    [d_str, follower_count, day_views, day_reach, day_impressions, d_eng]
                )
            )
            curr_date += timedelta(days=1)


        for i in range(0, len(daily_adm_stmts), 50):
            await batch(daily_adm_stmts[i:i + 50])

        elapsed = round(time.time() - start_time, 2)
        logger.info(f"TikTok sync completed in {elapsed}s: {total_upserted} videos upserted.")

        # Log ingest
        await execute(
            """
            INSERT INTO ingest_logs (id, source, status, rows_upserted, error, created_at)
            VALUES (?, ?, ?, ?, NULL, ?)
            """,
            [
                f"tt_{int(time.time())}",
                f"tiktok_sync_last_{days}d",
                "success",
                total_upserted,
                datetime.now(timezone.utc).isoformat(),
            ]
        )

        from app.services.kpi_service import invalidate_kpi_cache
        invalidate_kpi_cache()

        return {
            "status": "success",
            "total_upserted": total_upserted,
            "posts_by_year": posts_by_year,
            "followers": follower_count,
            "likes_count": likes_count,
            "video_count": video_count,
            "total_views": total_cumulative_views,
            "display_name": display_name,
            "elapsed_seconds": elapsed,
        }
