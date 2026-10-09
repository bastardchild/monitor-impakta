-- 005_stream_and_privacy.sql
-- Menambahkan kolom streamed_at dan privacy_status pada tabel posts
ALTER TABLE posts ADD COLUMN streamed_at TEXT;
ALTER TABLE posts ADD COLUMN privacy_status TEXT DEFAULT 'public';
