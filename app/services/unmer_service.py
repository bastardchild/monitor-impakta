from datetime import datetime, timezone
import html
import logging
from typing import Any, Dict, List, Optional
import urllib.request
import json
from app.db import execute, fetch_all, fetch_one
from app.services.error_logger import ErrorCategory, ErrorCode, log_system_error
from app.services.kpi_service import format_date_dmy

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


async def sync_unmer_posts(max_pages: int = 2, per_page: int = 50) -> Dict[str, Any]:
    """
    Fetch posts from unmer.ac.id WordPress REST API for categories Berita (487) and Artikel (919)
    and store/upsert them into Turso/LibSQL database.
    """
    total_fetched = 0
    total_upserted = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    cat_param = f"{CATEGORY_BERITA_ID},{CATEGORY_ARTIKEL_ID}"

    for page in range(1, max_pages + 1):
        url = f"{UNMER_WP_BASE}/posts?categories={cat_param}&per_page={per_page}&page={page}&_embed=1"
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

                # Categorization
                post_cats = p.get("categories", [])
                if CATEGORY_ARTIKEL_ID in post_cats:
                    cat_id = CATEGORY_ARTIKEL_ID
                    cat_name = "Artikel"
                else:
                    cat_id = CATEGORY_BERITA_ID
                    cat_name = "Berita"

                # Publish Date
                date_str = p.get("date", "")  # e.g. "2026-10-04T09:52:15"
                pub_date = date_str[:10] if len(date_str) >= 10 else datetime.now(timezone.utc).strftime("%Y-%m-%d")

                # Author name if embedded
                author_name = "Humas UNMER Malang"
                author_list = embedded.get("author", [])
                if author_list and isinstance(author_list, list) and len(author_list) > 0:
                    author_name = author_list[0].get("name") or author_name

                # Upsert into database
                await execute(
                    """
                    INSERT INTO unmer_posts (
                        id, slug, title, link, excerpt, featured_image_url,
                        category_id, category_name, published_at, published_date,
                        author_name, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        link = excluded.link,
                        excerpt = excluded.excerpt,
                        featured_image_url = excluded.featured_image_url,
                        category_name = excluded.category_name,
                        published_at = excluded.published_at,
                        published_date = excluded.published_date,
                        author_name = excluded.author_name,
                        synced_at = excluded.synced_at
                    """,
                    [
                        post_id, slug, title, link, excerpt, featured_img,
                        cat_id, cat_name, date_str, pub_date,
                        author_name, now_iso
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
            break

    logger.info(f"UNMER sync completed: {total_fetched} fetched, {total_upserted} upserted.")
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

    return {
        "total_posts": total_posts,
        "total_berita": total_berita,
        "total_artikel": total_artikel,
        "last_synced_formatted": format_date_dmy(last_sync) if last_sync else "-",
        "latest_published_formatted": format_date_dmy(latest_pub) if latest_pub else "-",
    }


async def get_unmer_posts(
    category: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 30,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """Retrieve posts with optional filtering and formatting."""
    query = "SELECT * FROM unmer_posts WHERE 1=1"
    args = []

    if category and category.lower() in ("berita", "artikel"):
        query += " AND LOWER(category_name) = ?"
        args.append(category.lower())

    if search and search.strip():
        query += " AND (LOWER(title) LIKE ? OR LOWER(excerpt) LIKE ?)"
        term = f"%{search.strip().lower()}%"
        args.extend([term, term])

    query += " ORDER BY published_date DESC, id DESC LIMIT ? OFFSET ?"
    args.extend([limit, offset])

    rows = await fetch_all(query, args)
    
    # Format dates
    for row in rows:
        row["published_at_formatted"] = format_date_dmy(row.get("published_date") or row.get("published_at"))
        if not row.get("featured_image_url"):
            row["featured_image_url"] = ""

    return rows
