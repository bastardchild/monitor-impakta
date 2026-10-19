import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List
import aiohttp
from libsql_client import Statement

from app.db import batch, execute, close_client
from app.services.token_service import get_valid_credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("youtube_2023_fetch")


async def fetch_and_ingest_youtube_2023():
    start_time = time.time()
    start_2023_iso = "2023-01-01T00:00:00Z"
    end_2023_iso = "2023-12-31T23:59:59Z"
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    logger.info("Starting one-off fetch for YouTube videos from year 2023...")

    # 1. Get valid credentials
    creds = await get_valid_credentials("youtube")
    access_token = creds["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    async with aiohttp.ClientSession() as session:
        # 2. Get channel uploads playlist
        async with session.get(
            "https://www.googleapis.com/youtube/v3/channels?part=snippet,contentDetails,statistics&mine=true",
            headers=headers
        ) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                raise ValueError(f"Failed to fetch YouTube channel: status {resp.status}, response: {err_text}")
            ch_data = await resp.json()
            if "items" not in ch_data or not ch_data["items"]:
                raise ValueError("No YouTube channel found for connected account.")

            ch_item = ch_data["items"][0]
            uploads_playlist = ch_item["contentDetails"]["relatedPlaylists"]["uploads"]
            ch_title = ch_item["snippet"].get("title", "UNMER Malang")
            logger.info(f"Target YouTube Channel: {ch_title} (Uploads playlist: {uploads_playlist})")

        # 3. Traverse playlist to find videos in 2023
        all_2023_video_ids: List[str] = []
        page_token = None
        page_count = 0

        while True:
            page_count += 1
            url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=snippet,contentDetails&playlistId={uploads_playlist}&maxResults=50"
            if page_token:
                url += f"&pageToken={page_token}"

            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(f"Error fetching playlistItems page {page_count}: {await resp.text()}")
                    break

                pl_data = await resp.json()
                items = pl_data.get("items", [])
                if not items:
                    break

                for it in items:
                    pub = it["snippet"]["publishedAt"]
                    vid = it["contentDetails"]["videoId"]
                    if start_2023_iso <= pub <= end_2023_iso:
                        all_2023_video_ids.append(vid)

                next_token = pl_data.get("nextPageToken")
                oldest_pub = items[-1]["snippet"]["publishedAt"]

                logger.info(f"Page {page_count}: oldest video published at {oldest_pub}, found {len(all_2023_video_ids)} videos in 2023 so far.")

                if oldest_pub < start_2023_iso or not next_token:
                    logger.info("Reached videos before 2023 or end of playlist. Stopping pagination.")
                    break

                page_token = next_token

        logger.info(f"Total video IDs identified in 2023: {len(all_2023_video_ids)}")
        if not all_2023_video_ids:
            logger.warning("No videos found published in 2023.")
            return {"total_upserted": 0, "videos": []}

        # 4. Fetch full video statistics in batches of 50
        batch_size = 50
        all_videos_data: List[Dict[str, Any]] = []

        for i in range(0, len(all_2023_video_ids), batch_size):
            batch_ids = all_2023_video_ids[i:i + batch_size]
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

                    all_videos_data.append({
                        "id": f"youtube_{vid}",
                        "external_id": vid,
                        "title": title,
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "thumbnail_url": thumb_url,
                        "published_at": pub_at,
                        "streamed_at": streamed_at,
                        "privacy_status": privacy,
                        "views": v_views,
                        "likes": v_likes,
                        "comments": v_comments,
                    })

        # 5. Batch upsert into database
        statements: List[Statement] = []
        for v in all_videos_data:
            # Statement 1: Upsert post
            statements.append(
                Statement(
                    """
                    INSERT INTO posts (
                        id, platform_id, external_id, title, url, thumbnail_url, post_type, published_at, streamed_at, privacy_status
                    ) VALUES (?, 'youtube', ?, ?, ?, ?, 'video', ?, ?, ?)
                    ON CONFLICT(external_id) DO UPDATE SET
                        title = excluded.title,
                        url = excluded.url,
                        thumbnail_url = excluded.thumbnail_url,
                        post_type = excluded.post_type,
                        published_at = excluded.published_at,
                        streamed_at = COALESCE(excluded.streamed_at, posts.streamed_at),
                        privacy_status = COALESCE(excluded.privacy_status, posts.privacy_status)
                    """,
                    [v["id"], v["external_id"], v["title"], v["url"], v["thumbnail_url"], v["published_at"], v["streamed_at"], v["privacy_status"]]
                )
            )

            # Statement 2: Upsert post_daily_metrics for publish date
            pub_date = v["published_at"][:10] if v["published_at"] and len(v["published_at"]) >= 10 else None
            if pub_date:
                statements.append(
                    Statement(
                        """
                        INSERT INTO post_daily_metrics (
                            post_id, date, views, likes, comments, shares, saves, avg_watch_sec
                        ) VALUES (?, ?, ?, ?, ?, 0, 0, 0.0)
                        ON CONFLICT(post_id, date) DO UPDATE SET
                            views = excluded.views,
                            likes = excluded.likes,
                            comments = excluded.comments
                        """,
                        [v["id"], pub_date, v["views"], v["likes"], v["comments"]]
                    )
                )

            # Statement 3: Upsert post_daily_metrics for today snapshot
            statements.append(
                Statement(
                    """
                    INSERT INTO post_daily_metrics (
                        post_id, date, views, likes, comments, shares, saves, avg_watch_sec
                    ) VALUES (?, ?, ?, ?, ?, 0, 0, 0.0)
                    ON CONFLICT(post_id, date) DO UPDATE SET
                        views = excluded.views,
                        likes = excluded.likes,
                        comments = excluded.comments
                    """,
                    [v["id"], today_iso, v["views"], v["likes"], v["comments"]]
                )
            )

        logger.info(f"Executing {len(statements)} SQL statements in batches of 50...")
        for i in range(0, len(statements), 50):
            await batch(statements[i:i + 50])

        elapsed = round(time.time() - start_time, 2)
        logger.info(f"2023 YouTube fetch and upsert completed in {elapsed}s: {len(all_videos_data)} videos processed.")

        # Log to ingest_logs
        await execute(
            """
            INSERT INTO ingest_logs (id, source, status, rows_upserted, error, created_at)
            VALUES (?, ?, ?, ?, NULL, ?)
            """,
            [
                f"yt_2023_{int(time.time())}",
                "youtube_custom_sync_2023",
                "success",
                len(all_videos_data),
                datetime.now(timezone.utc).isoformat()
            ]
        )

        return {
            "status": "success",
            "total_upserted": len(all_videos_data),
            "videos": all_videos_data,
            "elapsed_seconds": elapsed,
        }


async def main():
    try:
        res = await fetch_and_ingest_youtube_2023()
        print("\n================= HASIL FETCH YOUTUBE 2023 =================")
        print(f"Total video tahun 2023 berhasil di-ingest: {res['total_upserted']}")
        print(f"Durasi proses: {res['elapsed_seconds']} detik")
        
        videos = res["videos"]
        if videos:
            # Sort by views desc
            videos_sorted = sorted(videos, key=lambda x: x["views"], reverse=True)
            print("\n🏆 TOP 5 VIDEO TERPOPULER TAHUN 2023:")
            for idx, v in enumerate(videos_sorted[:5], 1):
                stream_info = f" (Live: {v['streamed_at'][:10]})" if v["streamed_at"] else ""
                print(f"{idx}. {v['title'][:60]}...")
                print(f"   Views: {v['views']:,} | Likes: {v['likes']:,} | Comments: {v['comments']:,} | Rilis: {v['published_at'][:10]}{stream_info}")
                print(f"   URL: {v['url']}")

            # Range date
            pub_dates = [v["published_at"][:10] for v in videos if v["published_at"]]
            if pub_dates:
                print(f"\nRentang tanggal publikasi: {min(pub_dates)} s/d {max(pub_dates)}")
            live_count = sum(1 for v in videos if v["streamed_at"])
            print(f"Video Siaran Langsung (Live): {live_count} video")
    finally:
        await close_client()


if __name__ == "__main__":
    asyncio.run(main())
