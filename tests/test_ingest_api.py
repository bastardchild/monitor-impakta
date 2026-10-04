import pytest
from app.db import fetch_all, fetch_one


@pytest.mark.asyncio
async def test_ingest_account_metrics_idempotent(async_client):
    headers = {"X-API-Key": "test_ingest_key_valid_123"}
    payload = {
        "metrics": [
            {
                "platform_id": "youtube",
                "date": "2026-10-01",
                "followers": 15000,
                "views": 4500,
                "reach": 4000,
                "impressions": 8000,
                "watch_time_sec": 45000.0,
                "engagement_count": 350
            }
        ]
    }

    # First ingest
    resp1 = await async_client.post("/api/ingest/account-metrics", json=payload, headers=headers)
    assert resp1.status_code == 200
    assert resp1.json()["rows_upserted"] == 1

    row1 = await fetch_one("SELECT * FROM account_daily_metrics WHERE platform_id = 'youtube' AND date = '2026-10-01'")
    assert row1["views"] == 4500
    assert row1["followers"] == 15000

    # Second ingest with updated views for the same date (Idempotency check)
    payload["metrics"][0]["views"] = 6200
    payload["metrics"][0]["followers"] = 15020
    resp2 = await async_client.post("/api/ingest/account-metrics", json=payload, headers=headers)
    assert resp2.status_code == 200

    row2 = await fetch_one("SELECT * FROM account_daily_metrics WHERE platform_id = 'youtube' AND date = '2026-10-01'")
    assert row2["views"] == 6200
    assert row2["followers"] == 15020


@pytest.mark.asyncio
async def test_ingest_posts_and_post_metrics(async_client):
    headers = {"X-API-Key": "test_ingest_key_valid_123"}
    posts_payload = {
        "posts": [
            {
                "id": "yt_vid_test_101",
                "platform_id": "youtube",
                "external_id": "vid_test_101",
                "title": "Tutorial FastAPI + HTMX Lengkap",
                "url": "https://youtube.com/watch?v=101",
                "thumbnail_url": "https://example.com/thumb.jpg",
                "post_type": "video",
                "published_at": "2026-10-02"
            }
        ]
    }

    resp_posts = await async_client.post("/api/ingest/posts", json=posts_payload, headers=headers)
    assert resp_posts.status_code == 200

    post_row = await fetch_one("SELECT * FROM posts WHERE external_id = 'vid_test_101'")
    assert post_row is not None
    assert post_row["title"] == "Tutorial FastAPI + HTMX Lengkap"

    # Ingest metrics for that post
    metrics_payload = {
        "metrics": [
            {
                "external_id": "vid_test_101",
                "date": "2026-10-02",
                "views": 1250,
                "likes": 98,
                "comments": 15,
                "shares": 10,
                "saves": 25,
                "avg_watch_sec": 145.5
            }
        ]
    }

    resp_metrics = await async_client.post("/api/ingest/post-metrics", json=metrics_payload, headers=headers)
    assert resp_metrics.status_code == 200

    metric_row = await fetch_one("SELECT * FROM post_daily_metrics WHERE post_id = ?", [post_row["id"]])
    assert metric_row is not None
    assert metric_row["views"] == 1250
    assert metric_row["likes"] == 98
