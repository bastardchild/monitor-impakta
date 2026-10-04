# Social Media KPI Dashboard (YouTube, Instagram, TikTok)

Dashboard analitik kinerja media sosial pribadi (*single-user*, **BUKAN SaaS**) berbasis **Python (FastAPI)**, **libSQL (Turso Cloud)**, **n8n**, **HTMX**, dan **Alpine.js**.

---

## Daftar Isi
1. [Fitur Utama](#fitur-utama)
2. [Arsitektur Sistem](#arsitektur-sistem)
3. [Tech Stack](#tech-stack)
4. [Prasyarat Host](#prasyarat-host)
5. [Panduan Instalasi Cepat](#panduan-instalasi-cepat)
6. [Setup Database Turso Cloud](#setup-database-turso-cloud)
7. [Setup HTTPS & BASE_URL](#setup-https--base_url)
8. [Panduan Portal Developer per Platform](#panduan-portal-developer-per-platform)
   - [Google Cloud / YouTube](#1-google-cloud-console-youtube)
   - [Meta for Developers / Instagram](#2-meta-for-developers-instagram)
   - [TikTok for Developers](#3-tiktok-for-developers)
9. [Orkestrasi n8n & Aktivasi Workflow](#orkestrasi-n8n--aktivasi-workflow)
10. [Perintah Manajemen (Container-First)](#perintah-manajemen-container-first)
11. [Troubleshooting & Pemecahan Masalah](#troubleshooting--pemecahan-masalah)
12. [Asumsi & Batasan Desain](#asumsi--batasan-desain)

---

## Fitur Utama

- **OAuth 2.0 Terpusat di FastAPI**: Koneksi akun satu kali klik langsung dari menu Pengaturan. Token dienkripsi menggunakan Fernet (`cryptography`) sebelum disimpan ke database Turso.
- **Orkestrasi Otomatis n8n**: Penjadwalan sync tiap 6 jam dan tombol manual "Sync Sekarang" via webhook internal.
- **Pembaruan Token Otomatis (Dual-Layer Refresh)**:
  1. *Lazy Refresh*: Diperbarui seketika jika masa berlaku token tersisa < 10 menit saat n8n meminta kredensial.
  2. *Background Refresh*: Job APScheduler berkala setiap 30 menit dengan *lock per platform* (`asyncio.Lock`) dan *exponential backoff*.
- **Hypermedia SSR (HTMX + Alpine.js)**: Tanpa build step frontend (React/Vue/Webpack). Interaksi reaktif server-driven dengan partial swapping, polling berkala, dan visualisasi interaktif Chart.js.
- **KPI Multi-Platform**:
  - **YouTube**: Subscribers, Views, Waktu Tonton (Watch Time Jam), Rata-rata Durasi Tonton, Interaksi (Likes/Komentar/Share), Top Video.
  - **Instagram**: Followers, Reach (Jangkauan), Profile Views, Saves, Shares, Engagement Rate, Top Reels/Posts.
  - **TikTok**: Followers, Video Views, Likes, Komentar, Shares, Top Video.
- **Target KPI**: Form pembuatan sasaran target performa (mingguan/bulanan) dengan progress bar status (Hijau $\ge 100\%$, Kuning $\ge 70\%$, Merah $< 70\%$).
- **Mode Demo Bawaan**: Seed data realistis 90 hari untuk langsung mencoba dashboard tanpa menunggu setup kredensial OAuth.

---

## Arsitektur Sistem

```
┌──────────────────────────────────────────────────────────────┐
│                        BROWSER / KLIEN                       │
└──────────────────────────────▲───────────────────────────────┘
                               │ HTTP / HTMX
┌──────────────────────────────▼───────────────────────────────┐
│                      FASTAPI APP (app:8000)                  │
│  - SSR Jinja2 & Komponen HTMX                                │
│  - Penanganan Alur OAuth (YouTube, Meta, TikTok)             │
│  - Enkripsi Token Fernet & Storage Turso libSQL              │
│  - APScheduler Background Token Refresher (tiap 30m)         │
│  - Endpoint Kredensial Internal: /api/internal/credentials/* │
│  - Endpoint Ingest Data: /api/ingest/*                       │
└───────────────────────▲──────────────▲───────────────────────┘
                        │              │
      Ambil Kredensial  │              │ Ingest Metrik
      (X-API-Key)       │              │ (X-API-Key)
                        │              │
┌───────────────────────┴──────────────┴───────────────────────┐
│                        n8n (n8n:5678)                        │
│  - Schedule Trigger (Cron tiap 6 jam)                        │
│  - Webhook Trigger (Manual "Sync Sekarang")                  │
│  - Panggilan API Resmi Platform (Google, Meta, TikTok)       │
│  - Normalisasi JSON & Pengiriman ke /api/ingest              │
└──────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

- **Backend**: Python 3.12, FastAPI, Uvicorn (1 worker), Pydantic Settings, Cryptography (Fernet), Bcrypt, APScheduler, HTTPX.
- **Frontend**: Jinja2 Server-Side Templates, HTMX, Alpine.js, Tailwind CSS (CDN), Chart.js (CDN).
- **Database**: Turso Cloud (libSQL), SQLite (untuk automated testing lokal).
- **Orkestrasi**: n8n (Official Docker image dipin `n8nio/n8n:1.80.0`).
- **Container**: Docker + Docker Compose v2.

---

## Prasyarat Host

Sesuai prinsip **Docker-First**, komputer host **HANYA** memerlukan:
- **Docker Engine** (versi 24.0+)
- **Docker Compose v2** (versi 2.20+)
- *(Opsional)* `make`

> **PENTING**: Dilarang menginstal Python, pip, Node.js, atau pustaka lokal langsung di host. Seluruh build, pengujian, migrasi, dan seed dijalankan di dalam container Docker.

---

## Panduan Instalasi Cepat

### 1. Salin Berkas Konfigurasi
```bash
cp .env.example .env
```

### 2. Generate Kunci Enkripsi & Password Hash
Jalankan script generator di dalam container `tools`:
```bash
docker compose run --rm -it tools python scripts/generate_keys.py
```
Masukkan password admin yang Anda inginkan saat diminta. Salin output kunci yang dihasilkan (`TOKEN_ENCRYPTION_KEY`, `APP_SECRET_KEY`, `INGEST_API_KEY`, `N8N_ENCRYPTION_KEY`, `N8N_WEBHOOK_SECRET`, dan `ADMIN_PASSWORD_HASH`) ke dalam file `.env`.

### 3. Jalankan Aplikasi & n8n
```bash
docker compose up -d --build
```
Perintah ini akan menjalankan:
- `kpi_app`: Backend FastAPI di port `8000` (migrasi tabel database berjalan otomatis saat startup).
- `kpi_n8n`: Service orkestrasi n8n di port `5678`.
- `kpi_n8n_import`: One-shot runner yang otomatis mengimpor workflow dari `./n8n/workflows`.

### 4. Isi Data Demo (Opsional namun Direkomendasikan)
Untuk langsung melihat tampilan dashboard dengan metrik 90 hari:
```bash
docker compose run --rm tools python scripts/seed_demo.py --reset
```

Buka peramban ke **http://127.0.0.1:8000**, login dengan:
- **Username**: `admin` (atau sesuai `ADMIN_USERNAME` di `.env`)
- **Password**: Password yang Anda masukkan di langkah 2.

---

## Setup Database Turso Cloud

Aplikasi menggunakan [Turso Cloud](https://turso.tech/) (libSQL berbasis SQLite).

1. Buat akun di [turso.tech](https://turso.tech/) atau gunakan Turso CLI:
   ```bash
   turso db create kpi-sosmed
   ```
2. Ambil Database URL:
   ```bash
   turso db show kpi-sosmed --url
   # Contoh output: libsql://kpi-sosmed-[user].turso.io
   ```
3. Buat Auth Token:
   ```bash
   turso db tokens create kpi-sosmed
   ```
4. Masukkan ke `.env`:
   ```ini
   TURSO_DATABASE_URL=libsql://kpi-sosmed-[user].turso.io
   TURSO_AUTH_TOKEN=eyJh...
   ```
*(Catatan: Untuk pengujian lokal tanpa internet, Anda dapat mengatur `TURSO_DATABASE_URL=file:/tmp/local.db`)*

---

## Setup HTTPS & BASE_URL

Platform OAuth (terutama TikTok dan Google) **wajib** menggunakan URL callback berbasis HTTPS yang dapat diakses publik.

### Opsi A: Menggunakan Cloudflare Tunnel (Bawaan)
1. Buat Tunnel di dashboard [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) -> Networks -> Tunnels.
2. Arahkan hostname publik (misal: `https://kpi.domainanda.com`) ke service internal `http://app:8000`.
3. Salin token tunnel ke `.env`:
   ```ini
   TUNNEL_TOKEN=eyJh...
   BASE_URL=https://kpi.domainanda.com
   SECURE_COOKIES=true
   ```
4. Jalankan container cloudflared:
   ```bash
   docker compose --profile tunnel up -d
   ```

### Opsi B: Development Lokal (Testing Sandbox)
Jika sedang menguji tanpa domain, gunakan:
```ini
BASE_URL=http://127.0.0.1:8000
SECURE_COOKIES=false
```

---

## Panduan Portal Developer per Platform

Karena dashboard ini adalah alat personal (*Single-User*), **TIDAK DIPERLUKAN App Review**. Anda cukup membiarkan status aplikasi di mode **Development / Sandbox / Testing** dan mendaftarkan akun pribadi Anda sebagai Tester.

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
   - **Trik Penting**: Setelah menambahkan email Anda di Test Users, klik tombol **"Publish App" (ubah status ke "In Production")** agar refresh token **tidak kedaluwarsa setelah 7 hari**. Karena hanya akun Anda yang menggunakan aplikasi ini, verifikasi Google tidak diwajibkan.
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
4. Pastikan akun Meta Anda terdaftar sebagai Admin atau Developer di tab **Roles**. Aplikasi tetap berada dalam status **Development Mode**.
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

Workflow n8n otomatis diimpor saat pertama kali `docker compose up` melalui service `n8n-import`.

1. Buka antarmuka n8n di browser lokal: **http://127.0.0.1:5678**.
   - *Catatan keamanan*: n8n **hanya** terekspos di localhost dan tidak dapat diakses dari internet publik.
2. Selesaikan setup akun Owner n8n saat pertama kali membuka.
3. Masuk ke menu **Workflows**. Anda akan melihat:
   - `YouTube KPI Sync`
   - `Instagram KPI Sync`
   - `TikTok KPI Sync`
   - `Sync Error Alert`
4. Buka masing-masing workflow, periksa alurnya, lalu ubah status toggle di kanan atas menjadi **Active**.
5. Tombol **"Sync Sekarang"** di halaman Pengaturan Dashboard FastAPI akan memicu webhook internal n8n secara instan.

---

## Perintah Manajemen (Container-First)

Tersedia pembungkus `Makefile` (opsional):

| Perintah Makefile | Perintah Standar Docker Compose | Deskripsi |
|---|---|---|
| `make keys` | `docker compose run --rm -it tools python scripts/generate_keys.py` | Buat kunci enkripsi dan hash password |
| `make up` | `docker compose up -d --build` | Jalankan semua service (App + n8n) |
| `make tunnel` | `docker compose --profile tunnel up -d` | Jalankan Cloudflare Tunnel |
| `make seed` | `docker compose run --rm tools python scripts/seed_demo.py --reset` | Masukkan data demo 90 hari |
| `make test` | `docker compose --profile test run --rm tests` | Jalankan suite pengujian pytest |
| `make logs` | `docker compose logs -f app` | Pantau log FastAPI realtime |
| `make down` | `docker compose down` | Hentikan container (data n8n tetap tersimpan) |
| `make rebuild` | `docker compose build --no-cache && docker compose up -d` | Rebuild penuh image Docker |

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
- **Solusi**: Keduanya berada dalam network bridge Docker `kpi_net`. Jangan gunakan `localhost:8000` di dalam node HTTP n8n; gunakan selalu hostname container `http://app:8000`.

---

## Asumsi & Batasan Desain

1. **Batasan Metrik TikTok (Display API)**:
   API resmi TikTok Login Kit & Display API **TIDAK** menyediakan metrik *Watch Time* atau *Video Completion Rate*. Metrik tersebut hanya tersedia untuk akun TikTok Shop / Ads Business Partner. Dashboard ini secara sengaja tidak menampilkan metrik palsu untuk TikTok.
2. **Snapshot Harian Followers**:
   API Instagram Graph dan TikTok tidak selalu menyediakan riwayat harian (*historical series*) untuk jumlah pengikut akun masa lampau. Dashboard menyimpan **snapshot harian** setiap kali sinkronisasi berlangsung, dan menghitung pertumbuhan (*follower growth*) dari selisih snapshot antara awal dan akhir periode.
3. **Model Single-User**:
   Aplikasi dirancang efisien dan ringan untuk kreator pribadi tanpa overhead multi-tenant database. Menghubungkan akun baru pada platform yang sama akan memperbarui koneksi sebelumnya.
