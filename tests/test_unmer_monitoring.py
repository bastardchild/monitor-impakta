import pytest
from app.db import execute, fetch_all, fetch_one
from app.services.unmer_service import _clean_html_text, get_unmer_summary, get_unmer_posts


def test_clean_html_text():
    raw = "<p>Halo &amp; selamat datang di <strong>UNMER</strong> Malang!&#8211; Kampus Berdampak</p>"
    cleaned = _clean_html_text(raw)
    assert cleaned == "Halo & selamat datang di UNMER Malang!– Kampus Berdampak"
    assert "<" not in cleaned
    assert ">" not in cleaned


@pytest.mark.asyncio
async def test_unmer_posts_storage_and_queries(client):
    # Ensure unmer_posts table exists in test DB
    await execute(
        """
        CREATE TABLE IF NOT EXISTS unmer_posts (
            id INTEGER PRIMARY KEY,
            slug TEXT NOT NULL,
            title TEXT NOT NULL,
            link TEXT NOT NULL,
            excerpt TEXT,
            featured_image_url TEXT,
            category_id INTEGER NOT NULL,
            category_name TEXT NOT NULL,
            published_at TEXT NOT NULL,
            published_date TEXT NOT NULL,
            author_name TEXT,
            synced_at TEXT NOT NULL
        )
        """
    )

    # Insert test dummy posts
    await execute(
        """
        INSERT OR REPLACE INTO unmer_posts (
            id, slug, title, link, excerpt, featured_image_url,
            category_id, category_name, published_at, published_date,
            author_name, synced_at
        ) VALUES 
        (101, 'berita-satu', 'Berita Wisuda 2026', 'https://unmer.ac.id/berita-satu', 'Kutipan berita', 'https://unmer.ac.id/img1.jpg', 487, 'Berita', '2026-10-01T10:00:00', '2026-10-01', 'Humas', '2026-10-04T12:00:00'),
        (102, 'artikel-satu', 'Artikel Artificial Intelligence', 'https://unmer.ac.id/artikel-satu', 'Kutipan artikel', '', 919, 'Artikel', '2026-10-02T10:00:00', '2026-10-02', 'Dosen', '2026-10-04T12:00:00')
        """
    )

    summary = await get_unmer_summary()
    assert summary["total_posts"] == 2
    assert summary["total_berita"] == 1
    assert summary["total_artikel"] == 1

    # Filter category
    berita_list = await get_unmer_posts(category="berita")
    assert len(berita_list) == 1
    assert berita_list[0]["category_name"] == "Berita"

    artikel_list = await get_unmer_posts(category="artikel")
    assert len(artikel_list) == 1
    assert artikel_list[0]["category_name"] == "Artikel"

    # Search keyword
    search_res = await get_unmer_posts(search="Wisuda")
    assert len(search_res) == 1
    assert search_res[0]["title"] == "Berita Wisuda 2026"


@pytest.mark.asyncio
async def test_unmer_ui_and_partial_endpoints(client):
    # Test GET /unmer with authenticated session
    resp = await client.get("/unmer", follow_redirects=True)
    assert resp.status_code == 200
    assert "Monitoring Portal unmer.ac.id" in resp.text
    assert "IMPAKTA RADAR" in resp.text

    # Test GET /partials/unmer/posts
    part_resp = await client.get("/partials/unmer/posts?category=berita")
    assert part_resp.status_code == 200
    assert "data-table" in part_resp.text
