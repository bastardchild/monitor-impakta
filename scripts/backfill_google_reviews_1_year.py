#!/usr/bin/env python3
"""
scripts/backfill_google_reviews_1_year.py
Script backfill awal untuk mengambil ulasan Google Maps UNMER Malang
selama 1 tahun terakhir (365 hari) via SerpAPI.
"""

import argparse
import asyncio
import logging
import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

from app.db import close_client, execute, fetch_one
from app.services.google_review_service import DEFAULT_PLACE_ID, sync_google_reviews
from migrations.runner import parse_sql_statements

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill.google_reviews")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Google Maps Reviews UNMER Malang (default: 1 tahun terakhir)."
    )
    parser.add_argument("--days", type=int, default=365, help="Rentang hari ke belakang (default: 365)")
    parser.add_argument("--max-pages", type=int, default=60, help="Batas maksimal halaman SerpAPI (default: 60)")
    parser.add_argument("--place-id", type=str, default=DEFAULT_PLACE_ID, help="Google Maps Place ID")
    parser.add_argument("--dry-run", action="store_true", help="Simulasi tanpa menyimpan ke database")
    args = parser.parse_args()

    logger.info("Memastikan tabel google_reviews sudah tersedia...")
    migration_file = Path(__file__).resolve().parent.parent / "migrations" / "010_google_reviews.sql"
    for stmt in parse_sql_statements(migration_file.read_text(encoding="utf-8")):
        await execute(stmt)
    existing_mig = await fetch_one(
        "SELECT version FROM schema_migrations WHERE version = ?",
        ["010_google_reviews.sql"],
    )
    if not existing_mig:
        from datetime import datetime, timezone
        await execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            ["010_google_reviews.sql", datetime.now(timezone.utc).isoformat()],
        )

    logger.info(
        "Memulai backfill Google Reviews: place_id=%s, days=%s, max_pages=%s, dry_run=%s",
        args.place_id,
        args.days,
        args.max_pages,
        args.dry_run,
    )
    result = await sync_google_reviews(
        days=args.days,
        max_pages=args.max_pages,
        place_id=args.place_id,
        stop_on_existing=False,
        dry_run=args.dry_run,
    )
    await close_client()

    if result.get("status") != "ok":
        logger.error("Backfill gagal: %s", result.get("error"))
        return 1

    logger.info(
        "Backfill selesai! pages=%s, fetched=%s, upserted=%s (new=%s, updated=%s), skipped_older=%s, stopped_reason=%s",
        result["pages_fetched"],
        result["total_fetched"],
        result["total_upserted"],
        result["new_saved"],
        result["updated_saved"],
        result["skipped_older"],
        result["stopped_reason"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
