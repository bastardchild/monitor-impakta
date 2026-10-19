#!/usr/bin/env python3
"""
Script Khusus: Refresh & Cache Foto Profil (PP / Avatar) Akun Media Sosial
Mengambil URL foto profil terbaru dari API resmi TikTok, Instagram, dan YouTube,
mengunduh file gambar ke disk server (/app/data/thumbnails/avatar_{platform}.jpg),
dan memperbarui kolom avatar_url di tabel connected_accounts.

Cara Menjalankan:
  docker compose exec app python scripts/refresh_avatars.py
"""
import asyncio
import logging
import os
import sys
import time
import aiohttp
from app.db import execute, fetch_all
from app.services.token_service import get_valid_credentials

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("refresh-avatars")

CACHE_DIR = os.environ.get("THUMBNAIL_CACHE_DIR", "/app/data/thumbnails")
os.makedirs(CACHE_DIR, exist_ok=True)


async def download_image(session: aiohttp.ClientSession, url: str, destination: str) -> bool:
    """Download image bytes and write to destination file."""
    if not url or not url.startswith("http"):
        return False
    try:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.read()
                if len(data) > 100:
                    with open(destination, "wb") as f:
                        f.write(data)
                    return True
            else:
                logger.warning(f"Download returned status {resp.status} for {url[:60]}")
    except Exception as e:
        logger.error(f"Error downloading {url[:60]}: {e}")
    return False


async def refresh_tiktok_avatar(session: aiohttp.ClientSession) -> bool:
    """Fetch fresh TikTok avatar and save to disk."""
    print("\n[*] Menyegarkan Foto Profil TikTok...")
    try:
        creds = await get_valid_credentials("tiktok")
        token = creds.get("access_token")
        if not token:
            print("    [WARN] Token TikTok tidak ditemukan.")
            return False

        headers = {"Authorization": f"Bearer {token}"}
        url = "https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name,avatar_url,avatar_url_100,avatar_large_url"
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                print(f"    [WARN] Gagal memanggil TikTok User Info API: Status {resp.status} - {err_text}")
                return False
            data = await resp.json()

        user = data.get("data", {}).get("user", {})
        avatar_url = user.get("avatar_large_url") or user.get("avatar_url") or user.get("avatar_url_100")
        display_name = user.get("display_name")

        if not avatar_url:
            print("    [WARN] Avatar URL kosong dari TikTok API.")
            return False

        dest = os.path.join(CACHE_DIR, "avatar_tiktok.jpg")
        success = await download_image(session, avatar_url, dest)
        if success:
            await execute(
                "UPDATE connected_accounts SET avatar_url = ?, updated_at = ? WHERE platform_id = 'tiktok'",
                [avatar_url, int(time.time())]
            )
            print(f"    [OK] Foto Profil TikTok berhasil diunduh ({os.path.getsize(dest)} bytes).")
            print(f"         Nama Akun: {display_name}")
            print(f"         Tersimpan di: {dest}")
            return True
        else:
            print("    [ERROR] Gagal mengunduh file gambar TikTok avatar.")
    except Exception as e:
        print(f"    [ERROR] Exception saat memproses TikTok avatar: {e}")
    return False


async def refresh_instagram_avatar(session: aiohttp.ClientSession) -> bool:
    """Fetch fresh Instagram profile picture and save to disk."""
    print("\n[*] Menyegarkan Foto Profil Instagram...")
    try:
        creds = await get_valid_credentials("instagram")
        token = creds.get("access_token")
        account_id = creds.get("account_id")
        if not token or not account_id:
            print("    [WARN] Token atau Account ID Instagram tidak ditemukan.")
            return False

        url = f"https://graph.facebook.com/v21.0/{account_id}?fields=profile_picture_url,name,username&access_token={token}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                print(f"    [WARN] Gagal memanggil Meta Graph API: Status {resp.status} - {err_text}")
                return False
            data = await resp.json()

        avatar_url = data.get("profile_picture_url")
        name = data.get("name") or data.get("username", "Instagram")

        if not avatar_url:
            print("    [WARN] Profile picture URL kosong dari Meta Graph API.")
            return False

        dest = os.path.join(CACHE_DIR, "avatar_instagram.jpg")
        success = await download_image(session, avatar_url, dest)
        if success:
            await execute(
                "UPDATE connected_accounts SET avatar_url = ?, updated_at = ? WHERE platform_id = 'instagram'",
                [avatar_url, int(time.time())]
            )
            print(f"    [OK] Foto Profil Instagram berhasil diunduh ({os.path.getsize(dest)} bytes).")
            print(f"         Nama Akun: {name}")
            print(f"         Tersimpan di: {dest}")
            return True
        else:
            print("    [ERROR] Gagal mengunduh file gambar Instagram avatar.")
    except Exception as e:
        print(f"    [ERROR] Exception saat memproses Instagram avatar: {e}")
    return False


async def refresh_youtube_avatar(session: aiohttp.ClientSession) -> bool:
    """Download and cache YouTube channel avatar from DB URL."""
    print("\n[*] Menyegarkan Foto Profil YouTube...")
    try:
        row = await fetch_all("SELECT avatar_url, display_name FROM connected_accounts WHERE platform_id = 'youtube'")
        if not row or not row[0].get("avatar_url"):
            print("    [INFO] YouTube belum terhubung atau avatar_url belum diset.")
            return False

        avatar_url = row[0]["avatar_url"]
        dest = os.path.join(CACHE_DIR, "avatar_youtube.jpg")
        success = await download_image(session, avatar_url, dest)
        if success:
            print(f"    [OK] Foto Profil YouTube berhasil disimpan ({os.path.getsize(dest)} bytes).")
            print(f"         Tersimpan di: {dest}")
            return True
    except Exception as e:
        print(f"    [ERROR] Exception saat memproses YouTube avatar: {e}")
    return False


async def main():
    print("=" * 60)
    print("  IMPAKTA - Refresh & Cache Foto Profil (PP / Avatar)")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        t_ok = await refresh_tiktok_avatar(session)
        i_ok = await refresh_instagram_avatar(session)
        y_ok = await refresh_youtube_avatar(session)

    print("\n" + "=" * 60)
    print("  Ringkasan Hasil:")
    print(f"  - TikTok    : {'BERHASIL' if t_ok else 'GAGAL/LEWATI'}")
    print(f"  - Instagram : {'BERHASIL' if i_ok else 'GAGAL/LEWATI'}")
    print(f"  - YouTube   : {'BERHASIL' if y_ok else 'GAGAL/LEWATI'}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
