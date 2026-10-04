-- Migration 003: Standardized System & Error Logs Schema

CREATE TABLE IF NOT EXISTS system_error_logs (
    id TEXT PRIMARY KEY,
    error_code TEXT NOT NULL,
    category TEXT NOT NULL,          -- OAUTH, INGEST, AUTH, DATABASE, BACKGROUND, SYSTEM
    message TEXT NOT NULL,
    path TEXT,
    method TEXT,
    platform_id TEXT,
    status_code INTEGER DEFAULT 500,
    exception_type TEXT,
    details_json TEXT,              -- JSON context (sanitized)
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sel_created ON system_error_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_sel_category ON system_error_logs(category);
CREATE INDEX IF NOT EXISTS idx_sel_platform ON system_error_logs(platform_id);
CREATE INDEX IF NOT EXISTS idx_sel_error_code ON system_error_logs(error_code);
