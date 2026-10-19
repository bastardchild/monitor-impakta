# IMPAKTA - Social Media & Media Monitoring Dashboard

Dashboard analitik kinerja media sosial dan pemantauan media (*single-user*, **BUKAN SaaS**) berbasis **Python (FastAPI)**, **libSQL (Turso Cloud)**, **n8n**, **HTMX**, dan **Alpine.js**.

---

## Daftar Isi
1. [Fitur Utama](#fitur-utama)
2. [Arsitektur Sistem](#arsitektur-sistem)
3. [Tech Stack & Manajemen Dependensi](#tech-stack--manajemen-dependensi)
4. [Prasyarat Host](#prasyarat-host)
5. [Panduan Instalasi Cepat (Development)](#panduan-instalasi-cepat-development)
6. [Panduan Deployment Production (VPS + Host Caddy)](#panduan-deployment-production-vps--host-caddy)
7. [Ganti Password Admin](#ganti-password-admin)
8. [Setup Database Turso Cloud](#setup-database-turso-cloud)
9. [Setup HTTPS & BASE_URL](#setup-https--base_url)
10. [Panduan Portal Developer per Platform](#panduan-portal-developer-per-platform)
    - [Google Cloud / YouTube](#1-google-cloud-console-youtube)
    - [Meta for Developers / Instagram](#2-meta-for-developers-instagram)
    - [TikTok for Developers](#3-tiktok-for-developers)
11. [Orkestrasi n8n & Aktivasi Workflow](#orkestrasi-n8n--aktivasi-workflow)
12. [Perintah Manajemen (Container-First)](#perintah-manajemen-container-first)
13. [Troubleshooting & Pemecahan Masalah](#troubleshooting--pemecahan-masalah)
14. [Asumsi & Batasan Desain](#asumsi--batasan-desain)

---

## Fitur Utama

- **OAuth 2.0 Terpusat di FastAPI**: Koneksi akun satu kali klik langsung dari menu Pengaturan. Token dienkripsi menggunakan Fernet (`cryptography`) sebelum disimpan ke database Turso.
- **KPI Multi-Platform**:
  - **YouTube**: Subscribers, Views, Watch Time (Jam), Rata-rata Durasi Tonton, Interaksi (Likes/Komentar/Share), Top Video.
  - **Instagram**: Followers, Reach (Jangkauan), Profile Views, Saves, Shares, Engagement Rate, Top Reels/Posts.
  - **TikTok**: Followers, Video Views, Likes, Komentar, Shares, Top Video.
- **Monitoring Portal unmer.ac.id**: Sinkronisasi otomatis berita dan artikel resmi UNMER dengan batching efisien, filter kategori, pencarian, dan metrik publikasi.
- **Media Eksternal (Mention Tracker)**: Pemantauan liputan berita dari media eksternal (Sindonews, Antaranews, Kompas, Times Indonesia, dll.).
- **Orkestrasi Otomatis n8n**: Penjadwalan sync berkala dan tombol manual "Sync Sekarang" via webhook internal.
- **Pembaruan Token Otomatis (Dual-Layer Refresh)**:
  1. *Lazy Refresh*: Diperbarui seketika jika masa berlaku token tersisa < 10 menit saat n8n meminta kredensial.
  2. *Background Refresh*: Job APScheduler berkala setiap 30 menit dengan *lock per platform* (`asyncio.Lock`) dan *exponential backoff*.
- **Hypermedia SSR (HTMX + Alpine.js)**: Tanpa build step frontend (React/Vue/Webpack). Interaksi reaktif server-driven dengan partial swapping, polling berkala, dan visualisasi interaktif Chart.js.
- **Target KPI**: Form pembuatan sasaran target performa (mingguan/bulanan) dengan indikator progress bar.

---

## Arsitektur Sistem

```
┌──────────────────────────────────────────────────────────────┐
│                        BROWSER / KLIEN                       │
└──────────────────────────────▲───────────────────────────────┘
                               │ HTTPS / Caddy Reverse Proxy
┌──────────────────────────────▼───────────────────────────────┐
│              FASTAPI APP (monitor_impakta:8000)              │
│  - SSR Jinja2 & Komponen HTMX                                │
│  - Penanganan Alur OAuth (YouTube, Meta, TikTok)             │
│  - Enkripsi Token Fernet & Storage Turso libSQL              │
│  - Monitoring unmer.ac.id & Media Eksternal                  │
│  - APScheduler Background Token Refresher (tiap 30m)         │
│  - Endpoint Kredensial Internal: /api/internal/credentials/* │
│  - Endpoint Ingest Data: /api/ingest/*                       │
└───────────────────────▲──────────────▲───────────────────────┘
                        │              │
      Ambil Kredensial  │              │ Ingest Metrik
      (X-API-Key)       │              │ (X-API-Key)
                        │              │
┌───────────────────────┴──────────────┴───────────────────────┐
│                   n8n (monitor_n8n:5678)                     │
│  - Schedule Trigger (Cron berkala)                           │
│  - Webhook Trigger (Manual "Sync Sekarang")                  │
│  - Panggilan API Resmi Platform (Google, Meta, TikTok)       │
│  - Normalisasi JSON & Pengiriman ke /api/ingest              │
└──────────────────────────────────────────────────────────────┘
```

---

## Tech Stack & Manajemen Dependensi

- **Backend**: Python 3.12, FastAPI, Uvicorn, Pydantic Settings, Cryptography (Fernet), Bcrypt, APScheduler, HTTPX.
- **Dependensi Standar**: Dikelola secara modern melalui **`pyproject.toml`** (PEP 621) yang mencakup dependensi inti dan pengujian dev.
- **Frontend**: Jinja2 Server-Side Templates, HTMX, Alpine.js, Tailwind CSS (CDN), Chart.js (CDN).
- **Database**: Turso Cloud (libSQL), SQLite (untuk automated testing lokal).
- **Orkestrasi**: n8n (Official Docker image dipin `n8nio/n8n:1.80.0`).
- **Container**: Docker + Docker Compose v2 (Multi-stage build).

---

## Prasyarat Host

Sesuai prinsip **Docker-First**, komputer host **HANYA** memerlukan:
- **Docker Engine** (versi 24.0+)
- **Docker Compose v2** (versi 2.20+)
- *(Opsional)* `make`

> **PENTING**: Dilarang menginstal Python, pip, Node.js, atau pustaka lokal langsung di host. Seluruh build, pengujian, migrasi, dan dependency installation dijalankan di dalam container Docker.

---

## Panduan Instalasi Cepat (Development)

### 1. Salin Berkas Konfigurasi
```bash
cp .env.example .env
```

### 2. Generate Kunci Enkripsi & Password Hash
Jalankan script generator di dalam container `tools`:
```bash
docker compose run --rm -it tools python scripts/generate_keys.py
```
Salin output kunci yang dihasilkan (`TOKEN_ENCRYPTION_KEY`, `APP_SECRET_KEY`, `INGEST_API_KEY`, `N8N_ENCRYPTION_KEY`, `N8N_WEBHOOK_SECRET`, dan `ADMIN_PASSWORD_HASH`) ke file `.env`.

### 3. Jalankan Aplikasi (Mode Dev / Tunnel)
```bash
docker compose up -d --build
```
Perintah ini menjalankan:
- `monitor_impakta`: Backend FastAPI di port `8001 -> 8000`.
- `monitor_n8n`: Service orkestrasi n8n di port `5678`.
- `monitor_n8n_import`: One-shot runner yang otomatis mengimpor workflow dari `./n8n/workflows`.
- `monitor_tunnel`: Cloudflare tunnel (opsional untuk callback dev).

Buka browser ke **http://127.0.0.1:8001**, login dengan akun admin Anda.

---

## Panduan Deployment Production (VPS + Host Caddy)

Untuk server VPS produksi dengan Caddy yang terpasang langsung di host OS (tanpa Cloudflare Tunnel):

### 1. File Konfigurasi Production
Gunakan file **`docker-compose.prod.yml`** yang bersih tanpa container `cloudflared`:
- Port `monitor_impakta` diikat ke `127.0.0.1:8001:8000` (hanya lokal).
- Port `monitor_n8n` diikat ke `127.0.0.1:5678:5678` (hanya lokal).
- `restart: always` dan rotasi log aktif.

### 2. Jalankan di VPS
```bash
docker compose -f docker-compose.prod.yml up -d --build
```

### 3. Konfigurasi Caddy Host
Edit file `/etc/caddy/Caddyfile` di VPS Anda:
```caddy
monitor.impakta.my.id {
    reverse_proxy 127.0.0.1:8001
}
```

Terapkan perubahan konfigurasi Caddy:
```bash
sudo systemctl reload caddy
```

Caddy akan otomatis mengurus sertifikat SSL (HTTPS Let's Encrypt), dan aplikasi Anda dapat diakses di `https://monitor.impakta.my.id`.

Pastikan di `.env` VPS Anda:
```ini
BASE_URL=https://monitor.impakta.my.id
SECURE_COOKIES=true
```

---

## Ganti Password Admin

Jika Anda ingin mengganti password admin:

### Cara 1: Menggunakan Script Interaktif
Jalankan perintah berikut:
```bash
docker compose exec -it app python scripts/change_password.py
```
Atau berikan password baru sebagai argumen:
```bash
docker compose exec app python scripts/change_password.py "PasswordBaruAnda#123"
```

### Cara 2: Generate Manual & Update `.env`
Bcrypt hash di dalam file `.env` **wajib** menggunakan escape dollar ganda (`$$`) agar tidak terpotong oleh parser Docker Compose:
```env
ADMIN_PASSWORD_HASH=$$2b$$12$$...
```
Setelah mengubah `.env`, terapkan perubahan dengan merefresh container:
```bash
docker compose up -d app
# atau untuk production:
docker compose -f docker-compose.prod.yml up -d app
```

---

## Setup Database Turso Cloud

Aplikasi menggunakan [Turso Cloud](https://turso.tech/) (libSQL berbasis SQLite).

1. Buat akun di [turso.tech](https://turso.tech/) atau gunakan Turso CLI:
   ```bash
   turso db create monitor-impakta
   ```
2. Ambil Database URL:
   ```bash
   turso db show monitor-impakta --url
   # Contoh output: https://monitor-[user].turso.io
   ```
3. Buat Auth Token:
   ```bash
   turso db tokens create monitor-impakta
   ```
4. Masukkan ke `.env`:
   ```ini
   TURSO_DATABASE_URL=https://monitor-[user].turso.io
   TURSO_AUTH_TOKEN=eyJh...
   ```

---

## Setup HTTPS & BASE_URL

Platform OAuth (terutama TikTok dan Google) **wajib** menggunakan URL callback berbasis HTTPS yang dapat diakses publik.

- **Production (Caddy Host)**:
  ```ini
  BASE_URL=https://monitor.impakta.my.id
  SECURE_COOKIES=true
  ```
- **Development (Cloudflare Tunnel)**:
  ```ini
  BASE_URL=https://subdomain.trycloudflare.com
  SECURE_COOKIES=false
  ```
- **Local Testing**:
  ```ini
  BASE_URL=http://127.0.0.1:8001
  SECURE_COOKIES=false
  ```

---

## Panduan Portal Developer per Platform

Karena dashboard ini adalah alat personal (*Single-User*), **TIDAK DIPERLUKAN App Review**. Cukup biarkan status aplikasi di mode **Development / Sandbox / Testing** dan daftarkan akun pribadi Anda sebagai Tester.

### 1. Google Cloud Console (YouTube)
1. Buka [Google Cloud Console](https://console.cloud.google.com/).
2. Buat proyek baru (misal: `Personal-KPI-Dashboard`).
3. Masuk ke **APIs & Services → Library**, aktifkan:
   - **YouTube Data API v3**
   - **YouTube Analytics API**
4. Buka **APIs & Services → OAuth consent screen**:
   - Pilih **External**.
   - Isi Nama Aplikasi & email dukungan.
   - Pada bagian **Test Users**, tambahkan email Google Anda.
   - **Trik Penting**: Setelah menambahkan email Anda di Test Users, klik tombol **"Publish App" (ubah status ke "In Production")** agar refresh token **tidak kedaluwarsa setelah 7 hari**.
5. Buka **Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Web application**.
   - Authorized redirect URIs:
     ```
     {BASE_URL}/connect/youtube/callback
     ```
6. Salin Client ID dan Client Secret ke `.env` (`GOOGLE_CLIENT_ID` dan `GOOGLE_CLIENT_SECRET`).

---

### 2. Meta for Developers (Instagram)
> **Syarat Wajib**: Akun Instagram Anda harus berupa **Akun Profesional (Business atau Creator)** dan sudah ditautkan ke **Facebook Page** yang Anda kelola.

1. Buka [Meta for Developers](https://developers.facebook.com/) dan buat aplikasi baru bertipe **Business**.
2. Di dashboard aplikasi, tambahkan produk **Facebook Login for Business** (atau Facebook Login).
3. Masuk ke **Facebook Login → Settings**:
   - Valid OAuth Redirect URIs:
     ```
     {BASE_URL}/connect/instagram/callback
     ```
4. Pastikan akun Meta Anda terdaftar sebagai Admin atau Developer di tab **Roles**.
5. Salin App ID dan App Secret ke `.env` (`META_APP_ID` dan `META_APP_SECRET`).

---

### 3. TikTok for Developers
1. Buka [TikTok for Developers](https://developers.tiktok.com/) dan buat aplikasi baru.
2. Tambahkan produk **Login Kit** dan **Display API**.
3. Di konfigurasi Redirect URI, daftarkan:
   ```
   {BASE_URL}/connect/tiktok/callback
   ```
4. Scopes yang diaktifkan: `user.info.basic`, `user.info.stats`, `video.list`.
5. Masuk ke tab **Sandbox / Target Users**, undang akun TikTok pribadi Anda sebagai tester dan terima undangan di aplikasi TikTok Anda.
6. Salin Client Key dan Client Secret ke `.env` (`TIKTOK_CLIENT_KEY` dan `TIKTOK_CLIENT_SECRET`).

---

## Orkestrasi n8n & Aktivasi Workflow

Workflow n8n otomatis diimpor saat container n8n pertama kali dijalankan melalui service `n8n-import`.

1. Buka antarmuka n8n di browser lokal: **http://127.0.0.1:5678**.
   - *Catatan keamanan*: n8n hanya terekspos di localhost dan tidak dapat diakses dari internet publik.
2. Selesaikan setup akun Owner n8n saat pertama kali membuka.
3. Masuk ke menu **Workflows**:
   - `YouTube KPI Sync`
   - `Instagram KPI Sync`
   - `TikTok KPI Sync`
   - `Sync Error Alert`
4. Buka masing-masing workflow, periksa alurnya, lalu ubah status toggle di kanan atas menjadi **Active**.
5. Tombol **"Sync Sekarang"** di halaman Pengaturan Dashboard FastAPI akan memicu webhook internal n8n secara instan.

---

## Perintah Manajemen (Container-First)

| Kebutuhan | Perintah Docker Compose |
|---|---|
| **Jalankan Dev** | `docker compose up -d` |
| **Jalankan Prod** | `docker compose -f docker-compose.prod.yml up -d --build` |
| **Ganti Password Admin** | `docker compose exec app python scripts/change_password.py` |
| **Buat Kunci Enkripsi Baru** | `docker compose run --rm -it tools python scripts/generate_keys.py` |
| **Pantau Log Aplikasi** | `docker compose logs -f app` |
| **Hentikan Container** | `docker compose down` (atau `docker compose -f docker-compose.prod.yml down`) |
| **Uji Coba Pytest** | `docker compose run --rm tests` |

---

## Troubleshooting & Pemecahan Masalah

### 1. `redirect_uri_mismatch`
- **Penyebab**: Redirect URI yang dipanggil tidak sama persis dengan yang didaftarkan di konsol pengembang.
- **Solusi**: Periksa `BASE_URL` di `.env`. Pastikan tidak ada *trailing slash* (`/`), protokol (`https://` vs `http://`), dan path harus persis `{BASE_URL}/connect/{platform}/callback`.

### 2. Akun Instagram Tidak Ditemukan (*No Instagram Business Account*)
- **Penyebab**: Akun Instagram masih personal atau belum ditautkan ke Facebook Page.
- **Solusi**:
  1. Buka aplikasi Instagram di HP -> Pengaturan -> Akun -> Beralih ke Akun Profesional (Business/Creator).
  2. Masuk ke Facebook Page yang Anda kelola -> Pengaturan -> Akun Tertaut (Linked Accounts) -> Hubungkan akun Instagram tersebut.

### 3. Token Google Kedaluwarsa Setelah 7 Hari
- **Penyebab**: Aplikasi Google Cloud masih dalam status "Testing / In development".
- **Solusi**: Pada menu **OAuth consent screen** di Google Cloud, klik tombol **"Publish App"** agar statusnya menjadi "In production".

### 4. TikTok Sandbox Login Gagal
- **Penyebab**: Akun TikTok belum menerima undangan Sandbox Target User.
- **Solusi**: Periksa tab Target Users di TikTok Developer Portal. Buka notifikasi di aplikasi TikTok di smartphone Anda dan setujui undangan tester.

### 5. n8n Tidak Bisa Menghubungi App (`ECONNREFUSED app:8000`)
- **Penyebab**: Komunikasi antar container terputus.
- **Solusi**: Keduanya berada dalam network bridge Docker `monitor_net`. Jangan gunakan `localhost:8000` di dalam node HTTP n8n; gunakan selalu hostname container `http://app:8000`.

---

## Asumsi & Batasan Desain

1. **Batasan Metrik TikTok (Display API)**:
   API resmi TikTok Login Kit & Display API tidak menyediakan metrik *Watch Time* atau *Video Completion Rate*. Metrik tersebut hanya tersedia untuk akun TikTok Shop / Ads Business Partner.
2. **Snapshot Harian Followers**:
   API Instagram Graph dan TikTok tidak selalu menyediakan riwayat harian untuk jumlah pengikut akun masa lampau. Dashboard menyimpan **snapshot harian** setiap kali sinkronisasi berlangsung, dan menghitung pertumbuhan (*follower growth*) dari selisih snapshot antara awal dan akhir periode.
3. **Model Single-User**:
   Aplikasi dirancang efisien dan ringan untuk kreator / tim komunikasi internal tanpa overhead multi-tenant database. Menghubungkan akun baru pada platform yang sama akan memperbarui koneksi sebelumnya.
