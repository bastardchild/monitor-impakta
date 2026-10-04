-- Migration 002: Sentiment & Tone Analytics Schema

CREATE TABLE IF NOT EXISTS sentiment_daily_metrics (
    platform_id TEXT NOT NULL REFERENCES platforms(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    positive_count INTEGER NOT NULL DEFAULT 0,
    neutral_count INTEGER NOT NULL DEFAULT 0,
    negative_count INTEGER NOT NULL DEFAULT 0,
    tone_enthusiastic INTEGER NOT NULL DEFAULT 0,
    tone_informative INTEGER NOT NULL DEFAULT 0,
    tone_curious INTEGER NOT NULL DEFAULT 0,
    tone_critical INTEGER NOT NULL DEFAULT 0,
    dominant_tone TEXT NOT NULL DEFAULT 'Antusias & Apresiatif',
    sample_positive_quote TEXT,
    sample_negative_quote TEXT,
    PRIMARY KEY (platform_id, date)
);

CREATE INDEX IF NOT EXISTS idx_sdm_date ON sentiment_daily_metrics(date);
CREATE INDEX IF NOT EXISTS idx_sdm_platform ON sentiment_daily_metrics(platform_id);
