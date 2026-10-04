# PERAN
Kamu adalah senior full-stack engineer yang berpengalaman membangun dashboard analitik berbasis Python dan hypermedia. Bangun proyek secara lengkap, rapi, dan siap dijalankan. Jangan tinggalkan placeholder "TODO".

# TUJUAN
Buat **Social Media KPI Dashboard pribadi** (single-user, BUKAN SaaS) yang mengonsolidasikan performa **YouTube, TikTok, dan Instagram**. Pemilik cukup login ke dashboard, buka **Pengaturan → Koneksi Akun**, klik tombol **"Hubungkan"** per platform, login/izinkan di halaman resmi platform, lalu data tersinkron otomatis lewat n8n.

# TECH STACK (WAJIB)
- **n8n** (self-hosted, Docker): orkestrasi sync terjadwal dan manual
- **FastAPI** (Python 3.12, async): backend, SSR Jinja2, DAN penanganan OAuth 2.0 semua platform
- **HTMX**: interaksi server-driven (partial swap, polling, filter, tombol aksi)
- **Alpine.js**: state UI ringan saja (dropdown, tab, modal konfirmasi, toggle tema, date-range)
- **Turso Cloud** (libSQL): database utama (URL dan auth token dari env)
- **Docker + docker-compose**: semua service berjalan via compose
- Tailwind CSS (CDN) + Chart.js (CDN). Tanpa build step frontend, tanpa React/Vue.
- Library: `httpx` (HTTP client async), `cryptography` (Fernet), `itsdangerous`/`starlette SessionMiddleware`, `apscheduler`, `pydantic-settings`, `pytest`, `respx` (mock HTTP di tes)

# ARSITEKTUR
1. **Koneksi:** Pengaturan (tombol) → FastAPI redirect ke halaman otorisasi platform → callback ke FastAPI → tukar code jadi token → enkripsi → simpan di Turso.
2. **Sync:** n8n (cron atau webhook tombol "Sync sekarang") → `GET /api/internal/credentials/{platform}` ke FastAPI (dapat access token yang dijamin masih valid) → n8n memanggil API platform → normalisasi → `POST /api/ingest/*` → upsert ke Turso.
3. **Tampil:** dashboard membaca Turso → render HTML + partial HTMX.
n8n TIDAK menyimpan token platform. Satu sumber kebenaran token adalah FastAPI/Turso.

# MODE PERSONAL (batasan desain)
- Satu user admin, kredensial dari env (`ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH`), login via form + session cookie (HttpOnly, SameSite=Lax, Secure di produksi).
- Satu akun per platform (YouTube 1 channel, Instagram 1 akun, TikTok 1 akun). Tidak ada tabel user/tenant. Menghubungkan ulang platform yang sama menggantikan koneksi lama.
- Tidak perlu App Review: semua app OAuth tetap di mode Development/Sandbox/Testing dengan akun sendiri sebagai tester. Jelaskan ini di README.

# OAUTH DI FASTAPI

## Struktur
`app/oauth/base.py` (kelas abstrak `OAuthProvider`: `build_auth_url`, `exchange_code`, `refresh`, `revoke`, `fetch_account_info`), `app/oauth/google.py`, `app/oauth/meta.py`, `app/oauth/tiktok.py`, `app/services/token_service.py`, `app/services/crypto.py`.

## Endpoint
- `GET /settings/connections` → halaman Koneksi Akun
- `GET /connect/{platform}` → buat `state` acak (+ PKCE `code_verifier` bila platform mendukung), simpan di session, redirect ke URL otorisasi
- `GET /connect/{platform}/callback` → validasi `state` (tolak jika tidak cocok → CSRF), tukar `code` jadi token, ambil info akun, simpan terenkripsi, redirect ke `/settings/connections` dengan flash message sukses/gagal
- `POST /disconnect/{platform}` → revoke token di platform (best effort), hapus dari DB (konfirmasi modal Alpine)
- `POST /connections/{platform}/sync` → memicu webhook n8n platform tersebut (`N8N_WEBHOOK_URL_{PLATFORM}`), return fragment status via HTMX
- `GET /partials/connection-status` → kartu status, auto-refresh `hx-trigger="load, every 30s"`
- `GET /api/internal/credentials/{platform}` → khusus n8n (header `X-API-Key`): kembalikan `access_token` yang sudah di-refresh jika kedaluwarsa dalam < 10 menit, plus `account_id`, `account_handle`, `expires_at`. Status 409 bila belum terhubung atau `needs_reauth`. Jangan pernah log token.

## Detail per provider
**Google / YouTube**
- Auth: `https://accounts.google.com/o/oauth2/v2/auth` dengan `access_type=offline`, `prompt=consent`, `include_granted_scopes=true`
- Token/refresh: `https://oauth2.googleapis.com/token`; revoke: `https://oauth2.googleapis.com/revoke`
- Scope: `https://www.googleapis.com/auth/youtube.readonly`, `https://www.googleapis.com/auth/yt-analytics.readonly`
- Info akun: `GET https://www.googleapis.com/youtube/v3/channels?part=id,snippet&mine=true` (simpan channel id, title, handle)
- Refresh token tidak berubah; access token ±1 jam.

**Meta / Instagram (Facebook Login)**
- Auth: `https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth`; token: `https://graph.facebook.com/{GRAPH_VERSION}/oauth/access_token`; `GRAPH_VERSION` dari env
- Permission: `instagram_basic`, `instagram_manage_insights`, `pages_show_list`, `pages_read_engagement`, `business_management`
- Alur: code → short-lived user token → tukar ke **long-lived** (`grant_type=fb_exchange_token`, ±60 hari) → `GET /me/accounts?fields=id,name,access_token,instagram_business_account{id,username}` → pilih Page yang punya `instagram_business_account`; jika lebih dari satu, tampilkan pilihan (halaman pilih akun sederhana via HTMX) → simpan IG user id + username
- Refresh: sebelum kedaluwarsa (≤ 7 hari), tukar ulang token long-lived dengan `fb_exchange_token`. Bila gagal/kedaluwarsa → status `needs_reauth`.
- Tampilkan pesan jelas bila akun bukan Business/Creator atau belum terhubung ke Facebook Page.

**TikTok**
- Auth: `https://www.tiktok.com/v2/auth/authorize/` (param `client_key`, `scope`, `response_type=code`, `redirect_uri`, `state`)
- Token & refresh: `POST https://open.tiktokapis.com/v2/oauth/token/` (form-urlencoded; `client_key`, `client_secret`, `code`/`refresh_token`, `grant_type`, `redirect_uri`)
- Scope: `user.info.basic`, `user.info.stats`, `video.list`
- Info akun: `GET https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name,username,avatar_url`
- Access token ±24 jam, refresh token ±365 hari. Refresh token bisa berganti setiap refresh → SELALU simpan refresh token terbaru dari respons.
- Redirect URI harus HTTPS dan terdaftar persis sama di portal developer.

## Penyimpanan & enkripsi token
- Enkripsi `access_token` dan `refresh_token` dengan **Fernet** (`TOKEN_ENCRYPTION_KEY` dari env) sebelum disimpan. Sediakan script `scripts/generate_keys.py` untuk membuat key, password hash, dan API key.
- Token tidak pernah dikirim ke browser, tidak pernah tampil di log atau halaman.
- Refresh dengan **lock per platform** (`asyncio.Lock`) agar tidak terjadi refresh ganda yang membatalkan refresh token.
- Dua lapis refresh: (1) lazy, saat n8n meminta kredensial; (2) job APScheduler tiap 30 menit yang me-refresh token yang akan kedaluwarsa. Bila refresh gagal berulang (3x, backoff) → status `needs_reauth` dan tampilkan banner di dashboard.

# UI HALAMAN KONEKSI AKUN (`/settings/connections`)
Tiga kartu (YouTube, Instagram, TikTok), masing-masing menampilkan:
- Status: `Belum terhubung` (abu), `Terhubung` (hijau), `Segera kedaluwarsa` (kuning), `Perlu hubungkan ulang` (merah)
- Nama/handle akun + avatar, tanggal terhubung, `expires_at` (waktu relatif, Asia/Jakarta), waktu sync terakhir dan hasilnya (dari `ingest_logs`)
- Tombol: **Hubungkan** (link ke `/connect/{platform}`), **Hubungkan ulang**, **Sync sekarang** (hx-post, spinner `htmx-indicator`, hasil via swap), **Putuskan** (modal konfirmasi Alpine)
- Pesan error ramah (contoh: "Akun Instagram harus bertipe Business/Creator dan terhubung ke Facebook Page")
- Panel kecil "Panduan setup" yang menampilkan Redirect URI yang harus didaftarkan di tiap portal developer (dibaca dari `BASE_URL`)

# KPI YANG DITAMPILKAN
**Global:** total followers/subscribers dan pertumbuhan (abs & %), total views/reach, engagement rate = (likes+comments+shares+saves)/views atau reach, jumlah posting dan frekuensi per minggu, skor pencapaian KPI vs target (progress bar hijau/kuning/merah).
**YouTube:** subscribers, views, watch time (jam), avg view duration, likes/comments/shares, top video.
**Instagram:** followers, reach, profile views, saves, shares, engagement rate, top post/reel.
**TikTok:** followers, video views, likes/comments/shares, top video. (Display API TIDAK menyediakan watch time/completion rate: jangan tampilkan metrik itu.)
Catatan data historis: API Instagram dan TikTok tidak selalu memberi riwayat harian untuk followers. Simpan **snapshot harian** tiap sync dan hitung pertumbuhan dari selisih snapshot. Tulis ini di "Asumsi & Batasan".

# DATA MODEL (Turso/libSQL)
Migrasi SQL di `migrations/*.sql` dengan runner sederhana saat startup (tabel `schema_migrations`). Tabel:
- `platforms` (id, name UNIQUE, handle, account_id)
- `connected_accounts` (platform_id UNIQUE, access_token_enc, refresh_token_enc, token_type, scopes, expires_at, refresh_expires_at, external_account_id, display_name, avatar_url, status [connected/needs_reauth/disconnected], last_refresh_at, last_error, connected_at, updated_at)
- `account_daily_metrics` (platform_id, date, followers, views, reach, impressions, watch_time_sec, engagement_count, UNIQUE(platform_id, date))
- `posts` (id, platform_id, external_id UNIQUE, title, url, thumbnail_url, post_type, published_at)
- `post_daily_metrics` (post_id, date, views, likes, comments, shares, saves, avg_watch_sec, UNIQUE(post_id, date))
- `kpi_targets` (platform_id nullable, metric, period [weekly/monthly], target_value, start_date, end_date)
- `ingest_logs` (source, status, rows_upserted, error, created_at)
Semua ingest idempoten (`ON CONFLICT DO UPDATE`), parameterized query, index pada kolom tanggal dan platform_id.

# ENDPOINT LAIN FASTAPI
- Auth: `GET/POST /login`, `POST /logout`; semua halaman dan partial wajib login kecuali `/login` dan `/health`
- `POST /api/ingest/account-metrics`, `/api/ingest/posts`, `/api/ingest/post-metrics` (Pydantic, `X-API-Key`, batch upsert)
- `GET /` overview, `GET /platform/{name}` detail
- `GET /partials/kpi-cards`, `/partials/trend-chart`, `/partials/top-posts`, `/partials/kpi-targets` (query `range`, `platform`)
- `GET/POST /settings/targets` (CRUD target KPI via form HTMX)
- `GET /health`
Gunakan dependency injection, async, logging terstruktur (JSON), middleware yang menyamarkan field `token`/`secret`/`authorization` di log.

# FRONTEND (HTMX + Alpine.js)
- Layout: sidebar (Overview, YouTube, Instagram, TikTok, Target KPI, Pengaturan), topbar dengan date-range dan dark mode
- Filter periode (7d/30d/90d/custom) memakai `hx-get` + `hx-target` + `hx-push-url`
- Kartu KPI `hx-trigger="load, every 300s"`; skeleton loading, empty state ("Hubungkan akun di Pengaturan"), error state
- Chart.js: line chart tren, bar chart perbandingan platform; data via tag JSON di partial, diinisialisasi di komponen Alpine (`x-data`, `x-init`), destroy chart lama saat swap
- Responsif mobile-first, format angka Indonesia (1,2 jt / 3,4 rb), zona waktu Asia/Jakarta
- Alpine hanya untuk state UI; semua data dari server

# WORKFLOW N8N (folder `n8n/workflows/`, JSON siap impor)
Tiap workflow punya dua trigger: **Schedule** (default tiap 6 jam) dan **Webhook** (dipanggil tombol "Sync sekarang"; path `/webhook/sync-{platform}`, dilindungi header secret).
1. `youtube_sync.json`: HTTP Request `GET http://app:8000/api/internal/credentials/youtube` → ambil `access_token` → YouTube Data API (`channels?part=statistics&mine=true`, `playlistItems`/`videos` untuk daftar video + statistik) dan YouTube Analytics API (`reports?ids=channel==MINE`, metrik views, estimatedMinutesWatched, averageViewDuration, subscribersGained/Lost, likes, comments, shares; dimensi day) → Code node normalisasi → POST ke `/api/ingest/*`
2. `instagram_sync.json`: ambil kredensial → Graph API: info akun (`followers_count`, `media_count`), insight akun (reach, profile views, dll., sesuai versi API terbaru), daftar media dan insight per media (views/reach, likes, comments, saves, shares) dengan pagination → normalisasi → POST
3. `tiktok_sync.json`: ambil kredensial → `GET /v2/user/info/?fields=open_id,display_name,follower_count,likes_count,video_count` → `POST /v2/video/list/` (fields: id, title, cover_image_url, share_url, create_time, view_count, like_count, comment_count, share_count; `max_count` 20, pagination via `cursor`/`has_more`) → normalisasi → POST
4. `error_alert.json`: Error Trigger → notifikasi Telegram/Email
Aturan: Authorization header dibuat dari output node kredensial (BUKAN hardcode dan BUKAN n8n credential OAuth), `Retry On Fail` (3x, backoff), tangani HTTP 429 dengan wait, bila `/credentials` mengembalikan 409 workflow berhenti dengan pesan jelas dan mencatat ke `ingest_logs`. Header `X-API-Key` dari n8n Credentials (Header Auth) atau env.

# DOCKER
- `Dockerfile` multi-stage FastAPI (non-root, uvicorn, healthcheck)
- `docker-compose.yml`: service `app`, `n8n` (volume persisten), dan **opsional** `cloudflared` (profile `tunnel`) untuk URL HTTPS publik tanpa buka port; network internal, n8n memanggil `http://app:8000`
- `.env.example`: `BASE_URL` (HTTPS publik, dipakai membentuk redirect URI), `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`, `APP_SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, `INGEST_API_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH`, `GOOGLE_CLIENT_ID/SECRET`, `META_APP_ID/SECRET`, `META_GRAPH_VERSION`, `TIKTOK_CLIENT_KEY/SECRET`, `N8N_WEBHOOK_URL_YOUTUBE/INSTAGRAM/TIKTOK`, `N8N_WEBHOOK_SECRET`, `N8N_ENCRYPTION_KEY`, `N8N_BASIC_AUTH_*`, `TZ=Asia/Jakarta`
- Redirect URI otomatis: `{BASE_URL}/connect/{youtube|instagram|tiktok}/callback`
- `docker compose up -d --build` harus cukup untuk menjalankan semuanya

# KEAMANAN
Session cookie aman, CSRF protection untuk semua POST (token di form HTMX via `hx-headers`), validasi `state` OAuth, rate-limit sederhana pada `/login`, header keamanan dasar, parameterized query, tidak ada secret di repo, endpoint `/api/internal/*` dan `/api/ingest/*` hanya dengan API key (bandingkan memakai `secrets.compare_digest`).

# SEED & DEMO
`scripts/seed_demo.py` mengisi data dummy 90 hari untuk tiga platform dan status koneksi palsu ("demo"), agar dashboard bisa dilihat sebelum OAuth dikonfigurasi. Beri flag `--reset`.

# TES (pytest)
- Enkripsi/dekripsi token (Fernet) dan tidak bocor di log
- Validasi `state` OAuth (cocok/tidak cocok)
- Alur callback tiap provider dengan `respx` (mock endpoint token dan info akun)
- Logika refresh (lazy, lock, token TikTok berganti, gagal → `needs_reauth`)
- `/api/internal/credentials/{platform}` (auth, 409 saat belum terhubung)
- Endpoint ingest (idempoten, auth) dan perhitungan KPI/pertumbuhan

# DELIVERABLES
1. Struktur folder lengkap + seluruh source code
2. Migrasi, seed, Dockerfile, compose, `.env.example`, `scripts/generate_keys.py`
3. Workflow n8n JSON
4. `README.md` berbahasa Indonesia berisi:
   - Setup Turso (`turso db create`, `turso db show --url`, `turso db tokens create`)
   - Cara membuat URL HTTPS (Cloudflare Tunnel / domain) dan mengisi `BASE_URL`
   - Panduan portal developer per platform, lengkap dengan Redirect URI, scope, dan cara menambahkan diri sebagai tester:
     • Google Cloud: aktifkan YouTube Data API v3 + YouTube Analytics API, OAuth consent screen, tambahkan email sendiri sebagai Test user, lalu **ubah ke "In production"** agar refresh token tidak kedaluwarsa 7 hari
     • Meta for Developers: app tipe Business, tambah Facebook Login, daftarkan Redirect URI, Page + akun IG Business/Creator, mode Development dengan akun sendiri sebagai admin/tester
     • TikTok for Developers: buat app, tambah Login Kit + Display API, daftarkan Redirect URI, tambahkan akun sendiri sebagai target user di Sandbox
   - Langkah: generate key → isi `.env` → `docker compose up` → login → Pengaturan → klik Hubungkan → import workflow n8n → aktifkan → Sync sekarang
   - Troubleshooting (redirect_uri_mismatch, token kedaluwarsa, akun IG tidak ditemukan, TikTok sandbox, n8n tidak bisa memanggil app)
   - Bagian "Asumsi & Batasan" (field API yang bisa berubah, keterbatasan metrik TikTok, snapshot harian)

# CARA KERJA
- Mulai dengan rencana singkat (struktur folder, skema DB, alur OAuth), lalu implementasikan bertahap: (1) DB + auth + seed, (2) OAuth + halaman Koneksi, (3) ingest + KPI + dashboard UI, (4) n8n + Docker + README
- Setelah selesai: jalankan tes, pastikan aplikasi start tanpa error, seed tampil di dashboard, dan alur `/connect/*` bisa diuji dengan mock
- Cek dokumentasi resmi terbaru tiap API sebelum menulis endpoint/field; bila berbeda dari prompt ini, ikuti dokumentasi dan catat di "Asumsi & Batasan"


# DOCKER-FIRST (WAJIB, MENGGANTIKAN BAGIAN "# DOCKER" SEBELUMNYA)

## Prinsip
Seluruh proyek HARUS berjalan, diuji, dan dikelola di dalam container. Host hanya membutuhkan Docker + Docker Compose v2 (dan `make` bersifat opsional). Dilarang ada langkah README yang meminta `pip install`, `npm install`, atau menjalankan Python langsung di host. Tidak ada build step frontend: Tailwind dan Chart.js via CDN.

## Service di docker-compose.yml
1. `app`: FastAPI + APScheduler.
   - Build dari `Dockerfile` multi-stage (builder → runtime `python:3.12-slim`), user non-root, `PYTHONUNBUFFERED=1`, healthcheck ke `/health`.
   - Jalankan uvicorn dengan **1 worker** (agar job APScheduler dan lock refresh token tidak ganda).
   - Migrasi dijalankan otomatis saat startup (idempoten).
   - Port dipublish hanya ke `127.0.0.1:8000:8000`.
2. `n8n`: image resmi `n8nio/n8n` dengan versi dipin (bukan `latest`).
   - Volume named `n8n_data`; env `N8N_ENCRYPTION_KEY`, `GENERIC_TIMEZONE=Asia/Jakarta`, `TZ`, `WEBHOOK_URL`, `N8N_HOST`, `N8N_PROTOCOL`.
   - Mount `./n8n/workflows:/workflows:ro`.
   - Port hanya ke `127.0.0.1:5678:5678` (n8n TIDAK diekspos ke internet).
   - Cek dokumentasi n8n versi terpin untuk metode auth terbaru (owner setup vs basic auth) dan ikuti yang berlaku.
   - Jika workflow membaca env var lewat expression, set `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` dan catat risikonya di README.
3. `n8n-import`: service one-shot (`restart: "no"`), `depends_on: n8n (service_healthy)`, menjalankan `n8n import:workflow --separate --input=/workflows` agar workflow terimpor otomatis. Tulis di README cara mengaktifkan workflow setelah impor.
4. `cloudflared` (profile `tunnel`): Cloudflare Tunnel, `TUNNEL_TOKEN` dari env. Hanya route yang perlu publik: hostname → `http://app:8000` (untuk callback OAuth). n8n tetap privat.
5. `tools` (profile `tools`): memakai image yang sama dengan `app`, untuk tugas satu kali. Tidak berjalan otomatis.
6. `tests` (profile `test`): image yang sama dengan dependency dev (pytest, respx, pytest-asyncio), menjalankan `pytest -q`.
7. Turso adalah layanan cloud, jadi TIDAK ada container database. Tes memakai file libSQL lokal sementara di dalam container `tests` (tanpa menyentuh Turso produksi).

## Aturan compose
- Network bridge internal `kpi_net`; antar-service pakai hostname (`http://app:8000`, `http://n8n:5678`). Tombol "Sync sekarang" memanggil `http://n8n:5678/webhook/sync-{platform}` lewat network internal.
- `restart: unless-stopped`, healthcheck di `app` dan `n8n`, `depends_on` dengan `condition: service_healthy`.
- Env dibaca dari `.env`; tidak ada secret di image atau repo. `.dockerignore` dan `.gitignore` lengkap (`.env`, `__pycache__`, data lokal).
- Log driver `json-file` dengan `max-size`/`max-file` agar disk tidak penuh.
- `docker-compose.override.yml` untuk development: mount source code, uvicorn `--reload`, tanpa mengubah konfigurasi produksi. Compose produksi tidak boleh mount source.
- Jalankan proses app dengan filesystem read-only bila memungkinkan (`read_only: true` + `tmpfs: /tmp`); bila menyulitkan, catat alasannya.

## Perintah yang HARUS bisa dijalankan (semua via container)
```
cp .env.example .env
docker compose run --rm tools python scripts/generate_keys.py   # buat semua key, password hash, API key
docker compose up -d --build                                    # app + n8n + n8n-import
docker compose --profile tunnel up -d                           # + Cloudflare Tunnel
docker compose run --rm tools python scripts/seed_demo.py --reset
docker compose --profile test run --rm tests                    # jalankan pytest
docker compose logs -f app
docker compose down            # data n8n tetap aman di volume
```
Sediakan `Makefile` sebagai pembungkus (`make keys`, `make up`, `make tunnel`, `make seed`, `make test`, `make logs`, `make down`, `make rebuild`) dan jelaskan bahwa Makefile opsional.

## Penyesuaian bagian lain
- `scripts/generate_keys.py` mencetak nilai `TOKEN_ENCRYPTION_KEY`, `APP_SECRET_KEY`, `INGEST_API_KEY`, `N8N_ENCRYPTION_KEY`, `N8N_WEBHOOK_SECRET`, dan meminta password admin secara interaktif untuk menghasilkan `ADMIN_PASSWORD_HASH` (jalankan dengan `docker compose run --rm -it tools ...`). Output ditampilkan di terminal; tidak menimpa `.env` otomatis.
- Semua instruksi README (Turso CLI di luar scope: tampilkan opsi menjalankan Turso CLI via container jika memungkinkan, atau tautkan dokumentasi resmi), seed, tes, dan troubleshooting memakai perintah `docker compose`.
- Tes tidak boleh memanggil API eksternal atau Turso produksi; semua HTTP dimock dengan `respx`.

## Verifikasi akhir (wajib dijalankan sebelum menyatakan selesai)
1. `docker compose config` valid tanpa warning.
2. `docker compose up -d --build` → semua service `healthy`.
3. `docker compose --profile test run --rm tests` lulus.
4. Seed demo tampil di dashboard (`http://127.0.0.1:8000`).
5. Workflow n8n terimpor (`n8n-import` exit code 0).
6. Tidak ada secret yang tercetak di `docker compose logs`.
