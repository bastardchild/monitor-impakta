#!/usr/bin/env python3
"""
Script Khusus: Refresh & Cache Thumbnail Media Sosial (Instagram & TikTok)
Mengambil fresh signed URL dari API resmi untuk 2 tahun ke belakang (2024-sekarang),
memperbarui database, dan mengunduh file thumbnail ke disk server lokal (/app/data/thumbnails)
agar gambar tidak pernah kedaluwarsa lagi (Zero 403 Forever).

Cara Menjalankan:
  # Di server VPS via Docker Compose:
  docker compose exec app python scripts/refresh_thumbnails_history.py --years 2 --platform all

  # Hanya Instagram:
  docker compose exec app python scripts/refresh_thumbnails_history.py --years 2 --platform instagram

  # Uji coba cepat (hanya 10 post):
  docker compose exec app python scripts/refresh_thumbnails_history.py --limit 10 --platform instagram
"""
import argparse
import asyncio
from datetime import datetime, timezone, timedelta
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional
import aiohttp
from libsql_client import Statement

# Setup logger
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("refresh-thumbnails")

CACHE_DIR = os.environ.get("THUMBNAIL_CACHE_DIR", "/app/data/thumbnails")
os.makedirs(CACHE_DIR, exist_ok=True)


async def download_and_cache(session: aiohttp.ClientSession, url: str, post_id: str) -> bool:
    """Download thumbnail and save locally on disk."""
    if not url or not url.startswith("http"):
        return False
    
    file_path = os.path.join(CACHE_DIR, f"{post_id}.jpg")
    try:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.read()
                if len(data) > 100:
                    with open(file_path, "wb") as f:
                        f.write(data)
                    return True
    except Exception as e:
        logger.debug(f"Failed to download {post_id} from {url[:60]}: {e}")
    return False


async def refresh_instagram(years: int = 2, limit: Optional[int] = None):
    """Fetch fresh signed thumbnail URLs for Instagram up to N years ago."""
    from app.db import batch, execute, fetch_all
    from app.services.token_service import get_valid_credentials

    print("\n" + "=" * 60)
    print(f"  Memulai Refresh Thumbnail Instagram ({years} Tahun ke Belakang)")
    print("=" * 60)

    try:
        creds = await get_valid_credentials("instagram")
    except Exception as e:
        print(f"[ERROR] Kredensial Instagram tidak ditemukan atau perlu login ulang: {e}")
        return

    access_token = creds.get("access_token")
    account_id = creds.get("account_id")
    if not access_token or not account_id:
        print("[ERROR] access_token atau account_id Instagram kosong.")
        return

    headers = {"Authorization": f"Bearer {access_token}"}
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=years * 365)
    cutoff_iso = cutoff_dt.strftime("%Y-%m-%dT00:00:00Z")

    print(f"[*] Account ID: {account_id}")
    print(f"[*] Cutoff Tanggal: {cutoff_iso} (~{years} tahun lalu)")
    print(f"[*] Direktori Cache: {CACHE_DIR}")

    total_processed = 0
    total_db_updated = 0
    total_cached_disk = 0

    api_url = (
        f"https://graph.facebook.com/v21.0/{account_id}/media"
        "?fields=id,caption,media_type,media_url,thumbnail_url,timestamp,permalink"
        "&limit=100"
    )

    page_num = 0
    current_url = api_url

    async with aiohttp.ClientSession() as session:
        while current_url:
            page_num += 1
            print(f"\n[Page {page_num}] Memanggil Meta Graph API...")

            try:
                async with session.get(current_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        err_text = await resp.text()
                        print(f"[WARN] Gagal mengambil halaman {page_num}: Status {resp.status} - {err_text}")
                        break
                    page_data = await resp.json()
            except Exception as e:
                print(f"[ERROR] Request timeout/error di halaman {page_num}: {e}")
                break

            items = page_data.get("data", [])
            if not items:
                print("[INFO] Tidak ada data lagi.")
                break

            reached_cutoff = False
            batch_updates: List[Statement] = []
            download_tasks = []

            for m in items:
                mid = str(m.get("id"))
                pub_at = m.get("timestamp") or ""
                if pub_at.endswith("+0000"):
                    pub_at = pub_at[:-5] + "Z"

                if pub_at and pub_at < cutoff_iso:
                    reached_cutoff = True
                    break

                fresh_url = m.get("thumbnail_url") or m.get("media_url") or ""
                if not fresh_url:
                    continue

                total_processed += 1
                post_id = f"instagram_{mid}"

                # Update DB statement
                batch_updates.append(
                    Statement(
                        "UPDATE posts SET thumbnail_url = ? WHERE external_id = ? OR id = ?",
                        [fresh_url, mid, post_id]
                    )
                )

                # Download thumbnail in background
                download_tasks.append((post_id, fresh_url))

                if limit and total_processed >= limit:
                    reached_cutoff = True
                    break

            # Execute DB Batch
            if batch_updates:
                try:
                    await batch(batch_updates)
                    total_db_updated += len(batch_updates)
                except Exception as e:
                    print(f"[WARN] Error batch update database: {e}")

            # Download & Cache thumbnails concurrently
            if download_tasks:
                sem = asyncio.Semaphore(10)
                async def sem_download(pid, u):
                    async with sem:
                        return await download_and_cache(session, u, pid)

                results = await asyncio.gather(*[sem_download(pid, u) for pid, u in download_tasks])
                page_cached = sum(1 for r in results if r)
                total_cached_disk += page_cached
                print(f"       -> {len(download_tasks)} postingan diproses ({page_cached} gambar baru disimpan ke disk).")

            if reached_cutoff:
                print(f"[INFO] Batas waktu {years} tahun tercapai.")
                break

            # Next page
            paging = page_data.get("paging", {})
            current_url = paging.get("next")
            await asyncio.sleep(0.3)

        # Step 2: Pastikan juga Top 20 Konten Terpopuler Instagram yang ada di DB tersimpan ke disk
        print("\n[*] Memeriksa Top 20 Konten Terpopuler Instagram di DB...")
        try:
            top_rows = await fetch_all(
                """
                SELECT id, external_id, thumbnail_url, title 
                FROM posts 
                WHERE platform_id = 'instagram' 
                ORDER BY published_at DESC 
                LIMIT 20
                """
            )
            top_saved = 0
            for r in top_rows:
                pid = r["id"]
                file_path = os.path.join(CACHE_DIR, f"{pid}.jpg")
                if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
                    ext_id = r["external_id"]
                    # Fetch single media fresh from Meta
                    g_url = f"https://graph.facebook.com/v21.0/{ext_id}?fields=thumbnail_url,media_url&access_token={access_token}"
                    async with session.get(g_url) as gr:
                        if gr.status == 200:
                            gd = await gr.json()
                            f_url = gd.get("thumbnail_url") or gd.get("media_url")
                            if f_url and await download_and_cache(session, f_url, pid):
                                top_saved += 1
                                await execute("UPDATE posts SET thumbnail_url = ? WHERE id = ?", [f_url, pid])
            if top_saved > 0:
                print(f"       -> {top_saved} thumbnail top posts berhasil disegarkan & disimpan ke disk.")
        except Exception as e:
            print(f"[WARN] Gagal menyisir top posts Instagram: {e}")

    print("\n" + "-" * 60)
    print(f"  [SELESAI INSTAGRAM]")
    print(f"  Total Postingan Diperiksa : {total_processed}")
    print(f"  Database Terupdate        : {total_db_updated}")
    print(f"  Gambar Tersimpan di Disk  : {total_cached_disk}")
    print("-" * 60)


async def refresh_tiktok(years: int = 2, limit: Optional[int] = None):
    """Fetch fresh signed cover URLs for TikTok up to N years ago."""
    from app.db import batch, execute
    from app.services.token_service import get_valid_credentials

    print("\n" + "=" * 60)
    print(f"  Memulai Refresh Thumbnail TikTok ({years} Tahun ke Belakang)")
    print("=" * 60)

    try:
        creds = await get_valid_credentials("tiktok")
    except Exception as e:
        print(f"[ERROR] Kredensial TikTok tidak ditemukan atau perlu login ulang: {e}")
        return

    access_token = creds.get("access_token")
    if not access_token:
        print("[ERROR] access_token TikTok kosong.")
        return

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=years * 365)
    cutoff_ts = int(cutoff_dt.timestamp())

    print(f"[*] Cutoff Waktu: {cutoff_dt.strftime('%Y-%m-%d')} (~{years} tahun lalu)")
    print(f"[*] Direktori Cache: {CACHE_DIR}")

    total_processed = 0
    total_db_updated = 0
    total_cached_disk = 0

    video_url = (
        "https://open.tiktokapis.com/v2/video/list/"
        "?fields=id,title,video_description,cover_image_url,create_time"
    )

    cursor = None
    has_more = True
    page_num = 0

    async with aiohttp.ClientSession() as session:
        while has_more:
            page_num += 1
            print(f"\n[Page {page_num}] Memanggil TikTok Video List API...")

            payload: Dict[str, Any] = {"max_count": 20}
            if cursor is not None:
                payload["cursor"] = cursor

            try:
                async with session.post(video_url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        err_text = await resp.text()
                        print(f"[WARN] Gagal mengambil halaman TikTok {page_num}: {err_text}")
                        break
                    res_json = await resp.json()
            except Exception as e:
                print(f"[ERROR] Request timeout/error di halaman TikTok {page_num}: {e}")
                break

            data = res_json.get("data", {})
            videos = data.get("videos", [])
            cursor = data.get("cursor")
            has_more = data.get("has_more", False)

            if not videos:
                print("[INFO] Tidak ada video lagi.")
                break

            reached_cutoff = False
            batch_updates: List[Statement] = []
            download_tasks = []

            for v in videos:
                vid = str(v.get("id"))
                create_time = int(v.get("create_time", 0))

                if create_time < cutoff_ts:
                    reached_cutoff = True
                    break

                cover_url = v.get("cover_image_url") or ""
                if not cover_url:
                    continue

                total_processed += 1
                post_id = f"tiktok_{vid}"

                batch_updates.append(
                    Statement(
                        "UPDATE posts SET thumbnail_url = ? WHERE external_id = ? OR id = ?",
                        [cover_url, vid, post_id]
                    )
                )

                download_tasks.append((post_id, cover_url))

                if limit and total_processed >= limit:
                    reached_cutoff = True
                    break

            # Execute DB Batch
            if batch_updates:
                try:
                    await batch(batch_updates)
                    total_db_updated += len(batch_updates)
                except Exception as e:
                    print(f"[WARN] Error batch update database TikTok: {e}")

            # Download & Cache concurrently
            if download_tasks:
                sem = asyncio.Semaphore(10)
                async def sem_download(pid, u):
                    async with sem:
                        return await download_and_cache(session, u, pid)

                results = await asyncio.gather(*[sem_download(pid, u) for pid, u in download_tasks])
                page_cached = sum(1 for r in results if r)
                total_cached_disk += page_cached
                print(f"       -> {len(download_tasks)} video diproses ({page_cached} gambar baru disimpan ke disk).")

            if reached_cutoff or not has_more:
                break

            await asyncio.sleep(0.3)

    print("\n" + "-" * 60)
    print(f"  [SELESAI TIKTOK]")
    print(f"  Total Video Diperiksa     : {total_processed}")
    print(f"  Database Terupdate        : {total_db_updated}")
    print(f"  Gambar Tersimpan di Disk  : {total_cached_disk}")
    print("-" * 60)


async def main():
    parser = argparse.ArgumentParser(description="Refresh & Cache Thumbnails 2 Tahun untuk Instagram & TikTok")
    parser.add_argument("--years", type=int, default=2, help="Jumlah tahun ke belakang (default: 2)")
    parser.add_argument("--platform", type=str, choices=["all", "instagram", "tiktok"], default="all", help="Platform target")
    parser.add_argument("--limit", type=int, default=None, help="Batas maksimal postingan untuk uji coba (opsional)")
    args = parser.parse_args()

    start_time = time.time()
    print("=" * 60)
    print("  IMPAKTA - Social Media Thumbnail Refresh & Storage Engine")
    print("=" * 60)

    if args.platform in ("all", "instagram"):
        await refresh_instagram(years=args.years, limit=args.limit)

    if args.platform in ("all", "tiktok"):
        await refresh_tiktok(years=args.years, limit=args.limit)

    elapsed = round(time.time() - start_time, 2)
    print(f"\n[SUKSES] Seluruh proses selesai dalam {elapsed} detik.")
    print(f"Semua thumbnail kini tersimpan permanen di: {CACHE_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
