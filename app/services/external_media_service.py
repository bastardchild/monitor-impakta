import asyncio
import calendar
from datetime import datetime, timezone
import html
import json
import logging
from typing import Any, Dict, List, Optional
import urllib.request
import urllib.parse
import uuid
import re

from app.db import execute, fetch_all, fetch_one
from app.services.error_logger import ErrorCategory, ErrorCode, log_system_error
from app.services.kpi_service import format_number_id, format_date_dmy

logger = logging.getLogger("kpi-sosmed.external_media")

async def _get_deleted_post_ids() -> set:
    """Retrieve all IDs of deleted posts so they are not re-inserted on future syncs."""
    try:
        rows = await fetch_all("SELECT id FROM deleted_external_posts")
        return {r["id"] for r in rows}
    except Exception:
        return set()

TIMES_API_URL = "https://timesindonesia.co.id/api/news/all"
TIMES_API_KEY = "VT926Xevq9juBMyR2Iddjm5OZRLP"
TIMES_BASE_WEB = "https://timesindonesia.co.id"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "x-api-key": TIMES_API_KEY,
}


def _clean_html_text(raw_html: Optional[str]) -> str:
    """Strip tags and unescape html entities."""
    if not raw_html:
        return ""
    clean = re.sub(r"<[^>]+>", " ", str(raw_html))
    clean = html.unescape(clean)
    return " ".join(clean.split()).strip()


async def sync_times_indonesia(limit: int = 50, max_pages: int = 10, after_date: Optional[str] = "2025-01-01") -> Dict[str, Any]:
    """
    Fetch UNMER Malang news from TIMES Indonesia API and upsert into external_news_posts.
    Supports filtering by after_date (e.g. '2025-01-01' to fetch back to early 2025).
    """
    total_fetched = 0
    total_upserted = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    stop_early = False
    deleted_ids = await _get_deleted_post_ids()

    try:
        for page_idx in range(max_pages):
            if stop_early:
                break
            offset = page_idx * limit
            url = f"{TIMES_API_URL}?news_type=search&title=unmer+malang&limit={limit}&offset={offset}"
            req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            items = data.get("data", [])
            if not items or not isinstance(items, list):
                break

            for it in items:
                total_fetched += 1
                ext_id = str(it.get("news_id") or "").strip()
                if not ext_id:
                    continue

                raw_title = it.get("news_title") or ""
                title = _clean_html_text(raw_title)
                if not title:
                    continue

                # Relative link in url_ci4 or url_ci
                rel_url = it.get("url_ci4") or it.get("url_ci") or f"/read/news/{ext_id}"
                full_url = f"{TIMES_BASE_WEB}{rel_url}" if rel_url.startswith("/") else rel_url

                excerpt = _clean_html_text(it.get("news_description") or "")[:400]
                featured_img = it.get("news_image_new") or ""

                cat_name = (it.get("cat_title") or "Umum").strip()
                author = it.get("editor_name") or it.get("news_writer") or "Redaksi TIMES Indonesia"
                city = it.get("news_city") or "Malang"

                try:
                    pageviews = int(it.get("pageviews") or it.get("news_view") or 0)
                except (ValueError, TypeError):
                    pageviews = 0

                # Date: 'YYYY-MM-DD HH:mm:ss'
                pub_raw = it.get("news_datepub") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                pub_date = pub_raw[:10] if len(pub_raw) >= 10 else datetime.now(timezone.utc).strftime("%Y-%m-%d")

                if after_date and pub_date < after_date:
                    stop_early = True
                    break

                post_id = f"times_indonesia_{ext_id}"
                if post_id in deleted_ids:
                    continue

                await execute(
                    """
                    INSERT INTO external_news_posts (
                        id, source_id, source_name, external_id, title, url, excerpt,
                        featured_image_url, category_name, author_name, city, pageviews,
                        published_at, published_date, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        url = excluded.url,
                        excerpt = excluded.excerpt,
                        featured_image_url = excluded.featured_image_url,
                        category_name = excluded.category_name,
                        author_name = excluded.author_name,
                        city = excluded.city,
                        pageviews = excluded.pageviews,
                        published_at = excluded.published_at,
                        published_date = excluded.published_date,
                        synced_at = excluded.synced_at
                    """,
                    [
                        post_id, "times_indonesia", "TIMES Indonesia", ext_id, title, full_url,
                        excerpt, featured_img, cat_name, author, city, pageviews,
                        pub_raw, pub_date, now_iso
                    ]
                )
                total_upserted += 1

            if len(items) < limit:
                break

        return {
            "status": "success",
            "source": "times_indonesia",
            "total_fetched": total_fetched,
            "total_upserted": total_upserted,
            "synced_at": now_iso
        }
    except Exception as e:
        logger.error(f"Error syncing TIMES Indonesia news: {e}", exc_info=True)
        await log_system_error(
            category=ErrorCategory.INGEST,
            error_code=ErrorCode.INGEST_API_FAIL,
            message=f"Gagal sinkronisasi TIMES Indonesia: {str(e)}",
            exc=e,
            platform="times_indonesia"
        )
        return {
            "status": "error",
            "source": "times_indonesia",
            "error": str(e),
            "total_fetched": total_fetched,
            "total_upserted": total_upserted
        }



def _fetch_radar_data_page(url: str) -> Optional[Dict[str, Any]]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            html_text = r.read().decode("utf-8")
        m = re.search(r'data-page="([^"]+)"', html_text)
        if not m:
            m = re.search(r"data-page='([^']+)'", html_text)
        if m:
            return json.loads(html.unescape(m.group(1)))
    except Exception as e:
        logger.warning(f"Error fetching Radar Malang page {url}: {e}")
    return None


async def sync_radar_malang(after_date: Optional[str] = "2025-01-01") -> Dict[str, Any]:
    """
    Fetch UNMER Malang news from Radar Malang (Jawa Pos) and upsert into external_news_posts.
    Traverses search queries and topical tag archives.
    """
    total_fetched = 0
    total_upserted = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    seen_article_ids = set()
    deleted_ids = await _get_deleted_post_ids()

    try:
        urls_to_crawl = []
        # 1. Search queries
        for q in ["unmer malang", "unmer"]:
            for page in range(1, 4):
                urls_to_crawl.append(
                    f"https://radarmalang.jawapos.com/search?q={urllib.parse.quote(q)}&page={page}"
                )
        # 2. Tag pages
        for tag in ["unmer", "unmer-malang", "universitas-merdeka-malang", "universitas-merdeka"]:
            for page in range(1, 3):
                urls_to_crawl.append(
                    f"https://radarmalang.jawapos.com/tag/{tag}?page={page}"
                )

        for crawl_url in urls_to_crawl:
            data = await asyncio.to_thread(_fetch_radar_data_page, crawl_url)
            if not data or not isinstance(data, dict):
                continue

            news_obj = data.get("props", {}).get("news", {})
            items = news_obj.get("data", [])
            if not items or not isinstance(items, list):
                continue

            for it in items:
                art_id = str(it.get("article_id") or it.get("id") or "").strip()
                if not art_id or art_id in seen_article_ids:
                    continue
                seen_article_ids.add(art_id)
                total_fetched += 1

                raw_title = it.get("title") or ""
                title = _clean_html_text(raw_title)
                if not title:
                    continue

                post_id = f"radar_malang_{art_id}"
                if post_id in deleted_ids:
                    continue

                cat_obj = it.get("category") or {}
                cat_slug = cat_obj.get("slug") or "berita"
                cat_name = (cat_obj.get("name") or "Umum").strip()

                slug = it.get("slug") or ""
                article_id_num = it.get("article_id") or art_id
                if slug:
                    full_url = f"https://radarmalang.jawapos.com/{cat_slug}/{article_id_num}/{slug}"
                else:
                    full_url = f"https://radarmalang.jawapos.com/search?q={art_id}"

                excerpt = _clean_html_text(it.get("description") or "")[:400]
                featured_img = it.get("image") or ""
                author = "Redaksi Radar Malang"
                city = "Malang"
                pageviews = 0

                pub_raw = it.get("date") or it.get("timestamp") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                pub_date = pub_raw[:10] if len(pub_raw) >= 10 else datetime.now(timezone.utc).strftime("%Y-%m-%d")

                if after_date and pub_date < after_date:
                    continue

                await execute(
                    """
                    INSERT INTO external_news_posts (
                        id, source_id, source_name, external_id, title, url, excerpt,
                        featured_image_url, category_name, author_name, city, pageviews,
                        published_at, published_date, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        url = excluded.url,
                        excerpt = excluded.excerpt,
                        featured_image_url = excluded.featured_image_url,
                        category_name = excluded.category_name,
                        author_name = excluded.author_name,
                        city = excluded.city,
                        pageviews = excluded.pageviews,
                        published_at = excluded.published_at,
                        published_date = excluded.published_date,
                        synced_at = excluded.synced_at
                    """,
                    [
                        post_id, "radar_malang", "Radar Malang", art_id, title, full_url,
                        excerpt, featured_img, cat_name, author, city, pageviews,
                        pub_raw, pub_date, now_iso
                    ]
                )
                total_upserted += 1

            has_more = news_obj.get("paginatorInfo", {}).get("hasMorePages", False)
            if not has_more:
                pass

        return {
            "status": "success",
            "source": "radar_malang",
            "total_fetched": total_fetched,
            "total_upserted": total_upserted,
            "synced_at": now_iso
        }
    except Exception as e:
        logger.error(f"Error syncing Radar Malang news: {e}", exc_info=True)
        await log_system_error(
            category=ErrorCategory.INGEST,
            error_code=ErrorCode.INGEST_API_FAIL,
            message=f"Gagal sinkronisasi Radar Malang: {str(e)}",
            exc=e,
            platform="radar_malang"
        )
        return {
            "status": "error",
            "source": "radar_malang",
            "error": str(e),
            "total_fetched": total_fetched,
            "total_upserted": total_upserted
        }


async def sync_all_external_media(after_date: Optional[str] = "2025-01-01") -> Dict[str, Any]:
    """Sync all active external media sources (TIMES Indonesia and Radar Malang)."""
    res_times = await sync_times_indonesia(limit=50, max_pages=10, after_date=after_date)
    res_radar = await sync_radar_malang(after_date=after_date)

    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "status": "success",
        "times_indonesia": res_times,
        "radar_malang": res_radar,
        "total_upserted": (res_times.get("total_upserted", 0) or 0) + (res_radar.get("total_upserted", 0) or 0),
        "synced_at": now_iso
    }


async def create_manual_external_post(data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a manual external news record."""
    uid = uuid.uuid4().hex[:10]
    post_id = f"manual_{uid}"
    now_iso = datetime.now(timezone.utc).isoformat()

    source_name = (data.get("source_name") or "Media Lain").strip()
    source_id = re.sub(r"[^a-z0-9_]+", "_", source_name.lower()).strip("_") or "manual"

    title = _clean_html_text(data.get("title") or "")
    url = (data.get("url") or "").strip()
    excerpt = _clean_html_text(data.get("excerpt") or "")[:400]
    featured_img = (data.get("featured_image_url") or "").strip()
    category_name = (data.get("category_name") or "Umum").strip()
    author_name = (data.get("author_name") or "Redaksi").strip()
    city = (data.get("city") or "Malang").strip()

    try:
        pageviews = int(data.get("pageviews") or 0)
    except (ValueError, TypeError):
        pageviews = 0

    pub_date = (data.get("published_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")).strip()
    pub_time = (data.get("published_time") or "12:00:00").strip()
    if len(pub_time) == 5:
        pub_time += ":00"
    pub_raw = f"{pub_date} {pub_time}"

    await execute(
        """
        INSERT INTO external_news_posts (
            id, source_id, source_name, external_id, title, url, excerpt,
            featured_image_url, category_name, author_name, city, pageviews,
            published_at, published_date, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            post_id, source_id, source_name, uid, title, url, excerpt,
            featured_img, category_name, author_name, city, pageviews,
            pub_raw, pub_date, now_iso
        ]
    )
    return {"status": "success", "id": post_id}


async def delete_external_post(post_id: str) -> bool:
    """Delete an external news post by its ID and record it in deleted_external_posts."""
    now_iso = datetime.now(timezone.utc).isoformat()
    await execute(
        """
        INSERT INTO deleted_external_posts (id, deleted_at)
        VALUES (?, ?)
        ON CONFLICT(id) DO UPDATE SET deleted_at = excluded.deleted_at
        """,
        [post_id, now_iso]
    )
    await execute("DELETE FROM external_news_posts WHERE id = ?", [post_id])
    return True


async def get_external_media_summary() -> Dict[str, Any]:
    """Retrieve statistical summary for all external media news."""
    # 1. Total Posts & Total Views
    row = await fetch_one(
        """
        SELECT 
            COUNT(id) as total_posts,
            COALESCE(SUM(pageviews), 0) as total_views
        FROM external_news_posts
        """
    )
    total_posts = int(row["total_posts"]) if row and row.get("total_posts") is not None else 0
    total_views = int(row["total_views"]) if row and row.get("total_views") is not None else 0

    # 2. Total by Source
    source_rows = await fetch_all(
        """
        SELECT source_id, source_name, COUNT(id) as count, COALESCE(SUM(pageviews), 0) as views
        FROM external_news_posts
        GROUP BY source_id, source_name
        ORDER BY count DESC
        """
    )
    sources = [
        {
            "source_id": r["source_id"],
            "source_name": r["source_name"],
            "count": r["count"],
            "views": r["views"],
            "views_formatted": format_number_id(r["views"])
        }
        for r in source_rows
    ]

    # 3. Last Synced
    last_sync_row = await fetch_one(
        "SELECT MAX(synced_at) as last_synced FROM external_news_posts"
    )
    last_synced = last_sync_row.get("last_synced") if last_sync_row else None
    last_synced_formatted = format_date_dmy(last_synced) if last_synced else "Belum Sinkron"

    # 4. Top Category
    cat_rows = await fetch_all(
        """
        SELECT category_name, COUNT(id) as count
        FROM external_news_posts
        GROUP BY category_name
        ORDER BY count DESC
        """
    )

    return {
        "total_posts": total_posts,
        "total_views": total_views,
        "total_views_formatted": format_number_id(total_views),
        "total_sources": len(sources),
        "sources": sources,
        "categories": cat_rows,
        "last_synced": last_synced,
        "last_synced_formatted": last_synced_formatted
    }


async def get_external_news_posts(
    source_id: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 20,
    page: int = 1,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    month_filter: Optional[str] = None,
    sort_by: str = "date"
) -> Dict[str, Any]:
    """Retrieve external media news with filtering and pagination."""
    limit = int(limit)
    if limit not in (20, 50, 100):
        limit = 20
    page = max(1, int(page))
    offset = (page - 1) * limit

    where_conditions = ["1=1"]
    params: List[Any] = []

    if source_id and source_id != "all":
        where_conditions.append("source_id = ?")
        params.append(source_id)

    if category and category != "all":
        where_conditions.append("category_name = ?")
        params.append(category)

    if search and search.strip():
        term = f"%{search.strip()}%"
        where_conditions.append("(title LIKE ? OR excerpt LIKE ? OR author_name LIKE ?)")
        params.extend([term, term, term])

    # Date normalization
    clean_start = start_date.strip() if start_date else None
    clean_end = end_date.strip() if end_date else None

    # Handle month dropdown
    if month_filter and month_filter != "all" and not clean_start and not clean_end:
        try:
            yr, mo = month_filter.split("-")
            yr_int, mo_int = int(yr), int(mo)
            _, last_day = calendar.monthrange(yr_int, mo_int)
            clean_start = f"{yr_int:04d}-{mo_int:02d}-01"
            clean_end = f"{yr_int:04d}-{mo_int:02d}-{last_day:02d}"
        except Exception:
            pass

    if clean_start and not clean_end:
        clean_end = clean_start

    if clean_start and clean_end:
        where_conditions.append("published_date >= ? AND published_date <= ?")
        params.extend([clean_start, clean_end])

    where_sql = " AND ".join(where_conditions)

    # Order clause
    if sort_by == "views":
        order_clause = "pageviews DESC, published_at DESC"
    else:
        order_clause = "published_at DESC"

    # Count
    count_sql = f"SELECT COUNT(id) as total FROM external_news_posts WHERE {where_sql}"
    count_row = await fetch_one(count_sql, params)
    total_count = int(count_row["total"]) if count_row and count_row.get("total") is not None else 0
    total_pages = max(1, (total_count + limit - 1) // limit) if total_count > 0 else 1

    # Fetch rows
    data_sql = f"""
        SELECT 
            id, source_id, source_name, external_id, title, url, excerpt,
            featured_image_url, category_name, author_name, city, pageviews,
            published_at, published_date
        FROM external_news_posts
        WHERE {where_sql}
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
    """
    rows = await fetch_all(data_sql, params + [limit, offset])

    for r in rows:
        r["pageviews_formatted"] = format_number_id(r.get("pageviews", 0))
        # Format published_at Indonesian style
        pub = r.get("published_at") or r.get("published_date") or ""
        r["published_at_formatted"] = format_date_dmy(pub)
        if len(pub) >= 16:
            r["published_time"] = pub[11:16] + " WIB"
        else:
            r["published_time"] = ""

    # Monthly archive breakdown for dropdown
    monthly_sql = """
        SELECT SUBSTR(published_date, 1, 7) as ym, COUNT(id) as count
        FROM external_news_posts
        GROUP BY ym
        ORDER BY ym DESC
    """
    monthly_rows = await fetch_all(monthly_sql)
    month_names_id = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
    archive_months = []
    for m in monthly_rows:
        ym = m["ym"]
        if ym and "-" in ym:
            try:
                y, mo = ym.split("-")
                name = f"{month_names_id[int(mo)]} {y}"
                archive_months.append({"ym": ym, "label": name, "count": m["count"]})
            except Exception:
                pass

    return {
        "posts": rows,
        "total_count": total_count,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if page < total_pages else None,
        "source_id": source_id or "all",
        "category": category or "all",
        "search": search.strip() if search else "",
        "start_date": clean_start or "",
        "end_date": clean_end or "",
        "month_filter": month_filter or "",
        "sort_by": sort_by,
        "archive_months": archive_months,
        "start_idx": offset + 1 if total_count > 0 else 0,
        "end_idx": min(offset + limit, total_count)
    }
