import asyncio
import calendar
from datetime import datetime, timezone
import html
import logging
import re
from typing import Any, Dict, List, Optional
import urllib.request
import json
from app.db import execute, fetch_all, fetch_one
from app.services.error_logger import ErrorCategory, ErrorCode, log_system_error
from app.services.kpi_service import format_date_dmy, format_number_id

logger = logging.getLogger("kpi-sosmed.unmer")

UNMER_WP_BASE = "https://unmer.ac.id/wp-json/wp/v2"
CATEGORY_BERITA_ID = 487
CATEGORY_ARTIKEL_ID = 919

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _clean_html_text(raw_html: Optional[str]) -> str:
    """Strip tags and unescape html entities."""
    if not raw_html:
        return ""
    import re
    # Remove HTML tags
    clean = re.sub(r"<[^>]+>", " ", raw_html)
    # Decode entities like &amp;, &#8211;, &quot;
    clean = html.unescape(clean)
    # Collapse multiple whitespaces
    return " ".join(clean.split()).strip()


def _fetch_unmer_views_sync() -> Dict[int, int]:
    """Crawl unmer.ac.id category archive pages to extract public post views from ThemeREX HTML."""
    views_map: Dict[int, int] = {}
    pattern = r'id="post-(\d+)"[\s\S]*?post_meta_views[\s\S]*?post_meta_number[^>]*>(\d+)'
    
    for cat in ["berita", "artikel"]:
        for page in range(1, 6):
            url = f"https://unmer.ac.id/category/{cat}/page/{page}/" if page > 1 else f"https://unmer.ac.id/category/{cat}/"
            try:
                req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
                with urllib.request.urlopen(req, timeout=12) as r:
                    html_text = r.read().decode("utf-8")
                matches = re.findall(pattern, html_text)
                if not matches:
                    break
                for pid_str, v_str in matches:
                    try:
                        pid = int(pid_str)
                        views_map[pid] = int(v_str)
                    except ValueError:
                        pass
            except Exception as e:
                logger.debug(f"Stop crawling category {cat} page {page}: {e}")
                break
    return views_map


async def sync_unmer_posts(max_pages: int = 5, per_page: int = 100, after: Optional[str] = "2026-01-01T00:00:00") -> Dict[str, Any]:
    """
    Fetch posts from unmer.ac.id WordPress REST API for categories Berita (487) and Artikel (919)
    and store/upsert them into Turso/LibSQL database. Supports year/date filtering.
    """
    total_fetched = 0
    total_upserted = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    views_map = await asyncio.to_thread(_fetch_unmer_views_sync)

    for page in range(1, max_pages + 1):
        url = f"{UNMER_WP_BASE}/posts?categories={CATEGORY_BERITA_ID},{CATEGORY_ARTIKEL_ID}&per_page={per_page}&page={page}&_embed=1"
        if after:
            url += f"&after={after}"
        try:
            req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
            # Execute with timeout
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if not data or not isinstance(data, list):
                break

            for p in data:
                total_fetched += 1
                post_id = p.get("id")
                slug = p.get("slug", "")
                raw_title = p.get("title", {}).get("rendered", "")
                title = _clean_html_text(raw_title)
                link = p.get("link", "")
                
                raw_excerpt = p.get("excerpt", {}).get("rendered", "")
                excerpt = _clean_html_text(raw_excerpt)[:350]

                # Extract featured media
                featured_img = ""
                embedded = p.get("_embedded", {})
                media_list = embedded.get("wp:featuredmedia", [])
                if media_list and isinstance(media_list, list) and len(media_list) > 0:
                    first_media = media_list[0]
                    featured_img = first_media.get("source_url") or ""

                # Categorization (Strictly check Berita or Artikel)
                post_cats = p.get("categories", [])
                if CATEGORY_ARTIKEL_ID in post_cats:
                    cat_id = CATEGORY_ARTIKEL_ID
                    cat_name = "Artikel"
                elif CATEGORY_BERITA_ID in post_cats:
                    cat_id = CATEGORY_BERITA_ID
                    cat_name = "Berita"
                else:
                    # Skip any post that does not belong to Berita or Artikel
                    continue

                # Publish Date
                date_str = p.get("date", "")  # e.g. "2026-10-04T09:52:15"
                pub_date = date_str[:10] if len(date_str) >= 10 else datetime.now(timezone.utc).strftime("%Y-%m-%d")

                # Author name if embedded
                author_name = "Humas UNMER Malang"
                author_list = embedded.get("author", [])
                if author_list and isinstance(author_list, list) and len(author_list) > 0:
                    author_name = author_list[0].get("name") or author_name

                # Pageviews from ThemeREX
                post_views = views_map.get(post_id, 0)

                # Upsert into database
                await execute(
                    """
                    INSERT INTO unmer_posts (
                        id, slug, title, link, excerpt, featured_image_url,
                        category_id, category_name, published_at, published_date,
                        author_name, pageviews, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        link = excluded.link,
                        excerpt = excluded.excerpt,
                        featured_image_url = excluded.featured_image_url,
                        category_name = excluded.category_name,
                        published_at = excluded.published_at,
                        published_date = excluded.published_date,
                        author_name = excluded.author_name,
                        pageviews = CASE WHEN excluded.pageviews > 0 THEN excluded.pageviews ELSE unmer_posts.pageviews END,
                        synced_at = excluded.synced_at
                    """,
                    [
                        post_id, slug, title, link, excerpt, featured_img,
                        cat_id, cat_name, date_str, pub_date,
                        author_name, post_views, now_iso
                    ]
                )
                total_upserted += 1

        except urllib.error.HTTPError as he:
            if he.code == 400:
                # Page out of bounds
                break
            logger.error(f"HTTPError fetching UNMER posts page {page}: {he}")
            await log_system_error(
                category=ErrorCategory.INGEST,
                error_code=ErrorCode.INGEST_PAYLOAD_INVALID,
                message=f"HTTP Error fetching UNMER posts page {page}: {he.code} {he.reason}",
                path=url,
                method="GET",
                platform_id="unmer",
                status_code=he.code,
            )
            break
        except Exception as e:
            logger.error(f"Exception fetching UNMER posts page {page}: {e}")
            await log_system_error(
                category=ErrorCategory.INGEST,
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                message=f"Error syncing UNMER posts: {e}",
                path=url,
                method="GET",
                platform_id="unmer",
            )
    # Bulk update views for all posts mapped from ThemeREX
    for pid, v in views_map.items():
        if v > 0:
            await execute("UPDATE unmer_posts SET pageviews = ? WHERE id = ?", [v, pid])

    logger.info(f"UNMER sync completed: {total_fetched} fetched, {total_upserted} upserted, {len(views_map)} views updated.")
    return {
        "status": "success",
        "total_fetched": total_fetched,
        "total_upserted": total_upserted,
        "synced_at": now_iso,
    }


async def get_unmer_summary() -> Dict[str, Any]:
    """Calculate summary statistics for UNMER portal monitoring."""
    total_row = await fetch_one("SELECT COUNT(*) as count FROM unmer_posts")
    total_posts = total_row["count"] if total_row else 0

    berita_row = await fetch_one("SELECT COUNT(*) as count FROM unmer_posts WHERE category_name = 'Berita'")
    total_berita = berita_row["count"] if berita_row else 0

    artikel_row = await fetch_one("SELECT COUNT(*) as count FROM unmer_posts WHERE category_name = 'Artikel'")
    total_artikel = artikel_row["count"] if artikel_row else 0

    # Last synced timestamp
    sync_row = await fetch_one("SELECT MAX(synced_at) as last_sync FROM unmer_posts")
    last_sync = sync_row["last_sync"] if sync_row and sync_row["last_sync"] else None

    # Latest publish date
    latest_pub_row = await fetch_one("SELECT MAX(published_date) as latest_pub FROM unmer_posts")
    latest_pub = latest_pub_row["latest_pub"] if latest_pub_row and latest_pub_row["latest_pub"] else None

    # Total Views
    views_row = await fetch_one("SELECT COALESCE(SUM(pageviews), 0) as total_views FROM unmer_posts")
    total_views = int(views_row["total_views"]) if views_row and views_row.get("total_views") is not None else 0

    return {
        "total_posts": total_posts,
        "total_berita": total_berita,
        "total_artikel": total_artikel,
        "total_views": total_views,
        "total_views_formatted": format_number_id(total_views),
        "last_synced_formatted": format_date_dmy(last_sync) if last_sync else "-",
        "latest_published_formatted": format_date_dmy(latest_pub) if latest_pub else "-",
    }


INDONESIAN_MONTH_NAMES = {
    "01": "Januari", "02": "Februari", "03": "Maret", "04": "April",
    "05": "Mei", "06": "Juni", "07": "Juli", "08": "Agustus",
    "09": "September", "10": "Oktober", "11": "November", "12": "Desember"
}


async def get_unmer_available_months() -> List[Dict[str, Any]]:
    """Retrieve list of available publication months in unmer_posts with count and date range."""
    sql = """
        SELECT substr(published_date, 1, 7) as year_month, count(*) as count
        FROM unmer_posts
        WHERE published_date IS NOT NULL AND length(published_date) >= 7
        GROUP BY year_month
        ORDER BY year_month DESC
    """
    rows = await fetch_all(sql)
    months = []
    for r in rows:
        ym = r["year_month"]
        if not ym or "-" not in ym:
            continue
        try:
            y_str, m_str = ym.split("-", 1)
            y = int(y_str)
            m = int(m_str)
            m_name = INDONESIAN_MONTH_NAMES.get(f"{m:02d}", str(m))
            last_d = calendar.monthrange(y, m)[1]
            months.append({
                "year_month": ym,
                "label": f"{m_name} {y}",
                "count": r["count"],
                "start_date": f"{ym}-01",
                "end_date": f"{ym}-{last_d:02d}",
            })
        except Exception:
            continue
    return months


async def get_unmer_posts(
    category: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 20,
    page: int = 1,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sort_by: str = "date"
) -> Dict[str, Any]:
    """Retrieve posts with filtering by category, search, date range, sorting, and pagination."""
    limit = int(limit)
    if limit not in (20, 50, 100):
        limit = 20
    page = max(1, int(page))
    offset = (page - 1) * limit

    where_clauses = ["1=1"]
    params: List[Any] = []

    if category and category.lower() in ("berita", "artikel"):
        where_clauses.append("LOWER(category_name) = ?")
        params.append(category.lower())

    if search and search.strip():
        where_clauses.append("(LOWER(title) LIKE ? OR LOWER(excerpt) LIKE ?)")
        term = f"%{search.strip().lower()}%"
        params.extend([term, term])

    # Clean and normalize date parameters
    clean_start = start_date.strip() if start_date and start_date.strip() else None
    clean_end = end_date.strip() if end_date and end_date.strip() else None

    # If user specifies only one date, treat as single-day match to prevent returning unrelated latest posts
    if clean_start and not clean_end:
        clean_end = clean_start
    elif clean_end and not clean_start:
        clean_start = clean_end

    if clean_start and clean_end:
        # Swap if start is later than end
        if clean_start > clean_end:
            clean_start, clean_end = clean_end, clean_start
        where_clauses.append("published_date >= ? AND published_date <= ?")
        params.extend([clean_start, clean_end])

    where_sql = " AND ".join(where_clauses)

    # Order clause
    sort_by_clean = (sort_by or "date").strip().lower()
    if sort_by_clean in ("views", "views_desc"):
        order_clause = "pageviews DESC, published_date DESC, id DESC"
    elif sort_by_clean == "views_asc":
        order_clause = "pageviews ASC, published_date DESC, id DESC"
    elif sort_by_clean == "date_asc":
        order_clause = "published_date ASC, id ASC"
    else:
        order_clause = "published_date DESC, id DESC"

    # Count total matching rows
    count_sql = f"SELECT COUNT(*) as total FROM unmer_posts WHERE {where_sql}"
    count_row = await fetch_one(count_sql, params)
    total_count = int(count_row["total"]) if count_row and count_row.get("total") is not None else 0
    total_pages = max(1, (total_count + limit - 1) // limit) if total_count > 0 else 1

    # Fetch rows
    data_sql = f"SELECT * FROM unmer_posts WHERE {where_sql} ORDER BY {order_clause} LIMIT ? OFFSET ?"
    rows = await fetch_all(data_sql, params + [limit, offset])

    # Format dates and metrics
    for row in rows:
        pub = row.get("published_at") or row.get("published_date") or ""
        row["published_at_formatted"] = format_date_dmy(row.get("published_date") or pub)
        if len(pub) >= 16:
            row["published_time"] = pub[11:16] + " WIB"
        else:
            row["published_time"] = ""
        row["pageviews_formatted"] = format_number_id(row.get("pageviews") or 0)
        if not row.get("featured_image_url"):
            row["featured_image_url"] = ""

    # Detect if current active date range corresponds to a full month
    active_month_label = None
    if clean_start and clean_end and clean_start[:7] == clean_end[:7]:
        try:
            y, m = map(int, clean_start[:7].split("-"))
            last_d = calendar.monthrange(y, m)[1]
            if clean_start == f"{clean_start[:7]}-01" and clean_end == f"{clean_start[:7]}-{last_d:02d}":
                active_month_label = f"{INDONESIAN_MONTH_NAMES.get(f'{m:02d}', str(m))} {y}"
        except Exception:
            pass

    # Suggested month info if 0 results on a specific day/range
    suggested_month_label = None
    suggested_month_count = 0
    suggested_month_start = None
    suggested_month_end = None

    if total_count == 0 and clean_start:
        ym = clean_start[:7]
        try:
            y, m = map(int, ym.split("-"))
            last_d = calendar.monthrange(y, m)[1]
            month_row = await fetch_one(
                "SELECT COUNT(*) as count FROM unmer_posts WHERE substr(published_date, 1, 7) = ?",
                [ym]
            )
            c = month_row["count"] if month_row else 0
            if c > 0:
                suggested_month_label = f"{INDONESIAN_MONTH_NAMES.get(f'{m:02d}', str(m))} {y}"
                suggested_month_count = c
                suggested_month_start = f"{ym}-01"
                suggested_month_end = f"{ym}-{last_d:02d}"
        except Exception:
            pass

    available_months = await get_unmer_available_months()

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
        "category": category or "all",
        "search": search.strip() if search else "",
        "start_date": clean_start or "",
        "end_date": clean_end or "",
        "sort_by": sort_by_clean,
        "active_month_label": active_month_label,
        "available_months": available_months,
        "suggested_month_label": suggested_month_label,
        "suggested_month_count": suggested_month_count,
        "suggested_month_start": suggested_month_start,
        "suggested_month_end": suggested_month_end,
        "start_idx": offset + 1 if total_count > 0 else 0,
        "end_idx": min(offset + limit, total_count)
    }

