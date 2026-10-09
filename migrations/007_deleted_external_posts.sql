-- Migration 007: Deleted External Posts Tracking (Prevent irrelevant news from resurfacing on sync)

CREATE TABLE IF NOT EXISTS deleted_external_posts (
    id TEXT PRIMARY KEY,
    deleted_at TEXT NOT NULL
);
