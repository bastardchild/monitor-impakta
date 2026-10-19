-- 009_composite_indexes.sql
-- Optimasi Composite Indexes untuk efisiensi row reads pada snapshot terkini dan filter platform

CREATE INDEX IF NOT EXISTS idx_pdm_post_date_desc ON post_daily_metrics(post_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_posts_plat_pub_desc ON posts(platform_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_adm_plat_date_desc ON account_daily_metrics(platform_id, date DESC);
