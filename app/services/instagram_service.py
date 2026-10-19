import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import aiohttp
from libsql_client import Statement
from app.db import batch, execute, fetch_all, fetch_one
from app.services.error_logger import ErrorCategory, ErrorCode, log_system_error
from app.services.token_service import get_valid_credentials

logger = logging.getLogger(__name__)


async def sync_instagram_posts(days: int = 7, max_pages: int = 2) -> Dict[str, Any]:
    """
    Fetch recent Instagram posts from the connected professional/business account
    for the last N days (default: 7 days) and upsert them along with their statistics
    into Turso database.
    """
    start_time = time.time()
    since_dt = datetime.now(timezone.utc) - timedelta(days=days)
    since_iso = since_dt.strftime("%Y-%m-%dT00:00:00Z")
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        creds = await get_valid_credentials("instagram")
    except Exception as e:
        logger.error(f"Cannot get valid Instagram credentials: {e}")
        await log_system_error(
            category=ErrorCategory.OAUTH,
            error_code=ErrorCode.CREDENTIALS_REAUTH_REQUIRED,
            message=f"Instagram credentials invalid or not connected: {e}",
            platform_id="instagram",
            exc=e,
        )
        raise

    access_token = creds["access_token"]
    account_id = creds["account_id"]
    if not account_id:
        raise ValueError("Instagram account_id is not set in connected credentials.")

    headers = {"Authorization": f"Bearer {access_token}"}

    async with aiohttp.ClientSession() as session:
        # 1. Fetch Instagram Account Profile
        profile_url = (
            f"https://graph.facebook.com/v21.0/{account_id}"
            "?fields=id,username,name,followers_count,media_count"
        )
        async with session.get(profile_url, headers=headers) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                raise ValueError(f"Failed to fetch Instagram profile: status {resp.status}, response: {err_text}")
            profile_data = await resp.json()

        followers_count = int(profile_data.get("followers_count", 0))
        media_count = int(profile_data.get("media_count", 0))
        account_name = profile_data.get("name") or profile_data.get("username", "")

        # 2. Fetch Account Insights (Reach) for past 30 days
        reach_today = 0
        reach_by_date: Dict[str, int] = {}
        try:
            now_ts = int(time.time())
            since_ts = now_ts - (max(days, 7) * 86400)
            insights_url = (
                f"https://graph.facebook.com/v21.0/{account_id}/insights"
                f"?metric=reach&period=day&since={since_ts}&until={now_ts}"
            )
            async with session.get(insights_url, headers=headers) as resp:
                if resp.status == 200:
                    ins_data = await resp.json()
                    data_list = ins_data.get("data", [])
                    if data_list:
                        values = data_list[0].get("values", [])
                        insights_batch: List[Statement] = []
                        for val in values:
                            day_val = int(val.get("value", 0))
                            end_t = val.get("end_time", "")
                            if len(end_t) >= 10:
                                day_str = end_t[:10]
                                reach_by_date[day_str] = day_val
                                insights_batch.append(
                                    Statement(
                                        """
                                        INSERT INTO account_daily_metrics (
                                            platform_id, date, followers, views, reach, impressions, watch_time_sec, engagement_count
                                        ) VALUES (?, ?, ?, ?, ?, ?, 0.0, 0)
                                        ON CONFLICT(platform_id, date) DO UPDATE SET
                                            followers = excluded.followers,
                                            views = excluded.views,
                                            reach = excluded.reach,
                                            impressions = excluded.impressions
                                        """,
                                        ["instagram", day_str, followers_count, day_val, day_val, day_val]
                                    )
                                )
                        if values:
                            reach_today = int(values[-1].get("value", 0))
                        if insights_batch:
                            await batch(insights_batch)
                else:
                    logger.warning(f"Could not fetch reach insights: {await resp.text()}")
        except Exception as e:
            logger.warning(f"Error querying reach insights: {e}")

        # 3. Ensure today's record exists in account_daily_metrics
        await execute(
            """
            INSERT INTO account_daily_metrics (
                platform_id, date, followers, views, reach, impressions, watch_time_sec, engagement_count
            ) VALUES (?, ?, ?, ?, ?, ?, 0.0, 0)
            ON CONFLICT(platform_id, date) DO UPDATE SET
                followers = excluded.followers,
                views = excluded.views,
                reach = excluded.reach,
                impressions = excluded.impressions
            """,
            ["instagram", today_iso, followers_count, reach_today, reach_today, reach_today]
        )

        # 4. Fetch Media items with pagination
        media_url = (
            f"https://graph.facebook.com/v21.0/{account_id}/media"
            "?fields=id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count"
            "&limit=50"
        )

        total_upserted = 0
        posts_by_year: Dict[str, int] = {}
        total_engagement = 0
        page_count = 0

        current_url = media_url

        while current_url and page_count < max_pages:
            page_count += 1
            async with session.get(current_url, headers=headers) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    logger.warning(f"Failed to fetch Instagram media page {page_count}: {err_text}")
                    break

                media_page = await resp.json()
                items = media_page.get("data", [])
                if not items:
                    break

                reached_cutoff = False
                batch_stmts: List[Statement] = []

                # Fetch media insights (saved, shares, reach) in parallel for this page
                media_insights: Dict[str, Dict[str, int]] = {}
                sem = asyncio.Semaphore(10)

                async def fetch_item_insights(mid_str: str):
                    u = f"https://graph.facebook.com/v21.0/{mid_str}/insights?metric=saved,shares,reach"
                    async with sem:
                        try:
                            async with session.get(u, headers=headers) as r:
                                if r.status == 200:
                                    d = await r.json()
                                    sv, sh, rc = 0, 0, 0
                                    for it in d.get("data", []):
                                        val = int(it["values"][0]["value"] if it.get("values") else 0)
                                        if it["name"] == "saved":
                                            sv = val
                                        elif it["name"] == "shares":
                                            sh = val
                                        elif it["name"] == "reach":
                                            rc = val
                                    return mid_str, {"saved": sv, "shares": sh, "reach": rc}
                        except Exception:
                            pass
                        return mid_str, {"saved": 0, "shares": 0, "reach": 0}

                ins_results = await asyncio.gather(
                    *[fetch_item_insights(str(m["id"])) for m in items if m.get("id")]
                )
                for m_id, m_ins in ins_results:
                    media_insights[m_id] = m_ins

                for m in items:
                    mid = str(m.get("id"))
                    pub_at = m.get("timestamp")  # e.g. "2026-10-05T07:35:40+0000"
                    
                    if pub_at and pub_at.endswith("+0000"):
                        pub_at = pub_at[:-5] + "Z"

                    if pub_at and pub_at < since_iso:
                        reached_cutoff = True
                        break

                    caption = m.get("caption") or ""
                    lines = [line.strip() for line in caption.split("\n") if line.strip()]
                    title = lines[0][:150] if lines else "Instagram Post"

                    media_type = (m.get("media_type") or "image").lower()
                    thumb_url = m.get("thumbnail_url") or m.get("media_url") or ""
                    permalink = m.get("permalink") or f"https://www.instagram.com/p/{mid}/"

                    likes = int(m.get("like_count", 0))
                    comments = int(m.get("comments_count", 0))
                    
                    m_ins = media_insights.get(mid, {})
                    shares = m_ins.get("shares", 0)
                    saves = m_ins.get("saved", 0)
                    m_reach = m_ins.get("reach", 0)
                    views = m_reach if m_reach > 0 else (likes + comments)
                    total_engagement += (likes + comments + shares + saves)

                    yr = pub_at[:4] if pub_at else "unknown"
                    posts_by_year[yr] = posts_by_year.get(yr, 0) + 1

                    post_id = f"instagram_{mid}"

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
                            [post_id, "instagram", mid, title, permalink, thumb_url, media_type, pub_at]
                        )
                    )

                    # Statement 2: Upsert post daily metrics for publish date
                    if pub_at and len(pub_at) >= 10:
                        pub_date = pub_at[:10]
                        batch_stmts.append(
                            Statement(
                                """
                                INSERT INTO post_daily_metrics (
                                    post_id, date, views, likes, comments, shares, saves, avg_watch_sec
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0.0)
                                ON CONFLICT(post_id, date) DO UPDATE SET
                                    views = excluded.views,
                                    likes = excluded.likes,
                                    comments = excluded.comments,
                                    shares = excluded.shares,
                                    saves = excluded.saves
                                """,
                                [post_id, pub_date, views, likes, comments, shares, saves]
                            )
                        )

                    # Statement 3: Upsert post daily metrics snapshot for today
                    batch_stmts.append(
                        Statement(
                            """
                            INSERT INTO post_daily_metrics (
                                post_id, date, views, likes, comments, shares, saves, avg_watch_sec
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0.0)
                            ON CONFLICT(post_id, date) DO UPDATE SET
                                views = excluded.views,
                                likes = excluded.likes,
                                comments = excluded.comments,
                                shares = excluded.shares,
                                saves = excluded.saves
                            """,
                            [post_id, today_iso, views, likes, comments, shares, saves]
                        )
                    )

                    total_upserted += 1

                # Execute all statements for this page in a single batch call
                if batch_stmts:
                    await batch(batch_stmts)

                if reached_cutoff:
                    break

                # Get next page URL
                paging = media_page.get("paging", {})
                current_url = paging.get("next")
                if not current_url:
                    break

                await asyncio.sleep(0.05)

        # 5. Update today's engagement_count in account_daily_metrics
        if total_engagement > 0:
            await execute(
                """
                UPDATE account_daily_metrics
                SET engagement_count = ?
                WHERE platform_id = 'instagram' AND date = ?
                """,
                [total_engagement, today_iso]
            )

        elapsed = round(time.time() - start_time, 2)
        logger.info(
            f"Instagram sync completed in {elapsed}s: {total_upserted} posts upserted for {account_name}."
        )

        # Log to ingest_logs
        await execute(
            """
            INSERT INTO ingest_logs (id, source, status, rows_upserted, error, created_at)
            VALUES (?, ?, ?, ?, NULL, ?)
            """,
            [
                f"ig_{int(time.time())}",
                f"instagram_sync_last_{days}d",
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
            "followers": followers_count,
            "reach": reach_today,
            "media_count": media_count,
            "account_name": account_name,
            "elapsed_seconds": elapsed,
        }
