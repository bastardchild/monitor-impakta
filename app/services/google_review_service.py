"""
Service untuk sinkronisasi dan query Google Maps Reviews (via SerpAPI).
Default lokasi: Universitas Merdeka Malang (Place ID: ChIJj7qtWSso1i0RvE-S5MXstO0).
"""

from datetime import datetime, timedelta, timezone
import json
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import get_settings
from app.db import batch, fetch_all, fetch_one

logger = logging.getLogger("kpi-sosmed.google_reviews")

SERPAPI_URL = "https://serpapi.com/search.json"
DEFAULT_PLACE_ID = "ChIJj7qtWSso1i0RvE-S5MXstO0"
DEFAULT_LOCATION_NAME = "Universitas Merdeka Malang"
WIB_TZ = timezone(timedelta(hours=7))


def derive_sentiment_from_rating(rating: int) -> str:
    """Turunkan label sentimen dari rating bintang Google Maps (1-5)."""
    if rating >= 4:
        return "positive"
    if rating == 3:
        return "neutral"
    return "negative"


def _parse_iso_datetime(iso_str: Optional[str]) -> Optional[datetime]:
    """Parse string ISO 8601 (mis. '2026-11-01T08:30:00Z') menjadi datetime UTC."""
    if not iso_str:
        return None
    cleaned = str(iso_str).strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _parse_review_item(
    item: Dict[str, Any],
    place_id: str,
    synced_at_str: str,
) -> Optional[Tuple[Dict[str, Any], Optional[datetime]]]:
    """Ekstrak dan normalisasi satu objek ulasan dari respons SerpAPI."""
    review_id = str(item.get("review_id") or "").strip()
    if not review_id:
        return None

    user_info = item.get("user") if isinstance(item.get("user"), dict) else {}
    author_name = str(user_info.get("name") or "Pengguna Google").strip()

    try:
        rating = int(float(item.get("rating") or 0))
    except (TypeError, ValueError):
        rating = 0
    rating = max(1, min(5, rating)) if rating > 0 else 1

    extracted = item.get("extracted_snippet") if isinstance(item.get("extracted_snippet"), dict) else {}
    snippet = str(item.get("snippet") or extracted.get("original") or "").strip()

    dt_utc = _parse_iso_datetime(item.get("iso_date")) or _parse_iso_datetime(
        item.get("iso_date_of_last_edit")
    )
    dt_wib = (dt_utc or datetime.now(timezone.utc)).astimezone(WIB_TZ)

    response_info = item.get("response") if isinstance(item.get("response"), dict) else {}
    owner_reply = str(response_info.get("snippet") or "").strip()

    record = {
        "review_id": review_id,
        "place_id": place_id,
        "author_name": author_name,
        "rating": rating,
        "snippet": snippet,
        "sentiment": derive_sentiment_from_rating(rating),
        "published_at": dt_wib.strftime("%Y-%m-%d %H:%M:%S"),
        "published_date": dt_wib.strftime("%Y-%m-%d"),
        "relative_date": str(item.get("date") or "").strip(),
        "review_link": str(item.get("link") or "").strip(),
        "owner_reply": owner_reply,
        "raw_json": json.dumps(item, ensure_ascii=False),
        "synced_at": synced_at_str,
    }
    return record, dt_utc


async def _get_existing_review_ids(place_id: str) -> set[str]:
    rows = await fetch_all(
        "SELECT review_id FROM google_reviews WHERE place_id = ?",
        [place_id],
    )
    return {str(r["review_id"]) for r in rows}


async def _upsert_reviews(records: List[Dict[str, Any]]) -> None:
    if not records:
        return
    sql = """
    INSERT INTO google_reviews (
        review_id, place_id, author_name, rating, snippet, sentiment,
        published_at, published_date, relative_date, review_link,
        owner_reply, raw_json, synced_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(review_id) DO UPDATE SET
        author_name = excluded.author_name,
        rating = excluded.rating,
        snippet = excluded.snippet,
        sentiment = excluded.sentiment,
        published_at = excluded.published_at,
        published_date = excluded.published_date,
        relative_date = excluded.relative_date,
        review_link = excluded.review_link,
        owner_reply = excluded.owner_reply,
        raw_json = excluded.raw_json,
        synced_at = excluded.synced_at
    """
    statements = [
        (
            sql,
            [
                r["review_id"],
                r["place_id"],
                r["author_name"],
                r["rating"],
                r["snippet"],
                r["sentiment"],
                r["published_at"],
                r["published_date"],
                r["relative_date"],
                r["review_link"],
                r["owner_reply"],
                r["raw_json"],
                r["synced_at"],
            ],
        )
        for r in records
    ]
    for i in range(0, len(statements), 50):
        await batch(statements[i : i + 50])


async def sync_google_reviews(
    days: int = 14,
    max_pages: int = 10,
    place_id: Optional[str] = None,
    stop_on_existing: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Ambil ulasan Google Maps dari SerpAPI (`sort_by=newestFirst`) untuk `days` hari terakhir.
    - Tombol Sync UI memanggil dengan `days=14` (2 minggu terakhir) & `stop_on_existing=True`.
    - Script backfill awal memanggil dengan `days=365` (1 tahun terakhir) & `stop_on_existing=False`.
    """
    settings = get_settings()
    api_key = (settings.SERPAPI_KEY or "").strip()
    target_place_id = (place_id or settings.GOOGLE_MAPS_PLACE_ID or DEFAULT_PLACE_ID).strip()

    if not api_key:
        return {
            "status": "error",
            "error": "SERPAPI_KEY belum dikonfigurasi di environment (.env).",
            "place_id": target_place_id,
            "days": days,
            "pages_fetched": 0,
            "total_fetched": 0,
            "total_upserted": 0,
            "new_saved": 0,
            "updated_saved": 0,
            "skipped_older": 0,
        }

    now_utc = datetime.now(timezone.utc)
    cutoff_utc = now_utc - timedelta(days=max(1, days))
    synced_at_str = now_utc.astimezone(WIB_TZ).strftime("%Y-%m-%d %H:%M:%S")

    existing_ids = set() if dry_run else await _get_existing_review_ids(target_place_id)

    pages_fetched = 0
    total_fetched = 0
    new_saved = 0
    updated_saved = 0
    skipped_older = 0
    stopped_reason = "completed"
    next_page_token: Optional[str] = None
    all_records_to_upsert: List[Dict[str, Any]] = []
    seen_in_run: set[str] = set()

    async with httpx.AsyncClient(timeout=45.0) as client:
        for _ in range(max(1, max_pages)):
            params: Dict[str, Any] = {
                "engine": "google_maps_reviews",
                "place_id": target_place_id,
                "api_key": api_key,
                "hl": "id",
                "sort_by": "newestFirst",
            }
            if next_page_token:
                params["next_page_token"] = next_page_token

            try:
                resp = await client.get(SERPAPI_URL, params=params)
            except httpx.HTTPError as exc:
                logger.exception("SerpAPI HTTP error saat fetch Google Reviews")
                return {
                    "status": "error",
                    "error": f"Gagal menghubungi SerpAPI: {exc}",
                    "place_id": target_place_id,
                    "days": days,
                    "pages_fetched": pages_fetched,
                    "total_fetched": total_fetched,
                    "total_upserted": 0,
                    "new_saved": 0,
                    "updated_saved": 0,
                    "skipped_older": skipped_older,
                }

            if resp.status_code != 200:
                try:
                    err_msg = resp.json().get("error", resp.text)
                except Exception:
                    err_msg = resp.text
                return {
                    "status": "error",
                    "error": f"SerpAPI Error ({resp.status_code}): {err_msg}",
                    "place_id": target_place_id,
                    "days": days,
                    "pages_fetched": pages_fetched,
                    "total_fetched": total_fetched,
                    "total_upserted": 0,
                    "new_saved": 0,
                    "updated_saved": 0,
                    "skipped_older": skipped_older,
                }

            data = resp.json()
            pages_fetched += 1
            reviews = data.get("reviews") or []
            if not reviews:
                stopped_reason = "no_more_reviews"
                break

            total_fetched += len(reviews)
            page_has_new = False
            reached_cutoff = False

            for item in reviews:
                parsed = _parse_review_item(item, target_place_id, synced_at_str)
                if not parsed:
                    continue
                record, dt_utc = parsed

                if dt_utc is not None and dt_utc < cutoff_utc:
                    skipped_older += 1
                    reached_cutoff = True
                    continue

                rid = record["review_id"]
                if rid in seen_in_run:
                    continue
                seen_in_run.add(rid)

                if rid in existing_ids:
                    updated_saved += 1
                else:
                    new_saved += 1
                    page_has_new = True

                all_records_to_upsert.append(record)

            if reached_cutoff:
                stopped_reason = "reached_date_cutoff"
                break

            if stop_on_existing and not page_has_new:
                stopped_reason = "reached_existing_reviews"
                break

            pagination = data.get("serpapi_pagination") or {}
            next_page_token = pagination.get("next_page_token")
            if not next_page_token:
                stopped_reason = "end_of_pagination"
                break
        else:
            stopped_reason = "reached_max_pages"

    if not dry_run and all_records_to_upsert:
        await _upsert_reviews(all_records_to_upsert)

    return {
        "status": "ok",
        "place_id": target_place_id,
        "days": days,
        "dry_run": dry_run,
        "pages_fetched": pages_fetched,
        "total_fetched": total_fetched,
        "total_upserted": len(all_records_to_upsert),
        "new_saved": new_saved,
        "updated_saved": updated_saved,
        "skipped_older": skipped_older,
        "stopped_reason": stopped_reason,
    }


async def get_google_reviews_summary(place_id: Optional[str] = None) -> Dict[str, Any]:
    """Ambil ringkasan metrik Google Reviews yang tersimpan di database."""
    settings = get_settings()
    target_place_id = (place_id or settings.GOOGLE_MAPS_PLACE_ID or DEFAULT_PLACE_ID).strip()

    row = await fetch_one(
        """
        SELECT
            COUNT(*) AS total_reviews,
            COALESCE(ROUND(AVG(rating), 2), 0) AS avg_rating,
            SUM(CASE WHEN sentiment = 'positive' THEN 1 ELSE 0 END) AS positive_count,
            SUM(CASE WHEN sentiment = 'neutral' THEN 1 ELSE 0 END) AS neutral_count,
            SUM(CASE WHEN sentiment = 'negative' THEN 1 ELSE 0 END) AS negative_count,
            SUM(CASE WHEN rating = 5 THEN 1 ELSE 0 END) AS star_5,
            SUM(CASE WHEN rating = 4 THEN 1 ELSE 0 END) AS star_4,
            SUM(CASE WHEN rating = 3 THEN 1 ELSE 0 END) AS star_3,
            SUM(CASE WHEN rating = 2 THEN 1 ELSE 0 END) AS star_2,
            SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) AS star_1,
            MAX(published_date) AS latest_date,
            MIN(published_date) AS oldest_date,
            MAX(synced_at) AS last_synced_at
        FROM google_reviews
        WHERE place_id = ?
        """,
        [target_place_id],
    )

    total = int((row or {}).get("total_reviews") or 0)
    return {
        "place_id": target_place_id,
        "location_name": DEFAULT_LOCATION_NAME,
        "total_reviews": total,
        "avg_rating": float((row or {}).get("avg_rating") or 0.0),
        "positive_count": int((row or {}).get("positive_count") or 0),
        "neutral_count": int((row or {}).get("neutral_count") or 0),
        "negative_count": int((row or {}).get("negative_count") or 0),
        "stars": {
            5: int((row or {}).get("star_5") or 0),
            4: int((row or {}).get("star_4") or 0),
            3: int((row or {}).get("star_3") or 0),
            2: int((row or {}).get("star_2") or 0),
            1: int((row or {}).get("star_1") or 0),
        },
        "latest_date": (row or {}).get("latest_date"),
        "oldest_date": (row or {}).get("oldest_date"),
        "last_synced_at": (row or {}).get("last_synced_at"),
    }


async def get_google_reviews(
    rating: Optional[str] = "all",
    sentiment: Optional[str] = "all",
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sort_by: str = "newest",
    page: int = 1,
    limit: int = 20,
    place_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Query daftar ulasan Google Maps dengan filter, pencarian, dan pagination."""
    settings = get_settings()
    target_place_id = (place_id or settings.GOOGLE_MAPS_PLACE_ID or DEFAULT_PLACE_ID).strip()

    conditions = ["place_id = ?"]
    params: List[Any] = [target_place_id]

    if rating and rating != "all":
        try:
            rating_int = int(rating)
            if 1 <= rating_int <= 5:
                conditions.append("rating = ?")
                params.append(rating_int)
        except ValueError:
            pass

    if sentiment and sentiment in {"positive", "neutral", "negative"}:
        conditions.append("sentiment = ?")
        params.append(sentiment)

    if start_date:
        conditions.append("published_date >= ?")
        params.append(start_date)

    if end_date:
        conditions.append("published_date <= ?")
        params.append(end_date)

    if search and search.strip():
        kw = f"%{search.strip()}%"
        conditions.append("(author_name LIKE ? OR snippet LIKE ? OR owner_reply LIKE ?)")
        params.extend([kw, kw, kw])

    where_sql = " AND ".join(conditions)

    order_map = {
        "newest": "published_at DESC, review_id DESC",
        "oldest": "published_at ASC, review_id ASC",
        "highest": "rating DESC, published_at DESC",
        "lowest": "rating ASC, published_at DESC",
    }
    order_sql = order_map.get(sort_by, order_map["newest"])

    count_row = await fetch_one(
        f"SELECT COUNT(*) AS cnt FROM google_reviews WHERE {where_sql}",
        params,
    )
    total_count = int((count_row or {}).get("cnt") or 0)

    safe_limit = max(1, min(100, int(limit or 20)))
    total_pages = max(1, math.ceil(total_count / safe_limit))
    safe_page = max(1, min(int(page or 1), total_pages))
    offset = (safe_page - 1) * safe_limit

    rows = await fetch_all(
        f"""
        SELECT
            review_id, place_id, author_name, rating, snippet, sentiment,
            published_at, published_date, relative_date, review_link,
            owner_reply, synced_at
        FROM google_reviews
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        [*params, safe_limit, offset],
    )

    return {
        "reviews": rows,
        "total_count": total_count,
        "page": safe_page,
        "limit": safe_limit,
        "total_pages": total_pages,
        "rating": rating or "all",
        "sentiment": sentiment or "all",
        "search": search or "",
        "start_date": start_date or "",
        "end_date": end_date or "",
        "sort_by": sort_by or "newest",
    }
