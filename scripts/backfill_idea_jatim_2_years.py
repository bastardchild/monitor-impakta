#!/usr/bin/env python3
"""
scripts/backfill_idea_jatim_2_years.py
Script untuk melakukan backfill / scraping data liputan berita UNMER Malang dari
portal Idea Jatim (https://ideajatim.id) selama 2 tahun ke belakang.
"""

import asyncio
from datetime import datetime, timezone, timedelta
import html
import json
import logging
import re
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("backfill.idea_jatim")

from app.db import batch, fetch_all

IDEA_JATIM_API_URL = "https://ideajatim.id/wp-json/wp/v2/posts"
IDEA_JATIM_BASE_WEB = "https://ideajatim.id"

WP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _clean_html_text(raw_html: Optional[str]) -> str:
    """Strip tags, clean zero-width characters, and unescape html entities."""
    if not raw_html:
        return ""
    clean = re.sub(r"<[^>]+>", " ", str(raw_html))
    clean = html.unescape(clean)
    # Remove zero-width spaces and normalize whitespace
    clean = clean.replace("\u200b", " ").replace("\ufeff", " ")
    return " ".join(clean.split()).strip()


async def _get_deleted_post_ids() -> set:
    try:
        rows = await fetch_all("SELECT id FROM deleted_external_posts")
        return {r["id"] for r in rows}
    except Exception:
        return set()


async def _save_posts_batch(posts: List[Dict[str, Any]]) -> int:
    if not posts:
        return 0

    upsert_sql = """
    INSERT INTO external_news_posts (
        id, source_id, source_name, external_id, title, url, excerpt,
        featured_image_url, category_name, author_name, city, pageviews,
        published_at, published_date, synced_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        title = excluded.title,
        url = excluded.url,
        excerpt = excluded.excerpt,
        featured_image_url = CASE 
            WHEN excluded.featured_image_url != '' THEN excluded.featured_image_url 
            ELSE external_news_posts.featured_image_url 
        END,
        category_name = excluded.category_name,
        author_name = excluded.author_name,
        city = excluded.city,
        pageviews = CASE
            WHEN excluded.pageviews > external_news_posts.pageviews THEN excluded.pageviews
            ELSE external_news_posts.pageviews
        END,
        published_at = excluded.published_at,
        published_date = excluded.published_date,
        synced_at = excluded.synced_at
    """
    statements = [
        (upsert_sql, [
            p["id"], p["source_id"], p["source_name"], p["external_id"],
            p["title"], p["url"], p["excerpt"], p["featured_image_url"],
            p["category_name"], p["author_name"], p["city"], p["pageviews"],
            p["published_at"], p["published_date"], p["synced_at"]
        ])
        for p in posts
    ]
    await batch(statements)
    return len(statements)


async def main():
    logger.info("=== MEMULAI BACKFILL IDEA JATIM (2 TAHUN KE BELAKANG) ===")
    
    # 2 tahun kebelakang (~730 hari)
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=730)).strftime("%Y-%m-%d")
    cutoff_iso = f"{cutoff_date}T00:00:00"
    now_iso = datetime.now(timezone.utc).isoformat()
    
    logger.info(f"Target rentang waktu: {cutoff_date} s.d. hari ini")
    
    deleted_ids = await _get_deleted_post_ids()
    if deleted_ids:
        logger.info(f"Ditemukan {len(deleted_ids)} post ID yang pernah dihapus manual (akan di-skip).")

    all_posts_to_save: List[Dict[str, Any]] = []
    seen_ids = set()
    page = 1
    per_page = 50

    while True:
        logger.info(f"Mengambil data halaman {page} (per_page={per_page})...")
        url = f"{IDEA_JATIM_API_URL}?search=unmer&_embed=1&per_page={per_page}&page={page}&after={cutoff_iso}"
        
        req = urllib.request.Request(url, headers=WP_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                total_posts_header = resp.headers.get("X-WP-Total")
                total_pages_header = resp.headers.get("X-WP-TotalPages")
                items = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as he:
            if he.code == 400:
                logger.info(f"Halaman {page} di luar batas atau tidak ada data lagi (HTTP 400). Selesai.")
                break
            else:
                logger.error(f"HTTP Error {he.code} saat mengakses {url}: {he.reason}")
                break
        except Exception as e:
            logger.error(f"Error jaringan saat request halaman {page}: {e}")
            break

        if not items or not isinstance(items, list):
            logger.info(f"Tidak ada artikel lagi di halaman {page}. Selesai.")
            break

        logger.info(f"Halaman {page}: Berhasil menerima {len(items)} artikel (Total indikasi API: {total_posts_header} artikel, {total_pages_header} halaman).")

        page_saved_count = 0
        for it in items:
            ext_id = str(it.get("id") or "").strip()
            if not ext_id or ext_id in seen_ids:
                continue
            seen_ids.add(ext_id)

            raw_title = it.get("title", {}).get("rendered") if isinstance(it.get("title"), dict) else str(it.get("title") or "")
            title = _clean_html_text(raw_title)
            if not title:
                continue

            full_url = it.get("link") or f"{IDEA_JATIM_BASE_WEB}/?p={ext_id}"

            raw_excerpt = it.get("excerpt", {}).get("rendered") if isinstance(it.get("excerpt"), dict) else str(it.get("excerpt") or "")
            excerpt = _clean_html_text(raw_excerpt)[:400]

            featured_img = ""
            media_list = it.get("_embedded", {}).get("wp:featuredmedia", [])
            if isinstance(media_list, list) and media_list and isinstance(media_list[0], dict):
                featured_img = media_list[0].get("source_url") or ""

            cat_name = "Umum"
            terms_groups = it.get("_embedded", {}).get("wp:term", [])
            if isinstance(terms_groups, list):
                for group in terms_groups:
                    if isinstance(group, list):
                        for term in group:
                            tname = (term.get("name") or "").strip()
                            if tname and tname.lower() not in ("idea jatim", "headline", "unmer", "unmer malang"):
                                cat_name = tname
                                break
                        if cat_name != "Umum":
                            break

            author = "Redaksi Idea Jatim"
            authors = it.get("_embedded", {}).get("author", [])
            if isinstance(authors, list) and authors and isinstance(authors[0], dict):
                aname = (authors[0].get("name") or "").strip()
                if aname:
                    author = aname

            pub_raw = it.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            pub_date = pub_raw[:10] if len(pub_raw) >= 10 else datetime.now(timezone.utc).strftime("%Y-%m-%d")

            post_id = f"idea_jatim_{ext_id}"
            if post_id in deleted_ids:
                continue

            all_posts_to_save.append({
                "id": post_id,
                "source_id": "idea_jatim",
                "source_name": "Idea Jatim",
                "external_id": ext_id,
                "title": title,
                "url": full_url,
                "excerpt": excerpt,
                "featured_image_url": featured_img,
                "category_name": cat_name,
                "author_name": author,
                "city": "Malang",
                "pageviews": 0,
                "published_at": pub_raw,
                "published_date": pub_date,
                "synced_at": now_iso
            })
            page_saved_count += 1

        logger.info(f"Diproses {page_saved_count} artikel valid dari halaman {page}.")

        if total_pages_header and page >= int(total_pages_header):
            break

        page += 1
        await asyncio.sleep(0.5)

    logger.info(f"Total artikel Idea Jatim yang siap disimpan: {len(all_posts_to_save)} artikel.")

    if all_posts_to_save:
        chunk_size = 50
        total_upserted = 0
        for i in range(0, len(all_posts_to_save), chunk_size):
            chunk = all_posts_to_save[i:i + chunk_size]
            upserted = await _save_posts_batch(chunk)
            total_upserted += upserted
            logger.info(f"Berhasil meng-upsert batch {i // chunk_size + 1}: {upserted} artikel.")

        logger.info(f"=== SELESAI: Berhasil menyimpan total {total_upserted} artikel liputan Idea Jatim ke Database Turso! ===")
    else:
        logger.info("Tidak ada artikel baru yang perlu disimpan.")


if __name__ == "__main__":
    asyncio.run(main())
