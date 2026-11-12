from datetime import datetime, timedelta, timezone
import httpx
import pytest
import respx

from app.routes.auth import require_auth
from app.config import get_settings
from app.db import execute
from app.main import app
from app.services.google_review_service import (
    DEFAULT_PLACE_ID,
    SERPAPI_URL,
    derive_sentiment_from_rating,
    get_google_reviews,
    get_google_reviews_summary,
    sync_google_reviews,
)


def test_derive_sentiment_from_rating():
    assert derive_sentiment_from_rating(5) == "positive"
    assert derive_sentiment_from_rating(4) == "positive"
    assert derive_sentiment_from_rating(3) == "neutral"
    assert derive_sentiment_from_rating(2) == "negative"
    assert derive_sentiment_from_rating(1) == "negative"


@pytest.mark.asyncio
async def test_sync_google_reviews_pagination_cutoff_and_dedup(monkeypatch):
    await execute("DELETE FROM google_reviews")
    monkeypatch.setenv("SERPAPI_KEY", "test-serpapi-key")
    get_settings.cache_clear()

    now_utc = datetime.now(timezone.utc)
    recent_1 = (now_utc - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent_2 = (now_utc - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_beyond_14d = (now_utc - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    page_1_payload = {
        "reviews": [
            {
                "review_id": "rev_1",
                "rating": 5,
                "date": "2 hari lalu",
                "iso_date": recent_1,
                "snippet": "Kampus nyaman dan bersih",
                "link": "https://maps.google.com/?cid=1",
                "user": {"name": "Budi Santoso"},
                "response": {"snippet": "Terima kasih atas ulasannya."},
            }
        ],
        "serpapi_pagination": {"next_page_token": "tok_page_2"},
    }
    page_2_payload = {
        "reviews": [
            {
                "review_id": "rev_2",
                "rating": 3,
                "date": "10 hari lalu",
                "iso_date": recent_2,
                "snippet": "Parkiran cukup ramai",
                "link": "https://maps.google.com/?cid=2",
                "user": {"name": "Siti Aminah"},
            },
            {
                "review_id": "rev_old",
                "rating": 1,
                "date": "1 bulan lalu",
                "iso_date": old_beyond_14d,
                "snippet": "Ulasan lama di luar 14 hari",
                "user": {"name": "Andi"},
            },
        ]
    }

    with respx.mock(assert_all_called=True) as mock:
        mock.get(SERPAPI_URL).mock(
            side_effect=[
                httpx.Response(200, json=page_1_payload),
                httpx.Response(200, json=page_2_payload),
            ]
        )

        res = await sync_google_reviews(days=14, max_pages=5, stop_on_existing=True)

    assert res["status"] == "ok"
    assert res["pages_fetched"] == 2
    assert res["total_upserted"] == 2
    assert res["new_saved"] == 2
    assert res["skipped_older"] == 1
    assert res["stopped_reason"] == "reached_date_cutoff"

    # Re-sync with updated snippet for rev_1 -> must update without duplicating
    page_1_updated = {
        "reviews": [
            {
                "review_id": "rev_1",
                "rating": 4,
                "date": "2 hari lalu",
                "iso_date": recent_1,
                "snippet": "Kampus nyaman dan bersih (edited)",
                "user": {"name": "Budi Santoso"},
            }
        ]
    }
    with respx.mock(assert_all_called=True) as mock:
        mock.get(SERPAPI_URL).mock(return_value=httpx.Response(200, json=page_1_updated))
        res_2 = await sync_google_reviews(days=14, max_pages=5, stop_on_existing=True)

    assert res_2["status"] == "ok"
    assert res_2["new_saved"] == 0
    assert res_2["updated_saved"] == 1

    summary = await get_google_reviews_summary(DEFAULT_PLACE_ID)
    assert summary["total_reviews"] == 2
    assert summary["positive_count"] == 1
    assert summary["neutral_count"] == 1
    assert summary["avg_rating"] == 3.5

    filtered = await get_google_reviews(search="edited")
    assert filtered["total_count"] == 1
    assert filtered["reviews"][0]["rating"] == 4


@pytest.mark.asyncio
async def test_google_reviews_routes(async_client, monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "test-serpapi-key")
    get_settings.cache_clear()

    app.dependency_overrides[require_auth] = lambda: "admin"
    try:
        resp = await async_client.get("/google-reviews")
        assert resp.status_code == 200
        assert "Monitoring Google Reviews" in resp.text
        assert "Sync 2 Minggu" in resp.text

        part_resp = await async_client.get("/partials/google-reviews/list?sentiment=positive")
        assert part_resp.status_code == 200

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with respx.mock(assert_all_called=True) as mock:
            mock.get(SERPAPI_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "reviews": [
                            {
                                "review_id": "rev_route_1",
                                "rating": 5,
                                "date": "baru saja",
                                "iso_date": now_iso,
                                "snippet": "Pelayanan akademik sangat baik",
                                "user": {"name": "Dewi"},
                            }
                        ]
                    },
                )
            )
            sync_resp = await async_client.post("/partials/google-reviews/sync")

        assert sync_resp.status_code == 200
        assert "Sync 2 minggu terakhir selesai!" in sync_resp.text
        assert "Pelayanan akademik sangat baik" in sync_resp.text
    finally:
        app.dependency_overrides.clear()
