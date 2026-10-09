-- Migration 008: Add pageviews column to unmer_posts table
-- Tracks public reader views extracted from WordPress ThemeREX category archives

ALTER TABLE unmer_posts ADD COLUMN pageviews INTEGER DEFAULT 0;
