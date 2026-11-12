-- Migration 010: Google Maps Reviews Schema

CREATE TABLE IF NOT EXISTS google_reviews (
    review_id TEXT PRIMARY KEY,
    place_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    rating INTEGER NOT NULL,
    snippet TEXT,
    sentiment TEXT NOT NULL,         -- 'positive', 'neutral', 'negative'
    published_at TEXT NOT NULL,      -- 'YYYY-MM-DD HH:MM:SS' (WIB)
    published_date TEXT NOT NULL,    -- 'YYYY-MM-DD'
    relative_date TEXT,              -- e.g. '2 minggu lalu'
    review_link TEXT,
    owner_reply TEXT,
    raw_json TEXT,
    synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gr_place_id ON google_reviews(place_id);
CREATE INDEX IF NOT EXISTS idx_gr_published_date ON google_reviews(published_date);
CREATE INDEX IF NOT EXISTS idx_gr_rating ON google_reviews(rating);
CREATE INDEX IF NOT EXISTS idx_gr_sentiment ON google_reviews(sentiment);
