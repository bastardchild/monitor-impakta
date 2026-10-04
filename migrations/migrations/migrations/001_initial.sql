-- Migration 001: Initial Schema for Social Media KPI Dashboard

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platforms (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    handle TEXT,
    account_id TEXT
);

CREATE TABLE IF NOT EXISTS connected_accounts (
    platform_id TEXT PRIMARY KEY REFERENCES platforms(id) ON DELETE CASCADE,
    access_token_enc TEXT,
    refresh_token_enc TEXT,
    token_type TEXT,
    scopes TEXT,
    expires_at INTEGER,
    refresh_expires_at INTEGER,
    external_account_id TEXT,
    display_name TEXT,
    avatar_url TEXT,
    status TEXT NOT NULL DEFAULT 'disconnected',
    last_refresh_at INTEGER,
    last_error TEXT,
    connected_at INTEGER,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS account_daily_metrics (
    platform_id TEXT NOT NULL REFERENCES platforms(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    followers INTEGER NOT NULL DEFAULT 0,
    views INTEGER NOT NULL DEFAULT 0,
    reach INTEGER NOT NULL DEFAULT 0,
    impressions INTEGER NOT NULL DEFAULT 0,
    watch_time_sec REAL NOT NULL DEFAULT 0.0,
    engagement_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (platform_id, date)
);

CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    platform_id TEXT NOT NULL REFERENCES platforms(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL UNIQUE,
    title TEXT,
    url TEXT,
    thumbnail_url TEXT,
    post_type TEXT,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS post_daily_metrics (
    post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    shares INTEGER NOT NULL DEFAULT 0,
    saves INTEGER NOT NULL DEFAULT 0,
    avg_watch_sec REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (post_id, date)
);

CREATE TABLE IF NOT EXISTS kpi_targets (
    id TEXT PRIMARY KEY,
    platform_id TEXT,
    metric TEXT NOT NULL,
    period TEXT NOT NULL,
    target_value REAL NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_logs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    rows_upserted INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_adm_date ON account_daily_metrics(date);
CREATE INDEX IF NOT EXISTS idx_adm_platform ON account_daily_metrics(platform_id);
CREATE INDEX IF NOT EXISTS idx_posts_platform ON posts(platform_id);
CREATE INDEX IF NOT EXISTS idx_posts_published ON posts(published_at);
CREATE INDEX IF NOT EXISTS idx_pdm_date ON post_daily_metrics(date);
CREATE INDEX IF NOT EXISTS idx_pdm_post_id ON post_daily_metrics(post_id);
CREATE INDEX IF NOT EXISTS idx_ingest_created ON ingest_logs(created_at);

-- Initial Platforms Seed
INSERT OR IGNORE INTO platforms (id, name, handle, account_id) VALUES
('youtube', 'YouTube', '', ''),
('instagram', 'Instagram', '', ''),
('tiktok', 'TikTok', '', '');
