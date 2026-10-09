from datetime import datetime, timezone
import logging
import uuid
from typing import List
from app.db import execute, fetch_one
from app.models.schemas import AccountMetricsItem, PostItem, PostMetricsItem, SentimentItem
from app.services.error_logger import ErrorCategory, ErrorCode, log_system_error

logger = logging.getLogger(__name__)


async def log_ingest(source: str, status: str, rows_upserted: int = 0, error: str = "") -> None:
    now_str = datetime.now(timezone.utc).isoformat()
    log_id = str(uuid.uuid4())
    await execute(
        """
        INSERT INTO ingest_logs (id, source, status, rows_upserted, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [log_id, source, status, rows_upserted, error or None, now_str]
    )


async def ingest_account_metrics(items: List[AccountMetricsItem]) -> int:
    """Upsert account daily metrics idempotently."""
    rows = 0
    try:
        for item in items:
            await execute(
                """
                INSERT INTO account_daily_metrics (
                    platform_id, date, followers, views, reach, impressions, watch_time_sec, engagement_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform_id, date) DO UPDATE SET
                    followers = excluded.followers,
                    views = excluded.views,
                    reach = excluded.reach,
                    impressions = excluded.impressions,
                    watch_time_sec = excluded.watch_time_sec,
                    engagement_count = excluded.engagement_count
                """,
                [
                    item.platform_id, item.date, item.followers, item.views,
                    item.reach, item.impressions, item.watch_time_sec, item.engagement_count
                ]
            )
            rows += 1
        await log_ingest("account_metrics", "success", rows_upserted=rows)
        return rows
    except Exception as e:
        logger.error(f"Error ingesting account metrics: {e}")
        await log_ingest("account_metrics", "error", rows_upserted=rows, error=str(e))
        await log_system_error(
            category=ErrorCategory.INGEST,
            error_code=ErrorCode.INGEST_DB_ERROR,
            message=f"Error ingesting account metrics: {str(e)}",
            exc=e,
        )
        raise


async def ingest_posts(items: List[PostItem]) -> int:
    """Upsert posts idempotently."""
    rows = 0
    try:
        for item in items:
            post_id = item.id or f"{item.platform_id}_{item.external_id}"
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
                [
                    post_id, item.platform_id, item.external_id, item.title,
                    item.url, item.thumbnail_url, item.post_type, item.published_at,
                    item.streamed_at, item.privacy_status or "public"
                ]
            )
            rows += 1
        await log_ingest("posts", "success", rows_upserted=rows)
        return rows
    except Exception as e:
        logger.error(f"Error ingesting posts: {e}")
        await log_ingest("posts", "error", rows_upserted=rows, error=str(e))
        await log_system_error(
            category=ErrorCategory.INGEST,
            error_code=ErrorCode.INGEST_DB_ERROR,
            message=f"Error ingesting posts: {str(e)}",
            exc=e,
        )
        raise


async def ingest_post_metrics(items: List[PostMetricsItem]) -> int:
    """Upsert post metrics idempotently."""
    rows = 0
    try:
        for item in items:
            # Resolve target post id
            target_post_id = item.post_id
            if item.external_id:
                post = await fetch_one("SELECT id FROM posts WHERE external_id = ?", [item.external_id])
                if post:
                    target_post_id = post["id"]
                elif not target_post_id:
                    target_post_id = item.external_id

            if not target_post_id:
                continue

            await execute(
                """
                INSERT INTO post_daily_metrics (
                    post_id, date, views, likes, comments, shares, saves, avg_watch_sec
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_id, date) DO UPDATE SET
                    views = excluded.views,
                    likes = excluded.likes,
                    comments = excluded.comments,
                    shares = excluded.shares,
                    saves = excluded.saves,
                    avg_watch_sec = excluded.avg_watch_sec
                """,
                [
                    target_post_id, item.date, item.views, item.likes,
                    item.comments, item.shares, item.saves, item.avg_watch_sec
                ]
            )
            rows += 1
        await log_ingest("post_metrics", "success", rows_upserted=rows)
        return rows
    except Exception as e:
        logger.error(f"Error ingesting post metrics: {e}")
        await log_ingest("post_metrics", "error", rows_upserted=rows, error=str(e))
        await log_system_error(
            category=ErrorCategory.INGEST,
            error_code=ErrorCode.INGEST_DB_ERROR,
            message=f"Error ingesting post metrics: {str(e)}",
            exc=e,
        )
        raise


async def ingest_sentiment_metrics(items: List[SentimentItem]) -> int:
    """Upsert sentiment daily metrics idempotently."""
    rows = 0
    try:
        for item in items:
            await execute(
                """
                INSERT INTO sentiment_daily_metrics (
                    platform_id, date, positive_count, neutral_count, negative_count,
                    tone_enthusiastic, tone_informative, tone_curious, tone_critical,
                    dominant_tone, sample_positive_quote, sample_negative_quote
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform_id, date) DO UPDATE SET
                    positive_count = excluded.positive_count,
                    neutral_count = excluded.neutral_count,
                    negative_count = excluded.negative_count,
                    tone_enthusiastic = excluded.tone_enthusiastic,
                    tone_informative = excluded.tone_informative,
                    tone_curious = excluded.tone_curious,
                    tone_critical = excluded.tone_critical,
                    dominant_tone = excluded.dominant_tone,
                    sample_positive_quote = COALESCE(excluded.sample_positive_quote, sentiment_daily_metrics.sample_positive_quote),
                    sample_negative_quote = COALESCE(excluded.sample_negative_quote, sentiment_daily_metrics.sample_negative_quote)
                """,
                [
                    item.platform_id, item.date, item.positive_count, item.neutral_count, item.negative_count,
                    item.tone_enthusiastic, item.tone_informative, item.tone_curious, item.tone_critical,
                    item.dominant_tone, item.sample_positive_quote, item.sample_negative_quote
                ]
            )
            rows += 1
        await log_ingest("sentiment_metrics", "success", rows_upserted=rows)
        return rows
    except Exception as e:
        logger.error(f"Error ingesting sentiment metrics: {e}")
        await log_ingest("sentiment_metrics", "error", rows_upserted=rows, error=str(e))
        await log_system_error(
            category=ErrorCategory.INGEST,
            error_code=ErrorCode.INGEST_DB_ERROR,
            message=f"Error ingesting sentiment metrics: {str(e)}",
            exc=e,
        )
        raise
