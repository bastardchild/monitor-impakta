import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import random
import sys
import time
import uuid

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import close_client, execute, fetch_one
from migrations.runner import run_migrations


async def seed(reset: bool = False):
    print("Menyiapkan database dan migrasi...")
    await run_migrations()

    if reset:
        print("Mereset data demo sebelumnya (--reset)...")
        await execute("DELETE FROM post_daily_metrics")
        await execute("DELETE FROM posts")
        await execute("DELETE FROM account_daily_metrics")
        await execute("DELETE FROM kpi_targets")
        await execute("DELETE FROM ingest_logs")
        await execute("DELETE FROM connected_accounts")

    now_ts = int(time.time())
    today = date.today()

    print("Membuat koneksi akun demo (YouTube, Instagram, TikTok)...")
    demo_accounts = [
        {
            "platform_id": "youtube",
            "name": "YouTube",
            "handle": "@TechCreatorID",
            "account_id": "UC_DEMO_YOUTUBE_001",
            "display_name": "Tech Creator ID",
            "avatar_url": "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=128&auto=format&fit=crop&q=80",
        },
        {
            "platform_id": "instagram",
            "name": "Instagram",
            "handle": "@techcreator.id",
            "account_id": "IG_DEMO_ACCOUNT_002",
            "display_name": "Tech Creator Indonesia",
            "avatar_url": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=128&auto=format&fit=crop&q=80",
        },
        {
            "platform_id": "tiktok",
            "name": "TikTok",
            "handle": "@techcreator_tok",
            "account_id": "TT_DEMO_ACCOUNT_003",
            "display_name": "Tech Creator TikTok",
            "avatar_url": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=128&auto=format&fit=crop&q=80",
        },
    ]

    for acc in demo_accounts:
        await execute(
            """
            UPDATE platforms SET handle = ?, account_id = ? WHERE id = ?
            """,
            [acc["handle"], acc["account_id"], acc["platform_id"]]
        )
        await execute(
            """
            INSERT INTO connected_accounts (
                platform_id, access_token_enc, refresh_token_enc, token_type, scopes,
                expires_at, refresh_expires_at, external_account_id, display_name,
                avatar_url, status, last_refresh_at, connected_at, updated_at
            ) VALUES (
                ?, 'demo_encrypted_access_token', 'demo_encrypted_refresh_token', 'Bearer', 'read',
                ?, ?, ?, ?,
                ?, 'connected', ?, ?, ?
            )
            ON CONFLICT(platform_id) DO UPDATE SET
                display_name = excluded.display_name,
                avatar_url = excluded.avatar_url,
                status = 'connected',
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            [
                acc["platform_id"],
                now_ts + (86400 * 30),  # Valid 30 days
                now_ts + (86400 * 90),
                acc["account_id"],
                acc["display_name"],
                acc["avatar_url"],
                now_ts,
                now_ts - (86400 * 90),
                now_ts
            ]
        )

    print("Mengisi data metrik harian 90 hari...")
    # Base followers 90 days ago
    base_followers = {
        "youtube": 12500,
        "instagram": 21000,
        "tiktok": 34000,
    }

    current_followers = dict(base_followers)

    for i in range(89, -1, -1):
        d = today - timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")

        # YouTube daily metrics
        yt_new_subs = random.randint(15, 45)
        current_followers["youtube"] += yt_new_subs
        yt_views = random.randint(2500, 7500)
        yt_watch_sec = float(yt_views * random.randint(120, 280))
        yt_engagement = int(yt_views * random.uniform(0.04, 0.08))
        await execute(
            """
            INSERT INTO account_daily_metrics (
                platform_id, date, followers, views, reach, impressions, watch_time_sec, engagement_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform_id, date) DO UPDATE SET
                followers = excluded.followers, views = excluded.views,
                watch_time_sec = excluded.watch_time_sec, engagement_count = excluded.engagement_count
            """,
            ["youtube", d_str, current_followers["youtube"], yt_views, int(yt_views * 1.3), int(yt_views * 2.1), yt_watch_sec, yt_engagement]
        )

        # Instagram daily metrics
        ig_new_folls = random.randint(20, 60)
        current_followers["instagram"] += ig_new_folls
        ig_views = random.randint(4000, 11000)
        ig_reach = int(ig_views * random.uniform(0.65, 0.85))
        ig_impr = int(ig_views * random.uniform(1.2, 1.8))
        ig_engagement = int(ig_reach * random.uniform(0.05, 0.10))
        await execute(
            """
            INSERT INTO account_daily_metrics (
                platform_id, date, followers, views, reach, impressions, watch_time_sec, engagement_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform_id, date) DO UPDATE SET
                followers = excluded.followers, views = excluded.views, reach = excluded.reach,
                impressions = excluded.impressions, engagement_count = excluded.engagement_count
            """,
            ["instagram", d_str, current_followers["instagram"], ig_views, ig_reach, ig_impr, 0.0, ig_engagement]
        )

        # TikTok daily metrics (Display API: NO watch time or completion rate!)
        tt_new_folls = random.randint(40, 120)
        current_followers["tiktok"] += tt_new_folls
        tt_views = random.randint(10000, 28000)
        tt_engagement = int(tt_views * random.uniform(0.06, 0.12))
        await execute(
            """
            INSERT INTO account_daily_metrics (
                platform_id, date, followers, views, reach, impressions, watch_time_sec, engagement_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform_id, date) DO UPDATE SET
                followers = excluded.followers, views = excluded.views, engagement_count = excluded.engagement_count
            """,
            ["tiktok", d_str, current_followers["tiktok"], tt_views, tt_views, tt_views, 0.0, tt_engagement]
        )

        # Sentiment & Tone Daily Metrics
        for plat_name, eng_val in [("youtube", yt_engagement), ("instagram", ig_engagement), ("tiktok", tt_engagement)]:
            pos_cnt = int(eng_val * random.uniform(0.68, 0.82))
            neu_cnt = int(eng_val * random.uniform(0.12, 0.22))
            neg_cnt = max(1, eng_val - pos_cnt - neu_cnt)
            t_enth = int(pos_cnt * random.uniform(0.55, 0.70))
            t_info = int(pos_cnt * random.uniform(0.20, 0.35))
            t_cur = int(neu_cnt * random.uniform(0.60, 0.80))
            t_crit = neg_cnt
            dom_tone = "Antusias & Apresiatif" if t_enth >= t_info else "Informatif & Edukatif"
            
            quotes_pos = [
                "Setup-nya clean banget bang, terinspirasi buat rapihin meja kerja!",
                "Penjelasan arsitektur n8n dan webhook-nya daging semua, gampang dipraktekin.",
                "Review laptop-nya objektif banget, gak melulu muji brand doang.",
                "Tips produktivitasnya praktis, udah aku coba dan beneran ngefek!",
                "Suka banget sama visual editing dan audionya yang nyaman didenger.",
            ]
            quotes_neg = [
                "Mic di menit awal agak sedikit mendem bang, tapi kontennya tetep oke.",
                "Bisa bikinin tutorial versi yang open-source tanpa bayar gak min?",
                "Penjelasan bagian OAuth agak kecepetan langkah-langkahnya.",
                "Font code editor di video kekecilan pas ditonton di layar smartphone.",
            ]
            pos_q = random.choice(quotes_pos)
            neg_q = random.choice(quotes_neg)

            await execute(
                """
                INSERT INTO sentiment_daily_metrics (
                    platform_id, date, positive_count, neutral_count, negative_count,
                    tone_enthusiastic, tone_informative, tone_curious, tone_critical,
                    dominant_tone, sample_positive_quote, sample_negative_quote
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform_id, date) DO UPDATE SET
                    positive_count = excluded.positive_count,
                    neutral_count = excluded.neutral_count,
                    negative_count = excluded.negative_count,
                    tone_enthusiastic = excluded.tone_enthusiastic,
                    tone_informative = excluded.tone_informative,
                    tone_curious = excluded.tone_curious,
                    tone_critical = excluded.tone_critical,
                    dominant_tone = excluded.dominant_tone
                """,
                [plat_name, d_str, pos_cnt, neu_cnt, neg_cnt, t_enth, t_info, t_cur, t_crit, dom_tone, pos_q, neg_q]
            )

    print("Membuat konten dan metrik konten (Posts & Post Metrics)...")
    sample_posts = [
        ("youtube", "yt_001", "Setup Workspace Minimalis 2026 untuk Programmer", "https://youtube.com/watch?v=demo1", "https://images.unsplash.com/photo-1518770660439-4636190af475?w=240&auto=format&fit=crop&q=80", "video", 25),
        ("youtube", "yt_002", "Review Lengkap Laptop AI Terkini: Apakah Worth It?", "https://youtube.com/watch?v=demo2", "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=240&auto=format&fit=crop&q=80", "video", 18),
        ("youtube", "yt_003", "Tips Produktif Belajar Coding Otodidak Tanpa Bootcamp", "https://youtube.com/watch?v=demo3", "https://images.unsplash.com/photo-1517694712202-14dd9538aa97?w=240&auto=format&fit=crop&q=80", "video", 10),
        ("youtube", "yt_004", "Membuat Otomasi Workflow n8n dalam 10 Menit!", "https://youtube.com/watch?v=demo4", "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?w=240&auto=format&fit=crop&q=80", "video", 3),

        ("instagram", "ig_001", "3 Keyboard Mekanikal Terbaik untuk Coding Seharian", "https://instagram.com/p/demo1", "https://images.unsplash.com/photo-1587829741301-dc798b83add3?w=240&auto=format&fit=crop&q=80", "reel", 20),
        ("instagram", "ig_002", "Behind the Scenes Konten Studio Upgrade", "https://instagram.com/p/demo2", "https://images.unsplash.com/photo-1598488035139-bdbb2231ce04?w=240&auto=format&fit=crop&q=80", "post", 14),
        ("instagram", "ig_003", "Cara Mengatur Manajemen Waktu sebagai Tech Creator", "https://instagram.com/p/demo3", "https://images.unsplash.com/photo-1434030216411-0b793f4b4173?w=240&auto=format&fit=crop&q=80", "reel", 5),

        ("tiktok", "tt_001", "Life hack rahasia VS Code yang wajib kamu coba! #coding #developer", "https://tiktok.com/@demo/video/1", "https://images.unsplash.com/photo-1555066931-4365d14bab8c?w=240&auto=format&fit=crop&q=80", "video", 22),
        ("tiktok", "tt_002", "Reaksi pas nemu bug di production jam 5 sore 😭 #techhumor", "https://tiktok.com/@demo/video/2", "https://images.unsplash.com/photo-1531482615713-2afd69097998?w=240&auto=format&fit=crop&q=80", "video", 12),
        ("tiktok", "tt_003", "Setup monitor vertikal untuk debugging log, game changer!", "https://tiktok.com/@demo/video/3", "https://images.unsplash.com/photo-1547082299-de196ea013d6?w=240&auto=format&fit=crop&q=80", "video", 4),
    ]

    for plat, ext_id, title, url, thumb, p_type, days_ago in sample_posts:
        p_id = f"{plat}_{ext_id}"
        pub_date = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        await execute(
            """
            INSERT INTO posts (id, platform_id, external_id, title, url, thumbnail_url, post_type, published_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(external_id) DO UPDATE SET title = excluded.title
            """,
            [p_id, plat, ext_id, title, url, thumb, p_type, pub_date]
        )

        # Generate metrics for this post
        p_views = random.randint(5000, 35000)
        p_likes = int(p_views * random.uniform(0.04, 0.09))
        p_comments = int(p_views * random.uniform(0.005, 0.015))
        p_shares = int(p_views * random.uniform(0.008, 0.02))
        p_saves = int(p_views * random.uniform(0.01, 0.03))
        avg_watch = round(random.uniform(25.0, 95.0), 1)

        await execute(
            """
            INSERT INTO post_daily_metrics (post_id, date, views, likes, comments, shares, saves, avg_watch_sec)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(post_id, date) DO UPDATE SET views = excluded.views, likes = excluded.likes
            """,
            [p_id, pub_date, p_views, p_likes, p_comments, p_shares, p_saves, avg_watch]
        )

    print("Membuat contoh Target KPI aktif...")
    start_month = today.replace(day=1).strftime("%Y-%m-%d")
    end_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    end_month_str = end_month.strftime("%Y-%m-%d")

    sample_targets = [
        (str(uuid.uuid4()), None, "views", "monthly", 350000.0, start_month, end_month_str),
        (str(uuid.uuid4()), "youtube", "views", "monthly", 120000.0, start_month, end_month_str),
        (str(uuid.uuid4()), "instagram", "followers", "monthly", 25000.0, start_month, end_month_str),
        (str(uuid.uuid4()), "tiktok", "views", "monthly", 200000.0, start_month, end_month_str),
    ]

    for t in sample_targets:
        await execute(
            """
            INSERT INTO kpi_targets (id, platform_id, metric, period, target_value, start_date, end_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            list(t)
        )

    # Ingest logs
    await execute(
        """
        INSERT INTO ingest_logs (id, source, status, rows_upserted, error, created_at)
        VALUES 
        (?, 'demo_youtube_sync', 'success', 90, NULL, ?),
        (?, 'demo_instagram_sync', 'success', 90, NULL, ?),
        (?, 'demo_tiktok_sync', 'success', 90, NULL, ?)
        """,
        [
            str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(),
            str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(),
            str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(),
        ]
    )

    print("Sukses! 90 hari data demo realistis berhasil dimasukkan.")
    await close_client()


def main():
    parser = argparse.ArgumentParser(description="Seed demo data for Social Media KPI Dashboard.")
    parser.add_argument("--reset", action="store_true", help="Reset existing data before seeding")
    args = parser.parse_args()

    asyncio.run(seed(reset=args.reset))


if __name__ == "__main__":
    main()
