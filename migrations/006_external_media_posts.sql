-- Migration 006: External News Posts Schema (Multi-Source External Media)

CREATE TABLE IF NOT EXISTS external_news_posts (
    id TEXT PRIMARY KEY,               -- e.g. 'times_indonesia_611595'
    source_id TEXT NOT NULL,           -- 'times_indonesia' (siap untuk media lain)
    source_name TEXT NOT NULL,         -- 'TIMES Indonesia'
    external_id TEXT NOT NULL,         -- '611595'
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    excerpt TEXT,
    featured_image_url TEXT,
    category_name TEXT NOT NULL,       -- 'Indonesia Positif', 'Pendidikan', etc.
    author_name TEXT,                  -- Wartawan / Editor
    city TEXT,                         -- 'MALANG'
    pageviews INTEGER DEFAULT 0,       -- Metrik pembaca
    published_at TEXT NOT NULL,        -- '2026-10-06 08:51:00'
    published_date TEXT NOT NULL,      -- '2026-10-06' (YYYY-MM-DD)
    synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ext_source ON external_news_posts(source_id);
CREATE INDEX IF NOT EXISTS idx_ext_pubdate ON external_news_posts(published_date);
CREATE INDEX IF NOT EXISTS idx_ext_cat ON external_news_posts(category_name);
