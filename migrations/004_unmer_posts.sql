-- Migration 004: Portal unmer.ac.id Posts Monitoring Schema

CREATE TABLE IF NOT EXISTS unmer_posts (
    id INTEGER PRIMARY KEY,             -- WordPress Post ID (misal: 26965)
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    link TEXT NOT NULL,
    excerpt TEXT,
    featured_image_url TEXT,
    category_id INTEGER NOT NULL,       -- 487 (Berita) atau 919 (Artikel)
    category_name TEXT NOT NULL,        -- 'Berita' atau 'Artikel'
    published_at TEXT NOT NULL,         -- Format ISO atau timestamp string asli
    published_date TEXT NOT NULL,       -- YYYY-MM-DD untuk kemudahan filter & grouping
    author_name TEXT,
    synced_at TEXT NOT NULL             -- ISO-8601 saat data disimpan
);

CREATE INDEX IF NOT EXISTS idx_unmer_cat ON unmer_posts(category_name);
CREATE INDEX IF NOT EXISTS idx_unmer_pubdate ON unmer_posts(published_date);
CREATE INDEX IF NOT EXISTS idx_unmer_cat_id ON unmer_posts(category_id);
